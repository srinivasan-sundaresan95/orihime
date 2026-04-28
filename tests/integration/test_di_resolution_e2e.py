"""End-to-end DI resolution integration test (P3-1.3).

A separate mini-repo is needed here to prove DI resolution without noise from
real BFF/bitcoin repos — those have hundreds of unresolved calls that would
mask a regression.
"""
from __future__ import annotations

import os
import shutil
import tempfile

import kuzu
import pytest

from indra.indexer import index_repo

FIXTURES_DIR = os.path.join(
    os.path.dirname(__file__),
    "..",
    "fixtures",
)

DI_FIXTURES = [
    "WalletService.java",
    "WalletServiceImpl.java",
    "NonServiceImpl.java",
    "DICallerClass.java",
]


# Module scope: one indexing pass is expensive enough that repeating it per
# test would dominate runtime and obscure which assertion failed.
@pytest.fixture(scope="module")
def di_conn():
    with tempfile.TemporaryDirectory() as repo_dir:
        with tempfile.TemporaryDirectory() as db_dir:
            for fname in DI_FIXTURES:
                src = os.path.join(FIXTURES_DIR, fname)
                shutil.copy(src, os.path.join(repo_dir, fname))

            db_path = os.path.join(db_dir, "di_test.db")
            stats = index_repo(
                repo_path=repo_dir,
                repo_name="di-e2e-test",
                db_path=db_path,
                max_workers=1,
            )
            print(f"\n[di-e2e] index stats: {stats}")

            db = kuzu.Database(db_path)
            conn = kuzu.Connection(db)
            yield conn
            # Explicitly release DB handles before temp dirs are deleted —
            # KuzuDB holds file locks that cause PermissionError on Windows/WSL2
            # if the directory is removed while the handles are still open.
            del conn, db


@pytest.mark.integration
def test_di_calls_edge_exists(di_conn):
    """A CALLS edge must go from DICallerClass.fetchBalance to WalletServiceImpl.getBalance."""
    result = di_conn.execute(
        "MATCH (a:Method)-[:CALLS]->(b:Method) "
        "WHERE a.fqn = 'com.example.DICallerClass.fetchBalance' "
        "AND b.fqn = 'com.example.WalletServiceImpl.getBalance' "
        "RETURN a.fqn, b.fqn"
    )

    rows = []
    while result.has_next():
        rows.append(result.get_next())

    assert len(rows) == 1, (
        f"Expected exactly one CALLS edge from fetchBalance to WalletServiceImpl.getBalance, "
        f"got {len(rows)}: {rows}"
    )
    caller_fqn, callee_fqn = rows[0]
    assert "DICallerClass" in caller_fqn, f"Unexpected caller FQN: {caller_fqn}"
    # DI resolver must wire to the @Service impl, not the interface method
    assert "WalletServiceImpl" in callee_fqn, f"Expected impl class in callee FQN, got: {callee_fqn}"


@pytest.mark.integration
def test_no_unresolved_call_from_fetch_balance(di_conn):
    """Zero UNRESOLVED_CALL edges should originate from fetchBalance after DI resolution."""
    result = di_conn.execute(
        "MATCH (a:Method)-[:UNRESOLVED_CALL]->(b:RestCall) "
        "WHERE a.fqn CONTAINS 'fetchBalance' "
        "RETURN a.fqn, b.url_pattern"
    )

    rows = []
    while result.has_next():
        rows.append(result.get_next())

    assert len(rows) == 0, (
        f"Expected 0 UNRESOLVED_CALL edges from fetchBalance, "
        f"got {len(rows)}: {rows}"
    )


@pytest.mark.integration
def test_non_service_impl_not_wired(di_conn):
    """NonServiceImpl implements WalletService but has no Spring annotation.

    DI resolution must NOT create a CALLS edge to NonServiceImpl.getBalance —
    only the @Service-annotated WalletServiceImpl must be wired.
    """
    result = di_conn.execute(
        "MATCH (a:Method)-[:CALLS]->(b:Method) "
        "WHERE b.fqn CONTAINS 'NonServiceImpl' "
        "RETURN b.fqn"
    )

    rows = []
    while result.has_next():
        rows.append(result.get_next())

    assert len(rows) == 0, (
        f"Expected zero CALLS edges to NonServiceImpl (not annotated @Service), "
        f"got {len(rows)}: {rows}"
    )


@pytest.mark.integration
def test_di_edge_type_summary(di_conn):
    """Total CALLS >= 1 and UNRESOLVED_CALL == 0 across the whole mini-repo."""
    calls_result = di_conn.execute("MATCH ()-[:CALLS]->() RETURN count(*)")
    calls_count = calls_result.get_next()[0]

    unresolved_result = di_conn.execute("MATCH ()-[:UNRESOLVED_CALL]->() RETURN count(*)")
    unresolved_count = unresolved_result.get_next()[0]

    print(
        f"\n[di-e2e] Edge summary — CALLS: {calls_count}, "
        f"UNRESOLVED_CALL: {unresolved_count}"
    )

    assert calls_count >= 1, f"Expected >= 1 CALLS edges total, got {calls_count}"
    assert unresolved_count == 0, (
        f"Expected 0 UNRESOLVED_CALL edges in DI mini-repo, got {unresolved_count}"
    )
