"""Unit tests for Kotlin data-class generated-method detection (P5-3).

Covers both the pure logic helper _is_kotlin_data_generated and the
KotlinExtractor.extract() integration with the KotlinDataClass.kt fixture.
"""
from __future__ import annotations

from pathlib import Path

from dedalus.kotlin_extractor import _is_kotlin_data_generated, KotlinExtractor
from dedalus.language import get_parser

_FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load_and_extract(filename: str):
    src = (_FIXTURES / filename).read_bytes()
    parser = get_parser("kotlin")
    tree = parser.parse(src)
    extractor = KotlinExtractor()
    return extractor.extract(tree, src, "file-id", "repo-id")


def _find_method(result, name: str):
    return next((m for m in result.methods if m["name"] == name), None)


# ---------------------------------------------------------------------------
# Pure-logic unit tests for _is_kotlin_data_generated
# ---------------------------------------------------------------------------

def test_copy_is_generated():
    assert _is_kotlin_data_generated("copy", True) is True


def test_tostring_is_generated():
    assert _is_kotlin_data_generated("toString", True) is True


def test_hashcode_is_generated():
    assert _is_kotlin_data_generated("hashCode", True) is True


def test_equals_is_generated():
    assert _is_kotlin_data_generated("equals", True) is True


def test_component1_is_generated():
    assert _is_kotlin_data_generated("component1", True) is True


def test_component12_is_generated():
    assert _is_kotlin_data_generated("component12", True) is True


def test_custom_method_not_generated():
    assert _is_kotlin_data_generated("customMethod", True) is False


def test_regular_class_not_generated():
    assert _is_kotlin_data_generated("toString", False) is False


# ---------------------------------------------------------------------------
# KotlinExtractor integration tests against KotlinDataClass.kt
# ---------------------------------------------------------------------------

def test_kotlin_extractor_tostring_tagged():
    result = _load_and_extract("KotlinDataClass.kt")
    m = _find_method(result, "toString")
    assert m is not None, "toString not found in KotlinDataClass"
    assert m["generated"] is True


def test_kotlin_extractor_copy_tagged():
    result = _load_and_extract("KotlinDataClass.kt")
    m = _find_method(result, "copy")
    assert m is not None, "copy not found in KotlinDataClass"
    assert m["generated"] is True


def test_kotlin_extractor_custom_not_tagged():
    result = _load_and_extract("KotlinDataClass.kt")
    m = _find_method(result, "customMethod")
    assert m is not None, "customMethod not found in KotlinDataClass"
    assert m["generated"] is False


def test_kotlin_regular_class_not_tagged():
    result = _load_and_extract("KotlinDataClass.kt")
    m = _find_method(result, "getName")
    assert m is not None, "getName not found in RegularKotlinClass"
    assert m["generated"] is False


# ---------------------------------------------------------------------------
# Integrity check: every method dict must carry a bool "generated" field
# ---------------------------------------------------------------------------

def test_all_kotlin_methods_have_generated_field():
    result = _load_and_extract("KotlinDataClass.kt")
    for m in result.methods:
        assert "generated" in m, f"Method {m['name']} is missing 'generated' key"
        assert isinstance(m["generated"], bool), (
            f"Method {m['name']}: 'generated' must be bool, got {type(m['generated'])}"
        )
