import os


def _configure_pyroscope():
    server_address = os.environ.get("PYROSCOPE_SERVER_ADDRESS")
    app_name = os.environ.get("PYROSCOPE_APPLICATION_NAME")
    if not server_address or not app_name:
        return

    try:
        import pyroscope
    except Exception as exc:
        print(f"pyroscope bootstrap disabled: failed to import pyroscope: {exc}", flush=True)
        return

    tags = {}
    raw_labels = os.environ.get("PYROSCOPE_LABELS", "")
    for item in raw_labels.split(","):
        if "=" in item:
            key, value = item.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key and value and key != "service_name":
                tags[key] = value

    try:
        pyroscope.configure(
            application_name=app_name,
            server_address=server_address,
            sample_rate=100,
            oncpu=True,
            enable_logging=True,
            tags=tags,
        )
        print(f"pyroscope bootstrap enabled for {app_name}", flush=True)
    except Exception as exc:
        print(f"pyroscope bootstrap disabled: configure failed: {exc}", flush=True)


_configure_pyroscope()
