#!/usr/bin/env python3
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path


PACKET_TOOL = Path(__file__).resolve().parent / "pyroscope_weak_model_packet.py"


def load_packet_module():
    spec = importlib.util.spec_from_file_location("packet_tool", PACKET_TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_packet(report, output):
    proc = subprocess.run(
        [
            sys.executable,
            str(PACKET_TOOL),
            "--report",
            str(report),
            "--repo",
            str(report.parent),
            "--task",
            "analyze",
            "--max-hotspots",
            "10",
            "--max-chars",
            "8000",
            "--max-context-tokens",
            "128000",
            "--output",
            str(output),
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if proc.returncode != 0:
        raise AssertionError(f"packet generation failed\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return json.loads(output.read_text(encoding="utf-8"))


def expect_failure(report, expected_text):
    proc = subprocess.run(
        [sys.executable, str(PACKET_TOOL), "--report", str(report)],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if proc.returncode == 0:
        raise AssertionError("expected packet generation to fail")
    if expected_text.lower() not in (proc.stdout + proc.stderr).lower():
        raise AssertionError(f"failure did not include {expected_text!r}")


def main():
    packet_module = load_packet_module()
    if packet_module.as_float("14.2 %") != 14.2:
        raise AssertionError("percent parsing failed")
    if packet_module.split_markdown_row("| `a|b` | c\\|d | e |") != ["`a|b`", "c\\|d", "e"]:
        raise AssertionError("markdown table splitting failed")

    with tempfile.TemporaryDirectory(prefix="pyroscope-packet-") as tmp:
        root = Path(tmp)
        report = root / "report.md"
        output = root / "packet.json"
        write(
            report,
            """# Pyroscope Profile Report

Service: `packet-smoke`
Window: `10m`

## Eligible Hotspots

| Function | Self % | Total % | Mapping | Source matches | Recommendation |
| --- | ---: | ---: | --- | --- | --- |
| `safe_serializer` | 14.2 % | 22.5% | unique | src/core/serializer.py | Reuse immutable options when tests prove equivalence. |
| `generated_mapper` | 12% | 18% | unique | src/generated/mapper.py | Looks local but generated code must not be edited. |
| `spark_job` | 7% | 12% | unique | src/spark/Job.scala | Candidate touches repartition semantics. |
| `ambiguous_json` | 9% | 9% | ambiguous | src/a.py, src/b.py | Plan-only because mapping is ambiguous. |
| `partial_index_hotspot` | 8% | 14% | partial | src/core/partial.py | Plan-only because the source index was truncated. |
| `missing_source_hotspot` | 7% | 12% | missing | none | Plan-only because no local source mapping exists. |
| `pipe_case` | 6% | 11% | unique | src/core/pipe.py | Contains `a|b` inside code span but remains parseable. |

## Required Validation

- tests
""",
        )
        packet = run_packet(report, output)
        candidates = {row["function"] for row in packet["candidate_hotspots"]}
        plan_only = {row["function"] for row in packet["plan_only_hotspots"]}
        if "safe_serializer" not in candidates or "pipe_case" not in candidates:
            raise AssertionError(f"safe candidates missing: {candidates}")
        if (
            "generated_mapper" not in plan_only
            or "spark_job" not in plan_only
            or "ambiguous_json" not in plan_only
            or "partial_index_hotspot" not in plan_only
            or "missing_source_hotspot" not in plan_only
        ):
            raise AssertionError(f"unsafe rows not plan-only: {plan_only}")

        missing_column = root / "missing-column.md"
        write(
            missing_column,
            """# Report
Service: `bad`
Window: `1m`
## Eligible Hotspots
| Function | Self % | Total % | Mapping | Source matches |
| --- | ---: | ---: | --- | --- |
| `x` | 1 | 1 | unique | src/x.py |
""",
        )
        expect_failure(missing_column, "missing required columns")

        zero_rows = root / "zero-rows.md"
        write(
            zero_rows,
            """# Report
Service: `bad`
Window: `1m`
## Eligible Hotspots
| Function | Self % | Total % | Mapping | Source matches | Recommendation |
| --- | ---: | ---: | --- | --- | --- |
""",
        )
        expect_failure(zero_rows, "parsed zero rows")

        oversized = root / "oversized.md"
        write(
            oversized,
            """# Report
Service: `bad`
Window: `1m`
## Eligible Hotspots
| Function | Self % | Total % | Mapping | Source matches | Recommendation |
| --- | ---: | ---: | --- | --- | --- |
| `x` | 6 | 8 | unique | src/x.py | Mechanical local candidate. |
"""
            + ("x" * 13000),
        )
        expect_failure(oversized, "max-report-chars")

        raw_profile = root / "raw-profile.md"
        write(
            raw_profile,
            """# Report
Service: `bad`
Window: `1m`
root;handler;expensive 99
## Eligible Hotspots
| Function | Self % | Total % | Mapping | Source matches | Recommendation |
| --- | ---: | ---: | --- | --- | --- |
| `x` | 6 | 8 | unique | src/x.py | Mechanical local candidate. |
""",
        )
        expect_failure(raw_profile, "raw Pyroscope profile data")

    print(json.dumps({"ok": True, "cases": 8}, indent=2))


if __name__ == "__main__":
    main()
