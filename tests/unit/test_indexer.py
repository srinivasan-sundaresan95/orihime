"""Unit tests for dedalus.indexer.index_repo."""
from __future__ import annotations

import pathlib
import shutil
import tempfile

import kuzu
import pytest

from dedalus.indexer import index_repo, _git_blob_hash

# The fixtures directory contains Sample.java — a single-file mini-repo
FIXTURES_DIR = pathlib.Path(__file__).parent.parent / "fixtures"

_SUMMARY_KEYS = {"repos", "files", "files_skipped", "classes", "methods", "endpoints", "rest_calls", "call_edges", "inheritance_edges", "entity_relations"}


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
    """Indexing the same repo twice must not raise; DB node counts must be unchanged."""
    db_path = _make_db_path()
    index_repo(FIXTURES_DIR, "test-repo", db_path)

    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)

    def _count(table: str) -> int:
        r = conn.execute(f"MATCH (n:{table}) RETURN count(n)")
        return r.get_next()[0]

    counts_before = {t: _count(t) for t in ("Class", "Method", "Endpoint", "File")}
    del db, conn

    index_repo(FIXTURES_DIR, "test-repo", db_path)

    db2 = kuzu.Database(str(db_path))
    conn2 = kuzu.Connection(db2)

    def _count2(table: str) -> int:
        r = conn2.execute(f"MATCH (n:{table}) RETURN count(n)")
        return r.get_next()[0]

    counts_after = {t: _count2(t) for t in ("Class", "Method", "Endpoint", "File")}
    assert counts_before == counts_after, (
        f"DB counts changed on second index run:\n  before={counts_before}\n  after={counts_after}"
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

    comparable_keys = _SUMMARY_KEYS - {"files_skipped"}
    for key in comparable_keys:
        assert summary_serial[key] == summary_parallel[key], (
            f"Key '{key}' differs: serial={summary_serial[key]}, parallel={summary_parallel[key]}"
        )


# ---------------------------------------------------------------------------
# 8. Incremental re-index: unchanged files are skipped
# ---------------------------------------------------------------------------


def test_incremental_skips_unchanged_files():
    """Second index run without changes must skip all files (files_skipped == files)."""
    db_path = _make_db_path()
    summary1 = index_repo(FIXTURES_DIR, "test-repo", db_path, max_workers=1)
    summary2 = index_repo(FIXTURES_DIR, "test-repo", db_path, max_workers=1)

    # On the second run nothing changed → all files should be skipped
    assert summary2["files_skipped"] == summary1["files"], (
        f"Expected all {summary1['files']} files to be skipped, "
        f"but got files_skipped={summary2['files_skipped']}"
    )
    assert summary2["files"] == 0, (
        f"Expected 0 files re-parsed, but got {summary2['files']}"
    )


def test_incremental_reparses_modified_file():
    """When one file changes, only that file is re-parsed; others are skipped."""
    # Work in a temp copy so we can modify files without touching the real fixtures
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_copy = pathlib.Path(tmpdir) / "repo"
        shutil.copytree(FIXTURES_DIR, repo_copy)

        db_path = _make_db_path()
        summary1 = index_repo(repo_copy, "test-repo", db_path, max_workers=1)
        total_files = summary1["files"]

        # Touch Sample.java content (append a comment) so its blob hash changes
        sample = repo_copy / "Sample.java"
        original = sample.read_text()
        sample.write_text(original + "\n// incremental-test-marker\n")

        summary2 = index_repo(repo_copy, "test-repo", db_path, max_workers=1)

        # Exactly one file should be re-parsed, the rest skipped
        assert summary2["files"] == 1, (
            f"Expected exactly 1 file re-parsed, got {summary2['files']}"
        )
        assert summary2["files_skipped"] == total_files - 1, (
            f"Expected {total_files - 1} skipped, got {summary2['files_skipped']}"
        )


def test_force_reparses_all_files():
    """--force flag must re-parse every file even when blob hashes are unchanged."""
    db_path = _make_db_path()
    summary1 = index_repo(FIXTURES_DIR, "test-repo", db_path, max_workers=1)
    summary_force = index_repo(FIXTURES_DIR, "test-repo", db_path, max_workers=1, force=True)

    assert summary_force["files_skipped"] == 0, (
        "Expected 0 files skipped with --force, "
        f"got {summary_force['files_skipped']}"
    )
    assert summary_force["files"] == summary1["files"], (
        f"--force should re-parse all {summary1['files']} files, "
        f"but got {summary_force['files']}"
    )


def test_blob_hash_stored_in_db():
    """File nodes must have a non-empty blob_hash after indexing."""
    db_path = _make_db_path()
    index_repo(FIXTURES_DIR, "test-repo", db_path, max_workers=1)

    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)

    result = conn.execute("MATCH (f:File) RETURN f.blob_hash")
    hashes: list[str] = []
    while result.has_next():
        hashes.append(result.get_next()[0])

    assert hashes, "No File nodes found"
    assert all(h for h in hashes), (
        f"Some File nodes have empty blob_hash: {[h for h in hashes if not h]}"
    )
    # All hashes should be valid hex strings (40-char SHA-1)
    for h in hashes:
        assert len(h) == 40 and all(c in "0123456789abcdef" for c in h), (
            f"Invalid blob_hash format: {h!r}"
        )


def test_git_blob_hash_fallback_outside_git():
    """_git_blob_hash falls back to SHA-1 of bytes for files outside any git repo."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write a file in an isolated dir that is NOT a git repo
        isolated = pathlib.Path(tmpdir) / "isolated.java"
        isolated.write_bytes(b"class A {}")

        h = _git_blob_hash(isolated)
        assert len(h) == 40
        assert all(c in "0123456789abcdef" for c in h)
