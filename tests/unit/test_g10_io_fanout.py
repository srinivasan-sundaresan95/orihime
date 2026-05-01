"""Unit tests for G10 — I/O Fan-out Detection.

Tests 1–9: parse small inline Java/Kotlin source strings, run extractor,
           check io_fanout fields in method dict.
Test 10:   integration — index a fixture repo, call find_io_fanout.
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
from orihime.io_fanout_pass import detect_io_fanout


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_java(source: str) -> list[dict]:
    src = source.encode("utf-8")
    parser = get_parser("java")
    tree = parser.parse(src)
    extractor = JavaExtractor()
    result = extractor.extract(tree, src, "file1", "repo1")
    return result.methods


def _extract_kotlin(source: str) -> list[dict]:
    src = source.encode("utf-8")
    parser = get_parser("kotlin")
    tree = parser.parse(src)
    extractor = KotlinExtractor()
    result = extractor.extract(tree, src, "Test.kt", "repo1")
    return result.methods


def _io_for(methods: list[dict], method_name: str) -> dict:
    for m in methods:
        if m["name"] == method_name:
            return {
                "total": m.get("io_fanout", 0),
                "parallel_count": m.get("io_parallel_count", 0),
                "serial_count": m.get("io_serial_count", 0),
                "parallel_wrapper": m.get("io_parallel_wrapper", ""),
            }
    names = [m["name"] for m in methods]
    raise KeyError(f"Method {method_name!r} not found in {names}")


# ---------------------------------------------------------------------------
# Test 1: None body → zeros
# ---------------------------------------------------------------------------

def test_none_body_returns_zeros():
    result = detect_io_fanout(None, b"", "java", [])
    assert result == {"total": 0, "parallel_count": 0, "serial_count": 0, "parallel_wrapper": ""}


# ---------------------------------------------------------------------------
# Test 2: Java DB call (findById) → total=1, serial=1, parallel=0
# ---------------------------------------------------------------------------

_JAVA_DB_CALL = """\
package com.example;
public class Foo {
    private UserRepo repo;
    public void loadUser(Long id) {
        User u = repo.findById(id);
        process(u);
    }
    private void process(Object o) {}
}
"""


def test_java_db_call_serial():
    methods = _extract_java(_JAVA_DB_CALL)
    io = _io_for(methods, "loadUser")
    assert io["total"] == 1
    assert io["serial_count"] == 1
    assert io["parallel_count"] == 0
    assert io["parallel_wrapper"] == ""


# ---------------------------------------------------------------------------
# Test 3: Java HTTP call (getForObject) → total=1, serial=1
# ---------------------------------------------------------------------------

_JAVA_HTTP_CALL = """\
package com.example;
public class Foo {
    private org.springframework.web.client.RestTemplate restTemplate;
    public String fetchData(String url) {
        return restTemplate.getForObject(url, String.class);
    }
}
"""


def test_java_http_call_serial():
    methods = _extract_java(_JAVA_HTTP_CALL)
    io = _io_for(methods, "fetchData")
    assert io["total"] == 1
    assert io["serial_count"] == 1
    assert io["parallel_count"] == 0


# ---------------------------------------------------------------------------
# Test 4: Java findBy* prefix detection
# ---------------------------------------------------------------------------

_JAVA_FINDBY = """\
package com.example;
import java.util.List;
public class Foo {
    private UserRepo repo;
    public List<User> search(String name) {
        return repo.findByName(name);
    }
}
"""


def test_java_findby_prefix():
    methods = _extract_java(_JAVA_FINDBY)
    io = _io_for(methods, "search")
    assert io["total"] == 1
    assert io["serial_count"] == 1


# ---------------------------------------------------------------------------
# Test 5: Java CompletableFuture supplyAsync → parallel
# ---------------------------------------------------------------------------

_JAVA_COMPLETABLE = """\
package com.example;
import java.util.concurrent.CompletableFuture;
public class Foo {
    private UserRepo repo;
    public CompletableFuture<User> loadAsync(Long id) {
        return CompletableFuture.supplyAsync(() -> repo.findById(id));
    }
}
"""


def test_java_completable_future_parallel():
    methods = _extract_java(_JAVA_COMPLETABLE)
    io = _io_for(methods, "loadAsync")
    assert io["total"] == 1
    assert io["parallel_count"] == 1
    assert io["serial_count"] == 0
    assert io["parallel_wrapper"] == "completable_future"


# ---------------------------------------------------------------------------
# Test 6: Kotlin async/coroutineScope → coroutine wrapper
# ---------------------------------------------------------------------------

_KOTLIN_COROUTINE = """\
package com.example
import kotlinx.coroutines.*
class Foo {
    private val repo: UserRepo = TODO()
    suspend fun loadParallel(id: Long): User {
        return coroutineScope {
            val deferred = async { repo.findById(id) }
            deferred.await()
        }
    }
}
"""


def test_kotlin_coroutine_parallel():
    methods = _extract_kotlin(_KOTLIN_COROUTINE)
    io = _io_for(methods, "loadParallel")
    assert io["total"] >= 1
    assert io["parallel_count"] >= 1
    assert io["parallel_wrapper"] == "coroutine"


# ---------------------------------------------------------------------------
# Test 7: Mixed serial + parallel in same Java method
# ---------------------------------------------------------------------------

_JAVA_MIXED = """\
package com.example;
import java.util.concurrent.CompletableFuture;
public class Foo {
    private UserRepo repo;
    private OrderRepo orderRepo;
    public void mixed(Long id) {
        // serial DB call
        User u = repo.findById(id);
        // parallel DB call
        CompletableFuture.supplyAsync(() -> orderRepo.findAll());
    }
}
"""


def test_java_mixed_serial_and_parallel():
    methods = _extract_java(_JAVA_MIXED)
    io = _io_for(methods, "mixed")
    assert io["total"] == 2
    assert io["serial_count"] == 1
    assert io["parallel_count"] == 1
    assert io["parallel_wrapper"] == "completable_future"


# ---------------------------------------------------------------------------
# Test 8: @Async annotation → spring_async wrapper, method body is all parallel
# ---------------------------------------------------------------------------

_JAVA_SPRING_ASYNC = """\
package com.example;
import org.springframework.scheduling.annotation.Async;
public class Foo {
    private UserRepo repo;
    @Async
    public void asyncMethod(Long id) {
        repo.findById(id);
        repo.save(null);
    }
}
"""


def test_java_spring_async_wrapper():
    methods = _extract_java(_JAVA_SPRING_ASYNC)
    io = _io_for(methods, "asyncMethod")
    assert io["total"] == 2
    assert io["parallel_wrapper"] == "spring_async"
    assert io["parallel_count"] == 2
    assert io["serial_count"] == 0


# ---------------------------------------------------------------------------
# Test 9: clean method with no I/O → total=0
# ---------------------------------------------------------------------------

_JAVA_NO_IO = """\
package com.example;
public class Foo {
    public int add(int a, int b) {
        return a + b;
    }
}
"""


def test_java_no_io():
    methods = _extract_java(_JAVA_NO_IO)
    io = _io_for(methods, "add")
    assert io["total"] == 0
    assert io["serial_count"] == 0
    assert io["parallel_count"] == 0
    assert io["parallel_wrapper"] == ""


# ---------------------------------------------------------------------------
# Test 10a: Kotlin serial-only — two DB calls, no parallel wrapper
# ---------------------------------------------------------------------------

_KOTLIN_SERIAL_ONLY = """\
package com.example
class Foo {
    private val repo: UserRepo = TODO()
    fun fetchTwo(id: Long): String {
        val a = repo.findById(id)
        val b = repo.findAll()
        return a.toString() + b.toString()
    }
}
"""


def test_kotlin_serial_only():
    methods = _extract_kotlin(_KOTLIN_SERIAL_ONLY)
    io = _io_for(methods, "fetchTwo")
    assert io["total"] == 2
    assert io["serial_count"] == 2
    assert io["parallel_count"] == 0
    assert io["parallel_wrapper"] == ""


# ---------------------------------------------------------------------------
# Test 10b: Reactor Mono.zip → reactor wrapper, all calls parallel
# ---------------------------------------------------------------------------

_JAVA_REACTOR_MONO_ZIP = """\
package com.example;
import reactor.core.publisher.Mono;
public class Foo {
    private ServiceA serviceA;
    private ServiceB serviceB;
    public Mono<String> fetchCombined() {
        return Mono.zip(serviceA.retrieve(), serviceB.retrieve())
                   .map(tuple -> tuple.getT1() + tuple.getT2());
    }
}
"""


def test_reactor_mono_zip_parallel():
    methods = _extract_java(_JAVA_REACTOR_MONO_ZIP)
    io = _io_for(methods, "fetchCombined")
    assert io["total"] == 2
    assert io["parallel_count"] == 2
    assert io["parallel_wrapper"] == "reactor"


# ---------------------------------------------------------------------------
# Test 11 (integration): index fixture, call find_io_fanout
# ---------------------------------------------------------------------------

def test_find_io_fanout_integration():
    """Index a fixture file with I/O calls and check find_io_fanout."""
    import kuzu
    from orihime.indexer import index_repo
    from orihime.mcp_server import find_io_fanout as _fif, _reset_connection
    import orihime.mcp_server as mcp_mod

    java_src = """\
package com.example;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
public class UserController {
    private UserRepo repo;
    @GetMapping("/users")
    public java.util.List<User> getUsers() {
        java.util.List<User> all = repo.findAll();
        User one = repo.findById(1L);
        return all;
    }
    public int helper(int a, int b) {
        return a + b;
    }
}
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_dir = os.path.join(tmpdir, "src")
        os.makedirs(src_dir)
        with open(os.path.join(src_dir, "UserController.java"), "w") as f:
            f.write(java_src)

        db_path = os.path.join(tmpdir, "test.db")
        old_db_path = mcp_mod._DB_PATH
        mcp_mod._DB_PATH = db_path
        _reset_connection()

        try:
            index_repo(src_dir, "test-repo", db_path)
            _reset_connection()

            results = _fif("test-repo", min_total=2)
            assert isinstance(results, list), f"Expected list, got: {type(results)}"

            # getUsers has 2 I/O calls (findAll + findById)
            fqns = [r["handler_fqn"] for r in results]
            assert any("getUsers" in fqn for fqn in fqns), \
                f"getUsers not found in results: {fqns}"

            # helper has 0 I/O calls → should NOT appear with min_total=2
            assert not any("helper" in r["handler_fqn"] for r in results), \
                "helper should not appear (0 I/O calls)"

            # Check total_io for getUsers
            for r in results:
                if "getUsers" in r["handler_fqn"]:
                    assert r["total_io"] == 2, f"Expected 2 I/O calls, got {r['total_io']}"
                    assert r["latency_floor_ms"] is None  # no perf data ingested

        finally:
            mcp_mod._DB_PATH = old_db_path
            _reset_connection()
