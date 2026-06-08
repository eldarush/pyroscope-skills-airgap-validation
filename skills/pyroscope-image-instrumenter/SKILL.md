---
name: pyroscope-image-instrumenter
description: Create safe Pyroscope profiling images from existing repositories. Use when asked to add Pyroscope to a Dockerized app, patch Dockerfile/CI to publish only *-pyroscope tags, create a feature branch and PR/MR, verify local Docker runtime, or debug why a Pyroscope-instrumented image does not build, run, or reach Pyroscope. Supports airgapped GitLab/Artifactory workflows and GitHub test emulation.
---

# Pyroscope Image Instrumenter

## Contract

Create a profiling image without changing production behavior:

- Work only on a feature branch or fork.
- Edit only Dockerfile/CI, `pyroscope-agent.yaml`, and bundled image-level bootstrap files.
- Never deploy, merge, or publish production-like tags.
- Only publish or propose image tags ending in `-pyroscope`.
- Never write secrets into repo files, Dockerfiles, CI files, logs, or PR/MR text.

## Workflow

1. Inspect the repo with `scripts/pyroscope_image_tool.py detect --repo <repo>`.
   If the repo has multiple Dockerfiles and no root `Dockerfile`, rerun with `--dockerfile <path>` or refuse as ambiguous.
2. Prefer existing `pyroscope-agent.yaml`; otherwise generate one during instrumentation.
3. Patch the Dockerfile and optional CI with:
   `scripts/pyroscope_image_tool.py instrument --repo <repo> --pyroscope-url <url> [--service-name <name>] [--dockerfile <path>] [--build-context <path>] [--mode github-test|airgap]`.
   Use `--github-ci` or `--gitlab-ci` only for profiling-image build jobs; generated tags must end in `-pyroscope`. When `--dockerfile` is supplied, generated CI must build that same Dockerfile with `docker build -f <path>`.
4. Review the generated report and refuse unsupported runtimes or ambiguous Dockerfiles.
5. Run local verification:
   `scripts/pyroscope_image_tool.py verify --repo <repo> --pyroscope-url <url> --seconds 30`.
   For monorepos or compose-based local development, pass the same `--dockerfile <path>` and `--build-context <path>` used during instrumentation. The verifier builds and runs the profiling image directly; it detects compose files for reporting but does not patch or deploy them.
   Add `--require-profile --profile-timeout 60` when the service can run long enough to prove Pyroscope ingestion.
6. Audit the branch and PR/MR plan with:
   `scripts/pyroscope_git_workflow.py audit --repo <repo> --provider github|gitlab --service-name <name> --base-branch <base> --body-output <path>`.
   Create a PR/MR only after this reports `ok: true`, verifying the diff contains no likely secrets, no production tags, only allowed image-instrumentation files, and a feature branch whose name contains `pyroscope`.

## Runtime Policy

Supported v1:

- `.NET 6+` Linux amd64 by Dockerfile/env/native-profiler bundle, with glibc or musl assets selected from the final runtime image.
- Java/JVM apps by Dockerfile/env/javaagent bundle.
- Spark/Flink JVM images as Java subcases.

Best effort:

- Python via Dockerfile-installed package and injected `sitecustomize.py` bootstrap. Airgapped mode must use an internal wheelhouse.

Conditional:

- Go only when the app already exposes pprof or a no-source-change collector path is explicitly present. Do not add Go source instrumentation.
- The helper refuses Go unless it detects both a pprof marker and an HTTP listener in the selected build context, or an approved `.pyroscope/go/collector` bundle inside that same Docker build context.

Refuse:

- Source-code instrumentation for image setup.
- Production deployment or production tag changes.
- Cluster-wide Alloy/eBPF/daemonset changes.
- Go binaries with no pprof/no-source collection path.
- .NET images that are not recognized Linux amd64 glibc/musl-compatible runtime images.
- Missing runtime artifacts in airgapped mode.

## Metadata

Use `pyroscope-agent.yaml` as the deterministic input for weak models. If absent, create a draft:

```yaml
schema_version: 1
service_name: checkout
runtime: python
dockerfile: Dockerfile
image:
  repository: checkout
  pyroscope_tag_suffix: pyroscope
pyroscope:
  server_address_env: PYROSCOPE_SERVER_ADDRESS
  application_name_env: PYROSCOPE_APPLICATION_NAME
  labels_env: PYROSCOPE_LABELS
local_run:
  command: ""
  required_env: []
source:
  roots: ["src", "app"]
  exclude: ["test", "tests", "generated", "vendor", "node_modules", "bin", "obj", "target"]
profile_mapping:
  expected_labels: ["service_name", "runtime", "repo", "branch", "git_sha", "image_tag", "environment", "profiling_mode"]
```

## Verification Standard

Success for v1 means:

- branch exists,
- only allowed files changed,
- Docker image builds,
- Dockerfiles that use a final-stage `USER` keep that runtime user after the Pyroscope block,
- image tag ends with `-pyroscope`,
- container starts locally when the repo has a valid local run path,
- Pyroscope URL is reachable from inside the container,
- `--require-profile` proves Pyroscope observed the service when the app can run locally without missing dependencies,
- `service_name` is supplied by the Pyroscope application name and must not be duplicated in tag labels,
- generated report says what could and could not be proven.

Run `scripts/pyroscope_image_tool_smoke.py` after helper edits. It generates synthetic Python, .NET, Java, Spark, Flink, Go, airgap-missing-asset, CI tag, compose-local-run, monorepo, and ambiguous-manifest repos without building Docker images.

Run `scripts/pyroscope_git_workflow_smoke.py` after PR/MR helper edits. It validates safe GitHub PR command planning, required PR labels, protected-branch refusal, secret refusal, and production-tag refusal without contacting GitHub.

Run `scripts/pyroscope_image_docker_smoke.py` when Docker is available. It builds minimal airgapped Docker images for Python, .NET glibc, .NET musl, Java, Spark, Flink, and conditional Go using local dummy artifacts, and runs all but the .NET dummy-native-profiler images. This validates generated Dockerfile syntax and tag safety without reaching external package registries.

If the app needs missing DB/Kafka/secrets/config, mark image creation successful but runtime verification blocked. Do not invent app dependencies.

## PR/MR Rules

GitHub test mode may use `gh`. Airgapped mode must use `git` plus GitLab REST API token from env.

PR/MR title:

```text
Add Pyroscope profiling image for <service_name>
```

Required labels or text:

```text
pyroscope
profiling
devops-review
```

Never auto-merge.

## References

- Runtime rules: `references/runtime-matrix.md`
- Airgapped bundle contract: `references/airgap-bundle.md`
- Helper: `scripts/pyroscope_image_tool.py`
