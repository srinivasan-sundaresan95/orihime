"""Unit tests for P2-6: static field access chains resolved as CALLS edges."""
from __future__ import annotations

import pathlib

import indra.java_extractor  # noqa: F401 — triggers register()
from indra.java_extractor import JavaExtractor
from indra.language import get_parser
from indra.resolver import build_fqn_index, resolve_calls

FIXTURE = pathlib.Path(__file__).parent.parent / "fixtures" / "StaticFieldCalls.java"


def _parse_fixture():
    src = FIXTURE.read_bytes()
    parser = get_parser("java")
    tree = parser.parse(src)
    extractor = JavaExtractor()
    result = extractor.extract(tree, src, "sf_file", "repo1")
    return tree, src, result


def test_helper_do_work_found_as_method():
    """Helper.doWork must be extracted as a method."""
    _, _, result = _parse_fixture()
    names = {m["name"] for m in result.methods}
    assert "doWork" in names, f"doWork not found in methods: {names}"


def test_static_field_calls_caller_found():
    """StaticFieldCalls.caller must be extracted as a method."""
    _, _, result = _parse_fixture()
    names = {m["name"] for m in result.methods}
    assert "caller" in names, f"caller not found in methods: {names}"


def test_caller_has_calls_edge_to_do_work():
    """StaticFieldCalls.caller should have a CALLS edge to Helper.doWork.

    The call is `Helper.INSTANCE.doWork()` — the object is a field_access chain
    whose root class is `Helper`, and `doWork` is a known method name in this file,
    so the resolver should emit a CALLS edge.
    """
    tree, src, result = _parse_fixture()

    fqn_index = build_fqn_index(result.methods)
    edges = resolve_calls(tree, src, result.methods, fqn_index, "sf_file", "repo1")

    # Find IDs
    caller_method = next((m for m in result.methods if m["name"] == "caller"), None)
    callee_method = next((m for m in result.methods if m["name"] == "doWork"), None)
    assert caller_method is not None
    assert callee_method is not None

    calls_edges = [
        e for e in edges
        if e.edge_type == "CALLS"
        and e.caller_id == caller_method["id"]
        and e.callee_id == callee_method["id"]
    ]
    assert len(calls_edges) >= 1, (
        f"Expected at least 1 CALLS edge from caller→doWork, "
        f"got edges: {[(e.caller_id, e.callee_id, e.edge_type) for e in edges]}"
    )


def test_logger_info_is_unresolved():
    """Logger.log.info('hello') targets a non-indexed method — expect UNRESOLVED_CALL."""
    tree, src, result = _parse_fixture()

    fqn_index = build_fqn_index(result.methods)
    edges = resolve_calls(tree, src, result.methods, fqn_index, "sf_file", "repo1")

    unresolved = [e for e in edges if e.edge_type == "UNRESOLVED_CALL"]
    # "info" is not in the indexed methods, so should appear as unresolved
    assert len(unresolved) >= 1, (
        f"Expected at least 1 UNRESOLVED_CALL edge, got edges: {edges}"
    )
