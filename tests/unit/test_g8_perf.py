"""Unit + integration tests for G8 — Perf Result Ingestion + Hotspot Correlation.

Tests 1–5: parser unit tests (no DB required).
Test 6:    integration — ingest_perf_results returns dict with "ingested" key.
Test 7:    integration — find_hotspots returns a list.
Test 8:    integration — estimate_capacity returns a list.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from orihime.perf_ingest import (
    parse_gatling,
    parse_jmeter,
    parse_json,
    parse_perf_file,
    _percentile,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_tmp(suffix: str, content: str) -> str:
    """Write *content* to a temp file with the given *suffix*, return path."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


# ---------------------------------------------------------------------------
# Test 1: parse_gatling on a small inline simulation.log string
# ---------------------------------------------------------------------------

_GATLING_LOG = """\
RUN\tsimulation\tstart\t1700000000000\t
REQUEST\tuser1\t\tGetItems\t1700000000100\t1700000000250\tOK\t
REQUEST\tuser2\t\tGetItems\t1700000000200\t1700000000400\tOK\t
REQUEST\tuser3\t\tGetItems\t1700000000300\t1700000000600\tOK\t
REQUEST\tuser4\t\tPostOrder\t1700000000100\t1700000001100\tOK\t
END
"""


def test_parse_gatling_basic():
    path = _write_tmp(".log", _GATLING_LOG)
    try:
        samples = parse_gatling(path)
        assert isinstance(samples, list)
        names = {s["endpoint_fqn"] for s in samples}
        assert "GetItems" in names
        assert "PostOrder" in names
        for s in samples:
            assert s["source"] == "gatling"
            assert s["p50_ms"] >= 0
            assert s["p99_ms"] >= s["p50_ms"]
            assert s["rps"] > 0
            assert s["sample_time"]
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Test 2: parse_jmeter on a small inline XML string
# ---------------------------------------------------------------------------

_JMETER_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<testResults version="1.2">
  <httpSample t="120" ts="1700000000000" s="true" lb="GetUsers" rc="200" rm="OK" tn="Thread-1" dt="text" de="utf-8" by="1024" sby="320" ng="1" na="1"/>
  <httpSample t="200" ts="1700000000150" s="true" lb="GetUsers" rc="200" rm="OK" tn="Thread-2" dt="text" de="utf-8" by="1024" sby="320" ng="1" na="1"/>
  <httpSample t="90" ts="1700000000300" s="true" lb="GetUsers" rc="200" rm="OK" tn="Thread-3" dt="text" de="utf-8" by="1024" sby="320" ng="1" na="1"/>
  <httpSample t="500" ts="1700000000100" s="true" lb="CreateOrder" rc="201" rm="Created" tn="Thread-4" dt="text" de="utf-8" by="256" sby="100" ng="1" na="1"/>
</testResults>
"""


def test_parse_jmeter_basic():
    path = _write_tmp(".xml", _JMETER_XML)
    try:
        samples = parse_jmeter(path)
        assert isinstance(samples, list)
        names = {s["endpoint_fqn"] for s in samples}
        assert "GetUsers" in names
        assert "CreateOrder" in names
        for s in samples:
            assert s["source"] == "jmeter"
            assert s["p50_ms"] >= 0
            assert s["p99_ms"] >= s["p50_ms"]
            assert s["sample_time"]
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Test 3: parse_json on a simple JSON array
# ---------------------------------------------------------------------------

_JSON_DATA = [
    {"fqn": "com.example.ItemController.getItems", "p50_ms": 50.0, "p99_ms": 200.0, "rps": 100.0},
    {"fqn": "com.example.OrderController.createOrder", "p50_ms": 120.0, "p99_ms": 450.0, "rps": 20.0},
]


def test_parse_json_basic():
    path = _write_tmp(".json", json.dumps(_JSON_DATA))
    try:
        samples = parse_json(path)
        assert len(samples) == 2
        fqns = {s["endpoint_fqn"] for s in samples}
        assert "com.example.ItemController.getItems" in fqns
        assert "com.example.OrderController.createOrder" in fqns
        for s in samples:
            assert s["source"] == "json"
            assert s["p99_ms"] >= s["p50_ms"]
            assert s["rps"] > 0
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Test 4: parse_perf_file auto-detects format by extension
# ---------------------------------------------------------------------------

def test_parse_perf_file_autodetect():
    # .log → Gatling
    path_log = _write_tmp(".log", _GATLING_LOG)
    try:
        samples = parse_perf_file(path_log)
        assert all(s["source"] == "gatling" for s in samples)
    finally:
        os.unlink(path_log)

    # .xml → JMeter
    path_xml = _write_tmp(".xml", _JMETER_XML)
    try:
        samples = parse_perf_file(path_xml)
        assert all(s["source"] == "jmeter" for s in samples)
    finally:
        os.unlink(path_xml)

    # .json → JSON
    path_json = _write_tmp(".json", json.dumps(_JSON_DATA))
    try:
        samples = parse_perf_file(path_json)
        assert all(s["source"] == "json" for s in samples)
    finally:
        os.unlink(path_json)

    # Unknown extension raises ValueError
    path_txt = _write_tmp(".txt", "data")
    try:
        with pytest.raises(ValueError, match="Unrecognised"):
            parse_perf_file(path_txt)
    finally:
        os.unlink(path_txt)


# ---------------------------------------------------------------------------
# Test 5: p50/p99 calculation correctness on known data
# ---------------------------------------------------------------------------

def test_percentile_known_data():
    # 10 values: 1..10
    values = [float(i) for i in range(1, 11)]
    # p50: ceil(5.0) - 1 = index 4 = value 5
    assert _percentile(values, 50) == 5.0
    # p99: ceil(9.9) - 1 = index 9 = value 10
    assert _percentile(values, 99) == 10.0
    # p0 → index 0 = value 1
    assert _percentile(values, 0) == 1.0
    # p100 → index 9 = value 10
    assert _percentile(values, 100) == 10.0


def test_percentile_single_value():
    assert _percentile([42.0], 50) == 42.0
    assert _percentile([42.0], 99) == 42.0


def test_percentile_empty():
    assert _percentile([], 50) == 0.0


# Verify Gatling p50/p99 on known elapsed times: 150, 200, 300 ms → p50=200, p99=300
def test_gatling_percentile_values():
    # elapsed: endTime - startTime
    # user1: 250-100=150, user2: 400-200=200, user3: 600-300=300
    path = _write_tmp(".log", _GATLING_LOG)
    try:
        samples = parse_gatling(path)
        get_items = next(s for s in samples if s["endpoint_fqn"] == "GetItems")
        # sorted elapseds: [150, 200, 300]
        # p50: ceil(1.5)-1=1 → 200
        assert get_items["p50_ms"] == 200.0
        # p99: ceil(2.97)-1=2 → 300
        assert get_items["p99_ms"] == 300.0
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Test 6 (integration): ingest_perf_results returns dict with "ingested" key
# ---------------------------------------------------------------------------

def test_ingest_perf_results_integration():
    import kuzu
    from orihime.indexer import index_repo
    from orihime.mcp_server import ingest_perf_results as _ipr, _reset_connection
    import orihime.mcp_server as mcp_mod

    java_src = """\
package com.example;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/items")
public class ItemController {
    @GetMapping
    public java.util.List<String> getItems() {
        return java.util.Collections.emptyList();
    }
}
"""
    perf_data = [
        {"fqn": "com.example.ItemController.getItems", "p50_ms": 50.0, "p99_ms": 200.0, "rps": 100.0},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        src_dir = os.path.join(tmpdir, "src")
        os.makedirs(src_dir)
        with open(os.path.join(src_dir, "ItemController.java"), "w") as f:
            f.write(java_src)

        perf_file = os.path.join(tmpdir, "perf.json")
        with open(perf_file, "w") as f:
            json.dump(perf_data, f)

        db_path = os.path.join(tmpdir, "test.db")
        old_db_path = mcp_mod._DB_PATH
        mcp_mod._DB_PATH = db_path
        _reset_connection()

        try:
            index_repo(src_dir, "test-repo", db_path)
            _reset_connection()

            result = _ipr("test-repo", perf_file)
            assert isinstance(result, dict), f"Expected dict, got {result}"
            assert "ingested" in result, f"Missing 'ingested' key: {result}"
            assert result["ingested"] == 1
            assert "matched_methods" in result
            assert "unmatched" in result
        finally:
            mcp_mod._DB_PATH = old_db_path
            _reset_connection()


# ---------------------------------------------------------------------------
# Test 7 (integration): find_hotspots returns a list
# ---------------------------------------------------------------------------

def test_find_hotspots_integration():
    import kuzu
    from orihime.indexer import index_repo
    from orihime.mcp_server import (
        ingest_perf_results as _ipr,
        find_hotspots as _fh,
        _reset_connection,
    )
    import orihime.mcp_server as mcp_mod

    java_src = """\
package com.example;
import java.util.List;
public class HotService {
    private Repo repo;
    public void badMethod(List<Long> ids) {
        for (Long id : ids) {
            for (Long id2 : ids) {
                Object o = repo.findById(id);
            }
        }
    }
    public int clean(int a, int b) {
        return a + b;
    }
}
"""
    perf_data = [
        {"fqn": "com.example.HotService.badMethod", "p50_ms": 500.0, "p99_ms": 2000.0, "rps": 10.0},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        src_dir = os.path.join(tmpdir, "src")
        os.makedirs(src_dir)
        with open(os.path.join(src_dir, "HotService.java"), "w") as f:
            f.write(java_src)

        perf_file = os.path.join(tmpdir, "perf.json")
        with open(perf_file, "w") as f:
            json.dump(perf_data, f)

        db_path = os.path.join(tmpdir, "test.db")
        old_db_path = mcp_mod._DB_PATH
        mcp_mod._DB_PATH = db_path
        _reset_connection()

        try:
            index_repo(src_dir, "test-repo", db_path)
            _reset_connection()
            _ipr("test-repo", perf_file)

            results = _fh("test-repo")
            assert isinstance(results, list)
            # badMethod should appear (has complexity hints)
            fqns = [r["method_fqn"] for r in results]
            assert any("badMethod" in fqn for fqn in fqns), f"badMethod not found in: {fqns}"
            # All results should have required keys
            for r in results:
                assert "method_fqn" in r
                assert "complexity_hint" in r
                assert "risk_score" in r
                assert r["risk_score"] > 0
        finally:
            mcp_mod._DB_PATH = old_db_path
            _reset_connection()


# ---------------------------------------------------------------------------
# Test 8 (integration): estimate_capacity returns a list
# ---------------------------------------------------------------------------

def test_estimate_capacity_integration():
    import kuzu
    from orihime.indexer import index_repo
    from orihime.mcp_server import (
        ingest_perf_results as _ipr,
        estimate_capacity as _ec,
        _reset_connection,
    )
    import orihime.mcp_server as mcp_mod

    java_src = """\
package com.example;
import org.springframework.web.bind.annotation.*;
@RestController
public class CapController {
    @GetMapping("/cap")
    public String cap() { return "ok"; }
}
"""
    perf_data = [
        {"fqn": "CapController.cap", "p50_ms": 50.0, "p99_ms": 500.0, "rps": 50.0},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        src_dir = os.path.join(tmpdir, "src")
        os.makedirs(src_dir)
        with open(os.path.join(src_dir, "CapController.java"), "w") as f:
            f.write(java_src)

        perf_file = os.path.join(tmpdir, "perf.json")
        with open(perf_file, "w") as f:
            json.dump(perf_data, f)

        db_path = os.path.join(tmpdir, "test.db")
        old_db_path = mcp_mod._DB_PATH
        mcp_mod._DB_PATH = db_path
        _reset_connection()

        try:
            index_repo(src_dir, "test-repo", db_path)
            _reset_connection()
            _ipr("test-repo", perf_file)

            results = _ec("test-repo")
            assert isinstance(results, list)
            assert len(results) >= 1
            for r in results:
                assert "endpoint_fqn" in r
                assert "saturation_rps" in r
                assert "risk_level" in r
                assert r["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
                assert r["saturation_rps"] > 0
        finally:
            mcp_mod._DB_PATH = old_db_path
            _reset_connection()
