"""Unit tests for orihime.cross_resolver.

All tests use a fresh in-memory KuzuDB instance (temp dir) with hand-inserted
nodes — no real repo checkout required.
"""
from __future__ import annotations

import os
import tempfile

import kuzu
import pytest

from orihime.schema import init_schema
from orihime.cross_resolver import load_indexed_repos, run_cross_resolution


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn() -> kuzu.Connection:
    """Return a fresh KuzuDB connection backed by a temporary directory."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    db = kuzu.Database(db_path)
    conn = kuzu.Connection(db)
    init_schema(conn)
    return conn


def _count(conn: kuzu.Connection, cypher: str) -> int:
    result = conn.execute(cypher)
    return result.get_next()[0]


# ---------------------------------------------------------------------------
# Shared fixture: 2 repos, 1 Method each, 1 Endpoint in repo2, 1 RestCall
# in repo1 targeting that endpoint.
#
# Repo1 (caller): Method m1 calls /api/users/123 (GET)
# Repo2 (callee): Endpoint e1 GET /api/users/{id}  →  handler Method m2
# ---------------------------------------------------------------------------

@pytest.fixture
def two_repo_conn():
    conn = _make_conn()

    # Repo nodes
    conn.execute("CREATE (:Repo {id: 'repo1', name: 'caller-repo', root_path: '/tmp/repo1'})")
    conn.execute("CREATE (:Repo {id: 'repo2', name: 'callee-repo', root_path: '/tmp/repo2'})")

    # File nodes (required for Class → Method chain, but we skip Class here
    # and insert Method directly which is sufficient for cross_resolver)
    conn.execute(
        "CREATE (:File {id: 'f1', path: '/tmp/repo1/A.kt', language: 'kotlin', repo_id: 'repo1'})"
    )
    conn.execute(
        "CREATE (:File {id: 'f2', path: '/tmp/repo2/B.kt', language: 'kotlin', repo_id: 'repo2'})"
    )

    # Class nodes (needed because Method.class_id is a foreign key stored as
    # plain string — the cross_resolver only reads Method.id and repo_id, so
    # we just need valid Class nodes to keep the DB consistent)
    conn.execute(
        "CREATE (:Class {id: 'c1', name: 'Caller', fqn: 'com.Caller', file_id: 'f1', "
        "repo_id: 'repo1', is_interface: false, annotations: []})"
    )
    conn.execute(
        "CREATE (:Class {id: 'c2', name: 'Callee', fqn: 'com.Callee', file_id: 'f2', "
        "repo_id: 'repo2', is_interface: false, annotations: []})"
    )

    # Method nodes
    conn.execute(
        "CREATE (:Method {id: 'm1', name: 'callUsers', fqn: 'com.Caller.callUsers', "
        "class_id: 'c1', file_id: 'f1', repo_id: 'repo1', line_start: 10, "
        "is_suspend: false, annotations: []})"
    )
    conn.execute(
        "CREATE (:Method {id: 'm2', name: 'getUser', fqn: 'com.Callee.getUser', "
        "class_id: 'c2', file_id: 'f2', repo_id: 'repo2', line_start: 20, "
        "is_suspend: false, annotations: []})"
    )

    # Endpoint in repo2 — path_regex deliberately left empty so cross_resolver
    # computes it on the fly from path
    conn.execute(
        "CREATE (:Endpoint {id: 'e1', http_method: 'GET', path: '/api/users/{id}', "
        "path_regex: '', handler_method_id: 'm2', repo_id: 'repo2'})"
    )

    # RestCall in repo1 — caller_method_id points to m1
    conn.execute(
        "CREATE (:RestCall {id: 'rc1', http_method: 'GET', url_pattern: '/api/users/123', "
        "caller_method_id: 'm1', repo_id: 'repo1'})"
    )

    # UNRESOLVED_CALL edge (Method → RestCall) — resolver should delete this
    conn.execute(
        "MATCH (m:Method {id: 'm1'}), (rc:RestCall {id: 'rc1'}) "
        "CREATE (m)-[:UNRESOLVED_CALL]->(rc)"
    )

    return conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_run_cross_resolution_matches_rest_call(two_repo_conn):
    """After resolution the CALLS_REST edge count must be exactly 1."""
    stats = run_cross_resolution(two_repo_conn)

    calls_rest = _count(
        two_repo_conn,
        "MATCH ()-[:CALLS_REST]->() RETURN count(*)"
    )
    assert calls_rest == 1, (
        f"Expected 1 CALLS_REST edge, got {calls_rest}; stats={stats}"
    )
    assert stats["matched"] == 1


def test_run_cross_resolution_creates_depends_on(two_repo_conn):
    """After resolution a DEPENDS_ON edge between the two repos must exist."""
    run_cross_resolution(two_repo_conn)

    depends_on = _count(
        two_repo_conn,
        "MATCH ()-[:DEPENDS_ON]->() RETURN count(*)"
    )
    assert depends_on == 1, (
        f"Expected 1 DEPENDS_ON edge, got {depends_on}"
    )


def test_unresolved_call_not_matched_if_dynamic():
    """A RestCall with url_pattern='DYNAMIC' must produce no CALLS_REST edge."""
    conn = _make_conn()

    conn.execute("CREATE (:Repo {id: 'repo1', name: 'r1', root_path: '/tmp/r1'})")
    conn.execute("CREATE (:Repo {id: 'repo2', name: 'r2', root_path: '/tmp/r2'})")

    conn.execute(
        "CREATE (:File {id: 'f1', path: '/tmp/r1/A.kt', language: 'kotlin', repo_id: 'repo1'})"
    )
    conn.execute(
        "CREATE (:File {id: 'f2', path: '/tmp/r2/B.kt', language: 'kotlin', repo_id: 'repo2'})"
    )
    conn.execute(
        "CREATE (:Class {id: 'c1', name: 'C1', fqn: 'C1', file_id: 'f1', "
        "repo_id: 'repo1', is_interface: false, annotations: []})"
    )
    conn.execute(
        "CREATE (:Class {id: 'c2', name: 'C2', fqn: 'C2', file_id: 'f2', "
        "repo_id: 'repo2', is_interface: false, annotations: []})"
    )
    conn.execute(
        "CREATE (:Method {id: 'm1', name: 'caller', fqn: 'C1.caller', "
        "class_id: 'c1', file_id: 'f1', repo_id: 'repo1', line_start: 1, "
        "is_suspend: false, annotations: []})"
    )
    conn.execute(
        "CREATE (:Method {id: 'm2', name: 'handler', fqn: 'C2.handler', "
        "class_id: 'c2', file_id: 'f2', repo_id: 'repo2', line_start: 1, "
        "is_suspend: false, annotations: []})"
    )
    conn.execute(
        "CREATE (:Endpoint {id: 'e1', http_method: 'GET', path: '/api/users/{id}', "
        "path_regex: '', handler_method_id: 'm2', repo_id: 'repo2'})"
    )
    # DYNAMIC url_pattern — should be skipped entirely
    conn.execute(
        "CREATE (:RestCall {id: 'rc_dyn', http_method: 'GET', url_pattern: 'DYNAMIC', "
        "caller_method_id: 'm1', repo_id: 'repo1'})"
    )

    stats = run_cross_resolution(conn)

    calls_rest = _count(conn, "MATCH ()-[:CALLS_REST]->() RETURN count(*)")
    assert calls_rest == 0, (
        f"DYNAMIC RestCall should not produce CALLS_REST edge; got {calls_rest}"
    )
    assert stats["matched"] == 0
    assert stats["unresolved"] >= 1


def test_load_indexed_repos_returns_names():
    """load_indexed_repos must return all repo names currently indexed."""
    conn = _make_conn()

    conn.execute("CREATE (:Repo {id: 'r1', name: 'alpha', root_path: '/a'})")
    conn.execute("CREATE (:Repo {id: 'r2', name: 'beta', root_path: '/b'})")

    names = load_indexed_repos(conn)

    assert set(names) == {"alpha", "beta"}, (
        f"Expected {{'alpha', 'beta'}}, got {set(names)}"
    )
