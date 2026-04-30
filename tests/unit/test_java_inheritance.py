"""Unit tests for P5-1: Java inheritance graph extraction.

Tests cover:
  - _extract_inheritance (unit): EXTENDS and IMPLEMENTS edges from AST nodes
  - _build_import_map (unit): import → FQN resolution
  - JavaExtractor.extract (integration): inheritance_edges field on ExtractResult

Fixtures used:
  InheritanceSimple.java   — interfaces, abstract class, concrete class, multi-implements
  InheritanceChain.java    — A → B → C three-level extends chain
  InheritanceExternal.java — extends a class resolved via import statement
  InheritanceGeneric.java  — implements a generic interface (Comparator<String>)
"""
from __future__ import annotations

import pathlib

import pytest

from indra.java_extractor import JavaExtractor, _build_import_map, _extract_inheritance
from indra.language import get_parser

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_java(fixture_name: str):
    """Return (tree, source_bytes) for a Java fixture file."""
    src = (FIXTURES / fixture_name).read_bytes()
    parser = get_parser("java")
    tree = parser.parse(src)
    return tree, src


def _find_class_node(root, class_name: str):
    """Walk the AST depth-first to find a class_declaration or interface_declaration
    whose identifier child matches *class_name*.  Returns the node or None.
    """
    for node in _walk_all_nodes(root):
        if node.type in ("class_declaration", "interface_declaration"):
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
# 1. FundBalanceStrategy extends BalanceStrategy (abstract class)
# ---------------------------------------------------------------------------

def test_class_extends_abstract_class():
    tree, src = _parse_java("InheritanceSimple.java")
    root = tree.root_node
    import_map = _build_import_map(root, src)
    node = _find_class_node(root, "FundBalanceStrategy")
    assert node is not None, "FundBalanceStrategy class node not found in fixture"

    edges = _extract_inheritance(
        node, src,
        "com.example.inheritance.FundBalanceStrategy",
        "some-uuid",
        import_map,
        "com.example.inheritance",
    )

    extends_edges = [e for e in edges if e["edge_type"] == "EXTENDS"]
    assert len(extends_edges) == 1, f"Expected 1 EXTENDS edge, got {extends_edges}"
    assert extends_edges[0]["parent_fqn"] == "com.example.inheritance.BalanceStrategy"
    assert extends_edges[0]["child_id"] == "some-uuid"


# ---------------------------------------------------------------------------
# 2. CashStrategy implements PaymentStrategy
# ---------------------------------------------------------------------------

def test_class_implements_interface():
    tree, src = _parse_java("InheritanceSimple.java")
    root = tree.root_node
    import_map = _build_import_map(root, src)
    node = _find_class_node(root, "CashStrategy")
    assert node is not None

    edges = _extract_inheritance(
        node, src,
        "com.example.inheritance.CashStrategy",
        "uuid-cash",
        import_map,
        "com.example.inheritance",
    )

    impl_edges = [e for e in edges if e["edge_type"] == "IMPLEMENTS"]
    assert len(impl_edges) == 1
    assert impl_edges[0]["parent_fqn"] == "com.example.inheritance.PaymentStrategy"


# ---------------------------------------------------------------------------
# 3. BalanceStrategy (abstract class) implements FundStrategy
# ---------------------------------------------------------------------------

def test_abstract_class_implements_interface():
    tree, src = _parse_java("InheritanceSimple.java")
    root = tree.root_node
    import_map = _build_import_map(root, src)
    node = _find_class_node(root, "BalanceStrategy")
    assert node is not None

    edges = _extract_inheritance(
        node, src,
        "com.example.inheritance.BalanceStrategy",
        "uuid-balance",
        import_map,
        "com.example.inheritance",
    )

    impl_edges = [e for e in edges if e["edge_type"] == "IMPLEMENTS"]
    assert len(impl_edges) == 1
    assert impl_edges[0]["parent_fqn"] == "com.example.inheritance.FundStrategy"


# ---------------------------------------------------------------------------
# 4. FundStrategy (interface) extends PaymentStrategy (interface)
# ---------------------------------------------------------------------------

def test_interface_extends_interface():
    tree, src = _parse_java("InheritanceSimple.java")
    root = tree.root_node
    import_map = _build_import_map(root, src)
    node = _find_class_node(root, "FundStrategy")
    assert node is not None
    assert node.type == "interface_declaration"

    edges = _extract_inheritance(
        node, src,
        "com.example.inheritance.FundStrategy",
        "uuid-fund",
        import_map,
        "com.example.inheritance",
    )

    # interface-extends-interface is canonically represented as IMPLEMENTS
    assert len(edges) == 1
    assert edges[0]["edge_type"] == "IMPLEMENTS"
    assert edges[0]["parent_fqn"] == "com.example.inheritance.PaymentStrategy"


# ---------------------------------------------------------------------------
# 5. InheritanceChain: B extends A
# ---------------------------------------------------------------------------

def test_multi_level_chain_B_extends_A():
    tree, src = _parse_java("InheritanceChain.java")
    root = tree.root_node
    import_map = _build_import_map(root, src)
    node = _find_class_node(root, "B")
    assert node is not None

    edges = _extract_inheritance(
        node, src,
        "com.example.chain.B",
        "uuid-B",
        import_map,
        "com.example.chain",
    )

    extends_edges = [e for e in edges if e["edge_type"] == "EXTENDS"]
    assert len(extends_edges) == 1
    assert extends_edges[0]["parent_fqn"] == "com.example.chain.A"


# ---------------------------------------------------------------------------
# 6. InheritanceChain: C extends B
# ---------------------------------------------------------------------------

def test_multi_level_chain_C_extends_B():
    tree, src = _parse_java("InheritanceChain.java")
    root = tree.root_node
    import_map = _build_import_map(root, src)
    node = _find_class_node(root, "C")
    assert node is not None

    edges = _extract_inheritance(
        node, src,
        "com.example.chain.C",
        "uuid-C",
        import_map,
        "com.example.chain",
    )

    extends_edges = [e for e in edges if e["edge_type"] == "EXTENDS"]
    assert len(extends_edges) == 1
    assert extends_edges[0]["parent_fqn"] == "com.example.chain.B"


# ---------------------------------------------------------------------------
# 7. A has no inheritance — should return []
# ---------------------------------------------------------------------------

def test_no_inheritance_returns_empty():
    tree, src = _parse_java("InheritanceChain.java")
    root = tree.root_node
    import_map = _build_import_map(root, src)
    node = _find_class_node(root, "A")
    assert node is not None

    edges = _extract_inheritance(
        node, src,
        "com.example.chain.A",
        "uuid-A",
        import_map,
        "com.example.chain",
    )

    assert edges == []


# ---------------------------------------------------------------------------
# 8. Generic type parameter String must NOT appear as a supertype
# ---------------------------------------------------------------------------

def test_type_parameter_not_extracted_as_supertype():
    tree, src = _parse_java("InheritanceGeneric.java")
    root = tree.root_node
    import_map = _build_import_map(root, src)
    node = _find_class_node(root, "NameComparator")
    assert node is not None

    edges = _extract_inheritance(
        node, src,
        "com.example.generic.NameComparator",
        "uuid-nc",
        import_map,
        "com.example.generic",
    )

    # No edge should have "String" as parent_fqn
    string_edges = [e for e in edges if "String" in e.get("parent_fqn", "")]
    assert string_edges == [], f"Unexpected edges with 'String' in parent_fqn: {string_edges}"

    # At least one IMPLEMENTS edge should exist (for Comparator)
    impl_edges = [e for e in edges if e["edge_type"] == "IMPLEMENTS"]
    assert len(impl_edges) >= 1, "Expected at least one IMPLEMENTS edge for Comparator"


# ---------------------------------------------------------------------------
# 9. Import map resolution for external class
# ---------------------------------------------------------------------------

def test_import_map_resolution():
    tree, src = _parse_java("InheritanceExternal.java")
    root = tree.root_node
    import_map = _build_import_map(root, src)

    # Verify the import was captured correctly
    assert "Controller" in import_map, (
        f"Expected 'Controller' in import_map, got keys: {list(import_map.keys())}"
    )
    assert import_map["Controller"] == "org.springframework.web.servlet.mvc.Controller"

    node = _find_class_node(root, "MyController")
    assert node is not None

    edges = _extract_inheritance(
        node, src,
        "com.example.external.MyController",
        "uuid-mc",
        import_map,
        "com.example.external",
    )

    extends_edges = [e for e in edges if e["edge_type"] == "EXTENDS"]
    assert len(extends_edges) == 1
    assert extends_edges[0]["parent_fqn"] == "org.springframework.web.servlet.mvc.Controller"


# ---------------------------------------------------------------------------
# 10. JavaExtractor.extract populates inheritance_edges
# ---------------------------------------------------------------------------

def test_inheritance_edges_in_extract_result():
    tree, src = _parse_java("InheritanceSimple.java")
    extractor = JavaExtractor()
    result = extractor.extract(tree, src, "file-id", "repo-id")

    assert hasattr(result, "inheritance_edges"), (
        "ExtractResult must have an 'inheritance_edges' field"
    )
    assert isinstance(result.inheritance_edges, list)
    assert len(result.inheritance_edges) > 0, "Expected at least one inheritance edge"

    valid_edge_types = {"EXTENDS", "IMPLEMENTS"}
    for edge in result.inheritance_edges:
        assert "child_id" in edge, f"Edge missing 'child_id': {edge}"
        assert "parent_fqn" in edge, f"Edge missing 'parent_fqn': {edge}"
        assert "edge_type" in edge, f"Edge missing 'edge_type': {edge}"
        assert edge["edge_type"] in valid_edge_types, (
            f"Unexpected edge_type {edge['edge_type']!r}; must be one of {valid_edge_types}"
        )
