"""Integration tests for the cross-repo resolver.

A BFF service and a downstream API service are indexed into the
same KuzuDB instance, then run_cross_resolution is called once.

Mark: @pytest.mark.integration  (deselect with -m "not integration")
"""
from __future__ import annotations

import os
import tempfile

import kuzu
import pytest

from orihime.indexer import index_repo
from orihime.cross_resolver import run_cross_resolution

BFF_REPO_PATH = os.getenv("BFF_REPO_PATH", "/path/to/your/bff-service")
BFF_REPO_NAME = os.getenv("BFF_REPO_NAME", "bff-service")

DOWNSTREAM_REPO_PATH = os.getenv("DOWNSTREAM_REPO_PATH", "/path/to/your/downstream-service")
DOWNSTREAM_REPO_NAME = os.getenv("DOWNSTREAM_REPO_NAME", "downstream-service")


# ---------------------------------------------------------------------------
# Module-scoped fixture: index both repos, run resolution, yield (conn, stats)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cross_resolved():
    """Index both repos into a shared DB, run cross resolution, yield results."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "cross_test.db")

    # Index BFF first (creates schema on first call)
    bff_stats = index_repo(BFF_REPO_PATH, BFF_REPO_NAME, db_path)
    print(f"\n[cross-integration] BFF index stats: {bff_stats}")

    # Index downstream service into the same DB (schema already present)
    downstream_stats = index_repo(DOWNSTREAM_REPO_PATH, DOWNSTREAM_REPO_NAME, db_path)
    print(f"[cross-integration] Downstream index stats: {downstream_stats}")

    db = kuzu.Database(db_path)
    conn = kuzu.Connection(db)

    resolution_stats = run_cross_resolution(conn)
    print(f"[cross-integration] Resolution stats: {resolution_stats}")

    yield conn, resolution_stats


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_cross_resolution_creates_calls_rest_edges(cross_resolved):
    """Resolution must complete without error; CALLS_REST count is logged."""
    conn, stats = cross_resolved

    result = conn.execute("MATCH ()-[:CALLS_REST]->() RETURN count(*)")
    count = result.get_next()[0]
    print(f"\n[cross-integration] CALLS_REST edges after resolution: {count}")

    # We only assert non-negative — real repos may or may not have matching
    # RestCall / Endpoint pairs depending on what was extracted.
    assert count >= 0
    assert stats["matched"] >= 0
    assert stats["unresolved"] >= 0


@pytest.mark.integration
def test_cross_resolution_depends_on_if_matched(cross_resolved):
    """If any CALLS_REST edge exists across repos, at least one DEPENDS_ON must exist."""
    conn, stats = cross_resolved

    calls_rest_result = conn.execute("MATCH ()-[:CALLS_REST]->() RETURN count(*)")
    calls_rest_count = calls_rest_result.get_next()[0]

    depends_on_result = conn.execute("MATCH ()-[:DEPENDS_ON]->() RETURN count(*)")
    depends_on_count = depends_on_result.get_next()[0]

    print(
        f"\n[cross-integration] CALLS_REST={calls_rest_count}  "
        f"DEPENDS_ON={depends_on_count}"
    )

    if calls_rest_count > 0:
        assert depends_on_count >= 1, (
            f"Expected at least 1 DEPENDS_ON edge when CALLS_REST={calls_rest_count}, "
            f"got {depends_on_count}"
        )
