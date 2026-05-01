"""Tests for array-literal annotation syntax in Kotlin and Java extractors.

Kotlin: @GetMapping(value = ["/v5/point_card"])
Java:   @GetMapping(value = {"/v5/point_card"})

These were silently not indexed before the fix because the extractor only
walked the first string_literal child of a value_argument, never descending
into collection_literal (Kotlin) or array_initializer (Java).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from orihime.kotlin_extractor import KotlinExtractor
from orihime.java_extractor import JavaExtractor
from orihime.language import get_parser

KT_FIXTURE = Path(__file__).parent.parent / "fixtures" / "ArrayAnnotationController.kt"
JAVA_FIXTURE = Path(__file__).parent.parent / "fixtures" / "ArrayAnnotationController.java"


# ---------------------------------------------------------------------------
# Kotlin — collection_literal / named-arg forms
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def kt_result():
    src = KT_FIXTURE.read_bytes()
    parser = get_parser("kotlin")
    tree = parser.parse(src)
    return KotlinExtractor().extract(tree, src, "array_kt", "repo1")


def test_kotlin_array_value_endpoint_extracted(kt_result):
    """@GetMapping(value = ['/point_card']) must produce an Endpoint node."""
    paths = {e["path"] for e in kt_result.endpoints}
    assert "/v5/point_card" in paths, (
        f"Expected '/v5/point_card' in endpoints but got: {paths}"
    )


def test_kotlin_array_path_endpoint_extracted(kt_result):
    """@PostMapping(path = ['/point_card/update']) must produce an Endpoint node."""
    paths = {e["path"] for e in kt_result.endpoints}
    assert "/v5/point_card/update" in paths, (
        f"Expected '/v5/point_card/update' in endpoints but got: {paths}"
    )


def test_kotlin_positional_endpoint_still_works(kt_result):
    """@GetMapping('/health') positional form must still be extracted."""
    paths = {e["path"] for e in kt_result.endpoints}
    assert "/v5/health" in paths, (
        f"Expected '/v5/health' in endpoints but got: {paths}"
    )


def test_kotlin_all_three_endpoints_present(kt_result):
    """All three controller methods should produce Endpoint nodes."""
    assert len(kt_result.endpoints) == 3, (
        f"Expected 3 endpoints, got {len(kt_result.endpoints)}: "
        f"{[e['path'] for e in kt_result.endpoints]}"
    )


def test_kotlin_endpoint_http_methods(kt_result):
    """HTTP methods must match the annotation type."""
    by_path = {e["path"]: e["http_method"] for e in kt_result.endpoints}
    assert by_path.get("/v5/point_card") == "GET"
    assert by_path.get("/v5/point_card/update") == "POST"
    assert by_path.get("/v5/health") == "GET"


# ---------------------------------------------------------------------------
# Java — array_initializer / named-arg forms
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def java_result():
    src = JAVA_FIXTURE.read_bytes()
    parser = get_parser("java")
    tree = parser.parse(src)
    return JavaExtractor().extract(tree, src, "array_java", "repo1")


def test_java_array_value_endpoint_extracted(java_result):
    """@GetMapping(value = {'/point_card'}) must produce an Endpoint node."""
    paths = {e["path"] for e in java_result.endpoints}
    assert "/v5/point_card" in paths, (
        f"Expected '/v5/point_card' in endpoints but got: {paths}"
    )


def test_java_array_path_endpoint_extracted(java_result):
    """@PostMapping(path = {'/point_card/update'}) must produce an Endpoint node."""
    paths = {e["path"] for e in java_result.endpoints}
    assert "/v5/point_card/update" in paths, (
        f"Expected '/v5/point_card/update' in endpoints but got: {paths}"
    )


def test_java_positional_endpoint_still_works(java_result):
    """@GetMapping('/health') positional form must still be extracted."""
    paths = {e["path"] for e in java_result.endpoints}
    assert "/v5/health" in paths, (
        f"Expected '/v5/health' in endpoints but got: {paths}"
    )


def test_java_all_three_endpoints_present(java_result):
    """All three controller methods should produce Endpoint nodes."""
    assert len(java_result.endpoints) == 3, (
        f"Expected 3 endpoints, got {len(java_result.endpoints)}: "
        f"{[e['path'] for e in java_result.endpoints]}"
    )


def test_java_endpoint_http_methods(java_result):
    """HTTP methods must match the annotation type."""
    by_path = {e["path"]: e["http_method"] for e in java_result.endpoints}
    assert by_path.get("/v5/point_card") == "GET"
    assert by_path.get("/v5/point_card/update") == "POST"
    assert by_path.get("/v5/health") == "GET"
