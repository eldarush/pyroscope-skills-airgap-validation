#!/usr/bin/env python3
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


IMAGE_TOOL = Path(__file__).resolve().parent / "pyroscope_image_tool.py"
DOTNET_SMOKE_BASE = "mcr.microsoft.com/dotnet/aspnet:10.0-pyroscope-smoke"
DOTNET_MUSL_SMOKE_BASE = "mcr.microsoft.com/dotnet/aspnet:10.0-alpine-pyroscope-smoke"


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
        raise AssertionError(
            f"command failed: {' '.join(str(arg) for arg in args)}\n"
            f"stdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
        )
    return proc


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def touch(path, text="placeholder"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="ascii")


def require_docker():
    if not shutil.which("docker"):
        raise SystemExit("docker is required for pyroscope_image_docker_smoke.py")
    run(["docker", "version", "--format", "{{.Server.Version}}"])


def create_local_dotnet_smoke_base(root, tag):
    dockerfile = root / f"{tag.replace('/', '_').replace(':', '_')}.Dockerfile"
    write(dockerfile, "FROM scratch\n")
    run(["docker", "build", "-q", "-t", tag, "-f", str(dockerfile), "."], cwd=root)
    return tag


def base_dockerfile(runtime):
    if runtime == "dotnet":
        return (
            f"FROM {DOTNET_SMOKE_BASE}\n"
            "USER 65532:65532\n"
            "CMD [\"/app/App.dll\"]\n"
        )
    if runtime == "dotnet-musl":
        return (
            f"FROM {DOTNET_MUSL_SMOKE_BASE}\n"
            "USER 65532:65532\n"
            "CMD [\"/app/App.dll\"]\n"
        )
    setup = ""
    if runtime == "python":
        setup = (
            "RUN mkdir -p /usr/local/bin /app && "
            "printf '#!/bin/sh\\nexit 0\\n' > /usr/local/bin/pip && "
            "chmod +x /usr/local/bin/pip\n"
            "WORKDIR /app\n"
            "COPY app.py /app/app.py\n"
        )
    return (
        "FROM alpine:3.20\n"
        f"{setup}"
        "USER 65532:65532\n"
        "CMD [\"/bin/sh\", \"-c\", \"true\"]\n"
    )


def prepare_airgap_assets(repo, runtime):
    if runtime == "python":
        touch(repo / ".pyroscope" / "python" / "wheels" / "pyroscope_io-0.0.0-py3-none-any.whl")
        write(repo / "app.py", "print('ok')\n")
        write(repo / "requirements.txt", "flask\n")
    elif runtime in {"java", "spark", "flink"}:
        touch(repo / ".pyroscope" / "java" / "pyroscope.jar", "dummy jar for Dockerfile build smoke")
        if runtime == "java":
            write(repo / "pom.xml", "<project></project>\n")
    elif runtime == "dotnet":
        touch(repo / ".pyroscope" / "dotnet" / "Pyroscope.Profiler.Native.so")
        touch(repo / ".pyroscope" / "dotnet" / "Pyroscope.Linux.ApiWrapper.x64.so")
        write(repo / "App.csproj", "<Project Sdk=\"Microsoft.NET.Sdk.Web\"></Project>\n")
    elif runtime == "dotnet-musl":
        touch(repo / ".pyroscope" / "dotnet" / "musl" / "Pyroscope.Profiler.Native.so")
        touch(repo / ".pyroscope" / "dotnet" / "musl" / "Pyroscope.Linux.ApiWrapper.x64.so")
        write(repo / "App.csproj", "<Project Sdk=\"Microsoft.NET.Sdk.Web\"></Project>\n")
    elif runtime == "go":
        write(repo / "go.mod", "module example.com/app\n")
        write(repo / "main.go", "package main\nfunc main(){}\n")
        write(repo / ".pyroscope" / "go" / "collector" / "collector.yaml", "endpoint: /debug/pprof/profile\n")


def instrument(repo, runtime):
    tool_runtime = "dotnet" if runtime == "dotnet-musl" else runtime
    args = [
        sys.executable,
        str(IMAGE_TOOL),
        "instrument",
        "--repo",
        str(repo),
        "--runtime",
        tool_runtime,
        "--mode",
        "airgap",
        "--service-name",
        f"docker-smoke-{runtime}",
        "--image-tag",
        f"docker-smoke-{runtime}-pyroscope",
    ]
    if runtime == "go":
        args.append("--allow-go-conditional")
    run(args, cwd=repo)


def assert_dockerfile_safety(repo, runtime):
    text = (repo / "Dockerfile").read_text(encoding="utf-8")
    if "pyroscope-image-instrumenter:start" not in text:
        raise AssertionError(f"{runtime}: missing Pyroscope block")
    if "docker-smoke-" not in text:
        raise AssertionError(f"{runtime}: missing deterministic service name")
    if "latest-pyroscope" in text or ":latest" in text:
        raise AssertionError(f"{runtime}: unsafe latest tag appeared in Dockerfile")
    if runtime in {"spark", "flink"} and "JAVA_TOOL_OPTIONS" in text:
        raise AssertionError(f"{runtime}: duplicate-prone JAVA_TOOL_OPTIONS path used")
    if runtime == "java" and 'JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS} ${PYROSCOPE_JAVA_AGENT_OPTION}"' not in text:
        raise AssertionError("java: existing JAVA_TOOL_OPTIONS would not be preserved")
    if runtime == "go" and "PYROSCOPE_GO_CONDITIONAL=collector-bundle" not in text:
        raise AssertionError("go: approved collector bundle path was not used")
    if runtime == "dotnet-musl" and "COPY .pyroscope/dotnet/musl/ /opt/pyroscope/dotnet/" not in text:
        raise AssertionError("dotnet-musl: musl airgap asset path was not used")


def build_and_run(repo, runtime):
    tag = f"pyroscope-docker-smoke-{runtime}:local-pyroscope"
    run(["docker", "build", "-q", "-t", tag, "."], cwd=repo)
    # .NET uses LD_PRELOAD and dummy native files in this syntax smoke, so build is the meaningful check.
    if runtime not in {"dotnet", "dotnet-musl"}:
        run(["docker", "run", "--rm", tag])
    return tag


def remove_images(tags):
    for tag in reversed(tags):
        run(["docker", "rmi", tag], check=False)


def main():
    require_docker()
    tags = []
    cases = []
    try:
        with tempfile.TemporaryDirectory(prefix="pyroscope-image-docker-smoke-") as tmp:
            root = Path(tmp)
            tags.append(create_local_dotnet_smoke_base(root, DOTNET_SMOKE_BASE))
            tags.append(create_local_dotnet_smoke_base(root, DOTNET_MUSL_SMOKE_BASE))
            for runtime in ["python", "dotnet", "dotnet-musl", "java", "spark", "flink", "go"]:
                repo = root / runtime
                repo.mkdir()
                write(repo / "Dockerfile", base_dockerfile(runtime))
                prepare_airgap_assets(repo, runtime)
                instrument(repo, runtime)
                assert_dockerfile_safety(repo, runtime)
                tag = build_and_run(repo, runtime)
                tags.append(tag)
                cases.append(
                    {
                        "runtime": runtime,
                        "tag": tag,
                        "built": True,
                        "ran": runtime not in {"dotnet", "dotnet-musl"},
                        "dockerfile": str(repo / "Dockerfile"),
                    }
                )
    finally:
        remove_images(tags)
    print(json.dumps({"ok": True, "cases": cases}, indent=2))


if __name__ == "__main__":
    main()
