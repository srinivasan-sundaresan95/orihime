"""Unit tests for P4-3: Kotlin object method call resolution.

Kotlin ``object`` declarations are singletons accessed via a type-like
(capitalised) receiver, e.g. ``DateTimeUtil.isInTimePeriod(42)``.  These are
statically dispatched and cannot be injected via DI, so the impl_index
restriction that ordinarily limits suffix matches to local methods must not
apply to them.

Three test scenarios:
    1. Same-file object call  — resolves to CALLS (baseline, always worked).
    2. Cross-file object call — resolves to CALLS even with impl_index active.
    3. Existing resolver tests — run the full test suite to confirm no regression.
"""
from __future__ import annotations

import uuid

import indra.kotlin_extractor  # noqa: F401 — triggers register()
from indra.language import get_parser
from indra.resolver import build_fqn_index, resolve_calls


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _method(name: str, fqn: str, line_start: int, file_id: str = "file1") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "fqn": fqn,
        "class_id": str(uuid.uuid4()),
        "file_id": file_id,
        "repo_id": "repo1",
        "line_start": line_start,
        "is_suspend": False,
        "annotations": [],
    }


def _parse_kotlin(source: bytes):
    parser = get_parser("kotlin")
    return parser.parse(source), source


# ---------------------------------------------------------------------------
# Test 1 — same-file object call resolves to CALLS (no impl_index)
# ---------------------------------------------------------------------------

SAME_FILE_SRC = b"""
package com.example

object DateTimeUtil {
    fun isInTimePeriod(value: Int): Boolean = value > 0
}

class MyService {
    fun process(): Boolean {
        return DateTimeUtil.isInTimePeriod(42)
    }
}
"""


def test_same_file_object_call_resolves_no_impl_index():
    """DateTimeUtil.isInTimePeriod() in the same file resolves to CALLS (no impl_index)."""
    tree, src = _parse_kotlin(SAME_FILE_SRC)

    from indra.kotlin_extractor import KotlinExtractor
    extractor = KotlinExtractor()
    result = extractor.extract(tree, src, "Test.kt", "repo1")

    fqn_index = build_fqn_index(result.methods)
    edges = resolve_calls(tree, src, result.methods, fqn_index, "file1", "repo1")

    calls = [e for e in edges if e.edge_type == "CALLS" and e.callee_name == "isInTimePeriod"]
    assert len(calls) >= 1, (
        f"Expected CALLS edge for isInTimePeriod (no impl_index). All edges: {edges}"
    )

    # callee_id must be the fqn_index entry, not a fresh uuid
    callee_fqn = "com.example.DateTimeUtil.isInTimePeriod"
    assert calls[0].callee_id == fqn_index[callee_fqn], (
        f"callee_id mismatch. Expected {fqn_index[callee_fqn]!r}, got {calls[0].callee_id!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — cross-file object call resolves to CALLS even with impl_index active
# ---------------------------------------------------------------------------

CALLER_SRC = b"""
package com.example

class MyService {
    fun process(): Boolean {
        return DateTimeUtil.isInTimePeriod(42)
    }
}
"""


def test_cross_file_object_call_resolves_with_impl_index():
    """Cross-file DateTimeUtil.isInTimePeriod() resolves to CALLS when impl_index is active.

    Root cause being tested: when impl_index is not None the suffix lookup used
    to be restricted to _local_method_ids, excluding cross-file object methods.
    The fix allows capitalised-receiver calls to bypass that restriction.
    """
    tree, src = _parse_kotlin(CALLER_SRC)

    # Only the local method is known in this file
    process_m = _method("process", "com.example.MyService.process", line_start=4, file_id="file2")
    local_methods = [process_m]

    # fqn_index spans both files
    datetime_m = _method("isInTimePeriod", "com.example.DateTimeUtil.isInTimePeriod",
                         line_start=5, file_id="file1")
    fqn_index = build_fqn_index([process_m, datetime_m])

    # Test with empty impl_index (active but no DI mappings)
    edges_empty = resolve_calls(tree, src, local_methods, fqn_index, "file2", "repo1",
                                impl_index={})
    calls_empty = [e for e in edges_empty if e.edge_type == "CALLS" and e.callee_name == "isInTimePeriod"]
    assert len(calls_empty) >= 1, (
        f"Expected CALLS for isInTimePeriod with empty impl_index. Edges: {edges_empty}"
    )
    assert calls_empty[0].callee_id == datetime_m["id"], (
        f"callee_id should be datetime_m id. Got: {calls_empty[0].callee_id!r}"
    )

    # Test with non-empty impl_index containing unrelated mapping
    edges_nonempty = resolve_calls(tree, src, local_methods, fqn_index, "file2", "repo1",
                                   impl_index={"com.example.IFoo": "com.example.FooImpl"})
    calls_nonempty = [e for e in edges_nonempty if e.edge_type == "CALLS" and e.callee_name == "isInTimePeriod"]
    assert len(calls_nonempty) >= 1, (
        f"Expected CALLS for isInTimePeriod with non-empty impl_index. Edges: {edges_nonempty}"
    )
    assert calls_nonempty[0].callee_id == datetime_m["id"], (
        f"callee_id should be datetime_m id. Got: {calls_nonempty[0].callee_id!r}"
    )

    # Confirm no UNRESOLVED_CALL for isInTimePeriod in either case
    unresolved_empty = [e for e in edges_empty if e.edge_type == "UNRESOLVED_CALL"
                        and e.callee_name == "isInTimePeriod"]
    assert len(unresolved_empty) == 0, f"Unexpected UNRESOLVED for isInTimePeriod: {unresolved_empty}"

    unresolved_nonempty = [e for e in edges_nonempty if e.edge_type == "UNRESOLVED_CALL"
                           and e.callee_name == "isInTimePeriod"]
    assert len(unresolved_nonempty) == 0, f"Unexpected UNRESOLVED for isInTimePeriod: {unresolved_nonempty}"


def test_cross_file_object_call_callee_id_matches_fqn_index():
    """callee_id on the CALLS edge must exactly match the fqn_index entry (not a fresh uuid)."""
    tree, src = _parse_kotlin(CALLER_SRC)

    process_m = _method("process", "com.example.MyService.process", line_start=4, file_id="file2")
    datetime_m = _method("isInTimePeriod", "com.example.DateTimeUtil.isInTimePeriod",
                         line_start=5, file_id="file1")
    fqn_index = build_fqn_index([process_m, datetime_m])

    edges = resolve_calls(tree, src, [process_m], fqn_index, "file2", "repo1",
                          impl_index={})

    calls = [e for e in edges if e.edge_type == "CALLS" and e.callee_name == "isInTimePeriod"]
    assert len(calls) == 1, f"Expected exactly one CALLS edge: {edges}"
    assert calls[0].callee_id == fqn_index["com.example.DateTimeUtil.isInTimePeriod"]


# ---------------------------------------------------------------------------
# Test 3 — lowercase-receiver calls are NOT affected by the fix
#           (still go through the impl_index gate as before)
# ---------------------------------------------------------------------------

LOWERCASE_SRC = b"""
package com.example

class MyService {
    fun process() {
        someService.doWork()
    }
}
"""


def test_lowercase_receiver_still_unresolved_without_impl_match():
    """someService.doWork() (lowercase receiver) stays UNRESOLVED when impl_index has no match.

    The object-style bypass must NOT apply to lowercase receivers, which are
    DI-injected variables and require an impl_index mapping to resolve.
    """
    tree, src = _parse_kotlin(LOWERCASE_SRC)

    process_m = _method("process", "com.example.MyService.process", line_start=4)
    other_m = _method("doWork", "com.example.OtherService.doWork", line_start=2, file_id="file_other")
    fqn_index = build_fqn_index([process_m, other_m])

    edges = resolve_calls(tree, src, [process_m], fqn_index, "file1", "repo1",
                          impl_index={"com.example.IFoo": "com.example.FooImpl"})

    # doWork should remain UNRESOLVED because impl_index has no entry for it
    unresolved = [e for e in edges if e.edge_type == "UNRESOLVED_CALL" and e.callee_name == "doWork"]
    assert len(unresolved) >= 1, (
        f"Expected UNRESOLVED_CALL for doWork (lowercase receiver, no impl match). Edges: {edges}"
    )


def test_lowercase_receiver_resolves_via_impl_index():
    """someService.doWork() (lowercase receiver) resolves via impl_index when a mapping exists.

    The DI path (impl_index gate) must still work for instance calls.
    """
    tree, src = _parse_kotlin(LOWERCASE_SRC)

    process_m = _method("process", "com.example.MyService.process", line_start=4)
    impl_m = _method("doWork", "com.example.SomeServiceImpl.doWork", line_start=2, file_id="file_impl")
    fqn_index = build_fqn_index([process_m, impl_m])

    edges = resolve_calls(tree, src, [process_m], fqn_index, "file1", "repo1",
                          impl_index={"com.example.ISomeService": "com.example.SomeServiceImpl"})

    calls = [e for e in edges if e.edge_type == "CALLS" and e.callee_name == "doWork"]
    assert len(calls) >= 1, (
        f"Expected CALLS for doWork via impl_index. Edges: {edges}"
    )
    assert calls[0].callee_id == impl_m["id"]
