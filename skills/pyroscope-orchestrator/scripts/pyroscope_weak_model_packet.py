#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path


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


def strip_code(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == "`" and value[-1] == "`":
        return value[1:-1].strip()
    return value


def truncate(value, limit):
    value = " ".join(str(value).split())
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3].rstrip() + "..."


def as_float(value):
    value = strip_code(value).strip()
    if not value:
        return 0.0
    value = value.replace("%", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    if match:
        value = match.group(0)
    try:
        return float(value)
    except ValueError:
        return 0.0


def parse_meta(lines):
    meta = {"service": "", "window": ""}
    for line in lines:
        if line.startswith("Service:"):
            meta["service"] = strip_code(line.split(":", 1)[1].strip())
        elif line.startswith("Window:"):
            meta["window"] = strip_code(line.split(":", 1)[1].strip())
    return meta


def split_markdown_row(line):
    text = line.strip().strip("|")
    cells = []
    current = []
    in_code = False
    escaped = False
    for char in text:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            continue
        if char == "`":
            in_code = not in_code
            current.append(char)
            continue
        if char == "|" and not in_code:
            cells.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    cells.append("".join(current).strip())
    return cells


def split_sources(value):
    cleaned = strip_code(value)
    if cleaned.lower() in {"", "none"}:
        return []
    return [item.strip() for item in cleaned.split(",") if item.strip()]


def unsafe_surface(function, source_matches, recommendation):
    combined = " ".join([function, " ".join(source_matches), recommendation]).lower().replace("\\", "/")
    unsafe_terms = [
        "generated",
        "vendor",
        "/test/",
        "/tests/",
        "auth",
        "security",
        "persistence",
        "repository",
        "retry",
        "timeout",
        "lock",
        "concurrency",
        "public contract",
        "business behavior",
        "cache invalidation",
        "database",
        "include",
        "join",
        "filter",
        "repartition",
        "checkpoint",
        "state ttl",
        "window",
        "timer",
        "rocksdb",
        "/spark/",
        "/flink/",
    ]
    blocked_phrases = [
        "plan only",
        "plan-only",
        "do not edit",
        "do not auto-edit",
        "blocked",
        "unsafe",
        "semantic",
    ]
    return any(term in combined for term in unsafe_terms + blocked_phrases)


def parse_hotspots(text):
    lines = text.splitlines()
    in_hotspots = False
    saw_hotspots_section = False
    header = None
    rows = []
    required_columns = ["Function", "Self %", "Total %", "Mapping", "Source matches", "Recommendation"]

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_hotspots and "Eligible Hotspots" not in stripped:
                break
            in_hotspots = "Eligible Hotspots" in stripped
            saw_hotspots_section = saw_hotspots_section or in_hotspots
            continue
        if not in_hotspots or not stripped.startswith("|"):
            continue

        raw_cells = split_markdown_row(stripped)
        if raw_cells and all(set(cell) <= {"-", ":"} for cell in raw_cells if cell):
            continue
        if raw_cells and raw_cells[0].lower() == "function":
            header = raw_cells
            missing = [column for column in required_columns if column not in header]
            if missing:
                raise SystemExit(f"hotspot table missing required columns: {missing}")
            continue
        if not header:
            continue

        cells = split_markdown_row(stripped)
        if len(cells) != len(header):
            raise SystemExit(f"hotspot row has {len(cells)} columns but header has {len(header)}: {stripped}")
        row = dict(zip(header, cells))
        mapping = strip_code(row.get("Mapping", "")).lower()
        source_matches = split_sources(row.get("Source matches", ""))
        recommendation = strip_code(row.get("Recommendation", ""))
        function = strip_code(row.get("Function", ""))
        self_percent = as_float(row.get("Self %", ""))
        total_percent = as_float(row.get("Total %", ""))
        if row.get("Self %", "").strip() and self_percent == 0.0 and "0" not in row.get("Self %", ""):
            raise SystemExit(f"could not parse Self % value: {row.get('Self %')}")
        if row.get("Total %", "").strip() and total_percent == 0.0 and "0" not in row.get("Total %", ""):
            raise SystemExit(f"could not parse Total % value: {row.get('Total %')}")
        safe_mapping = mapping == "unique" and len(source_matches) == 1
        blocked_surface = unsafe_surface(function, source_matches, recommendation)
        rows.append(
            {
                "function": function,
                "self_percent": self_percent,
                "total_percent": total_percent,
                "mapping": mapping or "unknown",
                "source_matches": truncate(", ".join(source_matches) or "none", 180),
                "recommendation": truncate(recommendation, 220),
                "implementation_eligibility": "candidate" if safe_mapping and not blocked_surface else "plan-only",
                "eligibility_reason": "unique-safe-local" if safe_mapping and not blocked_surface else "mapping-or-safety-gate",
            }
        )
    if not saw_hotspots_section:
        raise SystemExit("report does not contain an Eligible Hotspots section")
    if header is None:
        raise SystemExit("Eligible Hotspots section does not contain a hotspot table header")
    if not rows:
        raise SystemExit("Eligible Hotspots table parsed zero rows")
    return rows


def compact_rows(rows, max_hotspots):
    candidates = [row for row in rows if row["implementation_eligibility"] == "candidate"]
    blocked = [row for row in rows if row["implementation_eligibility"] != "candidate"]
    return candidates[:max_hotspots], blocked[:max_hotspots]


def report_safety_failures(text, max_report_chars):
    failures = []
    if len(text) > max_report_chars:
        failures.append(f"report exceeds --max-report-chars ({len(text)} > {max_report_chars})")
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            failures.append("report contains a likely secret")
            break
    for pattern in RAW_PROFILE_PATTERNS:
        if pattern.search(text):
            failures.append("report appears to contain raw Pyroscope profile data")
            break
    return failures


def build_packet(args):
    report_path = Path(args.report).resolve()
    text = report_path.read_text(encoding="utf-8")
    report_failures = report_safety_failures(text, args.max_report_chars)
    if report_failures:
        raise SystemExit("; ".join(report_failures))
    meta = parse_meta(text.splitlines())
    rows = parse_hotspots(text)
    candidates, blocked = compact_rows(rows, args.max_hotspots)

    service = args.service or meta["service"]
    window = args.window or meta["window"]
    packet = {
        "packet_version": 1,
        "purpose": "bounded Pyroscope profile-analysis handoff for weak or airgapped agents",
        "model_execution": {
            "performed_by_this_script": False,
            "local_model_required": False,
            "note": "This script only prepares deterministic context. It does not call, install, or run any model.",
        },
        "task": args.task,
        "repo": str(Path(args.repo).resolve()) if args.repo else "",
        "service": service,
        "profile_window": window,
        "report_path": str(report_path),
        "report_context_policy": {
            "trace_path_only": True,
            "report_chars": len(text),
            "max_report_chars": args.max_report_chars,
            "raw_profile_data_allowed": False,
            "weak_model_should_open_report": False,
        },
        "hotspot_counts": {
            "total_rows": len(rows),
            "implementation_candidates": len([row for row in rows if row["implementation_eligibility"] == "candidate"]),
            "plan_only": len([row for row in rows if row["implementation_eligibility"] != "candidate"]),
        },
        "parse_contract": {
            "required_columns": ["Function", "Self %", "Total %", "Mapping", "Source matches", "Recommendation"],
            "zero_rows_allowed": False,
            "unique_mapping_requires_exactly_one_source": True,
            "unsafe_surfaces_force_plan_only": True,
        },
        "candidate_hotspots": candidates,
        "plan_only_hotspots": blocked,
        "agent_steps": [
            "Use this packet as the bounded weak-model context; do not open the full report during a weak-model run.",
            "Open only mapped source files needed for the top candidate, and only after confirming the packet marks the row as a candidate.",
            "For analysis tasks, produce recommendations and validation queries only.",
            "For implement-safe tasks, edit only uniquely mapped local code when tests prove behavior preservation.",
            "Keep image instrumentation and code optimization in separate branches and PRs.",
        ],
        "hard_refusal_rules": [
            "Do not infer missing source mappings.",
            "Do not load raw Pyroscope JSON, folded stacks, pprof dumps, or unbounded reports into a weak-model prompt.",
            "Do not edit ambiguous, generated, vendor, test, auth, persistence, retry, timeout, or concurrency logic.",
            "Do not change public contracts or business behavior.",
            "Do not publish or suggest production-like image tags; Pyroscope image tags must end in -pyroscope.",
            "Do not deploy, merge, or auto-approve.",
        ],
        "required_evidence_for_code_edits": [
            "hotspot row with unique source mapping",
            "local mechanical change only",
            "focused tests before and after",
            "diff review proving no core behavior changed",
            "post-deployment Pyroscope window to confirm improvement",
        ],
    }
    return packet


def render(packet, pretty):
    if pretty:
        return json.dumps(packet, indent=2, sort_keys=True)
    return json.dumps(packet, sort_keys=True, separators=(",", ":"))


def estimated_tokens(text):
    # Conservative enough for context-budget guarding without tokenizer deps.
    return max(1, (len(text) + 3) // 4)


def write_packet(packet, args):
    output = render(packet, args.pretty)
    if len(output) > args.max_chars:
        raise SystemExit(
            f"packet exceeds --max-chars ({len(output)} > {args.max_chars}); rerun with a smaller --max-hotspots"
        )
    token_estimate = estimated_tokens(output)
    if token_estimate > args.max_context_tokens:
        raise SystemExit(
            f"packet exceeds --max-context-tokens ({token_estimate} > {args.max_context_tokens}); rerun with a smaller --max-hotspots"
        )
    packet["context_budget"] = {
        "chars": len(output),
        "estimated_tokens": token_estimate,
        "max_context_tokens": args.max_context_tokens,
        "fits_context": True,
    }
    output = render(packet, args.pretty)
    if len(output) > args.max_chars:
        raise SystemExit(
            f"packet with context budget exceeds --max-chars ({len(output)} > {args.max_chars}); rerun with a smaller --max-hotspots"
        )
    token_estimate = estimated_tokens(output)
    if token_estimate > args.max_context_tokens:
        raise SystemExit(
            f"packet with context budget exceeds --max-context-tokens ({token_estimate} > {args.max_context_tokens}); rerun with a smaller --max-hotspots"
        )
    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


def main():
    parser = argparse.ArgumentParser(
        description="Create a compact, deterministic handoff packet from a Pyroscope analyzer report."
    )
    parser.add_argument("--report", required=True, help="Markdown report from pyroscope_profile_tool.py report")
    parser.add_argument("--repo", default="", help="Repository path associated with the report")
    parser.add_argument("--service", default="", help="Override service name")
    parser.add_argument("--window", default="", help="Override profile window")
    parser.add_argument("--task", choices=["analyze", "implement-safe"], default="analyze")
    parser.add_argument("--max-hotspots", type=int, default=8)
    parser.add_argument("--max-chars", type=int, default=8000)
    parser.add_argument("--max-report-chars", type=int, default=12000)
    parser.add_argument("--max-context-tokens", type=int, default=128000)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()

    if args.max_hotspots < 1:
        print("--max-hotspots must be at least 1", file=sys.stderr)
        raise SystemExit(2)
    packet = build_packet(args)
    write_packet(packet, args)


if __name__ == "__main__":
    main()
