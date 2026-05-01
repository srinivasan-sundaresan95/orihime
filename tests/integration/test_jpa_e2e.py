"""End-to-end JPA Entity Graph integration test (P5-4).

Indexes JpaEntities.java as a single-file repo and verifies that
EntityRelation nodes and HAS_RELATION edges are persisted in KuzuDB.

Schema additions required (P5-4 production code):
  - CREATE NODE TABLE EntityRelation(
        id STRING,
        source_class_id STRING,
        target_class_fqn STRING,
        field_name STRING,
        relation_type STRING,
        fetch_type STRING,
        repo_id STRING,
        PRIMARY KEY(id)
    )
  - CREATE REL TABLE HAS_RELATION(FROM Class TO EntityRelation)

Tests will fail until the P5-4 coder's production code lands — that is expected.
"""
from __future__ import annotations

import os
import shutil
import tempfile

import kuzu
import pytest

from orihime.indexer import index_repo

FIXTURES_DIR = os.path.join(
    os.path.dirname(__file__),
    "..",
    "fixtures",
)

JPA_FIXTURES = [
    "JpaEntities.java",
]


@pytest.fixture(scope="module")
def jpa_conn():
    """Index JpaEntities.java and return a KuzuDB connection."""
    with tempfile.TemporaryDirectory() as repo_dir:
        with tempfile.TemporaryDirectory() as db_dir:
            for fname in JPA_FIXTURES:
                src = os.path.join(FIXTURES_DIR, fname)
                shutil.copy(src, os.path.join(repo_dir, fname))

            db_path = os.path.join(db_dir, "jpa_test.db")
            stats = index_repo(
                repo_path=repo_dir,
                repo_name="jpa-e2e-test",
                db_path=db_path,
                max_workers=1,
            )
            print(f"\n[jpa-e2e] index stats: {stats}")

            db = kuzu.Database(db_path)
            conn = kuzu.Connection(db)
            yield conn
            del conn, db


def _collect_rows(result) -> list:
    rows = []
    while result.has_next():
        rows.append(result.get_next())
    return rows


def _get_repo_id(conn) -> str:
    """Return the repo_id inserted for the jpa-e2e-test repo."""
    result = conn.execute(
        "MATCH (r:Repo) WHERE r.name = 'jpa-e2e-test' RETURN r.id"
    )
    rows = _collect_rows(result)
    assert rows, "Repo node for jpa-e2e-test not found"
    return rows[0][0]


# ---------------------------------------------------------------------------
# 1. EntityRelation nodes are indexed for this repo
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_entity_relations_indexed(jpa_conn):
    """After indexing, at least one EntityRelation node must exist for the repo."""
    rid = _get_repo_id(jpa_conn)
    result = jpa_conn.execute(
        "MATCH (n:EntityRelation) WHERE n.repo_id = $rid RETURN count(*)",
        {"rid": rid},
    )
    rows = _collect_rows(result)
    count = rows[0][0]
    assert count > 0, (
        f"Expected > 0 EntityRelation nodes for repo_id={rid!r}, got {count}"
    )


# ---------------------------------------------------------------------------
# 2. Order.customer is ManyToOne with EAGER fetch
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_order_customer_eager(jpa_conn):
    """An EntityRelation for ManyToOne/EAGER must exist, linked to the Order class."""
    rid = _get_repo_id(jpa_conn)

    # First find Order's class id
    cls_result = jpa_conn.execute(
        "MATCH (c:Class) WHERE c.name = 'Order' AND c.repo_id = $rid RETURN c.id",
        {"rid": rid},
    )
    cls_rows = _collect_rows(cls_result)
    assert cls_rows, f"Order Class node not found for repo_id={rid!r}"
    order_class_id = cls_rows[0][0]

    # Now query for EntityRelation with the expected properties
    rel_result = jpa_conn.execute(
        "MATCH (n:EntityRelation) "
        "WHERE n.repo_id = $rid "
        "AND n.relation_type = 'ManyToOne' "
        "AND n.fetch_type = 'EAGER' "
        "AND n.source_class_id = $cid "
        "RETURN n.field_name",
        {"rid": rid, "cid": order_class_id},
    )
    rows = _collect_rows(rel_result)
    assert len(rows) >= 1, (
        f"Expected at least one ManyToOne/EAGER EntityRelation for Order "
        f"(class_id={order_class_id!r}); got {rows}"
    )
    field_names = [r[0] for r in rows]
    assert "customer" in field_names, (
        f"Expected field_name='customer' in EAGER ManyToOne relations; got: {field_names}"
    )


# ---------------------------------------------------------------------------
# 3. HAS_RELATION edges exist between Class nodes and EntityRelation nodes
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_has_relation_edges_exist(jpa_conn):
    """HAS_RELATION edges from Class to EntityRelation must exist for this repo."""
    rid = _get_repo_id(jpa_conn)
    result = jpa_conn.execute(
        "MATCH (c:Class)-[:HAS_RELATION]->(er:EntityRelation) "
        "WHERE er.repo_id = $rid "
        "RETURN count(*)",
        {"rid": rid},
    )
    rows = _collect_rows(result)
    count = rows[0][0]
    assert count > 0, (
        f"Expected > 0 HAS_RELATION edges for repo_id={rid!r}, got {count}"
    )
