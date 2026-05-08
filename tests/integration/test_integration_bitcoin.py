"""Integration tests for indexing a downstream Java API service into Orihime."""
from __future__ import annotations

import os
import tempfile

import kuzu
import pytest

from orihime.indexer import index_repo

DOWNSTREAM_REPO_PATH = os.getenv("DOWNSTREAM_REPO_PATH", "/path/to/your/downstream-service")
DOWNSTREAM_REPO_NAME = os.getenv("DOWNSTREAM_REPO_NAME", "downstream-service")


@pytest.fixture(scope="module")
def downstream_conn():
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    index_repo(DOWNSTREAM_REPO_PATH, DOWNSTREAM_REPO_NAME, db_path)
    db = kuzu.Database(db_path)
    conn = kuzu.Connection(db)
    yield conn


@pytest.mark.integration
def test_downstream_method_count(downstream_conn):
    """Indexed downstream repo should contain more than 20 Method nodes."""
    result = downstream_conn.execute("MATCH (m:Method) RETURN count(m)")
    count = result.get_next()[0]
    print(f"\n[downstream] method count = {count}")
    assert count > 20, f"Expected > 20 methods, got {count}"


@pytest.mark.integration
def test_downstream_endpoint_count(downstream_conn):
    """Indexed downstream repo should contain at least 1 Endpoint node."""
    result = downstream_conn.execute("MATCH (e:Endpoint) RETURN count(e)")
    count = result.get_next()[0]
    print(f"\n[downstream] endpoint count = {count}")
    assert count > 0, f"Expected > 0 endpoints, got {count}"


@pytest.mark.integration
def test_downstream_has_repo_node(downstream_conn):
    """A Repo node matching DOWNSTREAM_REPO_NAME should exist after indexing."""
    result = downstream_conn.execute(
        f"MATCH (r:Repo) WHERE r.name = '{DOWNSTREAM_REPO_NAME}' RETURN r"
    )
    rows = []
    while result.has_next():
        rows.append(result.get_next())
    print(f"\n[downstream] repo rows found = {len(rows)}")
    assert len(rows) == 1, (
        f"Expected exactly 1 Repo node for '{DOWNSTREAM_REPO_NAME}', got {len(rows)}"
    )


@pytest.mark.integration
def test_downstream_rest_calls_exist(downstream_conn):
    """Downstream service calls external services — should have at least 1 RestCall node."""
    result = downstream_conn.execute("MATCH (rc:RestCall) RETURN count(rc)")
    count = result.get_next()[0]
    print(f"\n[downstream] rest call count = {count}")
    assert count > 0, f"Expected > 0 RestCall nodes, got {count}"


@pytest.mark.integration
def test_downstream_spot_check_endpoint_methods(downstream_conn):
    """Endpoints should have a recognised HTTP method (GET/POST/PUT/DELETE/PATCH).

    NOTE ON PATH EXTRACTION LIMITATION:
    Some repos store endpoint paths as Java constants
    (e.g. @GetMapping(path = RequestMapping.SOME_CONSTANT)).  The current Java
    extractor only resolves inline string literals, so path fields may be empty
    strings when constants are used.  This test therefore validates http_method
    (which IS extracted correctly from the annotation name) rather than the path
    value.  When the extractor gains constant-reference resolution the assertion
    can be tightened to check actual path content.
    """
    result = downstream_conn.execute("MATCH (e:Endpoint) RETURN e.http_method, e.path")
    rows = []
    while result.has_next():
        rows.append(result.get_next())  # (http_method, path)
    print(f"\n[downstream] endpoint (http_method, path) rows = {rows}")
    assert len(rows) > 0, "No endpoints found in downstream repo"
    known_methods = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
    bad = [r for r in rows if r[0] not in known_methods]
    assert bad == [], (
        f"Found {len(bad)} endpoint(s) with unrecognised http_method: {bad}"
    )
