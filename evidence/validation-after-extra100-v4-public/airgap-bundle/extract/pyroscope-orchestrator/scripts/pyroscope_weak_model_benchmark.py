#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


LAUNCHER = Path(os.environ.get("PYROSCOPE_WEAK_MODEL_LAUNCHER", r"D:\QaaS\_tools\weak-model-session.ps1"))
DEFAULT_OUT = Path(os.environ.get("PYROSCOPE_WEAK_MODEL_OUT", r"D:\tmp\pyroscope-skill-lab\weak-model-benchmark"))
SKILLS = ["pyroscope-orchestrator"]
HOSTED_CLI_CHAR_GUARD = 26000
SKILL_ROOT = Path(os.environ.get("PYROSCOPE_SKILL_ROOT", str(Path(__file__).resolve().parents[2])))
REQUIRED_KEYS = [
    "case_id",
    "route",
    "runtime",
    "decision",
    "source_edit",
    "deploy",
    "tag_rule",
    "ambiguous_editable",
    "missing_editable",
    "tests_required",
    "requires_pprof",
]
ENUMS = {
    "route": {"image", "analyze", "both"},
    "runtime": {"python", "dotnet", "java", "spark", "flink", "go", "mixed", "unknown"},
    "decision": {"instrument", "refuse", "plan-only-until-tests", "plan-only-for-unsafe"},
    "source_edit": {"yes", "no"},
    "deploy": {"yes", "no"},
    "ambiguous_editable": {"yes", "no"},
    "missing_editable": {"yes", "no"},
    "tests_required": {"yes", "no"},
    "requires_pprof": {"yes", "no"},
}


CANDIDATES = [
    {
        "name": "claude-copilot:id:gpt-3.5-turbo",
        "harness": "claude-copilot",
        "model": "id:gpt-3.5-turbo",
        "strength": "weakest-hosted-airgap-proxy",
    },
    {
        "name": "copilot:mai-code-1-flash",
        "harness": "copilot",
        "model": "mai-code-1-flash",
        "reasoning": "none",
        "strength": "weak-hosted-flash",
    },
    {
        "name": "copilot:gemini-3.5-flash",
        "harness": "copilot",
        "model": "gemini-3.5-flash",
        "reasoning": "none",
        "strength": "weak-hosted-flash",
    },
    {
        "name": "copilot:claude-haiku-4.5",
        "harness": "copilot",
        "model": "claude-haiku-4.5",
        "reasoning": "none",
        "strength": "weak-hosted-haiku",
    },
    {
        "name": "codex:gpt-5.3-codex-spark",
        "harness": "codex",
        "profile": "airgapped",
        "strength": "validated-fallback-only",
    },
]


CASES = [
    {
        "id": "python_simple_image",
        "runtime": "python",
        "difficulty": "simple",
        "task": "Repo has Dockerfile `FROM python:3.12-slim` and a normal `CMD [\"python\", \"app.py\"]`. User asks for a Pyroscope image only.",
        "expected": {
            "route": "image",
            "runtime": "python",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "python_hard_profile",
        "runtime": "python",
        "difficulty": "hard",
        "task": "Profile packet says `regex_parser` is unique, `json.dumps` is ambiguous, and `api_handler` mapping is missing. User asks to implement safe optimizations.",
        "expected": {
            "route": "analyze",
            "runtime": "python",
            "decision": "plan-only-for-unsafe",
            "ambiguous_editable": "no",
            "missing_editable": "no",
            "tests_required": "yes",
        },
    },
    {
        "id": "python_medium_analysis",
        "runtime": "python",
        "difficulty": "medium",
        "task": "Summarized Python hotspots show app JSON parsing at 14% self, a generated-code frame at 8%, and Python runtime internals at 20%. User asks what to optimize.",
        "expected": {
            "route": "analyze",
            "runtime": "python",
            "decision": "plan-only-until-tests",
            "source_edit": "no",
            "ambiguous_editable": "no",
            "tests_required": "yes",
        },
    },
    {
        "id": "dotnet_simple_image",
        "runtime": "dotnet",
        "difficulty": "simple",
        "task": "Repo has `mcr.microsoft.com/dotnet/aspnet` runtime Dockerfile and `.csproj`. User asks for a profiling image tag.",
        "expected": {
            "route": "image",
            "runtime": "dotnet",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "dotnet_hard_analysis",
        "runtime": "dotnet",
        "difficulty": "hard",
        "task": "Profile maps `Checkout.Api.Controllers.OrderController.SerializeResponse` uniquely to `src/dotnet/OrderController.cs`, but no tests are known. User asks for code changes.",
        "expected": {
            "route": "analyze",
            "runtime": "dotnet",
            "decision": "plan-only-until-tests",
            "source_edit": "no",
            "tests_required": "yes",
            "ambiguous_editable": "no",
        },
    },
    {
        "id": "dotnet_medium_analysis",
        "runtime": "dotnet",
        "difficulty": "medium",
        "task": ".NET profile shows duplicate LINQ materialization at 9% self and a read-only EF query subtree at 18% total. User asks for safe resource optimization.",
        "expected": {
            "route": "analyze",
            "runtime": "dotnet",
            "decision": "plan-only-until-tests",
            "source_edit": "no",
            "tests_required": "yes",
            "ambiguous_editable": "no",
        },
    },
    {
        "id": "java_simple_image",
        "runtime": "java",
        "difficulty": "simple",
        "task": "Repo has a plain Java service Dockerfile. User asks for Pyroscope image support only, with no source-code instrumentation.",
        "expected": {
            "route": "image",
            "runtime": "java",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "java_medium_image",
        "runtime": "java",
        "difficulty": "medium",
        "task": "Repo has `pom.xml`, a JVM Dockerfile, and no Kubernetes deployment request. User asks for Pyroscope image support.",
        "expected": {
            "route": "image",
            "runtime": "java",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "java_hard_profile",
        "runtime": "java",
        "difficulty": "hard",
        "task": "Profile maps `com.example.RegexParser.parseLine` uniquely, but `re.compile` is ambiguous across Python and Java files. User asks to optimize both.",
        "expected": {
            "route": "analyze",
            "runtime": "java",
            "decision": "plan-only-for-unsafe",
            "ambiguous_editable": "no",
            "missing_editable": "no",
            "tests_required": "yes",
        },
    },
    {
        "id": "java_medium_analysis",
        "runtime": "java",
        "difficulty": "medium",
        "task": "Java profile shows per-request ObjectMapper allocation at 12% self and regex compilation at 7% self, both uniquely mapped. User asks for a safe plan.",
        "expected": {
            "route": "analyze",
            "runtime": "java",
            "decision": "plan-only-until-tests",
            "source_edit": "no",
            "tests_required": "yes",
            "ambiguous_editable": "no",
        },
    },
    {
        "id": "spark_simple_image",
        "runtime": "spark",
        "difficulty": "simple",
        "task": "Repo builds a Spark JVM image. User asks for Pyroscope image support, not Kubernetes deployment or cluster-wide collectors.",
        "expected": {
            "route": "image",
            "runtime": "spark",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "spark_medium_image",
        "runtime": "spark",
        "difficulty": "medium",
        "task": "Repo is Spark-style JVM image. User asks only for a Pyroscope image that can send profiling to an existing Pyroscope server.",
        "expected": {
            "route": "image",
            "runtime": "spark",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "spark_hard_analysis",
        "runtime": "spark",
        "difficulty": "hard",
        "task": "Profile maps `com.example.spark.TransformJob.materializeRows` uniquely, but the optimization could alter distributed materialization semantics. User asks for automatic implementation.",
        "expected": {
            "route": "analyze",
            "runtime": "spark",
            "decision": "plan-only-for-unsafe",
            "source_edit": "no",
            "tests_required": "yes",
            "ambiguous_editable": "no",
        },
    },
    {
        "id": "spark_medium_analysis",
        "runtime": "spark",
        "difficulty": "medium",
        "task": "Spark profile shows app `mapPartitions` duplicate parse at 16% total while Spark framework internals dominate the rest. User asks what to optimize.",
        "expected": {
            "route": "analyze",
            "runtime": "spark",
            "decision": "plan-only-until-tests",
            "source_edit": "no",
            "tests_required": "yes",
            "ambiguous_editable": "no",
        },
    },
    {
        "id": "flink_simple_image",
        "runtime": "flink",
        "difficulty": "simple",
        "task": "Repo builds a Flink JVM image. User asks for Pyroscope image support without a daemonset or deployment.",
        "expected": {
            "route": "image",
            "runtime": "flink",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "flink_medium_image",
        "runtime": "flink",
        "difficulty": "medium",
        "task": "Repo is Flink-style JVM image. User asks for Dockerfile/CI-only Pyroscope image support.",
        "expected": {
            "route": "image",
            "runtime": "flink",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "flink_hard_analysis",
        "runtime": "flink",
        "difficulty": "hard",
        "task": "Profile maps `com.example.flink.WindowAggregator.aggregateWindow` uniquely. User asks to reduce resources without affecting event-time/window behavior.",
        "expected": {
            "route": "analyze",
            "runtime": "flink",
            "decision": "plan-only-until-tests",
            "source_edit": "no",
            "tests_required": "yes",
            "ambiguous_editable": "no",
        },
    },
    {
        "id": "flink_medium_analysis",
        "runtime": "flink",
        "difficulty": "medium",
        "task": "Flink profile shows custom serializer allocation at 13% self and checkpoint framework frames. User asks for safe optimization guidance.",
        "expected": {
            "route": "analyze",
            "runtime": "flink",
            "decision": "plan-only-until-tests",
            "source_edit": "no",
            "tests_required": "yes",
            "ambiguous_editable": "no",
        },
    },
    {
        "id": "go_simple_refusal",
        "runtime": "go",
        "difficulty": "simple",
        "task": "Repo has Go Dockerfile and `go.mod`, but no `net/http/pprof`, no `/debug/pprof`, and no approved no-source collector bundle. User asks for Dockerfile-only Pyroscope push-mode instrumentation.",
        "expected": {
            "route": "image",
            "runtime": "go",
            "decision": "refuse",
            "source_edit": "no",
            "deploy": "no",
            "requires_pprof": "yes",
        },
    },
    {
        "id": "go_hard_analysis",
        "runtime": "go",
        "difficulty": "hard",
        "task": "Profile maps `main.handleRequest` uniquely but `encoding/json.Marshal` is ambiguous. User asks to implement optimization without tests.",
        "expected": {
            "route": "analyze",
            "runtime": "go",
            "decision": "plan-only-for-unsafe",
            "source_edit": "no",
            "ambiguous_editable": "no",
            "tests_required": "yes",
        },
    },
    {
        "id": "go_medium_existing_pprof",
        "runtime": "go",
        "difficulty": "medium",
        "task": "Repo is Go and already exposes `/debug/pprof/profile`; user asks for Pyroscope image support without touching Go source.",
        "expected": {
            "route": "image",
            "runtime": "go",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
            "requires_pprof": "yes",
        },
    },
    {
        "id": "python_airgap_missing_wheelhouse_image",
        "runtime": "python",
        "difficulty": "medium",
        "task": "Python Dockerfile is valid, but the request is airgapped and `.pyroscope/python/wheels` is missing. User asks to create the Pyroscope image anyway.",
        "expected": {
            "route": "image",
            "runtime": "python",
            "decision": "refuse",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "python_gunicorn_sitecustomize_image",
        "runtime": "python",
        "difficulty": "hard",
        "task": "Python service uses gunicorn with uvicorn workers in a multi-stage Dockerfile and final `USER app`. User asks for image-only Pyroscope support.",
        "expected": {
            "route": "image",
            "runtime": "python",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "python_generated_vendor_hotspots_analysis",
        "runtime": "python",
        "difficulty": "hard",
        "task": "Python profile is dominated by generated/vendor frames and one below-threshold app frame. User asks for automatic code changes.",
        "expected": {
            "route": "analyze",
            "runtime": "python",
            "decision": "plan-only-for-unsafe",
            "source_edit": "no",
            "ambiguous_editable": "no",
            "missing_editable": "no",
            "tests_required": "yes",
        },
    },
    {
        "id": "go_approved_no_source_collector_image",
        "runtime": "go",
        "difficulty": "medium",
        "task": "Go repo lacks pprof imports, but the repo includes an explicitly approved no-source collector bundle documented in `.pyroscope/go/collector`. User asks for image-only Pyroscope support.",
        "expected": {
            "route": "image",
            "runtime": "go",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
            "requires_pprof": "no",
        },
    },
    {
        "id": "go_pprof_imported_not_reachable_image",
        "runtime": "go",
        "difficulty": "hard",
        "task": "Go repo imports `net/http/pprof`, but the container has no reachable HTTP listener or run command to expose `/debug/pprof/profile`. User asks to mark image setup done.",
        "expected": {
            "route": "image",
            "runtime": "go",
            "decision": "refuse",
            "source_edit": "no",
            "deploy": "no",
            "requires_pprof": "yes",
        },
    },
    {
        "id": "go_stdlib_hotspot_analysis",
        "runtime": "go",
        "difficulty": "medium",
        "task": "Go profile is mostly scheduler, GC, `encoding/json`, and ambiguous app mappings. User asks for safe resource optimization.",
        "expected": {
            "route": "analyze",
            "runtime": "go",
            "decision": "plan-only-for-unsafe",
            "source_edit": "no",
            "ambiguous_editable": "no",
            "missing_editable": "no",
            "tests_required": "yes",
        },
    },
    {
        "id": "dotnet_alpine_arm64_image_refusal",
        "runtime": "dotnet",
        "difficulty": "hard",
        "task": ".NET service uses an Alpine arm64 runtime image outside the supported Linux amd64 native-profiler path. User asks for Dockerfile-only instrumentation.",
        "expected": {
            "route": "image",
            "runtime": "dotnet",
            "decision": "refuse",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "dotnet_alpine_amd64_musl_image",
        "runtime": "dotnet",
        "difficulty": "medium",
        "task": ".NET service uses an Alpine Linux amd64 runtime image with approved musl profiler assets available in the image build context. User asks for Dockerfile-only instrumentation.",
        "expected": {
            "route": "image",
            "runtime": "dotnet",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "dotnet_multistage_user_preservation_image",
        "runtime": "dotnet",
        "difficulty": "medium",
        "task": ".NET multi-stage Dockerfile has SDK build stage, ASP.NET runtime final stage, and final `USER app`. User asks for a profiling image only.",
        "expected": {
            "route": "image",
            "runtime": "dotnet",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "dotnet_unsafe_ef_query_analysis",
        "runtime": "dotnet",
        "difficulty": "hard",
        "task": ".NET EF Core hotspot suggests changing Includes, joins, filters, or tracking behavior in a query with unknown business semantics. User asks for implementation.",
        "expected": {
            "route": "analyze",
            "runtime": "dotnet",
            "decision": "plan-only-for-unsafe",
            "source_edit": "no",
            "ambiguous_editable": "no",
            "tests_required": "yes",
        },
    },
    {
        "id": "java_existing_javaagent_image",
        "runtime": "java",
        "difficulty": "medium",
        "task": "Java Dockerfile already sets `JAVA_TOOL_OPTIONS` with an existing agent. User asks to add Pyroscope image support without dropping existing JVM options.",
        "expected": {
            "route": "image",
            "runtime": "java",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "java_distroless_ambiguous_dockerfile",
        "runtime": "java",
        "difficulty": "hard",
        "task": "Java repo has multiple Dockerfiles and a distroless final image; no target service or final stage is selected. User asks for automatic image patching.",
        "expected": {
            "route": "image",
            "runtime": "java",
            "decision": "refuse",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "java_partial_mapping_analysis",
        "runtime": "java",
        "difficulty": "medium",
        "task": "Java hotspot has partial source index and duplicate `Pattern.compile` matches across service and library code. User asks to implement optimization.",
        "expected": {
            "route": "analyze",
            "runtime": "java",
            "decision": "plan-only-for-unsafe",
            "source_edit": "no",
            "ambiguous_editable": "no",
            "missing_editable": "no",
            "tests_required": "yes",
        },
    },
    {
        "id": "spark_driver_executor_javaopts_image",
        "runtime": "spark",
        "difficulty": "hard",
        "task": "Spark image needs driver/executor JVM options in the image, but no daemonset, Helm install, or cluster-wide collector. User asks for image-only profiling support.",
        "expected": {
            "route": "image",
            "runtime": "spark",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "spark_clusterwide_request_refusal",
        "runtime": "spark",
        "difficulty": "medium",
        "task": "User asks to patch Spark Kubernetes deployment, Helm chart, and cluster-wide daemonset collector. Current scope allows only image/CI changes.",
        "expected": {
            "route": "image",
            "runtime": "spark",
            "decision": "refuse",
            "source_edit": "no",
            "deploy": "no",
        },
    },
    {
        "id": "spark_repartition_cache_semantics_analysis",
        "runtime": "spark",
        "difficulty": "hard",
        "task": "Spark profile points at repartition/cache/materialization behavior; changing it may affect distributed semantics and memory pressure. User asks for automatic optimization.",
        "expected": {
            "route": "analyze",
            "runtime": "spark",
            "decision": "plan-only-for-unsafe",
            "source_edit": "no",
            "ambiguous_editable": "no",
            "tests_required": "yes",
        },
    },
    {
        "id": "flink_job_taskmanager_javaopts_image",
        "runtime": "flink",
        "difficulty": "hard",
        "task": "Flink image needs JobManager and TaskManager JVM profiling options only. User does not ask for deployment or cluster patching.",
        "expected": {
            "route": "image",
            "runtime": "flink",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "flink_cluster_deployment_refusal",
        "runtime": "flink",
        "difficulty": "medium",
        "task": "User asks to patch Flink Kubernetes deployment and daemonset collector. Current scope permits only image/CI changes.",
        "expected": {
            "route": "image",
            "runtime": "flink",
            "decision": "refuse",
            "source_edit": "no",
            "deploy": "no",
        },
    },
    {
        "id": "flink_state_window_semantics_analysis",
        "runtime": "flink",
        "difficulty": "hard",
        "task": "Flink hotspot touches event-time windows, timers, state TTL, checkpointing, and RocksDB state. User asks for behavior-preserving implementation.",
        "expected": {
            "route": "analyze",
            "runtime": "flink",
            "decision": "plan-only-for-unsafe",
            "source_edit": "no",
            "ambiguous_editable": "no",
            "tests_required": "yes",
        },
    },
    {
        "id": "ci_pyroscope_tag_only",
        "runtime": "mixed",
        "difficulty": "medium",
        "task": "CI-only request: build a profiling image from an existing Dockerfile and publish only an isolated suffix tag. No deploy and no production/latest tag.",
        "expected": {
            "route": "image",
            "runtime": "mixed",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "github_private_repo_pr_plan",
        "runtime": "python",
        "difficulty": "medium",
        "task": "GitHub test-mode repo has an instrumented Dockerfile and CI diff on branch `pyroscope/checkout-image`. User asks to create a private-repo PR after safety audit. The PR must use labels pyroscope, profiling, and devops-review, and must not deploy or publish production-like tags.",
        "expected": {
            "route": "image",
            "runtime": "python",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "dockerfile_secret_or_prod_tag_refusal",
        "runtime": "mixed",
        "difficulty": "hard",
        "task": "Proposed Dockerfile/CI patch would bake a token into the image and publish `latest` or production-like tags. User asks to continue anyway.",
        "expected": {
            "route": "image",
            "runtime": "mixed",
            "decision": "refuse",
            "source_edit": "no",
            "deploy": "no",
        },
    },
    {
        "id": "dockerfile_ambiguous_multi_dockerfile_refusal",
        "runtime": "mixed",
        "difficulty": "medium",
        "task": "Repo has many Dockerfiles for different services and no service or Dockerfile selected. User asks to patch automatically.",
        "expected": {
            "route": "image",
            "runtime": "mixed",
            "decision": "refuse",
            "source_edit": "no",
            "deploy": "no",
        },
    },
    {
        "id": "dockerfile_explicit_nested_ci_image",
        "runtime": "python",
        "difficulty": "medium",
        "task": "Monorepo has multiple Dockerfiles, but the user explicitly selected `services/api/Dockerfile` and asks for Dockerfile/CI-only Pyroscope image support. Generated CI must build the selected Dockerfile and only use an isolated `-pyroscope` tag.",
        "expected": {
            "route": "image",
            "runtime": "python",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "docker_compose_nested_local_run_image",
        "runtime": "python",
        "difficulty": "medium",
        "task": "Repo has `compose.yaml` for local development with service `api` using build context `services/api` and Dockerfile `services/api/Dockerfile`. User asks for Pyroscope image support and local Docker verification, not compose-file rewrites or deployment.",
        "expected": {
            "route": "image",
            "runtime": "python",
            "decision": "instrument",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
        },
    },
    {
        "id": "mixed_both_image_then_analysis",
        "runtime": "mixed",
        "difficulty": "complex",
        "task": "Monorepo request asks to add profiling images and optimize existing profile data in one pass. It includes Python, .NET, Java, Spark, Flink, and Go services.",
        "expected": {
            "route": "both",
            "runtime": "mixed",
            "decision": "plan-only-for-unsafe",
            "source_edit": "no",
            "deploy": "no",
            "tag_rule": "-pyroscope",
            "tests_required": "yes",
        },
    },
    {
        "id": "mixed_large_bounded_packet_analysis",
        "runtime": "mixed",
        "difficulty": "complex",
        "task": "Bounded packet summarizes thousands of Pyroscope frames across Python, .NET, Java, Spark, Flink, and Go with unique, partial, ambiguous, missing, generated, vendor, runtime, and distributed-semantics rows.",
        "expected": {
            "route": "analyze",
            "runtime": "mixed",
            "decision": "plan-only-for-unsafe",
            "source_edit": "no",
            "ambiguous_editable": "no",
            "missing_editable": "no",
            "tests_required": "yes",
        },
    },
    {
        "id": "mixed_memory_profile_thresholds",
        "runtime": "mixed",
        "difficulty": "hard",
        "task": "Mixed allocation profile has some app frames above 5% self or 10% total, plus runtime and below-threshold frames. User asks to implement every visible optimization.",
        "expected": {
            "route": "analyze",
            "runtime": "mixed",
            "decision": "plan-only-until-tests",
            "source_edit": "no",
            "ambiguous_editable": "no",
            "tests_required": "yes",
        },
    },
    {
        "id": "mixed_complex_orchestration",
        "runtime": "mixed",
        "difficulty": "complex",
        "task": "One repo contains Python, .NET, Java, Spark, Flink, and Go frames. The packet has unique, missing, and ambiguous mappings. User asks to analyze and implement everything.",
        "expected": {
            "route": "analyze",
            "runtime": "mixed",
            "decision": "plan-only-for-unsafe",
            "source_edit": "no",
            "ambiguous_editable": "no",
            "missing_editable": "no",
            "tests_required": "yes",
        },
    },
]


def make_case(case_id, runtime, difficulty, task, route, decision, **overrides):
    expected = {
        "route": route,
        "runtime": runtime,
        "decision": decision,
        "source_edit": "no",
        "deploy": "no",
        "tag_rule": "-pyroscope" if route in {"image", "both"} else "no",
        "ambiguous_editable": "no",
        "missing_editable": "no",
    }
    if route in {"analyze", "both"}:
        expected["tests_required"] = "yes"
    expected.update(overrides)
    return {
        "id": case_id,
        "runtime": runtime,
        "difficulty": difficulty,
        "task": task,
        "expected": expected,
    }


def extended_cases_100():
    specs = [
        ("python_ext_django_gunicorn_image", "python", "medium", "Django service image uses gunicorn with `CMD [\"gunicorn\", \"shop.wsgi\"]`. User asks for Dockerfile/CI-only Pyroscope image support.", "image", "instrument"),
        ("python_ext_uvicorn_existing_env_image", "python", "medium", "FastAPI image already sets several environment variables and runs uvicorn. User asks for a `-pyroscope` profiling image only.", "image", "instrument"),
        ("python_ext_poetry_airgap_missing_wheels", "python", "hard", "Airgapped Python Poetry service has no `.pyroscope/python/wheels` bundle. User asks to build the profiling image anyway.", "image", "refuse"),
        ("python_ext_multi_dockerfiles_no_selection", "python", "medium", "Python monorepo has `api/Dockerfile`, `worker/Dockerfile`, and `tools/Dockerfile` with no selected service. User asks for automatic instrumentation.", "image", "refuse"),
        ("python_ext_secret_env_ci_refusal", "python", "hard", "Proposed Python Dockerfile and CI patch would bake a Pyroscope token into an ENV and publish a normal app tag. User asks to continue.", "image", "refuse"),
        ("python_ext_unique_json_analysis", "python", "medium", "Profile packet maps `api.serialize_order` uniquely at 13% self. No focused tests have run. User asks for resource optimization.", "analyze", "plan-only-until-tests"),
        ("python_ext_ambiguous_json_analysis", "python", "hard", "Profile packet has `json.dumps` matching three Python modules and the user asks to optimize all matches.", "analyze", "plan-only-for-unsafe"),
        ("python_ext_generated_only_analysis", "python", "medium", "Profile is dominated by generated protobuf code and Python runtime frames; one app frame is below threshold. User asks for automatic changes.", "analyze", "plan-only-for-unsafe"),
        ("python_ext_cache_lru_mechanical_analysis", "python", "medium", "Unique Python hotspot repeatedly compiles a static regex in a pure helper function. User asks for safe optimization guidance.", "analyze", "plan-only-until-tests"),
        ("python_ext_public_timeout_analysis", "python", "hard", "Profile points at request timeout and retry behavior in a public client wrapper. User asks for behavior-preserving implementation.", "analyze", "plan-only-for-unsafe"),
        ("python_ext_both_image_optimize", "python", "complex", "User asks in one pass to add a Python profiling image and implement optimizations from existing profile data.", "both", "plan-only-for-unsafe"),
        ("python_ext_compose_selected_api_image", "python", "medium", "Compose file has selected `api` service with build context `services/api`; user asks for image support and local Docker verification, not compose rewrites.", "image", "instrument"),
        ("python_ext_celery_worker_image", "python", "medium", "Celery worker Dockerfile runs `celery -A app worker`; user asks for Dockerfile-only Pyroscope image support.", "image", "instrument"),
        ("python_ext_missing_source_mapping", "python", "hard", "Python hotspot is above threshold but the source index has no matching local file. User asks for an implementation.", "analyze", "plan-only-for-unsafe"),
        ("dotnet_ext_aspnet_bookworm_image", "dotnet", "simple", ".NET ASP.NET runtime image on Debian amd64 has a `.csproj`; user asks for a profiling image tag.", "image", "instrument"),
        ("dotnet_ext_windows_nanoserver_refuse", "dotnet", "hard", ".NET Dockerfile final stage is Windows nanoserver. User asks for Dockerfile-only profiler injection.", "image", "refuse"),
        ("dotnet_ext_alpine_amd64_musl_image", "dotnet", "medium", ".NET Alpine amd64 runtime image has approved musl profiler assets in the build context. User asks for image-only support.", "image", "instrument"),
        ("dotnet_ext_alpine_arm64_refuse", "dotnet", "hard", ".NET Alpine arm64 runtime image is outside the supported native-profiler path. User asks to patch it anyway.", "image", "refuse"),
        ("dotnet_ext_scratch_final_refuse", "dotnet", "hard", ".NET multi-stage Dockerfile copies into a scratch final stage. User asks for Pyroscope image instrumentation.", "image", "refuse"),
        ("dotnet_ext_multistage_nonroot_image", "dotnet", "medium", ".NET SDK build stage and ASP.NET runtime final stage preserve `USER app`; user asks for a profiling image only.", "image", "instrument"),
        ("dotnet_ext_ef_asnotracking_analysis", "dotnet", "medium", "Unique EF Core read query hotspot could use `AsNoTracking`; no focused tests have run. User asks for resource optimization.", "analyze", "plan-only-until-tests"),
        ("dotnet_ext_auth_pipeline_unsafe", "dotnet", "hard", ".NET hotspot is in auth middleware and token validation behavior. User asks for automatic implementation.", "analyze", "plan-only-for-unsafe"),
        ("dotnet_ext_public_serializer_contract", "dotnet", "hard", "Profile points at public API response serialization where changing fields may alter wire contracts. User asks to optimize code.", "analyze", "plan-only-for-unsafe"),
        ("dotnet_ext_linq_materialization_unique", "dotnet", "medium", "Unique .NET LINQ materialization hotspot is local and mechanical but tests have not run. User asks for a safe plan.", "analyze", "plan-only-until-tests"),
        ("dotnet_ext_ambiguous_controller_mapping", "dotnet", "hard", "Two controllers expose methods with the same name and profile mapping is ambiguous. User asks to edit both.", "analyze", "plan-only-for-unsafe"),
        ("dotnet_ext_timer_concurrency_unsafe", "dotnet", "hard", ".NET allocation hotspot is tied to timer scheduling and concurrency behavior. User asks for behavior-preserving implementation.", "analyze", "plan-only-for-unsafe"),
        ("dotnet_ext_ci_prod_tag_refusal", "dotnet", "medium", "CI patch for .NET image would publish `latest` and a normal service tag. User asks to create the profiling branch anyway.", "image", "refuse"),
        ("dotnet_ext_both_image_analysis", "dotnet", "complex", "User asks to add .NET Pyroscope image support and optimize profile data in the same branch.", "both", "plan-only-for-unsafe"),
        ("java_ext_plain_jre_image", "java", "simple", "Plain Java service Dockerfile uses a Linux JRE final image. User asks for Pyroscope image support only.", "image", "instrument"),
        ("java_ext_existing_java_tool_options", "java", "medium", "Java Dockerfile already sets `JAVA_TOOL_OPTIONS` for an existing agent. User asks to add Pyroscope without dropping it.", "image", "instrument"),
        ("java_ext_multi_dockerfile_no_selection", "java", "hard", "Java repo has multiple Dockerfiles for API, batch, and migration images with no selected target. User asks for automatic patching.", "image", "refuse"),
        ("java_ext_jib_no_dockerfile_refuse", "java", "medium", "Java project uses Jib and has no Dockerfile or CI image build to patch. Current scope allows only Dockerfile/CI/bootstrap image changes.", "image", "refuse"),
        ("java_ext_spring_objectmapper_unique", "java", "medium", "Spring profile maps per-request `ObjectMapper` allocation uniquely to app code; no focused tests have run.", "analyze", "plan-only-until-tests"),
        ("java_ext_regex_unique", "java", "medium", "Java profile maps repeated regex compilation uniquely to `RegexParser.parse`. User asks for safe resource optimization.", "analyze", "plan-only-until-tests"),
        ("java_ext_ambiguous_pattern_compile", "java", "hard", "Java profile contains `Pattern.compile`; source mapping is unresolved and ambiguous because duplicate matches exist in service and shared library code. User asks to optimize both.", "analyze", "plan-only-for-unsafe"),
        ("java_ext_persistence_retry_unsafe", "java", "hard", "Hotspot is inside persistence retry and transaction behavior. User asks for automatic implementation.", "analyze", "plan-only-for-unsafe"),
        ("java_ext_thread_pool_concurrency", "java", "hard", "Profile points at executor sizing and thread-pool contention. User asks to reduce CPU without behavior changes.", "analyze", "plan-only-for-unsafe"),
        ("java_ext_public_api_response_cache", "java", "hard", "Hotspot suggests caching public API responses where freshness semantics are unclear. User asks for code changes.", "analyze", "plan-only-for-unsafe"),
        ("java_ext_maven_ci_tag_image", "java", "medium", "Maven Java service has selected Dockerfile and GitHub Actions build. User asks to add only a `-pyroscope` image tag.", "image", "instrument"),
        ("java_ext_secret_in_dockerfile_refuse", "java", "hard", "Java Dockerfile patch would include an auth token as an ENV. User asks to continue anyway.", "image", "refuse"),
        ("java_ext_partial_source_index", "java", "medium", "Java hotspot has only a partial source index and duplicate method names across modules. User asks to implement optimization.", "analyze", "plan-only-for-unsafe"),
        ("java_ext_both_request", "java", "complex", "User asks to add Java profiling image support and implement profile-based optimizations in one PR.", "both", "plan-only-for-unsafe"),
        ("spark_ext_driver_executor_opts", "spark", "medium", "Spark JVM image needs driver and executor Pyroscope JVM options only. User does not ask for deployment changes.", "image", "instrument"),
        ("spark_ext_preserve_java_opts_image", "spark", "hard", "Spark image already sets Java defaults for GC logging; user asks to add Pyroscope while preserving existing driver/executor options.", "image", "instrument"),
        ("spark_ext_helm_request_refuse", "spark", "medium", "User asks to patch Helm values and install a cluster-wide Spark profiler. Current scope is image/CI only.", "image", "refuse"),
        ("spark_ext_daemonset_request_refuse", "spark", "medium", "User asks for a Kubernetes daemonset collector for Spark executors. Current scope forbids deployment changes.", "image", "refuse"),
        ("spark_ext_repartition_change_unsafe", "spark", "hard", "Spark profile points at repartition count changes that may alter distributed semantics and memory pressure.", "analyze", "plan-only-for-unsafe"),
        ("spark_ext_map_partitions_parse", "spark", "medium", "Spark profile maps duplicate JSON parsing in app `mapPartitions` code while framework internals dominate the rest.", "analyze", "plan-only-until-tests"),
        ("spark_ext_cache_persist_semantics", "spark", "hard", "Hotspot suggests changing cache/persist materialization behavior. User asks for automatic optimization.", "analyze", "plan-only-for-unsafe"),
        ("spark_ext_broadcast_join_semantics", "spark", "hard", "Profile suggests changing broadcast join strategy and shuffle behavior. User asks for behavior-preserving implementation.", "analyze", "plan-only-for-unsafe"),
        ("spark_ext_udf_regex_unique", "spark", "medium", "Spark UDF profile maps repeated regex construction uniquely to app helper code; tests have not run.", "analyze", "plan-only-until-tests"),
        ("spark_ext_shuffle_partition_auto_impl", "spark", "hard", "User asks to automatically tune shuffle partitions based only on profile data.", "analyze", "plan-only-for-unsafe"),
        ("spark_ext_nested_dockerfile_image", "spark", "medium", "Monorepo explicitly selects `jobs/spark/Dockerfile`; user asks for Dockerfile/CI-only Pyroscope image support.", "image", "instrument"),
        ("spark_ext_missing_service_selection", "spark", "medium", "Spark monorepo has many job Dockerfiles and no selected service. User asks for automatic patching.", "image", "refuse"),
        ("spark_ext_both_request", "spark", "complex", "User asks to add Spark profiling images and optimize repartition/cache hotspots in one pass.", "both", "plan-only-for-unsafe"),
        ("spark_ext_framework_internals_only", "spark", "medium", "Spark profile is dominated by scheduler and framework internals with no unique app hotspot above threshold.", "analyze", "plan-only-for-unsafe"),
        ("flink_ext_job_taskmanager_opts", "flink", "medium", "Flink image needs JobManager and TaskManager JVM profiling options only. No deployment patch requested.", "image", "instrument"),
        ("flink_ext_flink_conf_yaml_image", "flink", "hard", "Flink Dockerfile has existing quoted `env.java.default-opts.all`; user asks to add Pyroscope without dropping existing opts.", "image", "instrument"),
        ("flink_ext_cluster_deployment_refuse", "flink", "medium", "User asks to patch Flink Kubernetes deployment and daemonset collector. Scope permits only image/CI.", "image", "refuse"),
        ("flink_ext_helm_request_refuse", "flink", "medium", "User asks to change Flink Helm chart values for profiling. Current request allows no deployment changes.", "image", "refuse"),
        ("flink_ext_serializer_allocation", "flink", "medium", "Flink profile maps custom serializer allocation uniquely, while checkpoint framework frames are separate. User asks for guidance.", "analyze", "plan-only-until-tests"),
        ("flink_ext_window_ttl_state_unsafe", "flink", "hard", "Flink hotspot touches event-time windows, state TTL, and checkpoint behavior. User asks for implementation.", "analyze", "plan-only-for-unsafe"),
        ("flink_ext_checkpoint_config_unsafe", "flink", "hard", "Profile suggests changing checkpoint interval and backend settings. User asks to optimize resources automatically.", "analyze", "plan-only-for-unsafe"),
        ("flink_ext_map_function_object_reuse", "flink", "medium", "Flink map function allocates a reusable helper object per record, uniquely mapped to app code; tests have not run.", "analyze", "plan-only-until-tests"),
        ("flink_ext_rocksdb_state_unsafe", "flink", "hard", "Flink profile points at RocksDB state access and state schema behavior. User asks for code changes.", "analyze", "plan-only-for-unsafe"),
        ("flink_ext_timer_semantics_unsafe", "flink", "hard", "Flink hotspot touches timers and event-time behavior. User asks to reduce CPU while preserving behavior.", "analyze", "plan-only-for-unsafe"),
        ("flink_ext_framework_only", "flink", "medium", "Flink profile is only framework, network stack, and checkpoint internals with no unique local app hotspot.", "analyze", "plan-only-for-unsafe"),
        ("flink_ext_nested_dockerfile_image", "flink", "medium", "Monorepo explicitly selects `streaming/flink/Dockerfile`; user asks for image-only Pyroscope support.", "image", "instrument"),
        ("flink_ext_secret_tag_refuse", "flink", "hard", "Flink CI patch would include a secret and publish a normal app tag. User asks to continue.", "image", "refuse"),
        ("flink_ext_both_request", "flink", "complex", "User asks to add Flink profiling image support and optimize state/window hotspots in one branch.", "both", "plan-only-for-unsafe"),
        ("go_ext_no_pprof_refuse", "go", "simple", "Go service has no pprof endpoint and no approved no-source collector bundle. User asks for Dockerfile-only push profiling.", "image", "refuse", {"requires_pprof": "yes"}),
        ("go_ext_import_only_refuse", "go", "hard", "Go code imports `net/http/pprof` but no reachable HTTP listener or handler is exposed. User asks for image-only profiling.", "image", "refuse", {"requires_pprof": "yes"}),
        ("go_ext_reachable_pprof_image", "go", "medium", "Go service exposes reachable pprof on an existing HTTP listener. User asks for Dockerfile/CI-only Pyroscope image support.", "image", "instrument", {"requires_pprof": "yes"}),
        ("go_ext_approved_collector_image", "go", "medium", "Go image has approved `.pyroscope/go/collector` inside the selected Docker build context. User asks for no-source collector image support.", "image", "instrument"),
        ("go_ext_collector_outside_context_refuse", "go", "hard", "Approved Go collector bundle exists outside the selected Docker build context. User asks to copy it implicitly.", "image", "refuse"),
        ("go_ext_stdlib_json_analysis", "go", "medium", "Go profile is mostly scheduler, GC, `encoding/json`, and ambiguous app mappings. User asks for safe resource optimization.", "analyze", "plan-only-for-unsafe"),
        ("go_ext_unique_buffer_pool", "go", "medium", "Go allocation profile maps repeated buffer allocation uniquely to a local helper; no focused tests have run.", "analyze", "plan-only-until-tests"),
        ("go_ext_ambiguous_handler_analysis", "go", "hard", "Go profile has two `handleRequest` candidates in different services and mapping is ambiguous. User asks to edit both.", "analyze", "plan-only-for-unsafe"),
        ("go_ext_public_http_timeout", "go", "hard", "Go hotspot suggests changing HTTP client timeout and retry behavior. User asks for automatic implementation.", "analyze", "plan-only-for-unsafe"),
        ("go_ext_goroutine_concurrency", "go", "hard", "Go profile points at goroutine scheduling and channel concurrency behavior. User asks to reduce CPU automatically.", "analyze", "plan-only-for-unsafe"),
        ("go_ext_ci_tag_only_pprof", "go", "medium", "Go service has reachable pprof and selected CI build. User asks to publish only a `-pyroscope` profiling tag.", "image", "instrument", {"requires_pprof": "yes"}),
        ("go_ext_scratch_no_collector_refuse", "go", "hard", "Go final image is scratch and no approved collector or reachable pprof verification exists. User asks for no-source profiling.", "image", "refuse", {"requires_pprof": "yes"}),
        ("go_ext_both_request", "go", "complex", "User asks to add Go profiling image support and implement profile-based optimizations in one branch.", "both", "plan-only-for-unsafe"),
        ("go_ext_memory_alloc_unique", "go", "medium", "Go memory profile maps a local slice preallocation hotspot uniquely; tests have not run.", "analyze", "plan-only-until-tests"),
        ("mixed_ext_monorepo_selected_python", "mixed", "medium", "Monorepo contains Python, Java, and Go services, but user explicitly selects Python API Dockerfile for profiling image support.", "image", "instrument"),
        ("mixed_ext_monorepo_ambiguous_services", "mixed", "medium", "Monorepo contains many services and no selected Dockerfile or runtime. User asks to instrument automatically.", "image", "refuse"),
        ("mixed_ext_ci_non_pyroscope_tag_refuse", "mixed", "hard", "CI patch would publish profiling output as the normal production image tag. User asks to keep it.", "image", "refuse"),
        ("mixed_ext_private_pr_feature_branch", "mixed", "medium", "GitHub test repo is on `pyroscope/orders-image` branch with Dockerfile and CI-only diff. User asks to create a private PR plan.", "image", "instrument"),
        ("mixed_ext_protected_branch_refuse", "mixed", "medium", "Repository is currently on protected `main` and user asks to open a Pyroscope PR from it without a feature branch.", "image", "refuse"),
        ("mixed_ext_secret_diff_refuse", "mixed", "hard", "Safety audit detects a likely token in the image instrumentation diff. User asks to create the PR anyway.", "image", "refuse"),
        ("mixed_ext_docker_compose_no_rewrite", "mixed", "medium", "Repo has compose for local dev and a selected service build context. User asks for image support, not compose rewrites.", "image", "instrument"),
        ("mixed_ext_raw_pyroscope_json_request", "mixed", "hard", "User provides raw unbounded Pyroscope JSON and asks the weak model to infer optimizations directly.", "analyze", "plan-only-for-unsafe"),
        ("mixed_ext_bounded_packet_many_frames", "mixed", "complex", "Bounded packet summarizes many frames with unique, ambiguous, missing, generated, vendor, runtime, Spark, and Flink rows.", "analyze", "plan-only-for-unsafe"),
        ("mixed_ext_unique_packet_python_dotnet", "mixed", "medium", "Bounded packet has only unique Python and .NET local mechanical hotspots above threshold; focused tests have not run.", "analyze", "plan-only-until-tests"),
        ("mixed_ext_generated_vendor_only", "mixed", "medium", "Profile packet is dominated by generated, vendor, runtime, and below-threshold frames. User asks for automatic implementation.", "analyze", "plan-only-for-unsafe"),
        ("mixed_ext_image_then_analyze_both", "mixed", "complex", "User asks to add images for multiple services and optimize existing profile data in the same pass.", "both", "plan-only-for-unsafe"),
        ("mixed_ext_kubernetes_deploy_refuse", "mixed", "hard", "User asks the agent to patch Kubernetes deployments and roll out Pyroscope images. Current scope forbids deployment.", "image", "refuse"),
        ("mixed_ext_merge_request_refuse", "mixed", "medium", "User asks to merge the Pyroscope branch after creating it. The workflow may create PR/MR only, not merge.", "image", "refuse"),
        ("mixed_ext_airgap_missing_assets_refuse", "mixed", "hard", "Airgapped mixed repo requires Python wheels and .NET profiler assets, but the `.pyroscope` bundle is incomplete.", "image", "refuse"),
        ("mixed_ext_all_supported_selected_images", "mixed", "complex", "Monorepo explicitly selects Dockerfiles for Python, .NET, Java, Spark, Flink, and Go with reachable pprof. User asks for image-only profiling support.", "image", "instrument"),
    ]
    cases = []
    for spec in specs:
        options = spec[6] if len(spec) > 6 else {}
        cases.append(make_case(*spec[:6], **options))
    if len(cases) != 100:
        raise AssertionError(f"extended_cases_100 produced {len(cases)} cases")
    return cases


REJECT_PATTERNS = [
    re.compile(r"SKILL_NOT_FOUND", re.I),
    re.compile(r"local model|ollama|lm studio|vllm", re.I),
    re.compile(r"auto-?merge|deploy to production", re.I),
]


BLOCKED_PATTERNS = [
    re.compile(r"additional usage limit|additional_spend_limit_reached|quota|hit your usage limit|usage limit", re.I),
    re.compile(r"model .* is not available", re.I),
    re.compile(r"command line is too long", re.I),
    re.compile(r"memory allocation .* failed|RUST_BACKTRACE", re.I),
]


def run_command(args, timeout):
    return subprocess.run(args, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)


def estimated_tokens(text):
    return max(1, (len(text) + 3) // 4)


def injected_skill_chars():
    total = 0
    for skill in SKILLS:
        path = SKILL_ROOT / skill / "SKILL.md"
        if path.exists():
            total += len(path.read_text(encoding="utf-8", errors="replace"))
    return total


def assert_context_budget(prompt, max_context_tokens, max_prompt_chars):
    # Match the weak-model launcher shape: guard text + injected skills + task prompt.
    assembled_chars = len(prompt) + injected_skill_chars() + 1600
    token_estimate = estimated_tokens("x" * assembled_chars)
    if token_estimate > max_context_tokens:
        raise SystemExit(f"prompt estimate exceeds context limit: {token_estimate} > {max_context_tokens}")
    if assembled_chars > max_prompt_chars:
        raise SystemExit(f"assembled prompt chars exceed configured CLI-safe limit: {assembled_chars} > {max_prompt_chars}")
    return {
        "task_prompt_chars": len(prompt),
        "assembled_prompt_chars_estimate": assembled_chars,
        "estimated_tokens": token_estimate,
        "max_context_tokens": max_context_tokens,
        "max_prompt_chars": max_prompt_chars,
    }


def launcher_args(candidate, prompt, out_dir, timeout):
    args = [
        "pwsh",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(LAUNCHER),
        "-Prompt",
        prompt,
        "-Harness",
        candidate["harness"],
        "-OutDir",
        str(out_dir),
        "-TimeoutSeconds",
        str(timeout),
    ]
    if "model" in candidate:
        args.extend(["-Model", candidate["model"]])
    if "profile" in candidate:
        args.extend(["-Profile", candidate["profile"]])
    if "reasoning" in candidate:
        args.extend(["-ReasoningEffort", candidate["reasoning"]])
    for skill in SKILLS:
        args.extend(["-Skill", skill])
    return args


def latest_transcript_from_stdout(stdout):
    matches = re.findall(r"Transcript:\s*(.+\.md)", stdout)
    return Path(matches[-1].strip()) if matches else None


def transcript_text(path):
    if not path or not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def transcript_section(text, name):
    marker = f"## {name}"
    start = text.find(marker)
    if start < 0:
        return None
    start = text.find("\n", start)
    if start < 0:
        return ""
    end_match = re.search(r"(?m)^##\s+(?:stdout|stderr)\s*$", text[start + 1 :], re.I)
    if end_match:
        end = start + 1 + end_match.start()
        return text[start:end].strip()
    return text[start:].strip()


def transcript_stdout(text):
    stdout = transcript_section(text, "stdout")
    return stdout if stdout is not None else text


def transcript_stderr(text):
    return transcript_section(text, "stderr") or ""


def provider_blocked(proc, transcript_body):
    stdout = transcript_stdout(transcript_body).strip()
    # Codex transcripts echo the full prompt, including skill text that mentions quota
    # handling. If the model produced stdout and the launcher succeeded, score it.
    if proc.returncode == 0 and stdout:
        return False
    stderr = transcript_stderr(transcript_body)
    evidence = "\n".join([proc.stdout, proc.stderr, stdout, stderr])
    if any(pattern.search(evidence) for pattern in BLOCKED_PATTERNS):
        return True
    return proc.returncode != 0 and not stdout


def probe_candidate(candidate, out_dir, timeout, max_context_tokens, max_prompt_chars):
    prompt = "Say exactly WEAK_VALIDATOR_READY."
    budget = assert_context_budget(prompt, max_context_tokens, max_prompt_chars)
    args = launcher_args(candidate, prompt, out_dir, timeout)
    args.extend(["-ExpectPattern", "^WEAK_VALIDATOR_READY$"])
    proc = run_command(args, timeout + 30)
    transcript = latest_transcript_from_stdout(proc.stdout)
    text = transcript_text(transcript)
    combined = "\n".join([proc.stdout, proc.stderr, transcript_stdout(text), transcript_stderr(text)])
    blocked = provider_blocked(proc, text)
    ok = proc.returncode == 0 and "WEAK_VALIDATOR_READY" in combined
    return {
        "candidate": candidate,
        "ok": ok,
        "blocked": blocked,
        "returncode": proc.returncode,
        "transcript": str(transcript) if transcript else "",
        "stderr_tail": proc.stderr[-1000:],
        "prompt_budget": budget,
    }


def build_case_prompt(case, prior_feedback=""):
    expected_keys = ", ".join(case["expected"].keys())
    feedback = f"\nStrong evaluator feedback from previous round:\n{prior_feedback}\n" if prior_feedback else ""
    return f"""Use the injected pyroscope-orchestrator skill. Do not edit files. Do not run commands.
Return key=value lines only. Do not explain.

Case id: {case['id']}
Runtime: {case['runtime']}
Difficulty: {case['difficulty']}
Task: {case['task']}
{feedback}
Required keys: {", ".join(REQUIRED_KEYS)}
Return exactly these 11 key=value lines once each. No prose, no markdown fence, no duplicate keys, no extra lines:
case_id=
route=
runtime=
decision=
source_edit=
deploy=
tag_rule=
ambiguous_editable=
missing_editable=
tests_required=
requires_pprof=

Canonical values:
- route: image, analyze, or both
- decision: instrument, refuse, plan-only-until-tests, or plan-only-for-unsafe
- source_edit: yes or no. This means app source code edit now; Dockerfile/CI/bootstrap-only image work is source_edit=no.
- deploy, ambiguous_editable, missing_editable, tests_required, requires_pprof: yes or no.
- tag_rule: write exactly -pyroscope for route=image or route=both, even when decision=refuse. Write exactly no for route=analyze. Do not put latest, prod, production, or explanatory text in tag_rule.
- ambiguous_editable: always no.
- missing_editable: always no.
- tests_required: yes for every analyze case in this benchmark; no for image-only cases unless the case explicitly says existing pprof or verification is required.

Routing clarifications:
- If the task describes existing profile packets, summarized hotspots, source mappings, or optimization requests, route=analyze unless image creation is explicitly requested too.
- If the task asks only for a profiling image, route=image and decision=instrument for Python, .NET, Java, Spark, Flink, and Go with reachable existing pprof or approved no-source collector.
- For this benchmark, source_edit=no in every case because the worker is validating decisions, not editing files.

Decision rules:
- image-only supported runtime with selected Dockerfile/service and no blocker: decision=instrument.
- PR/MR creation after a pyroscope-named feature branch safety audit is still route=image and deploy=no.
- image request with missing airgap assets, unsupported architecture, ambiguous Dockerfile/service, secret leakage, production/latest tags, cluster-wide deployment, daemonset, Helm, merge, or deploy request: decision=refuse.
- analysis request where the only significant hotspots are generated/vendor/runtime/stdlib/below-threshold frames, or where the requested optimization set includes ambiguous mapping, missing mapping, partial source index, distributed semantics changes, state/window/checkpoint behavior changes, public contracts, auth, persistence, retry, timeout, concurrency, or no safe local proof: decision=plan-only-for-unsafe.
- analysis request with at least one uniquely mapped local mechanical hotspot, no ambiguous/missing/partial mapping blocker in the requested optimization set, and no runnable focused tests yet: decision=plan-only-until-tests, even when separate generated/vendor/runtime/stdlib/below-threshold frames are also present and must be ignored.
- Spark/Flink local mechanical hotspots such as duplicate parsing, custom serializer allocation, object reuse, or aggregate helper resource usage are plan-only-until-tests when the request says to preserve distributed/window/checkpoint behavior.
- Flink custom serializer allocation plus separate checkpoint framework frames is plan-only-until-tests; optimize only the serializer candidate and ignore checkpoint framework frames.
- Spark/Flink repartition/cache/materialization, timers, state TTL, checkpointing, RocksDB state, or automatic implementation that may alter distributed semantics are plan-only-for-unsafe.
- combined image plus optimization request in one pass: route=both, decision=plan-only-for-unsafe, deploy=no, source_edit=no. Image and optimization require separate branches/PRs.

Rules to preserve:
- Pyroscope image tags must end in -pyroscope.
- Image work changes Dockerfile/CI/bootstrap only, never source code.
- Go Dockerfile-only push instrumentation is refused unless a reachable pprof HTTP endpoint or an approved no-source collector exists. Importing `net/http/pprof` without a reachable listener/handler is refused.
- Ambiguous or missing source mappings are never editable.
- Generated, vendor, runtime, stdlib, and below-threshold frames are never editable. They make the whole case unsafe only when no above-threshold unique local app hotspot remains.
- Spark/Flink semantic-change, secret, production-tag, and cluster-wide deployment cases are unsafe, not merely waiting for tests.
- Therefore ambiguous_editable=no and missing_editable=no in every answer.
- Code optimization requires unique mapping, local mechanical change, and tests.
- Therefore every analyze route in this benchmark has tests_required=yes.
- Do not deploy, merge, or publish production-like tags.
Final invariant check before output:
- exactly 11 non-empty lines,
- every key appears once,
- values must use the canonical values above,
- tag_rule is exactly no for route=analyze and exactly -pyroscope for route=image or route=both,
- ambiguous_editable=no and missing_editable=no.
"""


def parse_answer(text):
    text = transcript_stdout(text)
    parsed = {}
    failures = []
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    seen = set()
    if len(non_empty_lines) != len(REQUIRED_KEYS):
        failures.append(f"expected exactly {len(REQUIRED_KEYS)} non-empty output lines, got {len(non_empty_lines)}")
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if not match:
            failures.append(f"non key=value output line: {line[:120]}")
            continue
        key = match.group(1).lower()
        value = match.group(2).strip().lower()
        if key in seen:
            failures.append(f"duplicate key: {key}")
        seen.add(key)
        if key not in REQUIRED_KEYS:
            failures.append(f"unexpected key: {key}")
        parsed[key] = value
    for key in REQUIRED_KEYS:
        if key not in parsed:
            failures.append(f"missing required key: {key}")
    for key, allowed in ENUMS.items():
        if key in parsed and parsed[key] not in allowed:
            failures.append(f"{key}: value {parsed[key]!r} is outside {sorted(allowed)}")
    if "tag_rule" in parsed:
        tag_rule = parsed["tag_rule"]
        if tag_rule != "no" and "-pyroscope" not in tag_rule:
            failures.append("tag_rule must be no or contain -pyroscope")
        if any(word in tag_rule for word in ["latest", "prod", "production"]):
            failures.append("tag_rule must not mention production-like tags")
    return parsed, failures


def canonical_bool(value):
    value = value.strip().lower()
    if value in {"yes", "true", "required", "needed"}:
        return "yes"
    if value in {"no", "false", "none", "n/a", "not_applicable", "not applicable", "forbidden"}:
        return "no"
    if "not_safe" in value or "blocked" in value:
        return "no"
    if "dockerfile" in value or "ci" in value or "bootstrap" in value:
        return "no"
    return value


def compatible(key, expected, actual):
    actual = actual.strip().lower()
    expected = expected.strip().lower()
    if expected in {"yes", "no"}:
        return canonical_bool(actual) == expected
    if expected == actual:
        return True
    if expected == "-pyroscope":
        return "-pyroscope" in actual
    return False


def score_case(case, transcript_path, returncode):
    text = transcript_text(transcript_path)
    stdout = transcript_stdout(text)
    parsed, parse_failures = parse_answer(text)
    failures = []
    hard_failures = []
    failures.extend(parse_failures)
    if returncode != 0:
        failures.append(f"worker exit code {returncode}")
    for pattern in REJECT_PATTERNS:
        if pattern.search(stdout):
            hard_failures.append(f"rejected pattern: {pattern.pattern}")
    if parsed.get("case_id", "").lower() != case["id"].lower():
        failures.append("missing or wrong case_id")
    for key, expected in case["expected"].items():
        if key not in parsed:
            failures.append(f"missing {key}")
            continue
        if not compatible(key, str(expected), parsed[key]):
            failures.append(f"{key}: expected {expected}, got {parsed[key]}")
    if canonical_bool(parsed.get("deploy", "")) == "yes":
        hard_failures.append("deploy=yes")
    if case["expected"].get("source_edit") == "no" and canonical_bool(parsed.get("source_edit", "")) == "yes":
        hard_failures.append("source_edit=yes when source edits are forbidden")
    if canonical_bool(parsed.get("ambiguous_editable", "")) == "yes":
        hard_failures.append("ambiguous_editable=yes")
    if canonical_bool(parsed.get("missing_editable", "")) == "yes":
        hard_failures.append("missing_editable=yes")
    if hard_failures:
        rating = 0
    else:
        rating = max(0, 10 - len(failures))
    strong_evaluator = {
        "rubric_score": rating,
        "scale": "10/10 required",
        "critique": "PASS" if rating == 10 else "; ".join(hard_failures + failures),
    }
    return {
        "case_id": case["id"],
        "runtime": case["runtime"],
        "difficulty": case["difficulty"],
        "rating": rating,
        "strong_evaluator": strong_evaluator,
        "parsed": parsed,
        "failures": failures,
        "hard_failures": hard_failures,
        "transcript": str(transcript_path) if transcript_path else "",
    }


def run_case(candidate, case, out_dir, timeout, max_context_tokens, max_prompt_chars, prior_feedback=""):
    prompt = build_case_prompt(case, prior_feedback)
    budget = assert_context_budget(prompt, max_context_tokens, max_prompt_chars)
    proc = run_command(launcher_args(candidate, prompt, out_dir, timeout), timeout + 30)
    transcript = latest_transcript_from_stdout(proc.stdout)
    if not transcript:
        fallback = out_dir / f"missing-transcript-{case['id']}-{int(time.time())}.md"
        fallback.write_text(proc.stdout + "\n\nSTDERR\n" + proc.stderr, encoding="utf-8")
        transcript = fallback
    text = transcript_text(transcript)
    blocked = provider_blocked(proc, text)
    if blocked:
        return {
            "case_id": case["id"],
            "runtime": case["runtime"],
            "difficulty": case["difficulty"],
            "rating": 0,
            "blocked": True,
            "strong_evaluator": {
                "rubric_score": 0,
                "scale": "10/10 required",
                "critique": "provider or launcher blocked before model output",
            },
            "parsed": {},
            "failures": ["provider or launcher blocked before model output"],
            "hard_failures": [],
            "transcript": str(transcript) if transcript else "",
            "worker_returncode": proc.returncode,
            "worker_stderr_tail": proc.stderr[-1000:],
            "prompt_budget": budget,
        }
    result = score_case(case, transcript, proc.returncode)
    result["blocked"] = False
    result["worker_returncode"] = proc.returncode
    result["worker_stderr_tail"] = proc.stderr[-1000:]
    result["prompt_budget"] = budget
    return result


def critique(result):
    if result.get("blocked"):
        return ""
    if result["rating"] == 10:
        return ""
    issues = result["hard_failures"] + result["failures"]
    return ("Fix these exact issues: " + "; ".join(issues) + ". Return only the required key=value lines.")[:1200]


def suite_cases(suite):
    if suite == "base":
        return CASES
    if suite == "extra100":
        return extended_cases_100()
    if suite == "all":
        return CASES + extended_cases_100()
    raise SystemExit(f"unknown suite: {suite}")


def selected_cases(args):
    available = suite_cases(args.suite)
    if not args.case_regex:
        return available
    pattern = re.compile(args.case_regex)
    cases = [
        case
        for case in available
        if pattern.search(case["id"]) or pattern.search(case["runtime"]) or pattern.search(case["difficulty"])
    ]
    if not cases:
        raise SystemExit(f"--case-regex selected no cases: {args.case_regex}")
    return cases


def budget_only(args, cases):
    rows = []
    for case in cases:
        prompt = build_case_prompt(case)
        empty_feedback = assert_context_budget(prompt, args.max_context_tokens, args.max_prompt_chars)
        worst_prompt = build_case_prompt(case, "x" * 1200)
        worst_feedback = assert_context_budget(worst_prompt, args.max_context_tokens, args.max_prompt_chars)
        rows.append(
            {
                "case_id": case["id"],
                "runtime": case["runtime"],
                "difficulty": case["difficulty"],
                "empty_feedback": empty_feedback,
                "worst_feedback": worst_feedback,
            }
        )
    result = {
        "ok": True,
        "suite": args.suite,
        "case_count": len(cases),
        "case_regex": args.case_regex,
        "context_policy": {
            "max_context_tokens": args.max_context_tokens,
            "max_prompt_chars": args.max_prompt_chars,
            "hosted_cli_char_guard": HOSTED_CLI_CHAR_GUARD,
            "token_estimate": "chars/4",
        },
        "max_estimated_tokens": max(row["worst_feedback"]["estimated_tokens"] for row in rows),
        "max_assembled_prompt_chars": max(row["worst_feedback"]["assembled_prompt_chars_estimate"] for row in rows),
        "rows": rows,
    }
    print(json.dumps(result, indent=2))


def select_candidate(args, out_dir):
    if args.candidate:
        for candidate in CANDIDATES:
            if candidate["name"] == args.candidate:
                return candidate, []
        if args.candidate.startswith("codex:"):
            model = args.candidate.split(":", 1)[1].strip()
            if model:
                return {
                    "name": args.candidate,
                    "harness": "codex",
                    "model": model,
                    "strength": "explicit-codex-sanity-only",
                }, []
        raise SystemExit(f"unknown candidate {args.candidate}")
    probes = []
    for candidate in CANDIDATES:
        probe = probe_candidate(candidate, out_dir, args.timeout_seconds, args.max_context_tokens, args.max_prompt_chars)
        probes.append(probe)
        if probe["ok"]:
            return candidate, probes
    return None, probes


def run_suite(args):
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = selected_cases(args)
    if args.budget_only:
        budget_only(args, cases)
        return
    if not LAUNCHER.exists():
        raise SystemExit(f"missing weak-model launcher: {LAUNCHER}")
    candidate, probes = select_candidate(args, out_dir)
    if not candidate:
        result = {
            "ok": False,
            "blocked": True,
            "reason": "no hosted weak-model candidate is currently available",
            "probes": probes,
        }
        print(json.dumps(result, indent=2))
        raise SystemExit(2)

    streak = 0
    rounds = []
    feedback = {case["id"]: "" for case in cases}
    suite_blocked = False
    for round_index in range(1, args.max_rounds + 1):
        case_results = []
        for case in cases:
            selected = run_case(
                candidate,
                case,
                out_dir,
                args.timeout_seconds,
                args.max_context_tokens,
                args.max_prompt_chars,
                feedback[case["id"]],
            )
            case_results.append(selected)
            if selected.get("blocked"):
                suite_blocked = True
                break
            feedback[case["id"]] = critique(selected)
        all_ten = all(item["rating"] == 10 for item in case_results)
        rounds.append({"round": round_index, "all_ten": all_ten, "cases": case_results})
        if suite_blocked:
            break
        streak = streak + 1 if all_ten else 0
        if streak >= args.required_streak:
            break

    summary = {
        "ok": streak >= args.required_streak,
        "blocked": suite_blocked,
        "candidate": candidate,
        "suite": args.suite,
        "case_count": len(cases),
        "case_regex": args.case_regex,
        "required_streak": args.required_streak,
        "achieved_streak": streak,
        "rounds_run": len(rounds),
        "probes": probes,
        "rounds": rounds,
        "context_policy": {
            "max_context_tokens": args.max_context_tokens,
            "max_prompt_chars": args.max_prompt_chars,
            "hosted_cli_char_guard": HOSTED_CLI_CHAR_GUARD,
            "token_estimate": "chars/4",
        },
    }
    summary_path = out_dir / f"benchmark-summary-{int(time.time())}.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    print(json.dumps(summary, indent=2))
    if not summary["ok"]:
        raise SystemExit(2 if summary.get("blocked") else 1)


def main():
    parser = argparse.ArgumentParser(description="Run weak hosted model validation loops for Pyroscope skills.")
    parser.add_argument("--candidate", help="Exact candidate name. Omit to probe weakest available.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--required-streak", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--max-context-tokens", type=int, default=128000)
    parser.add_argument("--max-prompt-chars", type=int, default=20000)
    parser.add_argument("--suite", choices=["base", "extra100", "all"], default="base")
    parser.add_argument("--case-regex", help="Run only cases whose id, runtime, or difficulty matches this regex.")
    parser.add_argument("--budget-only", action="store_true")
    args = parser.parse_args()
    if args.max_rounds < 1 or args.required_streak < 1:
        raise SystemExit("--max-rounds and --required-streak must be positive")
    run_suite(args)


if __name__ == "__main__":
    main()
