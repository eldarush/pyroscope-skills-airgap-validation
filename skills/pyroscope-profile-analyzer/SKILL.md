---
name: pyroscope-profile-analyzer
description: Analyze Pyroscope profile data and local source code to produce safe, actionable optimization plans. Use when asked to inspect existing Pyroscope data, export/query profiles, map hotspots to repository code, explain CPU or memory waste, create behavior-preserving optimization plans, or implement low-risk mechanical optimizations with tests. Designed for weak/airgapped models by using deterministic summarizer scripts before reasoning.
---

# Pyroscope Profile Analyzer

## Contract

Default to analysis-only. Implement code only in `implement-safe` mode and only when behavior preservation is locally obvious and test-covered.

Never change:

- public API contracts,
- persistence semantics,
- auth/security,
- validation rules,
- retry/timeout behavior,
- lock/concurrency ordering,
- business decisions,
- generated/vendor/dependency code.

If source mapping or logic preservation is uncertain, produce a plan instead of edits.

## Workflow

1. Query Pyroscope labels/profile types with `scripts/pyroscope_profile_tool.py discover`.
2. Summarize a bounded profile window with `scripts/pyroscope_profile_tool.py summarize`.
   For offline stress tests or exported folded profiles, use `scripts/pyroscope_profile_tool.py summarize-folded --file <profile.folded> --service <name>`.
3. Map eligible frames to local source files using `pyroscope-agent.yaml` and repository search.
4. Produce a ranked report:
   `Function | Self % | Total % | Mapping | Source matches | Recommendation`.
   Use `scripts/pyroscope_profile_tool.py report` to create a compact Markdown handoff.
   Mapping values other than `unique`, including `partial`, are plan-only and not editable.
   Treat recommendation text as a starting point; code edits still require the safety gates below.
   For weak-model handoff, run `scripts/pyroscope_weak_model_packet.py`; the generated packet is the bounded context. Do not load raw Pyroscope JSON, folded stacks, pprof dumps, or unbounded reports into a weak-model prompt.
5. For `implement-safe`, require tests or add focused tests first, make only mechanical local changes, run tests, and create a PR/MR. Never auto-merge.

## Eligibility Thresholds

Recommend only when:

- application-code frame has at least 5% self CPU/allocation,
- or application-code subtree has at least 10% total,
- or the same function is top 10 in two windows.

Ignore:

- runtime/framework internals,
- generated/vendor/test code,
- profiler overhead,
- unknown symbols,
- ambiguous source matches.
- partial source indexes caused by file-count limits.

## Safe Edits

Allowed examples:

- compile regex once,
- reuse serializer/options objects,
- avoid duplicate materialization,
- pre-size collections when count is known,
- avoid log string formatting when logging is disabled,
- replace obviously equivalent hot-loop allocation patterns.

Refuse examples:

- change database filters/joins/includes,
- change cache invalidation semantics,
- change locks or async ordering,
- reduce resource limits blindly,
- remove audit/security logs,
- change algorithms with unclear domain meaning.

## Required PR Text

Every optimization PR must include:

- hotspot evidence,
- source mapping confidence,
- behavior-preservation explanation,
- tests run,
- expected resource impact,
- Pyroscope validation query/window to confirm after deployment.

## References

- API and report format: `references/pyroscope-api.md`
- Helper: `scripts/pyroscope_profile_tool.py`
- Stress fixture generator: `scripts/pyroscope_stress_fixture.py` (`--mixed-runtimes` adds Python, .NET/C#, Java, Scala/Spark/Flink-style, and Go source frames).
- Complex deterministic smoke: `scripts/pyroscope_complex_profile_smoke.py` generates a large mixed folded profile, maps sources, creates a report, builds a weak-model packet, and audits 128k context safety without a live Pyroscope server.
- Profile budget stress: `scripts/pyroscope_profile_budget_stress.py` generates a high-cardinality mixed folded profile, validates streaming summarization, maps thousands of source files, and proves the final weak-model packet stays bounded.
- Local round-trip smoke: `scripts/pyroscope_local_roundtrip_smoke.py --url http://localhost:4040` ingests a folded profile into a real Pyroscope server, queries it back, maps sources, creates a report, builds a packet, and audits weak-model context safety.
