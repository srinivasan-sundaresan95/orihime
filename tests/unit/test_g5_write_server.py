"""Unit tests for G5-Fix-C — Write-Serialization Server.

Tests:
  1. WriteClient.is_available() returns False when server not running
  2. _Writer with no server_url routes execute() to local conn
  3. _Writer.execute_batch with no server executes all statements on conn
  4. write_server app /ping returns {"pong": true}
  5. write_server app /write executes a CREATE in a temp DB
  6. write_server app /health returns {"status": "ok", ...}
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, call, patch

import kuzu
import pytest

from dedalus.write_client import WriteClient
from dedalus.indexer import _Writer


# ---------------------------------------------------------------------------
# Test 1: WriteClient.is_available() returns False when server not running
# ---------------------------------------------------------------------------

def test_write_client_is_available_false_when_not_running():
    client = WriteClient("http://localhost:19999")  # nothing listening here
    assert client.is_available() is False


# ---------------------------------------------------------------------------
# Test 2: _Writer with no server_url routes execute() to local conn
# ---------------------------------------------------------------------------

def test_writer_local_mode_execute_routes_to_conn():
    mock_conn = MagicMock()
    writer = _Writer(mock_conn, server_url="")
    writer.execute("CREATE (:Test {id: '1'})")
    mock_conn.execute.assert_called_once_with("CREATE (:Test {id: '1'})", {})


def test_writer_local_mode_execute_with_params():
    mock_conn = MagicMock()
    writer = _Writer(mock_conn, server_url="")
    writer.execute("CREATE (:Test {id: $id})", {"id": "abc"})
    mock_conn.execute.assert_called_once_with("CREATE (:Test {id: $id})", {"id": "abc"})


# ---------------------------------------------------------------------------
# Test 3: _Writer.execute_batch with no server executes all statements on conn
# ---------------------------------------------------------------------------

def test_writer_local_mode_execute_batch_runs_all():
    mock_conn = MagicMock()
    writer = _Writer(mock_conn, server_url="")
    statements = [
        {"cypher": "CREATE (:A {id: '1'})", "params": {"id": "1"}},
        {"cypher": "CREATE (:B {id: '2'})", "params": {}},
    ]
    writer.execute_batch(statements)
    assert mock_conn.execute.call_count == 2
    mock_conn.execute.assert_any_call("CREATE (:A {id: '1'})", {"id": "1"})
    mock_conn.execute.assert_any_call("CREATE (:B {id: '2'})", {})


# ---------------------------------------------------------------------------
# Tests 4–6: write_server FastAPI app (using TestClient with a temp DB)
# ---------------------------------------------------------------------------

def _make_write_server_client():
    """Create a TestClient for the write server pointed at a fresh temp DB."""
    from fastapi.testclient import TestClient

    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test.db")

    # Patch the env var before importing/using the app so _startup picks it up.
    with patch.dict(os.environ, {"DEDALUS_DB_PATH": db_path}):
        # Reset module-level globals so each test gets a clean state.
        import dedalus.write_server as ws
        ws._db = None
        ws._conn = None

        client = TestClient(ws.app)
        # TestClient triggers lifespan (startup) automatically.
        return client, db_path, tmp_dir


def _fresh_client(db_path: str):
    """Return a TestClient for write_server with a fresh DB at *db_path*.

    Uses the context-manager form so the ASGI lifespan (startup event) runs
    and _conn is initialised before requests are made.
    """
    from fastapi.testclient import TestClient
    import dedalus.write_server as ws

    # Reset module-level globals so this test gets its own fresh connection.
    ws._db = None
    ws._conn = None

    return TestClient(ws.app, raise_server_exceptions=True)


def test_write_server_ping():
    """GET /ping should return {"pong": true}."""
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test_ping.db")

    with patch.dict(os.environ, {"DEDALUS_DB_PATH": db_path}):
        import dedalus.write_server as ws
        ws._db = None
        ws._conn = None
        from fastapi.testclient import TestClient
        with TestClient(ws.app) as client:
            response = client.get("/ping")

    assert response.status_code == 200
    assert response.json() == {"pong": True}


def test_write_server_health():
    """GET /health should return {"status": "ok", "db_path": ...}."""
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test_health.db")

    with patch.dict(os.environ, {"DEDALUS_DB_PATH": db_path}):
        import dedalus.write_server as ws
        ws._db = None
        ws._conn = None
        from fastapi.testclient import TestClient
        with TestClient(ws.app) as client:
            response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "db_path" in data


def test_write_server_write_executes_create():
    """POST /write with a CREATE TABLE statement should return ok=true."""
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test_write.db")

    with patch.dict(os.environ, {"DEDALUS_DB_PATH": db_path}):
        import dedalus.write_server as ws
        ws._db = None
        ws._conn = None
        from fastapi.testclient import TestClient
        with TestClient(ws.app) as client:
            # Create a simple node table then insert a row.
            response = client.post(
                "/write",
                json={
                    "statements": [
                        {"cypher": "CREATE NODE TABLE TestNode(id STRING, PRIMARY KEY(id))", "params": {}},
                        {"cypher": "CREATE (:TestNode {id: 'x1'})", "params": {}},
                    ]
                },
            )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["rows_affected"] == 2


def test_write_server_write_returns_error_on_bad_cypher():
    """POST /write with invalid Cypher should return ok=false with an error message."""
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test_err.db")

    with patch.dict(os.environ, {"DEDALUS_DB_PATH": db_path}):
        import dedalus.write_server as ws
        ws._db = None
        ws._conn = None
        from fastapi.testclient import TestClient
        with TestClient(ws.app) as client:
            response = client.post(
                "/write",
                json={"statements": [{"cypher": "THIS IS NOT VALID CYPHER", "params": {}}]},
            )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["error"] != ""
