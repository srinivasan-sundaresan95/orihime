"""Unit tests for KotlinExtractor — top-level and extension functions."""
from __future__ import annotations

from pathlib import Path

import pytest

from orihime.kotlin_extractor import KotlinExtractor
from orihime.language import get_parser
from orihime.resolver import build_fqn_index, resolve_calls

FIXTURE = Path(__file__).parent.parent / "fixtures" / "ExtensionFunctions.kt"


@pytest.fixture(scope="module")
def result():
    src = FIXTURE.read_bytes()
    parser = get_parser("kotlin")
    tree = parser.parse(src)
    extractor = KotlinExtractor()
    return extractor.extract(tree, src, "ExtensionFunctions.kt", "repo1")


@pytest.fixture(scope="module")
def parsed():
    src = FIXTURE.read_bytes()
    parser = get_parser("kotlin")
    tree = parser.parse(src)
    return tree, src


# ---------------------------------------------------------------------------
# Synthetic top-level class
# ---------------------------------------------------------------------------

def test_synthetic_kt_class_emitted(result):
    """A synthetic class named ExtensionFunctionsKt must be emitted."""
    names = {c["name"] for c in result.classes}
    assert "ExtensionFunctionsKt" in names


def test_synthetic_kt_class_is_not_interface(result):
    cls = next(c for c in result.classes if c["name"] == "ExtensionFunctionsKt")
    assert cls["is_interface"] is False


def test_synthetic_kt_class_fqn(result):
    cls = next(c for c in result.classes if c["name"] == "ExtensionFunctionsKt")
    assert cls["fqn"] == "com.example.ExtensionFunctionsKt"


# ---------------------------------------------------------------------------
# Extension function methods
# ---------------------------------------------------------------------------

def test_is_in_time_period_method_found(result):
    names = {m["name"] for m in result.methods}
    assert "isInTimePeriod" in names


def test_to_slug_method_found(result):
    names = {m["name"] for m in result.methods}
    assert "toSlug" in names


def test_extension_methods_belong_to_synthetic_class(result):
    cls = next(c for c in result.classes if c["name"] == "ExtensionFunctionsKt")
    ext_methods = {m["name"] for m in result.methods if m["class_id"] == cls["id"]}
    assert "isInTimePeriod" in ext_methods
    assert "toSlug" in ext_methods


def test_extension_method_fqn(result):
    m = next(m for m in result.methods if m["name"] == "isInTimePeriod")
    assert m["fqn"] == "com.example.ExtensionFunctionsKt.isInTimePeriod"


# ---------------------------------------------------------------------------
# ScheduleService class
# ---------------------------------------------------------------------------

def test_schedule_service_class_found(result):
    names = {c["name"] for c in result.classes}
    assert "ScheduleService" in names


def test_check_method_found(result):
    names = {m["name"] for m in result.methods}
    assert "check" in names


def test_check_method_belongs_to_schedule_service(result):
    cls = next(c for c in result.classes if c["name"] == "ScheduleService")
    check = next(m for m in result.methods if m["name"] == "check")
    assert check["class_id"] == cls["id"]


# ---------------------------------------------------------------------------
# Resolver integration: suffix_index matches callers of extension function
# ---------------------------------------------------------------------------

def test_suffix_index_resolves_is_in_time_period(result, parsed):
    """ScheduleService.check calls isInTimePeriod — resolver should find it via suffix_index."""
    tree, src = parsed
    fqn_index = build_fqn_index(result.methods)
    edges = resolve_calls(tree, src, result.methods, fqn_index, "ExtensionFunctions.kt", "repo1")

    # Find the method id of isInTimePeriod
    is_in_time_period_id = next(
        m["id"] for m in result.methods if m["name"] == "isInTimePeriod"
    )
    # Find the method id of check
    check_id = next(m["id"] for m in result.methods if m["name"] == "check")

    calls_edges = [e for e in edges if e.edge_type == "CALLS"]
    # There should be a CALLS edge from check → isInTimePeriod
    matching = [e for e in calls_edges if e.caller_id == check_id and e.callee_id == is_in_time_period_id]
    assert len(matching) >= 1, (
        f"Expected a CALLS edge from check to isInTimePeriod. Edges: {edges}"
    )


# ---------------------------------------------------------------------------
# General integrity
# ---------------------------------------------------------------------------

def test_all_methods_have_non_empty_ids(result):
    for m in result.methods:
        assert m["id"] != "", f"Empty id for method {m['name']}"


def test_all_classes_have_non_empty_ids(result):
    for c in result.classes:
        assert c["id"] != "", f"Empty id for class {c['name']}"


def test_method_class_ids_reference_known_classes(result):
    class_ids = {c["id"] for c in result.classes}
    for m in result.methods:
        assert m["class_id"] in class_ids, f"Method {m['name']} has unknown class_id"


def test_line_starts_positive(result):
    for m in result.methods:
        # Synthetic <init> methods use line_start=0; all real methods must be > 0
        if m["name"] == "<init>":
            assert m["line_start"] == 0, f"<init> should have line_start=0, got {m['line_start']}"
        else:
            assert m["line_start"] > 0, f"line_start not > 0 for {m['name']}"
