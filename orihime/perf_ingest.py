"""G8 — Perf Result Ingestion.

Parsers for four load-test result formats:
  - Gatling simulation.log
  - JMeter JTL XML
  - k6 summary JSON  (--summary-export result.json)
  - k6 CSV           (--out csv=result.csv)
  - Simple JSON ({fqn, p50_ms, p99_ms, rps}[])

Each parser returns a list of dicts with keys:
    endpoint_fqn, p50_ms, p99_ms, rps, sample_time, source
(id and repo_id are added by the caller.)
"""
from __future__ import annotations

import csv
import json
import math
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Percentile helper
# ---------------------------------------------------------------------------

def _percentile(values: list[float], pct: float) -> float:
    """Return the *pct*-th percentile of *values* (sorted ascending).

    Uses the nearest-rank method (same as Gatling/JMeter reports).
    *pct* must be in [0, 100].
    """
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    # nearest-rank: ceiling of (pct/100 * n), 1-based
    idx = max(0, math.ceil(pct / 100.0 * n) - 1)
    return sorted_vals[min(idx, n - 1)]


def _iso_from_ms(ts_ms: int) -> str:
    """Convert a Unix timestamp in milliseconds to an ISO-8601 string (UTC)."""
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Gatling parser
# ---------------------------------------------------------------------------

def parse_gatling(file_path: str) -> list[dict]:
    """Parse a Gatling simulation.log file.

    Lines of interest start with REQUEST:
        REQUEST\\t<userId>\\t\\t<requestName>\\t<startTime>\\t<endTime>\\t<status>\\t<message>

    Groups by requestName, computes p50/p99 from (endTime - startTime) in ms.
    rps = count / ((max_endTime - min_startTime) / 1000)
    source = "gatling"
    sample_time = ISO string derived from max_endTime.
    """
    # requestName -> list of elapsed ms
    elapsed_map: dict[str, list[float]] = {}
    start_map: dict[str, int] = {}   # requestName -> min startTime (ms)
    end_map: dict[str, int] = {}     # requestName -> max endTime (ms)

    with open(file_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.startswith("REQUEST"):
                continue
            parts = line.split("\t")
            # Expected columns: REQUEST, userId, (blank), requestName, startTime, endTime, status, message
            # Allow both 7 and 8 columns (message may be absent)
            if len(parts) < 7:
                continue
            request_name = parts[3].strip()
            try:
                start_ms = int(parts[4])
                end_ms = int(parts[5])
            except ValueError:
                continue
            if not request_name:
                continue

            elapsed = float(end_ms - start_ms)
            elapsed_map.setdefault(request_name, []).append(elapsed)
            if request_name not in start_map or start_ms < start_map[request_name]:
                start_map[request_name] = start_ms
            if request_name not in end_map or end_ms > end_map[request_name]:
                end_map[request_name] = end_ms

    samples: list[dict] = []
    for req_name, elapseds in elapsed_map.items():
        count = len(elapseds)
        duration_s = (end_map[req_name] - start_map[req_name]) / 1000.0
        rps = count / duration_s if duration_s > 0 else 0.0
        samples.append({
            "endpoint_fqn": req_name,
            "p50_ms": _percentile(elapseds, 50),
            "p99_ms": _percentile(elapseds, 99),
            "rps": rps,
            "sample_time": _iso_from_ms(end_map[req_name]),
            "source": "gatling",
        })
    return samples


# ---------------------------------------------------------------------------
# JMeter parser
# ---------------------------------------------------------------------------

def parse_jmeter(file_path: str) -> list[dict]:
    """Parse a JMeter JTL XML file.

    Root element: <testResults>
    Each <httpSample> or <sample> has:
        lb  — label (request name)
        t   — elapsed time in ms
        ts  — timestamp in ms (Unix epoch)

    Groups by lb, computes p50/p99 from t values.
    rps = count / ((max_ts + last_t - min_ts) / 1000)  (approximate wall time)
    source = "jmeter"
    sample_time = ISO string derived from max_ts.
    """
    tree = ET.parse(file_path)
    root = tree.getroot()

    elapsed_map: dict[str, list[float]] = {}
    ts_map_min: dict[str, int] = {}
    ts_map_max: dict[str, int] = {}
    last_t_map: dict[str, int] = {}  # elapsed at max ts (for wall-time approx)

    for elem in root.iter():
        if elem.tag not in ("httpSample", "sample"):
            continue
        lb = (elem.get("lb") or "").strip()
        t_str = elem.get("t", "0")
        ts_str = elem.get("ts", "0")
        if not lb:
            continue
        try:
            t = int(t_str)
            ts = int(ts_str)
        except ValueError:
            continue

        elapsed_map.setdefault(lb, []).append(float(t))
        if lb not in ts_map_min or ts < ts_map_min[lb]:
            ts_map_min[lb] = ts
        if lb not in ts_map_max or ts > ts_map_max[lb]:
            ts_map_max[lb] = ts
            last_t_map[lb] = t

    samples: list[dict] = []
    for lb, elapseds in elapsed_map.items():
        count = len(elapseds)
        max_ts = ts_map_max[lb]
        min_ts = ts_map_min[lb]
        last_t = last_t_map.get(lb, 0)
        # Wall time = from start of first sample to end of last sample
        wall_ms = (max_ts + last_t) - min_ts
        duration_s = wall_ms / 1000.0
        rps = count / duration_s if duration_s > 0 else 0.0
        samples.append({
            "endpoint_fqn": lb,
            "p50_ms": _percentile(elapseds, 50),
            "p99_ms": _percentile(elapseds, 99),
            "rps": rps,
            "sample_time": _iso_from_ms(max_ts),
            "source": "jmeter",
        })
    return samples


# ---------------------------------------------------------------------------
# JSON parser
# ---------------------------------------------------------------------------

def parse_json(file_path: str) -> list[dict]:
    """Parse a simple JSON file.

    Expected format: list of objects with keys:
        fqn      — endpoint FQN (maps directly to endpoint_fqn)
        p50_ms   — p50 latency in ms
        p99_ms   — p99 latency in ms
        rps      — requests per second

    Optional keys:
        sample_time — ISO timestamp string (defaults to current UTC time)

    source = "json"
    """
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    with open(file_path, encoding="utf-8") as fh:
        data = json.load(fh)

    samples: list[dict] = []
    for item in data:
        fqn = item.get("fqn") or item.get("endpoint_fqn", "")
        if not fqn:
            continue
        samples.append({
            "endpoint_fqn": fqn,
            "p50_ms": float(item.get("p50_ms", 0)),
            "p99_ms": float(item.get("p99_ms", 0)),
            "rps": float(item.get("rps", 0)),
            "sample_time": item.get("sample_time", now_iso),
            "source": "json",
        })
    return samples


# ---------------------------------------------------------------------------
# k6 summary JSON parser  (--summary-export result.json)
# ---------------------------------------------------------------------------

def parse_k6_summary(file_path: str) -> list[dict]:
    """Parse a k6 summary export JSON file (``k6 run --summary-export result.json``).

    k6 groups HTTP metrics by URL tag under the top-level ``metrics`` key.
    Each group named ``http_req_duration{...}`` contains ``p(50)`` and ``p(99)``
    sub-values (in ms) and ``http_reqs{...}`` provides the request rate.

    The URL is extracted from the ``url`` tag in the metric group name.
    If no URL tag is present the plain metric name is used as the endpoint key
    so callers can still match against it.

    source = "k6_summary"
    sample_time = top-level ``state.testRunDurationMs`` converted to ISO, or now().
    """
    with open(file_path, encoding="utf-8") as fh:
        data = json.load(fh)

    if not isinstance(data, dict) or "metrics" not in data:
        raise ValueError("Not a k6 summary export — expected top-level 'metrics' key.")

    metrics: dict = data["metrics"]

    # Derive sample_time from state block if present
    state = data.get("state", {})
    test_run_ms = state.get("testRunDurationMs")
    if test_run_ms:
        sample_time = _iso_from_ms(int(test_run_ms))
    else:
        sample_time = datetime.now(tz=timezone.utc).isoformat()

    # Build a map: url_tag -> {p50, p99, count, rate}
    # Metric names look like:
    #   "http_req_duration"               (untagged — single scenario)
    #   "http_req_duration{url:GET /foo}" (tagged — per-URL breakdown)
    # Rate comes from matching "http_reqs" or "http_reqs{url:...}" entry.

    def _extract_tag(metric_name: str, tag: str) -> str | None:
        """Return the value of *tag* from a k6 metric name like 'name{tag:val,...}'."""
        brace = metric_name.find("{")
        if brace == -1:
            return None
        inner = metric_name[brace + 1: -1]  # strip { }
        for part in inner.split(","):
            k, _, v = part.partition(":")
            if k.strip() == tag:
                return v.strip()
        return None

    # Collect duration entries
    duration_entries: dict[str, dict] = {}  # group_key -> {p50, p99}
    rate_entries: dict[str, float] = {}     # group_key -> rps

    for metric_name, metric_body in metrics.items():
        base = metric_name.split("{")[0]
        if base == "http_req_duration":
            url = _extract_tag(metric_name, "url") or metric_name
            values = metric_body.get("values", {})
            p50 = float(values.get("p(50)", 0.0))
            p99 = float(values.get("p(99)", 0.0))
            duration_entries[url] = {"p50_ms": p50, "p99_ms": p99}
        elif base == "http_reqs":
            url = _extract_tag(metric_name, "url") or metric_name
            values = metric_body.get("values", {})
            # k6 exposes rate as requests/second in the summary
            rate_entries[url] = float(values.get("rate", 0.0))

    if not duration_entries:
        raise ValueError(
            "No 'http_req_duration' metrics found in k6 summary. "
            "Run k6 with --summary-export and ensure HTTP checks are used."
        )

    samples: list[dict] = []
    for url, durations in duration_entries.items():
        # For untagged single-scenario runs the key IS the metric name string;
        # use it as-is — callers can map it to an endpoint FQN.
        rps = rate_entries.get(url, 0.0)
        samples.append({
            "endpoint_fqn": url,
            "p50_ms": durations["p50_ms"],
            "p99_ms": durations["p99_ms"],
            "rps": rps,
            "sample_time": sample_time,
            "source": "k6_summary",
        })
    return samples


# ---------------------------------------------------------------------------
# k6 CSV parser  (--out csv=result.csv)
# ---------------------------------------------------------------------------

def parse_k6_csv(file_path: str) -> list[dict]:
    """Parse a k6 raw metrics CSV file (``k6 run --out csv=result.csv``).

    k6 CSV columns:
        metric_name, timestamp, metric_value, [tags...]

    Only rows where metric_name == "http_req_duration" are used.
    The ``url`` tag (if present) is used as the endpoint key; otherwise
    ``name`` tag is tried, then the raw metric_name.

    Groups by endpoint key, computes p50/p99 from metric_value (already in ms),
    rps = count / wall_duration_seconds.
    sample_time = ISO string from max timestamp.

    source = "k6_csv"
    """
    elapsed_map: dict[str, list[float]] = {}
    ts_min: dict[str, int] = {}
    ts_max: dict[str, int] = {}

    with open(file_path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if row.get("metric_name") != "http_req_duration":
                continue
            try:
                value = float(row["metric_value"])
                # timestamp column is Unix seconds (float) in k6 CSV
                ts_s = float(row["timestamp"])
                ts_ms = int(ts_s * 1000)
            except (KeyError, ValueError):
                continue

            # Resolve endpoint key from tags: prefer 'url', fallback 'name'
            url = row.get("url") or row.get("name") or "http_req_duration"
            url = url.strip()

            elapsed_map.setdefault(url, []).append(value)
            if url not in ts_min or ts_ms < ts_min[url]:
                ts_min[url] = ts_ms
            if url not in ts_max or ts_ms > ts_max[url]:
                ts_max[url] = ts_ms

    if not elapsed_map:
        raise ValueError(
            "No 'http_req_duration' rows found in k6 CSV. "
            "Ensure the file was generated with '--out csv=result.csv'."
        )

    samples: list[dict] = []
    for url, elapseds in elapsed_map.items():
        count = len(elapseds)
        duration_s = (ts_max[url] - ts_min[url]) / 1000.0
        rps = count / duration_s if duration_s > 0 else 0.0
        samples.append({
            "endpoint_fqn": url,
            "p50_ms": _percentile(elapseds, 50),
            "p99_ms": _percentile(elapseds, 99),
            "rps": rps,
            "sample_time": _iso_from_ms(ts_max[url]),
            "source": "k6_csv",
        })
    return samples


# ---------------------------------------------------------------------------
# Auto-detect dispatcher
# ---------------------------------------------------------------------------

def parse_perf_file(file_path: str) -> list[dict]:
    """Auto-detect format by file extension and content, then parse.

    ``.log``  → Gatling simulation.log
    ``.xml``  → JMeter JTL XML
    ``.csv``  → k6 CSV (--out csv=result.csv)
    ``.json`` → k6 summary export if top-level key "metrics" present,
                otherwise simple JSON array

    Raises ValueError for unrecognised extensions.
    """
    ext = Path(file_path).suffix.lower()
    if ext == ".log":
        return parse_gatling(file_path)
    elif ext == ".xml":
        return parse_jmeter(file_path)
    elif ext == ".csv":
        return parse_k6_csv(file_path)
    elif ext == ".json":
        # Peek at the file to distinguish k6 summary from simple JSON array
        with open(file_path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "metrics" in data:
            return parse_k6_summary(file_path)
        return parse_json(file_path)
    else:
        raise ValueError(
            f"Unrecognised perf file extension {ext!r}. "
            "Expected .log (Gatling), .xml (JMeter), .csv (k6), or .json."
        )
