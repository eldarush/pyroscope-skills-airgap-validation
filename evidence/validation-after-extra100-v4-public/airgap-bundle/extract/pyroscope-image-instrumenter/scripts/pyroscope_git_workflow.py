#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


PROTECTED_BRANCHES = {"main", "master", "develop", "release", "prod", "production"}
REQUIRED_PR_LABELS = ["pyroscope", "profiling", "devops-review"]
SECRET_PATTERNS = [
    re.compile(r"(?i)(password|passwd|secret|token|apikey|api_key)\s*[:=]\s*['\"]?[^'\"\s,}]+"),
    re.compile(r"gho_[A-Za-z0-9_]+"),
    re.compile(r"glpat-[A-Za-z0-9_-]+"),
]
PRODUCTION_TAG_PATTERN = re.compile(r"(?i)(:|=|\s)(latest|prod|production)(\s|$|['\"]|,)")


def run(args, cwd, check=True):
    proc = subprocess.run(args, cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if check and proc.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(args)}\n{proc.stdout}\n{proc.stderr}")
    return proc


def git(repo, *args, check=True):
    return run(["git", *args], repo, check=check)


def current_branch(repo):
    return git(repo, "branch", "--show-current").stdout.strip()


def default_base(repo, requested):
    if requested:
        return requested
    for branch in ["origin/main", "origin/master", "main", "master"]:
        if git(repo, "rev-parse", "--verify", branch, check=False).returncode == 0:
            return branch
    return "HEAD~1"


def changed_files(repo, base):
    proc = git(repo, "diff", "--name-only", f"{base}...HEAD", check=False)
    if proc.returncode != 0:
        proc = git(repo, "diff", "--name-only", base, "HEAD", check=False)
    if proc.returncode != 0:
        proc = git(repo, "diff", "--name-only", "HEAD", check=False)
    return [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]


def diff_text(repo, base):
    proc = git(repo, "diff", f"{base}...HEAD", check=False)
    if proc.returncode != 0:
        proc = git(repo, "diff", base, "HEAD", check=False)
    if proc.returncode != 0:
        proc = git(repo, "diff", "HEAD", check=False)
    return proc.stdout


def allowed_file(path):
    normalized = path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    if name.startswith("Dockerfile"):
        return True
    if normalized == "pyroscope-agent.yaml":
        return True
    if normalized.startswith(".pyroscope/"):
        return True
    if normalized == ".gitlab-ci.yml":
        return True
    if normalized == ".github/workflows/pyroscope-image.yml":
        return True
    return False


def added_lines(diff):
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            yield line[1:]


def scan_diff(diff):
    failures = []
    for pattern in SECRET_PATTERNS:
        if pattern.search(diff):
            failures.append("diff contains a likely secret")
            break
    for line in added_lines(diff):
        lowered = line.lower()
        image_line = lowered.lstrip().startswith("image:") and bool(line.split(":", 1)[1].strip())
        if ("docker build" in lowered and " -t " in lowered) or image_line:
            if "-pyroscope" not in line:
                failures.append(f"new image tag line does not contain -pyroscope: {line.strip()}")
        if PRODUCTION_TAG_PATTERN.search(line) and "pyroscope" not in lowered:
            failures.append(f"new line appears to use a production-like tag: {line.strip()}")
    return failures


def branch_failures(branch):
    failures = []
    if not branch:
        failures.append("not on a named branch")
    if branch in PROTECTED_BRANCHES:
        failures.append(f"current branch is protected: {branch}")
    if branch and "pyroscope" not in branch.lower():
        failures.append("feature branch name must contain pyroscope")
    return failures


def pr_body(service, base, branch, changed, verification):
    changed_list = "\n".join(f"- `{path}`" for path in changed) or "- none"
    verification_list = "\n".join(f"- {item}" for item in verification) or "- Not run"
    return f"""Adds an isolated Pyroscope profiling image workflow for `{service}`.

Safety:
- Profiling image tags must end in `-pyroscope`.
- No deployment, merge, or production tag change is included.
- Source code behavior is not changed by this PR.
- Labels requested: {', '.join(REQUIRED_PR_LABELS)}

Branch:
- Base: `{base}`
- Feature: `{branch}`

Changed files:
{changed_list}

Verification:
{verification_list}
"""


def command_plan(args, body_path):
    title = f"Add Pyroscope profiling image for {args.service_name}"
    if args.provider == "github":
        return [
            "gh",
            "pr",
            "create",
            "--base",
            args.base_branch,
            "--head",
            args.branch,
            "--title",
            title,
            "--body-file",
            str(body_path),
            "--label",
            "pyroscope",
            "--label",
            "profiling",
            "--label",
            "devops-review",
        ]
    return {
        "method": "POST",
        "url": f"$GITLAB_API_URL/projects/<project-id>/merge_requests",
        "headers": ["PRIVATE-TOKEN: $GITLAB_TOKEN"],
        "form": {
            "source_branch": args.branch,
            "target_branch": args.base_branch,
            "title": title,
            "description_file": str(body_path),
            "labels": ",".join(REQUIRED_PR_LABELS),
        },
    }


def audit(args):
    repo = Path(args.repo).resolve()
    base = default_base(repo, args.base_branch)
    branch = current_branch(repo)
    changed = changed_files(repo, base)
    diff = diff_text(repo, base)
    failures = []
    failures.extend(branch_failures(branch))
    disallowed = [path for path in changed if not allowed_file(path)]
    if disallowed:
        failures.append(f"changed files outside allowed image instrumentation surface: {disallowed}")
    failures.extend(scan_diff(diff))
    if not changed:
        failures.append("no changed files found for PR/MR")
    body = pr_body(args.service_name, base, branch, changed, args.verification)
    if any(pattern.search(body) for pattern in SECRET_PATTERNS):
        failures.append("generated PR/MR body contains a likely secret")
    body_path = Path(args.body_output).resolve() if args.body_output else None
    if body_path:
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_text(body, encoding="utf-8")
    plan = command_plan(
        argparse.Namespace(**{**vars(args), "base_branch": base, "branch": branch}),
        body_path or Path("<body-file>"),
    )
    result = {
        "ok": not failures,
        "repo": str(repo),
        "provider": args.provider,
        "base_branch": base,
        "branch": branch,
        "changed_files": changed,
        "allowed_changed_files": [path for path in changed if allowed_file(path)],
        "disallowed_changed_files": disallowed,
        "required_labels": REQUIRED_PR_LABELS,
        "title": f"Add Pyroscope profiling image for {args.service_name}",
        "body_output": str(body_path) if body_path else "",
        "command_plan": plan,
        "failures": failures,
    }
    print(json.dumps(result, indent=2))
    if failures:
        raise SystemExit(1)


def execute(args):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix="-pyroscope-pr.md", delete=False) as handle:
        body_path = Path(handle.name)
    args.body_output = str(body_path)
    audit_proc = run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "audit",
            "--repo",
            args.repo,
            "--provider",
            args.provider,
            "--service-name",
            args.service_name,
            "--base-branch",
            args.base_branch,
            "--body-output",
            str(body_path),
            *sum([["--verification", item] for item in args.verification], []),
        ],
        Path(args.repo).resolve(),
    )
    parsed = json.loads(audit_proc.stdout)
    if args.provider != "github":
        raise SystemExit("execute currently supports GitHub only; use audit output for GitLab REST API.")
    command = parsed["command_plan"]
    proc = run(command, Path(args.repo).resolve(), check=False)
    result = {"ok": proc.returncode == 0, "command": command, "stdout": proc.stdout, "stderr": proc.stderr}
    print(json.dumps(result, indent=2))
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def main():
    parser = argparse.ArgumentParser(description="Audit or create safe Pyroscope image PR/MR workflow metadata.")
    sub = parser.add_subparsers(required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=".")
    common.add_argument("--provider", choices=["github", "gitlab"], default="github")
    common.add_argument("--service-name", required=True)
    common.add_argument("--base-branch")
    common.add_argument("--verification", action="append", default=[])
    a = sub.add_parser("audit", parents=[common])
    a.add_argument("--body-output")
    a.set_defaults(func=audit)
    e = sub.add_parser("execute", parents=[common])
    e.set_defaults(func=execute)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
