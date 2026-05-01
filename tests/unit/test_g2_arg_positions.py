"""Unit tests for G2: caller_arg_pos / callee_param_pos on CALLS edges.

Test 1: CALLS edge has caller_arg_pos property after indexing (field exists, not null)
Test 2: zero-argument call has caller_arg_pos = -1
Test 3: call with arguments has caller_arg_pos = 0
Test 4: find_taint_flows returns a list
Test 5: find_taint_sinks results now include caller_arg_pos field
"""
from __future__ import annotations

import pathlib
import tempfile

import kuzu
import pytest

from orihime.indexer import index_repo

FIXTURES_DIR = pathlib.Path(__file__).parent.parent / "fixtures"


def _make_db_path() -> pathlib.Path:
    tmpdir = tempfile.mkdtemp()
    return pathlib.Path(tmpdir) / "test.db"


@pytest.fixture(scope="module")
def g2_db():
    """Index the fixtures directory into a fresh KuzuDB for G2 tests."""
    db_path = _make_db_path()
    index_repo(FIXTURES_DIR, "g2-repo", db_path, max_workers=1)
    return db_path


@pytest.fixture(scope="module")
def g2_conn(g2_db):
    db = kuzu.Database(str(g2_db))
    return kuzu.Connection(db)


# ---------------------------------------------------------------------------
# Test 1: CALLS edge has caller_arg_pos property after indexing
# ---------------------------------------------------------------------------

def test_calls_edge_has_caller_arg_pos_property(g2_conn):
    """After indexing, CALLS edges must have caller_arg_pos column (not null for any row)."""
    result = g2_conn.execute(
        "MATCH (a:Method)-[c:CALLS]->(b:Method) "
        "RETURN c.caller_arg_pos, c.callee_param_pos "
        "LIMIT 10"
    )
    rows = []
    while result.has_next():
        rows.append(result.get_next())

    assert rows, "Expected at least one CALLS edge after indexing the fixture directory"

    for cap, cpp in rows:
        # Both fields must be integers (not None / null)
        assert isinstance(cap, int), f"caller_arg_pos must be int, got {type(cap)!r} (value={cap!r})"
        assert isinstance(cpp, int), f"callee_param_pos must be int, got {type(cpp)!r} (value={cpp!r})"
        # Values must be either -1 (not tracked) or >= 0
        assert cap >= -1, f"caller_arg_pos must be >= -1, got {cap}"
        assert cpp >= -1, f"callee_param_pos must be >= -1, got {cpp}"


# ---------------------------------------------------------------------------
# Test 2: zero-argument call has caller_arg_pos = -1
# ---------------------------------------------------------------------------

def test_zero_arg_call_has_minus_one(g2_conn):
    """Calls to zero-argument methods (e.g. CallChain.methodB() → methodC()) must have caller_arg_pos = -1.

    CallChain.java has methodA() { methodB(); } — zero args, so both positions should be -1.
    """
    result = g2_conn.execute(
        "MATCH (a:Method)-[c:CALLS]->(b:Method) "
        "WHERE b.name = 'methodC' "
        "RETURN c.caller_arg_pos, c.callee_param_pos"
    )
    rows = []
    while result.has_next():
        rows.append(result.get_next())

    if not rows:
        pytest.skip("No CALLS edge to methodC found in fixtures — skipping zero-arg test")

    for cap, cpp in rows:
        assert cap == -1, (
            f"Expected caller_arg_pos=-1 for zero-arg call to methodC, got {cap}"
        )
        assert cpp == -1, (
            f"Expected callee_param_pos=-1 for zero-arg call to methodC, got {cpp}"
        )


# ---------------------------------------------------------------------------
# Test 3: call with arguments has caller_arg_pos = 0
# ---------------------------------------------------------------------------

def test_call_with_args_has_zero_position(g2_conn):
    """At least some CALLS edges must have caller_arg_pos = 0 (indicating a call with >= 1 argument).

    Any fixture method that calls another with at least one argument will satisfy this.
    """
    result = g2_conn.execute(
        "MATCH (a:Method)-[c:CALLS]->(b:Method) "
        "WHERE c.caller_arg_pos = 0 "
        "RETURN count(*) AS cnt"
    )
    assert result.has_next()
    cnt = result.get_next()[0]
    assert cnt > 0, (
        f"Expected at least one CALLS edge with caller_arg_pos=0 (call with arguments), got {cnt}. "
        "Check that the fixtures contain at least one method call with arguments."
    )


# ---------------------------------------------------------------------------
# Test 4: find_taint_flows returns a list
# ---------------------------------------------------------------------------

def test_find_taint_flows_returns_list(g2_db, monkeypatch):
    """find_taint_flows must return a list (possibly empty for fixtures without taint-source annotations)."""
    import orihime.mcp_server as mcp_mod
    db = kuzu.Database(str(g2_db))
    conn = kuzu.Connection(db)
    monkeypatch.setattr(mcp_mod, "_conn", conn)
    monkeypatch.setattr(mcp_mod, "_db", db)

    result = mcp_mod.find_taint_flows("g2-repo")
    assert isinstance(result, list), f"Expected list, got {type(result)}"

    # If there are results, they must have the expected keys
    for item in result:
        assert "error" not in item, f"Unexpected error in find_taint_flows: {item}"
        assert "source_method_fqn" in item, f"Missing source_method_fqn in {item}"
        assert "sink_method_name" in item, f"Missing sink_method_name in {item}"
        assert "caller_arg_pos" in item, f"Missing caller_arg_pos in {item}"
        assert "callee_param_pos" in item, f"Missing callee_param_pos in {item}"
        assert "file_path" in item, f"Missing file_path in {item}"
        assert "line_start" in item, f"Missing line_start in {item}"
        assert "owasp_category" in item, f"Missing owasp_category in {item}"
        assert item["caller_arg_pos"] == 0, (
            f"find_taint_flows only returns flows with caller_arg_pos=0, got {item['caller_arg_pos']}"
        )


# ---------------------------------------------------------------------------
# Test 5: find_taint_sinks results now include caller_arg_pos field
# ---------------------------------------------------------------------------

def test_find_taint_sinks_includes_arg_pos_fields(g2_db, monkeypatch):
    """find_taint_sinks must include caller_arg_pos and callee_param_pos in every result dict."""
    import orihime.mcp_server as mcp_mod
    db = kuzu.Database(str(g2_db))
    conn = kuzu.Connection(db)
    monkeypatch.setattr(mcp_mod, "_conn", conn)
    monkeypatch.setattr(mcp_mod, "_db", db)

    result = mcp_mod.find_taint_sinks("g2-repo")
    assert isinstance(result, list), f"Expected list, got {type(result)}"

    for item in result:
        assert "error" not in item, f"Unexpected error in find_taint_sinks: {item}"
        assert "caller_arg_pos" in item, (
            f"G2: find_taint_sinks result missing caller_arg_pos field: {item}"
        )
        assert "callee_param_pos" in item, (
            f"G2: find_taint_sinks result missing callee_param_pos field: {item}"
        )
        # Values must be integers
        assert isinstance(item["caller_arg_pos"], int), (
            f"caller_arg_pos must be int, got {type(item['caller_arg_pos'])!r}"
        )
        assert isinstance(item["callee_param_pos"], int), (
            f"callee_param_pos must be int, got {type(item['callee_param_pos'])!r}"
        )
