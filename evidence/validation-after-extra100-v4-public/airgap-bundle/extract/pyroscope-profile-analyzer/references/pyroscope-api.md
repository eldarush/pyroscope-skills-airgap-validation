# Pyroscope API Notes

Use Pyroscope HTTP APIs directly in airgapped mode. Do not require profilecli, pprof tools, or other CLIs.

Useful endpoints:

- `POST /querier.v1.QuerierService/LabelNames`
- `POST /querier.v1.QuerierService/LabelValues`
- `POST /querier.v1.QuerierService/ProfileTypes`
- `POST /querier.v1.QuerierService/SelectMergeStacktraces`
- `POST /querier.v1.QuerierService/SelectMergeProfile`
- `POST /push.v1.PusherService/Push`
- legacy `POST /ingest`

Multi-tenant Pyroscope may require `X-Scope-OrgID`.

Always bound queries by time window and profile type. Do not ask weak models to read raw profile JSON unless summarized first.

Official references used while authoring:

- https://grafana.com/docs/pyroscope/latest/reference-server-api/
- https://grafana.com/docs/pyroscope/latest/view-and-analyze-profile-data/profile-cli/
