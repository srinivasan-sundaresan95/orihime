from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import tree_sitter_java
import tree_sitter_kotlin
from tree_sitter import Language, Parser

_LANGUAGE_FACTORIES: dict[str, object] = {
    "java": tree_sitter_java.language,
    "kotlin": tree_sitter_kotlin.language,
}

_parser_cache: dict[str, Parser] = {}
_registry: dict[str, "LanguageExtractor"] = {}


@dataclass
class ExtractResult:
    classes: list[dict] = field(default_factory=list)
    methods: list[dict] = field(default_factory=list)
    endpoints: list[dict] = field(default_factory=list)
    rest_calls: list[dict] = field(default_factory=list)


class LanguageExtractor(Protocol):
    language: str
    file_extensions: frozenset[str]

    def extract(
        self,
        tree,
        source_bytes: bytes,
        file_id: str,
        repo_id: str,
    ) -> ExtractResult: ...


def register(extractor: LanguageExtractor) -> None:
    _registry[extractor.language] = extractor


def get_extractor(lang: str) -> LanguageExtractor | None:
    return _registry.get(lang)


def registered_extensions() -> dict[str, str]:
    result: dict[str, str] = {}
    for extractor in _registry.values():
        for ext in extractor.file_extensions:
            result[ext] = extractor.language
    return result


def get_parser(lang: str) -> Parser:
    if lang in _parser_cache:
        return _parser_cache[lang]
    factory = _LANGUAGE_FACTORIES.get(lang)
    if factory is None:
        raise ValueError(f"Unknown language: {lang!r}")
    ts_language = Language(factory())
    parser = Parser(ts_language)
    _parser_cache[lang] = parser
    return parser
