#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


START = "# pyroscope-image-instrumenter:start"
END = "# pyroscope-image-instrumenter:end"
JAVA_AGENT_VERSION = "2.5.2"
DOTNET_PROFILER_VERSION = "0.14.2"
SECRET_PATTERNS = [
    re.compile(r"(?i)(password|passwd|secret|token|apikey|api_key)\s*[:=]\s*['\"]?[^'\"\s]+"),
    re.compile(r"gho_[A-Za-z0-9_]+"),
    re.compile(r"glpat-[A-Za-z0-9_-]+"),
]
COMPOSE_FILENAMES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")


def run(args, cwd=None, check=True):
    proc = subprocess.run(args, cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if check and proc.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(args)}\n{proc.stdout}\n{proc.stderr}")
    return proc


def read(path):
    return Path(path).read_text(encoding="utf-8")


def write(path, value):
    Path(path).write_text(value, encoding="utf-8")


def find_dockerfile(repo):
    root = repo / "Dockerfile"
    if root.is_file():
        return root
    candidates = [
        p
        for p in repo.rglob("Dockerfile*")
        if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts
    ]
    return candidates[0] if len(candidates) == 1 else None


def find_compose_file(repo):
    for name in COMPOSE_FILENAMES:
        path = repo / name
        if path.is_file():
            return path
    return None


def relative_to_repo(repo, path):
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(repo).as_posix()
    except ValueError:
        raise SystemExit(f"Path is outside repository: {resolved}")


def resolve_inside_repo(repo, value, default="."):
    raw = value or default
    resolved = (repo / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    try:
        resolved.relative_to(repo)
    except ValueError:
        raise SystemExit(f"Path is outside repository: {resolved}")
    return resolved


def detect_runtime(repo, dockerfile=None):
    names = {p.name.lower() for p in repo.iterdir() if p.is_file()}
    docker_text = read(dockerfile).lower() if dockerfile and dockerfile.exists() else ""
    files = [str(p.relative_to(repo)).lower() for p in repo.rglob("*") if p.is_file()]

    # Prefer Dockerfile signals over broad repo-wide file names. Monorepos often
    # contain Spark/Flink/Go/etc. sidecars that are unrelated to the target image.
    if "flink" in docker_text:
        return "flink"
    if "spark" in docker_text:
        return "spark"
    if "mcr.microsoft.com/dotnet" in docker_text:
        return "dotnet"
    if (
        "eclipse-temurin" in docker_text
        or "openjdk" in docker_text
        or "maven" in docker_text
        or "gradle" in docker_text
    ):
        return "java"
    if "from python" in docker_text:
        return "python"
    if "from golang" in docker_text:
        return "go"

    manifest_hits = set()
    if any(f.endswith(".csproj") for f in files):
        manifest_hits.add("dotnet")
    if "pom.xml" in names or "build.gradle" in names or "build.gradle.kts" in names:
        manifest_hits.add("java")
    if "requirements.txt" in names or "pyproject.toml" in names or "setup.py" in names:
        manifest_hits.add("python")
    if "go.mod" in names:
        manifest_hits.add("go")
    if any("flink" in f for f in files):
        manifest_hits.add("flink")
    if any("spark" in f for f in files):
        manifest_hits.add("spark")
    if len(manifest_hits) == 1:
        return next(iter(manifest_hits))
    return "unknown"


def go_source_files(source_root):
    ignored = {".git", "vendor", "generated"}
    for path in source_root.rglob("*.go"):
        if ignored.intersection(path.parts):
            continue
        yield path


def has_go_pprof_marker(source_root):
    for path in go_source_files(source_root):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "net/http/pprof" in text or "/debug/pprof" in text:
            return True
    return False


def has_go_http_listener(source_root):
    listener_patterns = [
        re.compile(r"\bhttp\.ListenAndServe(?:TLS)?\s*\("),
        re.compile(r"\bListenAndServe(?:TLS)?\s*\("),
        re.compile(r"\.ListenAndServe(?:TLS)?\s*\("),
        re.compile(r"\bhttp\.Server\s*{"),
    ]
    for path in go_source_files(source_root):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in listener_patterns):
            return True
    return False


def has_go_collector_bundle(build_context):
    collector = build_context / ".pyroscope" / "go" / "collector"
    return collector.is_dir() and any(path.is_file() for path in collector.rglob("*"))


def go_support_mode(build_context, source_root):
    if has_go_collector_bundle(build_context):
        return "collector"
    if has_go_pprof_marker(source_root) and has_go_http_listener(source_root):
        return "pprof"
    return ""


def repo_has_dotnet_manifest(repo):
    return any(path.suffix.lower() == ".csproj" and ".git" not in path.parts for path in repo.rglob("*.csproj"))


def dotnet_profiler_image(libc):
    return f"pyroscope/pyroscope-dotnet:{DOTNET_PROFILER_VERSION}-{libc}"


def final_from_line(docker_text):
    stages = {}
    final = ""
    final_source = ""
    for line in docker_text.splitlines():
        if not re.match(r"^\s*FROM\b", line, re.I):
            continue
        normalized = line.strip().lower()
        final = normalized
        tokens = normalized.split()
        source = ""
        index = 1
        while index < len(tokens) and tokens[index].startswith("--"):
            index += 1
        if index < len(tokens):
            source = tokens[index]
        final_source = source
        alias = re.search(r"\s+as\s+([a-z0-9_.-]+)\s*$", normalized)
        if alias:
            stages[alias.group(1)] = normalized

    resolved = [final]
    seen = set()
    while final_source in stages and final_source not in seen:
        seen.add(final_source)
        final = stages[final_source]
        resolved.append(final)
        tokens = final.split()
        index = 1
        while index < len(tokens) and tokens[index].startswith("--"):
            index += 1
        final_source = tokens[index] if index < len(tokens) else ""
    return "\n".join(resolved)


def dotnet_support_mode(repo, docker_text):
    final = final_from_line(docker_text)
    if not final:
        return "", ""
    if any(marker in final for marker in ["nanoserver", "servercore", "windows"]):
        return "", "Unsupported .NET Pyroscope profiler image: the native profiler path is Linux-only."
    if any(marker in final for marker in ["linux/arm", "arm64", "aarch64", "arm/v"]):
        return "", "Unsupported .NET Pyroscope profiler image: the bundled native profiler path is Linux amd64 only."
    if any(marker in final for marker in ["$targetplatform", "${targetplatform}", "$buildplatform", "${buildplatform}"]):
        return "", "Unsupported .NET Pyroscope profiler image: variable target platforms are not proven Linux amd64."
    if "scratch" in final:
        return "", "Unsupported .NET Pyroscope profiler image: scratch final stages cannot load the native profiler."
    if "mcr.microsoft.com/dotnet" not in final and not repo_has_dotnet_manifest(repo):
        return "", "Unsupported .NET Pyroscope profiler image: no recognized .NET runtime base or project manifest was found."
    libc = "musl" if any(marker in final for marker in ["alpine", "musl"]) else "glibc"
    return libc, ""


def service_name(repo):
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", repo.name).strip("-").lower() or "app"


def git_value(repo, args):
    proc = run(["git"] + args, cwd=repo, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def labels(repo, runtime, service, image_tag):
    branch = git_value(repo, ["branch", "--show-current"]) or "unknown"
    sha = git_value(repo, ["rev-parse", "--short", "HEAD"]) or "unknown"
    return ",".join(
        [
            f"service_name={service}",
            f"runtime={runtime}",
            f"repo={repo.name}",
            f"branch={branch}",
            f"git_sha={sha}",
            f"image_tag={image_tag}",
            "environment=local",
            "profiling_mode=pyroscope-image",
        ]
    )


def metadata_text(repo, runtime, service, dockerfile):
    dockerfile_path = relative_to_repo(repo, dockerfile)
    return f"""schema_version: 1
service_name: {service}
runtime: {runtime}
dockerfile: {dockerfile_path}
image:
  repository: {service}
  pyroscope_tag_suffix: pyroscope
pyroscope:
  server_address_env: PYROSCOPE_SERVER_ADDRESS
  application_name_env: PYROSCOPE_APPLICATION_NAME
  labels_env: PYROSCOPE_LABELS
local_run:
  command: ""
  required_env: []
source:
  roots: ["src", "app", "."]
  exclude: ["test", "tests", "generated", "vendor", "node_modules", "bin", "obj", "target"]
profile_mapping:
  expected_labels: ["service_name", "runtime", "repo", "branch", "git_sha", "image_tag", "environment", "profiling_mode"]
"""


def strip_existing_block(text):
    return re.sub(rf"\n?{re.escape(START)}.*?{re.escape(END)}\n?", "\n", text, flags=re.S)


def runtime_block(runtime, service, image_tag, mode, go_mode="", dotnet_libc="glibc", dotnet_airgap_source=".pyroscope/dotnet"):
    pyroscope_labels = (
        f"runtime:{runtime},profiling_mode:pyroscope-image"
        if runtime == "dotnet"
        else f"runtime={runtime},profiling_mode=pyroscope-image"
    )
    common = [
        START,
        f"ARG PYROSCOPE_SERVER_ADDRESS=http://host.docker.internal:4040",
        f"ENV PYROSCOPE_SERVER_ADDRESS=${{PYROSCOPE_SERVER_ADDRESS}}",
        f"ENV PYROSCOPE_APPLICATION_NAME={service}",
        f"ENV PYROSCOPE_LABELS={pyroscope_labels}",
    ]
    if runtime == "python":
        if mode == "airgap":
            common.extend(
                [
                    "COPY .pyroscope/python/wheels/ /opt/pyroscope/wheels/",
                    "RUN pip install --no-index --find-links=/opt/pyroscope/wheels pyroscope-io",
                ]
            )
        else:
            common.append("RUN pip install --no-cache-dir pyroscope-io")
        common.extend(
            [
                "COPY .pyroscope/python/sitecustomize.py /opt/pyroscope/python/sitecustomize.py",
                "ENV PYTHONPATH=/opt/pyroscope/python:${PYTHONPATH}",
            ]
        )
    elif runtime in {"java", "spark", "flink"}:
        if mode == "github-test":
            common.append(
                f"ADD https://repo.maven.apache.org/maven2/io/pyroscope/agent/{JAVA_AGENT_VERSION}/agent-{JAVA_AGENT_VERSION}.jar /opt/pyroscope/pyroscope.jar"
            )
        else:
            common.append("COPY .pyroscope/java/pyroscope.jar /opt/pyroscope/pyroscope.jar")
        common.extend(
            [
                "ENV PYROSCOPE_FORMAT=jfr",
                "ENV PYROSCOPE_PROFILER_EVENT=itimer",
                "ENV PYROSCOPE_UPLOAD_INTERVAL=10s",
                "ENV PYROSCOPE_LOG_LEVEL=info",
                'ENV PYROSCOPE_JAVA_AGENT_OPTION="-javaagent:/opt/pyroscope/pyroscope.jar"',
            ]
        )
        if runtime == "java":
            common.append('ENV JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS} ${PYROSCOPE_JAVA_AGENT_OPTION}"')
        elif runtime == "spark":
            common.extend(
                [
                    "RUN set -eux; \\",
                    '    spark_conf="${SPARK_HOME:-/opt/spark}/conf/spark-defaults.conf"; \\',
                    '    mkdir -p "$(dirname "$spark_conf")"; \\',
                    '    touch "$spark_conf"; \\',
                    '    for key in spark.driver.defaultJavaOptions spark.executor.defaultJavaOptions; do \\',
                    '      if grep -Eq "^[[:space:]]*${key}[[:space:]].*-javaagent:/opt/pyroscope/pyroscope[.]jar" "$spark_conf"; then \\',
                    "        continue; \\",
                    '      elif grep -Eq "^[[:space:]]*${key}[[:space:]]" "$spark_conf"; then \\',
                    '        sed -i -E "s|^([[:space:]]*${key}[[:space:]])(.*)$|\\1${PYROSCOPE_JAVA_AGENT_OPTION} \\2|" "$spark_conf"; \\',
                    "      else \\",
                    '        printf "%s %s\\n" "$key" "$PYROSCOPE_JAVA_AGENT_OPTION" >> "$spark_conf"; \\',
                    "      fi; \\",
                    "    done",
                ]
            )
        elif runtime == "flink":
            common.extend(
                [
                    "RUN set -eux; \\",
                    '    flink_conf_dir="${FLINK_HOME:-/opt/flink}/conf"; \\',
                    '    mkdir -p "$flink_conf_dir"; \\',
                    '    flink_conf=""; \\',
                    '    for candidate in "$flink_conf_dir/config.yaml" "$flink_conf_dir/flink-conf.yaml"; do \\',
                    '      if [ -f "$candidate" ]; then flink_conf="$candidate"; break; fi; \\',
                    "    done; \\",
                    '    flink_conf="${flink_conf:-$flink_conf_dir/flink-conf.yaml}"; \\',
                    '    touch "$flink_conf"; \\',
                    '    key="env.java.default-opts.all"; \\',
                    '    if grep -Eq "^[[:space:]]*${key}:[[:space:]].*-javaagent:/opt/pyroscope/pyroscope[.]jar" "$flink_conf"; then \\',
                    "      true; \\",
                    '    elif grep -Eq "^[[:space:]]*${key}:[[:space:]]*\\"" "$flink_conf"; then \\',
                    '      sed -i -E "s|^([[:space:]]*${key}:[[:space:]]*)\\"(.*)\\"[[:space:]]*$|\\1\\"${PYROSCOPE_JAVA_AGENT_OPTION} \\2\\"|" "$flink_conf"; \\',
                    '    elif grep -Eq "^[[:space:]]*${key}:[[:space:]]*' + "'" + '" "$flink_conf"; then \\',
                    '      sed -i -E "s|^([[:space:]]*${key}:[[:space:]]*)' + "'" + '(.*)' + "'" + '[[:space:]]*$|\\1' + "'" + '${PYROSCOPE_JAVA_AGENT_OPTION} \\2' + "'" + '|" "$flink_conf"; \\',
                    '    elif grep -Eq "^[[:space:]]*${key}:" "$flink_conf"; then \\',
                    '      sed -i -E "s|^([[:space:]]*${key}:[[:space:]]*)(.*)$|\\1${PYROSCOPE_JAVA_AGENT_OPTION} \\2|" "$flink_conf"; \\',
                    "    else \\",
                    '      printf "%s: %s\\n" "$key" "$PYROSCOPE_JAVA_AGENT_OPTION" >> "$flink_conf"; \\',
                    "    fi",
                ]
            )
    elif runtime == "dotnet":
        if mode == "github-test":
            profiler_image = dotnet_profiler_image(dotnet_libc)
            common.extend(
                [
                    f"COPY --from={profiler_image} /Pyroscope.Profiler.Native.so /opt/pyroscope/dotnet/Pyroscope.Profiler.Native.so",
                    f"COPY --from={profiler_image} /Pyroscope.Linux.ApiWrapper.x64.so /opt/pyroscope/dotnet/Pyroscope.Linux.ApiWrapper.x64.so",
                ]
            )
        else:
            common.append(f"COPY {dotnet_airgap_source}/ /opt/pyroscope/dotnet/")
        common.extend(
            [
                "ENV PYROSCOPE_PROFILING_ENABLED=1",
                "ENV CORECLR_ENABLE_PROFILING=1",
                "ENV CORECLR_PROFILER={BD1A650D-AC5D-4896-B64F-D6FA25D6B26A}",
                "ENV CORECLR_PROFILER_PATH=/opt/pyroscope/dotnet/Pyroscope.Profiler.Native.so",
                "ENV LD_PRELOAD=/opt/pyroscope/dotnet/Pyroscope.Linux.ApiWrapper.x64.so",
                "ENV LD_LIBRARY_PATH=/opt/pyroscope/dotnet",
                "ENV DOTNET_EnableDiagnostics=1",
                "ENV DOTNET_EnableDiagnostics_IPC=0",
                "ENV DOTNET_EnableDiagnostics_Debugger=0",
                "ENV DOTNET_EnableDiagnostics_Profiler=1",
            ]
        )
    elif runtime == "go":
        if go_mode == "collector":
            common.extend(
                [
                    "# Go is conditional: this image uses an approved no-source collector bundle.",
                    "COPY .pyroscope/go/collector/ /opt/pyroscope/go/collector/",
                    "ENV PYROSCOPE_GO_CONDITIONAL=collector-bundle",
                ]
            )
        else:
            common.extend(
                [
                    "# Go is conditional: this image requires an existing reachable /debug/pprof/profile endpoint.",
                    "ENV PYROSCOPE_GO_CONDITIONAL=pprof-required",
                ]
            )
    else:
        common.append("# Unsupported runtime. Do not use this image until runtime is set explicitly.")
    common.append(END)
    return "\n" + "\n".join(common) + "\n"


def insert_runtime_block(text, block):
    stripped = text.rstrip() + "\n"
    lines = stripped.splitlines(keepends=True)
    last_from = 0
    for index, line in enumerate(lines):
        if re.match(r"^\s*FROM\b", line, re.I):
            last_from = index
    insert_at = len(lines)
    for index in range(len(lines) - 1, last_from - 1, -1):
        if re.match(r"^\s*USER\b", lines[index], re.I):
            insert_at = index
            break
    lines[insert_at:insert_at] = [block]
    return "".join(lines)


def patch_dockerfile(repo, dockerfile, runtime, service, image_tag, mode, go_mode="", dotnet_libc="glibc", dotnet_airgap_source=".pyroscope/dotnet"):
    text = strip_existing_block(read(dockerfile))
    write(dockerfile, insert_runtime_block(text, runtime_block(runtime, service, image_tag, mode, go_mode, dotnet_libc, dotnet_airgap_source)))


def copy_bootstrap(repo, runtime):
    if runtime != "python":
        return
    src = Path(__file__).resolve().parents[1] / "assets" / "python-bootstrap" / "sitecustomize.py"
    target = repo / ".pyroscope" / "python"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target / "sitecustomize.py")


def docker_build_command(service, tag_expr, dockerfile_relative, build_context_relative):
    args = ["docker", "build"]
    if dockerfile_relative != "Dockerfile":
        args.extend(["-f", dockerfile_relative])
    args.extend(["-t", f"{service}:{tag_expr}-pyroscope", build_context_relative])
    return " ".join(args)


def image_ref_tag_ends_pyroscope(image):
    last = image.rsplit("/", 1)[-1]
    if "@" in last or ":" not in last:
        return False
    tag = last.rsplit(":", 1)[1]
    return tag.endswith("-pyroscope")


def patch_ci(repo, service, dockerfile_relative, build_context_relative):
    workflow_dir = repo / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    workflow = workflow_dir / "pyroscope-image.yml"
    if workflow.exists():
        return
    build_command = docker_build_command(service, "${{ github.sha }}", dockerfile_relative, build_context_relative)
    workflow.write_text(
        f"""name: pyroscope-image

on:
  pull_request:
    paths:
      - {dockerfile_relative}
      - pyroscope-agent.yaml
      - .pyroscope/**
      - .github/workflows/pyroscope-image.yml
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build Pyroscope image
        run: {build_command}
""",
        encoding="utf-8",
    )


def patch_gitlab_ci(repo, service, dockerfile_relative, build_context_relative):
    workflow = repo / ".gitlab-ci.yml"
    marker = "# pyroscope-image-instrumenter:gitlab-ci"
    if workflow.exists() and marker in workflow.read_text(encoding="utf-8", errors="ignore"):
        return
    build_command = docker_build_command("${CI_REGISTRY_IMAGE}", "${CI_COMMIT_SHA}", dockerfile_relative, build_context_relative)
    block = f"""

{marker}
pyroscope-image:
  stage: build
  rules:
    - if: '$CI_COMMIT_BRANCH'
  script:
    - {build_command}
"""
    if workflow.exists():
        workflow.write_text(workflow.read_text(encoding="utf-8", errors="ignore").rstrip() + block, encoding="utf-8")
    else:
        workflow.write_text(block.lstrip(), encoding="utf-8")


def dotnet_airgap_asset_source(repo, dotnet_libc):
    if dotnet_libc == "musl":
        return ".pyroscope/dotnet/musl"
    glibc_dir = repo / ".pyroscope" / "dotnet" / "glibc"
    if glibc_dir.is_dir():
        return ".pyroscope/dotnet/glibc"
    return ".pyroscope/dotnet"


def require_airgap_assets(repo, runtime, dotnet_libc="glibc"):
    if runtime == "python":
        wheel_dir = repo / ".pyroscope" / "python" / "wheels"
        if not wheel_dir.is_dir() or not any(path.suffix == ".whl" for path in wheel_dir.iterdir() if path.is_file()):
            raise SystemExit("Airgap Python instrumentation requires .pyroscope/python/wheels/*.whl.")
    elif runtime in {"java", "spark", "flink"}:
        if not (repo / ".pyroscope" / "java" / "pyroscope.jar").is_file():
            raise SystemExit("Airgap JVM instrumentation requires .pyroscope/java/pyroscope.jar.")
    elif runtime == "dotnet":
        source = dotnet_airgap_asset_source(repo, dotnet_libc)
        required = [
            repo / source / "Pyroscope.Profiler.Native.so",
            repo / source / "Pyroscope.Linux.ApiWrapper.x64.so",
        ]
        missing = [str(path.relative_to(repo)) for path in required if not path.is_file()]
        if missing:
            raise SystemExit(f"Airgap .NET {dotnet_libc} instrumentation is missing profiler files: {missing}")
        return source
    return ""


def scan_secrets(repo):
    findings = []
    for path in [p for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts]:
        if path.stat().st_size > 1_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                findings.append(str(path.relative_to(repo)))
                break
    return sorted(set(findings))


def cmd_detect(args):
    repo = Path(args.repo).resolve()
    dockerfile = resolve_inside_repo(repo, args.dockerfile) if args.dockerfile else find_dockerfile(repo)
    if args.dockerfile and not dockerfile.is_file():
        raise SystemExit(f"Dockerfile does not exist: {dockerfile}")
    runtime = detect_runtime(repo, dockerfile)
    print(json.dumps({"repo": str(repo), "dockerfile": str(dockerfile) if dockerfile else None, "runtime": runtime, "service_name": service_name(repo)}, indent=2))


def cmd_instrument(args):
    repo = Path(args.repo).resolve()
    dockerfile = resolve_inside_repo(repo, args.dockerfile) if args.dockerfile else find_dockerfile(repo)
    if not dockerfile:
        raise SystemExit("No unambiguous Dockerfile found. Set --dockerfile explicitly.")
    if not dockerfile.is_file():
        raise SystemExit(f"Dockerfile does not exist: {dockerfile}")
    build_context = resolve_inside_repo(repo, args.build_context, ".")
    if not build_context.is_dir():
        raise SystemExit(f"Build context is not a directory: {build_context}")
    dockerfile_relative = relative_to_repo(repo, dockerfile)
    build_context_relative = relative_to_repo(repo, build_context)
    runtime = args.runtime or detect_runtime(repo, dockerfile)
    service = args.service_name or service_name(repo)
    requested_tag = args.image_tag or service
    image_tag = requested_tag if requested_tag.endswith("-pyroscope") else f"{requested_tag}-pyroscope"
    if not image_tag.endswith("-pyroscope"):
        raise SystemExit("Refusing to create a non-pyroscope tag.")
    if runtime == "unknown":
        raise SystemExit("Unknown runtime. Set --runtime explicitly.")
    dotnet_libc = "glibc"
    dotnet_airgap_source = ".pyroscope/dotnet"
    if runtime == "dotnet":
        dotnet_libc, blocker = dotnet_support_mode(repo, read(dockerfile))
        if blocker:
            raise SystemExit(blocker)
    go_mode = ""
    if runtime == "go":
        go_mode = go_support_mode(build_context, build_context)
        if not go_mode:
            if has_go_pprof_marker(build_context):
                raise SystemExit(
                    "Go pprof import found, but no reachable HTTP listener was detected. "
                    "Dockerfile-only instrumentation requires /debug/pprof/profile or .pyroscope/go/collector."
                )
            raise SystemExit(
                "Go Dockerfile-only instrumentation is conditional and this repo has no pprof endpoint or approved collector bundle."
            )
    if args.mode == "airgap":
        dotnet_airgap_source = require_airgap_assets(repo, runtime, dotnet_libc) or dotnet_airgap_source
    findings = scan_secrets(repo)
    if findings:
        raise SystemExit(f"Refusing to continue: possible secrets in {findings}")

    metadata = repo / "pyroscope-agent.yaml"
    if not metadata.exists():
        write(metadata, metadata_text(repo, runtime, service, dockerfile))
    copy_bootstrap(repo, runtime)
    patch_dockerfile(repo, dockerfile, runtime, service, image_tag, args.mode, go_mode, dotnet_libc, dotnet_airgap_source)
    if args.github_ci:
        patch_ci(repo, service, dockerfile_relative, build_context_relative)
    if args.gitlab_ci:
        patch_gitlab_ci(repo, service, dockerfile_relative, build_context_relative)
    print(json.dumps({"instrumented": True, "runtime": runtime, "service_name": service, "dockerfile": str(dockerfile), "image_tag": image_tag}, indent=2))


def pyroscope_ready(url):
    for path in ["/ready", "/-/ready"]:
        try:
            req = Request(url.rstrip("/") + path)
            with urlopen(req, timeout=5) as resp:
                if 200 <= resp.status < 500:
                    return True
        except URLError:
            pass
    return False


def pyroscope_service_observed(url, service, timeout_seconds=0):
    deadline = time.time() + timeout_seconds
    while True:
        end = int(time.time() * 1000)
        start = end - 60 * 60 * 1000
        body = json.dumps({"start": start, "end": end, "name": "service_name"}).encode("utf-8")
        try:
            req = Request(
                url.rstrip("/") + "/querier.v1.QuerierService/LabelValues",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw or "{}")
                if service in parsed.get("names", []):
                    return True
        except (URLError, json.JSONDecodeError):
            pass
        if time.time() >= deadline:
            return False
        time.sleep(5)


def verify_labels(runtime):
    if runtime == "dotnet":
        return "environment:local,profiling_mode:pyroscope-image"
    return "environment=local,profiling_mode=pyroscope-image"


def docker_verify_build_command(repo, dockerfile, build_context, image):
    return [
        "docker",
        "build",
        "-f",
        relative_to_repo(repo, dockerfile),
        "-t",
        image,
        relative_to_repo(repo, build_context),
    ]


def cmd_verify(args):
    repo = Path(args.repo).resolve()
    dockerfile = resolve_inside_repo(repo, args.dockerfile) if args.dockerfile else find_dockerfile(repo)
    if not dockerfile:
        raise SystemExit("No unambiguous Dockerfile found. Set --dockerfile explicitly.")
    if not dockerfile.is_file():
        raise SystemExit(f"Dockerfile does not exist: {dockerfile}")
    build_context = resolve_inside_repo(repo, args.build_context, ".")
    if not build_context.is_dir():
        raise SystemExit(f"Build context is not a directory: {build_context}")
    service = args.service_name or service_name(repo)
    runtime = args.runtime or detect_runtime(repo, dockerfile)
    image = args.image or f"{service}:local-pyroscope"
    if not image_ref_tag_ends_pyroscope(image):
        raise SystemExit("Refusing to build a non-pyroscope image tag.")
    build_cmd = docker_verify_build_command(repo, dockerfile, build_context, image)
    env = [
        "-e",
        f"PYROSCOPE_SERVER_ADDRESS={args.container_pyroscope_url}",
        "-e",
        f"PYROSCOPE_APPLICATION_NAME={service}",
        "-e",
        f"PYROSCOPE_LABELS={verify_labels(runtime)}",
    ]
    run_cmd = ["docker", "run", "-d", "--rm"] + env + [image]
    compose_file = find_compose_file(repo)
    base_result = {
        "image": image,
        "runtime": runtime,
        "dockerfile": relative_to_repo(repo, dockerfile),
        "build_context": relative_to_repo(repo, build_context),
        "build_command": build_cmd,
        "run_command": run_cmd,
        "compose_file": relative_to_repo(repo, compose_file) if compose_file else None,
    }
    if args.dry_run:
        print(json.dumps({**base_result, "dry_run": True}, indent=2))
        return
    if not pyroscope_ready(args.pyroscope_url):
        raise SystemExit(f"Pyroscope is not reachable at {args.pyroscope_url}")
    run(build_cmd, cwd=repo)
    proc = run(run_cmd, cwd=repo)
    container = proc.stdout.strip()
    time.sleep(args.seconds)
    inspect = run(["docker", "inspect", "-f", "{{.State.Running}}", container], check=False)
    logs = run(["docker", "logs", container], check=False)
    run(["docker", "rm", "-f", container], check=False)
    observed = pyroscope_service_observed(args.pyroscope_url, service, args.profile_timeout)
    result = {
        **base_result,
        "container_started": bool(container),
        "container_running_after_wait": "true" in inspect.stdout.lower(),
        "pyroscope_reachable_before_run": True,
        "container_pyroscope_url": args.container_pyroscope_url,
        "pyroscope_service_observed": observed,
        "log_excerpt": logs.stdout[-2000:],
    }
    if args.require_profile and not observed:
        print(json.dumps(result, indent=2))
        raise SystemExit(f"Pyroscope did not observe service_name={service}")
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(required=True)
    detect = sub.add_parser("detect")
    detect.add_argument("--repo", default=".")
    detect.add_argument("--dockerfile")
    detect.set_defaults(func=cmd_detect)
    inst = sub.add_parser("instrument")
    inst.add_argument("--repo", default=".")
    inst.add_argument("--dockerfile")
    inst.add_argument("--build-context", default=".")
    inst.add_argument("--runtime", choices=["dotnet", "java", "spark", "flink", "python", "go", "unknown"])
    inst.add_argument("--service-name")
    inst.add_argument("--image-tag")
    inst.add_argument("--pyroscope-url", default="http://host.docker.internal:4040")
    inst.add_argument("--mode", choices=["github-test", "airgap"], default="airgap")
    inst.add_argument("--github-ci", action="store_true")
    inst.add_argument("--gitlab-ci", action="store_true")
    inst.add_argument("--allow-go-conditional", action="store_true")
    inst.set_defaults(func=cmd_instrument)
    verify = sub.add_parser("verify")
    verify.add_argument("--repo", default=".")
    verify.add_argument("--dockerfile")
    verify.add_argument("--build-context", default=".")
    verify.add_argument("--service-name")
    verify.add_argument("--runtime", choices=["dotnet", "java", "spark", "flink", "python", "go", "unknown"])
    verify.add_argument("--image")
    verify.add_argument("--pyroscope-url", default="http://localhost:4040")
    verify.add_argument("--container-pyroscope-url", default="http://host.docker.internal:4040")
    verify.add_argument("--seconds", type=int, default=10)
    verify.add_argument("--profile-timeout", type=int, default=0)
    verify.add_argument("--require-profile", action="store_true")
    verify.add_argument("--dry-run", action="store_true")
    verify.set_defaults(func=cmd_verify)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
