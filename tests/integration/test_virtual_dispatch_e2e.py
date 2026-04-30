"""End-to-end virtual dispatch resolution integration test (P5-2).

Indexes VirtualDispatch.java as a single-file repo and verifies that
CALLS fan-out edges are created from the caller (AnimalTrainer.train)
to all concrete override implementations (Dog.speak, Cat.speak, etc.).

Production code requirement (P5-2):
  For every CALLS edge (A -> AbstractMethod), the indexer must also
  create CALLS edges (A -> ConcreteOverride1), (A -> ConcreteOverride2),
  etc., based on the inheritance graph already stored in KuzuDB.
"""
from __future__ import annotations

import os
import shutil
import tempfile

import kuzu
import pytest

from indra.indexer import index_repo

FIXTURES_DIR = os.path.join(
    os.path.dirname(__file__),
    "..",
    "fixtures",
)

VIRTUAL_DISPATCH_FIXTURES = [
    "VirtualDispatch.java",
]


@pytest.fixture(scope="module")
def vd_conn():
    """Index VirtualDispatch.java and return a KuzuDB connection."""
    with tempfile.TemporaryDirectory() as repo_dir:
        with tempfile.TemporaryDirectory() as db_dir:
            for fname in VIRTUAL_DISPATCH_FIXTURES:
                src = os.path.join(FIXTURES_DIR, fname)
                shutil.copy(src, os.path.join(repo_dir, fname))

            db_path = os.path.join(db_dir, "virtual_dispatch_test.db")
            stats = index_repo(
                repo_path=repo_dir,
                repo_name="virtual-dispatch-e2e-test",
                db_path=db_path,
                max_workers=1,
            )
            print(f"\n[virtual-dispatch-e2e] index stats: {stats}")

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
# 1. AnimalTrainer.train has CALLS edges to speak and move
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_virtual_dispatch_dog_speak(vd_conn):
    """AnimalTrainer.train must have CALLS edges to at least speak and move."""
    r = vd_conn.execute(
        "MATCH (a:Method)-[:CALLS]->(b:Method) WHERE a.name = $name RETURN b.name",
        {"name": "train"},
    )
    callees = set()
    while r.has_next():
        callees.add(r.get_next()[0])

    print(f"\n[virtual-dispatch-e2e] callees of train: {callees}")

    assert "speak" in callees, (
        f"Expected 'speak' in callees of train, got: {callees}"
    )
    assert "move" in callees, (
        f"Expected 'move' in callees of train, got: {callees}"
    )


# ---------------------------------------------------------------------------
# 2. Fan-out must cover BOTH Dog and Cat concrete overrides
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_virtual_dispatch_both_subclasses(vd_conn):
    """AnimalTrainer.train must have CALLS edges to both Dog.speak and Cat.speak."""
    r = vd_conn.execute(
        "MATCH (trainer:Method)-[:CALLS]->(m:Method) WHERE trainer.name = 'train' "
        "RETURN m.fqn",
        {},
    )
    fqns = set()
    while r.has_next():
        fqns.add(r.get_next()[0])

    print(f"\n[virtual-dispatch-e2e] fqns reachable from train: {fqns}")

    dog_speak = any("Dog" in f and "speak" in f for f in fqns if f is not None)
    cat_speak = any("Cat" in f and "speak" in f for f in fqns if f is not None)

    assert dog_speak, f"Expected Dog.speak in callees, got: {fqns}"
    assert cat_speak, f"Expected Cat.speak in callees, got: {fqns}"


# ---------------------------------------------------------------------------
# 3. Concrete method breathe() is never called — no CALLS edge should exist
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_virtual_dispatch_concrete_not_fanned_out(vd_conn):
    """breathe() is never invoked in this fixture; CALLS count to it must be 0."""
    r = vd_conn.execute(
        "MATCH (a:Method)-[:CALLS]->(b:Method) WHERE b.name = 'breathe' "
        "RETURN count(*) AS c",
        {},
    )
    count = r.get_next()[0]
    assert count == 0, (
        f"breathe() is never called; expected 0 CALLS edges to it, got {count}"
    )
