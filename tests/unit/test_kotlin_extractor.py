"""Unit tests for KotlinExtractor against SampleController.kt fixture."""
from __future__ import annotations

from pathlib import Path

import pytest

from indra.kotlin_extractor import KotlinExtractor
from indra.language import get_parser

FIXTURE = Path(__file__).parent.parent / "fixtures" / "SampleController.kt"


@pytest.fixture(scope="module")
def result():
    src = FIXTURE.read_bytes()
    parser = get_parser("kotlin")
    tree = parser.parse(src)
    extractor = KotlinExtractor()
    return extractor.extract(tree, src, "file1", "repo1")


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------

def test_exactly_one_class(result):
    assert len(result.classes) == 1


def test_class_name(result):
    assert result.classes[0]["name"] == "SampleController"


def test_class_fqn(result):
    assert result.classes[0]["fqn"] == "com.example.SampleController"


def test_class_id_non_empty(result):
    assert result.classes[0]["id"] != ""


def test_class_is_not_interface(result):
    assert result.classes[0]["is_interface"] is False


def test_class_has_rest_controller_annotation(result):
    annotations = result.classes[0]["annotations"]
    assert "RestController" in annotations


# ---------------------------------------------------------------------------
# Methods
# ---------------------------------------------------------------------------

def test_at_least_three_methods(result):
    assert len(result.methods) >= 3


def test_method_names_present(result):
    names = {m["name"] for m in result.methods}
    assert "getUser" in names
    assert "createUser" in names
    assert "helperMethod" in names


def test_get_user_is_suspend(result):
    method = next(m for m in result.methods if m["name"] == "getUser")
    assert method["is_suspend"] is True


def test_create_user_is_suspend(result):
    method = next(m for m in result.methods if m["name"] == "createUser")
    assert method["is_suspend"] is True


def test_helper_method_not_suspend(result):
    method = next(m for m in result.methods if m["name"] == "helperMethod")
    assert method["is_suspend"] is False


def test_all_method_ids_non_empty(result):
    for m in result.methods:
        assert m["id"] != "", f"Empty id for method {m['name']}"


def test_all_method_line_starts_positive(result):
    for m in result.methods:
        assert m["line_start"] > 0, f"line_start not > 0 for {m['name']}"


def test_methods_have_class_id(result):
    class_id = result.classes[0]["id"]
    for m in result.methods:
        assert m["class_id"] == class_id


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def test_exactly_two_endpoints(result):
    assert len(result.endpoints) == 2


def test_get_endpoint(result):
    get_eps = [e for e in result.endpoints if e["http_method"] == "GET"]
    assert len(get_eps) == 1
    assert get_eps[0]["path"] == "/api/users/{id}"


def test_post_endpoint(result):
    post_eps = [e for e in result.endpoints if e["http_method"] == "POST"]
    assert len(post_eps) == 1
    assert post_eps[0]["path"] == "/api/users"


def test_endpoint_ids_non_empty(result):
    for e in result.endpoints:
        assert e["id"] != ""


def test_endpoint_path_regex_present(result):
    for e in result.endpoints:
        assert e["path_regex"] != ""


# ---------------------------------------------------------------------------
# Rest calls
# ---------------------------------------------------------------------------

def test_exactly_one_rest_call(result):
    assert len(result.rest_calls) == 1


def test_rest_call_url_contains_user_service(result):
    rc = result.rest_calls[0]
    assert "user-service" in rc["url_pattern"]


def test_rest_call_http_method(result):
    rc = result.rest_calls[0]
    assert rc["http_method"] == "GET"


def test_rest_call_id_non_empty(result):
    assert result.rest_calls[0]["id"] != ""


def test_rest_call_caller_method_id(result):
    get_user = next(m for m in result.methods if m["name"] == "getUser")
    rc = result.rest_calls[0]
    assert rc["caller_method_id"] == get_user["id"]
