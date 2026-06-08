# Runtime Matrix

## .NET

Use Dockerfile-only configuration when the approved internal bundle contains the Pyroscope .NET native profiler files. Set profiler env vars in the final runtime stage. Refuse if the image is not Linux amd64 or if the bundle is unavailable in airgapped mode. The helper selects approved `glibc` or `musl` x86_64 profiler assets from the final runtime image, so it must refuse Windows, ARM, variable-platform, scratch, and unrecognized non-.NET final runtime images.

In `github-test` mode the helper may use the matching libc image:

```dockerfile
COPY --from=pyroscope/pyroscope-dotnet:0.14.2-glibc /Pyroscope.Profiler.Native.so /opt/pyroscope/dotnet/Pyroscope.Profiler.Native.so
COPY --from=pyroscope/pyroscope-dotnet:0.14.2-glibc /Pyroscope.Linux.ApiWrapper.x64.so /opt/pyroscope/dotnet/Pyroscope.Linux.ApiWrapper.x64.so
# Alpine/musl final stages use pyroscope/pyroscope-dotnet:0.14.2-musl.
```

## Java

Use a bundled `pyroscope.jar` and JVM options. Plain JVM images use `JAVA_TOOL_OPTIONS` and must append to an existing value instead of replacing it.

Spark and Flink are JVM subcases but should not also use `JAVA_TOOL_OPTIONS` when framework-level hooks are generated, because duplicate `-javaagent` options can break startup or double-profile the same process. For Spark, update `spark-defaults.conf` with `spark.driver.defaultJavaOptions` and `spark.executor.defaultJavaOptions` so the agent option is prepended to driver and executor JVMs. For Flink, update the image config with `env.java.default-opts.all` so the agent option is prepended to JobManager, TaskManager, and standalone job JVMs while preserving quoted YAML values. Do not guess production cluster settings outside the image.

In `github-test` mode the helper may use the Maven Central `io.pyroscope:agent` jar. In airgapped mode it must use `.pyroscope/java/pyroscope.jar`.

## Python

Best effort only. The bootstrap uses `sitecustomize.py` to call `pyroscope.configure(...)` from env when the interpreter imports `site`. This does not work for `python -S`, embedded Python, stripped images without package support, or entrypoints that bypass normal interpreter startup.

## Go

Dockerfile-only push-mode instrumentation is not supported for ordinary Go binaries because official push mode requires source code. Allow only:

- existing pprof endpoints when the selected build context contains both a pprof marker and an HTTP listener,
- an explicitly provided no-source collector in `.pyroscope/go/collector` inside the selected Docker build context,
- or analysis-only/blocker output.

Do not modify Go source to add `pyroscope.Start(...)`.

Official references used while authoring:

- https://grafana.com/docs/pyroscope/latest/configure-client/language-sdks/dotnet/
- https://grafana.com/docs/pyroscope/latest/configure-client/language-sdks/java/
- https://grafana.com/docs/pyroscope/latest/configure-client/language-sdks/python/
- https://grafana.com/docs/pyroscope/latest/configure-client/language-sdks/go_push/
