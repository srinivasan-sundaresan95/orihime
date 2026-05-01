"""Unit tests for resolve_calls() impl_index DI-resolution (P3-1.2).

These tests cover the new optional ``impl_index`` parameter that maps
interface FQNs to implementation class FQNs so that DI-injected calls
that would otherwise be UNRESOLVED_CALL can be emitted as CALLS edges.
"""
from __future__ import annotations

import pathlib
import uuid

import orihime.java_extractor  # noqa: F401 — triggers register()
from orihime.language import get_parser
from orihime.resolver import CallEdge, resolve_calls

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"
DI_CALLER_JAVA = FIXTURES / "DICallerClass.java"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _parse_java(source: bytes):
    parser = get_parser("java")
    return parser.parse(source), source


def _method_dict(name: str, fqn: str, line_start: int) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "fqn": fqn,
        "class_id": str(uuid.uuid4()),
        "file_id": "file-di-test",
        "repo_id": "repo-di-test",
        "line_start": line_start,
        "is_suspend": False,
        "annotations": [],
    }


def _di_caller_setup():
    """Return (tree, source_bytes, methods) for DICallerClass.java.

    fetchBalance() is declared on line 11 (1-based).
    """
    src = DI_CALLER_JAVA.read_bytes()
    tree, source_bytes = _parse_java(src)
    methods = [
        _method_dict(
            "fetchBalance",
            "com.example.DICallerClass.fetchBalance",
            line_start=11,
        )
    ]
    return tree, source_bytes, methods


# ---------------------------------------------------------------------------
# Test 1 — without impl_index, getBalance call is UNRESOLVED_CALL
# ---------------------------------------------------------------------------

def test_without_impl_index_produces_unresolved():
    """resolve_calls() with no impl_index emits UNRESOLVED_CALL for getBalance.

    DICallerClass.fetchBalance() calls walletService.getBalance().
    The fqn_index has no entry for getBalance, so the edge must be
    UNRESOLVED_CALL.  impl_index is intentionally omitted (uses the
    new default).
    """
    tree, source_bytes, methods = _di_caller_setup()

    # fqn_index only contains the caller's own method — no getBalance
    fqn_index = {
        "com.example.DICallerClass.fetchBalance": methods[0]["id"],
    }

    edges = resolve_calls(
        tree, source_bytes, methods, fqn_index, "file-di-test", "repo-di-test"
        # impl_index not passed — tests backward-compatible default path
    )

    unresolved = [e for e in edges if e.edge_type == "UNRESOLVED_CALL"]
    assert len(unresolved) >= 1, (
        f"Expected at least one UNRESOLVED_CALL for getBalance. "
        f"All edges: {edges}"
    )

    unresolved_names_approximated = unresolved  # callee_id is a new uuid each time
    for e in unresolved_names_approximated:
        assert isinstance(e.callee_id, str) and e.callee_id


# ---------------------------------------------------------------------------
# Test 2 — with impl_index, getBalance call becomes a CALLS edge
# ---------------------------------------------------------------------------

def test_with_impl_index_produces_calls_edge():
    """resolve_calls() with impl_index maps interface→impl and emits CALLS.

    impl_index = {"com.example.WalletService": "com.example.WalletServiceImpl"}
    fqn_index  contains "com.example.WalletServiceImpl.getBalance"

    The resolver must look up getBalance in fqn_index via the impl class
    and emit a CALLS edge instead of UNRESOLVED_CALL.
    """
    tree, source_bytes, methods = _di_caller_setup()

    impl_method_id = "impl-method-uuid-123"
    fqn_index = {
        "com.example.DICallerClass.fetchBalance": methods[0]["id"],
        "com.example.WalletServiceImpl.getBalance": impl_method_id,
    }
    impl_index = {
        "com.example.WalletService": "com.example.WalletServiceImpl",
    }

    edges = resolve_calls(
        tree, source_bytes, methods, fqn_index, "file-di-test", "repo-di-test",
        impl_index=impl_index,
    )

    calls_edges = [e for e in edges if e.edge_type == "CALLS"]
    assert len(calls_edges) >= 1, (
        f"Expected at least one CALLS edge after impl_index resolution. "
        f"All edges: {edges}"
    )

    # There must be no UNRESOLVED_CALL for a method that was resolved via impl_index
    unresolved = [e for e in edges if e.edge_type == "UNRESOLVED_CALL"]
    assert len(unresolved) == 0, (
        f"Expected zero UNRESOLVED_CALL edges when impl_index resolves getBalance. "
        f"Unresolved: {unresolved}"
    )


# ---------------------------------------------------------------------------
# Test 3 — impl_index=None is identical to omitting impl_index
# ---------------------------------------------------------------------------

def test_impl_index_none_is_backward_compatible():
    """Passing impl_index=None behaves identically to not passing it.

    Both calls must return the same edge_type distribution: getBalance
    unresolved in both cases because fqn_index has no entry for it.
    """
    tree, source_bytes, methods = _di_caller_setup()

    fqn_index = {
        "com.example.DICallerClass.fetchBalance": methods[0]["id"],
    }

    edges_default = resolve_calls(
        tree, source_bytes, methods, fqn_index, "file-di-test", "repo-di-test"
    )
    edges_none = resolve_calls(
        tree, source_bytes, methods, fqn_index, "file-di-test", "repo-di-test",
        impl_index=None,
    )

    types_default = sorted(e.edge_type for e in edges_default)
    types_none = sorted(e.edge_type for e in edges_none)

    assert types_default == types_none, (
        f"impl_index=None must behave like omitting it. "
        f"default={types_default}, explicit_none={types_none}"
    )


# ---------------------------------------------------------------------------
# Test 4 — impl_index={} (empty dict) falls back to UNRESOLVED_CALL
# ---------------------------------------------------------------------------

def test_impl_index_empty_falls_back_to_unresolved():
    """An empty impl_index provides no mappings; getBalance stays unresolved.

    Even though impl_index is provided as an empty dict, the resolver
    cannot find a mapping for com.example.WalletService, so the call
    must remain UNRESOLVED_CALL.
    """
    tree, source_bytes, methods = _di_caller_setup()

    fqn_index = {
        "com.example.DICallerClass.fetchBalance": methods[0]["id"],
        # WalletServiceImpl.getBalance is in fqn_index but impl_index is empty
        "com.example.WalletServiceImpl.getBalance": "impl-method-uuid-123",
    }
    impl_index: dict[str, str] = {}

    edges = resolve_calls(
        tree, source_bytes, methods, fqn_index, "file-di-test", "repo-di-test",
        impl_index=impl_index,
    )

    unresolved = [e for e in edges if e.edge_type == "UNRESOLVED_CALL"]
    assert len(unresolved) >= 1, (
        f"Expected UNRESOLVED_CALL when impl_index={{}}: {edges}"
    )


# ---------------------------------------------------------------------------
# Test 5 — CALLS edge uses callee_id from fqn_index (not a new uuid)
# ---------------------------------------------------------------------------

def test_impl_index_match_uses_correct_callee_id():
    """The CALLS edge callee_id must equal the value from fqn_index.

    When resolve_calls() resolves getBalance via impl_index, the callee_id
    on the emitted CALLS edge must be the exact string stored in fqn_index
    for "com.example.WalletServiceImpl.getBalance" — not a freshly-generated
    uuid.
    """
    tree, source_bytes, methods = _di_caller_setup()

    impl_method_id = "impl-method-uuid-123"  # fixed, deterministic
    fqn_index = {
        "com.example.DICallerClass.fetchBalance": methods[0]["id"],
        "com.example.WalletServiceImpl.getBalance": impl_method_id,
    }
    impl_index = {
        "com.example.WalletService": "com.example.WalletServiceImpl",
    }

    edges = resolve_calls(
        tree, source_bytes, methods, fqn_index, "file-di-test", "repo-di-test",
        impl_index=impl_index,
    )

    calls_edges = [e for e in edges if e.edge_type == "CALLS"]
    assert len(calls_edges) >= 1, f"No CALLS edge found: {edges}"

    callee_ids = {e.callee_id for e in calls_edges}
    assert impl_method_id in callee_ids, (
        f"Expected callee_id='{impl_method_id}' from fqn_index, "
        f"but got: {callee_ids}"
    )


# ---------------------------------------------------------------------------
# Test 6 — non-DI calls that resolve via fqn_index directly are unaffected
# ---------------------------------------------------------------------------

def test_non_di_call_still_resolves_normally():
    """A standard in-fqn_index call is unaffected by the presence of impl_index.

    Given inline source where methodAlpha() calls methodBeta(), and
    methodBeta is directly in fqn_index, the CALLS edge for that pair
    must still be emitted correctly regardless of impl_index being set.
    """
    source = b"""
package com.example;
public class Helper {
    public void methodAlpha() {
        methodBeta();
    }
    public void methodBeta() {}
}
"""
    tree, source_bytes = _parse_java(source)

    m_alpha = {
        "id": "alpha-uuid",
        "name": "methodAlpha",
        "fqn": "com.example.Helper.methodAlpha",
        "class_id": "class-uuid",
        "file_id": "file-helper",
        "repo_id": "repo-helper",
        "line_start": 4,
        "is_suspend": False,
        "annotations": [],
    }
    m_beta = {
        "id": "beta-uuid",
        "name": "methodBeta",
        "fqn": "com.example.Helper.methodBeta",
        "class_id": "class-uuid",
        "file_id": "file-helper",
        "repo_id": "repo-helper",
        "line_start": 7,
        "is_suspend": False,
        "annotations": [],
    }
    methods = [m_alpha, m_beta]
    fqn_index = {
        "com.example.Helper.methodAlpha": "alpha-uuid",
        "com.example.Helper.methodBeta": "beta-uuid",
    }
    # impl_index is present but maps something unrelated — must not interfere
    impl_index = {
        "com.example.WalletService": "com.example.WalletServiceImpl",
    }

    edges = resolve_calls(
        tree, source_bytes, methods, fqn_index, "file-helper", "repo-helper",
        impl_index=impl_index,
    )

    calls_edges = [e for e in edges if e.edge_type == "CALLS"]
    pairs = {(e.caller_id, e.callee_id) for e in calls_edges}

    assert ("alpha-uuid", "beta-uuid") in pairs, (
        f"Expected alpha→beta CALLS edge unaffected by impl_index. "
        f"All edges: {edges}"
    )

    # No unresolved edges — methodBeta is directly in fqn_index
    unresolved = [e for e in edges if e.edge_type == "UNRESOLVED_CALL"]
    assert len(unresolved) == 0, (
        f"Expected zero UNRESOLVED_CALL when fqn_index covers all calls. "
        f"Unresolved: {unresolved}"
    )
