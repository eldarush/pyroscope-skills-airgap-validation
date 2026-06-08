#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
IMAGE_TOOL = SCRIPT_DIR / "pyroscope_image_tool.py"
GIT_WORKFLOW = SCRIPT_DIR / "pyroscope_git_workflow.py"


def run(args, cwd=None, check=True):
    proc = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"command failed: {' '.join(str(a) for a in args)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return proc


def git(repo, *args, check=True):
    return run(["git", *args], repo, check)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def init_repo(root):
    root.mkdir(parents=True, exist_ok=True)
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "codex@example.invalid")
    git(repo, "config", "user.name", "Codex Smoke")
    git(repo, "checkout", "-b", "main")
    write(
        repo / "Dockerfile",
        """FROM python:3.12-slim
WORKDIR /app
COPY . .
CMD ["python", "app.py"]
""",
    )
    write(repo / "requirements.txt", "flask\n")
    write(repo / "app.py", "print('ok')\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "initial")
    return repo


def instrument(repo):
    run(
        [
            sys.executable,
            str(IMAGE_TOOL),
            "instrument",
            "--repo",
            str(repo),
            "--mode",
            "github-test",
            "--github-ci",
            "--service-name",
            "checkout",
            "--image-tag",
            "checkout-pyroscope",
        ],
        repo,
    )


def audit(repo, *extra, check=True):
    proc = run(
        [
            sys.executable,
            str(GIT_WORKFLOW),
            "audit",
            "--repo",
            str(repo),
            "--provider",
            "github",
            "--service-name",
            "checkout",
            "--base-branch",
            "main",
            "--verification",
            "python pyroscope_image_tool_smoke.py",
            *extra,
        ],
        repo,
        check=check,
    )
    return json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else proc


def commit_all(repo, message):
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", message)


def expect_failure(repo, expected_text):
    proc = run(
        [
            sys.executable,
            str(GIT_WORKFLOW),
            "audit",
            "--repo",
            str(repo),
            "--provider",
            "github",
            "--service-name",
            "checkout",
            "--base-branch",
            "main",
        ],
        repo,
        check=False,
    )
    if proc.returncode == 0:
        raise AssertionError("expected audit failure")
    if expected_text.lower() not in (proc.stdout + proc.stderr).lower():
        raise AssertionError(f"failure did not include {expected_text!r}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")


def main():
    with tempfile.TemporaryDirectory(prefix="pyroscope-git-workflow-") as tmp:
        root = Path(tmp)
        results = []

        repo = init_repo(root)
        git(repo, "checkout", "-b", "pyroscope/checkout-image")
        instrument(repo)
        commit_all(repo, "add pyroscope image")
        body = root / "pr-body.md"
        good = audit(repo, "--body-output", str(body))
        if not good["ok"]:
            raise AssertionError(good)
        if good["disallowed_changed_files"]:
            raise AssertionError(f"unexpected disallowed files: {good['disallowed_changed_files']}")
        if good["title"] != "Add Pyroscope profiling image for checkout":
            raise AssertionError(f"unexpected title: {good['title']}")
        if "--label" not in good["command_plan"] or "devops-review" not in good["command_plan"]:
            raise AssertionError("GitHub command plan did not include required labels")
        if "gho_" in body.read_text(encoding="utf-8"):
            raise AssertionError("PR body leaked a token-like value")
        results.append({"case": "github-feature-branch-pr-plan", "ok": True})

        repo_main = init_repo(root / "main-refusal")
        write(repo_main / ".github" / "workflows" / "pyroscope-image.yml", "jobs: {}\n")
        commit_all(repo_main, "bad main change")
        expect_failure(repo_main, "protected")
        results.append({"case": "protected-branch-refuses", "ok": True})

        repo_sensitive = init_repo(root / "secret-refusal")
        git(repo_sensitive, "checkout", "-b", "pyroscope/secret")
        secret_name = "API_" + "TOKEN"
        secret_value = "gho_" + "abcdefghijklmnopqrstuvwxyz"
        write(repo_sensitive / "Dockerfile", (repo_sensitive / "Dockerfile").read_text(encoding="utf-8") + f"\nENV {secret_name}={secret_value}\n")
        commit_all(repo_sensitive, "bad secret")
        expect_failure(repo_sensitive, "secret")
        results.append({"case": "secret-diff-refuses", "ok": True})

        repo_tag = init_repo(root / "tag-refusal")
        git(repo_tag, "checkout", "-b", "pyroscope/bad-tag")
        write(repo_tag / ".github" / "workflows" / "pyroscope-image.yml", "run: docker build -t checkout:latest .\n")
        commit_all(repo_tag, "bad tag")
        expect_failure(repo_tag, "-pyroscope")
        results.append({"case": "production-tag-refuses", "ok": True})

    print(json.dumps({"ok": True, "cases": results}, indent=2))


if __name__ == "__main__":
    main()
