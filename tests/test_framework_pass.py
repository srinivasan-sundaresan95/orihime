"""Tests for the framework_pass.py synthetic CALLS edge passes.

Each pass has:
  - Unit tests with a minimal in-memory KuzuDB fixture
  - A live integration check against the pointclubapp-api index (skipped if
    the DB is not present or the repo is not indexed)
"""
from __future__ import annotations

import os
import tempfile
import textwrap
import uuid
import pytest

# ---------------------------------------------------------------------------
# Fixtures — minimal in-memory KuzuDB with the full schema
# ---------------------------------------------------------------------------

def _make_db():
    """Return a fresh in-memory KuzuDB database with the Orihime schema."""
    import kuzu
    db = kuzu.Database()
    conn = kuzu.Connection(db)
    # Minimal schema — only the node/edge types used by framework_pass
    stmts = [
        "CREATE NODE TABLE Repo(id STRING, name STRING, PRIMARY KEY(id))",
        "CREATE NODE TABLE File(id STRING, path STRING, language STRING, repo_id STRING, PRIMARY KEY(id))",
        "CREATE NODE TABLE Class(id STRING, name STRING, fqn STRING, repo_id STRING, is_interface BOOLEAN, is_object BOOLEAN, annotations STRING[], PRIMARY KEY(id))",
        "CREATE NODE TABLE Method(id STRING, name STRING, fqn STRING, class_id STRING, repo_id STRING, annotations STRING[], file_id STRING, line_start INT64, PRIMARY KEY(id))",
        "CREATE NODE TABLE Endpoint(id STRING, path STRING, http_method STRING, handler_method_id STRING, repo_id STRING, PRIMARY KEY(id))",
        "CREATE NODE TABLE RestCall(id STRING, http_method STRING, url_pattern STRING, callee_name STRING, caller_method_id STRING, repo_id STRING, PRIMARY KEY(id))",
        "CREATE REL TABLE CALLS(FROM Method TO Method, callee_name STRING, caller_arg_pos INT64, callee_param_pos INT64)",
        "CREATE REL TABLE UNRESOLVED_CALL(FROM Method TO RestCall)",
        "CREATE REL TABLE EXTENDS(FROM Class TO Class)",
        "CREATE REL TABLE IMPLEMENTS(FROM Class TO Class)",
    ]
    for s in stmts:
        conn.execute(s)
    return db, conn


def _id() -> str:
    return str(uuid.uuid4())


class _FakeWriter:
    """Wraps a kuzu.Connection as a WriteClient-compatible object."""
    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, params=None):
        if params:
            self._conn.execute(query, params)
        else:
            self._conn.execute(query)


_METHOD_DEFAULTS = {"file_id": "", "line_start": 0}


def _insert(conn, tbl: str, row: dict):
    if tbl == "Method":
        row = {**_METHOD_DEFAULTS, **row}
    props = ", ".join(f"{k}: ${k}" for k in row.keys())
    conn.execute(f"CREATE (:{tbl} {{{props}}})", row)


# ---------------------------------------------------------------------------
# Pass A — @AssertTrue / @AssertFalse
# ---------------------------------------------------------------------------

class TestPassAAssertTrue:
    def _setup(self):
        db, conn = _make_db()
        writer = _FakeWriter(conn)
        repo_id = _id()
        _insert(conn, "Repo", {"id": repo_id, "name": "test-repo"})
        return db, conn, writer, repo_id

    def test_emits_edge_from_constructor_caller_to_assert_true_method(self):
        """Callers of a class's <init> should get edges to its @AssertTrue methods."""
        db, conn, writer, repo_id = self._setup()

        # Validated class: ConfigClass with @AssertTrue method
        cfg_class_id = _id()
        cfg_init_id = _id()
        cfg_assert_id = _id()
        _insert(conn, "Class", {"id": cfg_class_id, "name": "ConfigClass",
                                 "fqn": "com.example.ConfigClass", "repo_id": repo_id,
                                 "is_interface": False, "is_object": False, "annotations": []})
        _insert(conn, "Method", {"id": cfg_init_id, "name": "<init>",
                                  "fqn": "com.example.ConfigClass.<init>",
                                  "class_id": cfg_class_id, "repo_id": repo_id,
                                  "annotations": []})
        _insert(conn, "Method", {"id": cfg_assert_id, "name": "isValid",
                                  "fqn": "com.example.ConfigClass.isValid",
                                  "class_id": cfg_class_id, "repo_id": repo_id,
                                  "annotations": ["AssertTrue"]})

        # Caller class that constructs ConfigClass
        svc_class_id = _id()
        svc_init_id = _id()
        svc_method_id = _id()
        _insert(conn, "Class", {"id": svc_class_id, "name": "ServiceClass",
                                  "fqn": "com.example.ServiceClass", "repo_id": repo_id,
                                  "is_interface": False, "is_object": False, "annotations": []})
        _insert(conn, "Method", {"id": svc_init_id, "name": "<init>",
                                  "fqn": "com.example.ServiceClass.<init>",
                                  "class_id": svc_class_id, "repo_id": repo_id,
                                  "annotations": []})
        _insert(conn, "Method", {"id": svc_method_id, "name": "doSomething",
                                  "fqn": "com.example.ServiceClass.doSomething",
                                  "class_id": svc_class_id, "repo_id": repo_id,
                                  "annotations": []})

        # Wire: ServiceClass.doSomething calls ConfigClass.<init>
        conn.execute(
            "MATCH (a:Method), (b:Method) WHERE a.id = $a AND b.id = $b "
            "CREATE (a)-[:CALLS {callee_name: '<init>', caller_arg_pos: -1, callee_param_pos: -1}]->(b)",
            {"a": svc_method_id, "b": cfg_init_id},
        )

        from orihime.framework_pass import _pass_a_assert_true, _existing_call_pairs
        written = _existing_call_pairs(conn, repo_id)
        count = _pass_a_assert_true(conn, writer, repo_id, written)

        assert count > 0, "Expected at least one synthetic edge"
        # Check the edge exists: svc_method_id → cfg_assert_id
        r = conn.execute(
            "MATCH (a:Method)-[:CALLS]->(b:Method) WHERE a.id = $a AND b.id = $b RETURN count(*)",
            {"a": svc_method_id, "b": cfg_assert_id},
        )
        assert r.get_next()[0] == 1, "Edge from caller to @AssertTrue method not found"

    def test_no_edges_when_no_assert_true_methods(self):
        """No edges emitted when the repo has no @AssertTrue methods."""
        db, conn, writer, repo_id = self._setup()
        cls_id, mid = _id(), _id()
        _insert(conn, "Class", {"id": cls_id, "name": "Foo", "fqn": "com.Foo",
                                  "repo_id": repo_id, "is_interface": False,
                                  "is_object": False, "annotations": []})
        _insert(conn, "Method", {"id": mid, "name": "bar", "fqn": "com.Foo.bar",
                                  "class_id": cls_id, "repo_id": repo_id, "annotations": []})

        from orihime.framework_pass import _pass_a_assert_true, _existing_call_pairs
        written = _existing_call_pairs(conn, repo_id)
        count = _pass_a_assert_true(conn, writer, repo_id, written)
        assert count == 0

    def test_self_validation_edge_emitted_for_own_methods(self):
        """Methods within the validated class itself should get edges to @AssertTrue."""
        db, conn, writer, repo_id = self._setup()
        cls_id = _id()
        assert_mid = _id()
        own_mid = _id()
        _insert(conn, "Class", {"id": cls_id, "name": "Config", "fqn": "com.Config",
                                  "repo_id": repo_id, "is_interface": False,
                                  "is_object": False, "annotations": []})
        _insert(conn, "Method", {"id": assert_mid, "name": "isOk",
                                  "fqn": "com.Config.isOk", "class_id": cls_id,
                                  "repo_id": repo_id, "annotations": ["AssertTrue"]})
        _insert(conn, "Method", {"id": own_mid, "name": "doWork",
                                  "fqn": "com.Config.doWork", "class_id": cls_id,
                                  "repo_id": repo_id, "annotations": []})

        from orihime.framework_pass import _pass_a_assert_true, _existing_call_pairs
        written = _existing_call_pairs(conn, repo_id)
        count = _pass_a_assert_true(conn, writer, repo_id, written)
        assert count > 0
        r = conn.execute(
            "MATCH (a:Method)-[:CALLS]->(b:Method) WHERE a.id = $a AND b.id = $b RETURN count(*)",
            {"a": own_mid, "b": assert_mid},
        )
        assert r.get_next()[0] == 1

    def test_no_duplicate_edges(self):
        """Running the pass twice must not create duplicate edges."""
        db, conn, writer, repo_id = self._setup()
        cls_id, assert_mid, own_mid = _id(), _id(), _id()
        _insert(conn, "Class", {"id": cls_id, "name": "C", "fqn": "com.C",
                                  "repo_id": repo_id, "is_interface": False,
                                  "is_object": False, "annotations": []})
        _insert(conn, "Method", {"id": assert_mid, "name": "isOk",
                                  "fqn": "com.C.isOk", "class_id": cls_id,
                                  "repo_id": repo_id, "annotations": ["AssertTrue"]})
        _insert(conn, "Method", {"id": own_mid, "name": "doWork",
                                  "fqn": "com.C.doWork", "class_id": cls_id,
                                  "repo_id": repo_id, "annotations": []})

        from orihime.framework_pass import _pass_a_assert_true, _existing_call_pairs
        written = _existing_call_pairs(conn, repo_id)
        _pass_a_assert_true(conn, writer, repo_id, written)
        # Second run — written set already contains the edge
        count2 = _pass_a_assert_true(conn, writer, repo_id, written)
        assert count2 == 0, "Second run must emit 0 duplicate edges"


# ---------------------------------------------------------------------------
# Pass B — OncePerRequestFilter / HandlerInterceptor
# ---------------------------------------------------------------------------

class TestPassBFilters:
    def _setup(self):
        db, conn = _make_db()
        writer = _FakeWriter(conn)
        repo_id = _id()
        _insert(conn, "Repo", {"id": repo_id, "name": "test-repo"})
        return db, conn, writer, repo_id

    def _make_filter(self, conn, repo_id, class_name, extends_fqn, method_name="doFilterInternal"):
        base_id = _id()
        filter_class_id = _id()
        filter_method_id = _id()
        _insert(conn, "Class", {"id": base_id, "name": extends_fqn.rsplit(".", 1)[-1],
                                  "fqn": extends_fqn, "repo_id": "external",
                                  "is_interface": False, "is_object": False, "annotations": []})
        _insert(conn, "Class", {"id": filter_class_id, "name": class_name,
                                  "fqn": f"com.example.{class_name}", "repo_id": repo_id,
                                  "is_interface": False, "is_object": False, "annotations": ["Component"]})
        _insert(conn, "Method", {"id": filter_method_id, "name": method_name,
                                  "fqn": f"com.example.{class_name}.{method_name}",
                                  "class_id": filter_class_id, "repo_id": repo_id,
                                  "annotations": ["Override"]})
        conn.execute(
            "MATCH (a:Class), (b:Class) WHERE a.id = $a AND b.id = $b CREATE (a)-[:EXTENDS]->(b)",
            {"a": filter_class_id, "b": base_id},
        )
        return filter_class_id, filter_method_id

    def test_filter_edges_emitted_to_all_handlers(self):
        """doFilterInternal gets wired to every endpoint handler."""
        db, conn, writer, repo_id = self._setup()

        # Create a filter
        _, filter_mid = self._make_filter(
            conn, repo_id, "MyFilter",
            "org.springframework.web.filter.OncePerRequestFilter",
        )

        # Two endpoints
        handler_ids = []
        for i in range(2):
            ctrl_cls_id = _id()
            handler_mid = _id()
            ep_id = _id()
            _insert(conn, "Class", {"id": ctrl_cls_id, "name": f"Ctrl{i}",
                                     "fqn": f"com.Ctrl{i}", "repo_id": repo_id,
                                     "is_interface": False, "is_object": False, "annotations": []})
            _insert(conn, "Method", {"id": handler_mid, "name": f"handle{i}",
                                      "fqn": f"com.Ctrl{i}.handle{i}",
                                      "class_id": ctrl_cls_id, "repo_id": repo_id,
                                      "annotations": ["GetMapping"]})
            _insert(conn, "Endpoint", {"id": ep_id, "path": f"/v1/ep{i}",
                                        "http_method": "GET",
                                        "handler_method_id": handler_mid,
                                        "repo_id": repo_id})
            handler_ids.append(handler_mid)

        from orihime.framework_pass import _pass_b_filters, _existing_call_pairs
        written = _existing_call_pairs(conn, repo_id)
        count = _pass_b_filters(conn, writer, repo_id, written)

        assert count == 2, f"Expected 2 edges (one per handler), got {count}"
        for hid in handler_ids:
            r = conn.execute(
                "MATCH (a:Method)-[:CALLS]->(b:Method) WHERE a.id = $a AND b.id = $b RETURN count(*)",
                {"a": hid, "b": filter_mid},
            )
            assert r.get_next()[0] == 1, f"Missing edge from handler {hid} to filter"

    def test_no_edges_without_filters(self):
        db, conn, writer, repo_id = self._setup()
        cls_id, mid, ep_id = _id(), _id(), _id()
        _insert(conn, "Class", {"id": cls_id, "name": "Ctrl", "fqn": "com.Ctrl",
                                  "repo_id": repo_id, "is_interface": False,
                                  "is_object": False, "annotations": []})
        _insert(conn, "Method", {"id": mid, "name": "get", "fqn": "com.Ctrl.get",
                                  "class_id": cls_id, "repo_id": repo_id, "annotations": []})
        _insert(conn, "Endpoint", {"id": ep_id, "path": "/v1/x", "http_method": "GET",
                                    "handler_method_id": mid, "repo_id": repo_id})
        from orihime.framework_pass import _pass_b_filters, _existing_call_pairs
        written = _existing_call_pairs(conn, repo_id)
        count = _pass_b_filters(conn, writer, repo_id, written)
        assert count == 0

    def test_interceptor_via_implements(self):
        """HandlerInterceptor implementors are detected via IMPLEMENTS edge."""
        db, conn, writer, repo_id = self._setup()

        iface_id = _id()
        impl_id = _id()
        pre_mid = _id()
        _insert(conn, "Class", {"id": iface_id, "name": "HandlerInterceptor",
                                  "fqn": "org.springframework.web.servlet.HandlerInterceptor",
                                  "repo_id": "external", "is_interface": True,
                                  "is_object": False, "annotations": []})
        _insert(conn, "Class", {"id": impl_id, "name": "AuthInterceptor",
                                  "fqn": "com.AuthInterceptor", "repo_id": repo_id,
                                  "is_interface": False, "is_object": False, "annotations": []})
        _insert(conn, "Method", {"id": pre_mid, "name": "preHandle",
                                  "fqn": "com.AuthInterceptor.preHandle",
                                  "class_id": impl_id, "repo_id": repo_id, "annotations": []})
        conn.execute(
            "MATCH (a:Class), (b:Class) WHERE a.id = $a AND b.id = $b CREATE (a)-[:IMPLEMENTS]->(b)",
            {"a": impl_id, "b": iface_id},
        )

        ctrl_cls_id, handler_mid, ep_id = _id(), _id(), _id()
        _insert(conn, "Class", {"id": ctrl_cls_id, "name": "Ctrl", "fqn": "com.Ctrl",
                                  "repo_id": repo_id, "is_interface": False,
                                  "is_object": False, "annotations": []})
        _insert(conn, "Method", {"id": handler_mid, "name": "get", "fqn": "com.Ctrl.get",
                                  "class_id": ctrl_cls_id, "repo_id": repo_id, "annotations": []})
        _insert(conn, "Endpoint", {"id": ep_id, "path": "/v1/x", "http_method": "GET",
                                    "handler_method_id": handler_mid, "repo_id": repo_id})

        from orihime.framework_pass import _pass_b_filters, _existing_call_pairs
        written = _existing_call_pairs(conn, repo_id)
        count = _pass_b_filters(conn, writer, repo_id, written)
        assert count == 1

    def test_no_duplicate_edges(self):
        db, conn, writer, repo_id = self._setup()
        _, filter_mid = self._make_filter(
            conn, repo_id, "F", "org.springframework.web.filter.OncePerRequestFilter"
        )
        cls_id, handler_mid, ep_id = _id(), _id(), _id()
        _insert(conn, "Class", {"id": cls_id, "name": "C", "fqn": "com.C",
                                  "repo_id": repo_id, "is_interface": False,
                                  "is_object": False, "annotations": []})
        _insert(conn, "Method", {"id": handler_mid, "name": "h", "fqn": "com.C.h",
                                  "class_id": cls_id, "repo_id": repo_id, "annotations": []})
        _insert(conn, "Endpoint", {"id": ep_id, "path": "/x", "http_method": "GET",
                                    "handler_method_id": handler_mid, "repo_id": repo_id})

        from orihime.framework_pass import _pass_b_filters, _existing_call_pairs
        written = _existing_call_pairs(conn, repo_id)
        _pass_b_filters(conn, writer, repo_id, written)
        count2 = _pass_b_filters(conn, writer, repo_id, written)
        assert count2 == 0


# ---------------------------------------------------------------------------
# Pass C — @EventListener
# ---------------------------------------------------------------------------

class TestPassCEventListener:
    def _setup(self):
        db, conn = _make_db()
        writer = _FakeWriter(conn)
        repo_id = _id()
        _insert(conn, "Repo", {"id": repo_id, "name": "test-repo"})
        return db, conn, writer, repo_id

    def test_publisher_wired_to_listener(self):
        """publishEvent caller gets a CALLS edge to @EventListener method."""
        db, conn, writer, repo_id = self._setup()

        # @EventListener method
        listener_cls_id = _id()
        listener_mid = _id()
        _insert(conn, "Class", {"id": listener_cls_id, "name": "Handler",
                                  "fqn": "com.Handler", "repo_id": repo_id,
                                  "is_interface": False, "is_object": False, "annotations": []})
        _insert(conn, "Method", {"id": listener_mid, "name": "onFooEvent",
                                  "fqn": "com.Handler.onFooEvent",
                                  "class_id": listener_cls_id, "repo_id": repo_id,
                                  "annotations": ["EventListener"]})

        # Publisher class with a resolved publishEvent CALLS edge
        pub_cls_id = _id()
        pub_mid = _id()
        pe_cls_id = _id()
        pe_mid = _id()
        _insert(conn, "Class", {"id": pub_cls_id, "name": "Publisher",
                                  "fqn": "com.Publisher", "repo_id": repo_id,
                                  "is_interface": False, "is_object": False, "annotations": []})
        _insert(conn, "Method", {"id": pub_mid, "name": "send",
                                  "fqn": "com.Publisher.send",
                                  "class_id": pub_cls_id, "repo_id": repo_id, "annotations": []})
        # publishEvent as an external method (unindexed)
        _insert(conn, "Class", {"id": pe_cls_id, "name": "ApplicationEventPublisher",
                                  "fqn": "org.springframework.context.ApplicationEventPublisher",
                                  "repo_id": "external", "is_interface": True,
                                  "is_object": False, "annotations": []})
        _insert(conn, "Method", {"id": pe_mid, "name": "publishEvent",
                                  "fqn": "org.springframework.context.ApplicationEventPublisher.publishEvent",
                                  "class_id": pe_cls_id, "repo_id": "external", "annotations": []})
        conn.execute(
            "MATCH (a:Method), (b:Method) WHERE a.id = $a AND b.id = $b "
            "CREATE (a)-[:CALLS {callee_name: 'publishEvent', caller_arg_pos: -1, callee_param_pos: -1}]->(b)",
            {"a": pub_mid, "b": pe_mid},
        )

        from orihime.framework_pass import _pass_c_event_listeners, _existing_call_pairs
        written = _existing_call_pairs(conn, repo_id)
        count = _pass_c_event_listeners(conn, writer, repo_id, written)
        assert count == 1
        r = conn.execute(
            "MATCH (a:Method)-[:CALLS]->(b:Method) WHERE a.id = $a AND b.id = $b RETURN count(*)",
            {"a": pub_mid, "b": listener_mid},
        )
        assert r.get_next()[0] == 1

    def test_no_edges_when_no_listeners(self):
        db, conn, writer, repo_id = self._setup()
        cls_id, mid = _id(), _id()
        _insert(conn, "Class", {"id": cls_id, "name": "S", "fqn": "com.S",
                                  "repo_id": repo_id, "is_interface": False,
                                  "is_object": False, "annotations": []})
        _insert(conn, "Method", {"id": mid, "name": "run", "fqn": "com.S.run",
                                  "class_id": cls_id, "repo_id": repo_id, "annotations": []})
        from orihime.framework_pass import _pass_c_event_listeners, _existing_call_pairs
        written = _existing_call_pairs(conn, repo_id)
        count = _pass_c_event_listeners(conn, writer, repo_id, written)
        assert count == 0

    def test_no_edges_when_no_publishers(self):
        """@EventListener exists but nothing calls publishEvent → 0 edges."""
        db, conn, writer, repo_id = self._setup()
        cls_id, mid = _id(), _id()
        _insert(conn, "Class", {"id": cls_id, "name": "H", "fqn": "com.H",
                                  "repo_id": repo_id, "is_interface": False,
                                  "is_object": False, "annotations": []})
        _insert(conn, "Method", {"id": mid, "name": "onEvent", "fqn": "com.H.onEvent",
                                  "class_id": cls_id, "repo_id": repo_id,
                                  "annotations": ["EventListener"]})
        from orihime.framework_pass import _pass_c_event_listeners, _existing_call_pairs
        written = _existing_call_pairs(conn, repo_id)
        count = _pass_c_event_listeners(conn, writer, repo_id, written)
        assert count == 0


# ---------------------------------------------------------------------------
# Live integration tests — pointclubapp-api
# ---------------------------------------------------------------------------

ORIHIME_DB = os.environ.get("ORIHIME_DB_PATH", os.path.expanduser("~/.orihime/orihime.db"))
PCAPP_REPO = "/mnt/c/Users/srinivasa.sundaresan/IdeaProjects/pointclubapp-api"

def _live_conn():
    import kuzu
    db = kuzu.Database(ORIHIME_DB, read_only=True)
    return kuzu.Connection(db)


def _pcapp_repo_id(conn):
    r = conn.execute("MATCH (r:Repo) WHERE r.name = 'pointclubapp-api' RETURN r.id")
    return r.get_next()[0] if r.has_next() else None


@pytest.mark.skipif(
    not os.path.exists(ORIHIME_DB),
    reason="Orihime DB not present",
)
class TestLivePcapp:
    def test_pass_b_filter_classes_detected(self):
        """PCAPP has RequestFilter, ResponseFilter, LoggingFilter — all should be
        detected as OncePerRequestFilter subclasses."""
        conn = _live_conn()
        repo_id = _pcapp_repo_id(conn)
        assert repo_id, "pointclubapp-api not indexed"

        r = conn.execute(
            "MATCH (child:Class)-[:EXTENDS]->(parent:Class) "
            "WHERE child.repo_id = $rid AND parent.name = 'OncePerRequestFilter' "
            "RETURN child.name",
            {"rid": repo_id},
        )
        names = set()
        while r.has_next():
            names.add(r.get_next()[0])

        assert "RequestFilter" in names, f"RequestFilter not found in EXTENDS edges; got {names}"
        assert "ResponseFilter" in names, f"ResponseFilter not found; got {names}"
        assert "LoggingFilter" in names, f"LoggingFilter not found; got {names}"

    def test_pass_b_filter_edges_exist_after_index(self):
        """After re-indexing with framework_pass, every endpoint handler should have
        a CALLS edge to doFilterInternal of at least one filter."""
        conn = _live_conn()
        repo_id = _pcapp_repo_id(conn)
        assert repo_id

        # Find all handler method ids
        r_ep = conn.execute(
            "MATCH (e:Endpoint) WHERE e.repo_id = $rid RETURN e.handler_method_id",
            {"rid": repo_id},
        )
        handler_ids = []
        while r_ep.has_next():
            handler_ids.append(r_ep.get_next()[0])

        assert handler_ids, "No endpoints found"

        # Find doFilterInternal method ids
        r_fm = conn.execute(
            "MATCH (m:Method) WHERE m.repo_id = $rid AND m.name = 'doFilterInternal' RETURN m.id",
            {"rid": repo_id},
        )
        filter_mids = set()
        while r_fm.has_next():
            filter_mids.add(r_fm.get_next()[0])

        assert filter_mids, "No doFilterInternal methods found — OncePerRequestFilter subclasses not indexed"

        # Every handler should have at least one edge to a doFilterInternal
        for hid in handler_ids:
            r = conn.execute(
                "MATCH (a:Method)-[:CALLS]->(b:Method) WHERE a.id = $hid RETURN b.id",
                {"hid": hid},
            )
            callees = set()
            while r.has_next():
                callees.add(r.get_next()[0])
            assert callees & filter_mids, f"Handler {hid} has no edge to any doFilterInternal"

    def test_pass_a_assert_true_methods_exist(self):
        """PCAPP has @AssertTrue methods — they should be in the graph."""
        conn = _live_conn()
        repo_id = _pcapp_repo_id(conn)
        assert repo_id

        r = conn.execute(
            "MATCH (m:Method) WHERE m.repo_id = $rid AND $ann IN m.annotations RETURN count(*) AS cnt",
            {"rid": repo_id, "ann": "AssertTrue"},
        )
        cnt = r.get_next()[0]
        assert cnt > 0, "No @AssertTrue methods found in pointclubapp-api"

    def test_pass_c_no_event_listeners_in_pcapp(self):
        """PCAPP uses no @EventListener — pass C should have emitted 0 edges."""
        conn = _live_conn()
        repo_id = _pcapp_repo_id(conn)
        assert repo_id

        r = conn.execute(
            "MATCH (m:Method) WHERE m.repo_id = $rid AND $ann IN m.annotations RETURN count(*) AS cnt",
            {"rid": repo_id, "ann": "EventListener"},
        )
        cnt = r.get_next()[0]
        assert cnt == 0, f"Expected 0 @EventListener methods in PCAPP, found {cnt}"

    def test_logging_filter_reaches_all_endpoints(self):
        """After framework_pass, LoggingFilter.doFilterInternal should be reachable
        (in the blast radius sense) from every endpoint handler."""
        conn = _live_conn()
        repo_id = _pcapp_repo_id(conn)
        assert repo_id

        # Get LoggingFilter.doFilterInternal id
        r = conn.execute(
            "MATCH (m:Method) WHERE m.repo_id = $rid AND m.fqn CONTAINS 'LoggingFilter.doFilterInternal' RETURN m.id",
            {"rid": repo_id},
        )
        if not r.has_next():
            pytest.skip("LoggingFilter.doFilterInternal not found — re-index first")
        logging_filter_mid = r.get_next()[0]

        # Get all handler ids
        r_ep = conn.execute(
            "MATCH (e:Endpoint) WHERE e.repo_id = $rid RETURN e.path, e.handler_method_id",
            {"rid": repo_id},
        )
        handlers = []
        while r_ep.has_next():
            row = r_ep.get_next()
            handlers.append((row[0], row[1]))

        # Each handler should directly call LoggingFilter.doFilterInternal
        missing = []
        for path, hid in handlers:
            r2 = conn.execute(
                "MATCH (a:Method)-[:CALLS]->(b:Method) WHERE a.id = $hid AND b.id = $fid RETURN count(*)",
                {"hid": hid, "fid": logging_filter_mid},
            )
            if r2.get_next()[0] == 0:
                missing.append(path)

        assert not missing, f"These endpoints lack edge to LoggingFilter.doFilterInternal: {missing}"


# ---------------------------------------------------------------------------
# Pass D — Spring AOP @Around aspects
# ---------------------------------------------------------------------------

class TestPassDAopAspects:
    def _setup(self):
        db, conn = _make_db()
        writer = _FakeWriter(conn)
        repo_id = _id()
        _insert(conn, "Repo", {"id": repo_id, "name": "test-repo"})
        return db, conn, writer, repo_id

    def test_emits_edges_from_annotated_methods_to_advice(self, tmp_path):
        """Methods annotated with @MyAnnotation should get CALLS edges to the @Around advice."""
        db, conn, writer, repo_id = self._setup()

        # Write a fake Kotlin source file with the aspect method
        aspect_src = textwrap.dedent("""\
            @Aspect
            @Component
            class MyAspect {
                @Around(value = "@annotation(myAnnotation) && args(..)")
                fun processControl(
                    joinPoint: ProceedingJoinPoint,
                    myAnnotation: MyAnnotation,
                ): Any {
                    return joinPoint.proceed()
                }
            }
        """)
        src_file = tmp_path / "MyAspect.kt"
        src_file.write_text(aspect_src)

        file_id = _id()
        _insert(conn, "File", {"id": file_id, "path": str(src_file), "language": "kotlin", "repo_id": repo_id})

        # Advice method (line_start=4 to match line with @Around)
        advice_mid = _id()
        _insert(conn, "Method", {
            "id": advice_mid, "name": "processControl",
            "fqn": "com.example.MyAspect.processControl",
            "class_id": _id(), "repo_id": repo_id,
            "annotations": ["Around"],
            "file_id": file_id, "line_start": 4,
        })

        # Two target methods annotated with @MyAnnotation
        target1 = _id()
        target2 = _id()
        _insert(conn, "Method", {
            "id": target1, "name": "doAction",
            "fqn": "com.example.Controller.doAction",
            "class_id": _id(), "repo_id": repo_id,
            "annotations": ["MyAnnotation", "GetMapping"],
        })
        _insert(conn, "Method", {
            "id": target2, "name": "postAction",
            "fqn": "com.example.Controller.postAction",
            "class_id": _id(), "repo_id": repo_id,
            "annotations": ["MyAnnotation", "PostMapping"],
        })

        # Method without the annotation — should NOT get an edge
        unrelated = _id()
        _insert(conn, "Method", {
            "id": unrelated, "name": "healthCheck",
            "fqn": "com.example.Controller.healthCheck",
            "class_id": _id(), "repo_id": repo_id,
            "annotations": ["GetMapping"],
        })

        from orihime.framework_pass import _pass_d_aop_aspects, _existing_call_pairs
        written = _existing_call_pairs(conn, repo_id)
        count = _pass_d_aop_aspects(conn, writer, repo_id, written)

        assert count == 2, f"Expected 2 edges, got {count}"

        for target_id in (target1, target2):
            r = conn.execute(
                "MATCH (a:Method)-[:CALLS]->(b:Method) WHERE a.id = $a AND b.id = $b RETURN count(*)",
                {"a": target_id, "b": advice_mid},
            )
            assert r.get_next()[0] == 1, f"Missing edge from {target_id} to advice"

        # Unrelated method should have no edge to advice
        r2 = conn.execute(
            "MATCH (a:Method)-[:CALLS]->(b:Method) WHERE a.id = $a AND b.id = $b RETURN count(*)",
            {"a": unrelated, "b": advice_mid},
        )
        assert r2.get_next()[0] == 0

    def test_no_edges_when_no_annotation_pointcut(self, tmp_path):
        """@Around with a non-@annotation pointcut should emit no edges."""
        db, conn, writer, repo_id = self._setup()

        aspect_src = textwrap.dedent("""\
            @Aspect
            class TimingAspect {
                @Around("execution(* com.example.service.*.*(..))")
                fun timeMethod(joinPoint: ProceedingJoinPoint): Any = joinPoint.proceed()
            }
        """)
        src_file = tmp_path / "TimingAspect.kt"
        src_file.write_text(aspect_src)

        file_id = _id()
        _insert(conn, "File", {"id": file_id, "path": str(src_file), "language": "kotlin", "repo_id": repo_id})

        advice_mid = _id()
        _insert(conn, "Method", {
            "id": advice_mid, "name": "timeMethod",
            "fqn": "com.example.TimingAspect.timeMethod",
            "class_id": _id(), "repo_id": repo_id,
            "annotations": ["Around"],
            "file_id": file_id, "line_start": 3,
        })

        from orihime.framework_pass import _pass_d_aop_aspects, _existing_call_pairs
        written = _existing_call_pairs(conn, repo_id)
        count = _pass_d_aop_aspects(conn, writer, repo_id, written)
        assert count == 0

    def test_no_edges_when_no_around_methods(self):
        """Repos with no @Around/@Before/@After methods produce 0 edges."""
        db, conn, writer, repo_id = self._setup()
        mid = _id()
        _insert(conn, "Method", {
            "id": mid, "name": "doWork",
            "fqn": "com.example.S.doWork",
            "class_id": _id(), "repo_id": repo_id,
            "annotations": ["Transactional"],
        })
        from orihime.framework_pass import _pass_d_aop_aspects, _existing_call_pairs
        written = _existing_call_pairs(conn, repo_id)
        assert _pass_d_aop_aspects(conn, writer, repo_id, written) == 0

    def test_deduplication(self, tmp_path):
        """Running pass D twice on the same DB should not create duplicate edges."""
        db, conn, writer, repo_id = self._setup()

        aspect_src = textwrap.dedent("""\
            @Aspect
            class MyAspect {
                @Around("@annotation(ann) && args(..)")
                fun doAround(jp: ProceedingJoinPoint, ann: FlagAnnotation): Any = jp.proceed()
            }
        """)
        src_file = tmp_path / "MyAspect.kt"
        src_file.write_text(aspect_src)

        file_id = _id()
        _insert(conn, "File", {"id": file_id, "path": str(src_file), "language": "kotlin", "repo_id": repo_id})

        advice_mid = _id()
        _insert(conn, "Method", {
            "id": advice_mid, "name": "doAround",
            "fqn": "com.example.MyAspect.doAround",
            "class_id": _id(), "repo_id": repo_id,
            "annotations": ["Around"],
            "file_id": file_id, "line_start": 3,
        })
        target_mid = _id()
        _insert(conn, "Method", {
            "id": target_mid, "name": "flaggedMethod",
            "fqn": "com.example.Ctrl.flaggedMethod",
            "class_id": _id(), "repo_id": repo_id,
            "annotations": ["FlagAnnotation"],
        })

        from orihime.framework_pass import _pass_d_aop_aspects, _existing_call_pairs
        written = _existing_call_pairs(conn, repo_id)
        count1 = _pass_d_aop_aspects(conn, writer, repo_id, written)
        count2 = _pass_d_aop_aspects(conn, writer, repo_id, written)
        assert count1 == 1
        assert count2 == 0, "Second run should emit 0 (already in written set)"

        r = conn.execute(
            "MATCH (a:Method)-[:CALLS]->(b:Method) WHERE a.id = $a AND b.id = $b RETURN count(*)",
            {"a": target_mid, "b": advice_mid},
        )
        assert r.get_next()[0] == 1


class TestPassDIntegration:
    """Live-DB tests for Pass D against the pointclubapp-api index."""

    def test_card_state_control_aspect_wired(self):
        """processCardStateControl should have CALLS edges from all @CardStateControlled methods."""
        conn = _live_conn()
        repo_id = _pcapp_repo_id(conn)
        assert repo_id

        r = conn.execute(
            "MATCH (m:Method) WHERE m.repo_id = $rid AND m.name = 'processCardStateControl' RETURN m.id",
            {"rid": repo_id},
        )
        if not r.has_next():
            pytest.skip("processCardStateControl not found — re-index first")
        advice_id = r.get_next()[0]

        # Count @CardStateControlled methods
        r2 = conn.execute(
            "MATCH (m:Method) WHERE m.repo_id = $rid AND $ann IN m.annotations RETURN count(*)",
            {"rid": repo_id, "ann": "CardStateControlled"},
        )
        csc_count = r2.get_next()[0]
        assert csc_count > 0, "No @CardStateControlled methods found"

        # Count CALLS edges to the advice
        r3 = conn.execute(
            "MATCH (caller:Method)-[:CALLS]->(advice:Method) WHERE advice.id = $aid RETURN count(*)",
            {"aid": advice_id},
        )
        edge_count = r3.get_next()[0]
        assert edge_count >= csc_count, (
            f"Expected at least {csc_count} CALLS edges to processCardStateControl, got {edge_count}"
        )

    def test_post_auto_deposit_reaches_aspect(self):
        """postAutoDepositInfo should have a direct CALLS edge to processCardStateControl."""
        conn = _live_conn()
        repo_id = _pcapp_repo_id(conn)
        assert repo_id

        r = conn.execute(
            "MATCH (caller:Method)-[:CALLS]->(advice:Method) "
            "WHERE caller.repo_id = $rid "
            "AND caller.name = 'postAutoDepositInfo' "
            "AND advice.name = 'processCardStateControl' "
            "RETURN count(*)",
            {"rid": repo_id},
        )
        assert r.get_next()[0] == 1, "postAutoDepositInfo → processCardStateControl edge missing"
