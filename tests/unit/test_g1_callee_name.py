"""Unit tests for G1: callee_name stored on CALLS edges.

Test 1: CALLS edge has callee_name property after indexing a fixture
Test 2: callee_name is the short method name (not FQN)
Test 3: find_external_calls returns a list
Test 4: UNRESOLVED_CALL callee_name is populated
Test 5: fan-out virtual dispatch CALLS edges also carry callee_name
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
def g1_db():
    """Index the fixtures directory into a fresh KuzuDB for G1 tests."""
    db_path = _make_db_path()
    index_repo(FIXTURES_DIR, "g1-repo", db_path, max_workers=1)
    return db_path


@pytest.fixture(scope="module")
def g1_conn(g1_db):
    db = kuzu.Database(str(g1_db))
    return kuzu.Connection(db)


# ---------------------------------------------------------------------------
# Test 1: CALLS edge has callee_name property after indexing
# ---------------------------------------------------------------------------

def test_calls_edge_has_callee_name_property(g1_conn):
    """After indexing, at least some CALLS edges must have a non-empty callee_name."""
    result = g1_conn.execute(
        "MATCH (a:Method)-[c:CALLS]->(b:Method) "
        "WHERE c.callee_name <> '' "
        "RETURN count(*) AS cnt"
    )
    assert result.has_next()
    cnt = result.get_next()[0]
    assert cnt > 0, (
        f"Expected at least one CALLS edge with a non-empty callee_name, got {cnt}"
    )


# ---------------------------------------------------------------------------
# Test 2: callee_name is the short method name, not the FQN
# ---------------------------------------------------------------------------

def test_calls_edge_callee_name_is_short_not_fqn(g1_conn):
    """callee_name on CALLS edges must be the short method name, not a FQN (no dots)."""
    result = g1_conn.execute(
        "MATCH (a:Method)-[c:CALLS]->(b:Method) "
        "WHERE c.callee_name <> '' "
        "RETURN c.callee_name "
        "LIMIT 50"
    )
    rows = []
    while result.has_next():
        rows.append(result.get_next()[0])

    assert rows, "Expected at least some CALLS edges with callee_name set"

    for name in rows:
        # Short method name should not contain a dot (that would be a FQN)
        # Exception: <init> is a valid short name with special chars, but no dot-separated pkg
        if name != "<init>" and ".<init>" not in name:
            assert "." not in name or name.endswith(".<init>"), (
                f"callee_name '{name}' looks like a FQN (contains '.') — expected short name"
            )


# ---------------------------------------------------------------------------
# Test 3: find_external_calls returns a list
# ---------------------------------------------------------------------------

def test_find_external_calls_returns_list(g1_db, monkeypatch):
    """find_external_calls must return a list (possibly empty for a single-repo index)."""
    import orihime.mcp_server as mcp_mod
    db = kuzu.Database(str(g1_db))
    conn = kuzu.Connection(db)
    monkeypatch.setattr(mcp_mod, "_conn", conn)
    monkeypatch.setattr(mcp_mod, "_db", db)

    result = mcp_mod.find_external_calls("g1-repo")
    assert isinstance(result, list)

    # If there are results, they must have the expected keys
    for item in result:
        assert "error" not in item, f"Unexpected error in find_external_calls: {item}"
        assert "caller_fqn" in item
        assert "callee_name" in item
        assert "call_count" in item
        assert isinstance(item["call_count"], int)
        assert item["call_count"] >= 1


# ---------------------------------------------------------------------------
# Test 4: UNRESOLVED_CALL callee_name is populated (via RestCall node)
# ---------------------------------------------------------------------------

def test_unresolved_call_callee_name_populated(g1_conn):
    """RestCall stub nodes created from UNRESOLVED_CALLs must have callee_name set."""
    result = g1_conn.execute(
        "MATCH (m:Method)-[:UNRESOLVED_CALL]->(rc:RestCall) "
        "RETURN rc.callee_name "
        "LIMIT 50"
    )
    rows = []
    while result.has_next():
        rows.append(result.get_next()[0])

    if not rows:
        pytest.skip("No UNRESOLVED_CALL edges in the fixture — cannot verify callee_name")

    non_empty = [n for n in rows if n and n.strip()]
    assert non_empty, (
        f"All {len(rows)} RestCall nodes have empty callee_name; expected at least one non-empty"
    )


# ---------------------------------------------------------------------------
# Test 5: Fan-out virtual dispatch CALLS edges also carry callee_name
# ---------------------------------------------------------------------------

def test_fanout_calls_edges_carry_callee_name(g1_conn):
    """Virtual dispatch fan-out CALLS edges (Phase 6) must also carry callee_name.

    VirtualDispatch.java: AnimalTrainer.train calls Animal.speak (abstract).
    Phase 6 fans out to Dog.speak and Cat.speak.
    All these CALLS edges should have callee_name='speak'.
    """
    # Query for CALLS edges where the callee is Dog.speak or Cat.speak
    result = g1_conn.execute(
        "MATCH (a:Method)-[c:CALLS]->(b:Method) "
        "WHERE b.name = 'speak' "
        "RETURN a.fqn, b.fqn, c.callee_name"
    )
    rows = []
    while result.has_next():
        rows.append(result.get_next())

    if not rows:
        pytest.skip(
            "No CALLS edges to 'speak' found — VirtualDispatch fixture may not be indexed"
        )

    for caller_fqn, callee_fqn, callee_name in rows:
        assert callee_name, (
            f"CALLS edge {caller_fqn} -> {callee_fqn} has empty callee_name; expected 'speak'"
        )
        assert callee_name == "speak", (
            f"CALLS edge {caller_fqn} -> {callee_fqn} has callee_name='{callee_name}'; expected 'speak'"
        )
