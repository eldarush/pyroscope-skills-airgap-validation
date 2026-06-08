# Airgapped Bundle Contract

The skill must assume no internet access in the target environment. Approved artifacts come from DevOps-controlled internal storage.

Expected layout:

```text
pyroscope/
  java/pyroscope.jar
  dotnet/Pyroscope.Profiler.Native.so
  dotnet/Pyroscope.Linux.ApiWrapper.x64.so
  dotnet/glibc/Pyroscope.Profiler.Native.so
  dotnet/glibc/Pyroscope.Linux.ApiWrapper.x64.so
  dotnet/musl/Pyroscope.Profiler.Native.so
  dotnet/musl/Pyroscope.Linux.ApiWrapper.x64.so
  python/wheels/pyroscope_io-*.whl
  python/bootstrap/sitecustomize.py
  go/collector/
  checksums.sha256
  versions.json
```

Rules:

- Verify checksums when available.
- Never download from public internet in `airgap` mode.
- Never write credentials into repo files.
- For .NET, direct `dotnet/*.so` is accepted as legacy glibc layout; `dotnet/musl/*.so` is required for Alpine/musl final stages.
- In `github-test` mode, public package installation is allowed only for disposable test repos.
- Generated Dockerfiles must make the artifact source obvious.

Official local Pyroscope Docker reference used while authoring:

- https://grafana.com/docs/pyroscope/latest/get-started/
