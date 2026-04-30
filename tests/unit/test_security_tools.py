"""Unit tests for v1.2 security MCP tools (S4-S7)."""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from dedalus.indexer import index_repo
from dedalus.security_config import load_security_config, SecurityConfig

FIXTURES_DIR = pathlib.Path(__file__).parent.parent / "fixtures"


def _make_db_path() -> pathlib.Path:
    tmpdir = tempfile.mkdtemp()
    return pathlib.Path(tmpdir) / "test.db"


# ---------------------------------------------------------------------------
# SecurityConfig unit tests (S5)
# ---------------------------------------------------------------------------

def test_security_config_loads_builtin_defaults():
    cfg = load_security_config(config_path="/nonexistent/path.yml")
    assert "RequestParam" in cfg.source_annotations
    assert "RequestBody" in cfg.source_annotations
    assert "PathVariable" in cfg.source_annotations


def test_security_config_builtin_sinks():
    cfg = load_security_config(config_path="/nonexistent/path.yml")
    # SQL sinks
    assert any("execute" in s for s in cfg.sink_methods)
    # HTTP client sinks
    assert any("RestTemplate" in s or "getForEntity" in s for s in cfg.sink_methods)


def test_is_source_annotation_by_short_name():
    cfg = load_security_config(config_path="/nonexistent/path.yml")
    assert cfg.is_source_annotation("RequestParam")
    assert cfg.is_source_annotation("org.springframework.web.bind.annotation.RequestParam")
    assert not cfg.is_source_annotation("SomeOtherAnnotation")


def test_is_sink_method_short_name():
    cfg = load_security_config(config_path="/nonexistent/path.yml")
    assert cfg.is_sink_method("Statement.execute")
    assert cfg.is_sink_method("execute")
    assert not cfg.is_sink_method("doSomethingHarmless")


def test_is_sanitizer_method():
    cfg = load_security_config(config_path="/nonexistent/path.yml")
    assert cfg.is_sanitizer_method("HtmlUtils.htmlEscape")
    assert not cfg.is_sanitizer_method("doWork")


def test_security_config_merges_user_yaml(tmp_path):
    yaml_file = tmp_path / "security.yml"
    yaml_file.write_text("""
version: 1
sources:
  annotations:
    - "com.example.MyCustomSource"
sinks:
  methods:
    - "com.example.MyDangerousSink.doIt"
""")
    cfg = load_security_config(config_path=yaml_file)
    assert "com.example.MyCustomSource" in cfg.source_annotations
    assert "com.example.MyDangerousSink.doIt" in cfg.sink_methods
    # Built-ins still present
    assert "RequestParam" in cfg.source_annotations


# ---------------------------------------------------------------------------
# Integration: security MCP tools with fixture DB
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def security_db():
    db_path = _make_db_path()
    index_repo(FIXTURES_DIR, "test-repo", db_path, max_workers=1)
    return db_path


def test_find_cross_service_taint_returns_list(security_db, monkeypatch):
    import dedalus.mcp_server as mcp_mod
    import kuzu
    db = kuzu.Database(str(security_db))
    conn = kuzu.Connection(db)
    monkeypatch.setattr(mcp_mod, "_conn", conn)
    monkeypatch.setattr(mcp_mod, "_db", db)

    result = mcp_mod.find_cross_service_taint("test-repo")
    assert isinstance(result, list)
    # Each result must have expected keys if non-empty
    for item in result:
        assert "error" not in item
        assert "source_handler_fqn" in item
        assert "sink_url_pattern" in item
        assert "call_chain" in item
        assert isinstance(item["call_chain"], list)


def test_find_taint_sinks_returns_list(security_db, monkeypatch):
    import dedalus.mcp_server as mcp_mod
    import kuzu
    db = kuzu.Database(str(security_db))
    conn = kuzu.Connection(db)
    monkeypatch.setattr(mcp_mod, "_conn", conn)
    monkeypatch.setattr(mcp_mod, "_db", db)

    result = mcp_mod.find_taint_sinks("test-repo")
    assert isinstance(result, list)
    for item in result:
        assert "error" not in item
        assert "caller_fqn" in item
        assert "sink_method" in item


def test_list_security_config_returns_dict():
    import dedalus.mcp_server as mcp_mod
    result = mcp_mod.list_security_config()
    assert isinstance(result, dict)
    assert "source_annotations" in result
    assert "sink_methods" in result
    assert "sanitizer_methods" in result
    assert len(result["source_annotations"]) > 0
    assert len(result["sink_methods"]) > 0


def test_find_second_order_injection_returns_list(security_db, monkeypatch):
    import dedalus.mcp_server as mcp_mod
    import kuzu
    db = kuzu.Database(str(security_db))
    conn = kuzu.Connection(db)
    monkeypatch.setattr(mcp_mod, "_conn", conn)
    monkeypatch.setattr(mcp_mod, "_db", db)

    result = mcp_mod.find_second_order_injection("test-repo")
    assert isinstance(result, list)
    for item in result:
        assert "error" not in item
        if item:
            assert "write_method_fqn" in item
            assert "read_method_fqn" in item
            assert "risk_level" in item
            assert item["risk_level"] in ("HIGH", "MEDIUM")


def test_generate_security_report_owasp(security_db, monkeypatch):
    import dedalus.mcp_server as mcp_mod
    import kuzu
    db = kuzu.Database(str(security_db))
    conn = kuzu.Connection(db)
    monkeypatch.setattr(mcp_mod, "_conn", conn)
    monkeypatch.setattr(mcp_mod, "_db", db)

    result = mcp_mod.generate_security_report("test-repo", framework="owasp")
    assert isinstance(result, list)
    for item in result:
        assert "error" not in item
        assert "category" in item
        assert item["category"].startswith("A")


def test_generate_security_report_cwe(security_db, monkeypatch):
    import dedalus.mcp_server as mcp_mod
    import kuzu
    db = kuzu.Database(str(security_db))
    conn = kuzu.Connection(db)
    monkeypatch.setattr(mcp_mod, "_conn", conn)
    monkeypatch.setattr(mcp_mod, "_db", db)

    result = mcp_mod.generate_security_report("test-repo", framework="cwe")
    assert isinstance(result, list)
    for item in result:
        assert "error" not in item
        assert "cwe_id" in item


def test_generate_security_report_invalid_framework(security_db, monkeypatch):
    import dedalus.mcp_server as mcp_mod
    import kuzu
    db = kuzu.Database(str(security_db))
    conn = kuzu.Connection(db)
    monkeypatch.setattr(mcp_mod, "_conn", conn)
    monkeypatch.setattr(mcp_mod, "_db", db)

    result = mcp_mod.generate_security_report("test-repo", framework="invalid")
    assert len(result) == 1
    assert "error" in result[0]


def test_list_branches_returns_list(security_db, monkeypatch):
    import dedalus.mcp_server as mcp_mod
    import kuzu
    db = kuzu.Database(str(security_db))
    conn = kuzu.Connection(db)
    monkeypatch.setattr(mcp_mod, "_conn", conn)
    monkeypatch.setattr(mcp_mod, "_db", db)

    result = mcp_mod.list_branches()
    assert isinstance(result, list)
