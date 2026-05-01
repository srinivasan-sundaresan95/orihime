"""Integration test for P4-1: callee_name persisted on RestCall nodes in KuzuDB."""
from __future__ import annotations

import os
import shutil
import tempfile

import kuzu
import pytest

from orihime.indexer import index_repo

# ---------------------------------------------------------------------------
# Inline Java fixture: one method that calls an unknown method
# ---------------------------------------------------------------------------
_FIXTURE_SOURCE = b"""
package com.example;

public class UnresolvedCaller {
    public void triggerUnresolved() {
        unknownExternalCall();
    }
}
"""

_FIXTURE_FILENAME = "UnresolvedCaller.java"


# ---------------------------------------------------------------------------
# Module-scoped fixture: index once, yield connection, release before cleanup
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def unresolved_conn():
    with tempfile.TemporaryDirectory() as repo_dir:
        # Write the fixture Java file into the temp repo
        fixture_path = os.path.join(repo_dir, _FIXTURE_FILENAME)
        with open(fixture_path, "wb") as fh:
            fh.write(_FIXTURE_SOURCE)

        with tempfile.TemporaryDirectory() as db_dir:
            db_path = os.path.join(db_dir, "unresolved_test.db")
            stats = index_repo(
                repo_path=repo_dir,
                repo_name="unresolved-callee-test",
                db_path=db_path,
                max_workers=1,
            )
            print(f"\n[unresolved-callee-test] index stats: {stats}")

            db = kuzu.Database(db_path)
            conn = kuzu.Connection(db)
            yield conn
            # Release DB handles before temp dirs are removed (WSL2 file lock safety)
            del conn, db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_restcall_node_has_callee_name(unresolved_conn):
    """RestCall stub node created from an UNRESOLVED_CALL must have a non-empty callee_name."""
    result = unresolved_conn.execute(
        "MATCH (rc:RestCall) "
        "WHERE rc.url_pattern = 'UNRESOLVED' "
        "RETURN rc.callee_name AS callee_name"
    )
    rows = []
    while result.has_next():
        rows.append(result.get_next())

    assert len(rows) >= 1, (
        "Expected at least one RestCall node with url_pattern='UNRESOLVED'; got none."
    )

    callee_names = [row[0] for row in rows]
    non_empty = [n for n in callee_names if n]
    assert non_empty, (
        f"All RestCall nodes have empty callee_name. Values found: {callee_names}"
    )


@pytest.mark.integration
def test_restcall_callee_name_matches_source(unresolved_conn):
    """The callee_name on the RestCall stub must be 'unknownExternalCall'."""
    result = unresolved_conn.execute(
        "MATCH (rc:RestCall) "
        "WHERE rc.url_pattern = 'UNRESOLVED' "
        "RETURN rc.callee_name AS callee_name"
    )
    callee_names = []
    while result.has_next():
        callee_names.append(result.get_next()[0])

    assert "unknownExternalCall" in callee_names, (
        f"Expected 'unknownExternalCall' in RestCall.callee_name values. Got: {callee_names}"
    )
