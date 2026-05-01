"""Integration test: @RestController (non-@Service) calling a @Service impl via interface (P4-2).

Verifies that a Spring @RestController that injects a service interface via
constructor injection produces a resolved CALLS edge to the @Service impl's
method — NOT an UNRESOLVED_CALL.  This is distinct from the existing DI test
(test_di_resolution_e2e.py) where the caller is itself a @Service-annotated
class.  Here the caller has only @RestController, which does NOT make it an
implementing class — so its impl_map entry is empty.  The impl_index populated
by WalletServiceImpl must still allow the resolver to wire the call.
"""
from __future__ import annotations

import os
import shutil
import tempfile

import kuzu
import pytest

from orihime.indexer import index_repo

FIXTURES_DIR = os.path.join(
    os.path.dirname(__file__),
    "..",
    "fixtures",
)

CONTROLLER_DI_FIXTURES = [
    "WalletService.java",
    "WalletServiceImpl.java",
    "WalletControllerDI.java",
]


@pytest.fixture(scope="module")
def controller_di_conn():
    with tempfile.TemporaryDirectory() as repo_dir:
        with tempfile.TemporaryDirectory() as db_dir:
            for fname in CONTROLLER_DI_FIXTURES:
                src = os.path.join(FIXTURES_DIR, fname)
                shutil.copy(src, os.path.join(repo_dir, fname))

            db_path = os.path.join(db_dir, "controller_di_test.db")
            stats = index_repo(
                repo_path=repo_dir,
                repo_name="controller-di-e2e-test",
                db_path=db_path,
                max_workers=1,
            )
            print(f"\n[controller-di-e2e] index stats: {stats}")

            db = kuzu.Database(db_path)
            conn = kuzu.Connection(db)
            yield conn
            del conn, db


@pytest.mark.integration
def test_controller_calls_impl_via_interface(controller_di_conn):
    """CALLS edge must exist: WalletControllerDI.callEndpoint → WalletServiceImpl.getBalance."""
    result = controller_di_conn.execute(
        "MATCH (a:Method)-[:CALLS]->(b:Method) "
        "WHERE a.fqn = 'com.example.WalletControllerDI.callEndpoint' "
        "AND b.fqn = 'com.example.WalletServiceImpl.getBalance' "
        "RETURN a.fqn, b.fqn"
    )

    rows = []
    while result.has_next():
        rows.append(result.get_next())

    assert len(rows) == 1, (
        f"Expected exactly one CALLS edge from WalletControllerDI.callEndpoint "
        f"to WalletServiceImpl.getBalance, got {len(rows)}: {rows}"
    )
    caller_fqn, callee_fqn = rows[0]
    assert "WalletControllerDI" in caller_fqn, f"Unexpected caller FQN: {caller_fqn}"
    assert "WalletServiceImpl" in callee_fqn, f"Expected impl class in callee FQN, got: {callee_fqn}"


@pytest.mark.integration
def test_controller_no_unresolved_calls(controller_di_conn):
    """Zero UNRESOLVED_CALL edges should originate from callEndpoint."""
    result = controller_di_conn.execute(
        "MATCH (a:Method)-[:UNRESOLVED_CALL]->(b:RestCall) "
        "WHERE a.fqn CONTAINS 'callEndpoint' "
        "RETURN a.fqn, b.url_pattern"
    )

    rows = []
    while result.has_next():
        rows.append(result.get_next())

    assert len(rows) == 0, (
        f"Expected 0 UNRESOLVED_CALL edges from callEndpoint, "
        f"got {len(rows)}: {rows}"
    )


@pytest.mark.integration
def test_controller_edge_type_summary(controller_di_conn):
    """Total CALLS >= 1 and UNRESOLVED_CALL == 0 across the controller DI mini-repo."""
    calls_result = controller_di_conn.execute("MATCH ()-[:CALLS]->() RETURN count(*)")
    calls_count = calls_result.get_next()[0]

    unresolved_result = controller_di_conn.execute("MATCH ()-[:UNRESOLVED_CALL]->() RETURN count(*)")
    unresolved_count = unresolved_result.get_next()[0]

    print(
        f"\n[controller-di-e2e] Edge summary — CALLS: {calls_count}, "
        f"UNRESOLVED_CALL: {unresolved_count}"
    )

    assert calls_count >= 1, f"Expected >= 1 CALLS edges total, got {calls_count}"
    assert unresolved_count == 0, (
        f"Expected 0 UNRESOLVED_CALL edges in controller DI mini-repo, got {unresolved_count}"
    )
