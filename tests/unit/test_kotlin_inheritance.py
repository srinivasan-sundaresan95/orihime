"""Unit tests for P5-1: Kotlin inheritance graph extraction.

Tests cover:
  - _extract_kotlin_supertypes (unit): EXTENDS and IMPLEMENTS edges from AST nodes
  - KotlinExtractor.extract (integration): inheritance_edges field on ExtractResult

Fixtures used:
  InheritanceKotlin.kt  — interface, open class, class with mixed supertypes, object
"""
from __future__ import annotations

import pathlib

import pytest

from dedalus.kotlin_extractor import KotlinExtractor, _extract_kotlin_supertypes
from dedalus.language import get_parser

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_kotlin(fixture_name: str):
    """Return (tree, source_bytes) for a Kotlin fixture file."""
    src = (FIXTURES / fixture_name).read_bytes()
    parser = get_parser("kotlin")
    tree = parser.parse(src)
    return tree, src


def _find_class_node(root, class_name: str):
    """Walk the AST depth-first to find a class_declaration or object_declaration
    whose identifier child text matches *class_name*.  Returns the node or None.
    """
    _class_like_types = frozenset({
        "class_declaration",
        "object_declaration",
        "interface_declaration",
        "companion_object",
    })
    for node in _walk_all_nodes(root):
        if node.type in _class_like_types:
            for child in node.children:
                if child.type == "identifier":
                    text = child.text
                    if isinstance(text, bytes):
                        text = text.decode("utf-8", errors="replace")
                    if text == class_name:
                        return node
    return None


def _walk_all_nodes(node):
    """Yield all nodes depth-first."""
    yield node
    for child in node.children:
        yield from _walk_all_nodes(child)


# ---------------------------------------------------------------------------
# 1. ServiceImpl extends BaseService via constructor invocation
# ---------------------------------------------------------------------------

def test_class_extends_via_constructor_invocation():
    tree, src = _parse_kotlin("InheritanceKotlin.kt")
    root = tree.root_node
    node = _find_class_node(root, "ServiceImpl")
    assert node is not None, "ServiceImpl class node not found in fixture"

    edges = _extract_kotlin_supertypes(
        node, src,
        "com.example.kotlin.ServiceImpl",
        "uuid-si",
        "com.example.kotlin",
    )

    extends_edges = [e for e in edges if e["edge_type"] == "EXTENDS"]
    assert len(extends_edges) == 1, f"Expected 1 EXTENDS edge, got {extends_edges}"
    assert extends_edges[0]["parent_fqn"] == "com.example.kotlin.BaseService"
    assert extends_edges[0]["child_id"] == "uuid-si"


# ---------------------------------------------------------------------------
# 2. ServiceImpl implements Greeter
# ---------------------------------------------------------------------------

def test_class_implements_interface():
    tree, src = _parse_kotlin("InheritanceKotlin.kt")
    root = tree.root_node
    node = _find_class_node(root, "ServiceImpl")
    assert node is not None

    edges = _extract_kotlin_supertypes(
        node, src,
        "com.example.kotlin.ServiceImpl",
        "uuid-si",
        "com.example.kotlin",
    )

    impl_edges = [e for e in edges if e["edge_type"] == "IMPLEMENTS"]
    assert len(impl_edges) == 1, f"Expected 1 IMPLEMENTS edge, got {impl_edges}"
    assert impl_edges[0]["parent_fqn"] == "com.example.kotlin.Greeter"


# ---------------------------------------------------------------------------
# 3. SingletonService (object_declaration) extends BaseService
# ---------------------------------------------------------------------------

def test_object_extends_class():
    tree, src = _parse_kotlin("InheritanceKotlin.kt")
    root = tree.root_node
    node = _find_class_node(root, "SingletonService")
    assert node is not None
    assert node.type == "object_declaration"

    edges = _extract_kotlin_supertypes(
        node, src,
        "com.example.kotlin.SingletonService",
        "uuid-ss",
        "com.example.kotlin",
    )

    extends_edges = [e for e in edges if e["edge_type"] == "EXTENDS"]
    assert len(extends_edges) == 1
    assert extends_edges[0]["parent_fqn"] == "com.example.kotlin.BaseService"


# ---------------------------------------------------------------------------
# 4. BaseService has no supertypes — should return []
# ---------------------------------------------------------------------------

def test_no_supertypes_returns_empty():
    tree, src = _parse_kotlin("InheritanceKotlin.kt")
    root = tree.root_node
    node = _find_class_node(root, "BaseService")
    assert node is not None

    edges = _extract_kotlin_supertypes(
        node, src,
        "com.example.kotlin.BaseService",
        "uuid-bs",
        "com.example.kotlin",
    )

    assert edges == []


# ---------------------------------------------------------------------------
# 5. KotlinExtractor.extract populates inheritance_edges
# ---------------------------------------------------------------------------

def test_inheritance_edges_in_extract_result():
    tree, src = _parse_kotlin("InheritanceKotlin.kt")
    extractor = KotlinExtractor()
    result = extractor.extract(tree, src, "file-id", "repo-id")

    assert hasattr(result, "inheritance_edges"), (
        "ExtractResult must have an 'inheritance_edges' field"
    )
    assert isinstance(result.inheritance_edges, list)
    assert len(result.inheritance_edges) > 0, "Expected at least one inheritance edge"

    for edge in result.inheritance_edges:
        assert "child_id" in edge, f"Edge missing 'child_id': {edge}"
        assert "parent_fqn" in edge, f"Edge missing 'parent_fqn': {edge}"
        assert "edge_type" in edge, f"Edge missing 'edge_type': {edge}"


# ---------------------------------------------------------------------------
# 6. Greeter (interface) must NOT appear in inheritance_edges as a child
#    (interfaces themselves have no supertypes in this fixture)
# ---------------------------------------------------------------------------

def test_interface_not_in_inheritance_edges():
    tree, src = _parse_kotlin("InheritanceKotlin.kt")
    extractor = KotlinExtractor()
    result = extractor.extract(tree, src, "file-id", "repo-id")

    assert hasattr(result, "inheritance_edges"), (
        "ExtractResult must have an 'inheritance_edges' field"
    )

    # Find the class_id for Greeter
    greeter_class = next(
        (c for c in result.classes if c["name"] == "Greeter"),
        None,
    )
    assert greeter_class is not None, (
        "Greeter must be present in result.classes; check KotlinExtractor class collection"
    )
    greeter_id = greeter_class["id"]

    # Greeter has no supertypes, so it must not appear as the child of any edge
    greeter_as_child = [
        e for e in result.inheritance_edges if e["child_id"] == greeter_id
    ]
    assert greeter_as_child == [], (
        f"Greeter should have no inheritance edges as child, but found: {greeter_as_child}"
    )
