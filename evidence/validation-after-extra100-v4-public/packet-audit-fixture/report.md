# Pyroscope Profile Report

Service: `fixture-service`
Window: `synthetic`

## Eligible Hotspots

| Function | Self % | Total % | Mapping | Source matches | Recommendation |
| --- | ---: | ---: | --- | --- | --- |
| `regex_parser` | 12.5 | 18.0 | unique | src/core/regex_parser.py | Check for repeated regex compilation; move constant regex construction out of the hot path if behavior is unchanged. |
| `generated.vendor.Frame` | 9.0 | 11.0 | unique | vendor/generated.py | Plan only; do not auto-edit generated or vendor code. |
| `ambiguous_serializer` | 8.0 | 10.0 | ambiguous | src/a.py, src/b.py | Plan only; do not auto-edit until source mapping is resolved. |
