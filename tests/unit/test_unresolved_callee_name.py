"""Unit tests for P4-1: callee_name stored on CallEdge (no DB required)."""
from __future__ import annotations

import uuid

import orihime.java_extractor  # noqa: F401 — triggers register()
from orihime.language import get_parser
from orihime.resolver import CallEdge, build_fqn_index, resolve_calls


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_java(source: bytes):
    parser = get_parser("java")
    return parser.parse(source), source


def _method_dict(name: str, fqn: str, line_start: int = 1) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "fqn": fqn,
        "class_id": str(uuid.uuid4()),
        "file_id": "file1",
        "repo_id": "repo1",
        "line_start": line_start,
        "is_suspend": False,
        "annotations": [],
    }


# ---------------------------------------------------------------------------
# Test 1: UNRESOLVED_CALL edge carries callee_name
# ---------------------------------------------------------------------------

def test_unresolved_call_edge_carries_callee_name():
    """An UNRESOLVED_CALL edge must have callee_name set to the invoked method name."""
    source = b"""
package com.example;
public class Wrapper {
    public void foo() {
        unknownMethod();
    }
}
"""
    tree, source_bytes = _parse_java(source)

    m_foo = _method_dict("foo", "com.example.Wrapper.foo", line_start=4)
    methods = [m_foo]
    # unknownMethod is NOT in fqn_index
    fqn_index = build_fqn_index(methods)

    edges = resolve_calls(tree, source_bytes, methods, fqn_index, "file1", "repo1")

    unresolved = [e for e in edges if e.edge_type == "UNRESOLVED_CALL"]
    assert len(unresolved) >= 1, f"Expected at least one UNRESOLVED_CALL edge. All edges: {edges}"

    names = {e.callee_name for e in unresolved}
    assert "unknownMethod" in names, (
        f"Expected callee_name='unknownMethod' on an UNRESOLVED_CALL edge. Got names: {names}"
    )


# ---------------------------------------------------------------------------
# Test 2: CALLS edge also carries callee_name
# ---------------------------------------------------------------------------

def test_calls_edge_carries_callee_name():
    """A resolved CALLS edge must also have callee_name set to the invoked method name."""
    source = b"""
package com.example;
public class Pair {
    public void alpha() {
        beta();
    }
    public void beta() {}
}
"""
    tree, source_bytes = _parse_java(source)

    m_alpha = _method_dict("alpha", "com.example.Pair.alpha", line_start=4)
    m_beta  = _method_dict("beta",  "com.example.Pair.beta",  line_start=7)
    methods = [m_alpha, m_beta]
    fqn_index = build_fqn_index(methods)

    edges = resolve_calls(tree, source_bytes, methods, fqn_index, "file1", "repo1")

    calls_edges = [e for e in edges if e.edge_type == "CALLS"]
    assert len(calls_edges) >= 1, f"Expected at least one CALLS edge. All edges: {edges}"

    # Find the alpha->beta edge
    alpha_to_beta = [
        e for e in calls_edges
        if e.caller_id == m_alpha["id"] and e.callee_id == m_beta["id"]
    ]
    assert alpha_to_beta, (
        f"Expected a CALLS edge from alpha to beta. CALLS edges: {calls_edges}"
    )
    assert alpha_to_beta[0].callee_name == "beta", (
        f"Expected callee_name='beta', got '{alpha_to_beta[0].callee_name}'"
    )


# ---------------------------------------------------------------------------
# Test 3: Multiple unresolved calls have distinct callee_names
# ---------------------------------------------------------------------------

def test_multiple_unresolved_calls_have_distinct_names():
    """Three calls to unknown methods each yield an UNRESOLVED_CALL with a distinct callee_name."""
    source = b"""
package com.example;
public class Multi {
    public void caller() {
        libA();
        libB();
        libC();
    }
}
"""
    tree, source_bytes = _parse_java(source)

    m_caller = _method_dict("caller", "com.example.Multi.caller", line_start=4)
    methods = [m_caller]
    # libA, libB, libC are NOT in fqn_index
    fqn_index = build_fqn_index(methods)

    edges = resolve_calls(tree, source_bytes, methods, fqn_index, "file1", "repo1")

    unresolved = [e for e in edges if e.edge_type == "UNRESOLVED_CALL"]
    names = {e.callee_name for e in unresolved}

    assert "libA" in names, f"Expected 'libA' in unresolved callee_names. Got: {names}"
    assert "libB" in names, f"Expected 'libB' in unresolved callee_names. Got: {names}"
    assert "libC" in names, f"Expected 'libC' in unresolved callee_names. Got: {names}"
    assert len(names) == 3, f"Expected 3 distinct callee_names, got {len(names)}: {names}"
