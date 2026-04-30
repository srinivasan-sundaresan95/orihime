"""Integration tests for indexing point-bank-bff into Indra."""
from __future__ import annotations

import os
import tempfile

import kuzu
import pytest

from indra.indexer import index_repo

BFF_REPO_PATH = "/mnt/c/Users/srinivasa.sundaresan/IdeaProjects/point-bank-bff"
BFF_REPO_NAME = "point-bank-bff"


@pytest.fixture(scope="module")
def bff_conn():
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    index_repo(BFF_REPO_PATH, BFF_REPO_NAME, db_path)
    db = kuzu.Database(db_path)
    conn = kuzu.Connection(db)
    yield conn


@pytest.mark.integration
def test_bff_method_count(bff_conn):
    """Indexed BFF repo should contain more than 50 Method nodes."""
    result = bff_conn.execute("MATCH (m:Method) RETURN count(m)")
    count = result.get_next()[0]
    print(f"\n[bff] method count = {count}")
    assert count > 50, f"Expected > 50 methods, got {count}"


@pytest.mark.integration
def test_bff_endpoint_count(bff_conn):
    """BFF uses @BitcoinEndpoint (custom annotation, no standard path field).

    Indra extracts 0 endpoints from point-bank-bff because its controller methods
    are annotated with a domain-specific @BitcoinEndpoint(api, version) rather than
    standard Spring @GetMapping/@PostMapping. This is expected behaviour — custom
    annotation support is Phase 2. The test documents the actual count.
    """
    result = bff_conn.execute("MATCH (e:Endpoint) RETURN count(e)")
    count = result.get_next()[0]
    print(f"\n[bff] endpoint count = {count}")
    # 0 is correct for Phase 1 — BFF uses custom @BitcoinEndpoint, not standard Spring mappings
    assert count == 0, f"Expected 0 endpoints (custom annotation), got {count}"


@pytest.mark.integration
def test_bff_has_repo_node(bff_conn):
    """A Repo node named 'point-bank-bff' should exist after indexing."""
    result = bff_conn.execute(
        "MATCH (r:Repo) WHERE r.name = 'point-bank-bff' RETURN r"
    )
    rows = []
    while result.has_next():
        rows.append(result.get_next())
    print(f"\n[bff] repo rows found = {len(rows)}")
    assert len(rows) == 1, f"Expected exactly 1 Repo node for 'point-bank-bff', got {len(rows)}"


@pytest.mark.integration
def test_bff_endpoints_have_paths(bff_conn):
    """Endpoint path validation — skipped when BFF produces no endpoints (expected).

    point-bank-bff uses @BitcoinEndpoint(api, version) which carries no URL path.
    Until Phase 2 custom-annotation support, the BFF will produce 0 endpoints and
    this test documents that invariant.
    """
    result = bff_conn.execute("MATCH (e:Endpoint) RETURN e.path")
    paths = []
    while result.has_next():
        paths.append(result.get_next()[0])
    print(f"\n[bff] total endpoints checked = {len(paths)}")
    if len(paths) == 0:
        pytest.skip("No endpoints in BFF (custom @BitcoinEndpoint — Phase 2)")
    empty = [p for p in paths if not p]
    assert empty == [], f"Found {len(empty)} endpoint(s) with empty/null path: {empty}"


@pytest.mark.integration
def test_bff_methods_have_line_start(bff_conn):
    """Every non-generated Method node should have line_start > 0.

    Synthetic <init> nodes (generated=true, added in Phase 6-1) have no
    source location by design and are excluded from this check.
    """
    result = bff_conn.execute(
        "MATCH (m:Method) WHERE m.generated = false RETURN m.line_start"
    )
    line_starts = []
    while result.has_next():
        line_starts.append(result.get_next()[0])
    print(f"\n[bff] total non-generated methods checked = {len(line_starts)}")
    assert len(line_starts) > 0, "No methods found to validate"
    bad = [ls for ls in line_starts if ls is None or ls <= 0]
    assert bad == [], f"Found {len(bad)} method(s) with line_start <= 0 or None: {bad[:10]}"
