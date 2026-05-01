"""Unit tests for KotlinExtractor — object declarations and companion objects."""
from __future__ import annotations

from pathlib import Path

import pytest

from orihime.kotlin_extractor import KotlinExtractor
from orihime.language import get_parser

FIXTURE = Path(__file__).parent.parent / "fixtures" / "KotlinObjects.kt"


@pytest.fixture(scope="module")
def result():
    src = FIXTURE.read_bytes()
    parser = get_parser("kotlin")
    tree = parser.parse(src)
    extractor = KotlinExtractor()
    return extractor.extract(tree, src, "KotlinObjects.kt", "repo1")


# ---------------------------------------------------------------------------
# object DateTimeUtil
# ---------------------------------------------------------------------------

def test_datetime_util_class_found(result):
    names = {c["name"] for c in result.classes}
    assert "DateTimeUtil" in names


def test_datetime_util_is_not_interface(result):
    cls = next(c for c in result.classes if c["name"] == "DateTimeUtil")
    assert cls["is_interface"] is False


def test_datetime_util_fqn(result):
    cls = next(c for c in result.classes if c["name"] == "DateTimeUtil")
    assert cls["fqn"] == "com.example.DateTimeUtil"


def test_datetime_util_method_is_in_time_period(result):
    names = {m["name"] for m in result.methods}
    assert "isInTimePeriod" in names


def test_datetime_util_method_format_date(result):
    names = {m["name"] for m in result.methods}
    assert "formatDate" in names


def test_datetime_util_methods_belong_to_datetime_util(result):
    cls = next(c for c in result.classes if c["name"] == "DateTimeUtil")
    dt_methods = [m for m in result.methods if m["class_id"] == cls["id"]]
    method_names = {m["name"] for m in dt_methods}
    assert method_names == {"isInTimePeriod", "formatDate"}


# ---------------------------------------------------------------------------
# companion object (anonymous) inside SomeService
# ---------------------------------------------------------------------------

def test_companion_class_found(result):
    """A class with 'Companion' in its name must be emitted for the anonymous companion object."""
    companion_classes = [c for c in result.classes if "Companion" in c["name"]]
    assert len(companion_classes) >= 1


def test_companion_class_is_not_interface(result):
    companion_cls = next(c for c in result.classes if "Companion" in c["name"])
    assert companion_cls["is_interface"] is False


def test_companion_synthetic_name_includes_enclosing_class(result):
    """The synthetic name must reference the enclosing class SomeService."""
    companion_classes = [c for c in result.classes if "Companion" in c["name"]]
    assert any("SomeService" in c["name"] for c in companion_classes)


def test_companion_method_create_found(result):
    companion_cls = next(c for c in result.classes if "Companion" in c["name"])
    companion_methods = [m for m in result.methods if m["class_id"] == companion_cls["id"]]
    names = {m["name"] for m in companion_methods}
    assert "create" in names


def test_some_service_class_found(result):
    names = {c["name"] for c in result.classes}
    assert "SomeService" in names


def test_do_work_method_found(result):
    names = {m["name"] for m in result.methods}
    assert "doWork" in names


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
