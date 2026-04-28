"""Unit tests for indra.indexer.index_repo."""
from __future__ import annotations

import pathlib
import tempfile

import kuzu
import pytest

from indra.indexer import index_repo

# The fixtures directory contains Sample.java — a single-file mini-repo
FIXTURES_DIR = pathlib.Path(__file__).parent.parent / "fixtures"

_SUMMARY_KEYS = {"repos", "files", "classes", "methods", "endpoints", "rest_calls", "call_edges"}


def _make_db_path() -> pathlib.Path:
    """Return a fresh temp-dir path for a KuzuDB database."""
    tmpdir = tempfile.mkdtemp()
    return pathlib.Path(tmpdir) / "test.db"


# ---------------------------------------------------------------------------
# 1. Summary dict shape and positive counts
# ---------------------------------------------------------------------------


def test_index_repo_returns_summary_dict():
    """index_repo returns a dict with all expected keys and values > 0."""
    db_path = _make_db_path()
    summary = index_repo(FIXTURES_DIR, "test-repo", db_path)

    assert isinstance(summary, dict)
    assert _SUMMARY_KEYS == set(summary.keys()), (
        f"Missing keys: {_SUMMARY_KEYS - set(summary.keys())}"
    )
    assert summary["repos"] == 1
    assert summary["files"] > 0, "Expected at least one indexed file"
    assert summary["classes"] > 0, "Expected at least one class"
    assert summary["methods"] > 0, "Expected at least one method"
    assert summary["endpoints"] > 0, "Expected at least one endpoint"


# ---------------------------------------------------------------------------
# 2. Idempotency — second run must not fail and counts must match
# ---------------------------------------------------------------------------


def test_index_repo_idempotent():
    """Indexing the same repo twice must not raise and must yield identical counts."""
    db_path = _make_db_path()
    summary1 = index_repo(FIXTURES_DIR, "test-repo", db_path)
    summary2 = index_repo(FIXTURES_DIR, "test-repo", db_path)

    assert summary1 == summary2, (
        f"Second index run produced different counts:\n  first={summary1}\n  second={summary2}"
    )


# ---------------------------------------------------------------------------
# 3. Repo node is present in KuzuDB after indexing
# ---------------------------------------------------------------------------


def test_index_repo_writes_repo_node():
    """After indexing, a Repo node with the given name must exist in KuzuDB."""
    db_path = _make_db_path()
    repo_name = "my-sample-repo"
    index_repo(FIXTURES_DIR, repo_name, db_path)

    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)

    result = conn.execute("MATCH (r:Repo) RETURN r.name")
    names: list[str] = []
    while result.has_next():
        names.append(result.get_next()[0])

    assert repo_name in names, f"Repo '{repo_name}' not found; present: {names}"


# ---------------------------------------------------------------------------
# 4. Method nodes are written to KuzuDB
# ---------------------------------------------------------------------------


def test_index_repo_writes_methods():
    """After indexing, at least one Method node must exist in KuzuDB."""
    db_path = _make_db_path()
    index_repo(FIXTURES_DIR, "test-repo", db_path)

    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)

    result = conn.execute("MATCH (m:Method) RETURN count(m)")
    count = result.get_next()[0]
    assert count > 0, "Expected at least one Method node in KuzuDB"


# ---------------------------------------------------------------------------
# 5. Endpoint nodes are written to KuzuDB
# ---------------------------------------------------------------------------


def test_index_repo_writes_endpoints():
    """After indexing, at least one Endpoint node must exist in KuzuDB."""
    db_path = _make_db_path()
    index_repo(FIXTURES_DIR, "test-repo", db_path)

    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)

    result = conn.execute("MATCH (e:Endpoint) RETURN count(e)")
    count = result.get_next()[0]
    assert count > 0, "Expected at least one Endpoint node in KuzuDB"
