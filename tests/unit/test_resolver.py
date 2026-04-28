"""Unit tests for indra.resolver — local symbol resolver."""
from __future__ import annotations

import dataclasses
import pathlib
import uuid

import indra.java_extractor  # noqa: F401 — triggers register()
from indra.language import get_parser
from indra.resolver import CallEdge, build_fqn_index, resolve_calls

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"
CALL_CHAIN_JAVA = FIXTURES / "CallChain.java"


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
# 1. build_fqn_index — basic case
# ---------------------------------------------------------------------------

def test_build_fqn_index_maps_fqn_to_id():
    m1 = _method_dict("alpha", "com.example.Foo.alpha")
    m2 = _method_dict("beta",  "com.example.Foo.beta")
    m3 = _method_dict("gamma", "com.example.Bar.gamma")
    index = build_fqn_index([m1, m2, m3])

    assert index["com.example.Foo.alpha"] == m1["id"]
    assert index["com.example.Foo.beta"]  == m2["id"]
    assert index["com.example.Bar.gamma"] == m3["id"]


# ---------------------------------------------------------------------------
# 2. build_fqn_index — empty input
# ---------------------------------------------------------------------------

def test_build_fqn_index_empty():
    assert build_fqn_index([]) == {}


# ---------------------------------------------------------------------------
# 3. resolve_calls — CallChain.java: A→B and B→C
# ---------------------------------------------------------------------------

def test_resolve_calls_finds_ab_and_bc_edges():
    src = CALL_CHAIN_JAVA.read_bytes()
    tree, source_bytes = _parse_java(src)

    # CallChain.java line numbers (1-based):
    #   line 2: class declaration
    #   line 3: methodA
    #   line 4: methodB
    #   line 5: methodC
    m_a = _method_dict("methodA", "com.example.CallChain.methodA", line_start=3)
    m_b = _method_dict("methodB", "com.example.CallChain.methodB", line_start=4)
    m_c = _method_dict("methodC", "com.example.CallChain.methodC", line_start=5)
    methods = [m_a, m_b, m_c]
    fqn_index = build_fqn_index(methods)

    edges = resolve_calls(tree, source_bytes, methods, fqn_index, "file1", "repo1")

    # Filter to CALLS edges only
    calls_edges = [e for e in edges if e.edge_type == "CALLS"]

    # Collect (caller_id, callee_id) pairs
    pairs = {(e.caller_id, e.callee_id) for e in calls_edges}

    # A→B
    assert (m_a["id"], m_b["id"]) in pairs, (
        f"Expected A→B edge. Edges found: {calls_edges}"
    )
    # B→C
    assert (m_b["id"], m_c["id"]) in pairs, (
        f"Expected B→C edge. Edges found: {calls_edges}"
    )

    # caller_id and callee_id must be non-empty strings
    for e in calls_edges:
        assert isinstance(e.caller_id, str) and e.caller_id
        assert isinstance(e.callee_id, str) and e.callee_id


# ---------------------------------------------------------------------------
# 4. resolve_calls — unresolved call for external method
# ---------------------------------------------------------------------------

def test_resolve_calls_unresolved_for_external_call():
    source = b"""
package com.example;
public class Caller {
    public void doWork() {
        externalService.doSomething();
    }
}
"""
    tree, source_bytes = _parse_java(source)

    m_do = _method_dict("doWork", "com.example.Caller.doWork", line_start=4)
    methods = [m_do]
    # fqn_index does NOT contain doSomething
    fqn_index = build_fqn_index(methods)

    edges = resolve_calls(tree, source_bytes, methods, fqn_index, "file1", "repo1")

    unresolved = [e for e in edges if e.edge_type == "UNRESOLVED_CALL"]
    assert len(unresolved) >= 1, (
        f"Expected at least one UNRESOLVED_CALL edge. All edges: {edges}"
    )
    for e in unresolved:
        assert isinstance(e.callee_id, str) and e.callee_id


# ---------------------------------------------------------------------------
# 5. CallEdge is a proper dataclass with the required fields
# ---------------------------------------------------------------------------

def test_call_edge_has_correct_types():
    assert dataclasses.is_dataclass(CallEdge)

    fields = {f.name for f in dataclasses.fields(CallEdge)}
    assert "caller_id" in fields
    assert "callee_id" in fields
    assert "edge_type"  in fields

    edge = CallEdge(caller_id="a", callee_id="b", edge_type="CALLS")
    assert edge.caller_id == "a"
    assert edge.callee_id == "b"
    assert edge.edge_type == "CALLS"
