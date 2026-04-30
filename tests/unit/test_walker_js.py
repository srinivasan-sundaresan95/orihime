"""Unit tests for walk_repo JS/TS extension support."""
from __future__ import annotations

import tempfile
from pathlib import Path

import dedalus.js_extractor  # noqa: F401 — triggers registration of JS/TS extensions

from dedalus.walker import walk_repo


def _create_temp_repo(files: dict[str, bytes]) -> Path:
    """Create a temporary directory tree with the given files."""
    tmp = Path(tempfile.mkdtemp())
    for rel_path, content in files.items():
        full = tmp / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(content)
    return tmp


# ---------------------------------------------------------------------------
# Test 1: .ts files yield ("typescript", ...)
# ---------------------------------------------------------------------------

def test_ts_files_yield_typescript():
    repo = _create_temp_repo({
        "src/service.ts": b"class MyService {}",
        "src/utils.ts": b"export function helper() {}",
    })
    items = list(walk_repo(repo))
    langs = {lang for _, lang in items}
    assert "typescript" in langs

    ts_paths = [p for p, lang in items if lang == "typescript"]
    names = {p.name for p in ts_paths}
    assert "service.ts" in names
    assert "utils.ts" in names


# ---------------------------------------------------------------------------
# Test 2: .js files yield ("javascript", ...)
# ---------------------------------------------------------------------------

def test_js_files_yield_javascript():
    repo = _create_temp_repo({
        "src/app.js": b"const x = 1;",
        "src/index.js": b"module.exports = {};",
    })
    items = list(walk_repo(repo))
    langs = {lang for _, lang in items}
    assert "javascript" in langs

    js_paths = [p for p, lang in items if lang == "javascript"]
    names = {p.name for p in js_paths}
    assert "app.js" in names
    assert "index.js" in names


# ---------------------------------------------------------------------------
# Test 3: .tsx files are picked up as "typescript"
# ---------------------------------------------------------------------------

def test_tsx_files_yield_typescript():
    repo = _create_temp_repo({
        "components/Button.tsx": b"export function Button() { return null; }",
        "pages/index.tsx": b"export default function Home() { return null; }",
    })
    items = list(walk_repo(repo))

    tsx_items = [(p, lang) for p, lang in items if p.suffix == ".tsx"]
    assert len(tsx_items) >= 2
    for _, lang in tsx_items:
        assert lang == "typescript"


# ---------------------------------------------------------------------------
# Test 4: .jsx files are picked up as "javascript"
# ---------------------------------------------------------------------------

def test_jsx_files_yield_javascript():
    repo = _create_temp_repo({
        "components/Card.jsx": b"export function Card() { return null; }",
    })
    items = list(walk_repo(repo))
    jsx_items = [(p, lang) for p, lang in items if p.suffix == ".jsx"]
    assert len(jsx_items) >= 1
    for _, lang in jsx_items:
        assert lang == "javascript"


# ---------------------------------------------------------------------------
# Test 5: node_modules are excluded
# ---------------------------------------------------------------------------

def test_node_modules_excluded():
    repo = _create_temp_repo({
        "src/app.ts": b"class App {}",
        "node_modules/lodash/index.js": b"module.exports = {};",
    })
    items = list(walk_repo(repo))
    paths = [p for p, _ in items]
    assert all("node_modules" not in str(p) for p in paths)


# ---------------------------------------------------------------------------
# Test 6: Mixed Java + TS repo yields both language types
# ---------------------------------------------------------------------------

def test_mixed_repo_yields_both_java_and_typescript():
    import dedalus.java_extractor  # noqa: F401

    repo = _create_temp_repo({
        "src/main/java/Service.java": b"public class Service {}",
        "frontend/src/api.ts": b"export async function GET(req) {}",
    })
    items = list(walk_repo(repo))
    langs = {lang for _, lang in items}
    assert "java" in langs
    assert "typescript" in langs
