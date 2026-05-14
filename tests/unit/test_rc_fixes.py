"""Tests for RC-A, RC-B, RC-D, RC-H, RC-I1, RC-K bug fixes."""
from __future__ import annotations

import os
import pathlib
import tempfile
import textwrap
import uuid

import pytest

import orihime.java_extractor  # noqa: F401 — triggers register()
import orihime.kotlin_extractor  # noqa: F401 — triggers register()
from orihime.language import ExtractResult, get_parser, register, registered_extensions
import orihime.language as lang_module
from orihime.resolver import resolve_calls
from orihime.walker import walk_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_java(source: bytes):
    parser = get_parser("java")
    return parser.parse(source), source


def _parse_kotlin(source: bytes):
    parser = get_parser("kotlin")
    return parser.parse(source), source


def _method(name: str, fqn: str, file_id: str = "f1", line_start: int = 1) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "fqn": fqn,
        "class_id": str(uuid.uuid4()),
        "file_id": file_id,
        "repo_id": "repo1",
        "line_start": line_start,
        "is_suspend": False,
        "annotations": [],
    }


# ---------------------------------------------------------------------------
# RC-B: walker skips src/test/ directories
# ---------------------------------------------------------------------------

class TestRcBWalkerSkipsTestDirs:
    """RC-B: files under src/test/java or src/test/kotlin must be skipped."""

    @pytest.fixture()
    def registered_langs(self):
        """Register mock Java/Kotlin extractors, restore afterwards."""
        class _Mock:
            def __init__(self, language, extensions):
                self.language = language
                self.file_extensions = extensions
            def extract(self, tree, source_bytes, file_id, repo_id):
                return ExtractResult()

        original = dict(lang_module._registry)
        register(_Mock("java", frozenset({".java"})))
        register(_Mock("kotlin", frozenset({".kt"})))
        yield
        lang_module._registry.clear()
        lang_module._registry.update(original)

    def test_src_test_java_skipped(self, tmp_path, registered_langs):
        """A .java file under src/test/java/ must not be yielded."""
        main_dir = tmp_path / "src" / "main" / "java"
        main_dir.mkdir(parents=True)
        (main_dir / "Foo.java").touch()

        test_dir = tmp_path / "src" / "test" / "java"
        test_dir.mkdir(parents=True)
        (test_dir / "FooTest.java").touch()

        paths = [p for p, _ in walk_repo(tmp_path)]
        main_java = main_dir / "Foo.java"
        test_java = test_dir / "FooTest.java"
        assert main_java in paths, "main/java/Foo.java must be yielded"
        assert test_java not in paths, "test/java/FooTest.java must be skipped"

    def test_src_test_kotlin_skipped(self, tmp_path, registered_langs):
        """A .kt file under src/test/kotlin/ must not be yielded."""
        main_dir = tmp_path / "src" / "main" / "kotlin"
        main_dir.mkdir(parents=True)
        (main_dir / "Service.kt").touch()

        test_dir = tmp_path / "src" / "test" / "kotlin"
        test_dir.mkdir(parents=True)
        (test_dir / "ServiceTest.kt").touch()

        paths = [p for p, _ in walk_repo(tmp_path)]
        assert not any("ServiceTest.kt" in str(p) for p in paths)

    def test_non_test_subdir_not_skipped(self, tmp_path, registered_langs):
        """A file under src/integrationTest/ is NOT under src/test/ and must be yielded."""
        it_dir = tmp_path / "src" / "integrationTest" / "java"
        it_dir.mkdir(parents=True)
        (it_dir / "IntegrationSpec.java").touch()

        paths = [p for p, _ in walk_repo(tmp_path)]
        assert any("IntegrationSpec.java" in str(p) for p in paths)


# ---------------------------------------------------------------------------
# RC-A: import-based disambiguation for ambiguous simple class names
# ---------------------------------------------------------------------------

class TestRcAImportDisambiguation:
    """RC-A: when multiple classes share a simple name, filter by import."""

    def test_import_disambiguates_suffix_match(self):
        """Two classes named 'Config' in different packages; import selects correct one."""
        src = textwrap.dedent("""\
            package com.example.service;
            import com.example.config.Config;
            class Service {
                void doWork() {
                    Config c = new Config();
                    c.getValue();
                }
            }
        """).encode()
        tree, source_bytes = _parse_java(src)

        # Two candidates for Config.getValue
        id_correct = str(uuid.uuid4())
        id_wrong   = str(uuid.uuid4())
        caller_m   = _method("doWork", "com.example.service.Service.doWork", line_start=4)

        methods = [caller_m]
        fqn_index = {
            "com.example.service.Service.doWork": caller_m["id"],
            "com.example.config.Config.getValue": id_correct,
            "com.example.other.Config.getValue": id_wrong,
        }

        # Without import map — both candidates are in suffix index; result is ambiguous
        edges_no_import = resolve_calls(
            tree, source_bytes, methods, fqn_index, "f1", "repo1",
        )
        calls_no_import = [e for e in edges_no_import if e.edge_type == "CALLS"]

        # With import map — should filter to the imported package
        file_import_maps = {
            "f1": {"Config": "com.example.config.Config"},
        }
        edges_with_import = resolve_calls(
            tree, source_bytes, methods, fqn_index, "f1", "repo1",
            file_import_maps=file_import_maps,
        )
        calls_with_import = [e for e in edges_with_import if e.edge_type == "CALLS"]

        # With import map, only the imported Config should be resolved
        callee_ids = {e.callee_id for e in calls_with_import}
        assert id_correct in callee_ids, "Imported Config.getValue must be resolved"
        assert id_wrong not in callee_ids, "Non-imported Config.getValue must be filtered out"


# ---------------------------------------------------------------------------
# RC-D: field-type dispatch — navigated call uses DI field type
# ---------------------------------------------------------------------------

class TestRcDFieldTypeDispatch:
    """RC-D: receiver.method() where receiver is a class field uses field's declared type."""

    def test_field_receiver_filters_to_declared_type(self):
        """walletApiClient.buy() should resolve to WalletApiClient.buy, not ServiceImpl.buy."""
        src = textwrap.dedent("""\
            package com.example;
            class TradingService {
                private WalletApiClient walletApiClient;
                void executeTrade() {
                    walletApiClient.buy();
                }
            }
        """).encode()
        tree, source_bytes = _parse_java(src)

        caller_m = _method("executeTrade", "com.example.TradingService.executeTrade", line_start=4)
        id_correct = str(uuid.uuid4())
        id_wrong   = str(uuid.uuid4())

        methods = [caller_m]
        fqn_index = {
            "com.example.TradingService.executeTrade": caller_m["id"],
            "com.example.WalletApiClient.buy": id_correct,
            "com.example.TradingService.buy": id_wrong,
        }

        class_field_types = {
            "com.example.TradingService": {"walletApiClient": "WalletApiClient"},
        }

        edges = resolve_calls(
            tree, source_bytes, methods, fqn_index, "f1", "repo1",
            class_field_types=class_field_types,
        )
        calls = [e for e in edges if e.edge_type == "CALLS"]
        callee_ids = {e.callee_id for e in calls}

        assert id_correct in callee_ids, "WalletApiClient.buy must be resolved"
        assert id_wrong not in callee_ids, "TradingService.buy must NOT be picked (wrong receiver type)"


# ---------------------------------------------------------------------------
# RC-H: deduplication of Method nodes by FQN
# ---------------------------------------------------------------------------

class TestRcHMethodDeduplication:
    """RC-H: duplicate Method dicts (same FQN) are collapsed to one node in indexer."""

    def test_dedup_keeps_first_occurrence(self):
        """index_repo must not crash or write duplicate nodes for the same method FQN."""
        from orihime.indexer import index_repo

        src = textwrap.dedent("""\
            package com.example;
            import java.util.List;
            class GenericRepo<T> {
                public List<T> findAll() { return null; }
            }
        """)
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = pathlib.Path(tmpdir) / "repo"
            (repo_dir / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)
            java_file = repo_dir / "src" / "main" / "java" / "com" / "example" / "GenericRepo.java"
            java_file.write_text(src)

            db_path = str(pathlib.Path(tmpdir) / "test.db")
            import kuzu
            index_repo(str(repo_dir), "dedup-repo", db_path)

            db = kuzu.Database(db_path)
            conn = kuzu.Connection(db)
            result = conn.execute(
                "MATCH (m:Method) WHERE m.fqn = 'com.example.GenericRepo.findAll' RETURN count(m)"
            )
            count = result.get_next()[0]
            assert count == 1, f"Expected exactly 1 Method node for findAll, got {count}"


# ---------------------------------------------------------------------------
# RC-I1: @annotation(FQN) pointcut resolves to simple class name
# ---------------------------------------------------------------------------

class TestRcI1AopFqnPointcut:
    """RC-I1: @Around(\"@annotation(org.springframework.scheduling.annotation.Scheduled)\")
    must resolve to annotation name 'Scheduled', not fail."""

    def test_fqn_annotation_pointcut_resolves_to_simple_name(self, tmp_path):
        """_extract_annotation_name_from_source handles FQN in @annotation(...)."""
        from orihime.framework_pass import _extract_annotation_name_from_source

        aspect_src = textwrap.dedent("""\
            @Aspect
            @Component
            public class LogAdviceScheduler {
                @Around("@annotation(org.springframework.scheduling.annotation.Scheduled)")
                public Object logAroundScheduledTask(ProceedingJoinPoint joinPoint) throws Throwable {
                    return joinPoint.proceed();
                }
            }
        """)
        src_file = tmp_path / "LogAdviceScheduler.java"
        src_file.write_text(aspect_src)

        result = _extract_annotation_name_from_source(str(src_file), line_start=4, language="java")
        assert result == "Scheduled", \
            f"Expected 'Scheduled' from FQN pointcut, got {result!r}"

    def test_simple_name_pointcut_still_works(self, tmp_path):
        """Existing behavior (param-variable pattern) must not regress."""
        from orihime.framework_pass import _extract_annotation_name_from_source

        aspect_src = textwrap.dedent("""\
            @Aspect
            @Component
            class CardStateControlAspect {
                @Around(value = "@annotation(cardStateControlled) && args(..)")
                fun processCardStateControl(
                    joinPoint: ProceedingJoinPoint,
                    cardStateControlled: CardStateControlled,
                ): Any {
                    return joinPoint.proceed()
                }
            }
        """)
        src_file = tmp_path / "CardStateControlAspect.kt"
        src_file.write_text(aspect_src)

        result = _extract_annotation_name_from_source(str(src_file), line_start=4, language="kotlin")
        assert result == "CardStateControlled", \
            f"Simple-name pointcut must still resolve, got {result!r}"


# ---------------------------------------------------------------------------
# RC-K: extends_map enables superclass method resolution
# ---------------------------------------------------------------------------

class TestRcKExtendsMapResolution:
    """RC-K: when a caller's variable is typed as a subclass, calls to inherited
    superclass methods should resolve via extends_map."""

    def test_superclass_method_resolved_via_extends_map(self):
        """Service.doWork() calls helper.sharedMethod() where Helper extends BaseHelper.
        sharedMethod is declared on BaseHelper but called via a Helper-typed variable.
        With extends_map, Orihime must resolve it; without it, it may not.
        """
        src = textwrap.dedent("""\
            package com.example;
            class Service {
                private Helper helper;
                void doWork() {
                    helper.sharedMethod();
                }
            }
        """).encode()
        tree, source_bytes = _parse_java(src)

        caller_m  = _method("doWork", "com.example.Service.doWork", line_start=4)
        target_id = str(uuid.uuid4())
        methods   = [caller_m]
        fqn_index = {
            "com.example.Service.doWork": caller_m["id"],
            "com.example.BaseHelper.sharedMethod": target_id,
        }
        class_field_types = {
            "com.example.Service": {"helper": "Helper"},
        }
        class_by_simple_name: dict = {
            "Helper": ["com.example.Helper"],
        }
        # extends_map: Helper extends BaseHelper
        extends_map = {
            "com.example.Helper": ["com.example.BaseHelper"],
        }

        edges = resolve_calls(
            tree, source_bytes, methods, fqn_index, "f1", "repo1",
            class_field_types=class_field_types,
            extends_map=extends_map,
        )
        calls = [e for e in edges if e.edge_type == "CALLS"]
        callee_ids = {e.callee_id for e in calls}

        assert target_id in callee_ids, \
            "BaseHelper.sharedMethod must be resolved via extends_map walk"


# ---------------------------------------------------------------------------
# RC-L2: explicitly-written methods on Lombok-annotated classes must NOT be
#         flagged as generated=True
# ---------------------------------------------------------------------------

class TestRcL2LombokGeneratedFalseForExplicit:
    """RC-L2: _is_lombok_generated must return False when the method has a body."""

    def test_explicit_getter_not_generated(self):
        """A getX() method with an explicit body on a @Data class must be generated=False."""
        src = textwrap.dedent("""\
            package com.example;
            import lombok.Data;
            @Data
            class TxQueryCriteria {
                private String pattern;
                public String getPattern() {
                    return pattern == null ? "" : pattern.trim();
                }
            }
        """).encode()
        parser = get_parser("java")
        tree = parser.parse(src)
        from orihime.java_extractor import JavaExtractor
        from orihime.language import ExtractResult
        extractor = JavaExtractor()
        result = extractor.extract(tree, src, "f1", "repo1")
        methods = {m["name"]: m for m in result.methods}
        assert "getPattern" in methods, "getPattern must be indexed"
        assert not methods["getPattern"]["generated"], \
            "explicit getPattern on @Data class must NOT be generated=True"

    def test_pure_lombok_getter_is_generated(self):
        """A @Data class with no explicit getX() — the graph records no generated method
        (Lombok-generated bodies have no source node, so _process_methods never sees them).
        This test verifies _is_lombok_generated returns False when has_body=True and
        True when has_body=False (interface abstract method pattern)."""
        from orihime.java_extractor import _is_lombok_generated
        assert not _is_lombok_generated("getEasyId", ["Data"], has_body=True), \
            "method with body must not be generated even on @Data class"
        assert _is_lombok_generated("getEasyId", ["Data"], has_body=False), \
            "abstract getter on @Data class (no body) must be generated=True"


# ---------------------------------------------------------------------------
# Enum indexing: Java enum classes and their explicit methods must be indexed
# ---------------------------------------------------------------------------

class TestEnumIndexing:
    """Java enum declarations must produce Class and Method nodes."""

    def test_enum_class_and_methods_indexed(self):
        """An enum with explicit methods must produce a Class node and Method nodes."""
        src = textwrap.dedent("""\
            package com.example;
            public enum OrderStatus {
                PENDING, ACTIVE, CANCELLED;

                public boolean isFinal() {
                    return this == CANCELLED;
                }

                public String label() {
                    return name().toLowerCase();
                }
            }
        """).encode()
        parser = get_parser("java")
        tree = parser.parse(src)
        from orihime.java_extractor import JavaExtractor
        extractor = JavaExtractor()
        result = extractor.extract(tree, src, "f1", "repo1")

        class_fqns = {c["fqn"] for c in result.classes}
        assert "com.example.OrderStatus" in class_fqns, "enum class must be indexed"

        method_names = {m["name"] for m in result.methods}
        assert "isFinal" in method_names, "enum method isFinal must be indexed"
        assert "label" in method_names, "enum method label must be indexed"

    def test_enum_implements_recorded(self):
        """An enum that implements an interface must produce an IMPLEMENTS inheritance edge."""
        src = textwrap.dedent("""\
            package com.example;
            public enum Status implements Validatable {
                ACTIVE, INACTIVE;
                public boolean isValid() { return this == ACTIVE; }
            }
        """).encode()
        parser = get_parser("java")
        tree = parser.parse(src)
        from orihime.java_extractor import JavaExtractor
        extractor = JavaExtractor()
        result = extractor.extract(tree, src, "f1", "repo1")

        impls = [e for e in result.inheritance_edges if e["edge_type"] == "IMPLEMENTS"]
        parent_fqns = {e["parent_fqn"] for e in impls}
        assert any("Validatable" in fqn for fqn in parent_fqns), \
            "enum IMPLEMENTS edge must be recorded for Validatable"


# ---------------------------------------------------------------------------
# RC-Assert: @AssertTrue framework_pass must not emit cross-method edges
# ---------------------------------------------------------------------------

class TestAssertTrueFrameworkPassNoCrossEdges:
    """_pass_a_assert_true must only emit <init>→@AssertTrue, not sibling @AssertTrue edges."""

    def test_no_cross_assert_true_edges(self, tmp_path):
        """Two @AssertTrue methods on same class must not get edges to each other."""
        from orihime.indexer import index_repo
        import kuzu

        src = textwrap.dedent("""\
            package com.example;
            import javax.validation.constraints.AssertTrue;
            public class Config {
                private String a;
                private String b;

                @AssertTrue
                public boolean isAValid() { return a != null; }

                @AssertTrue
                public boolean isBValid() { return b != null; }
            }
        """)
        repo_dir = tmp_path / "repo"
        java_dir = repo_dir / "src" / "main" / "java" / "com" / "example"
        java_dir.mkdir(parents=True)
        (java_dir / "Config.java").write_text(src)

        db_path = str(tmp_path / "test.db")
        index_repo(str(repo_dir), "assert-test", db_path)

        db = kuzu.Database(db_path)
        conn = kuzu.Connection(db)

        # Check no edge from isAValid → isBValid or vice versa
        res = conn.execute(
            "MATCH (a:Method)-[:CALLS]->(b:Method) "
            "WHERE a.fqn CONTAINS 'isAValid' AND b.fqn CONTAINS 'isBValid' "
            "RETURN count(*)"
        )
        assert res.get_next()[0] == 0, "isAValid must NOT call isBValid"

        res2 = conn.execute(
            "MATCH (a:Method)-[:CALLS]->(b:Method) "
            "WHERE a.fqn CONTAINS 'isBValid' AND b.fqn CONTAINS 'isAValid' "
            "RETURN count(*)"
        )
        assert res2.get_next()[0] == 0, "isBValid must NOT call isAValid"
