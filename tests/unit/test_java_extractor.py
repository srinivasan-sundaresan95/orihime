"""Unit tests for indra.java_extractor — JavaExtractor on Sample.java."""
from __future__ import annotations

import pathlib

import pytest

import indra.java_extractor  # noqa: F401 — triggers register()
from indra.java_extractor import JavaExtractor
from indra.language import get_parser

FIXTURE = pathlib.Path(__file__).parent.parent / "fixtures" / "Sample.java"


@pytest.fixture(scope="module")
def extract_result():
    src = FIXTURE.read_bytes()
    parser = get_parser("java")
    tree = parser.parse(src)
    extractor = JavaExtractor()
    return extractor.extract(tree, src, "file1", "repo1")


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------


def test_exactly_one_class(extract_result):
    assert len(extract_result.classes) == 1


def test_class_name(extract_result):
    cls = extract_result.classes[0]
    assert cls["name"] == "SampleController"


def test_class_fqn(extract_result):
    cls = extract_result.classes[0]
    assert cls["fqn"] == "com.example.SampleController"


def test_class_ids_non_empty(extract_result):
    for cls in extract_result.classes:
        assert cls["id"] and isinstance(cls["id"], str)


def test_class_file_and_repo(extract_result):
    cls = extract_result.classes[0]
    assert cls["file_id"] == "file1"
    assert cls["repo_id"] == "repo1"


def test_class_annotations_contain_rest_controller(extract_result):
    cls = extract_result.classes[0]
    assert "RestController" in cls["annotations"]


# ---------------------------------------------------------------------------
# Methods
# ---------------------------------------------------------------------------


def test_at_least_three_methods(extract_result):
    assert len(extract_result.methods) >= 3


def test_method_names_present(extract_result):
    names = {m["name"] for m in extract_result.methods}
    assert "getUser" in names
    assert "createUser" in names
    assert "helperMethod" in names


def test_method_ids_non_empty(extract_result):
    for m in extract_result.methods:
        assert m["id"] and isinstance(m["id"], str)


def test_method_line_start_positive(extract_result):
    for m in extract_result.methods:
        assert m["line_start"] > 0, f"Method {m['name']} has line_start={m['line_start']}"


def test_method_file_and_repo(extract_result):
    for m in extract_result.methods:
        assert m["file_id"] == "file1"
        assert m["repo_id"] == "repo1"


def test_method_fqn_contains_class_fqn(extract_result):
    for m in extract_result.methods:
        assert m["fqn"].startswith("com.example.SampleController.")


def test_method_is_suspend_false(extract_result):
    for m in extract_result.methods:
        assert m["is_suspend"] is False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def test_exactly_two_endpoints(extract_result):
    assert len(extract_result.endpoints) == 2


def test_endpoint_get_users_id(extract_result):
    get_eps = [e for e in extract_result.endpoints if e["http_method"] == "GET"]
    assert len(get_eps) == 1
    assert get_eps[0]["path"] == "/api/users/{id}"


def test_endpoint_post_users(extract_result):
    post_eps = [e for e in extract_result.endpoints if e["http_method"] == "POST"]
    assert len(post_eps) == 1
    assert post_eps[0]["path"] == "/api/users"


def test_endpoint_ids_non_empty(extract_result):
    for e in extract_result.endpoints:
        assert e["id"] and isinstance(e["id"], str)


def test_endpoint_repo_id(extract_result):
    for e in extract_result.endpoints:
        assert e["repo_id"] == "repo1"


def test_endpoint_handler_method_id_matches_method(extract_result):
    method_ids = {m["id"] for m in extract_result.methods}
    for e in extract_result.endpoints:
        assert e["handler_method_id"] in method_ids


# ---------------------------------------------------------------------------
# Rest calls
# ---------------------------------------------------------------------------


def test_exactly_one_rest_call(extract_result):
    assert len(extract_result.rest_calls) == 1


def test_rest_call_url_contains_user_service(extract_result):
    rc = extract_result.rest_calls[0]
    assert "user-service" in rc["url_pattern"]


def test_rest_call_http_method(extract_result):
    rc = extract_result.rest_calls[0]
    assert rc["http_method"] == "GET"


def test_rest_call_id_non_empty(extract_result):
    rc = extract_result.rest_calls[0]
    assert rc["id"] and isinstance(rc["id"], str)


def test_rest_call_repo_id(extract_result):
    rc = extract_result.rest_calls[0]
    assert rc["repo_id"] == "repo1"


def test_rest_call_caller_method_id_matches_method(extract_result):
    method_ids = {m["id"] for m in extract_result.methods}
    rc = extract_result.rest_calls[0]
    assert rc["caller_method_id"] in method_ids


def test_rest_call_caller_is_get_user(extract_result):
    """The RestTemplate call is in getUser, so caller_method_id must match getUser's id."""
    get_user_method = next(
        (m for m in extract_result.methods if m["name"] == "getUser"), None
    )
    assert get_user_method is not None
    rc = extract_result.rest_calls[0]
    assert rc["caller_method_id"] == get_user_method["id"]
