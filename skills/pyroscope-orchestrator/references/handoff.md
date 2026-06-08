# Handoff Contract

Use separate branches and PRs:

- Image PR: Dockerfile/CI/metadata/bootstrap only.
- Optimization PR: code/tests only after profile analysis.

Store evidence in the PR/MR body:

- commands run,
- image tag,
- Pyroscope service name,
- profile query window,
- hotspot table path,
- tests run,
- known blockers.

For weak or airgapped agents, generate a packet before asking for reasoning:

```text
scripts/pyroscope_weak_model_packet.py --report <report.md> --repo <repo> --task analyze --pretty --output <packet.json>
```

The packet is deterministic context only. It must not run a model, query Pyroscope, build Docker images, or edit source.

If moving to airgapped GitLab, replace GitHub test adapter with GitLab REST API and internal Artifactory bundle paths. Do not change the safety gates.
