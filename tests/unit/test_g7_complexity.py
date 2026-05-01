"""Unit tests for G7 — Static Complexity Hints.

Tests 1–8: parse small inline Java/Kotlin source strings, run extractor,
           check complexity_hint field in method dict.
Test 9:    integration — index a fixture repo, call find_complexity_hints.
"""
from __future__ import annotations

import tempfile
import os

import pytest

import orihime.java_extractor  # noqa: F401 — trigger register()
import orihime.kotlin_extractor  # noqa: F401 — trigger register()

from orihime.java_extractor import JavaExtractor
from orihime.kotlin_extractor import KotlinExtractor
from orihime.language import get_parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_java(source: str) -> list[dict]:
    """Parse Java source string and return method dicts."""
    src = source.encode("utf-8")
    parser = get_parser("java")
    tree = parser.parse(src)
    extractor = JavaExtractor()
    result = extractor.extract(tree, src, "file1", "repo1")
    return result.methods


def _extract_kotlin(source: str) -> list[dict]:
    """Parse Kotlin source string and return method dicts."""
    src = source.encode("utf-8")
    parser = get_parser("kotlin")
    tree = parser.parse(src)
    extractor = KotlinExtractor()
    result = extractor.extract(tree, src, "Test.kt", "repo1")
    return result.methods


def _hint_for(methods: list[dict], method_name: str) -> str:
    """Return the complexity_hint for the named method, or raise."""
    for m in methods:
        if m["name"] == method_name:
            return m.get("complexity_hint", "")
    names = [m["name"] for m in methods]
    raise KeyError(f"Method {method_name!r} not found in {names}")


# ---------------------------------------------------------------------------
# Test 1: nested for loops → "O(n2)-candidate"
# ---------------------------------------------------------------------------

_JAVA_NESTED_LOOPS = """\
package com.example;
public class Foo {
    public void nested(java.util.List<String> items) {
        for (String a : items) {
            for (String b : items) {
                System.out.println(a + b);
            }
        }
    }
}
"""


def test_nested_loops_java():
    methods = _extract_java(_JAVA_NESTED_LOOPS)
    hint = _hint_for(methods, "nested")
    assert "O(n2)-candidate" in hint


_KOTLIN_NESTED_LOOPS = """\
package com.example
class Foo {
    fun nested(items: List<String>) {
        for (a in items) {
            for (b in items) {
                println(a + b)
            }
        }
    }
}
"""


def test_nested_loops_kotlin():
    methods = _extract_kotlin(_KOTLIN_NESTED_LOOPS)
    hint = _hint_for(methods, "nested")
    assert "O(n2)-candidate" in hint


# ---------------------------------------------------------------------------
# Test 2: list.contains() in loop → "O(n2)-list-scan"
# ---------------------------------------------------------------------------

_JAVA_LIST_SCAN = """\
package com.example;
import java.util.List;
public class Foo {
    public void scanCheck(List<String> haystack, List<String> needles) {
        for (String n : needles) {
            if (haystack.contains(n)) {
                System.out.println(n);
            }
        }
    }
}
"""


def test_list_scan_java():
    methods = _extract_java(_JAVA_LIST_SCAN)
    hint = _hint_for(methods, "scanCheck")
    assert "O(n2)-list-scan" in hint


# ---------------------------------------------------------------------------
# Test 3: recursive method → "recursive"
# ---------------------------------------------------------------------------

_JAVA_RECURSIVE = """\
package com.example;
public class Foo {
    public int factorial(int n) {
        if (n <= 1) return 1;
        return n * factorial(n - 1);
    }
}
"""


def test_recursive_java():
    methods = _extract_java(_JAVA_RECURSIVE)
    hint = _hint_for(methods, "factorial")
    assert "recursive" in hint


_KOTLIN_RECURSIVE = """\
package com.example
class Foo {
    fun factorial(n: Int): Int {
        if (n <= 1) return 1
        return n * factorial(n - 1)
    }
}
"""


def test_recursive_kotlin():
    methods = _extract_kotlin(_KOTLIN_RECURSIVE)
    hint = _hint_for(methods, "factorial")
    assert "recursive" in hint


# ---------------------------------------------------------------------------
# Test 4: findById() in loop → "n+1-risk"
# ---------------------------------------------------------------------------

_JAVA_N_PLUS_1 = """\
package com.example;
import java.util.List;
public class Foo {
    private UserRepository userRepo;
    public void loadAll(List<Long> ids) {
        for (Long id : ids) {
            User u = userRepo.findById(id);
            process(u);
        }
    }
    private void process(Object o) {}
}
"""


def test_n_plus_1_java():
    methods = _extract_java(_JAVA_N_PLUS_1)
    hint = _hint_for(methods, "loadAll")
    assert "n+1-risk" in hint


# ---------------------------------------------------------------------------
# Test 5: findAll() with no Pageable param → "unbounded-query"
# ---------------------------------------------------------------------------

_JAVA_UNBOUNDED = """\
package com.example;
import java.util.List;
public class Foo {
    private UserRepository userRepo;
    public List<User> getAll() {
        return userRepo.findAll();
    }
}
"""


def test_unbounded_query_no_pageable():
    methods = _extract_java(_JAVA_UNBOUNDED)
    hint = _hint_for(methods, "getAll")
    assert "unbounded-query" in hint


# ---------------------------------------------------------------------------
# Test 6: findAll() WITH Pageable param → no unbounded-query hint
# ---------------------------------------------------------------------------

_JAVA_PAGEABLE = """\
package com.example;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
public class Foo {
    private UserRepository userRepo;
    public Page<User> getPage(Pageable pageable) {
        return userRepo.findAll(pageable);
    }
}
"""


def test_unbounded_query_with_pageable():
    methods = _extract_java(_JAVA_PAGEABLE)
    hint = _hint_for(methods, "getPage")
    assert "unbounded-query" not in hint


# ---------------------------------------------------------------------------
# Test 7: clean method → ""
# ---------------------------------------------------------------------------

_JAVA_CLEAN = """\
package com.example;
public class Foo {
    public int add(int a, int b) {
        return a + b;
    }
}
"""


def test_clean_method_no_hints():
    methods = _extract_java(_JAVA_CLEAN)
    hint = _hint_for(methods, "add")
    assert hint == ""


# ---------------------------------------------------------------------------
# Test 8: multiple hints combined → comma-separated
# ---------------------------------------------------------------------------

_JAVA_MULTIPLE_HINTS = """\
package com.example;
import java.util.List;
public class Foo {
    private Repo repo;
    public void badMethod(List<Long> ids) {
        for (Long id : ids) {
            for (Long id2 : ids) {
                User u = repo.findById(id);
                if (ids.contains(id2)) {
                    process(u);
                }
            }
        }
    }
    private void process(Object o) {}
}
"""


def test_multiple_hints_combined():
    methods = _extract_java(_JAVA_MULTIPLE_HINTS)
    hint = _hint_for(methods, "badMethod")
    # Must have at least two distinct hints (comma-separated)
    tags = [t.strip() for t in hint.split(",") if t.strip()]
    assert len(tags) >= 2, f"Expected multiple hints, got: {hint!r}"
    assert "O(n2)-candidate" in hint
    assert "n+1-risk" in hint
    assert "O(n2)-list-scan" in hint


# ---------------------------------------------------------------------------
# Test 9 (integration): index fixture repo, find_complexity_hints returns list
# ---------------------------------------------------------------------------

def test_find_complexity_hints_integration():
    """Index a fixture file containing loops and check find_complexity_hints."""
    import kuzu
    from orihime.indexer import index_repo
    from orihime.mcp_server import find_complexity_hints as _fch, _reset_connection
    import orihime.mcp_server as mcp_mod

    # Build a tiny fixture with a nested loop
    java_src = """\
package com.example;
import java.util.List;
public class ComplexService {
    public void nestedLoop(List<String> items) {
        for (String a : items) {
            for (String b : items) {
                System.out.println(a + b);
            }
        }
    }
    public int sum(int a, int b) {
        return a + b;
    }
}
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write the Java source
        src_dir = os.path.join(tmpdir, "src")
        os.makedirs(src_dir)
        with open(os.path.join(src_dir, "ComplexService.java"), "w") as f:
            f.write(java_src)

        db_path = os.path.join(tmpdir, "test.db")

        # Point the MCP server at our temp DB
        old_db_path = mcp_mod._DB_PATH
        mcp_mod._DB_PATH = db_path
        _reset_connection()

        try:
            index_repo(src_dir, "test-repo", db_path)
            _reset_connection()  # force re-open with new DB

            results = _fch("test-repo", min_severity="low")
            assert isinstance(results, list)

            # nestedLoop should be flagged
            hints = {r["method_fqn"].split(".")[-1]: r["complexity_hint"] for r in results}
            assert "nestedLoop" in hints, f"nestedLoop not found in hints: {list(hints.keys())}"
            assert "O(n2)-candidate" in hints["nestedLoop"]

            # sum should NOT appear (clean method)
            assert "sum" not in hints, f"Clean method 'sum' should not be flagged"

        finally:
            mcp_mod._DB_PATH = old_db_path
            _reset_connection()
