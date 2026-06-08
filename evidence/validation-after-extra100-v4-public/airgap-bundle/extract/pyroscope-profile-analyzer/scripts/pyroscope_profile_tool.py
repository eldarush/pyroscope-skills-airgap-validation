#!/usr/bin/env python3
import argparse
import ast
import datetime as dt
import fnmatch
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_PROFILE_TYPE = "process_cpu:cpu:nanoseconds:cpu:nanoseconds"
DEFAULT_SOURCE_ROOTS = ["src", "app", "."]
DEFAULT_SOURCE_EXCLUDES = {"test", "tests", "generated", "vendor", "node_modules", "bin", "obj", "target", ".git"}


def now_ms():
    return int(time.time() * 1000)


def parse_window(value):
    if value.endswith("m"):
        return int(value[:-1]) * 60 * 1000
    if value.endswith("h"):
        return int(value[:-1]) * 60 * 60 * 1000
    return int(value) * 1000


def post_json(base_url, path, body, tenant=None):
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if tenant:
        headers["X-Scope-OrgID"] = tenant
    req = Request(base_url.rstrip("/") + path, data=data, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=20) as resp:
            raw = resp.read()
            if not raw:
                return {}
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return {"raw": raw.decode("utf-8", errors="replace")}
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Pyroscope HTTP {exc.code} at {path}: {text}")
    except URLError as exc:
        raise SystemExit(f"Cannot reach Pyroscope at {base_url}: {exc}")


def discover(args):
    end = now_ms()
    start = end - parse_window(args.window)
    common = {"start": start, "end": end}
    labels = post_json(args.url, "/querier.v1.QuerierService/LabelNames", common, args.tenant)
    services = post_json(args.url, "/querier.v1.QuerierService/LabelValues", {**common, "name": "service_name"}, args.tenant)
    profile_types = post_json(args.url, "/querier.v1.QuerierService/ProfileTypes", common, args.tenant)
    print(json.dumps({"labels": labels, "service_name_values": services, "profile_types": profile_types}, indent=2))


def extract_nodes(value):
    if isinstance(value, dict) and isinstance(value.get("flamegraph"), dict):
        return extract_flamegraph(value["flamegraph"])
    nodes = []
    if isinstance(value, dict):
        name = value.get("name") or value.get("function") or value.get("location") or value.get("label")
        self_value = value.get("self") or value.get("selfValue") or value.get("self_value") or value.get("selfTicks")
        total_value = value.get("total") or value.get("totalValue") or value.get("total_value") or value.get("cum") or value.get("cumulative")
        if name and (self_value is not None or total_value is not None):
            nodes.append({"name": str(name), "self": number(self_value), "total": number(total_value)})
        for child in value.values():
            nodes.extend(extract_nodes(child))
    elif isinstance(value, list):
        for item in value:
            nodes.extend(extract_nodes(item))
    return nodes


def extract_flamegraph(flamegraph):
    names = flamegraph.get("names") or []
    levels = flamegraph.get("levels") or []
    nodes = []
    for level in levels:
        values = level.get("values") if isinstance(level, dict) else None
        if not isinstance(values, list):
            continue
        for i in range(0, len(values) - 3, 4):
            try:
                total = float(values[i + 1])
                self_value = float(values[i + 2])
                name_index = int(values[i + 3])
            except (TypeError, ValueError):
                continue
            name = names[name_index] if 0 <= name_index < len(names) else f"name[{name_index}]"
            nodes.append({"name": name, "self": self_value, "total": total})
    return nodes


def number(value):
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def internal_frame(name):
    lowered = name.lower()
    if lowered in {"total", "other", "<module>", "root", "dotnet", "jvm", "go", "spark", "flink", "python"}:
        return True
    if lowered.startswith("."):
        return True
    if lowered.startswith("thread.") or lowered.startswith("_"):
        return True
    if lowered.startswith("system.") or lowered.startswith("microsoft."):
        return True
    if lowered.startswith("java.") or lowered.startswith("javax.") or lowered.startswith("sun."):
        return True
    if lowered.startswith("java/") or lowered.startswith("javax/") or lowered.startswith("jdk/") or lowered.startswith("sun/"):
        return True
    if lowered.startswith("runtime.") or lowered.startswith("reflect."):
        return True
    if lowered.startswith("lib") or ".so." in lowered or lowered.endswith(".so"):
        return True
    return False


def source_candidates(function_name):
    raw_parts = re.split(r"[/.$:<>]+", function_name)
    ignored_parts = {
        "api",
        "app",
        "com",
        "controller",
        "controllers",
        "example",
        "go",
        "io",
        "net",
        "org",
        "service",
        "services",
    }
    candidates = []
    for part in raw_parts:
        part = part.strip()
        lowered = part.lower()
        if len(part) >= 3 and not part.startswith("d__") and lowered not in ignored_parts and part not in {"MoveNext", "main"}:
            candidates.append(part)
    if function_name.startswith("Program."):
        candidates.append("Program")
    tail = function_name.split("/")[-1].split(".")[-1]
    if len(tail) >= 3:
        candidates.append(tail)
    seen = set()
    result = []
    for candidate in candidates:
        lowered = candidate.lower()
        if lowered not in seen:
            seen.add(lowered)
            result.append(candidate)
    return result


def parse_inline_list(value):
    value = value.strip()
    if not value.startswith("[") or not value.endswith("]"):
        return None
    try:
        parsed = ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return None
    if not isinstance(parsed, list):
        return None
    return [str(item) for item in parsed if str(item).strip()]


def metadata_source_config(repo):
    metadata = repo / "pyroscope-agent.yaml"
    roots = list(DEFAULT_SOURCE_ROOTS)
    excludes = set(DEFAULT_SOURCE_EXCLUDES)
    if not metadata.exists():
        return roots, excludes, False
    text = metadata.read_text(encoding="utf-8", errors="ignore")
    roots_match = re.search(r"(?m)^\s*roots\s*:\s*(\[[^\]]*\])\s*$", text)
    exclude_match = re.search(r"(?m)^\s*exclude\s*:\s*(\[[^\]]*\])\s*$", text)
    parsed_roots = parse_inline_list(roots_match.group(1)) if roots_match else None
    parsed_excludes = parse_inline_list(exclude_match.group(1)) if exclude_match else None
    if parsed_roots:
        roots = parsed_roots
    if parsed_excludes:
        excludes.update(item.lower() for item in parsed_excludes)
    return roots, excludes, True


def safe_root(repo, root):
    path = (repo / root).resolve()
    try:
        path.relative_to(repo)
    except ValueError:
        return None
    if path.exists() and path.is_dir():
        return path
    return None


def excluded_path(path, repo, excludes):
    relative = path.relative_to(repo)
    parts = [part.lower() for part in relative.parts]
    rel_text = relative.as_posix().lower()
    for item in excludes:
        lowered = item.lower()
        if lowered in parts or fnmatch.fnmatch(rel_text, lowered):
            return True
    return False


def collect_source_files(repo, roots, excludes, extensions, max_files):
    files = []
    seen = set()
    truncated = False
    resolved_roots = [root for root in (safe_root(repo, item) for item in roots) if root]
    if not resolved_roots:
        resolved_roots = [repo]
    for root in resolved_roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            if excluded_path(path, repo, excludes):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(path)
            if len(files) >= max_files:
                truncated = True
                return files, truncated, resolved_roots
    return files, truncated, resolved_roots


def build_source_index(repo, args):
    roots, excludes, metadata_found = metadata_source_config(repo)
    extensions = {".cs", ".java", ".py", ".go", ".kt", ".scala", ".fs", ".js", ".ts"}
    files, truncated, resolved_roots = collect_source_files(repo, roots, excludes, extensions, args.max_files)
    indexed = []
    skipped_large = 0
    unreadable = 0
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            unreadable += 1
            continue
        if size > args.max_file_bytes:
            skipped_large += 1
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            unreadable += 1
            continue
        indexed.append({"path": path, "stem": path.stem.lower(), "text": text})
    scan = {
        "metadata_found": metadata_found,
        "roots": roots,
        "resolved_roots": [str(path.relative_to(repo)) if path != repo else "." for path in resolved_roots],
        "exclude": sorted(excludes),
        "candidate_files": len(files),
        "indexed_files": len(indexed),
        "skipped_large_files": skipped_large,
        "unreadable_files": unreadable,
        "index_truncated": truncated,
        "max_files": args.max_files,
        "max_file_bytes": args.max_file_bytes,
    }
    return indexed, scan


def summarize(args):
    end = now_ms()
    start = end - parse_window(args.window)
    selector = args.selector or f'{{service_name="{args.service}"}}'
    body = {
        "start": start,
        "end": end,
        "labelSelector": selector,
        "profileTypeID": args.profile_type,
        "maxNodes": args.max_nodes,
        "format": "PROFILE_FORMAT_FLAMEGRAPH",
    }
    raw = post_json(args.url, "/querier.v1.QuerierService/SelectMergeStacktraces", body, args.tenant)
    nodes = extract_nodes(raw)
    total = max((n["total"] for n in nodes), default=0.0) or sum(n["self"] for n in nodes) or 1.0
    rows = []
    for n in nodes:
        self_pct = 100 * n["self"] / total if total else 0
        total_pct = 100 * n["total"] / total if total else 0
        eligible = not internal_frame(n["name"]) and (self_pct >= args.self_threshold or total_pct >= args.total_threshold)
        rows.append(
            {
                "function": n["name"],
                "self": n["self"],
                "total": n["total"],
                "self_pct": round(self_pct, 2),
                "total_pct": round(total_pct, 2),
                "eligible": eligible,
            }
        )
    rows.sort(key=lambda r: (r["total"], r["self"]), reverse=True)
    result = {
        "service": args.service,
        "selector": selector,
        "profile_type": args.profile_type,
        "window": args.window,
        "top": rows[: args.limit],
        "raw_shape": list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__,
    }
    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


def summarize_folded(args):
    profile = Path(args.file)
    if not profile.exists():
        raise SystemExit(f"Missing folded profile file: {profile}")
    totals = {}
    self_values = {}
    total_samples = 0.0
    unreadable = 0
    with profile.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                stack, value_text = stripped.rsplit(" ", 1)
                value = float(value_text)
            except ValueError:
                unreadable += 1
                continue
            frames = [frame.strip() for frame in stack.split(";") if frame.strip()]
            if not frames:
                unreadable += 1
                continue
            total_samples += value
            for frame in frames:
                totals[frame] = totals.get(frame, 0.0) + value
            self_values[frames[-1]] = self_values.get(frames[-1], 0.0) + value
    denominator = total_samples or 1.0
    rows = []
    for function, total in totals.items():
        self_value = self_values.get(function, 0.0)
        self_pct = 100 * self_value / denominator
        total_pct = 100 * total / denominator
        eligible = not internal_frame(function) and (
            self_pct >= args.self_threshold or total_pct >= args.total_threshold
        )
        rows.append(
            {
                "function": function,
                "self": self_value,
                "total": total,
                "self_pct": round(self_pct, 2),
                "total_pct": round(total_pct, 2),
                "eligible": eligible,
            }
        )
    rows.sort(key=lambda r: (r["total"], r["self"]), reverse=True)
    result = {
        "service": args.service,
        "selector": f'{{service_name="{args.service}"}}',
        "profile_type": args.profile_type,
        "window": args.window,
        "top": rows[: args.limit],
        "raw_shape": "folded",
        "folded": {
            "file": str(profile),
            "total_samples": total_samples,
            "unique_frames": len(rows),
            "unreadable_lines": unreadable,
        },
    }
    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


def map_source(args):
    repo = Path(args.repo).resolve()
    summary = json.loads(Path(args.summary).read_text(encoding="utf-8"))
    source_index, scan = build_source_index(repo, args)
    mapped = []
    for row in summary.get("top", []):
        if not row.get("eligible"):
            continue
        candidates = source_candidates(row["function"])
        if not candidates:
            continue
        matches = []
        for item in source_index:
            path = item["path"]
            filename_hit = any(item["stem"] == c.lower() for c in candidates)
            content_hit = any(re.search(rf"\b{re.escape(candidate)}\b", item["text"]) for candidate in candidates)
            if filename_hit or content_hit:
                matches.append(str(path.relative_to(repo)))
                if len(matches) >= args.max_matches_per_frame:
                    break
        if len(matches) == 1 and not scan["index_truncated"]:
            mapping = "unique"
        elif len(matches) == 1:
            mapping = "partial"
        else:
            mapping = "ambiguous" if matches else "missing"
        mapped.append({**row, "source_matches": matches, "mapping": mapping})
    result = {"mapped": mapped, "scan": scan}
    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


def report(args):
    mapping = json.loads(Path(args.mapping).read_text(encoding="utf-8"))
    lines = [
        "# Pyroscope Profile Report",
        "",
        f"Service: `{args.service}`",
        f"Window: `{args.window}`",
        "",
        "## Eligible Hotspots",
        "",
        "| Function | Self % | Total % | Mapping | Source matches | Recommendation |",
        "| --- | ---: | ---: | --- | --- | --- |",
    ]
    rows = mapping.get("mapped", [])
    if not rows:
        lines.append("| `none` | 0 | 0 | none | none | No eligible source-mapped hotspots. |")
    for row in rows:
        matches = ", ".join(row.get("source_matches") or [])
        lines.append(
            f"| `{row.get('function')}` | {row.get('self_pct')} | {row.get('total_pct')} | {row.get('mapping')} | {matches or 'none'} | {recommendation(row)} |"
        )
    scan = mapping.get("scan") or {}
    if scan:
        lines.extend(
            [
                "",
                "## Mapping Evidence",
                "",
                f"- Source metadata found: `{scan.get('metadata_found')}`",
                f"- Source roots: `{', '.join(scan.get('resolved_roots') or []) or 'none'}`",
                f"- Indexed files: `{scan.get('indexed_files')}` of `{scan.get('candidate_files')}` candidates",
                f"- Index truncated: `{scan.get('index_truncated')}`",
                f"- Skipped large/unreadable files: `{scan.get('skipped_large_files')}` / `{scan.get('unreadable_files')}`",
            ]
        )
    lines.extend(["", "## Implementation Gate", ""])
    for row in rows:
        fn = row.get("function")
        if row.get("mapping") == "unique":
            lines.append(f"- `{fn}` may be considered for `implement-safe` only after tests cover the path and the edit is local/mechanical.")
        else:
            lines.append(f"- `{fn}` is not safe to implement automatically because source mapping is {row.get('mapping')}.")
    lines.extend(
        [
            "",
            "## Required Validation",
            "",
            "- Run the narrowest meaningful tests before and after any code change.",
            "- Keep image instrumentation and code optimization in separate branches/PRs.",
            "- Confirm improvement with a later Pyroscope window after deployment.",
        ]
    )
    text = "\n".join(lines) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text)


def recommendation(row):
    function = str(row.get("function", ""))
    mapping = row.get("mapping")
    if mapping != "unique":
        return "Plan only; do not auto-edit until source mapping is resolved."
    lowered = function.lower()
    if "re.compile" in lowered or "regex" in lowered:
        return "Check for repeated regex compilation; move constant regex construction out of the hot path if behavior is unchanged."
    if "json.dumps" in lowered or "serializer" in lowered:
        return "Check repeated serialization/options allocation; reuse immutable options or avoid duplicate serialization if tests prove equivalence."
    if "allocation" in lowered or "list.append" in lowered:
        return "Check avoidable allocation in the loop; pre-size/reuse local collections only when output is identical."
    if lowered.endswith(".main") or lowered == "main":
        return "Entry frame; inspect mapped child hotspots before editing."
    return "Candidate for local mechanical optimization only with focused tests and behavior-preservation proof."


def ingest_folded(args):
    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"Missing folded profile file: {path}")
    end = int(time.time())
    # Synthetic folded profiles are used as recent verification seeds. Keep
    # them away from query-window edges so follow-up summarize calls stay stable.
    duration = min(max(int(args.seconds), 1), 60)
    start = end - max(duration - 2, 1)
    params = {
        "name": args.service,
        "from": start,
        "until": end,
        "format": "folded",
        "sampleRate": "100",
        "spyName": "codex-seed",
        "units": "samples",
        "aggregationType": "sum",
    }
    url = args.url.rstrip("/") + "/ingest?" + urlencode(params)
    headers = {"Content-Type": "binary/octet-stream"}
    if args.tenant:
        headers["X-Scope-OrgID"] = args.tenant
    req = Request(url, data=path.read_bytes(), headers=headers, method="POST")
    try:
        with urlopen(req, timeout=20) as resp:
            print(json.dumps({"status": resp.status, "response": resp.read().decode("utf-8", errors="replace")}, indent=2))
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Pyroscope HTTP {exc.code} at /ingest: {text}")
    except URLError as exc:
        raise SystemExit(f"Cannot reach Pyroscope at {args.url}: {exc}")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(required=True)
    d = sub.add_parser("discover")
    d.add_argument("--url", default=os.environ.get("PYROSCOPE_URL", "http://localhost:4040"))
    d.add_argument("--tenant")
    d.add_argument("--window", default="1h")
    d.set_defaults(func=discover)

    s = sub.add_parser("summarize")
    s.add_argument("--url", default=os.environ.get("PYROSCOPE_URL", "http://localhost:4040"))
    s.add_argument("--tenant")
    s.add_argument("--service", required=True)
    s.add_argument("--selector")
    s.add_argument("--profile-type", default=DEFAULT_PROFILE_TYPE)
    s.add_argument("--window", default="1h")
    s.add_argument("--max-nodes", type=int, default=200)
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--self-threshold", type=float, default=5.0)
    s.add_argument("--total-threshold", type=float, default=10.0)
    s.add_argument("--output")
    s.set_defaults(func=summarize)

    sf = sub.add_parser("summarize-folded")
    sf.add_argument("--file", required=True)
    sf.add_argument("--service", required=True)
    sf.add_argument("--profile-type", default=DEFAULT_PROFILE_TYPE)
    sf.add_argument("--window", default="synthetic")
    sf.add_argument("--limit", type=int, default=60)
    sf.add_argument("--self-threshold", type=float, default=5.0)
    sf.add_argument("--total-threshold", type=float, default=10.0)
    sf.add_argument("--output")
    sf.set_defaults(func=summarize_folded)

    m = sub.add_parser("map-source")
    m.add_argument("--repo", default=".")
    m.add_argument("--summary", required=True)
    m.add_argument("--max-files", type=int, default=20000)
    m.add_argument("--max-file-bytes", type=int, default=500000)
    m.add_argument("--max-matches-per-frame", type=int, default=6)
    m.add_argument("--output")
    m.set_defaults(func=map_source)

    r = sub.add_parser("report")
    r.add_argument("--service", required=True)
    r.add_argument("--window", default="1h")
    r.add_argument("--mapping", required=True)
    r.add_argument("--output")
    r.set_defaults(func=report)

    i = sub.add_parser("ingest-folded")
    i.add_argument("--url", default=os.environ.get("PYROSCOPE_URL", "http://localhost:4040"))
    i.add_argument("--tenant")
    i.add_argument("--service", required=True)
    i.add_argument("--file", required=True)
    i.add_argument("--seconds", default=60)
    i.set_defaults(func=ingest_folded)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
