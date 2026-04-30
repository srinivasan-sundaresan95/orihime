"""Unit tests for indra.indexer.index_repo."""
from __future__ import annotations

import pathlib
import tempfile

import kuzu
import pytest

from indra.indexer import index_repo

# The fixtures directory contains Sample.java — a single-file mini-repo
FIXTURES_DIR = pathlib.Path(__file__).parent.parent / "fixtures"

_SUMMARY_KEYS = {"repos", "files", "classes", "methods", "endpoints", "rest_calls", "call_edges", "inheritance_edges", "entity_relations"}


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


# ---------------------------------------------------------------------------
# 6. No duplicate CALLS edges
# ---------------------------------------------------------------------------


def test_no_duplicate_calls_edges():
    """After indexing, there must be no duplicate (caller_id, callee_id) CALLS pairs."""
    db_path = _make_db_path()
    index_repo(FIXTURES_DIR, "test-repo", db_path)

    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)

    result = conn.execute(
        "MATCH (a:Method)-[r:CALLS]->(b:Method) RETURN a.id, b.id"
    )
    pairs: list[tuple[str, str]] = []
    while result.has_next():
        row = result.get_next()
        pairs.append((row[0], row[1]))

    unique_pairs = list(set(pairs))
    assert len(pairs) == len(unique_pairs), (
        f"Found {len(pairs) - len(unique_pairs)} duplicate CALLS edges. "
        f"Total={len(pairs)}, unique={len(unique_pairs)}"
    )


# ---------------------------------------------------------------------------
# 7. Parallel output matches serial output
# ---------------------------------------------------------------------------


def test_parallel_matches_serial():
    """max_workers=1 (serial) and max_workers=4 (parallel) must produce identical summaries."""
    db_serial = _make_db_path()
    db_parallel = _make_db_path()

    summary_serial = index_repo(FIXTURES_DIR, "test-repo", db_serial, max_workers=1)
    summary_parallel = index_repo(FIXTURES_DIR, "test-repo", db_parallel, max_workers=4)

    assert summary_serial == summary_parallel, (
        f"Serial and parallel indexing produced different counts:\n"
        f"  serial  ={summary_serial}\n"
        f"  parallel={summary_parallel}"
    )
