#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def skill_path(name):
    return ROOT / name


def audit(_args):
    required = {
        "pyroscope-image-instrumenter": [
            "SKILL.md",
            "scripts/pyroscope_image_tool.py",
            "scripts/pyroscope_image_tool_smoke.py",
            "scripts/pyroscope_image_docker_smoke.py",
            "scripts/pyroscope_git_workflow.py",
            "scripts/pyroscope_git_workflow_smoke.py",
        ],
        "pyroscope-profile-analyzer": [
            "SKILL.md",
            "scripts/pyroscope_profile_tool.py",
            "scripts/pyroscope_stress_fixture.py",
            "scripts/pyroscope_complex_profile_smoke.py",
            "scripts/pyroscope_profile_budget_stress.py",
            "scripts/pyroscope_local_roundtrip_smoke.py",
        ],
        "pyroscope-orchestrator": [
            "SKILL.md",
            "scripts/pyroscope_orchestrator.py",
            "scripts/pyroscope_weak_model_packet.py",
            "scripts/pyroscope_weak_model_audit.py",
            "scripts/pyroscope_weak_model_benchmark.py",
            "scripts/pyroscope_packet_tool_smoke.py",
            "scripts/pyroscope_weak_model_harness_smoke.py",
            "scripts/pyroscope_validation_suite.py",
            "scripts/pyroscope_airgap_bundle.py",
        ],
    }
    result = {}
    ok = True
    for skill, files in required.items():
        base = skill_path(skill)
        missing = [f for f in files if not (base / f).exists()]
        result[skill] = {"path": str(base), "missing": missing, "installed": not missing}
        ok = ok and not missing
    print(json.dumps({"ok": ok, "skills": result}, indent=2))
    if not ok:
        raise SystemExit(1)


def plan(args):
    task = args.task
    steps = []
    if task in {"image", "both"}:
        steps.append(
            {
                "skill": "pyroscope-image-instrumenter",
                "purpose": "Create feature branch, patch Dockerfile/CI, build/run local *-pyroscope image, open PR/MR.",
                "script": "scripts/pyroscope_image_tool.py",
                "pr_safety_script": "scripts/pyroscope_git_workflow.py",
            }
        )
    if task in {"analyze", "both"}:
        steps.append(
            {
                "skill": "pyroscope-profile-analyzer",
                "purpose": "Discover Pyroscope labels/profile types, summarize hotspot window, map eligible frames to source, plan or implement safe optimization.",
                "script": "scripts/pyroscope_profile_tool.py",
                "weak_model_packet": "scripts/pyroscope_weak_model_packet.py",
            }
        )
    print(
        json.dumps(
            {
                "repo": args.repo,
                "service": args.service,
                "task": task,
                "steps": steps,
                "safety": [
                    "feature branch only",
                    "no production tags",
                    "no deployment",
                    "no secrets in diff",
                    "separate image and optimization PRs",
                ],
            },
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(required=True)
    a = sub.add_parser("audit")
    a.set_defaults(func=audit)
    p = sub.add_parser("plan")
    p.add_argument("--repo", default=".")
    p.add_argument("--service", default="")
    p.add_argument("--task", choices=["image", "analyze", "both"], required=True)
    p.set_defaults(func=plan)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
