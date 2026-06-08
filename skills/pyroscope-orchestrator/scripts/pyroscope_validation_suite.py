#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ORCH = ROOT / "pyroscope-orchestrator" / "scripts"
IMAGE = ROOT / "pyroscope-image-instrumenter" / "scripts"
PROFILE = ROOT / "pyroscope-profile-analyzer" / "scripts"
DEFAULT_OUT = Path(r"D:\tmp\pyroscope-skill-lab\validation-suite")


def run(label, args, timeout):
    started = time.time()
    proc = subprocess.run(
        args,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    elapsed = round(time.time() - started, 2)
    parsed = None
    stdout = proc.stdout.strip()
    if stdout:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = None
    return {
        "label": label,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "elapsed_seconds": elapsed,
        "command": [str(item) for item in args],
        "json": parsed,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }


def compact_step(step):
    parsed = step.get("json")
    if not isinstance(parsed, dict):
        return step
    keep = {}
    for key in [
        "ok",
        "case_count",
        "cases",
        "max_estimated_tokens",
        "max_assembled_prompt_chars",
        "context_budget",
        "packet_schema",
        "scripts",
        "summary_top",
        "mapped",
        "packet_candidates",
        "packet_plan_only",
        "packet_chars",
        "audit_estimated_total_tokens",
        "noise_files",
        "noise_stacks",
        "folded_bytes",
        "unique_frames",
        "indexed_files",
        "top_count",
        "observed",
        "started_container",
        "file_count",
        "audit_ok",
        "spark_not_candidate",
        "flink_not_candidate",
        "spark_full_parse_plan_only",
        "flink_full_parse_plan_only",
        "blocked",
        "reason",
        "candidate",
        "rounds_run",
        "achieved_streak",
    ]:
        if key in parsed:
            keep[key] = parsed[key]
    result = dict(step)
    result["json"] = keep or parsed
    return result


def py_compile_step():
    files = [
        IMAGE / "pyroscope_image_tool.py",
        IMAGE / "pyroscope_image_tool_smoke.py",
        IMAGE / "pyroscope_image_docker_smoke.py",
        IMAGE / "pyroscope_git_workflow.py",
        IMAGE / "pyroscope_git_workflow_smoke.py",
        PROFILE / "pyroscope_profile_tool.py",
        PROFILE / "pyroscope_stress_fixture.py",
        PROFILE / "pyroscope_complex_profile_smoke.py",
        PROFILE / "pyroscope_profile_budget_stress.py",
        PROFILE / "pyroscope_local_roundtrip_smoke.py",
        ORCH / "pyroscope_orchestrator.py",
        ORCH / "pyroscope_weak_model_packet.py",
        ORCH / "pyroscope_weak_model_audit.py",
        ORCH / "pyroscope_weak_model_benchmark.py",
        ORCH / "pyroscope_packet_tool_smoke.py",
        ORCH / "pyroscope_weak_model_harness_smoke.py",
        ORCH / "pyroscope_validation_suite.py",
        ORCH / "pyroscope_airgap_bundle.py",
    ]
    return [sys.executable, "-m", "py_compile", *[str(path) for path in files]]


def prepare_packet_audit_fixture(out_dir):
    fixture_dir = out_dir / "packet-audit-fixture"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    report = fixture_dir / "report.md"
    packet = fixture_dir / "packet.json"
    report.write_text(
        """# Pyroscope Profile Report

Service: `fixture-service`
Window: `synthetic`

## Eligible Hotspots

| Function | Self % | Total % | Mapping | Source matches | Recommendation |
| --- | ---: | ---: | --- | --- | --- |
| `regex_parser` | 12.5 | 18.0 | unique | src/core/regex_parser.py | Check for repeated regex compilation; move constant regex construction out of the hot path if behavior is unchanged. |
| `generated.vendor.Frame` | 9.0 | 11.0 | unique | vendor/generated.py | Plan only; do not auto-edit generated or vendor code. |
| `ambiguous_serializer` | 8.0 | 10.0 | ambiguous | src/a.py, src/b.py | Plan only; do not auto-edit until source mapping is resolved. |
""",
        encoding="utf-8",
    )
    step = run(
        "prepare_packet_audit_fixture",
        [
            sys.executable,
            str(ORCH / "pyroscope_weak_model_packet.py"),
            "--report",
            str(report),
            "--repo",
            str(fixture_dir),
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
        ],
        30,
    )
    if not step["ok"]:
        raise SystemExit(f"failed to prepare packet audit fixture: {step['stderr_tail'] or step['stdout_tail']}")
    return report, packet


def deterministic_steps(out_dir):
    report, packet = prepare_packet_audit_fixture(out_dir)
    return [
        ("py_compile", py_compile_step(), 30),
        ("orchestrator_audit", [sys.executable, str(ORCH / "pyroscope_orchestrator.py"), "audit"], 30),
        (
            "airgap_bundle_roundtrip",
            [sys.executable, str(ORCH / "pyroscope_airgap_bundle.py"), "roundtrip", "--out-dir", str(out_dir / "airgap-bundle")],
            60,
        ),
        ("image_smoke", [sys.executable, str(IMAGE / "pyroscope_image_tool_smoke.py")], 60),
        ("git_workflow_smoke", [sys.executable, str(IMAGE / "pyroscope_git_workflow_smoke.py")], 60),
        ("image_docker_smoke", [sys.executable, str(IMAGE / "pyroscope_image_docker_smoke.py")], 300),
        ("packet_smoke", [sys.executable, str(ORCH / "pyroscope_packet_tool_smoke.py")], 60),
        ("harness_smoke", [sys.executable, str(ORCH / "pyroscope_weak_model_harness_smoke.py")], 60),
        ("complex_profile_smoke", [sys.executable, str(PROFILE / "pyroscope_complex_profile_smoke.py")], 180),
        ("profile_budget_stress", [sys.executable, str(PROFILE / "pyroscope_profile_budget_stress.py")], 300),
        (
            "benchmark_budget",
            [
                sys.executable,
                str(ORCH / "pyroscope_weak_model_benchmark.py"),
                "--budget-only",
                "--max-context-tokens",
                "128000",
                "--max-prompt-chars",
                "20000",
                "--out-dir",
                str(out_dir / "weak-model-benchmark"),
            ],
            60,
        ),
        (
            "packet_audit_existing_mixed_report",
            [
                sys.executable,
                str(ORCH / "pyroscope_weak_model_audit.py"),
                "--report",
                str(report),
                "--packet",
                str(packet),
                "--max-context-tokens",
                "128000",
                "--max-packet-chars",
                "8000",
            ],
            60,
        ),
    ]


def live_steps(out_dir, full):
    case_regex = ".*" if full else "^java_medium_image$"
    max_rounds = "3" if full else "1"
    required_streak = "2" if full else "1"
    return [
        (
            "hosted_weak_or_fallback_benchmark",
            [
                sys.executable,
                str(ORCH / "pyroscope_weak_model_benchmark.py"),
                "--case-regex",
                case_regex,
                "--out-dir",
                str(out_dir / "weak-model-benchmark"),
                "--max-rounds",
                max_rounds,
                "--required-streak",
                required_streak,
                "--timeout-seconds",
                "180",
                "--max-context-tokens",
                "128000",
                "--max-prompt-chars",
                "20000",
            ],
            7200 if full else 240,
        )
    ]


def local_pyroscope_steps(url, start_container):
    command = [
        sys.executable,
        str(PROFILE / "pyroscope_local_roundtrip_smoke.py"),
        "--url",
        url,
    ]
    if start_container:
        command.append("--start-container")
    return [("local_pyroscope_roundtrip", command, 240)]


def main():
    parser = argparse.ArgumentParser(description="Run Pyroscope skill validation gates for airgapped weak-model readiness.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--live", action="store_true", help="Also run a tiny hosted weak/fallback benchmark probe.")
    parser.add_argument("--live-full", action="store_true", help="Run the full hosted 48-case weak/fallback benchmark.")
    parser.add_argument("--local-pyroscope", action="store_true", help="Also ingest/query a folded profile through a local Pyroscope instance.")
    parser.add_argument("--pyroscope-url", default="http://localhost:4040")
    parser.add_argument("--start-pyroscope-container", action="store_true", help="Start a temporary grafana/pyroscope container for --local-pyroscope.")
    parser.add_argument("--keep-going", action="store_true", help="Continue deterministic checks after failures.")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    steps = []
    for label, command, timeout in deterministic_steps(out_dir):
        step = run(label, command, timeout)
        steps.append(step)
        if not step["ok"] and not args.keep_going:
            break
    deterministic_ok = all(step["ok"] for step in steps)
    if deterministic_ok and (args.live or args.live_full):
        for label, command, timeout in live_steps(out_dir, args.live_full):
            steps.append(run(label, command, timeout))
    if deterministic_ok and args.local_pyroscope:
        for label, command, timeout in local_pyroscope_steps(args.pyroscope_url, args.start_pyroscope_container):
            steps.append(run(label, command, timeout))

    live_results = [step for step in steps if step["label"].startswith("hosted_")]
    live_blocked = any(isinstance(step.get("json"), dict) and step["json"].get("blocked") for step in live_results)
    result = {
        "ok": all(step["ok"] for step in steps),
        "deterministic_ok": deterministic_ok,
        "live_requested": args.live or args.live_full,
        "live_blocked": live_blocked,
        "local_pyroscope_requested": args.local_pyroscope,
        "out_dir": str(out_dir),
        "steps": [compact_step(step) for step in steps],
    }
    output = json.dumps(result, indent=2)
    (out_dir / "validation-summary.json").write_text(output + "\n", encoding="utf-8")
    print(output)
    if not result["ok"]:
        raise SystemExit(2 if live_blocked else 1)


if __name__ == "__main__":
    main()
