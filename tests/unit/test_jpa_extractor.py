"""Unit tests for P5-4: Spring Data/JPA Entity Graph extraction.

Tests cover _extract_entity_relations() directly (tests 1–9) and
JavaExtractor.extract() integration (tests 11–12).

The production implementation (_extract_entity_relations, EntityRelation in
parse_result, entity_relations field on ExtractResult) is being written in
parallel by the P5-4 coder.  Tests are expected to fail until that work lands.
"""
from __future__ import annotations

import pathlib

import pytest

# Trigger extractor registration
import indra.java_extractor  # noqa: F401

from indra.java_extractor import JavaExtractor, _build_import_map
from indra.language import get_parser

try:
    from indra.java_extractor import _extract_entity_relations
    _HAS_EXTRACT_ENTITY_RELATIONS = True
except ImportError:
    _HAS_EXTRACT_ENTITY_RELATIONS = False

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_java(filename: str):
    """Return (tree, source_bytes) for a Java fixture file."""
    src = (FIXTURES / filename).read_bytes()
    parser = get_parser("java")
    tree = parser.parse(src)
    return tree, src


def _walk_all_nodes(node):
    """Yield all nodes depth-first."""
    yield node
    for child in node.children:
        yield from _walk_all_nodes(child)


def _find_class_node(root, class_name: str):
    """Find a class_declaration node by name in the AST."""
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


def _get_entity_relations(class_name: str, tree, src: bytes, *, expect_entity: bool = True):
    """Invoke _extract_entity_relations for the named class.

    Returns the list of EntityRelation-like dicts.  Passes class_annotations
    with/without "Entity" based on *expect_entity*.
    """
    if not _HAS_EXTRACT_ENTITY_RELATIONS:
        pytest.skip("_extract_entity_relations not yet implemented (P5-4 pending)")

    root = tree.root_node
    import_map = _build_import_map(root, src)
    node = _find_class_node(root, class_name)
    assert node is not None, f"Class node '{class_name}' not found in fixture"

    class_id = f"fake-id-{class_name}"
    class_fqn = f"com.example.jpa.{class_name}"
    repo_id = "repo-jpa-test"

    class_annotations = ["Entity"] if expect_entity else []

    return _extract_entity_relations(
        node, src, class_id, class_fqn, repo_id, import_map, class_annotations
    )


# ---------------------------------------------------------------------------
# Fixture-level parsed result (used by tests 1–9 via helper)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def jpa_tree_src():
    return _parse_java("JpaEntities.java")


# ---------------------------------------------------------------------------
# 1. Order has a ManyToOne field: customer
# ---------------------------------------------------------------------------

def test_order_has_customer_relation(jpa_tree_src):
    tree, src = jpa_tree_src
    relations = _get_entity_relations("Order", tree, src)
    field_names = [r["field_name"] for r in relations]
    assert "customer" in field_names, (
        f"Expected field 'customer' in Order relations; got: {field_names}"
    )


# ---------------------------------------------------------------------------
# 2. Order.customer relation has fetch_type="EAGER"
# ---------------------------------------------------------------------------

def test_order_customer_fetch_type_eager(jpa_tree_src):
    tree, src = jpa_tree_src
    relations = _get_entity_relations("Order", tree, src)
    customer_rel = next((r for r in relations if r["field_name"] == "customer"), None)
    assert customer_rel is not None, "customer relation not found in Order"
    assert customer_rel["fetch_type"] == "EAGER", (
        f"Expected fetch_type='EAGER' for Order.customer, got: {customer_rel['fetch_type']!r}"
    )


# ---------------------------------------------------------------------------
# 3. Order has a OneToMany field: items
# ---------------------------------------------------------------------------

def test_order_has_items_relation(jpa_tree_src):
    tree, src = jpa_tree_src
    relations = _get_entity_relations("Order", tree, src)
    field_names = [r["field_name"] for r in relations]
    assert "items" in field_names, (
        f"Expected field 'items' in Order relations; got: {field_names}"
    )


# ---------------------------------------------------------------------------
# 4. Order.items relation has fetch_type="LAZY" (default when not specified)
# ---------------------------------------------------------------------------

def test_order_items_fetch_type_lazy(jpa_tree_src):
    tree, src = jpa_tree_src
    relations = _get_entity_relations("Order", tree, src)
    items_rel = next((r for r in relations if r["field_name"] == "items"), None)
    assert items_rel is not None, "items relation not found in Order"
    assert items_rel["fetch_type"] == "LAZY", (
        f"Expected fetch_type='LAZY' for Order.items, got: {items_rel['fetch_type']!r}"
    )


# ---------------------------------------------------------------------------
# 5. Order has a OneToOne field: shipment
# ---------------------------------------------------------------------------

def test_order_has_shipment_relation(jpa_tree_src):
    tree, src = jpa_tree_src
    relations = _get_entity_relations("Order", tree, src)
    field_names = [r["field_name"] for r in relations]
    assert "shipment" in field_names, (
        f"Expected field 'shipment' in Order relations; got: {field_names}"
    )


# ---------------------------------------------------------------------------
# 6. Customer has a OneToMany field: orders
# ---------------------------------------------------------------------------

def test_customer_has_orders_relation(jpa_tree_src):
    tree, src = jpa_tree_src
    relations = _get_entity_relations("Customer", tree, src)
    field_names = [r["field_name"] for r in relations]
    assert "orders" in field_names, (
        f"Expected field 'orders' in Customer relations; got: {field_names}"
    )


# ---------------------------------------------------------------------------
# 7. Customer.orders has fetch_type="LAZY" (explicitly specified)
# ---------------------------------------------------------------------------

def test_customer_orders_fetch_type_lazy(jpa_tree_src):
    tree, src = jpa_tree_src
    relations = _get_entity_relations("Customer", tree, src)
    orders_rel = next((r for r in relations if r["field_name"] == "orders"), None)
    assert orders_rel is not None, "orders relation not found in Customer"
    assert orders_rel["fetch_type"] == "LAZY", (
        f"Expected fetch_type='LAZY' for Customer.orders, got: {orders_rel['fetch_type']!r}"
    )


# ---------------------------------------------------------------------------
# 8. OrderItem has a ManyToOne field: order
# ---------------------------------------------------------------------------

def test_orderitem_has_order_relation(jpa_tree_src):
    tree, src = jpa_tree_src
    relations = _get_entity_relations("OrderItem", tree, src)
    field_names = [r["field_name"] for r in relations]
    assert "order" in field_names, (
        f"Expected field 'order' in OrderItem relations; got: {field_names}"
    )


# ---------------------------------------------------------------------------
# 9. Shipment has no JPA relation fields
# ---------------------------------------------------------------------------

def test_shipment_has_no_relations(jpa_tree_src):
    tree, src = jpa_tree_src
    relations = _get_entity_relations("Shipment", tree, src)
    assert relations == [], (
        f"Expected no relations for Shipment, got: {relations}"
    )


# ---------------------------------------------------------------------------
# 10. A plain class (not @Entity) returns empty list
# ---------------------------------------------------------------------------

def test_non_entity_class_ignored():
    if not _HAS_EXTRACT_ENTITY_RELATIONS:
        pytest.skip("_extract_entity_relations not yet implemented (P5-4 pending)")

    # Use an existing fixture that has no @Entity annotation (e.g. Sample.java)
    src = (FIXTURES / "Sample.java").read_bytes()
    parser = get_parser("java")
    tree = parser.parse(src)
    root = tree.root_node
    import_map = _build_import_map(root, src)

    # Find first class_declaration node
    node = _find_class_node(root, "SampleController")
    assert node is not None, "SampleController not found in Sample.java fixture"

    # Pass empty class_annotations (no @Entity)
    relations = _extract_entity_relations(
        node, src, "fake-id-sample", "com.example.SampleController", "repo-test",
        import_map, []
    )
    assert relations == [], (
        f"Non-@Entity class should return empty list; got: {relations}"
    )


# ---------------------------------------------------------------------------
# 11. JavaExtractor.extract populates entity_relations for JpaEntities.java
# ---------------------------------------------------------------------------

def test_all_entity_relations_via_extractor(jpa_tree_src):
    tree, src = jpa_tree_src
    extractor = JavaExtractor()
    result = extractor.extract(tree, src, "file-jpa", "repo-jpa")

    assert hasattr(result, "entity_relations"), (
        "ExtractResult must have an 'entity_relations' field (P5-4)"
    )
    assert isinstance(result.entity_relations, list), (
        f"entity_relations must be a list, got: {type(result.entity_relations)}"
    )
    assert len(result.entity_relations) > 0, (
        "Expected at least one EntityRelation from JpaEntities.java; got empty list"
    )


# ---------------------------------------------------------------------------
# 12. At least one EAGER relation is findable in extractor output
# ---------------------------------------------------------------------------

def test_eager_relations_findable(jpa_tree_src):
    tree, src = jpa_tree_src
    extractor = JavaExtractor()
    result = extractor.extract(tree, src, "file-jpa", "repo-jpa")

    assert hasattr(result, "entity_relations"), (
        "ExtractResult must have an 'entity_relations' field (P5-4)"
    )

    eager_rels = [r for r in result.entity_relations if r.get("fetch_type") == "EAGER"]
    assert len(eager_rels) >= 1, (
        f"Expected at least one EAGER fetch relation (Order.customer); "
        f"found: {eager_rels}. All relations: {result.entity_relations}"
    )
