"""Unit tests for P6-1: Constructor Call Tracking.

Verifies that:
- Java `new ClassName(...)` (object_creation_expression) emits CALLS edges to
  ClassName.<init>.
- Java `new ArrayList()` (external class not in fqn_index) does NOT produce a
  spurious CALLS edge.
- Kotlin `ClassName(...)` (call_expression with capitalised name) emits CALLS
  edges to ClassName.<init>.
- The synthetic <init> method is emitted by both Java and Kotlin extractors.
- Integration: after full extraction, the <init> method is reachable via CALLS.
"""
from __future__ import annotations

import pathlib
import uuid

import pytest

import orihime.java_extractor  # noqa: F401 — triggers register()
import orihime.kotlin_extractor  # noqa: F401 — triggers register()
from orihime.java_extractor import JavaExtractor
from orihime.kotlin_extractor import KotlinExtractor
from orihime.language import get_parser
from orihime.resolver import CallEdge, build_fqn_index, resolve_calls

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"
JAVA_FIXTURE = FIXTURES / "ConstructorCalls.java"
KT_FIXTURE = FIXTURES / "ConstructorCalls.kt"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_java(source: bytes):
    parser = get_parser("java")
    return parser.parse(source), source


def _parse_kotlin(source: bytes):
    parser = get_parser("kotlin")
    return parser.parse(source), source


def _method_dict(name: str, fqn: str, line_start: int = 1,
                 class_id: str | None = None) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "fqn": fqn,
        "class_id": class_id or str(uuid.uuid4()),
        "file_id": "file1",
        "repo_id": "repo1",
        "line_start": line_start,
        "is_suspend": False,
        "annotations": [],
        "generated": False,
    }


# ---------------------------------------------------------------------------
# Java extractor: synthetic <init> methods are emitted
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def java_extract():
    src = JAVA_FIXTURE.read_bytes()
    parser = get_parser("java")
    tree = parser.parse(src)
    extractor = JavaExtractor()
    return extractor.extract(tree, src, "file1", "repo1")


def test_java_extractor_emits_init_methods(java_extract):
    """Each non-interface class in ConstructorCalls.java must have a synthetic <init> method."""
    init_methods = [m for m in java_extract.methods if m["name"] == "<init>"]
    # Three classes: Address, Person, PersonFactory → expect 3 <init> methods
    assert len(init_methods) == 3, (
        f"Expected 3 <init> methods, got {len(init_methods)}: {init_methods}"
    )


def test_java_init_fqns(java_extract):
    """Each <init> method must have fqn of the form ClassName.<init>."""
    init_fqns = {m["fqn"] for m in java_extract.methods if m["name"] == "<init>"}
    assert "com.example.ctor.Address.<init>" in init_fqns
    assert "com.example.ctor.Person.<init>" in init_fqns
    assert "com.example.ctor.PersonFactory.<init>" in init_fqns


def test_java_init_has_zero_line_start(java_extract):
    """Synthetic <init> methods must have line_start=0."""
    for m in java_extract.methods:
        if m["name"] == "<init>":
            assert m["line_start"] == 0, (
                f"Expected line_start=0 for <init>, got {m['line_start']}"
            )


# ---------------------------------------------------------------------------
# Java resolver: object_creation_expression → CALLS to <init>
# ---------------------------------------------------------------------------

def test_java_constructor_call_emits_calls_edge():
    """new Address(city) inside Person constructor must produce a CALLS edge to Address.<init>."""
    src = JAVA_FIXTURE.read_bytes()
    tree, source_bytes = _parse_java(src)

    # Extract methods (includes synthetic <init>)
    extractor = JavaExtractor()
    result = extractor.extract(tree, src, "file1", "repo1")
    fqn_index = build_fqn_index(result.methods)

    edges = resolve_calls(tree, source_bytes, result.methods, fqn_index, "file1", "repo1")
    calls_edges = [e for e in edges if e.edge_type == "CALLS"]

    # Find Address.<init> method id
    address_init_id = fqn_index.get("com.example.ctor.Address.<init>")
    assert address_init_id is not None, "Address.<init> not found in fqn_index"

    # At least one CALLS edge must target Address.<init>
    callee_ids = {e.callee_id for e in calls_edges}
    assert address_init_id in callee_ids, (
        f"Expected a CALLS edge to Address.<init> ({address_init_id}). "
        f"All callee_ids: {callee_ids}"
    )


def test_java_constructor_callee_name_is_init():
    """CALLS edges produced from object_creation_expression must carry callee_name ending in .<init>."""
    src = JAVA_FIXTURE.read_bytes()
    tree, source_bytes = _parse_java(src)

    extractor = JavaExtractor()
    result = extractor.extract(tree, src, "file1", "repo1")
    fqn_index = build_fqn_index(result.methods)

    edges = resolve_calls(tree, source_bytes, result.methods, fqn_index, "file1", "repo1")
    init_calls = [e for e in edges if e.edge_type == "CALLS" and e.callee_name.endswith(".<init>")]
    # getAddress() → new Address("Tokyo"), create() → new Person(city),
    # Person constructor body → new Address(city): at least 3 total.
    assert len(init_calls) >= 3, (
        f"Expected at least 3 CALLS edges with .<init> callee_name, got {len(init_calls)}: {init_calls}"
    )


def test_java_external_class_no_spurious_edge():
    """new ArrayList() must NOT produce a CALLS edge when ArrayList is not in the index."""
    source = b"""
package com.example;
import java.util.ArrayList;
import java.util.List;
class Collector {
    void collect() {
        List<String> items = new ArrayList<>();
    }
}
"""
    tree, source_bytes = _parse_java(source)

    extractor = JavaExtractor()
    result = extractor.extract(tree, source, "file1", "repo1")
    fqn_index = build_fqn_index(result.methods)

    edges = resolve_calls(tree, source_bytes, result.methods, fqn_index, "file1", "repo1")

    # ArrayList is not in the index — no CALLS edge must target ArrayList.<init>
    init_calls = [e for e in edges if e.edge_type == "CALLS" and e.callee_name == "ArrayList.<init>"]
    assert len(init_calls) == 0, (
        f"Expected no spurious CALLS edge to ArrayList.<init>, but found: {init_calls}"
    )


# ---------------------------------------------------------------------------
# Kotlin extractor: synthetic <init> methods are emitted
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def kt_extract():
    src = KT_FIXTURE.read_bytes()
    parser = get_parser("kotlin")
    tree = parser.parse(src)
    extractor = KotlinExtractor()
    return extractor.extract(tree, src, "file1", "repo1")


def test_kotlin_extractor_emits_init_methods(kt_extract):
    """Each regular class in ConstructorCalls.kt must have a synthetic <init> method."""
    init_methods = [m for m in kt_extract.methods if m["name"] == "<init>"]
    # Classes: Point (data class), Rectangle, ShapeFactory → 3 <init> methods
    init_fqns = {m["fqn"] for m in init_methods}
    assert "com.example.ctor.Point.<init>" in init_fqns, f"init_fqns: {init_fqns}"
    assert "com.example.ctor.Rectangle.<init>" in init_fqns, f"init_fqns: {init_fqns}"
    assert "com.example.ctor.ShapeFactory.<init>" in init_fqns, f"init_fqns: {init_fqns}"


def test_kotlin_object_decl_has_no_init():
    """Kotlin object declarations (singletons) must NOT get a synthetic <init> method."""
    source = b"""
package com.example
object DateTimeUtil {
    fun isInTimePeriod(value: Int): Boolean = value > 0
}
"""
    parser = get_parser("kotlin")
    tree = parser.parse(source)
    extractor = KotlinExtractor()
    result = extractor.extract(tree, source, "file1", "repo1")

    init_methods = [m for m in result.methods if m["name"] == "<init>"]
    assert len(init_methods) == 0, (
        f"object declaration should have no <init>, but found: {init_methods}"
    )


# ---------------------------------------------------------------------------
# Kotlin resolver: call_expression with uppercase name → CALLS to <init>
# ---------------------------------------------------------------------------

def test_kotlin_constructor_call_emits_calls_edge():
    """Point(x1, y1) inside makeRect must produce a CALLS edge to Point.<init>."""
    src = KT_FIXTURE.read_bytes()
    tree, source_bytes = _parse_kotlin(src)

    extractor = KotlinExtractor()
    result = extractor.extract(tree, src, "file1", "repo1")
    fqn_index = build_fqn_index(result.methods)

    edges = resolve_calls(tree, source_bytes, result.methods, fqn_index, "file1", "repo1")
    calls_edges = [e for e in edges if e.edge_type == "CALLS"]

    # Find Point.<init> method id
    point_init_id = fqn_index.get("com.example.ctor.Point.<init>")
    assert point_init_id is not None, "Point.<init> not found in fqn_index"

    callee_ids = {e.callee_id for e in calls_edges}
    assert point_init_id in callee_ids, (
        f"Expected a CALLS edge to Point.<init> ({point_init_id}). "
        f"All callee_ids: {callee_ids}"
    )


def test_kotlin_multiple_constructor_calls_in_method():
    """makeRect calls Point twice and Rectangle once — all three must be CALLS edges to <init>."""
    src = KT_FIXTURE.read_bytes()
    tree, source_bytes = _parse_kotlin(src)

    extractor = KotlinExtractor()
    result = extractor.extract(tree, src, "file1", "repo1")
    fqn_index = build_fqn_index(result.methods)

    edges = resolve_calls(tree, source_bytes, result.methods, fqn_index, "file1", "repo1")

    # makeRect method id
    make_rect_id = next(
        m["id"] for m in result.methods
        if m["fqn"] == "com.example.ctor.ShapeFactory.makeRect"
    )

    init_edges_from_make_rect = [
        e for e in edges
        if e.caller_id == make_rect_id
        and e.edge_type == "CALLS"
        and e.callee_name.endswith(".<init>")
    ]
    # callee_name uses the simple class name form: "Point.<init>", "Rectangle.<init>"
    # Expect calls to Point.<init> (×2) and Rectangle.<init> (×1) = at least 2 distinct init edges
    callee_names = {e.callee_name for e in init_edges_from_make_rect}
    assert "Point.<init>" in callee_names, (
        f"Expected Point.<init> among callee names, got {callee_names}"
    )
    assert "Rectangle.<init>" in callee_names, (
        f"Expected Rectangle.<init> among callee names, got {callee_names}"
    )
