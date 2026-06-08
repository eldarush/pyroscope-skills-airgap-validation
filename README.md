# Pyroscope Skills Airgap Validation Package

This repository contains a validated local bundle for using Pyroscope with weak or airgapped coding agents.

It covers two workflows:

1. Build a separate profiling image by changing only Dockerfile, CI, or bootstrap image wiring. Profiling tags must end with `-pyroscope`.
2. Read existing Pyroscope profiling data, reduce it into a bounded weak-model packet, identify safe optimization candidates, and produce plans or code changes only when behavior preservation is locally obvious and test-covered.

The current package was validated on 2026-06-08.

## Current Status

| Area | Result |
| --- | --- |
| Airgap bundle | PASS, verified zip with 29 files |
| Local Pyroscope round-trip | PASS against `http://localhost:4040` |
| Weak-model benchmark | PASS on `extra100` suite |
| Weak model used | `codex:gpt-5.3-codex-spark` as the weakest runnable Codex-hosted fallback |
| Consecutive perfect rounds | 2 full rounds, 100/100 each |
| 128k context budget | PASS, packets and prompts stay far below 128k |

Important caveat: the preferred MiniMax-like hosted routes were blocked or unavailable in this environment. `gpt-5.3-codex-spark` is the weakest runnable Codex-hosted fallback here, but it may still be stronger than MiniMax M2.5. The skills are therefore designed to keep weak-model work deterministic, compact, and safety-gated.

## Folder Layout

```text
.
|-- README.md
|-- STATUS.html
|-- artifacts/
|   `-- pyroscope-skills-airgap-20260608-extra100-v4-public.zip
|-- skills/
|   |-- pyroscope-image-instrumenter/
|   |-- pyroscope-profile-analyzer/
|   `-- pyroscope-orchestrator/
`-- evidence/
    |-- validation-after-extra100-v2/
    |-- weak-codex-extra100-spark-after-reset/
    |-- weak-hosted-probe-current/
    `-- codex-54-mini-java-ambiguity-script-sanity/
```

Open `STATUS.html` for a short visual overview.

## Install In An Airgapped Agent Environment

Copy the zip from:

```powershell
artifacts\pyroscope-skills-airgap-20260608-extra100-v4-public.zip
```

Extract it into the target agent skills directory. For Codex-style local skills on Windows, that is usually:

```powershell
C:\Users\<user>\.codex\skills
```

Expected extracted skills:

```text
pyroscope-image-instrumenter
pyroscope-profile-analyzer
pyroscope-orchestrator
```

Then audit the install:

```powershell
python .\pyroscope-orchestrator\scripts\pyroscope_orchestrator.py audit
```

If you are validating from this repository without installing globally, run the same script from `skills\pyroscope-orchestrator\scripts`.

## Workflow 1: Create A Pyroscope Image

Use the `pyroscope-orchestrator` skill for routing. It delegates image work to `pyroscope-image-instrumenter`.

Input the agent should require:

- one repository path,
- one selected service or Dockerfile,
- one selected runtime,
- one target Pyroscope URL,
- confirmation that the image tag suffix is `-pyroscope`,
- confirmation that only Dockerfile, CI, or bootstrap image wiring may change.

Allowed output:

- feature branch,
- Dockerfile or CI diff,
- profiling image tag ending with `-pyroscope`,
- local build/run proof where possible,
- PR or MR.

Forbidden output:

- source code edits for image-only work,
- production, latest, or normal application tags,
- deployment, Helm install, cluster-wide DaemonSet work, merge, or auto-release,
- secrets baked into Dockerfile or CI diffs.

Use the deterministic route helper:

```powershell
python .\skills\pyroscope-orchestrator\scripts\pyroscope_orchestrator.py plan --repo <repo-path> --task image --service <service-name>
```

## Workflow 2: Understand Pyroscope Data With Weak Models

This is the core profile-analysis path. The weak model should never ingest raw unbounded Pyroscope JSON, pprof dumps, full folded stacks, or massive reports.

Use this sequence instead:

1. Discover available labels and profile types.
2. Summarize a bounded time window.
3. Create a compact Markdown report.
4. Convert the report into a strict weak-model packet.
5. Audit packet size and source-mapping safety.
6. Let the weak model reason only over that packet and the skill safety gates.

Commands:

```powershell
python .\skills\pyroscope-profile-analyzer\scripts\pyroscope_profile_tool.py discover --url http://localhost:4040
```

```powershell
python .\skills\pyroscope-profile-analyzer\scripts\pyroscope_profile_tool.py summarize --url http://localhost:4040 --service <service-name> --profile-type process_cpu:cpu:nanoseconds:cpu:nanoseconds --from <start> --until <end> --output profile-summary.json
```

```powershell
python .\skills\pyroscope-profile-analyzer\scripts\pyroscope_profile_tool.py report --summary profile-summary.json --repo <repo-path> --output pyroscope-report.md
```

```powershell
python .\skills\pyroscope-orchestrator\scripts\pyroscope_weak_model_packet.py --report pyroscope-report.md --repo <repo-path> --task analyze --pretty --output weak-model-packet.json
```

```powershell
python .\skills\pyroscope-orchestrator\scripts\pyroscope_weak_model_audit.py --report pyroscope-report.md --packet weak-model-packet.json --max-context-tokens 128000
```

For exported folded profiles instead of a live Pyroscope query:

```powershell
python .\skills\pyroscope-profile-analyzer\scripts\pyroscope_profile_tool.py summarize-folded --file profile.folded --service <service-name> --output profile-summary.json
```

The packet is the weak-model handoff. The model can explain hotspots and propose plans from it, but it must not edit code unless all gates pass.

## Optimization Safety Rules

Implementation is allowed only when all of these are true:

- source mapping is unique,
- the hotspot is local application code,
- the change is mechanical and behavior-preserving,
- focused tests exist or are added first,
- tests pass,
- the diff does not touch public contracts, persistence semantics, auth/security, retry/timeout behavior, locks, concurrency ordering, or business rules.

Safe examples:

- compile a regex once,
- reuse serializer/options objects,
- avoid duplicate collection materialization,
- pre-size collections when count is known,
- avoid hot-loop allocation patterns that are obviously equivalent.

Plan-only examples:

- ambiguous source mapping,
- missing source mapping,
- generated/vendor/runtime/framework-only frames,
- Spark or Flink distributed semantics,
- cache invalidation,
- database query behavior,
- retry, timeout, auth, persistence, or concurrency behavior.

If a weak model is unsure, it must output a plan and ask for focused tests or stronger review. It must not implement.

## Evidence Included

Final weak-model benchmark:

```text
evidence\weak-codex-extra100-spark-after-reset\full-extra100\benchmark-summary-1780922655.json
```

Key result:

```json
{
  "ok": true,
  "suite": "extra100",
  "case_count": 100,
  "rounds_run": 3,
  "required_streak": 2,
  "achieved_streak": 2
}
```

Round details:

- Round 1: 99/100. One raw Pyroscope JSON case was too conservative and returned `refuse`.
- Round 2: 100/100.
- Round 3: 100/100.

Local Pyroscope and analyzer validation:

```text
evidence\validation-after-extra100-v4-public\validation-summary.json
```

Important analyzer evidence:

```json
{
  "local_pyroscope_roundtrip": {
    "ok": true,
    "packet_chars": 7006,
    "audit_estimated_total_tokens": 7571,
    "top_count": 30,
    "observed": ["expensive_serializer", "regex_parser"]
  },
  "complex_profile_smoke": {
    "ok": true,
    "summary_top": 80,
    "mapped": 20,
    "packet_candidates": 6,
    "packet_plan_only": 8
  },
  "profile_budget_stress": {
    "ok": true,
    "noise_stacks": 300000,
    "folded_bytes": 12093156,
    "unique_frames": 3062,
    "indexed_files": 3009
  }
}
```

Weak hosted model availability probe:

```text
evidence\weak-hosted-probe-current\benchmark.stdout.log
```

This records that weaker preferred hosted candidates were blocked or unavailable, so the live pass used the weakest runnable Codex-hosted fallback.

## Re-run Validation

From the repository root:

```powershell
python .\skills\pyroscope-orchestrator\scripts\pyroscope_validation_suite.py --out-dir .\tmp-validation --local-pyroscope --pyroscope-url http://localhost:4040
```

Run prompt budget only:

```powershell
python .\skills\pyroscope-orchestrator\scripts\pyroscope_weak_model_benchmark.py --suite extra100 --budget-only --max-context-tokens 128000 --max-prompt-chars 20000
```

Run live weak-model validation when hosted quota is available:

```powershell
python .\skills\pyroscope-orchestrator\scripts\pyroscope_weak_model_benchmark.py --suite extra100 --candidate codex:gpt-5.3-codex-spark --out-dir .\tmp-weak-extra100 --max-rounds 3 --required-streak 2 --timeout-seconds 180 --max-context-tokens 128000 --max-prompt-chars 20000
```

For stronger Codex sanity only, not MiniMax proxy evidence:

```powershell
python .\skills\pyroscope-orchestrator\scripts\pyroscope_weak_model_benchmark.py --suite extra100 --candidate codex:gpt-5.4-mini --case-regex java_ext_ambiguous_pattern_compile --out-dir .\tmp-sanity --max-rounds 1 --required-streak 1
```

## How A Weak Agent Should Use This

Give the weak model only:

- the relevant `SKILL.md`,
- the deterministic helper output,
- the generated weak-model packet,
- the repository files that the packet uniquely maps to,
- the safety rules above.

Do not give it:

- full repository dumps,
- raw Pyroscope JSON,
- unbounded folded stacks,
- unrelated services,
- multiple profile windows at once,
- deployment instructions.

Ask it for key/value routing decisions or a short plan first. Only allow implementation after unique mapping and focused tests are present.

## Public Safety Note

This package is public validation evidence and reusable skill code. It should not contain production profile data, credentials, internal repository contents, or customer data. The included profiles and transcripts are synthetic validation artifacts.
