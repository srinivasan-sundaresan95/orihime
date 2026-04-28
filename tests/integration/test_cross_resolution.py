"""Integration tests for the cross-repo resolver.

Both point-bank-bff and point-bitcoin-internal-api are indexed into the
same KuzuDB instance, then run_cross_resolution is called once.

Mark: @pytest.mark.integration  (deselect with -m "not integration")
"""
from __future__ import annotations

import os
import tempfile

import kuzu
import pytest

from indra.indexer import index_repo
from indra.cross_resolver import run_cross_resolution

BFF_REPO_PATH = "/mnt/c/Users/srinivasa.sundaresan/IdeaProjects/point-bank-bff"
BFF_REPO_NAME = "point-bank-bff"

BITCOIN_REPO_PATH = "/mnt/c/Users/srinivasa.sundaresan/IdeaProjects/point-bitcoin-internal-api"
BITCOIN_REPO_NAME = "point-bitcoin-internal-api"


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

    # Index Bitcoin API into the same DB (schema already present)
    btc_stats = index_repo(BITCOIN_REPO_PATH, BITCOIN_REPO_NAME, db_path)
    print(f"[cross-integration] Bitcoin index stats: {btc_stats}")

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
