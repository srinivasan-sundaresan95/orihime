"""G8 — Perf Result Ingestion.

Parsers for three load-test result formats:
  - Gatling simulation.log
  - JMeter JTL XML
  - Simple JSON ({fqn, p50_ms, p99_ms, rps}[])

Each parser returns a list of dicts with keys:
    endpoint_fqn, p50_ms, p99_ms, rps, sample_time, source
(id and repo_id are added by the caller.)
"""
from __future__ import annotations

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
# Auto-detect dispatcher
# ---------------------------------------------------------------------------

def parse_perf_file(file_path: str) -> list[dict]:
    """Auto-detect format by file extension and parse accordingly.

    ``.log``  → Gatling simulation.log
    ``.xml``  → JMeter JTL XML
    ``.json`` → Simple JSON array

    Raises ValueError for unrecognised extensions.
    """
    ext = Path(file_path).suffix.lower()
    if ext == ".log":
        return parse_gatling(file_path)
    elif ext == ".xml":
        return parse_jmeter(file_path)
    elif ext == ".json":
        return parse_json(file_path)
    else:
        raise ValueError(
            f"Unrecognised perf file extension {ext!r}. "
            "Expected .log (Gatling), .xml (JMeter), or .json."
        )
