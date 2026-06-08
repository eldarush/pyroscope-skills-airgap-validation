#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path


BENCHMARK = Path(__file__).resolve().parent / "pyroscope_weak_model_benchmark.py"


def load_benchmark():
    spec = importlib.util.spec_from_file_location("benchmark", BENCHMARK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_transcript(root, name, stdout, stderr="", exit_code=0):
    path = root / name
    path.write_text(
        f"""# weak-model-session transcript

Command: smoke
ExitCode: {exit_code}

## stdout
{stdout}

## stderr
{stderr}
""",
        encoding="utf-8",
    )
    return path


def main():
    benchmark = load_benchmark()
    valid_stdout = """case_id=java_medium_image
route=image
runtime=java
decision=instrument
source_edit=no
deploy=no
tag_rule=service-pyroscope
ambiguous_editable=no
missing_editable=no
tests_required=no
requires_pprof=no"""
    case = next(item for item in benchmark.CASES if item["id"] == "java_medium_image")
    with tempfile.TemporaryDirectory(prefix="pyroscope-harness-") as tmp:
        root = Path(tmp)
        valid = write_transcript(root, "valid.md", valid_stdout)
        result = benchmark.score_case(case, valid, 0)
        if result["rating"] != 10:
            raise AssertionError(f"valid transcript did not score 10: {result}")

        duplicate = write_transcript(root, "duplicate.md", valid_stdout + "\nroute=image")
        duplicate_result = benchmark.score_case(case, duplicate, 0)
        if duplicate_result["rating"] == 10 or not any("duplicate key" in item for item in duplicate_result["failures"]):
            raise AssertionError("duplicate key was not rejected")

        prose = write_transcript(root, "prose.md", "Here is the answer:\n" + valid_stdout)
        prose_result = benchmark.score_case(case, prose, 0)
        if prose_result["rating"] == 10 or not any("non key=value output line" in item for item in prose_result["failures"]):
            raise AssertionError("extra prose was not rejected")

        quota = write_transcript(
            root,
            "quota.md",
            "",
            """OpenAI Codex
## Skill: pyroscope-orchestrator
This heading is part of the echoed prompt and must not truncate stderr.
ERROR: You've hit your usage limit for GPT-5.3-Codex-Spark. Try again later.""",
            exit_code=1,
        )
        text = quota.read_text(encoding="utf-8")
        stderr = benchmark.transcript_stderr(text)
        if "usage limit" not in stderr.lower():
            raise AssertionError("stderr extraction truncated before provider limit")
        proc = type("Proc", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        if not benchmark.provider_blocked(proc, text):
            raise AssertionError("provider limit was not classified as blocked")

        echoed_skill = write_transcript(
            root,
            "echoed-skill.md",
            valid_stdout,
            "Injected skill says: If hosted providers hit usage limits, report blocked.",
            exit_code=0,
        )
        ok_proc = type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        if benchmark.provider_blocked(ok_proc, echoed_skill.read_text(encoding="utf-8")):
            raise AssertionError("valid model stdout was misclassified as provider-blocked")

    print(json.dumps({"ok": True, "cases": 5}, indent=2))


if __name__ == "__main__":
    main()
