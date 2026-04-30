"""Unit tests for JavaExtractor Lombok generated-method tagging (P5-3).

These tests parse real Java fixture files with tree-sitter and verify that
the ``generated`` field is correctly set on every method dict returned by
JavaExtractor.extract().
"""
from __future__ import annotations

from pathlib import Path

import indra.java_extractor  # noqa: F401 — triggers register()
from indra.java_extractor import JavaExtractor
from indra.language import get_parser

_FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load_and_extract(filename: str):
    src = (_FIXTURES / filename).read_bytes()
    parser = get_parser("java")
    tree = parser.parse(src)
    extractor = JavaExtractor()
    return extractor.extract(tree, src, "file-id", "repo-id")


def _find_method(result, name: str):
    return next((m for m in result.methods if m["name"] == name), None)


# ---------------------------------------------------------------------------
# LombokDataClass.java — @Data generates getters/setters/equals/hashCode/toString
# ---------------------------------------------------------------------------

def test_data_class_getter_tagged():
    result = _load_and_extract("LombokDataClass.java")
    m = _find_method(result, "getName")
    assert m is not None, "getName not found in LombokDataClass"
    assert m["generated"] is True


def test_data_class_setter_tagged():
    result = _load_and_extract("LombokDataClass.java")
    m = _find_method(result, "setName")
    assert m is not None, "setName not found in LombokDataClass"
    assert m["generated"] is True


def test_data_class_age_getter_tagged():
    result = _load_and_extract("LombokDataClass.java")
    m = _find_method(result, "getAge")
    assert m is not None, "getAge not found in LombokDataClass"
    assert m["generated"] is True


def test_data_class_equals_tagged():
    result = _load_and_extract("LombokDataClass.java")
    m = _find_method(result, "equals")
    assert m is not None, "equals not found in LombokDataClass"
    assert m["generated"] is True


def test_data_class_hashcode_tagged():
    result = _load_and_extract("LombokDataClass.java")
    m = _find_method(result, "hashCode")
    assert m is not None, "hashCode not found in LombokDataClass"
    assert m["generated"] is True


def test_data_class_tostring_tagged():
    result = _load_and_extract("LombokDataClass.java")
    m = _find_method(result, "toString")
    assert m is not None, "toString not found in LombokDataClass"
    assert m["generated"] is True


def test_data_class_business_not_tagged():
    result = _load_and_extract("LombokDataClass.java")
    m = _find_method(result, "processData")
    assert m is not None, "processData not found in LombokDataClass"
    assert m["generated"] is False


# ---------------------------------------------------------------------------
# LombokBuilderClass.java — @Builder generates builder() and inner build()
# ---------------------------------------------------------------------------

def test_builder_builder_tagged():
    result = _load_and_extract("LombokBuilderClass.java")
    m = _find_method(result, "builder")
    assert m is not None, "builder not found in LombokBuilderClass"
    assert m["generated"] is True


def test_builder_build_tagged():
    result = _load_and_extract("LombokBuilderClass.java")
    m = _find_method(result, "build")
    assert m is not None, "build not found in LombokBuilderClass"
    assert m["generated"] is True


def test_builder_validate_not_tagged():
    result = _load_and_extract("LombokBuilderClass.java")
    m = _find_method(result, "validate")
    assert m is not None, "validate not found in LombokBuilderClass"
    assert m["generated"] is False


# ---------------------------------------------------------------------------
# NoLombokClass.java — no Lombok annotations, nothing should be flagged
# ---------------------------------------------------------------------------

def test_no_lombok_getName_not_tagged():
    result = _load_and_extract("NoLombokClass.java")
    m = _find_method(result, "getName")
    assert m is not None, "getName not found in NoLombokClass"
    assert m["generated"] is False


def test_no_lombok_doWork_not_tagged():
    result = _load_and_extract("NoLombokClass.java")
    m = _find_method(result, "doWork")
    assert m is not None, "doWork not found in NoLombokClass"
    assert m["generated"] is False


# ---------------------------------------------------------------------------
# ServiceDataClass.java — @Service + @Data: only Lombok-pattern names are tagged
# ---------------------------------------------------------------------------

def test_service_data_business_not_tagged():
    result = _load_and_extract("ServiceDataClass.java")
    m = _find_method(result, "executeBusinessLogic")
    assert m is not None, "executeBusinessLogic not found in ServiceDataClass"
    assert m["generated"] is False


def test_service_data_getter_tagged():
    result = _load_and_extract("ServiceDataClass.java")
    m = _find_method(result, "getConfig")
    assert m is not None, "getConfig not found in ServiceDataClass"
    assert m["generated"] is True


# ---------------------------------------------------------------------------
# Integrity check: every method dict must carry a bool "generated" field
# ---------------------------------------------------------------------------

def test_all_methods_have_generated_field():
    result = _load_and_extract("NoLombokClass.java")
    for m in result.methods:
        assert "generated" in m, f"Method {m['name']} is missing 'generated' key"
        assert isinstance(m["generated"], bool), (
            f"Method {m['name']}: 'generated' must be bool, got {type(m['generated'])}"
        )
