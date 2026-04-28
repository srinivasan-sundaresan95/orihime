"""Unit tests for indra.mcp_server — all 10 tools.

Strategy
--------
* "No DB" path  — patch ``_get_connection`` to return ``None``;
  verify every query tool returns an empty list (or ``None`` for
  ``get_file_location``).
* "With DB" path — open a real in-memory KuzuDB in a temp dir, populate
  minimal test data, then monkeypatch the module-level ``_conn`` and ``_db``
  globals so ``_get_connection`` always hands back the real connection.
  This exercises the actual Cypher queries without touching the file system.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from typing import Optional
from unittest.mock import patch

import kuzu
import pytest

from indra.schema import init_schema
import indra.mcp_server as mcp_mod
from indra.mcp_server import (
    blast_radius,
    find_callers,
    find_callees,
    find_endpoint_callers,
    find_repo_dependencies,
    get_file_location,
    list_endpoints,
    list_repos,
    list_unresolved_calls,
    search_symbol,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn() -> kuzu.Connection:
    """Create a fresh KuzuDB connection backed by a temp dir."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    db = kuzu.Database(db_path)
    return kuzu.Connection(db)


def _populate(conn: kuzu.Connection) -> dict:
    """Insert the canonical minimal test data set.

    Graph shape
    -----------
        Repo(test-repo)
          └── File(src/Foo.java)
                └── Class(com.example.FooService)
                      ├── Method(callFoo)  --CALLS-->  Method(doWork)
                      └── Method(doWork)
        Endpoint(GET /api/foo) handler=doWork
        Repo --EXPOSES--> Endpoint
    """
    repo_id   = str(uuid.uuid4())
    file_id   = str(uuid.uuid4())
    class_id  = str(uuid.uuid4())
    caller_id = str(uuid.uuid4())
    callee_id = str(uuid.uuid4())
    ep_id     = str(uuid.uuid4())

    conn.execute(
        "CREATE (:Repo {id: $id, name: $name, root_path: $rp})",
        {"id": repo_id, "name": "test-repo", "rp": "/tmp/test-repo"},
    )
    conn.execute(
        "CREATE (:File {id: $id, path: $path, repo_id: $rid, language: $lang})",
        {"id": file_id, "path": "src/Foo.java", "rid": repo_id, "lang": "java"},
    )
    conn.execute(
        "CREATE (:Class {id: $id, name: $name, fqn: $fqn, file_id: $fid, "
        "repo_id: $rid, is_interface: false, annotations: $ann})",
        {
            "id": class_id,
            "name": "FooService",
            "fqn": "com.example.FooService",
            "fid": file_id,
            "rid": repo_id,
            "ann": [],
        },
    )
    # caller method: callFoo (line 10)
    conn.execute(
        "CREATE (:Method {id: $id, name: $name, fqn: $fqn, class_id: $cid, "
        "file_id: $fid, repo_id: $rid, line_start: 10, is_suspend: false, annotations: $ann})",
        {
            "id": caller_id,
            "name": "callFoo",
            "fqn": "com.example.FooService.callFoo",
            "cid": class_id,
            "fid": file_id,
            "rid": repo_id,
            "ann": [],
        },
    )
    # callee method: doWork (line 20)
    conn.execute(
        "CREATE (:Method {id: $id, name: $name, fqn: $fqn, class_id: $cid, "
        "file_id: $fid, repo_id: $rid, line_start: 20, is_suspend: false, annotations: $ann})",
        {
            "id": callee_id,
            "name": "doWork",
            "fqn": "com.example.FooService.doWork",
            "cid": class_id,
            "fid": file_id,
            "rid": repo_id,
            "ann": [],
        },
    )
    # CALLS edge: callFoo -> doWork
    conn.execute(
        "MATCH (a:Method), (b:Method) WHERE a.id = $aid AND b.id = $bid "
        "CREATE (a)-[:CALLS]->(b)",
        {"aid": caller_id, "bid": callee_id},
    )
    # Endpoint for doWork
    conn.execute(
        "CREATE (:Endpoint {id: $id, http_method: $hm, path: $path, "
        "path_regex: $pr, handler_method_id: $hmid, repo_id: $rid})",
        {
            "id": ep_id,
            "hm": "GET",
            "path": "/api/foo",
            "pr": "^/api/foo$",
            "hmid": callee_id,
            "rid": repo_id,
        },
    )
    # EXPOSES: Repo -> Endpoint
    conn.execute(
        "MATCH (r:Repo), (e:Endpoint) WHERE r.id = $rid AND e.id = $eid "
        "CREATE (r)-[:EXPOSES]->(e)",
        {"rid": repo_id, "eid": ep_id},
    )

    return {
        "repo_id": repo_id,
        "file_id": file_id,
        "class_id": class_id,
        "caller_id": caller_id,
        "callee_id": callee_id,
        "ep_id": ep_id,
    }


# ---------------------------------------------------------------------------
# Module-scoped fixture: real populated DB
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def db_conn() -> kuzu.Connection:
    """Return a KuzuDB connection pre-loaded with the minimal test data set."""
    conn = _make_conn()
    init_schema(conn)
    _populate(conn)
    return conn


@pytest.fixture()
def with_db(db_conn: kuzu.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make all mcp_server tool calls use the real test DB connection."""
    monkeypatch.setattr(mcp_mod, "_conn", db_conn)
    # Set _db to a truthy sentinel so _get_connection() short-circuits to _conn.
    monkeypatch.setattr(mcp_mod, "_db", object())


# ---------------------------------------------------------------------------
# "No DB" path — _get_connection returns None
# ---------------------------------------------------------------------------

class TestNoConnection:
    """All query tools must degrade gracefully when the DB is absent."""

    def test_find_callers_returns_empty_list(self) -> None:
        with patch("indra.mcp_server._get_connection", return_value=None):
            assert find_callers("any.Method") == []

    def test_find_callees_returns_empty_list(self) -> None:
        with patch("indra.mcp_server._get_connection", return_value=None):
            assert find_callees("any.Method") == []

    def test_find_endpoint_callers_returns_empty_list(self) -> None:
        with patch("indra.mcp_server._get_connection", return_value=None):
            assert find_endpoint_callers("GET", "/any") == []

    def test_find_repo_dependencies_returns_empty_list(self) -> None:
        with patch("indra.mcp_server._get_connection", return_value=None):
            assert find_repo_dependencies("any-repo") == []

    def test_blast_radius_returns_empty_list(self) -> None:
        with patch("indra.mcp_server._get_connection", return_value=None):
            assert blast_radius("any.Method") == []

    def test_search_symbol_returns_empty_list(self) -> None:
        with patch("indra.mcp_server._get_connection", return_value=None):
            assert search_symbol("anything") == []

    def test_get_file_location_returns_none(self) -> None:
        with patch("indra.mcp_server._get_connection", return_value=None):
            assert get_file_location("any.FQN") is None

    def test_list_endpoints_returns_empty_list(self) -> None:
        with patch("indra.mcp_server._get_connection", return_value=None):
            assert list_endpoints("") == []

    def test_list_unresolved_calls_returns_empty_list(self) -> None:
        with patch("indra.mcp_server._get_connection", return_value=None):
            assert list_unresolved_calls("") == []

    @pytest.mark.parametrize("tool,args", [
        (find_callers,            ("any.Method",)),
        (find_callees,            ("any.Method",)),
        (find_repo_dependencies,  ("any-repo",)),
        (list_endpoints,          ("",)),
        (list_unresolved_calls,   ("",)),
    ])
    def test_all_list_tools_return_empty_list(self, tool, args) -> None:
        with patch("indra.mcp_server._get_connection", return_value=None):
            result = tool(*args)
            assert result == [], f"{tool.__name__} should return [] but got {result!r}"

    def test_get_file_location_returns_none_parametrized(self) -> None:
        with patch("indra.mcp_server._get_connection", return_value=None):
            assert get_file_location("no.such.FQN") is None


# ---------------------------------------------------------------------------
# "With DB" path — real KuzuDB queries
# ---------------------------------------------------------------------------

class TestWithDB:
    """Query tools against real in-memory data — validates Cypher correctness."""

    # --- find_callers -------------------------------------------------------

    def test_find_callers_returns_caller(self, with_db) -> None:
        results = find_callers("com.example.FooService.doWork")
        fqns = [r["fqn"] for r in results]
        assert "com.example.FooService.callFoo" in fqns

    def test_find_callers_result_has_expected_keys(self, with_db) -> None:
        results = find_callers("com.example.FooService.doWork")
        assert len(results) >= 1
        assert set(results[0].keys()) >= {"fqn", "file_path", "line_start"}

    def test_find_callers_nonexistent_returns_empty(self, with_db) -> None:
        assert find_callers("no.such.Method") == []

    # --- find_callees -------------------------------------------------------

    def test_find_callees_returns_callee(self, with_db) -> None:
        results = find_callees("com.example.FooService.callFoo")
        fqns = [r["fqn"] for r in results]
        assert "com.example.FooService.doWork" in fqns

    def test_find_callees_result_has_expected_keys(self, with_db) -> None:
        results = find_callees("com.example.FooService.callFoo")
        assert len(results) >= 1
        assert set(results[0].keys()) >= {"fqn", "file_path", "line_start"}

    def test_find_callees_nonexistent_returns_empty(self, with_db) -> None:
        assert find_callees("no.such.Method") == []

    # --- list_endpoints -----------------------------------------------------

    def test_list_endpoints_all_returns_result(self, with_db) -> None:
        results = list_endpoints("")
        assert len(results) >= 1

    def test_list_endpoints_all_has_expected_fields(self, with_db) -> None:
        results = list_endpoints("")
        ep = next((r for r in results if r["path"] == "/api/foo"), None)
        assert ep is not None, "Expected endpoint /api/foo not found"
        assert ep["http_method"] == "GET"
        assert ep["repo_name"] == "test-repo"
        assert "doWork" in ep["handler_fqn"]

    def test_list_endpoints_filtered_by_repo(self, with_db) -> None:
        results = list_endpoints("test-repo")
        assert len(results) >= 1
        assert all(r["repo_name"] == "test-repo" for r in results)

    def test_list_endpoints_unknown_repo_returns_empty(self, with_db) -> None:
        assert list_endpoints("other-repo") == []

    # --- search_symbol ------------------------------------------------------

    def test_search_symbol_finds_class(self, with_db) -> None:
        results = search_symbol("fooservice")
        types = [r["type"] for r in results]
        assert "class" in types

    def test_search_symbol_class_result_fqn(self, with_db) -> None:
        results = search_symbol("fooservice")
        class_hits = [r for r in results if r["type"] == "class"]
        fqns = [r["fqn"] for r in class_hits]
        assert "com.example.FooService" in fqns

    def test_search_symbol_finds_method(self, with_db) -> None:
        results = search_symbol("dowork")
        types = [r["type"] for r in results]
        assert "method" in types

    def test_search_symbol_method_result_fqn(self, with_db) -> None:
        results = search_symbol("dowork")
        method_hits = [r for r in results if r["type"] == "method"]
        fqns = [r["fqn"] for r in method_hits]
        assert "com.example.FooService.doWork" in fqns

    def test_search_symbol_no_match_returns_empty(self, with_db) -> None:
        assert search_symbol("xyzzy_nonexistent_99") == []

    # --- get_file_location --------------------------------------------------

    def test_get_file_location_method(self, with_db) -> None:
        result = get_file_location("com.example.FooService.doWork")
        assert result is not None
        assert result["line_start"] == 20
        assert result["fqn"] == "com.example.FooService.doWork"

    def test_get_file_location_class_returns_line_zero(self, with_db) -> None:
        result = get_file_location("com.example.FooService")
        assert result is not None
        assert result["line_start"] == 0
        assert result["fqn"] == "com.example.FooService"

    def test_get_file_location_missing_returns_none(self, with_db) -> None:
        assert get_file_location("no.such.FQN") is None

    def test_get_file_location_result_has_file_path(self, with_db) -> None:
        result = get_file_location("com.example.FooService.callFoo")
        assert result is not None
        assert "file_path" in result

    # --- blast_radius -------------------------------------------------------

    def test_blast_radius_finds_direct_caller(self, with_db) -> None:
        results = blast_radius("com.example.FooService.doWork", max_depth=2)
        fqns = {r["fqn"] for r in results}
        assert "com.example.FooService.callFoo" in fqns

    def test_blast_radius_depth_is_one_for_direct_caller(self, with_db) -> None:
        results = blast_radius("com.example.FooService.doWork", max_depth=2)
        hit = next(r for r in results if r["fqn"] == "com.example.FooService.callFoo")
        assert hit["depth"] == 1

    def test_blast_radius_result_keys(self, with_db) -> None:
        results = blast_radius("com.example.FooService.doWork", max_depth=2)
        assert len(results) >= 1
        assert set(results[0].keys()) >= {"fqn", "depth", "file_path"}

    def test_blast_radius_no_callers_returns_empty(self, with_db) -> None:
        # callFoo has no callers in the test data
        assert blast_radius("com.example.FooService.callFoo") == []

    def test_blast_radius_nonexistent_returns_empty(self, with_db) -> None:
        assert blast_radius("no.such.Method") == []

    # --- find_endpoint_callers ----------------------------------------------

    def test_find_endpoint_callers_returns_handler(self, with_db) -> None:
        results = find_endpoint_callers("GET", "/api/foo")
        handler = next((r for r in results if r["role"] == "handler"), None)
        assert handler is not None
        assert "doWork" in handler["fqn"]

    def test_find_endpoint_callers_handler_keys(self, with_db) -> None:
        results = find_endpoint_callers("GET", "/api/foo")
        handler = next(r for r in results if r["role"] == "handler")
        assert set(handler.keys()) >= {"role", "fqn", "file_path", "line_start"}

    def test_find_endpoint_callers_case_insensitive_verb(self, with_db) -> None:
        # http_method is uppercased inside the tool
        results_lower = find_endpoint_callers("get", "/api/foo")
        results_upper = find_endpoint_callers("GET", "/api/foo")
        assert len(results_lower) == len(results_upper)

    def test_find_endpoint_callers_unknown_path_returns_empty(self, with_db) -> None:
        assert find_endpoint_callers("GET", "/no/such/path") == []

    def test_find_endpoint_callers_wrong_method_returns_empty(self, with_db) -> None:
        assert find_endpoint_callers("POST", "/api/foo") == []

    # --- find_repo_dependencies (no data — just verify graceful empty) ------

    def test_find_repo_dependencies_no_edges_returns_empty(self, with_db) -> None:
        # No DEPENDS_ON edges were inserted; must return [] not an error
        result = find_repo_dependencies("test-repo")
        assert result == []

    # --- list_unresolved_calls (no data — just verify graceful empty) -------

    def test_list_unresolved_calls_no_data_returns_empty(self, with_db) -> None:
        # No RestCall nodes were inserted
        result = list_unresolved_calls("")
        assert result == []

    def test_list_unresolved_calls_filtered_no_data_returns_empty(self, with_db) -> None:
        result = list_unresolved_calls("test-repo")
        assert result == []

    # --- file_path key assertions (P3-4.1) ----------------------------------

    def test_find_callers_result_has_file_path(self, with_db) -> None:
        results = find_callers("com.example.FooService.doWork")
        assert len(results) >= 1
        assert "file_path" in results[0]
        assert "file_id" not in results[0]

    def test_find_callers_file_path_value(self, with_db) -> None:
        results = find_callers("com.example.FooService.doWork")
        assert len(results) >= 1
        assert results[0]["file_path"] == "src/Foo.java"

    def test_search_symbol_class_result_has_file_path(self, with_db) -> None:
        results = search_symbol("fooservice")
        class_hits = [r for r in results if r["type"] == "class"]
        assert len(class_hits) >= 1
        assert "file_path" in class_hits[0]
        assert "file_id" not in class_hits[0]

    def test_search_symbol_method_result_has_file_path(self, with_db) -> None:
        results = search_symbol("dowork")
        method_hits = [r for r in results if r["type"] == "method"]
        assert len(method_hits) >= 1
        assert "file_path" in method_hits[0]
        assert "file_id" not in method_hits[0]

    def test_get_file_location_has_file_path_not_file_id(self, with_db) -> None:
        result = get_file_location("com.example.FooService.doWork")
        assert result is not None
        assert "file_path" in result
        assert "file_id" not in result

    def test_get_file_location_file_path_value(self, with_db) -> None:
        result = get_file_location("com.example.FooService.doWork")
        assert result is not None
        assert result["file_path"] == "src/Foo.java"

    def test_get_file_location_class_has_file_path(self, with_db) -> None:
        result = get_file_location("com.example.FooService")
        assert result is not None
        assert "file_path" in result
        assert "file_id" not in result

    def test_blast_radius_result_has_file_path(self, with_db) -> None:
        results = blast_radius("com.example.FooService.doWork", max_depth=2)
        assert len(results) >= 1
        assert "file_path" in results[0]
        assert "file_id" not in results[0]

    # --- list_repos (P3-4.2) ------------------------------------------------

    def test_list_repos_returns_indexed_repo(self, with_db) -> None:
        results = list_repos()
        names = [r["name"] for r in results]
        assert "test-repo" in names

    def test_list_repos_result_has_expected_keys(self, with_db) -> None:
        results = list_repos()
        assert len(results) >= 1
        assert set(results[0].keys()) >= {"name", "root_path", "method_count", "endpoint_count"}

    def test_list_repos_method_count_is_nonzero(self, with_db) -> None:
        results = list_repos()
        repo = next(r for r in results if r["name"] == "test-repo")
        assert repo["method_count"] >= 2  # callFoo + doWork

    def test_list_repos_endpoint_count_is_nonzero(self, with_db) -> None:
        results = list_repos()
        repo = next(r for r in results if r["name"] == "test-repo")
        assert repo["endpoint_count"] >= 1  # GET /api/foo

    def test_list_repos_root_path_is_correct(self, with_db) -> None:
        results = list_repos()
        repo = next(r for r in results if r["name"] == "test-repo")
        assert repo["root_path"] == "/tmp/test-repo"


# ---------------------------------------------------------------------------
# "No DB" path — list_repos
# ---------------------------------------------------------------------------

class TestListReposNoConnection:
    """list_repos must degrade gracefully when the DB is absent."""

    def test_list_repos_no_connection_returns_empty(self) -> None:
        with patch("indra.mcp_server._get_connection", return_value=None):
            assert list_repos() == []


# ---------------------------------------------------------------------------
# "Empty DB" path — list_repos with no repos indexed
# ---------------------------------------------------------------------------

class TestListReposEmptyDB:
    """list_repos returns empty list when no repos have been indexed."""

    def test_list_repos_empty_db_returns_empty(self) -> None:
        conn = _make_conn()
        init_schema(conn)
        # Do not populate any data
        import indra.mcp_server as _mod
        from unittest.mock import patch as _patch
        with _patch.object(_mod, "_conn", conn), _patch.object(_mod, "_db", object()):
            result = list_repos()
        assert result == []
