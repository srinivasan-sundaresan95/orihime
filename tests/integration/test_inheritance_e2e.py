"""End-to-end inheritance graph integration test (P5-1).

Indexes InheritanceSimple.java as a single-file repo and verifies that
EXTENDS and IMPLEMENTS edges are persisted in KuzuDB.

Schema additions required (P5-1 production code):
  - CREATE REL TABLE EXTENDS(FROM Class TO Class, parent_fqn STRING)
  - CREATE REL TABLE IMPLEMENTS(FROM Class TO Class, parent_fqn STRING)

Or alternatively a single Inheritance rel table with an edge_type property.
The queries below match both approaches by querying the parent_fqn attribute
stored on the Class node or via a dedicated property on the relationship,
depending on the production schema decision.
"""
from __future__ import annotations

import os
import shutil
import tempfile

import kuzu
import pytest

from dedalus.indexer import index_repo

FIXTURES_DIR = os.path.join(
    os.path.dirname(__file__),
    "..",
    "fixtures",
)

INHERITANCE_FIXTURES = [
    "InheritanceSimple.java",
]


@pytest.fixture(scope="module")
def inh_conn():
    """Index InheritanceSimple.java and return a KuzuDB connection."""
    with tempfile.TemporaryDirectory() as repo_dir:
        with tempfile.TemporaryDirectory() as db_dir:
            for fname in INHERITANCE_FIXTURES:
                src = os.path.join(FIXTURES_DIR, fname)
                shutil.copy(src, os.path.join(repo_dir, fname))

            db_path = os.path.join(db_dir, "inheritance_test.db")
            stats = index_repo(
                repo_path=repo_dir,
                repo_name="inheritance-e2e-test",
                db_path=db_path,
                max_workers=1,
            )
            print(f"\n[inheritance-e2e] index stats: {stats}")

            db = kuzu.Database(db_path)
            conn = kuzu.Connection(db)
            yield conn
            del conn, db


def _collect_rows(result) -> list:
    rows = []
    while result.has_next():
        rows.append(result.get_next())
    return rows


# ---------------------------------------------------------------------------
# 1. FundBalanceStrategy EXTENDS BalanceStrategy
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fund_balance_strategy_extends_balance_strategy(inh_conn):
    """An EXTENDS relationship must exist from FundBalanceStrategy to BalanceStrategy."""
    result = inh_conn.execute(
        "MATCH (child:Class)-[:EXTENDS]->(parent:Class) "
        "WHERE child.fqn = 'com.example.inheritance.FundBalanceStrategy' "
        "AND parent.fqn = 'com.example.inheritance.BalanceStrategy' "
        "RETURN child.fqn, parent.fqn"
    )
    rows = _collect_rows(result)
    assert len(rows) == 1, (
        f"Expected exactly one EXTENDS edge from FundBalanceStrategy to BalanceStrategy, "
        f"got {len(rows)}: {rows}"
    )


# ---------------------------------------------------------------------------
# 2. CashStrategy IMPLEMENTS PaymentStrategy
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_cash_strategy_implements_payment_strategy(inh_conn):
    """An IMPLEMENTS relationship must exist from CashStrategy to PaymentStrategy."""
    result = inh_conn.execute(
        "MATCH (child:Class)-[:IMPLEMENTS]->(parent:Class) "
        "WHERE child.fqn = 'com.example.inheritance.CashStrategy' "
        "AND parent.fqn = 'com.example.inheritance.PaymentStrategy' "
        "RETURN child.fqn, parent.fqn"
    )
    rows = _collect_rows(result)
    assert len(rows) == 1, (
        f"Expected exactly one IMPLEMENTS edge from CashStrategy to PaymentStrategy, "
        f"got {len(rows)}: {rows}"
    )


# ---------------------------------------------------------------------------
# 3. Total EXTENDS + IMPLEMENTS edge count is greater than zero
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_total_inheritance_edge_count_positive(inh_conn):
    """The combined count of EXTENDS and IMPLEMENTS edges in the mini-repo must be > 0."""
    extends_result = inh_conn.execute("MATCH ()-[:EXTENDS]->() RETURN count(*)")
    extends_count = extends_result.get_next()[0]

    implements_result = inh_conn.execute("MATCH ()-[:IMPLEMENTS]->() RETURN count(*)")
    implements_count = implements_result.get_next()[0]

    total = extends_count + implements_count
    print(
        f"\n[inheritance-e2e] Edge summary — EXTENDS: {extends_count}, "
        f"IMPLEMENTS: {implements_count}, total: {total}"
    )
    assert total > 0, (
        f"Expected > 0 total inheritance edges (EXTENDS + IMPLEMENTS), got {total}"
    )
