"""Unit tests for P3-1.1: Spring DI impl_map extraction.

Tests cover _extract_impl_map (unit) and the impl_map field propagated through
ExtractResult (integration with JavaExtractor.extract).

Fixtures used:
  WalletServiceImpl.java  — @Service class implementing WalletService
  NonServiceImpl.java     — plain class implementing WalletService (no annotation)
  ComponentAdapter.java   — @Component class implementing SomePort
  RepositoryImpl.java     — @Repository class implementing DataStore
  Sample.java             — @RestController, no implements clause (existing fixture)
"""
from __future__ import annotations

import pathlib

import pytest

import dedalus.java_extractor  # noqa: F401 — triggers register()
from dedalus.java_extractor import JavaExtractor, _extract_impl_map
from dedalus.language import get_parser

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(fixture_name: str):
    """Return (root_node, source_bytes, package) for a fixture file."""
    src = (FIXTURES / fixture_name).read_bytes()
    parser = get_parser("java")
    tree = parser.parse(src)
    root = tree.root_node
    # Extract package via the same helper used internally
    from dedalus.java_extractor import _extract_package
    package = _extract_package(root, src)
    return root, src, package


def _extract(fixture_name: str):
    """Return ExtractResult for a fixture file via JavaExtractor.extract."""
    src = (FIXTURES / fixture_name).read_bytes()
    parser = get_parser("java")
    tree = parser.parse(src)
    extractor = JavaExtractor()
    return extractor.extract(tree, src, "test_file", "test_repo")


# ---------------------------------------------------------------------------
# 1. _extract_impl_map: @Service class returns correct mapping
# ---------------------------------------------------------------------------

def test_service_impl_map_contains_correct_entry():
    """_extract_impl_map on WalletServiceImpl.java must return a dict containing
    the mapping com.example.WalletService → com.example.WalletServiceImpl.
    """
    root, src, package = _parse("WalletServiceImpl.java")
    result = _extract_impl_map(root, src, package)
    assert isinstance(result, dict)
    assert "com.example.WalletService" in result
    assert result["com.example.WalletService"] == "com.example.WalletServiceImpl"


def test_service_impl_map_has_exactly_one_entry():
    """WalletServiceImpl implements a single interface; the map must have one entry."""
    root, src, package = _parse("WalletServiceImpl.java")
    result = _extract_impl_map(root, src, package)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# 2. _extract_impl_map: class without @Service/@Component returns empty dict
# ---------------------------------------------------------------------------

def test_non_service_impl_map_is_empty():
    """_extract_impl_map on NonServiceImpl.java (no Spring annotation) must return {}."""
    root, src, package = _parse("NonServiceImpl.java")
    result = _extract_impl_map(root, src, package)
    assert isinstance(result, dict)
    assert result == {}


# ---------------------------------------------------------------------------
# 3. ExtractResult.impl_map populated by JavaExtractor.extract — @Service case
# ---------------------------------------------------------------------------

def test_extract_result_impl_map_service():
    """ExtractResult from JavaExtractor.extract on WalletServiceImpl.java must have
    impl_map with the correct WalletService → WalletServiceImpl entry.
    """
    result = _extract("WalletServiceImpl.java")
    assert hasattr(result, "impl_map"), "ExtractResult must have an impl_map field"
    assert "com.example.WalletService" in result.impl_map
    assert result.impl_map["com.example.WalletService"] == "com.example.WalletServiceImpl"


def test_extract_result_impl_map_type_is_dict():
    """impl_map must be a dict (not None, not a list)."""
    result = _extract("WalletServiceImpl.java")
    assert isinstance(result.impl_map, dict)


# ---------------------------------------------------------------------------
# 4. ExtractResult.impl_map is empty for Sample.java (no @Service, no implements)
# ---------------------------------------------------------------------------

def test_extract_result_impl_map_empty_for_sample():
    """Sample.java has @RestController but no implements clause; impl_map must be {}."""
    result = _extract("Sample.java")
    assert hasattr(result, "impl_map"), "ExtractResult must have an impl_map field"
    assert result.impl_map == {}


# ---------------------------------------------------------------------------
# 5. @Component annotation is captured
# ---------------------------------------------------------------------------

def test_component_impl_map_contains_correct_entry():
    """_extract_impl_map on ComponentAdapter.java (@Component implements SomePort)
    must return a mapping com.example.SomePort → com.example.ComponentAdapter.
    """
    root, src, package = _parse("ComponentAdapter.java")
    result = _extract_impl_map(root, src, package)
    assert isinstance(result, dict)
    assert "com.example.SomePort" in result
    assert result["com.example.SomePort"] == "com.example.ComponentAdapter"


def test_component_extract_result_impl_map():
    """ExtractResult from JavaExtractor.extract on ComponentAdapter.java must also
    carry the impl_map entry for SomePort → ComponentAdapter.
    """
    result = _extract("ComponentAdapter.java")
    assert hasattr(result, "impl_map"), "ExtractResult must have an impl_map field"
    assert "com.example.SomePort" in result.impl_map
    assert result.impl_map["com.example.SomePort"] == "com.example.ComponentAdapter"


# ---------------------------------------------------------------------------
# 6. @Repository annotation is captured
# ---------------------------------------------------------------------------

def test_repository_impl_map_contains_correct_entry():
    """_extract_impl_map on RepositoryImpl.java (@Repository implements DataStore)
    must return a mapping com.example.DataStore → com.example.RepositoryImpl.
    """
    root, src, package = _parse("RepositoryImpl.java")
    result = _extract_impl_map(root, src, package)
    assert isinstance(result, dict)
    assert "com.example.DataStore" in result
    assert result["com.example.DataStore"] == "com.example.RepositoryImpl"


def test_repository_extract_result_impl_map():
    """ExtractResult from JavaExtractor.extract on RepositoryImpl.java must carry
    the impl_map entry for DataStore → RepositoryImpl.
    """
    result = _extract("RepositoryImpl.java")
    assert hasattr(result, "impl_map"), "ExtractResult must have an impl_map field"
    assert "com.example.DataStore" in result.impl_map
    assert result.impl_map["com.example.DataStore"] == "com.example.RepositoryImpl"


# ---------------------------------------------------------------------------
# 7. impl_map field default on ExtractResult is an empty dict (not shared state)
# ---------------------------------------------------------------------------

def test_extract_result_impl_map_default_is_empty_dict():
    """Two separate ExtractResult instances must each start with their own empty
    impl_map (default_factory=dict, not a shared mutable default).
    """
    from dedalus.language import ExtractResult
    r1 = ExtractResult()
    r2 = ExtractResult()
    assert r1.impl_map == {}
    assert r2.impl_map == {}
    r1.impl_map["key"] = "value"
    assert r2.impl_map == {}, "impl_map must not be shared across instances"


# ---------------------------------------------------------------------------
# 8. ParseResult.impl_map field exists and defaults to empty dict
# ---------------------------------------------------------------------------

def test_parse_result_impl_map_field_exists():
    """ParseResult must expose an impl_map field that defaults to {}."""
    from dedalus.parse_result import ParseResult
    pr = ParseResult(
        file_id="f1",
        file_path="/some/file.java",
        lang="java",
        repo_id="repo1",
    )
    assert hasattr(pr, "impl_map"), "ParseResult must have an impl_map field"
    assert pr.impl_map == {}


def test_parse_result_impl_map_not_shared():
    """ParseResult.impl_map must use default_factory=dict (not a class-level default)."""
    from dedalus.parse_result import ParseResult
    pr1 = ParseResult(file_id="f1", file_path="/a.java", lang="java", repo_id="r1")
    pr2 = ParseResult(file_id="f2", file_path="/b.java", lang="java", repo_id="r1")
    pr1.impl_map["com.example.Foo"] = "com.example.FooImpl"
    assert pr2.impl_map == {}, "ParseResult.impl_map must not be shared across instances"
