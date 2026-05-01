"""Unit tests for orihime.schema — all tests hit a real (temp-dir) KuzuDB instance."""
from __future__ import annotations

import tempfile

import kuzu
import pytest

from orihime.schema import SCHEMA_VERSION, create_schema, drop_schema, init_schema

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NODE_TABLES = {"Repo", "File", "Class", "Method", "Endpoint", "RestCall"}
_REL_TABLES = {
    "CALLS",
    "CALLS_REST",
    "UNRESOLVED_CALL",
    "CONTAINS_CLASS",
    "CONTAINS_METHOD",
    "EXPOSES",
    "DEPENDS_ON",
}


def make_conn() -> kuzu.Connection:
    """Open a fresh KuzuDB connection backed by a temporary directory.

    kuzu.Database in 0.11.x expects a *file path* (not a directory path).
    We point it at a non-existent file inside the temp dir so KuzuDB creates
    its own directory structure there.
    """
    import os

    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    db = kuzu.Database(db_path)
    return kuzu.Connection(db)


def get_table_names(conn: kuzu.Connection) -> set[str]:
    """Return the set of all table names currently in the database."""
    result = conn.execute("CALL show_tables() RETURN name")
    names: set[str] = set()
    while result.has_next():
        names.add(result.get_next()[0])
    return names


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_schema_version_is_int() -> None:
    """SCHEMA_VERSION must be an int with value >= 1."""
    assert isinstance(SCHEMA_VERSION, int)
    assert SCHEMA_VERSION >= 1


def test_create_schema_creates_all_node_tables() -> None:
    """After create_schema, all 6 node tables must be present."""
    conn = make_conn()
    create_schema(conn)
    tables = get_table_names(conn)
    assert _NODE_TABLES.issubset(tables), (
        f"Missing node tables: {_NODE_TABLES - tables}"
    )


def test_create_schema_creates_all_rel_tables() -> None:
    """After create_schema, all 7 rel tables must be present."""
    conn = make_conn()
    create_schema(conn)
    tables = get_table_names(conn)
    assert _REL_TABLES.issubset(tables), (
        f"Missing rel tables: {_REL_TABLES - tables}"
    )


def test_drop_schema_removes_all_tables() -> None:
    """After create_schema then drop_schema, zero tables should remain."""
    conn = make_conn()
    create_schema(conn)
    drop_schema(conn)
    assert get_table_names(conn) == set()


def test_init_schema_is_idempotent() -> None:
    """Calling init_schema twice must not raise and must leave all tables present."""
    conn = make_conn()
    init_schema(conn)
    init_schema(conn)  # second call must not raise
    tables = get_table_names(conn)
    assert _NODE_TABLES.issubset(tables), (
        f"Missing node tables after second init: {_NODE_TABLES - tables}"
    )
    assert _REL_TABLES.issubset(tables), (
        f"Missing rel tables after second init: {_REL_TABLES - tables}"
    )


def test_drop_schema_is_safe_on_empty_db() -> None:
    """drop_schema on a fresh (empty) connection must not raise."""
    conn = make_conn()
    drop_schema(conn)  # must not raise
    assert get_table_names(conn) == set()
