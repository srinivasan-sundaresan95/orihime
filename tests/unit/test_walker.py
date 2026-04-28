"""Unit tests for indra.walker.walk_repo.

The language registry (_registry) starts empty — Java/Kotlin extractors
are not auto-registered until a later task.  Every test that relies on
.java/.kt files therefore uses the ``registered_langs`` fixture, which
registers minimal mock extractors for those two languages before the test
runs and cleans up afterwards.
"""
from __future__ import annotations

import pytest

from pathlib import Path
from typing import Iterator

import indra.language as lang_module
from indra.language import ExtractResult, register, registered_extensions
from indra.walker import walk_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockExtractor:
    """Minimal LanguageExtractor-compatible stub."""

    def __init__(self, language: str, extensions: frozenset[str]) -> None:
        self.language = language
        self.file_extensions = extensions

    def extract(self, tree, source_bytes: bytes, file_id: str, repo_id: str) -> ExtractResult:  # pragma: no cover
        return ExtractResult()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def registered_langs():
    """Register mock Java and Kotlin extractors, then restore previous state."""
    java_ext = _MockExtractor("java", frozenset({".java"}))
    kotlin_ext = _MockExtractor("kotlin", frozenset({".kt", ".kts"}))

    # Stash whatever was in the registry before this test
    original_registry = dict(lang_module._registry)

    register(java_ext)
    register(kotlin_ext)

    yield

    # Restore registry to its pre-test state
    lang_module._registry.clear()
    lang_module._registry.update(original_registry)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_walk_repo_yields_java_files(tmp_path: Path, registered_langs) -> None:
    """walk_repo must yield (path, 'java') for a .java file in the root."""
    java_file = tmp_path / "Foo.java"
    java_file.touch()

    results: list[tuple[Path, str]] = list(walk_repo(tmp_path))

    assert len(results) == 1
    path, language = results[0]
    assert path == java_file
    assert language == "java"


def test_walk_repo_yields_kotlin_files(tmp_path: Path, registered_langs) -> None:
    """walk_repo must yield (path, 'kotlin') for a .kt file in the root."""
    kt_file = tmp_path / "Bar.kt"
    kt_file.touch()

    results: list[tuple[Path, str]] = list(walk_repo(tmp_path))

    assert len(results) == 1
    path, language = results[0]
    assert path == kt_file
    assert language == "kotlin"


def test_walk_repo_skips_build_dir(tmp_path: Path, registered_langs) -> None:
    """walk_repo must yield the src file but skip the identical file under build/."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src_file = src_dir / "Main.java"
    src_file.touch()

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_file = build_dir / "Main.java"
    build_file.touch()

    results: list[tuple[Path, str]] = list(walk_repo(tmp_path))

    paths = [r[0] for r in results]
    assert src_file in paths, "src/Main.java must be yielded"
    assert build_file not in paths, "build/Main.java must be skipped"


def test_walk_repo_skips_all_excluded_dirs(tmp_path: Path, registered_langs) -> None:
    """walk_repo must skip every directory listed in _SKIP_DIRS."""
    excluded_dirs = [
        "build",
        "out",
        "generated",
        ".gradle",
        ".git",
        "node_modules",
        ".venv",
        "__pycache__",
        "target",
    ]

    for dir_name in excluded_dirs:
        excluded_dir = tmp_path / dir_name
        excluded_dir.mkdir()
        (excluded_dir / "Hidden.java").touch()

    results: list[tuple[Path, str]] = list(walk_repo(tmp_path))

    assert results == [], (
        f"Expected no files to be yielded, but got: {results}"
    )


def test_walk_repo_only_yields_registered_extensions(tmp_path: Path, registered_langs) -> None:
    """walk_repo must skip files whose suffix is not in registered_extensions()."""
    (tmp_path / "README.md").touch()
    (tmp_path / "config.yml").touch()
    java_file = tmp_path / "Main.java"
    java_file.touch()

    results: list[tuple[Path, str]] = list(walk_repo(tmp_path))

    assert len(results) == 1
    path, language = results[0]
    assert path == java_file
    assert language == "java"
