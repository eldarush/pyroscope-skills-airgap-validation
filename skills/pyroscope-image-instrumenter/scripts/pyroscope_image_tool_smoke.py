#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path


IMAGE_TOOL = Path(__file__).resolve().parent / "pyroscope_image_tool.py"


def write(repo, relative, text):
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_tool(repo, *args, check=True):
    proc = subprocess.run(
        [sys.executable, str(IMAGE_TOOL), *args],
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"{args} failed\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return proc


def detect(repo):
    proc = run_tool(repo, "detect", "--repo", str(repo))
    return json.loads(proc.stdout)["runtime"]


def instrument(repo, runtime=None, *extra):
    args = ["instrument", "--repo", str(repo), "--mode", "github-test", *extra]
    if runtime:
        args.extend(["--runtime", runtime])
    return run_tool(repo, *args)


def instrument_and_expect_tag(repo, expected_tag, runtime=None, *extra):
    result = json.loads(instrument(repo, runtime, *extra).stdout)
    if result.get("image_tag") != expected_tag:
        raise AssertionError(f"expected image_tag {expected_tag!r}, got {result.get('image_tag')!r}")
    return result


def assert_block_before_user(repo, dockerfile="Dockerfile"):
    lines = (repo / dockerfile).read_text(encoding="utf-8").splitlines()
    block = next(i for i, line in enumerate(lines) if "pyroscope-image-instrumenter:start" in line)
    user = next(i for i, line in enumerate(lines) if line.strip().lower().startswith("user "))
    if block >= user:
        raise AssertionError("Pyroscope block must be inserted before final-stage USER")


def python_repo(repo):
    write(
        repo,
        "Dockerfile",
        """FROM python:3.12-slim AS build
RUN echo build
FROM python:3.12-slim
WORKDIR /app
COPY . .
USER app
CMD ["python", "app.py"]
""",
    )
    write(repo, "requirements.txt", "flask\n")
    write(repo, "app.py", "print('ok')\n")


def nested_python_repo(repo):
    write(
        repo,
        "services/api/Dockerfile",
        """FROM python:3.12-slim
WORKDIR /app
COPY services/api .
USER app
CMD ["python", "app.py"]
""",
    )
    write(repo, "services/api/requirements.txt", "flask\n")
    write(repo, "services/api/app.py", "print('ok')\n")


def compose_python_repo(repo):
    write(
        repo,
        "services/api/Dockerfile",
        """FROM python:3.12-slim
WORKDIR /app
COPY . .
USER app
CMD ["python", "app.py"]
""",
    )
    write(repo, "services/api/requirements.txt", "flask\n")
    write(repo, "services/api/app.py", "print('ok')\n")
    write(
        repo,
        "compose.yaml",
        """services:
  api:
    build:
      context: ./services/api
      dockerfile: Dockerfile
    image: api:dev
""",
    )


def ambiguous_nested_dockerfiles_repo(repo):
    write(repo, "services/api/Dockerfile", "FROM python:3.12-slim\nCMD [\"python\", \"app.py\"]\n")
    write(repo, "jobs/spark/Dockerfile", "FROM apache/spark:3.5.0\nCMD [\"/bin/true\"]\n")


def dotnet_repo(repo):
    write(
        repo,
        "Dockerfile",
        """FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
COPY . .
RUN dotnet publish -o /out
FROM mcr.microsoft.com/dotnet/aspnet:10.0
COPY --from=build /out /app
USER app
ENTRYPOINT ["dotnet", "/app/App.dll"]
""",
    )
    write(repo, "App.csproj", "<Project Sdk=\"Microsoft.NET.Sdk.Web\"></Project>\n")


def dotnet_alpine_arm64_repo(repo):
    write(
        repo,
        "Dockerfile",
        """FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
COPY . .
RUN dotnet publish -o /out
FROM --platform=linux/arm64 mcr.microsoft.com/dotnet/aspnet:10.0-alpine
COPY --from=build /out /app
ENTRYPOINT ["dotnet", "/app/App.dll"]
""",
    )
    write(repo, "App.csproj", "<Project Sdk=\"Microsoft.NET.Sdk.Web\"></Project>\n")


def dotnet_alpine_amd64_repo(repo):
    write(
        repo,
        "Dockerfile",
        """FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
COPY . .
RUN dotnet publish -o /out
FROM mcr.microsoft.com/dotnet/aspnet:10.0-alpine
COPY --from=build /out /app
USER app
ENTRYPOINT ["dotnet", "/app/App.dll"]
""",
    )
    write(repo, "App.csproj", "<Project Sdk=\"Microsoft.NET.Sdk.Web\"></Project>\n")


def dotnet_aliased_alpine_arm64_repo(repo):
    write(
        repo,
        "Dockerfile",
        """FROM --platform=linux/arm64 mcr.microsoft.com/dotnet/aspnet:10.0-alpine AS base
WORKDIR /app
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
COPY . .
RUN dotnet publish -o /out
FROM base AS final
COPY --from=build /out /app
USER app
ENTRYPOINT ["dotnet", "/app/App.dll"]
""",
    )
    write(repo, "App.csproj", "<Project Sdk=\"Microsoft.NET.Sdk.Web\"></Project>\n")


def dotnet_linux_armv7_repo(repo):
    write(
        repo,
        "Dockerfile",
        """FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
COPY . .
RUN dotnet publish -o /out
FROM --platform=linux/arm/v7 mcr.microsoft.com/dotnet/aspnet:10.0
COPY --from=build /out /app
ENTRYPOINT ["dotnet", "/app/App.dll"]
""",
    )
    write(repo, "App.csproj", "<Project Sdk=\"Microsoft.NET.Sdk.Web\"></Project>\n")


def non_dotnet_base_repo(repo):
    write(
        repo,
        "Dockerfile",
        """FROM ubuntu:24.04
CMD ["/bin/true"]
""",
    )


def java_repo(repo):
    write(
        repo,
        "Dockerfile",
        """FROM eclipse-temurin:21-jre
COPY target/app.jar /app.jar
USER app
ENTRYPOINT ["java", "-jar", "/app.jar"]
""",
    )
    write(repo, "pom.xml", "<project></project>\n")


def java_existing_options_repo(repo):
    write(
        repo,
        "Dockerfile",
        """FROM eclipse-temurin:21-jre
ENV JAVA_TOOL_OPTIONS="-XX:+UseG1GC -Dfeature.flag=true"
COPY target/app.jar /app.jar
USER app
ENTRYPOINT ["java", "-jar", "/app.jar"]
""",
    )
    write(repo, "pom.xml", "<project></project>\n")


def spark_repo(repo):
    write(
        repo,
        "Dockerfile",
        """FROM apache/spark:3.5.0
COPY target/job.jar /opt/job.jar
USER spark
CMD ["/opt/spark/bin/spark-submit", "/opt/job.jar"]
""",
    )


def flink_repo(repo):
    write(
        repo,
        "Dockerfile",
        """FROM flink:1.19
COPY target/job.jar /opt/flink/usrlib/job.jar
USER flink
CMD ["standalone-job", "--job-classname", "com.example.Job"]
""",
    )


def go_repo(repo, with_pprof, with_listener=False, with_collector=False):
    write(
        repo,
        "Dockerfile",
        """FROM golang:1.23 AS build
COPY . .
RUN go build -o /app
FROM gcr.io/distroless/base
COPY --from=build /app /app
USER nonroot
ENTRYPOINT ["/app"]
""",
    )
    write(repo, "go.mod", "module example.com/app\n")
    if with_pprof and with_listener:
        body = """package main

import (
    _ "net/http/pprof"
    "net/http"
)

func main() {
    _ = http.ListenAndServe(":6060", nil)
}
"""
    elif with_pprof:
        body = 'package main\nimport _ "net/http/pprof"\nfunc main(){}\n'
    else:
        body = "package main\nfunc main(){}\n"
    write(repo, "main.go", body)
    if with_collector:
        write(repo, ".pyroscope/go/collector/collector.yaml", "endpoint: /debug/pprof/profile\n")


def nested_go_collector_outside_context_repo(repo):
    write(
        repo,
        "services/api/Dockerfile",
        """FROM golang:1.23 AS build
COPY . .
RUN go build -o /app
FROM gcr.io/distroless/base
COPY --from=build /app /app
USER nonroot
ENTRYPOINT ["/app"]
""",
    )
    write(repo, "services/api/go.mod", "module example.com/api\n")
    write(repo, "services/api/main.go", "package main\nfunc main(){}\n")
    write(repo, ".pyroscope/go/collector/collector.yaml", "endpoint: /debug/pprof/profile\n")


def nested_go_collector_inside_context_repo(repo):
    nested_go_collector_outside_context_repo(repo)
    write(repo, "services/api/.pyroscope/go/collector/collector.yaml", "endpoint: /debug/pprof/profile\n")


def python_secret_repo(repo):
    python_repo(repo)
    secret_name = "API_" + "TOKEN"
    write(repo, ".env", f"{secret_name}=do-not-write-after-this\n")


def assert_contains(path, needle):
    text = path.read_text(encoding="utf-8")
    if needle not in text:
        raise AssertionError(f"{needle!r} not found in {path}")


def assert_not_contains(path, needle):
    text = path.read_text(encoding="utf-8")
    if needle in text:
        raise AssertionError(f"{needle!r} unexpectedly found in {path}")


def assert_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_truthy(value, label):
    if not value:
        raise AssertionError(label)


def expect_failure(repo, *args, contains):
    proc = run_tool(repo, *args, check=False)
    if proc.returncode == 0:
        raise AssertionError(f"{args} unexpectedly succeeded")
    if contains.lower() not in (proc.stdout + proc.stderr).lower():
        raise AssertionError(f"{args} failed without expected text {contains!r}")


def assert_not_mutated(repo):
    dockerfiles = [p for p in repo.rglob("Dockerfile*") if p.is_file()]
    for dockerfile in dockerfiles:
        assert_not_contains(dockerfile, "pyroscope-image-instrumenter:start")
    if (repo / "pyroscope-agent.yaml").exists():
        raise AssertionError("refusal wrote pyroscope-agent.yaml")


def assert_no_generated_outputs(repo):
    assert_not_mutated(repo)
    generated = [
        repo / ".github" / "workflows" / "pyroscope-image.yml",
        repo / ".gitlab-ci.yml",
        repo / ".pyroscope" / "python" / "sitecustomize.py",
    ]
    for path in generated:
        if path.exists():
            raise AssertionError(f"refusal wrote generated file: {path}")


def detect_with_dockerfile(repo, dockerfile):
    proc = run_tool(repo, "detect", "--repo", str(repo), "--dockerfile", str(repo / dockerfile))
    return json.loads(proc.stdout)["runtime"]


def run_case(name, builder, expected_runtime, validate):
    with tempfile.TemporaryDirectory(prefix=f"pyroscope-{name}-") as tmp:
        repo = Path(tmp)
        builder(repo)
        actual = detect(repo)
        if actual != expected_runtime:
            raise AssertionError(f"{name}: expected runtime {expected_runtime}, got {actual}")
        validate(repo)
    return {"case": name, "ok": True}


def main():
    results = []
    results.append(
        run_case(
            "python-multistage-user-gitlab-ci",
            python_repo,
            "python",
            lambda repo: (
                instrument_and_expect_tag(repo, "checkout-pyroscope", None, "--gitlab-ci", "--image-tag", "checkout-pyroscope"),
                assert_block_before_user(repo),
                assert_contains(repo / "Dockerfile", "RUN pip install --no-cache-dir pyroscope-io"),
                assert_contains(repo / ".gitlab-ci.yml", "${CI_COMMIT_SHA}-pyroscope"),
                assert_not_contains(repo / ".gitlab-ci.yml", "pyroscope-pyroscope"),
            ),
        )
    )
    results.append(
        run_case(
            "python-github-ci-single-pyroscope-suffix",
            python_repo,
            "python",
            lambda repo: (
                instrument_and_expect_tag(repo, "checkout-pyroscope", None, "--github-ci", "--image-tag", "checkout-pyroscope"),
                assert_block_before_user(repo),
                assert_contains(repo / ".github" / "workflows" / "pyroscope-image.yml", "${{ github.sha }}-pyroscope"),
                assert_not_contains(repo / ".github" / "workflows" / "pyroscope-image.yml", "pyroscope-pyroscope"),
            ),
        )
    )
    results.append(
        run_case(
            "python-nested-dockerfile-explicit-ci-paths",
            nested_python_repo,
            "python",
            lambda repo: (
                instrument_and_expect_tag(
                    repo,
                    "api-pyroscope",
                    None,
                    "--dockerfile",
                    str(repo / "services/api/Dockerfile"),
                    "--github-ci",
                    "--gitlab-ci",
                    "--image-tag",
                    "api-pyroscope",
                ),
                assert_block_before_user(repo, "services/api/Dockerfile"),
                assert_contains(repo / "pyroscope-agent.yaml", "dockerfile: services/api/Dockerfile"),
                assert_contains(repo / ".github" / "workflows" / "pyroscope-image.yml", "docker build -f services/api/Dockerfile"),
                assert_contains(repo / ".gitlab-ci.yml", "docker build -f services/api/Dockerfile"),
                assert_not_contains(repo / ".github" / "workflows" / "pyroscope-image.yml", "pyroscope-pyroscope"),
                assert_not_contains(repo / ".gitlab-ci.yml", "pyroscope-pyroscope"),
            ),
        )
    )
    results.append(
        run_case(
            "python-compose-explicit-build-context-verify-plan",
            compose_python_repo,
            "python",
            lambda repo: (
                instrument_and_expect_tag(
                    repo,
                    "api-pyroscope",
                    None,
                    "--dockerfile",
                    str(repo / "services/api/Dockerfile"),
                    "--build-context",
                    str(repo / "services/api"),
                    "--image-tag",
                    "api-pyroscope",
                ),
                assert_block_before_user(repo, "services/api/Dockerfile"),
                (lambda plan: (
                    assert_truthy(plan.get("dry_run"), "verify dry-run was not reported"),
                    assert_equal(plan.get("dockerfile"), "services/api/Dockerfile", "verify dockerfile"),
                    assert_equal(plan.get("build_context"), "services/api", "verify build context"),
                    assert_equal(plan.get("compose_file"), "compose.yaml", "compose file detection"),
                    assert_equal(
                        plan.get("build_command"),
                        [
                            "docker",
                            "build",
                            "-f",
                            "services/api/Dockerfile",
                            "-t",
                            "api:local-pyroscope",
                            "services/api",
                        ],
                        "verify build command",
                    ),
                ))(
                    json.loads(
                        run_tool(
                            repo,
                            "verify",
                            "--repo",
                            str(repo),
                            "--dockerfile",
                            str(repo / "services/api/Dockerfile"),
                            "--build-context",
                            str(repo / "services/api"),
                            "--image",
                            "api:local-pyroscope",
                            "--dry-run",
                        ).stdout
                    )
                ),
                assert_contains(repo / "compose.yaml", "image: api:dev"),
            ),
        )
    )
    results.append(
        run_case(
            "dotnet-multistage-user",
            dotnet_repo,
            "dotnet",
            lambda repo: (
                instrument(repo),
                assert_block_before_user(repo),
                assert_contains(repo / "Dockerfile", "CORECLR_ENABLE_PROFILING=1"),
            ),
        )
    )
    results.append(
        run_case(
            "dotnet-alpine-arm64-refuses",
            dotnet_alpine_arm64_repo,
            "dotnet",
            lambda repo: (
                expect_failure(
                    repo,
                    "instrument",
                    "--repo",
                    str(repo),
                    "--mode",
                    "github-test",
                    contains="unsupported .NET Pyroscope profiler image",
                ),
                assert_not_mutated(repo),
            ),
        )
    )
    results.append(
        run_case(
            "dotnet-alpine-amd64-uses-musl-profiler",
            dotnet_alpine_amd64_repo,
            "dotnet",
            lambda repo: (
                instrument(repo),
                assert_block_before_user(repo),
                assert_contains(repo / "Dockerfile", "pyroscope/pyroscope-dotnet:0.14.2-musl"),
                assert_contains(repo / "Dockerfile", "CORECLR_ENABLE_PROFILING=1"),
            ),
        )
    )
    results.append(
        run_case(
            "dotnet-alpine-airgap-requires-musl-assets",
            dotnet_alpine_amd64_repo,
            "dotnet",
            lambda repo: (
                expect_failure(
                    repo,
                    "instrument",
                    "--repo",
                    str(repo),
                    "--mode",
                    "airgap",
                    contains="Airgap .NET musl instrumentation is missing profiler files",
                ),
                assert_not_mutated(repo),
            ),
        )
    )
    results.append(
        run_case(
            "dotnet-alpine-airgap-uses-musl-assets",
            dotnet_alpine_amd64_repo,
            "dotnet",
            lambda repo: (
                write(repo, ".pyroscope/dotnet/musl/Pyroscope.Profiler.Native.so", "native\n"),
                write(repo, ".pyroscope/dotnet/musl/Pyroscope.Linux.ApiWrapper.x64.so", "wrapper\n"),
                run_tool(repo, "instrument", "--repo", str(repo), "--mode", "airgap"),
                assert_block_before_user(repo),
                assert_contains(repo / "Dockerfile", "COPY .pyroscope/dotnet/musl/ /opt/pyroscope/dotnet/"),
            ),
        )
    )
    results.append(
        run_case(
            "dotnet-aliased-alpine-arm64-refuses-without-mutation",
            dotnet_aliased_alpine_arm64_repo,
            "dotnet",
            lambda repo: (
                expect_failure(
                    repo,
                    "instrument",
                    "--repo",
                    str(repo),
                    "--mode",
                    "github-test",
                    contains="unsupported .NET Pyroscope profiler image",
                ),
                assert_not_mutated(repo),
            ),
        )
    )
    results.append(
        run_case(
            "dotnet-linux-armv7-refuses",
            dotnet_linux_armv7_repo,
            "dotnet",
            lambda repo: (
                expect_failure(
                    repo,
                    "instrument",
                    "--repo",
                    str(repo),
                    "--mode",
                    "github-test",
                    contains="Linux amd64 only",
                ),
                assert_not_mutated(repo),
            ),
        )
    )
    results.append(
        run_case(
            "explicit-dotnet-non-dotnet-base-refuses",
            non_dotnet_base_repo,
            "unknown",
            lambda repo: (
                expect_failure(
                    repo,
                    "instrument",
                    "--repo",
                    str(repo),
                    "--runtime",
                    "dotnet",
                    "--mode",
                    "github-test",
                    contains="no recognized .NET runtime base",
                ),
                assert_not_mutated(repo),
            ),
        )
    )
    results.append(
        run_case(
            "java-user",
            java_repo,
            "java",
            lambda repo: (
                instrument(repo),
                assert_block_before_user(repo),
                assert_contains(repo / "Dockerfile", "JAVA_TOOL_OPTIONS"),
                assert_contains(repo / "Dockerfile", "PYROSCOPE_JAVA_AGENT_OPTION"),
            ),
        )
    )
    results.append(
        run_case(
            "java-preserves-existing-tool-options",
            java_existing_options_repo,
            "java",
            lambda repo: (
                instrument(repo),
                assert_block_before_user(repo),
                assert_contains(repo / "Dockerfile", "-XX:+UseG1GC -Dfeature.flag=true"),
                assert_contains(repo / "Dockerfile", 'JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS} ${PYROSCOPE_JAVA_AGENT_OPTION}"'),
            ),
        )
    )
    results.append(
        run_case(
            "spark-image",
            spark_repo,
            "spark",
            lambda repo: (
                instrument(repo),
                assert_block_before_user(repo),
                assert_contains(repo / "Dockerfile", "spark.driver.defaultJavaOptions"),
                assert_contains(repo / "Dockerfile", "spark.executor.defaultJavaOptions"),
                assert_not_contains(repo / "Dockerfile", "JAVA_TOOL_OPTIONS"),
            ),
        )
    )
    results.append(
        run_case(
            "flink-image",
            flink_repo,
            "flink",
            lambda repo: (
                instrument(repo),
                assert_block_before_user(repo),
                assert_contains(repo / "Dockerfile", "env.java.default-opts.all"),
                assert_not_contains(repo / "Dockerfile", "JAVA_TOOL_OPTIONS"),
            ),
        )
    )
    results.append(
        run_case(
            "go-without-pprof-refuses",
            lambda repo: go_repo(repo, False),
            "go",
            lambda repo: (
                expect_failure(
                    repo,
                    "instrument",
                    "--repo",
                    str(repo),
                    "--mode",
                    "github-test",
                    contains="no pprof endpoint",
                ),
                assert_not_mutated(repo),
            ),
        )
    )
    results.append(
        run_case(
            "go-pprof-import-without-listener-refuses",
            lambda repo: go_repo(repo, True, False),
            "go",
            lambda repo: (
                expect_failure(
                    repo,
                    "instrument",
                    "--repo",
                    str(repo),
                    "--mode",
                    "github-test",
                    "--allow-go-conditional",
                    contains="no reachable HTTP listener",
                ),
                assert_not_mutated(repo),
            ),
        )
    )
    results.append(
        run_case(
            "go-existing-pprof-conditional",
            lambda repo: go_repo(repo, True, True),
            "go",
            lambda repo: (
                instrument(repo, None, "--allow-go-conditional"),
                assert_block_before_user(repo),
                assert_contains(repo / "Dockerfile", "PYROSCOPE_GO_CONDITIONAL=pprof-required"),
            ),
        )
    )
    results.append(
        run_case(
            "go-approved-no-source-collector",
            lambda repo: go_repo(repo, False, False, True),
            "go",
            lambda repo: (
                instrument(repo, None, "--allow-go-conditional"),
                assert_block_before_user(repo),
                assert_contains(repo / "Dockerfile", "COPY .pyroscope/go/collector/ /opt/pyroscope/go/collector/"),
                assert_contains(repo / "Dockerfile", "PYROSCOPE_GO_CONDITIONAL=collector-bundle"),
            ),
        )
    )
    results.append(
        run_case(
            "go-nested-collector-outside-build-context-refuses",
            nested_go_collector_outside_context_repo,
            "go",
            lambda repo: (
                expect_failure(
                    repo,
                    "instrument",
                    "--repo",
                    str(repo),
                    "--dockerfile",
                    str(repo / "services/api/Dockerfile"),
                    "--build-context",
                    str(repo / "services/api"),
                    "--runtime",
                    "go",
                    "--mode",
                    "github-test",
                    contains="no pprof endpoint or approved collector bundle",
                ),
                assert_not_mutated(repo),
            ),
        )
    )
    results.append(
        run_case(
            "go-nested-collector-inside-build-context",
            nested_go_collector_inside_context_repo,
            "go",
            lambda repo: (
                instrument(
                    repo,
                    "go",
                    "--dockerfile",
                    str(repo / "services/api/Dockerfile"),
                    "--build-context",
                    str(repo / "services/api"),
                ),
                assert_block_before_user(repo, "services/api/Dockerfile"),
                assert_contains(repo / "services/api/Dockerfile", "PYROSCOPE_GO_CONDITIONAL=collector-bundle"),
            ),
        )
    )
    results.append(
        run_case(
            "python-secret-refuses-before-mutation",
            python_secret_repo,
            "python",
            lambda repo: (
                expect_failure(
                    repo,
                    "instrument",
                    "--repo",
                    str(repo),
                    "--mode",
                    "github-test",
                    "--github-ci",
                    contains="possible secrets",
                ),
                assert_no_generated_outputs(repo),
            ),
        )
    )
    results.append(
        run_case(
            "python-airgap-missing-wheels-refuses",
            python_repo,
            "python",
            lambda repo: (
                expect_failure(
                    repo,
                    "instrument",
                    "--repo",
                    str(repo),
                    "--mode",
                    "airgap",
                    contains="requires .pyroscope/python/wheels",
                ),
                assert_not_mutated(repo),
            ),
        )
    )
    with tempfile.TemporaryDirectory(prefix="pyroscope-monorepo-") as tmp:
        repo = Path(tmp)
        python_repo(repo)
        write(repo, "jobs/spark/TransformJob.scala", "object TransformJob {}\n")
        write(repo, "services/go/go.mod", "module example.com/sidecar\n")
        actual = detect(repo)
        if actual != "python":
            raise AssertionError(f"monorepo Dockerfile precedence failed: got {actual}")
        results.append({"case": "monorepo-dockerfile-precedence", "ok": True})
    with tempfile.TemporaryDirectory(prefix="pyroscope-ambiguous-") as tmp:
        repo = Path(tmp)
        write(repo, "Dockerfile", "FROM alpine:3.20\nCMD [\"/bin/true\"]\n")
        write(repo, "requirements.txt", "flask\n")
        write(repo, "go.mod", "module example.com/app\n")
        actual = detect(repo)
        if actual != "unknown":
            raise AssertionError(f"ambiguous manifest repo should be unknown, got {actual}")
        results.append({"case": "ambiguous-manifests-require-runtime", "ok": True})
    with tempfile.TemporaryDirectory(prefix="pyroscope-multi-dockerfile-") as tmp:
        repo = Path(tmp)
        ambiguous_nested_dockerfiles_repo(repo)
        expect_failure(
            repo,
            "instrument",
            "--repo",
            str(repo),
            "--mode",
            "github-test",
            contains="No unambiguous Dockerfile",
        )
        if detect_with_dockerfile(repo, "services/api/Dockerfile") != "python":
            raise AssertionError("explicit nested Dockerfile detect failed")
        expect_failure(
            repo,
            "detect",
            "--repo",
            str(repo),
            "--dockerfile",
            str(repo / "missing/Dockerfile"),
            contains="Dockerfile does not exist",
        )
        results.append({"case": "ambiguous-nested-dockerfiles-require-explicit-path", "ok": True})
    with tempfile.TemporaryDirectory(prefix="pyroscope-verify-tag-") as tmp:
        repo = Path(tmp)
        python_repo(repo)
        expect_failure(
            repo,
            "verify",
            "--repo",
            str(repo),
            "--image",
            "example:local-pyroscope-prod",
            "--pyroscope-url",
            "http://127.0.0.1:9",
            contains="non-pyroscope image tag",
        )
        results.append({"case": "verify-rejects-non-suffix-pyroscope-tag", "ok": True})
    print(json.dumps({"ok": True, "cases": results}, indent=2))


if __name__ == "__main__":
    main()
