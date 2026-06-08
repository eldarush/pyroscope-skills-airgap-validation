#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SECRET_PATTERNS = [
    re.compile(r"(?i)(password|passwd|secret|token|apikey|api_key)\s*[:=]\s*['\"]?[^'\"\s,}]+"),
    re.compile(r"gho_[A-Za-z0-9_]+"),
    re.compile(r"glpat-[A-Za-z0-9_-]+"),
]
RAW_PROFILE_PATTERNS = [
    re.compile(r"(?im)^[^|\n#`]{1,240}(?:;[^;\n]{1,180})+\s+\d+(?:\.\d+)?$"),
    re.compile(r"(?is)\"flamebearer\"\s*:"),
    re.compile(r"(?is)\"levels\"\s*:\s*\["),
    re.compile(r"(?is)\"names\"\s*:\s*\[[^\]]{200,}"),
    re.compile(r"(?is)\"stacktrace\"\s*:"),
    re.compile(r"(?is)\"samples\"\s*:\s*\[[^\]]{200,}"),
]


def count(path):
    text = path.read_text(encoding="utf-8")
    return {"chars": len(text), "lines": text.count("\n") + 1, "estimated_tokens": max(1, (len(text) + 3) // 4)}


def load_packet(path):
    if not path:
        return None, []
    failures = []
    try:
        packet = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [f"packet is not valid JSON: {exc}"]
    required = ["packet_version", "task", "service", "profile_window", "hotspot_counts", "candidate_hotspots", "plan_only_hotspots"]
    for key in required:
        if key not in packet:
            failures.append(f"packet missing required key: {key}")
    if not str(packet.get("service", "")).strip():
        failures.append("packet service is empty")
    if not str(packet.get("profile_window", "")).strip():
        failures.append("packet profile_window is empty")
    counts = packet.get("hotspot_counts") or {}
    candidates = packet.get("candidate_hotspots") or []
    plan_only = packet.get("plan_only_hotspots") or []
    if not isinstance(candidates, list) or not isinstance(plan_only, list):
        failures.append("packet hotspot lists must be arrays")
        return packet, failures
    if counts.get("implementation_candidates") != len(candidates):
        failures.append("packet candidate count does not match candidate_hotspots length")
    if counts.get("plan_only") is not None and counts.get("plan_only") < len(plan_only):
        failures.append("packet plan_only count is smaller than plan_only_hotspots length")
    if counts.get("total_rows", 0) < len(candidates) + len(plan_only):
        failures.append("packet total_rows is smaller than emitted hotspot rows")
    if counts.get("total_rows", 0) <= 0:
        failures.append("packet contains zero hotspot rows")
    policy = packet.get("report_context_policy") or {}
    if policy.get("weak_model_should_open_report") is not False:
        failures.append("packet must mark the full report as not for weak-model loading")
    if policy.get("raw_profile_data_allowed") is not False:
        failures.append("packet must forbid raw profile data")
    if policy.get("trace_path_only") is not True:
        failures.append("packet report_path must be trace-only")
    unsafe = re.compile(r"(?i)(generated|vendor|/test/|/tests/|auth|security|retry|timeout|lock|concurrency|public contract|business behavior|repartition|checkpoint|state ttl|rocksdb|/spark/|/flink/)")
    for row in candidates:
        source_matches = str(row.get("source_matches", ""))
        sources = [item.strip() for item in source_matches.replace("\\", "/").split(",") if item.strip() and item.strip().lower() != "none"]
        if row.get("mapping") != "unique":
            failures.append(f"candidate is not uniquely mapped: {row.get('function')}")
        if len(sources) != 1:
            failures.append(f"candidate must have exactly one source match: {row.get('function')}")
        joined = " ".join([str(row.get("function", "")), source_matches, str(row.get("recommendation", ""))]).replace("\\", "/")
        if unsafe.search(joined):
            failures.append(f"candidate crosses unsafe surface: {row.get('function')}")
    serialized = json.dumps(packet)
    for pattern in SECRET_PATTERNS:
        if pattern.search(serialized):
            failures.append("packet contains a likely secret")
            break
    for pattern in RAW_PROFILE_PATTERNS:
        if pattern.search(serialized):
            failures.append("packet appears to contain raw Pyroscope profile data")
            break
    return packet, failures


def report_safety_failures(text):
    failures = []
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            failures.append("report contains a likely secret")
            break
    for pattern in RAW_PROFILE_PATTERNS:
        if pattern.search(text):
            failures.append("report appears to contain raw Pyroscope profile data")
            break
    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--packet")
    parser.add_argument("--max-skill-chars", type=int, default=12000)
    parser.add_argument("--max-report-chars", type=int, default=12000)
    parser.add_argument("--max-packet-chars", type=int, default=8000)
    parser.add_argument("--max-context-tokens", type=int, default=128000)
    args = parser.parse_args()

    skills = {
        name: count(ROOT / name / "SKILL.md")
        for name in [
            "pyroscope-image-instrumenter",
            "pyroscope-profile-analyzer",
            "pyroscope-orchestrator",
        ]
    }
    scripts = {
        "image_tool": (ROOT / "pyroscope-image-instrumenter" / "scripts" / "pyroscope_image_tool.py").exists(),
        "image_smoke": (
            ROOT / "pyroscope-image-instrumenter" / "scripts" / "pyroscope_image_tool_smoke.py"
        ).exists(),
        "image_docker_smoke": (
            ROOT / "pyroscope-image-instrumenter" / "scripts" / "pyroscope_image_docker_smoke.py"
        ).exists(),
        "git_workflow": (
            ROOT / "pyroscope-image-instrumenter" / "scripts" / "pyroscope_git_workflow.py"
        ).exists(),
        "git_workflow_smoke": (
            ROOT / "pyroscope-image-instrumenter" / "scripts" / "pyroscope_git_workflow_smoke.py"
        ).exists(),
        "profile_tool": (ROOT / "pyroscope-profile-analyzer" / "scripts" / "pyroscope_profile_tool.py").exists(),
        "stress_fixture": (ROOT / "pyroscope-profile-analyzer" / "scripts" / "pyroscope_stress_fixture.py").exists(),
        "complex_profile_smoke": (
            ROOT / "pyroscope-profile-analyzer" / "scripts" / "pyroscope_complex_profile_smoke.py"
        ).exists(),
        "profile_budget_stress": (
            ROOT / "pyroscope-profile-analyzer" / "scripts" / "pyroscope_profile_budget_stress.py"
        ).exists(),
        "local_roundtrip_smoke": (
            ROOT / "pyroscope-profile-analyzer" / "scripts" / "pyroscope_local_roundtrip_smoke.py"
        ).exists(),
        "orchestrator": (ROOT / "pyroscope-orchestrator" / "scripts" / "pyroscope_orchestrator.py").exists(),
        "packet_generator": (
            ROOT / "pyroscope-orchestrator" / "scripts" / "pyroscope_weak_model_packet.py"
        ).exists(),
        "packet_smoke": (
            ROOT / "pyroscope-orchestrator" / "scripts" / "pyroscope_packet_tool_smoke.py"
        ).exists(),
        "harness_smoke": (
            ROOT / "pyroscope-orchestrator" / "scripts" / "pyroscope_weak_model_harness_smoke.py"
        ).exists(),
        "validation_suite": (
            ROOT / "pyroscope-orchestrator" / "scripts" / "pyroscope_validation_suite.py"
        ).exists(),
        "airgap_bundle": (
            ROOT / "pyroscope-orchestrator" / "scripts" / "pyroscope_airgap_bundle.py"
        ).exists(),
    }
    report_path = Path(args.report)
    report = count(report_path)
    report_text = report_path.read_text(encoding="utf-8")
    packet = count(Path(args.packet)) if args.packet else None
    packet_json, packet_failures = load_packet(args.packet)
    failures = []
    for name, size in skills.items():
        if size["chars"] > args.max_skill_chars:
            failures.append(f"{name} SKILL.md is too large for weak-model use: {size['chars']} chars")
    if report["chars"] > args.max_report_chars:
        failures.append(f"report is too large for weak-model use: {report['chars']} chars")
    failures.extend(report_safety_failures(report_text))
    if packet and packet["chars"] > args.max_packet_chars:
        failures.append(f"packet is too large for weak-model use: {packet['chars']} chars")
    failures.extend(packet_failures)
    total_tokens = sum(size["estimated_tokens"] for size in skills.values()) + report["estimated_tokens"]
    if packet:
        total_tokens += packet["estimated_tokens"]
    if total_tokens > args.max_context_tokens:
        failures.append(f"combined skill/report/packet context estimate exceeds limit: {total_tokens} > {args.max_context_tokens}")
    for name, exists in scripts.items():
        if not exists:
            failures.append(f"missing deterministic script: {name}")
    result = {
        "ok": not failures,
        "skills": skills,
        "report": report,
        "packet": packet,
        "packet_schema": {
            "validated": bool(packet_json) if args.packet else False,
            "hotspot_counts": packet_json.get("hotspot_counts") if packet_json else None,
        },
        "context_budget": {
            "estimated_total_tokens": total_tokens,
            "max_context_tokens": args.max_context_tokens,
            "fits_context": total_tokens <= args.max_context_tokens,
            "note": "Estimate uses chars/4 to avoid tokenizer dependencies; actual prompt size may be lower or higher by model tokenizer.",
        },
        "scripts": scripts,
        "failures": failures,
        "interpretation": "PASS means a weak model can route from short skill instructions to deterministic scripts and consume a compact report or packet without raw profile data.",
    }
    print(json.dumps(result, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
