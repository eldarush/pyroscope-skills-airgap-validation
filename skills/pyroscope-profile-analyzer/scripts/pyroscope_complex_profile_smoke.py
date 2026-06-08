#!/usr/bin/env python3
import json
import importlib.util
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
        raise AssertionError(f"command failed: {' '.join(map(str, args))}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return proc


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_packet_module():
    spec = importlib.util.spec_from_file_location("packet_tool", PACKET_TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    out_root = Path(tempfile.gettempdir()) / "pyroscope-complex-profile-smoke"
    if out_root.exists():
        shutil.rmtree(out_root)
    repo = out_root / "repo"
    report = out_root / "report.md"
    summary = out_root / "summary.json"
    mapping = out_root / "mapping.json"
    packet = out_root / "packet.json"

    run(
        [
            sys.executable,
            str(STRESS_FIXTURE),
            "--out",
            str(repo),
            "--service",
            "complex-max",
            "--noise-files",
            "900",
            "--noise-stacks",
            "120000",
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
            "complex-max",
            "--window",
            "synthetic-120k",
            "--limit",
            "80",
            "--self-threshold",
            "3",
            "--total-threshold",
            "3",
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
            "2000",
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
            "complex-max",
            "--window",
            "synthetic-120k",
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
    audit = run(
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
    audit_json = json.loads(audit.stdout)
    packet_module = load_packet_module()
    parsed_rows = {
        row["function"]: row
        for row in packet_module.parse_hotspots(report.read_text(encoding="utf-8"))
    }
    functions = {row["function"] for row in mapping_json.get("mapped", [])}
    required_functions = {
        "expensive_serializer",
        "regex_parser",
        "Checkout.Api.Controllers.OrderController.SerializeResponse",
        "com.example.RegexParser.parseLine",
        "com.example.spark.TransformJob.materializeRows",
        "com.example.flink.WindowAggregator.aggregateWindow",
        "main.handleRequest",
    }
    missing = sorted(required_functions - functions)
    if missing:
        raise AssertionError(f"missing expected mixed-runtime mappings: {missing}")
    candidate_functions = {row["function"] for row in packet_json["candidate_hotspots"]}
    plan_only_functions = {row["function"] for row in packet_json["plan_only_hotspots"]}
    if "com.example.spark.TransformJob.materializeRows" in candidate_functions:
        raise AssertionError("Spark semantic hotspot must not be an implementation candidate")
    if "com.example.flink.WindowAggregator.aggregateWindow" in candidate_functions:
        raise AssertionError("Flink semantic hotspot must not be an implementation candidate")
    if parsed_rows["com.example.spark.TransformJob.materializeRows"]["implementation_eligibility"] != "plan-only":
        raise AssertionError("Spark semantic hotspot must be plan-only before packet compaction")
    if parsed_rows["com.example.flink.WindowAggregator.aggregateWindow"]["implementation_eligibility"] != "plan-only":
        raise AssertionError("Flink semantic hotspot must be plan-only before packet compaction")
    if not audit_json.get("ok"):
        raise AssertionError("audit did not pass")
    print(
        json.dumps(
            {
                "ok": True,
                "repo": str(repo),
                "summary_top": len(summary_json.get("top", [])),
                "mapped": len(mapping_json.get("mapped", [])),
                "packet_candidates": len(packet_json["candidate_hotspots"]),
                "packet_plan_only": len(packet_json["plan_only_hotspots"]),
                "packet_chars": Path(packet).stat().st_size,
                "audit_estimated_total_tokens": audit_json["context_budget"]["estimated_total_tokens"],
                "spark_not_candidate": "com.example.spark.TransformJob.materializeRows" not in candidate_functions,
                "flink_not_candidate": "com.example.flink.WindowAggregator.aggregateWindow" not in candidate_functions,
                "spark_full_parse_plan_only": parsed_rows["com.example.spark.TransformJob.materializeRows"][
                    "implementation_eligibility"
                ]
                == "plan-only",
                "flink_full_parse_plan_only": parsed_rows["com.example.flink.WindowAggregator.aggregateWindow"][
                    "implementation_eligibility"
                ]
                == "plan-only",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
