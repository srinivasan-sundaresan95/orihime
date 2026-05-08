"""OQ8: Tests for JS/TS taint analysis — security_config additions and callee-source augmentation.

Written against the spec only (no implementation read before writing).

Spec coverage:
  1. SecurityConfig.is_source_method — suffix-match on new JS/TS source entries
  2. _BUILTIN_SINK_METHODS — new JS/TS sink entries (fetch, axios.*, readFile, exec*, spawn*, query, createReadStream)
  3. find_taint_flows — callee-source augmentation: handlers that call req.body / req.query become taint sources
  4. find_taint_paths — BFS seeds include callee-source methods, not only annotation-holders
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers (same pattern as test_find_taint_paths.py)
# ---------------------------------------------------------------------------

def _make_result(rows: list[list[Any]]) -> MagicMock:
    """Return a MagicMock that behaves like a kuzu QueryResult with *rows*."""
    result = MagicMock()
    row_iter = iter(rows)
    remaining = [len(rows)]

    def _has_next() -> bool:
        return remaining[0] > 0

    def _get_next() -> list:
        remaining[0] -= 1
        return next(row_iter)

    result.has_next.side_effect = _has_next
    result.get_next.side_effect = _get_next
    return result


def _empty() -> MagicMock:
    """Convenience: QueryResult with no rows."""
    return _make_result([])


# ---------------------------------------------------------------------------
# Class: TestJsTsSourceMethods
# ---------------------------------------------------------------------------

class TestJsTsSourceMethods:
    """is_source_method recognises all new JS/TS request-object source entries."""

    def _cfg(self):
        """Return a fresh SecurityConfig loaded with defaults (no YAML override)."""
        from orihime.security_config import load_security_config
        return load_security_config(config_path="/dev/null")

    def test_req_body_is_source_method(self):
        assert self._cfg().is_source_method("req.body") is True

    def test_req_query_is_source_method(self):
        assert self._cfg().is_source_method("req.query") is True

    def test_req_params_is_source_method(self):
        assert self._cfg().is_source_method("req.params") is True

    def test_req_headers_is_source_method(self):
        assert self._cfg().is_source_method("req.headers") is True

    def test_request_body_is_source_method(self):
        assert self._cfg().is_source_method("request.body") is True

    def test_searchparams_get_is_source_method(self):
        assert self._cfg().is_source_method("searchParams.get") is True

    def test_usesearchparams_is_source_method(self):
        assert self._cfg().is_source_method("useSearchParams") is True

    def test_normal_method_is_not_source(self):
        assert self._cfg().is_source_method("SomeService.doWork") is False

    def test_fqn_req_body_is_source(self):
        """Full-qualified name ending with a known source suffix → True."""
        assert self._cfg().is_source_method("com.example.controller.UserController.req.body") is True


# ---------------------------------------------------------------------------
# Class: TestJsTsSinkMethods
# ---------------------------------------------------------------------------

class TestJsTsSinkMethods:
    """is_sink_method recognises all new JS/TS sink entries."""

    def _cfg(self):
        from orihime.security_config import load_security_config
        return load_security_config(config_path="/dev/null")

    def test_fetch_is_sink(self):
        assert self._cfg().is_sink_method("fetch") is True

    def test_axios_get_is_sink(self):
        assert self._cfg().is_sink_method("axios.get") is True

    def test_readfile_is_sink(self):
        assert self._cfg().is_sink_method("fs.readFile") is True

    def test_exec_is_sink(self):
        assert self._cfg().is_sink_method("child_process.exec") is True

    def test_query_is_sink(self):
        assert self._cfg().is_sink_method("Client.query") is True

    def test_spawn_is_sink(self):
        assert self._cfg().is_sink_method("child_process.spawn") is True

    def test_createreadstream_is_sink(self):
        assert self._cfg().is_sink_method("fs.createReadStream") is True


# ---------------------------------------------------------------------------
# Class: TestFindTaintFlowsCalleeSource
# ---------------------------------------------------------------------------

class TestFindTaintFlowsCalleeSource:
    """find_taint_flows: handlers that call a source method are tainted even without annotations."""

    # Query order issued by find_taint_flows (augmented):
    #   1. Repo lookup                   → [["repo-1"]]
    #   2. Annotation-source query       → empty (no Spring annotations)
    #   3. Callee-source query           → one row: the JS handler
    #   4. CALLS-from-source (method-1)  → one row: the sink call

    REPO_NAME = "js-repo"
    REPO_ID = "repo-1"
    METHOD_ID = "method-1"
    HANDLER_FQN = "com.example.handler.UserHandler.handle"
    SINK_NAME = "executeQuery"
    SINK_FQN = "db.executeQuery"

    def _build_mock_cfg(self, is_source_method_fn=None, is_sink_method_fn=None):
        """Build a mock SecurityConfig whose predicates can be customised."""
        cfg = MagicMock()
        cfg.is_source_annotation.return_value = False
        if is_source_method_fn is not None:
            cfg.is_source_method.side_effect = is_source_method_fn
        else:
            cfg.is_source_method.return_value = False
        if is_sink_method_fn is not None:
            cfg.is_sink_method.side_effect = is_sink_method_fn
        else:
            cfg.is_sink_method.return_value = False
        return cfg

    def test_callee_source_handler_detected_as_tainted(self):
        """Handler that calls req.body appears in results when it also calls a DB sink."""
        conn = MagicMock()
        conn.execute.side_effect = [
            # 1. Repo lookup
            _make_result([[self.REPO_ID]]),
            # 2. Annotation-source query → empty
            _empty(),
            # 3. Callee-source query: one JS handler that calls req.body
            _make_result([[
                self.METHOD_ID,
                self.HANDLER_FQN,
                "body",          # s.name  (callee_name)
                "req.body",      # s.fqn   (callee_fqn)
                "/src/handler.js",
                10,
            ]]),
            # 4. CALLS-from-source for method-1: calls db.executeQuery
            _make_result([[
                self.SINK_NAME,    # s.name
                self.SINK_FQN,     # s.fqn
                0,                 # caller_arg_pos
                0,                 # callee_param_pos
            ]]),
        ]

        def _is_source(name: str) -> bool:
            return "req.body" in name or name == "body"

        def _is_sink(name: str) -> bool:
            return self.SINK_NAME in name or self.SINK_FQN in name

        cfg = self._build_mock_cfg(
            is_source_method_fn=_is_source,
            is_sink_method_fn=_is_sink,
        )

        with (
            patch("orihime.mcp_server._get_connection", return_value=conn),
            patch("orihime.security_config.get_security_config", return_value=cfg),
        ):
            import orihime.mcp_server as mcp
            results = mcp.find_taint_flows(self.REPO_NAME)

        assert len(results) == 1
        r = results[0]
        assert "error" not in r
        assert r["source_method_fqn"] == self.HANDLER_FQN
        assert self.SINK_NAME in r["sink_method_name"]

    def test_no_callee_sources_no_results(self):
        """When the callee-source query also returns nothing, the result is []."""
        conn = MagicMock()
        conn.execute.side_effect = [
            # 1. Repo lookup
            _make_result([[self.REPO_ID]]),
            # 2. Annotation-source query → empty
            _empty(),
            # 3. Callee-source query → also empty
            _empty(),
            # No further queries expected (short-circuit on empty sources)
        ]

        cfg = self._build_mock_cfg()

        with (
            patch("orihime.mcp_server._get_connection", return_value=conn),
            patch("orihime.security_config.get_security_config", return_value=cfg),
        ):
            import orihime.mcp_server as mcp
            results = mcp.find_taint_flows(self.REPO_NAME)

        assert results == []


# ---------------------------------------------------------------------------
# Class: TestFindTaintPathsCalleeSource
# ---------------------------------------------------------------------------

class TestFindTaintPathsCalleeSource:
    """find_taint_paths: BFS seeds include methods that call a source method."""

    # Query order for find_taint_paths (augmented):
    #   1. Repo lookup
    #   2. CALLS adjacency list (all in-repo edges)
    #   3. id → fqn mapping
    #   4. Annotation-source query (Spring annotations) → empty
    #   5. Callee-source query (methods calling req.body / req.query) → handler
    #
    # BFS then walks adjacency map and finds sink.

    REPO_NAME = "js-paths-repo"
    REPO_ID = "repo-js"
    M_HANDLER = "m-handler"
    M_SINK = "m-sink"
    HANDLER_FQN = "com.example.handler.RequestHandler.handleRequest"
    SINK_FQN = "db.query"
    # The req.body accessor has its own ID in the graph
    M_REQ_BODY = "m-req-body"
    REQ_BODY_FQN = "req.body"

    def test_taint_paths_js_handler_seeded_via_callee(self):
        """Method that calls req.body is seeded; BFS finds one 1-hop path to a sink."""
        conn = MagicMock()
        conn.execute.side_effect = [
            # 1. Repo lookup
            _make_result([[self.REPO_ID]]),
            # 2. CALLS adjacency: handler→req.body edge AND handler→sink edge
            _make_result([
                [self.M_HANDLER, self.M_REQ_BODY, "body"],      # handler accesses req.body
                [self.M_HANDLER, self.M_SINK, "query"],          # handler calls db.query (sink)
            ]),
            # 3. id → fqn mapping
            _make_result([
                [self.M_HANDLER, self.HANDLER_FQN],
                [self.M_REQ_BODY, self.REQ_BODY_FQN],
                [self.M_SINK, self.SINK_FQN],
            ]),
            # 4. Annotation-source query → empty (no Spring annotations)
            _empty(),
            # No 5th query: JS/TS callee-source seeding reuses the adj+id_to_fqn
            # already loaded in steps 2–3, so no extra conn.execute is issued.
        ]

        def _is_source_method(name: str) -> bool:
            return "req.body" in name or name == "body"

        def _is_source_annotation(ann: str) -> bool:
            return False

        def _is_sink_method(name: str) -> bool:
            return "query" in name

        def _is_sanitizer_method(name: str) -> bool:
            return False

        cfg = MagicMock()
        cfg.is_source_annotation.side_effect = _is_source_annotation
        cfg.is_source_method.side_effect = _is_source_method
        cfg.is_sink_method.side_effect = _is_sink_method
        cfg.is_sanitizer_method.side_effect = _is_sanitizer_method

        with (
            patch("orihime.mcp_server._get_connection", return_value=conn),
            patch("orihime.security_config.get_security_config", return_value=cfg),
        ):
            import orihime.mcp_server as mcp
            results = mcp.find_taint_paths(self.REPO_NAME)

        # Expect exactly one result: handler → db.query (path_length=1)
        assert len(results) == 1
        r = results[0]
        assert "error" not in r
        assert r["source_method_fqn"] == self.HANDLER_FQN
        assert r["path_length"] == 1
        assert self.HANDLER_FQN in r["call_chain"]
        # The sink must be in the chain
        sink_fqn = r["sink_method_fqn"]
        assert "query" in sink_fqn
        assert r["call_chain"][-1] == sink_fqn
