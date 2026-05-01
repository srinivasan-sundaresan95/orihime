# Adding a Language to Orihime

This guide walks through adding support for a new programming language. The examples use `python` throughout; substitute your language name as needed.

---

## 1. How the Language Registry Works

`dedalus/language.py` contains three cooperating pieces:

**`LanguageExtractor` Protocol** — any class with these attributes satisfies it:

```python
class LanguageExtractor(Protocol):
    language: str               # canonical name, e.g. "python"
    file_extensions: frozenset[str]  # e.g. frozenset({".py"})

    def extract(
        self,
        tree,            # tree_sitter.Tree returned by Parser.parse()
        source_bytes: bytes,
        file_id: str,    # stable identifier for the file node in KuzuDB
        repo_id: str,    # identifier for the repo node in KuzuDB
    ) -> ExtractResult: ...
```

**`register(extractor)`** — stores the extractor instance in `_registry` keyed by `extractor.language`. Calling it again with the same language name overwrites the previous entry.

**`registered_extensions()`** — flattens every extractor's `file_extensions` into a single `dict[str, str]` mapping extension → language name. The file walker uses this dict to decide which extractor to invoke for a given source file.

---

## 2. Step-by-Step Guide

### Step 1 — Install the tree-sitter grammar

```bash
pip install tree-sitter-python
```

For other languages the pattern is always `tree-sitter-<lang>`. Check [PyPI](https://pypi.org/) for availability before proceeding. If no package exists, see section 4.

After installing, add the grammar to `_LANGUAGE_FACTORIES` in `dedalus/language.py`:

```python
import tree_sitter_python

_LANGUAGE_FACTORIES: dict[str, object] = {
    "java": tree_sitter_java.language,
    "kotlin": tree_sitter_kotlin.language,
    "python": tree_sitter_python.language,   # add this line
}
```

---

### Step 2 — Verify parsing works

Run this in a Python shell or a scratch script before writing any extractor code:

```python
from orihime.language import get_parser

parser = get_parser("python")
source = b"def hello(): pass"
tree = parser.parse(source)

assert not tree.root_node.has_error, "Parse produced ERROR nodes — check grammar version"
print(tree.root_node.sexp())  # inspect the node types you will query
```

Spend time reading the `sexp()` output. The node type names (e.g. `function_definition`, `class_definition`, `decorated_definition`) are what you will match in `extract()`.

---

### Step 3 — Create `dedalus/python_extractor.py`

The file must define a class that satisfies the `LanguageExtractor` Protocol and call `register()` at module level.

```python
from __future__ import annotations

from orihime.language import ExtractResult, LanguageExtractor, register


class PythonExtractor:
    language: str = "python"
    file_extensions: frozenset[str] = frozenset({".py"})

    def extract(
        self,
        tree,
        source_bytes: bytes,
        file_id: str,
        repo_id: str,
    ) -> ExtractResult:
        classes: list[dict] = []
        methods: list[dict] = []
        endpoints: list[dict] = []
        rest_calls: list[dict] = []

        # Walk tree.root_node, populate the four lists.
        # See section 3 for the required dict keys.

        return ExtractResult(
            classes=classes,
            methods=methods,
            endpoints=endpoints,
            rest_calls=rest_calls,
        )


register(PythonExtractor())
```

The `extract()` method receives the fully-parsed `tree_sitter.Tree`. Walk it by iterating `node.children` recursively or by using tree-sitter's cursor API (`tree.walk()`). Match on `node.type` to identify constructs.

---

### Step 4 — What `extract()` must return

`ExtractResult` is a dataclass with four `list[dict]` fields. Each dict becomes a node row in KuzuDB. The required keys for each list are defined by the schema in `dedalus/schema.py`.

See the full field reference in section 3 below.

**Key rules:**

- `id` must be globally unique. Use a deterministic formula: `f"{repo_id}:{file_id}:{fqn}"` for classes and methods, `f"{repo_id}:{file_id}:{http_method}:{path}"` for endpoints.
- `fqn` (fully qualified name) is the dotted path callers and the graph query layer use to resolve cross-file references. For Python: `module.ClassName.method_name`.
- `line_start` is 1-based. Tree-sitter node positions are 0-based — add 1.
- `annotations` maps to decorator names (Python) or annotation names (Java/Kotlin). Pass an empty list `[]` if none.
- `is_interface` / `is_suspend` are booleans; always supply them, default to `False`.
- For `endpoints`, populate `path_regex` by converting path template variables to regex groups, e.g. `/users/{id}` → `^/users/([^/]+)$`. The cross-repo resolver uses this field.
- For `rest_calls`, `url_pattern` is the literal URL or template string found in the source. Do not attempt to resolve it.

---

### Step 5 — Register and wire the import

At the bottom of `dedalus/python_extractor.py`, the `register(PythonExtractor())` call (already shown in the skeleton above) is enough to add the extractor to the registry at import time.

Then add the import to `dedalus/__init__.py`:

```python
import orihime.python_extractor  # registers PythonExtractor on import
```

The file walker imports `dedalus` before scanning files, so this guarantees the extractor is available before any `.py` file is processed.

---

### Step 6 — Write tests

**Fixture file** — create a minimal but representative source file:

```
tests/fixtures/sample.py
```

```python
# tests/fixtures/sample.py
import httpx

class UserService:
    def get_user(self, user_id: int) -> dict:
        return httpx.get(f"https://api.example.com/users/{user_id}").json()

    def create_user(self, data: dict) -> dict:
        return httpx.post("https://api.example.com/users", json=data).json()
```

**Test file** — create `tests/unit/test_python_extractor.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

import orihime.python_extractor  # ensure registration side-effect runs
from orihime.language import get_extractor, get_parser

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.py"


@pytest.fixture()
def result():
    source = FIXTURE.read_bytes()
    parser = get_parser("python")
    tree = parser.parse(source)
    extractor = get_extractor("python")
    return extractor.extract(tree, source, file_id="f1", repo_id="r1")


def test_extractor_is_registered():
    assert get_extractor("python") is not None


def test_class_extracted(result):
    names = [c["name"] for c in result.classes]
    assert "UserService" in names


def test_method_extracted(result):
    names = [m["name"] for m in result.methods]
    assert "get_user" in names
    assert "create_user" in names


def test_rest_calls_extracted(result):
    patterns = [r["url_pattern"] for r in result.rest_calls]
    assert any("users" in p for p in patterns)


def test_ids_are_unique(result):
    all_ids = (
        [c["id"] for c in result.classes]
        + [m["id"] for m in result.methods]
        + [e["id"] for e in result.endpoints]
        + [r["id"] for r in result.rest_calls]
    )
    assert len(all_ids) == len(set(all_ids)), "Duplicate ids detected"


def test_line_starts_are_one_based(result):
    for m in result.methods:
        assert m["line_start"] >= 1
```

Minimum assertions required to merge:
- At least one class is detected by name.
- At least one method is detected by name.
- All `id` values across all four lists are unique.
- `line_start` values are >= 1.
- `get_extractor("<lang>")` returns a non-None object after the extractor module is imported.

---

## 3. ExtractResult Field Reference

### `classes` — each dict maps to a `Class` node

| Key | Type | Description |
|-----|------|-------------|
| `id` | `str` | Unique node ID. Recommended: `f"{repo_id}:{file_id}:{fqn}"` |
| `name` | `str` | Simple class name, e.g. `UserService` |
| `fqn` | `str` | Fully qualified name, e.g. `com.example.UserService` or `module.UserService` |
| `file_id` | `str` | Pass through the `file_id` argument received by `extract()` |
| `repo_id` | `str` | Pass through the `repo_id` argument received by `extract()` |
| `is_interface` | `bool` | `True` for interfaces/protocols/abstract base classes |
| `annotations` | `list[str]` | Decorator or annotation names; empty list if none |

### `methods` — each dict maps to a `Method` node

| Key | Type | Description |
|-----|------|-------------|
| `id` | `str` | Unique node ID. Recommended: `f"{repo_id}:{file_id}:{fqn}"` |
| `name` | `str` | Simple method name |
| `fqn` | `str` | Fully qualified name including class, e.g. `module.UserService.get_user` |
| `class_id` | `str` | `id` of the parent `Class` dict; use `""` for top-level functions |
| `file_id` | `str` | Pass through from `extract()` arguments |
| `repo_id` | `str` | Pass through from `extract()` arguments |
| `line_start` | `int` | 1-based line number of the method declaration |
| `is_suspend` | `bool` | `True` for Kotlin `suspend` functions; always `False` for other languages |
| `annotations` | `list[str]` | Decorator or annotation names; empty list if none |

### `endpoints` — each dict maps to an `Endpoint` node

| Key | Type | Description |
|-----|------|-------------|
| `id` | `str` | Unique node ID. Recommended: `f"{repo_id}:{file_id}:{http_method}:{path}"` |
| `http_method` | `str` | Uppercase HTTP verb: `GET`, `POST`, `PUT`, `DELETE`, `PATCH` |
| `path` | `str` | Path template as written in source, e.g. `/users/{id}` |
| `path_regex` | `str` | Compiled regex for path matching, e.g. `^/users/([^/]+)$`; used by cross-repo resolver |
| `handler_method_id` | `str` | `id` of the `Method` dict that handles this endpoint |
| `repo_id` | `str` | Pass through from `extract()` arguments |

### `rest_calls` — each dict maps to a `RestCall` node

| Key | Type | Description |
|-----|------|-------------|
| `id` | `str` | Unique node ID. Recommended: `f"{repo_id}:{file_id}:{http_method}:{url_pattern}"` |
| `http_method` | `str` | Uppercase HTTP verb inferred from the call site, e.g. `GET`; use `UNKNOWN` if not determinable |
| `url_pattern` | `str` | Literal URL or template string found in source; do not resolve variables |
| `caller_method_id` | `str` | `id` of the enclosing `Method` dict; use `""` if call is at module scope |
| `repo_id` | `str` | Pass through from `extract()` arguments |

---

## 4. No tree-sitter Package Available

If `pip install tree-sitter-<lang>` returns no results, you have two options.

**Option A — Build the grammar from source**

Clone the grammar repository (always named `tree-sitter-<lang>` on GitHub) and use `tree_sitter.Language.build_library()` to compile a `.so`. Then load it with `Language(path_to_so, "<lang>")` instead of `Language(factory())`. Register a custom factory in `_LANGUAGE_FACTORIES` that returns the pre-built `Language` object directly.

This approach is fully supported but requires a C compiler at build time and produces a build artefact that must be committed or generated in CI.

**Option B — Regex-based extractor (fallback)**

Skip `get_parser()` entirely. Implement `extract()` to operate on `source_bytes.decode()` with `re` patterns. The method signature does not change; the `tree` argument is simply ignored.

```python
def extract(self, tree, source_bytes: bytes, file_id: str, repo_id: str) -> ExtractResult:
    source = source_bytes.decode(errors="replace")
    # use re.finditer() on source
    ...
```

Caveats of the regex approach:
- Will produce false positives for constructs inside comments and multi-line strings.
- Cannot reliably determine nesting (which class a method belongs to) without a full parser.
- `fqn` values will be approximate; cross-repo resolution accuracy degrades.
- The extractor should set `class_id` to `""` for all methods when nesting cannot be determined.

Document this limitation in a comment at the top of the extractor file so future contributors know to upgrade to tree-sitter when a package becomes available.
