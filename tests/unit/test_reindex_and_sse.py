"""Unit tests for POST /reindex (write_server.py) and serve-sse (__main__.py).

Feature 1 — POST /reindex
--------------------------
Accepts: {"repo_path": str, "repo_name": str, "branch": str, "force": bool}
Calls:   orihime.indexer.index_repo(repo_path, repo_name, db_path, force=..., branch=...)
Returns: {"ok": True, "summary": {...}, "error": ""}  on success
         {"ok": False, "summary": {}, "error": "..."}  on failure
Defaults: branch="master", force=False
db_path:  from ORIHIME_DB_PATH env var (default ~/.orihime/orihime.db)

Feature 2 — serve-sse command
------------------------------
python -m orihime serve-sse [--port 7702] [--db PATH]
Sets ORIHIME_DB_PATH via os.environ.setdefault, sets mcp.settings.port, then calls mcp.run(transport="sse").
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
import httpx
from httpx import ASGITransport

from orihime.write_server import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_async_client() -> httpx.AsyncClient:
    """Return an AsyncClient backed by the write_server ASGI app."""
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# Class TestReindexEndpoint
# ---------------------------------------------------------------------------

class TestReindexEndpoint:

    @pytest.mark.asyncio
    async def test_reindex_success(self):
        """Mock index_repo to return summary; assert ok=True and correct files count."""
        with patch("orihime.write_server.index_repo", return_value={"files": 5, "methods": 30}):
            async with _make_async_client() as client:
                resp = await client.post("/reindex", json={
                    "repo_path": "/some/path",
                    "repo_name": "my-repo",
                })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["summary"]["files"] == 5

    @pytest.mark.asyncio
    async def test_reindex_failure(self):
        """Mock index_repo to raise RuntimeError; assert ok=False and error message present."""
        with patch("orihime.write_server.index_repo", side_effect=RuntimeError("db locked")):
            async with _make_async_client() as client:
                resp = await client.post("/reindex", json={
                    "repo_path": "/some/path",
                    "repo_name": "my-repo",
                })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "db locked" in data["error"]

    @pytest.mark.asyncio
    async def test_reindex_defaults(self):
        """POST with only repo_path and repo_name; branch defaults to master, force to False."""
        captured = {}

        def _capture(repo_path, repo_name, db_path, force=False, branch="master"):
            captured["branch"] = branch
            captured["force"] = force
            return {"files": 0, "methods": 0}

        with patch("orihime.write_server.index_repo", side_effect=_capture):
            async with _make_async_client() as client:
                await client.post("/reindex", json={
                    "repo_path": "/some/path",
                    "repo_name": "my-repo",
                })
        assert captured["branch"] == "master"
        assert captured["force"] is False

    @pytest.mark.asyncio
    async def test_reindex_custom_branch(self):
        """POST with branch=feature-x and force=True; verify forwarded to index_repo."""
        captured = {}

        def _capture(repo_path, repo_name, db_path, force=False, branch="master"):
            captured["branch"] = branch
            captured["force"] = force
            return {"files": 1, "methods": 2}

        with patch("orihime.write_server.index_repo", side_effect=_capture):
            async with _make_async_client() as client:
                await client.post("/reindex", json={
                    "repo_path": "/some/path",
                    "repo_name": "my-repo",
                    "branch": "feature-x",
                    "force": True,
                })
        assert captured["branch"] == "feature-x"
        assert captured["force"] is True

    @pytest.mark.asyncio
    async def test_reindex_uses_env_db_path(self):
        """ORIHIME_DB_PATH env var is forwarded as db_path to index_repo."""
        captured = {}

        def _capture(repo_path, repo_name, db_path, force=False, branch="master"):
            captured["db_path"] = db_path
            return {"files": 0, "methods": 0}

        original = os.environ.pop("ORIHIME_DB_PATH", None)
        try:
            os.environ["ORIHIME_DB_PATH"] = "/tmp/test.db"
            with patch("orihime.write_server.index_repo", side_effect=_capture):
                async with _make_async_client() as client:
                    await client.post("/reindex", json={
                        "repo_path": "/some/path",
                        "repo_name": "my-repo",
                    })
        finally:
            if original is None:
                os.environ.pop("ORIHIME_DB_PATH", None)
            else:
                os.environ["ORIHIME_DB_PATH"] = original

        assert captured["db_path"] == "/tmp/test.db"


# ---------------------------------------------------------------------------
# Class TestServeSSECommand
# ---------------------------------------------------------------------------

class TestServeSSECommand:

    def test_serve_sse_default_port(self):
        """serve-sse with no --port should set mcp.settings.port=7702 then call mcp.run(transport='sse')."""
        from orihime.__main__ import main
        from orihime.mcp_server import mcp

        with patch("sys.argv", ["orihime", "serve-sse"]), \
             patch("orihime.mcp_server.mcp.run") as mock_run, \
             patch("os.environ.setdefault"):
            main()

        assert mcp.settings.port == 7702
        mock_run.assert_called_once_with(transport="sse")

    def test_serve_sse_custom_port(self):
        """serve-sse --port 8080 should set mcp.settings.port=8080 then call mcp.run(transport='sse')."""
        from orihime.__main__ import main
        from orihime.mcp_server import mcp

        with patch("sys.argv", ["orihime", "serve-sse", "--port", "8080"]), \
             patch("orihime.mcp_server.mcp.run") as mock_run, \
             patch("os.environ.setdefault"):
            main()

        assert mcp.settings.port == 8080
        mock_run.assert_called_once_with(transport="sse")

    def test_serve_sse_sets_db_env(self):
        """serve-sse --db /custom/orihime.db should call os.environ.setdefault with that path."""
        from orihime.__main__ import main

        with patch("sys.argv", ["orihime", "serve-sse", "--db", "/custom/orihime.db"]), \
             patch("orihime.mcp_server.mcp.run"), \
             patch("os.environ.setdefault") as mock_setdefault:
            main()

        mock_setdefault.assert_any_call("ORIHIME_DB_PATH", "/custom/orihime.db")
