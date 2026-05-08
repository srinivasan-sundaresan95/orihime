"""Unit tests for find_taint_paths — multi-hop BFS taint analysis.

Tests are written against the contract only; the implementation may not yet
exist.  T1-T3 are expected to pass even before the function is coded because
they exercise the null/not-found/no-sources early-exit branches.  T4-T18
validate correctness of the BFS, sanitizer pruning, deduplication, cycle
prevention, sorting, and error handling.

Query order issued by the implementation (one execute() per step):
  1. repo_id lookup
  2. CALLS adjacency list   (caller_id, callee_id, callee_fqn)
  3. id → fqn lookup        (method_id, fqn)
  4. source methods         (method_id, fqn, annotations, file_path, line_start)
"""
from __future__ import annotations

from collections import deque
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build fake QueryResult objects
# ---------------------------------------------------------------------------

def _make_result(rows: list[list[Any]]) -> MagicMock:
    """Return a MagicMock that behaves like a kuzu QueryResult with *rows*."""
    result = MagicMock()
    row_iter = iter(rows)
    remaining = [len(rows)]  # mutable counter

    def _has_next():
        return remaining[0] > 0

    def _get_next():
        remaining[0] -= 1
        return next(row_iter)

    result.has_next.side_effect = _has_next
    result.get_next.side_effect = _get_next
    return result


def _empty() -> MagicMock:
    """Convenience: QueryResult with no rows."""
    return _make_result([])


# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------

REPO_NAME = "test-repo"
REPO_ID = "repo-1"

# FQNs that trigger built-in patterns
SOURCE_FQN = "com.example.controller.UserController.getUser"
SOURCE_FQN_B = "com.example.controller.UserController.createUser"
SINK_FQN = "com.example.dao.StatementHelper.execute"        # matches Statement.execute suffix
SINK_FQN_2 = "com.example.dao.StatementHelper.executeQuery"  # second distinct sink
SANITIZER_FQN = "com.example.util.HtmlUtils.htmlEscape"      # matches HtmlUtils.htmlEscape
MID_SOURCE = "m-src"
MID_B = "m-b"
MID_C = "m-c"
MID_SINK = "m-sink"
MID_SINK_2 = "m-sink2"
MID_SANITIZER = "m-san"

# ---------------------------------------------------------------------------
# T1 — null connection returns empty list
# ---------------------------------------------------------------------------

def test_null_connection_returns_empty():
    """When _get_connection() returns None the function must return []."""
    with patch("orihime.mcp_server._get_connection", return_value=None):
        import orihime.mcp_server as mcp
        result = mcp.find_taint_paths(REPO_NAME)
    assert result == []


# ---------------------------------------------------------------------------
# T2 — repo not found returns empty list
# ---------------------------------------------------------------------------

def test_repo_not_found_returns_empty():
    """When the repo_id query returns no rows the function must return []."""
    conn = MagicMock()
    conn.execute.return_value = _empty()  # repo_id query finds nothing
    with patch("orihime.mcp_server._get_connection", return_value=conn):
        import orihime.mcp_server as mcp
        result = mcp.find_taint_paths(REPO_NAME)
    assert result == []


# ---------------------------------------------------------------------------
# T3 — no source annotations returns empty list
# ---------------------------------------------------------------------------

def test_no_source_annotations_returns_empty():
    """When no method in the repo carries a taint-source annotation → []."""
    conn = MagicMock()
    conn.execute.side_effect = [
        _make_result([[REPO_ID]]),   # 1. repo_id found
        _empty(),                    # 2. CALLS adjacency — irrelevant
        _empty(),                    # 3. id→fqn lookup — irrelevant
        _empty(),                    # 4. source methods — none
    ]
    with patch("orihime.mcp_server._get_connection", return_value=conn):
        import orihime.mcp_server as mcp
        result = mcp.find_taint_paths(REPO_NAME)
    assert result == []


# ---------------------------------------------------------------------------
# T4 — direct sink (path_length=1): source → sink
# ---------------------------------------------------------------------------

def test_direct_sink_path_length_1():
    """A(source,@RequestParam) → B(sink): one result, call_chain=[A,B], path_length=1."""
    conn = MagicMock()
    conn.execute.side_effect = [
        _make_result([[REPO_ID]]),                              # 1. repo_id
        _make_result([[MID_SOURCE, MID_SINK, SINK_FQN]]),      # 2. CALLS adjacency
        _make_result([                                          # 3. id→fqn
            [MID_SOURCE, SOURCE_FQN],
            [MID_SINK, SINK_FQN],
        ]),
        _make_result([[                                         # 4. sources
            MID_SOURCE, SOURCE_FQN,
            ["RequestParam", "Transactional"],
            "src/main/java/UserController.java", 42,
        ]]),
    ]
    with patch("orihime.mcp_server._get_connection", return_value=conn):
        import orihime.mcp_server as mcp
        result = mcp.find_taint_paths(REPO_NAME)

    assert len(result) == 1
    r = result[0]
    assert "error" not in r
    assert r["source_method_fqn"] == SOURCE_FQN
    assert r["sink_method_fqn"] == SINK_FQN
    assert r["path_length"] == 1
    assert r["call_chain"] == [SOURCE_FQN, SINK_FQN]
    assert r["sanitizer_pruned"] is False
    assert r["file_path"] == "src/main/java/UserController.java"
    assert r["line_start"] == 42


# ---------------------------------------------------------------------------
# T5 — two-hop path: source → intermediate → sink
# ---------------------------------------------------------------------------

def test_two_hop_path():
    """A(source) → B(nothing) → C(sink): call_chain=[A,B,C], path_length=2."""
    conn = MagicMock()
    conn.execute.side_effect = [
        _make_result([[REPO_ID]]),
        _make_result([                                         # 2. CALLS adjacency
            [MID_SOURCE, MID_B, "com.example.service.UserService.process"],
            [MID_B, MID_SINK, SINK_FQN],
        ]),
        _make_result([                                         # 3. id→fqn
            [MID_SOURCE, SOURCE_FQN],
            [MID_B, "com.example.service.UserService.process"],
            [MID_SINK, SINK_FQN],
        ]),
        _make_result([[                                         # 4. sources
            MID_SOURCE, SOURCE_FQN, ["RequestParam"],
            "src/main/java/UserController.java", 10,
        ]]),
    ]
    with patch("orihime.mcp_server._get_connection", return_value=conn):
        import orihime.mcp_server as mcp
        result = mcp.find_taint_paths(REPO_NAME)

    assert len(result) == 1
    r = result[0]
    assert r["path_length"] == 2
    assert r["call_chain"] == [
        SOURCE_FQN,
        "com.example.service.UserService.process",
        SINK_FQN,
    ]


# ---------------------------------------------------------------------------
# T6 — sanitizer at depth 1 prunes entirely: source → sanitizer → (nothing)
# ---------------------------------------------------------------------------

def test_sanitizer_at_depth1_prunes_to_empty():
    """A → S(sanitizer): sanitizer is not enqueued; returns []."""
    conn = MagicMock()
    conn.execute.side_effect = [
        _make_result([[REPO_ID]]),
        _make_result([[MID_SOURCE, MID_SANITIZER, SANITIZER_FQN]]),   # 2. CALLS
        _make_result([                                                  # 3. id→fqn
            [MID_SOURCE, SOURCE_FQN],
            [MID_SANITIZER, SANITIZER_FQN],
        ]),
        _make_result([[                                                  # 4. sources
            MID_SOURCE, SOURCE_FQN, ["RequestParam"],
            "src/UserController.java", 5,
        ]]),
    ]
    with patch("orihime.mcp_server._get_connection", return_value=conn):
        import orihime.mcp_server as mcp
        result = mcp.find_taint_paths(REPO_NAME)

    assert result == []


# ---------------------------------------------------------------------------
# T7 — sanitizer prunes one branch; other branch survives
# ---------------------------------------------------------------------------

def test_sanitizer_prunes_one_branch_other_survives():
    """A → S(sanitizer) AND A → B → C(sink): only [A,B,C] is returned."""
    MID_B2 = "m-b2"
    B2_FQN = "com.example.service.SafeHelper.doWork"
    conn = MagicMock()
    conn.execute.side_effect = [
        _make_result([[REPO_ID]]),
        _make_result([                                           # 2. CALLS adjacency
            [MID_SOURCE, MID_SANITIZER, SANITIZER_FQN],         # pruned branch
            [MID_SOURCE, MID_B2, B2_FQN],                       # surviving branch
            [MID_B2, MID_SINK, SINK_FQN],
        ]),
        _make_result([                                           # 3. id→fqn
            [MID_SOURCE, SOURCE_FQN],
            [MID_SANITIZER, SANITIZER_FQN],
            [MID_B2, B2_FQN],
            [MID_SINK, SINK_FQN],
        ]),
        _make_result([[                                           # 4. sources
            MID_SOURCE, SOURCE_FQN, ["RequestParam"],
            "src/UserController.java", 20,
        ]]),
    ]
    with patch("orihime.mcp_server._get_connection", return_value=conn):
        import orihime.mcp_server as mcp
        result = mcp.find_taint_paths(REPO_NAME)

    assert len(result) == 1
    r = result[0]
    assert r["call_chain"] == [SOURCE_FQN, B2_FQN, SINK_FQN]
    assert r["path_length"] == 2


# ---------------------------------------------------------------------------
# T8 — max_depth=0 returns empty list
# ---------------------------------------------------------------------------

def test_max_depth_zero_returns_empty():
    """max_depth=0 means no hops allowed; even a direct sink is unreachable."""
    conn = MagicMock()
    conn.execute.side_effect = [
        _make_result([[REPO_ID]]),
        _make_result([[MID_SOURCE, MID_SINK, SINK_FQN]]),      # 2. CALLS
        _make_result([                                          # 3. id→fqn
            [MID_SOURCE, SOURCE_FQN],
            [MID_SINK, SINK_FQN],
        ]),
        _make_result([[                                         # 4. sources
            MID_SOURCE, SOURCE_FQN, ["RequestParam"],
            "src/UserController.java", 1,
        ]]),
    ]
    with patch("orihime.mcp_server._get_connection", return_value=conn):
        import orihime.mcp_server as mcp
        result = mcp.find_taint_paths(REPO_NAME, max_depth=0)

    assert result == []


# ---------------------------------------------------------------------------
# T9 — max_depth=15 is capped to 10 (no exception; runs correctly)
# ---------------------------------------------------------------------------

def test_max_depth_capped_at_10():
    """max_depth values above 10 are silently capped to 10; no exception raised."""
    conn = MagicMock()
    conn.execute.side_effect = [
        _make_result([[REPO_ID]]),
        _make_result([[MID_SOURCE, MID_SINK, SINK_FQN]]),      # 2. CALLS
        _make_result([                                          # 3. id→fqn
            [MID_SOURCE, SOURCE_FQN],
            [MID_SINK, SINK_FQN],
        ]),
        _make_result([[                                         # 4. sources
            MID_SOURCE, SOURCE_FQN, ["RequestParam"],
            "src/UserController.java", 1,
        ]]),
    ]
    with patch("orihime.mcp_server._get_connection", return_value=conn):
        import orihime.mcp_server as mcp
        # Must not raise; must find the direct sink normally
        result = mcp.find_taint_paths(REPO_NAME, max_depth=15)

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["path_length"] == 1


# ---------------------------------------------------------------------------
# T10 — sink does NOT prune BFS: A → B(sink) → C(sink) yields two results
# ---------------------------------------------------------------------------

def test_sink_does_not_prune_bfs_two_sinks_in_chain():
    """BFS continues past a sink: A→B(sink)→C(sink) → two findings."""
    MID_C2 = "m-c2"
    C2_FQN = "com.example.dao.StatementHelper.executeQuery"   # also a built-in sink
    conn = MagicMock()
    conn.execute.side_effect = [
        _make_result([[REPO_ID]]),
        _make_result([                                           # 2. CALLS
            [MID_SOURCE, MID_SINK, SINK_FQN],
            [MID_SINK, MID_C2, C2_FQN],
        ]),
        _make_result([                                           # 3. id→fqn
            [MID_SOURCE, SOURCE_FQN],
            [MID_SINK, SINK_FQN],
            [MID_C2, C2_FQN],
        ]),
        _make_result([[                                           # 4. sources
            MID_SOURCE, SOURCE_FQN, ["RequestParam"],
            "src/UserController.java", 1,
        ]]),
    ]
    with patch("orihime.mcp_server._get_connection", return_value=conn):
        import orihime.mcp_server as mcp
        result = mcp.find_taint_paths(REPO_NAME)

    assert len(result) == 2
    chains = [r["call_chain"] for r in result]
    assert [SOURCE_FQN, SINK_FQN] in chains
    assert [SOURCE_FQN, SINK_FQN, C2_FQN] in chains


# ---------------------------------------------------------------------------
# T11 — multiple paths to same sink: A→B→D AND A→C→D
# ---------------------------------------------------------------------------

def test_multiple_paths_to_same_sink():
    """Two distinct call chains each reaching the same sink → two results."""
    MID_C3 = "m-c3"
    C3_FQN = "com.example.service.PathB.process"
    conn = MagicMock()
    conn.execute.side_effect = [
        _make_result([[REPO_ID]]),
        _make_result([                                            # 2. CALLS
            [MID_SOURCE, MID_B, "com.example.service.PathA.process"],
            [MID_SOURCE, MID_C3, C3_FQN],
            [MID_B, MID_SINK, SINK_FQN],
            [MID_C3, MID_SINK, SINK_FQN],
        ]),
        _make_result([                                            # 3. id→fqn
            [MID_SOURCE, SOURCE_FQN],
            [MID_B, "com.example.service.PathA.process"],
            [MID_C3, C3_FQN],
            [MID_SINK, SINK_FQN],
        ]),
        _make_result([[                                            # 4. sources
            MID_SOURCE, SOURCE_FQN, ["RequestParam"],
            "src/UserController.java", 1,
        ]]),
    ]
    with patch("orihime.mcp_server._get_connection", return_value=conn):
        import orihime.mcp_server as mcp
        result = mcp.find_taint_paths(REPO_NAME)

    assert len(result) == 2
    chains = [tuple(r["call_chain"]) for r in result]
    assert (SOURCE_FQN, "com.example.service.PathA.process", SINK_FQN) in chains
    assert (SOURCE_FQN, C3_FQN, SINK_FQN) in chains


# ---------------------------------------------------------------------------
# T12 — duplicate call chains are deduplicated to one entry
# ---------------------------------------------------------------------------

def test_duplicate_call_chains_deduplicated():
    """If BFS would produce identical call_chain tuples, only one is kept."""
    # Simulate two CALLS rows producing the same logical path (e.g. duplicated edge)
    conn = MagicMock()
    conn.execute.side_effect = [
        _make_result([[REPO_ID]]),
        _make_result([                                            # 2. CALLS (same edge twice)
            [MID_SOURCE, MID_SINK, SINK_FQN],
            [MID_SOURCE, MID_SINK, SINK_FQN],
        ]),
        _make_result([                                            # 3. id→fqn
            [MID_SOURCE, SOURCE_FQN],
            [MID_SINK, SINK_FQN],
        ]),
        _make_result([[                                            # 4. sources
            MID_SOURCE, SOURCE_FQN, ["RequestParam"],
            "src/UserController.java", 1,
        ]]),
    ]
    with patch("orihime.mcp_server._get_connection", return_value=conn):
        import orihime.mcp_server as mcp
        result = mcp.find_taint_paths(REPO_NAME)

    # Identical (source_fqn, sink_fqn, call_chain) must yield exactly one result
    assert len(result) == 1
    assert result[0]["call_chain"] == [SOURCE_FQN, SINK_FQN]


# ---------------------------------------------------------------------------
# T13 — cycle prevention: A→B→A (back to source). B also calls sink.
# ---------------------------------------------------------------------------

def test_cycle_prevention_no_infinite_loop():
    """A→B→A (cycle) with B→C(sink): one result [A,B,C]; no infinite loop."""
    conn = MagicMock()
    conn.execute.side_effect = [
        _make_result([[REPO_ID]]),
        _make_result([                                            # 2. CALLS
            [MID_SOURCE, MID_B, "com.example.service.CycleHelper.doSomething"],
            [MID_B, MID_SOURCE, SOURCE_FQN],                     # back-edge → cycle
            [MID_B, MID_SINK, SINK_FQN],
        ]),
        _make_result([                                            # 3. id→fqn
            [MID_SOURCE, SOURCE_FQN],
            [MID_B, "com.example.service.CycleHelper.doSomething"],
            [MID_SINK, SINK_FQN],
        ]),
        _make_result([[                                            # 4. sources
            MID_SOURCE, SOURCE_FQN, ["RequestParam"],
            "src/UserController.java", 1,
        ]]),
    ]
    with patch("orihime.mcp_server._get_connection", return_value=conn):
        import orihime.mcp_server as mcp
        result = mcp.find_taint_paths(REPO_NAME)

    assert len(result) == 1
    assert result[0]["call_chain"] == [
        SOURCE_FQN,
        "com.example.service.CycleHelper.doSomething",
        SINK_FQN,
    ]


# ---------------------------------------------------------------------------
# T14 — source that is also a sink emits no length-0 path when it calls nothing
# ---------------------------------------------------------------------------

def test_source_that_is_also_sink_no_length_zero_path():
    """If SOURCE_FQN matches a sink pattern but calls nothing, no result is emitted."""
    # Use a FQN that happens to match Statement.execute so the source IS a sink
    SOURCE_SINK_FQN = "com.example.controller.StatementHelper.execute"
    MID_SS = "m-ss"
    conn = MagicMock()
    conn.execute.side_effect = [
        _make_result([[REPO_ID]]),
        _empty(),                                                # 2. CALLS — no outgoing edges
        _make_result([[MID_SS, SOURCE_SINK_FQN]]),              # 3. id→fqn
        _make_result([[                                          # 4. sources
            MID_SS, SOURCE_SINK_FQN, ["RequestParam"],
            "src/UserController.java", 1,
        ]]),
    ]
    with patch("orihime.mcp_server._get_connection", return_value=conn):
        import orihime.mcp_server as mcp
        result = mcp.find_taint_paths(REPO_NAME)

    assert result == []


# ---------------------------------------------------------------------------
# T15 — sort order: path_length=1 before path_length=2
# ---------------------------------------------------------------------------

def test_sort_order_path_length_ascending():
    """Results are sorted path_length asc; the direct sink must come first."""
    MID_INTER = "m-inter"
    INTER_FQN = "com.example.service.UserService.process"
    conn = MagicMock()
    conn.execute.side_effect = [
        _make_result([[REPO_ID]]),
        _make_result([                                            # 2. CALLS
            [MID_SOURCE, MID_SINK, SINK_FQN],                    # direct: length 1
            [MID_SOURCE, MID_INTER, INTER_FQN],
            [MID_INTER, SINK_FQN_2, SINK_FQN_2],                 # need sink_2 in adj
        ]),
        _make_result([                                            # 3. id→fqn
            [MID_SOURCE, SOURCE_FQN],
            [MID_SINK, SINK_FQN],
            [MID_INTER, INTER_FQN],
            [MID_SINK_2, SINK_FQN_2],
        ]),
        _make_result([[                                            # 4. sources
            MID_SOURCE, SOURCE_FQN, ["RequestParam"],
            "src/UserController.java", 1,
        ]]),
    ]
    with patch("orihime.mcp_server._get_connection", return_value=conn):
        import orihime.mcp_server as mcp
        result = mcp.find_taint_paths(REPO_NAME)

    assert len(result) >= 1
    lengths = [r["path_length"] for r in result]
    assert lengths == sorted(lengths), "Results must be sorted by path_length ascending"
    assert result[0]["path_length"] == 1


# ---------------------------------------------------------------------------
# T16 — source_annotations filtered to only taint-source annotations
# ---------------------------------------------------------------------------

def test_source_annotations_filtered_to_taint_only():
    """Method annotated with [@RequestParam, @Transactional]: result has only @RequestParam."""
    conn = MagicMock()
    conn.execute.side_effect = [
        _make_result([[REPO_ID]]),
        _make_result([[MID_SOURCE, MID_SINK, SINK_FQN]]),       # 2. CALLS
        _make_result([                                           # 3. id→fqn
            [MID_SOURCE, SOURCE_FQN],
            [MID_SINK, SINK_FQN],
        ]),
        _make_result([[                                          # 4. sources (mixed annotations)
            MID_SOURCE, SOURCE_FQN,
            ["RequestParam", "Transactional", "Override"],
            "src/UserController.java", 7,
        ]]),
    ]
    with patch("orihime.mcp_server._get_connection", return_value=conn):
        import orihime.mcp_server as mcp
        result = mcp.find_taint_paths(REPO_NAME)

    assert len(result) == 1
    src_anns = result[0]["source_annotations"]
    assert "RequestParam" in src_anns
    # Non-taint annotations must be absent
    assert "Transactional" not in src_anns
    assert "Override" not in src_anns


# ---------------------------------------------------------------------------
# T17 — exception returns error dict
# ---------------------------------------------------------------------------

def test_exception_returns_error_dict():
    """RuntimeError during conn.execute → [{"error": "<message>"}]."""
    conn = MagicMock()
    conn.execute.side_effect = RuntimeError("simulated DB failure")
    with patch("orihime.mcp_server._get_connection", return_value=conn):
        import orihime.mcp_server as mcp
        result = mcp.find_taint_paths(REPO_NAME)

    assert len(result) == 1
    assert "error" in result[0]
    assert "simulated DB failure" in result[0]["error"]


# ---------------------------------------------------------------------------
# T18 — line_start None in DB → result has line_start=0
# ---------------------------------------------------------------------------

def test_line_start_none_in_db_becomes_zero():
    """When the DB returns None for line_start the result must have line_start=0."""
    conn = MagicMock()
    conn.execute.side_effect = [
        _make_result([[REPO_ID]]),
        _make_result([[MID_SOURCE, MID_SINK, SINK_FQN]]),       # 2. CALLS
        _make_result([                                           # 3. id→fqn
            [MID_SOURCE, SOURCE_FQN],
            [MID_SINK, SINK_FQN],
        ]),
        _make_result([[                                          # 4. sources — line_start=None
            MID_SOURCE, SOURCE_FQN, ["RequestParam"],
            "src/UserController.java", None,
        ]]),
    ]
    with patch("orihime.mcp_server._get_connection", return_value=conn):
        import orihime.mcp_server as mcp
        result = mcp.find_taint_paths(REPO_NAME)

    assert len(result) == 1
    assert result[0]["line_start"] == 0
