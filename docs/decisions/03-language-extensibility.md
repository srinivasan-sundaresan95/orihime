# Decision: Language Extensibility

## The problem with hardcoding

The first version indexes Java and Kotlin. A naive implementation would do:

```python
if lang == "java":
    result = java_extractor.extract(tree, src, file_id, repo_id)
elif lang == "kotlin":
    result = kotlin_extractor.extract(tree, src, file_id, repo_id)
```

This means adding Python/Go/TypeScript support requires modifying the orchestrator, the walker, and any dispatch code. It also makes it impossible to test a new language extractor in isolation before wiring it in.

## The solution: LanguageExtractor Protocol + registry

Every language extractor implements a structural protocol:

```python
from typing import Protocol
from dataclasses import dataclass

class LanguageExtractor(Protocol):
    language: str                        # "java", "kotlin", "python", ...
    file_extensions: frozenset[str]      # {".java"}, {".kt", ".kts"}, ...

    def extract(
        self,
        tree,                            # tree_sitter.Tree
        source_bytes: bytes,
        file_id: str,
        repo_id: str,
    ) -> "ExtractResult": ...
```

Extractors self-register at import time:

```python
# java_extractor.py
@dataclass
class JavaExtractor:
    language: str = "java"
    file_extensions: frozenset[str] = frozenset({".java"})

    def extract(self, tree, source_bytes, file_id, repo_id) -> ExtractResult:
        ...

register(JavaExtractor())   # one line at module bottom
```

The walker calls `registered_extensions()` — no hardcoded extensions anywhere.
The orchestrator calls `get_extractor(lang).extract(...)` — no language dispatch anywhere.

## Adding a new language (6 steps)

See `docs/adding-a-language.md` (written at end of T21) for the full guide. Summary:

1. `pip install tree-sitter-<language>`
2. Create `dedalus/<language>_extractor.py` implementing `LanguageExtractor`
3. Call `register(<LanguageExtractor>())` at module bottom
4. Import the module in `dedalus/__init__.py` to trigger registration
5. Add fixture files to `tests/fixtures/`
6. Write `test_<language>_extractor.py`

No changes to walker, orchestrator, resolver, MCP server, or schema.

## Why Protocol (not ABC)

Python `Protocol` is structural — an extractor doesn't need to inherit from a base class. This means:

- Third-party contributors can implement an extractor without importing from `dedalus`
- The orchestrator accepts any object that satisfies the shape, not one that inherits a specific class
- Tests can use simple mock objects without subclassing

## Registry is module-level, not singleton class

A module-level `_REGISTRY` dict is simpler than a singleton class and has the same semantics. Python module import is already a singleton — the registry is initialized once per process.
