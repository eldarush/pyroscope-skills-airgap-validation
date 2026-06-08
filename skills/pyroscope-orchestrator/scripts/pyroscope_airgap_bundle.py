#!/usr/bin/env python3
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILLS = ["pyroscope-image-instrumenter", "pyroscope-profile-analyzer", "pyroscope-orchestrator"]
MANIFEST = "pyroscope-airgap-manifest.json"


def include_file(path):
    parts = set(path.parts)
    if "__pycache__" in parts:
        return False
    if path.suffix in {".pyc", ".pyo"}:
        return False
    return path.is_file()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def file_rows(root):
    rows = []
    for skill in SKILLS:
        base = root / skill
        if not base.is_dir():
            raise SystemExit(f"missing skill directory: {base}")
        for path in sorted(base.rglob("*")):
            if not include_file(path):
                continue
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            rows.append({"path": relative, "bytes": len(data), "sha256": sha256_bytes(data)})
    return rows


def create_bundle(skills_root, out_path):
    root = Path(skills_root).resolve()
    out = Path(out_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = file_rows(root)
    manifest = {
        "schema_version": 1,
        "created_at_unix": int(time.time()),
        "purpose": "Pyroscope skills airgap transfer bundle",
        "skills": SKILLS,
        "file_count": len(rows),
        "files": rows,
        "post_import_validation": [
            "python pyroscope-orchestrator/scripts/pyroscope_orchestrator.py audit",
            "python pyroscope-orchestrator/scripts/pyroscope_validation_suite.py --out-dir <writable-output-dir>",
            "python pyroscope-orchestrator/scripts/pyroscope_validation_suite.py --local-pyroscope --pyroscope-url http://localhost:4040 --out-dir <writable-output-dir>",
        ],
    }
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        for row in rows:
            zf.write(root / row["path"], row["path"])
    return {"ok": True, "bundle": str(out), "file_count": len(rows), "bytes": out.stat().st_size}


def create(args):
    print(json.dumps(create_bundle(args.skills_root, args.out), indent=2))


def verify_bundle(bundle):
    bundle = Path(bundle).resolve()
    if not bundle.is_file():
        raise SystemExit(f"missing bundle: {bundle}")
    with zipfile.ZipFile(bundle, "r") as zf:
        if MANIFEST not in zf.namelist():
            raise SystemExit(f"bundle missing {MANIFEST}")
        manifest = json.loads(zf.read(MANIFEST).decode("utf-8"))
        failures = []
        names = set(zf.namelist())
        for row in manifest.get("files", []):
            path = row.get("path")
            if path not in names:
                failures.append(f"missing file in zip: {path}")
                continue
            data = zf.read(path)
            if len(data) != row.get("bytes"):
                failures.append(f"byte length mismatch: {path}")
            if sha256_bytes(data) != row.get("sha256"):
                failures.append(f"sha256 mismatch: {path}")
        for required in [f"{skill}/SKILL.md" for skill in SKILLS]:
            if required not in names:
                failures.append(f"missing required skill file: {required}")
        if failures:
            print(json.dumps({"ok": False, "bundle": str(bundle), "failures": failures}, indent=2))
            raise SystemExit(1)
        return manifest


def verify(args):
    manifest = verify_bundle(args.bundle)
    print(json.dumps({"ok": True, "bundle": str(Path(args.bundle).resolve()), "file_count": manifest.get("file_count")}, indent=2))


def run_audit(extract_dir):
    audit = extract_dir / "pyroscope-orchestrator" / "scripts" / "pyroscope_orchestrator.py"
    proc = subprocess.run(
        [sys.executable, str(audit), "audit"],
        cwd=extract_dir,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if proc.returncode != 0:
        raise AssertionError(f"extracted orchestrator audit failed\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return json.loads(proc.stdout)


def roundtrip(args):
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle = out_dir / "pyroscope-skills-airgap.zip"
    created = create_bundle(args.skills_root, bundle)
    manifest = verify_bundle(bundle)
    extract_dir = out_dir / "extract"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)
    with zipfile.ZipFile(bundle, "r") as zf:
        zf.extractall(extract_dir)
    audit_json = run_audit(extract_dir)
    print(
        json.dumps(
            {
                "ok": bool(audit_json.get("ok")),
                "bundle": str(bundle),
                "extract_dir": str(extract_dir),
                "file_count": manifest.get("file_count"),
                "bytes": created.get("bytes"),
                "audit_ok": bool(audit_json.get("ok")),
            },
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description="Create and verify a portable Pyroscope skills airgap bundle.")
    sub = parser.add_subparsers(required=True)
    c = sub.add_parser("create")
    c.add_argument("--skills-root", default=str(ROOT))
    c.add_argument("--out", required=True)
    c.set_defaults(func=create)
    v = sub.add_parser("verify")
    v.add_argument("--bundle", required=True)
    v.set_defaults(func=verify)
    r = sub.add_parser("roundtrip")
    r.add_argument("--skills-root", default=str(ROOT))
    r.add_argument("--out-dir", default=str(Path(tempfile.gettempdir()) / "pyroscope-airgap-bundle"))
    r.set_defaults(func=roundtrip)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
