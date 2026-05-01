"""Unit tests for G4 — Security + Performance Findings UI Tab.

Tests cover:
  1. GET /api/findings?repo=test-repo returns 200 and a JSON list
  2. Findings list items have all required keys
  3. type=security returns only security findings
  4. type=perf returns only complexity/performance findings
  5. min_severity=high returns only HIGH severity items
  6. GET /api/findings/export returns 200 with content-disposition header
"""
from __future__ import annotations

import os
import tempfile
import uuid

import kuzu
import pytest
from starlette.testclient import TestClient

from orihime.schema import init_schema
from orihime.ui_server import _DB, _make_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_db() -> tuple[str, kuzu.Connection]:
    """Create a temp KuzuDB with a populated test repo. Returns (db_path, conn)."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    db = kuzu.Database(db_path)
    conn = kuzu.Connection(db)
    init_schema(conn)
    _populate(conn)
    return db_path, conn


def _populate(conn: kuzu.Connection) -> dict:
    """Insert minimal test data with both security sinks and complexity hints."""
    repo_id   = str(uuid.uuid4())
    file_id   = str(uuid.uuid4())
    class_id  = str(uuid.uuid4())
    method_a  = str(uuid.uuid4())   # calls a known sink (execute) → security finding
    method_b  = str(uuid.uuid4())   # has complexity_hint → perf finding
    rest_id   = str(uuid.uuid4())

    conn.execute(
        "CREATE (:Repo {id: $id, name: $name, root_path: $rp})",
        {"id": repo_id, "name": "test-repo", "rp": "/tmp/test-repo"},
    )
    conn.execute(
        "CREATE (:File {id: $id, path: $path, repo_id: $rid, language: $lang})",
        {"id": file_id, "path": "src/TestService.java", "rid": repo_id, "lang": "java"},
    )
    conn.execute(
        "CREATE (:Class {id: $id, name: $name, fqn: $fqn, file_id: $fid, "
        "repo_id: $rid, is_interface: false, annotations: $ann})",
        {
            "id": class_id,
            "name": "TestService",
            "fqn": "com.example.TestService",
            "fid": file_id,
            "rid": repo_id,
            "ann": [],
        },
    )
    # method_a: calls an unresolved sink ("Statement.execute") — security
    conn.execute(
        "CREATE (:Method {id: $id, name: $name, fqn: $fqn, class_id: $cid, "
        "file_id: $fid, repo_id: $rid, line_start: 10, is_suspend: false, "
        "annotations: $ann, generated: false})",
        {
            "id": method_a,
            "name": "unsafeQuery",
            "fqn": "com.example.TestService.unsafeQuery",
            "cid": class_id,
            "fid": file_id,
            "rid": repo_id,
            "ann": [],
        },
    )
    # method_b: has complexity_hint → perf finding
    conn.execute(
        "CREATE (:Method {id: $id, name: $name, fqn: $fqn, class_id: $cid, "
        "file_id: $fid, repo_id: $rid, line_start: 30, is_suspend: false, "
        "annotations: $ann, generated: false, complexity_hint: $ch})",
        {
            "id": method_b,
            "name": "heavyProcess",
            "fqn": "com.example.TestService.heavyProcess",
            "cid": class_id,
            "fid": file_id,
            "rid": repo_id,
            "ann": [],
            "ch": "O(n2)-candidate",
        },
    )
    # RestCall representing "Statement.execute" called by method_a
    conn.execute(
        "CREATE (:RestCall {id: $id, http_method: $hm, url_pattern: $up, "
        "callee_name: $cn, caller_method_id: $cmid, repo_id: $rid})",
        {
            "id": rest_id,
            "hm": "CALL",
            "up": "",
            "cn": "Statement.execute",
            "cmid": method_a,
            "rid": repo_id,
        },
    )
    conn.execute(
        "MATCH (m:Method), (rc:RestCall) WHERE m.id = $mid AND rc.id = $rid "
        "CREATE (m)-[:UNRESOLVED_CALL]->(rc)",
        {"mid": method_a, "rid": rest_id},
    )
    return {"repo_id": repo_id, "method_a": method_a, "method_b": method_b}


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client() -> TestClient:
    """Return a Starlette TestClient backed by a real populated test DB."""
    db_path, _conn = _make_test_db()
    db = _DB(db_path)
    app = _make_app(db, db_path)
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestApiFindingsBasic:
    def test_returns_200_and_json_list(self, client: TestClient) -> None:
        """GET /api/findings?repo=test-repo must return HTTP 200 and a JSON list."""
        resp = client.get("/api/findings?repo=test-repo")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_findings_items_have_required_keys(self, client: TestClient) -> None:
        """Each finding dict must contain all required keys."""
        resp = client.get("/api/findings?repo=test-repo")
        assert resp.status_code == 200
        data = resp.json()
        required_keys = {"type", "severity", "category", "method_fqn"}
        for item in data:
            for key in required_keys:
                assert key in item, f"Missing key '{key}' in finding: {item}"

    def test_empty_result_for_unknown_repo(self, client: TestClient) -> None:
        """An unknown repo must return 200 and an empty list — not an error."""
        resp = client.get("/api/findings?repo=nonexistent-repo")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_empty_result_when_no_repo_given(self, client: TestClient) -> None:
        """No repo parameter must return 200 and an empty list."""
        resp = client.get("/api/findings")
        assert resp.status_code == 200
        assert resp.json() == []


class TestApiFindingsTypeFilter:
    def test_type_security_returns_only_security(self, client: TestClient) -> None:
        """type=security must return only security-type findings."""
        resp = client.get("/api/findings?repo=test-repo&type=security")
        assert resp.status_code == 200
        data = resp.json()
        for item in data:
            assert item["type"] in ("taint_sink", "cross_service_taint", "second_order"), \
                f"Unexpected security type: {item['type']}"

    def test_type_perf_returns_only_complexity(self, client: TestClient) -> None:
        """type=perf must return only performance/complexity findings."""
        resp = client.get("/api/findings?repo=test-repo&type=perf&min_severity=low")
        assert resp.status_code == 200
        data = resp.json()
        for item in data:
            assert item["type"] == "complexity_hint", \
                f"Expected complexity_hint, got: {item['type']}"

    def test_type_perf_includes_our_method(self, client: TestClient) -> None:
        """The O(n2) method planted in the fixture must appear in perf results."""
        resp = client.get("/api/findings?repo=test-repo&type=perf&min_severity=low")
        assert resp.status_code == 200
        data = resp.json()
        fqns = [item["method_fqn"] for item in data]
        assert "com.example.TestService.heavyProcess" in fqns

    def test_type_security_includes_our_sink(self, client: TestClient) -> None:
        """The Statement.execute sink planted in the fixture must appear in security results."""
        resp = client.get("/api/findings?repo=test-repo&type=security")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0, "Expected at least one security finding"
        fqns = [item["method_fqn"] for item in data]
        assert "com.example.TestService.unsafeQuery" in fqns


class TestApiFindingsSeverityFilter:
    def test_min_severity_high_returns_only_high(self, client: TestClient) -> None:
        """min_severity=high must return only findings with severity=HIGH."""
        resp = client.get("/api/findings?repo=test-repo&min_severity=high")
        assert resp.status_code == 200
        data = resp.json()
        for item in data:
            assert item["severity"] == "HIGH", \
                f"Expected only HIGH, got {item['severity']}: {item}"

    def test_min_severity_low_returns_all_severities(self, client: TestClient) -> None:
        """min_severity=low must return findings across all severity levels."""
        resp = client.get("/api/findings?repo=test-repo&min_severity=low")
        assert resp.status_code == 200
        data = resp.json()
        # Should have at least one finding (O(n2) planted above)
        assert len(data) > 0

    def test_min_severity_medium_excludes_low(self, client: TestClient) -> None:
        """min_severity=medium must not include LOW findings."""
        resp = client.get("/api/findings?repo=test-repo&min_severity=medium")
        assert resp.status_code == 200
        data = resp.json()
        for item in data:
            assert item["severity"] in ("HIGH", "MEDIUM"), \
                f"LOW finding slipped through: {item}"


class TestApiFindingsExport:
    def test_export_returns_200(self, client: TestClient) -> None:
        """GET /api/findings/export must return HTTP 200."""
        resp = client.get("/api/findings/export?repo=test-repo")
        assert resp.status_code == 200

    def test_export_has_content_disposition_header(self, client: TestClient) -> None:
        """Export endpoint must set a Content-Disposition attachment header."""
        resp = client.get("/api/findings/export?repo=test-repo")
        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert "attachment" in cd.lower(), \
            f"Expected Content-Disposition: attachment, got: {cd!r}"

    def test_export_returns_json_content_type(self, client: TestClient) -> None:
        """Export endpoint must return application/json content type."""
        resp = client.get("/api/findings/export?repo=test-repo")
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "application/json" in ct

    def test_export_body_is_valid_json_list(self, client: TestClient) -> None:
        """Export endpoint body must be a valid JSON list."""
        resp = client.get("/api/findings/export?repo=test-repo")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


class TestFindingsPage:
    def test_findings_page_returns_200(self, client: TestClient) -> None:
        """GET /findings must return HTTP 200 with HTML."""
        resp = client.get("/findings")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_findings_page_contains_table(self, client: TestClient) -> None:
        """The /findings page must contain the findings table structure."""
        resp = client.get("/findings")
        assert resp.status_code == 200
        assert "findingsTable" in resp.text

    def test_findings_page_nav_link_present(self, client: TestClient) -> None:
        """The nav bar must contain a 'Findings' link."""
        resp = client.get("/findings")
        assert resp.status_code == 200
        assert "/findings" in resp.text
