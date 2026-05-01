"""Unit tests for S8 — Entry-Point Reachability Filtering."""
from __future__ import annotations

import pathlib
import tempfile

import pytest

import orihime.java_extractor  # noqa: F401 — triggers register()
import orihime.kotlin_extractor  # noqa: F401 — triggers register()
from orihime.java_extractor import JavaExtractor
from orihime.kotlin_extractor import KotlinExtractor
from orihime.language import get_parser
from orihime.indexer import index_repo

FIXTURES_DIR = pathlib.Path(__file__).parent.parent / "fixtures"


def _make_db_path() -> pathlib.Path:
    tmpdir = tempfile.mkdtemp()
    return pathlib.Path(tmpdir) / "test.db"


# ---------------------------------------------------------------------------
# Helper: parse Java source string and extract
# ---------------------------------------------------------------------------

def _extract_java(source: str) -> list[dict]:
    src = source.encode("utf-8")
    parser = get_parser("java")
    tree = parser.parse(src)
    extractor = JavaExtractor()
    result = extractor.extract(tree, src, "file1", "repo1")
    return result.methods


# ---------------------------------------------------------------------------
# Test 1: @KafkaListener method has is_entry_point=True
# ---------------------------------------------------------------------------

def test_kafka_listener_is_entry_point():
    source = """
package com.example;

import org.springframework.kafka.annotation.KafkaListener;

@Service
public class EventConsumer {
    @KafkaListener(topics = "my-topic")
    public void handleEvent(String message) {
        // process
    }

    public void helperMethod() {
    }
}
"""
    methods = _extract_java(source)
    by_name = {m["name"]: m for m in methods}
    assert "handleEvent" in by_name, "handleEvent method not found"
    assert by_name["handleEvent"]["is_entry_point"] is True
    assert by_name["helperMethod"]["is_entry_point"] is False


# ---------------------------------------------------------------------------
# Test 2: @Scheduled method has is_entry_point=True
# ---------------------------------------------------------------------------

def test_scheduled_is_entry_point():
    source = """
package com.example;

import org.springframework.scheduling.annotation.Scheduled;

@Component
public class ScheduledTask {
    @Scheduled(fixedRate = 5000)
    public void runTask() {
        // scheduled work
    }

    private void internalHelper() {
    }
}
"""
    methods = _extract_java(source)
    by_name = {m["name"]: m for m in methods}
    assert "runTask" in by_name, "runTask method not found"
    assert by_name["runTask"]["is_entry_point"] is True
    assert by_name["internalHelper"]["is_entry_point"] is False


# ---------------------------------------------------------------------------
# Test 3: plain @Service method has is_entry_point=False
# ---------------------------------------------------------------------------

def test_plain_service_method_is_not_entry_point():
    source = """
package com.example;

@Service
public class UserService {
    public String findUser(String id) {
        return id;
    }

    public void updateUser(String id) {
    }
}
"""
    methods = _extract_java(source)
    for m in methods:
        if m["name"] not in ("<init>",):
            assert m["is_entry_point"] is False, f"{m['name']} should not be an entry point"


# ---------------------------------------------------------------------------
# Test 4: HTTP handler (@GetMapping) method has is_entry_point=True
# ---------------------------------------------------------------------------

def test_get_mapping_is_entry_point():
    source = """
package com.example;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class WalletController {
    @GetMapping("/wallet/balance")
    public String getBalance() {
        return "balance";
    }

    private void validate() {
    }
}
"""
    methods = _extract_java(source)
    by_name = {m["name"]: m for m in methods}
    assert "getBalance" in by_name, "getBalance method not found"
    assert by_name["getBalance"]["is_entry_point"] is True
    assert by_name["validate"]["is_entry_point"] is False


# ---------------------------------------------------------------------------
# Test 5 (integration): find_entry_points and find_reachable_sinks
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def security_db():
    db_path = _make_db_path()
    index_repo(FIXTURES_DIR, "test-repo", db_path, max_workers=1)
    return db_path


def test_find_entry_points_returns_list(security_db, monkeypatch):
    import orihime.mcp_server as mcp_mod
    import kuzu
    db = kuzu.Database(str(security_db))
    conn = kuzu.Connection(db)
    monkeypatch.setattr(mcp_mod, "_conn", conn)
    monkeypatch.setattr(mcp_mod, "_db", db)

    result = mcp_mod.find_entry_points("test-repo")
    assert isinstance(result, list)
    for item in result:
        assert "error" not in item
        assert "fqn" in item
        assert "file_path" in item
        assert "line_start" in item


def test_find_reachable_sinks_returns_list(security_db, monkeypatch):
    import orihime.mcp_server as mcp_mod
    import kuzu
    db = kuzu.Database(str(security_db))
    conn = kuzu.Connection(db)
    monkeypatch.setattr(mcp_mod, "_conn", conn)
    monkeypatch.setattr(mcp_mod, "_db", db)

    result = mcp_mod.find_reachable_sinks("test-repo")
    assert isinstance(result, list)
    for item in result:
        assert "error" not in item


def test_find_reachable_sinks_show_all_returns_same_or_more(security_db, monkeypatch):
    """show_all=True must return >= results than show_all=False (no filtering)."""
    import orihime.mcp_server as mcp_mod
    import kuzu
    db = kuzu.Database(str(security_db))
    conn = kuzu.Connection(db)
    monkeypatch.setattr(mcp_mod, "_conn", conn)
    monkeypatch.setattr(mcp_mod, "_db", db)

    filtered = mcp_mod.find_reachable_sinks("test-repo", show_all=False)
    all_sinks = mcp_mod.find_reachable_sinks("test-repo", show_all=True)

    assert isinstance(filtered, list)
    assert isinstance(all_sinks, list)
    # Reachable (filtered) can only be a subset of all sinks
    assert len(filtered) <= len(all_sinks)
