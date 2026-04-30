"""Unit tests for dedalus.language — ExtractResult, registry, and parser helpers."""
from __future__ import annotations

import dataclasses

import pytest

from dedalus.language import (
    ExtractResult,
    LanguageExtractor,
    get_extractor,
    get_parser,
    register,
    registered_extensions,
)


# ---------------------------------------------------------------------------
# 1. ExtractResult is a proper dataclass with the expected list fields
# ---------------------------------------------------------------------------


def test_extract_result_is_dataclass() -> None:
    """ExtractResult must be a dataclass with classes/methods/endpoints/rest_calls lists."""
    er = ExtractResult(classes=[], methods=[], endpoints=[], rest_calls=[])
    assert isinstance(er.classes, list)
    assert isinstance(er.methods, list)
    assert isinstance(er.endpoints, list)
    assert isinstance(er.rest_calls, list)
    assert dataclasses.is_dataclass(er)


# ---------------------------------------------------------------------------
# 2–3. get_parser returns a working tree-sitter Parser
# ---------------------------------------------------------------------------


def test_get_parser_java_parses_without_error() -> None:
    """Parser for 'java' must parse a trivial class declaration without ERROR nodes."""
    parser = get_parser("java")
    tree = parser.parse(b"class Foo {}")
    assert tree.root_node.type != "ERROR"
    assert not tree.root_node.has_error


def test_get_parser_kotlin_parses_without_error() -> None:
    """Parser for 'kotlin' must parse a trivial function declaration without ERROR nodes."""
    parser = get_parser("kotlin")
    tree = parser.parse(b"fun bar() {}")
    assert tree.root_node.type != "ERROR"
    assert not tree.root_node.has_error


# ---------------------------------------------------------------------------
# 4. get_parser raises ValueError for unknown languages
# ---------------------------------------------------------------------------


def test_get_parser_unknown_raises() -> None:
    """get_parser must raise ValueError for an unrecognised language name."""
    with pytest.raises(ValueError):
        get_parser("cobol")


# ---------------------------------------------------------------------------
# 5. get_parser is cached — same object returned on repeated calls
# ---------------------------------------------------------------------------


def test_get_parser_is_cached() -> None:
    """Two consecutive calls to get_parser('java') must return the identical object."""
    p1 = get_parser("java")
    p2 = get_parser("java")
    assert p1 is p2


# ---------------------------------------------------------------------------
# 6–7. register / get_extractor / registered_extensions
# ---------------------------------------------------------------------------


class _MockExtractor:
    """Minimal LanguageExtractor-compatible object for registry tests."""

    language: str = "testlang"
    file_extensions: frozenset[str] = frozenset({".tst"})

    def extract(self, source: bytes) -> ExtractResult:  # pragma: no cover
        return ExtractResult(classes=[], methods=[], endpoints=[], rest_calls=[])


def test_register_and_get_extractor() -> None:
    """Registering a mock extractor must make it retrievable via get_extractor."""
    mock = _MockExtractor()
    register(mock)
    assert get_extractor("testlang") is mock


def test_registered_extensions_includes_registered() -> None:
    """After registering the mock extractor, registered_extensions() must map '.tst' -> 'testlang'."""
    mock = _MockExtractor()
    register(mock)
    exts = registered_extensions()
    assert ".tst" in exts
    assert exts[".tst"] == "testlang"


# ---------------------------------------------------------------------------
# 8. Built-in Java and Kotlin extractors are present after import
# ---------------------------------------------------------------------------


def test_registered_extensions_includes_java_and_kotlin() -> None:
    """After importing dedalus (with extractors), 'java' and 'kotlin' must appear in registered_extensions()."""
    langs = set(registered_extensions().values())
    assert "java" in langs, f"'java' not found in registered language names: {langs}"
    assert "kotlin" in langs, f"'kotlin' not found in registered language names: {langs}"
