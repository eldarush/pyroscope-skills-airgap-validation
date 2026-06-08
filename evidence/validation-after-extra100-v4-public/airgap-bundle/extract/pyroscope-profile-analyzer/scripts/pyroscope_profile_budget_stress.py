#!/usr/bin/env python3
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROFILE_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROFILE_ROOT.parent
STRESS_FIXTURE = PROFILE_ROOT / "scripts" / "pyroscope_stress_fixture.py"
PROFILE_TOOL = PROFILE_ROOT / "scripts" / "pyroscope_profile_tool.py"
PACKET_TOOL = SKILL_ROOT / "pyroscope-orchestrator" / "scripts" / "pyroscope_weak_model_packet.py"
AUDIT_TOOL = SKILL_ROOT / "pyroscope-orchestrator" / "scripts" / "pyroscope_weak_model_audit.py"


def run(args, cwd=None):
    proc = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"command failed: {' '.join(str(arg) for arg in args)}\n"
            f"stdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
        )
    return proc


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    out_root = Path(tempfile.gettempdir()) / "pyroscope-profile-budget-stress"
    if out_root.exists():
        shutil.rmtree(out_root)
    repo = out_root / "repo"
    summary = out_root / "summary.json"
    mapping = out_root / "mapping.json"
    report = out_root / "report.md"
    packet = out_root / "packet.json"

    noise_files = 3000
    noise_stacks = 300000
    run(
        [
            sys.executable,
            str(STRESS_FIXTURE),
            "--out",
            str(repo),
            "--service",
            "budget-stress",
            "--noise-files",
            str(noise_files),
            "--noise-stacks",
            str(noise_stacks),
            "--noise-frame-cardinality",
            str(noise_files),
            "--mixed-runtimes",
        ]
    )
    folded = repo / "profiles" / "complex.folded"
    run(
        [
            sys.executable,
            str(PROFILE_TOOL),
            "summarize-folded",
            "--file",
            str(folded),
            "--service",
            "budget-stress",
            "--window",
            f"synthetic-{noise_stacks}",
            "--limit",
            "120",
            "--self-threshold",
            "1",
            "--total-threshold",
            "1",
            "--output",
            str(summary),
        ]
    )
    run(
        [
            sys.executable,
            str(PROFILE_TOOL),
            "map-source",
            "--repo",
            str(repo),
            "--summary",
            str(summary),
            "--max-files",
            "5000",
            "--max-file-bytes",
            "500000",
            "--output",
            str(mapping),
        ]
    )
    run(
        [
            sys.executable,
            str(PROFILE_TOOL),
            "report",
            "--service",
            "budget-stress",
            "--window",
            f"synthetic-{noise_stacks}",
            "--mapping",
            str(mapping),
            "--output",
            str(report),
        ]
    )
    run(
        [
            sys.executable,
            str(PACKET_TOOL),
            "--report",
            str(report),
            "--repo",
            str(repo),
            "--task",
            "analyze",
            "--max-hotspots",
            "8",
            "--max-chars",
            "8000",
            "--max-context-tokens",
            "128000",
            "--output",
            str(packet),
        ]
    )
    audit_proc = run(
        [
            sys.executable,
            str(AUDIT_TOOL),
            "--report",
            str(report),
            "--packet",
            str(packet),
            "--max-context-tokens",
            "128000",
            "--max-packet-chars",
            "8000",
        ]
    )
    summary_json = load_json(summary)
    mapping_json = load_json(mapping)
    packet_json = load_json(packet)
    audit_json = json.loads(audit_proc.stdout)
    scan = mapping_json["scan"]
    folded_meta = summary_json["folded"]
    failures = []
    if folded_meta["unique_frames"] < 3000:
        failures.append(f"expected at least 3000 unique frames, got {folded_meta['unique_frames']}")
    if folded_meta["total_samples"] < 4_000_000:
        failures.append(f"expected large sample total, got {folded_meta['total_samples']}")
    if scan["indexed_files"] < noise_files:
        failures.append(f"expected at least {noise_files} indexed files, got {scan['indexed_files']}")
    if scan["index_truncated"]:
        failures.append("source index unexpectedly truncated")
    if Path(packet).stat().st_size > 8000:
        failures.append(f"packet exceeded 8000 bytes: {Path(packet).stat().st_size}")
    if audit_json["context_budget"]["estimated_total_tokens"] > 128000:
        failures.append("audit context estimate exceeds 128k")
    if packet_json["hotspot_counts"]["total_rows"] <= 0:
        failures.append("packet parsed zero hotspot rows")
    if not audit_json["ok"]:
        failures.extend(audit_json.get("failures") or ["audit failed"])
    if failures:
        print(json.dumps({"ok": False, "failures": failures}, indent=2))
        raise SystemExit(1)
    print(
        json.dumps(
            {
                "ok": True,
                "noise_files": noise_files,
                "noise_stacks": noise_stacks,
                "folded_bytes": folded.stat().st_size,
                "summary_top": len(summary_json.get("top", [])),
                "unique_frames": folded_meta["unique_frames"],
                "indexed_files": scan["indexed_files"],
                "packet_chars": Path(packet).stat().st_size,
                "audit_estimated_total_tokens": audit_json["context_budget"]["estimated_total_tokens"],
                "candidate_hotspots": len(packet_json["candidate_hotspots"]),
                "plan_only_hotspots": len(packet_json["plan_only_hotspots"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
