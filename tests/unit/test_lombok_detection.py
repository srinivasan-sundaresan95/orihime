"""Unit tests for _is_lombok_generated (P5-3) — pure logic, no tree-sitter."""
from __future__ import annotations

from indra.java_extractor import _is_lombok_generated


# ---------------------------------------------------------------------------
# @Data annotation — generates getters, setters, equals, hashCode, toString,
# canEqual for all fields.
# ---------------------------------------------------------------------------

def test_getter_with_data():
    assert _is_lombok_generated("getName", ["Data"]) is True


def test_setter_with_data():
    assert _is_lombok_generated("setName", ["Data"]) is True


def test_is_getter_with_getter():
    assert _is_lombok_generated("isActive", ["Getter"]) is True


def test_equals_with_data():
    assert _is_lombok_generated("equals", ["Data"]) is True


def test_hashcode_with_data():
    assert _is_lombok_generated("hashCode", ["Data"]) is True


def test_tostring_with_data():
    assert _is_lombok_generated("toString", ["Data"]) is True


def test_canequal_with_data():
    assert _is_lombok_generated("canEqual", ["Data"]) is True


# ---------------------------------------------------------------------------
# @Builder annotation — generates builder() factory and inner Builder.build().
# ---------------------------------------------------------------------------

def test_builder_with_builder():
    assert _is_lombok_generated("builder", ["Builder"]) is True


def test_build_with_builder():
    assert _is_lombok_generated("build", ["Builder"]) is True


# ---------------------------------------------------------------------------
# Negative cases — business methods must never be flagged generated.
# ---------------------------------------------------------------------------

def test_business_method_with_data_not_flagged():
    assert _is_lombok_generated("processData", ["Data"]) is False


def test_getter_without_lombok_not_flagged():
    assert _is_lombok_generated("getName", ["Service"]) is False


def test_getter_no_annotations_not_flagged():
    assert _is_lombok_generated("getName", []) is False


def test_validate_with_builder_not_flagged():
    assert _is_lombok_generated("validate", ["Builder"]) is False


def test_business_method_with_service_and_data():
    assert _is_lombok_generated("executeBusinessLogic", ["Service", "Data"]) is False


def test_getter_with_service_and_data():
    assert _is_lombok_generated("getConfig", ["Service", "Data"]) is True
