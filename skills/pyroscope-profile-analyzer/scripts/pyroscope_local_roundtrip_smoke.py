#!/usr/bin/env python3
import argparse
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


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


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def ready(url):
    for path in ["/ready", "/-/ready"]:
        try:
            with urlopen(url.rstrip("/") + path, timeout=3) as resp:
                if 200 <= resp.status < 500:
                    return True
        except URLError:
            continue
    return False


def wait_ready(url, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ready(url):
            return True
        time.sleep(1)
    return False


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_pyroscope(image, timeout):
    if not shutil.which("docker"):
        raise SystemExit("docker is required to start a temporary Pyroscope container")
    port = free_port()
    name = f"pyroscope-roundtrip-smoke-{int(time.time())}"
    run(["docker", "run", "-d", "--rm", "--name", name, "-p", f"127.0.0.1:{port}:4040", image])
    url = f"http://localhost:{port}"
    if not wait_ready(url, timeout):
        run(["docker", "logs", name], cwd=None)
        run(["docker", "rm", "-f", name], cwd=None)
        raise SystemExit(f"temporary Pyroscope container did not become ready at {url}")
    return name, url


def summarize_until_observed(url, service, summary, timeout):
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        proc = subprocess.run(
            [
                sys.executable,
                str(PROFILE_TOOL),
                "summarize",
                "--url",
                url,
                "--service",
                service,
                "--window",
                "5m",
                "--limit",
                "30",
                "--self-threshold",
                "0",
                "--total-threshold",
                "0",
                "--output",
                str(summary),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
        if proc.returncode == 0:
            data = read_json(summary)
            functions = {row.get("function") for row in data.get("top", [])}
            if "expensive_serializer" in functions and "regex_parser" in functions:
                return data
        last_error = (proc.stdout + proc.stderr)[-2000:]
        time.sleep(2)
    raise AssertionError(f"Pyroscope did not return expected ingested hotspots before timeout. Last output:\n{last_error}")


def main():
    parser = argparse.ArgumentParser(description="Round-trip a folded profile through a local Pyroscope instance.")
    parser.add_argument("--url", default="http://localhost:4040", help="Existing Pyroscope URL.")
    parser.add_argument("--start-container", action="store_true", help="Start a temporary Pyroscope container instead of using --url.")
    parser.add_argument("--image", default="grafana/pyroscope:latest")
    parser.add_argument("--ready-timeout", type=int, default=60)
    parser.add_argument("--query-timeout", type=int, default=45)
    args = parser.parse_args()

    container = None
    url = args.url
    try:
        if args.start_container:
            container, url = start_pyroscope(args.image, args.ready_timeout)
        elif not wait_ready(url, args.ready_timeout):
            raise SystemExit(f"Pyroscope is not ready at {url}; rerun with --start-container or set --url")

        out_root = Path(tempfile.gettempdir()) / "pyroscope-local-roundtrip-smoke"
        if out_root.exists():
            shutil.rmtree(out_root)
        repo = out_root / "repo"
        summary = out_root / "summary.json"
        mapping = out_root / "mapping.json"
        report = out_root / "report.md"
        packet = out_root / "packet.json"
        service = f"roundtrip-{int(time.time())}"
        run(
            [
                sys.executable,
                str(STRESS_FIXTURE),
                "--out",
                str(repo),
                "--service",
                service,
                "--noise-files",
                "80",
                "--noise-stacks",
                "1500",
                "--mixed-runtimes",
            ]
        )
        run(
            [
                sys.executable,
                str(PROFILE_TOOL),
                "ingest-folded",
                "--url",
                url,
                "--service",
                service,
                "--file",
                str(repo / "profiles" / "complex.folded"),
                "--seconds",
                "30",
            ]
        )
        summary_json = summarize_until_observed(url, service, summary, args.query_timeout)
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
                "500",
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
                service,
                "--window",
                "5m",
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
        audit = json.loads(
            run(
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
            ).stdout
        )
        if not audit["ok"]:
            raise AssertionError(f"weak-model packet audit failed: {audit.get('failures')}")
        functions = [row.get("function") for row in summary_json.get("top", [])]
        print(
            json.dumps(
                {
                    "ok": True,
                    "url": url,
                    "service": service,
                    "top_count": len(summary_json.get("top", [])),
                    "observed": ["expensive_serializer", "regex_parser"],
                    "packet_chars": packet.stat().st_size,
                    "audit_estimated_total_tokens": audit["context_budget"]["estimated_total_tokens"],
                    "first_functions": functions[:8],
                    "started_container": bool(container),
                },
                indent=2,
            )
        )
    finally:
        if container:
            run(["docker", "rm", "-f", container])


if __name__ == "__main__":
    main()
