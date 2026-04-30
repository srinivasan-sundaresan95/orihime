"""Unit tests for v1.1-B branch mode."""
from __future__ import annotations

import pathlib
import tempfile

import kuzu
import pytest

from indra.indexer import index_repo

FIXTURES_DIR = pathlib.Path(__file__).parent.parent / "fixtures"


def _make_db_path() -> pathlib.Path:
    tmpdir = tempfile.mkdtemp()
    return pathlib.Path(tmpdir) / "test.db"


def test_branch_node_created():
    db_path = _make_db_path()
    index_repo(FIXTURES_DIR, "test-repo", db_path, max_workers=1, branch="feature/xyz")

    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    r = conn.execute("MATCH (b:Branch) WHERE b.name = $n RETURN b.name", {"n": "feature/xyz"})
    assert r.has_next(), "Branch node not created"


def test_has_branch_edge_created():
    db_path = _make_db_path()
    index_repo(FIXTURES_DIR, "test-repo", db_path, max_workers=1, branch="feature/xyz")

    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    r = conn.execute(
        "MATCH (r:Repo)-[:HAS_BRANCH]->(b:Branch) WHERE b.name = $n RETURN r.name",
        {"n": "feature/xyz"},
    )
    assert r.has_next(), "HAS_BRANCH edge not created"
    assert r.get_next()[0] == "test-repo"


def test_file_node_stores_branch_name():
    db_path = _make_db_path()
    index_repo(FIXTURES_DIR, "test-repo", db_path, max_workers=1, branch="my-branch")

    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    r = conn.execute("MATCH (f:File) RETURN DISTINCT f.branch_name")
    names: list[str] = []
    while r.has_next():
        names.append(r.get_next()[0])
    assert names == ["my-branch"], f"Expected only 'my-branch', got {names}"


def test_default_branch_is_master():
    db_path = _make_db_path()
    index_repo(FIXTURES_DIR, "test-repo", db_path, max_workers=1)

    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    r = conn.execute("MATCH (b:Branch) RETURN b.name")
    names: list[str] = []
    while r.has_next():
        names.append(r.get_next()[0])
    assert "master" in names, f"Default branch 'master' not found; got {names}"


def test_two_branches_coexist():
    db_path = _make_db_path()
    index_repo(FIXTURES_DIR, "test-repo", db_path, max_workers=1, branch="main", force=True)
    index_repo(FIXTURES_DIR, "test-repo", db_path, max_workers=1, branch="feature/abc", force=False)

    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    r = conn.execute(
        "MATCH (repo:Repo)-[:HAS_BRANCH]->(b:Branch) WHERE repo.name = $n RETURN b.name",
        {"n": "test-repo"},
    )
    names: list[str] = []
    while r.has_next():
        names.append(r.get_next()[0])
    assert "main" in names, f"'main' branch missing; got {names}"
    assert "feature/abc" in names, f"'feature/abc' branch missing; got {names}"


def test_branch_node_idempotent():
    """Indexing the same branch twice must not duplicate Branch nodes."""
    db_path = _make_db_path()
    index_repo(FIXTURES_DIR, "test-repo", db_path, max_workers=1, branch="stable")
    index_repo(FIXTURES_DIR, "test-repo", db_path, max_workers=1, branch="stable")

    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    r = conn.execute("MATCH (b:Branch) WHERE b.name = 'stable' RETURN count(b)")
    count = r.get_next()[0]
    assert count == 1, f"Expected 1 Branch node for 'stable', got {count}"
