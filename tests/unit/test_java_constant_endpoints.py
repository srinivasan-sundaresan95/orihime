"""Unit tests for P2-4: endpoint path extraction via static constant references."""
from __future__ import annotations

import pathlib

import dedalus.java_extractor  # noqa: F401 — triggers register()
from dedalus.java_extractor import JavaExtractor, _extract_static_final_strings
from dedalus.language import get_parser

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"
CONSTANTS_FILE = FIXTURES / "ConstantEndpoints.java"
WALLET_FILE = FIXTURES / "WalletController.java"


def _build_constant_index() -> dict[str, str]:
    """Parse ConstantEndpoints.java and return a constant_index."""
    src = CONSTANTS_FILE.read_bytes()
    parser = get_parser("java")
    tree = parser.parse(src)

    # Walk for class_body nodes and extract static final strings
    from dedalus.java_extractor import _walk_all, _text, _find_first_child_of_type

    constant_index: dict[str, str] = {}
    root = tree.root_node
    for node in _walk_all(root):
        if node.type == "class_declaration":
            name_node = node.child_by_field_name("name") or _find_first_child_of_type(node, "identifier")
            if name_node is None:
                continue
            class_name = _text(name_node, src)
            body_node = node.child_by_field_name("body") or _find_first_child_of_type(node, "class_body")
            if body_node:
                constant_index.update(_extract_static_final_strings(body_node, class_name, src))
    return constant_index


def test_constant_index_contains_wallet_status():
    idx = _build_constant_index()
    assert "RequestMapping.WALLET_STATUS" in idx
    assert idx["RequestMapping.WALLET_STATUS"] == "/wallet/status"


def test_constant_index_contains_user_info():
    idx = _build_constant_index()
    assert "RequestMapping.USER_INFO" in idx
    assert idx["RequestMapping.USER_INFO"] == "/user/info"


def test_wallet_controller_get_endpoint_path():
    """GET endpoint path should resolve to /api/wallet/status."""
    constant_index = _build_constant_index()

    src = WALLET_FILE.read_bytes()
    parser = get_parser("java")
    tree = parser.parse(src)
    extractor = JavaExtractor()
    result = extractor.extract(tree, src, "wallet_file", "repo1", constant_index=constant_index)

    get_eps = [e for e in result.endpoints if e["http_method"] == "GET"]
    assert len(get_eps) == 1, f"Expected 1 GET endpoint, got {len(get_eps)}: {get_eps}"
    assert get_eps[0]["path"] == "/api/wallet/status", (
        f"Expected /api/wallet/status, got {get_eps[0]['path']!r}"
    )


def test_wallet_controller_post_endpoint_path():
    """POST endpoint path should resolve to /api/user/info."""
    constant_index = _build_constant_index()

    src = WALLET_FILE.read_bytes()
    parser = get_parser("java")
    tree = parser.parse(src)
    extractor = JavaExtractor()
    result = extractor.extract(tree, src, "wallet_file", "repo1", constant_index=constant_index)

    post_eps = [e for e in result.endpoints if e["http_method"] == "POST"]
    assert len(post_eps) == 1, f"Expected 1 POST endpoint, got {len(post_eps)}: {post_eps}"
    assert post_eps[0]["path"] == "/api/user/info", (
        f"Expected /api/user/info, got {post_eps[0]['path']!r}"
    )


def test_wallet_controller_without_constant_index_produces_empty_paths():
    """Without a constant_index, field_access paths resolve to empty strings."""
    src = WALLET_FILE.read_bytes()
    parser = get_parser("java")
    tree = parser.parse(src)
    extractor = JavaExtractor()
    result = extractor.extract(tree, src, "wallet_file", "repo1")

    # Endpoints are still emitted (annotations are found) but paths are empty
    # because the constant values are unknown.
    assert len(result.endpoints) == 2, (
        f"Expected 2 endpoints even without constant_index, got {len(result.endpoints)}"
    )
    for ep in result.endpoints:
        assert ep["path"] == "/api", (
            f"Without constant_index, path should be just the class prefix '/api', got {ep['path']!r}"
        )


def test_in_file_constant_resolution():
    """Constants defined in the same file are resolved automatically (Pass 1 + Pass 2)."""
    # WalletController.java does NOT define the constants itself.
    # Use a self-contained file with both constants and controller.
    src = b"""
package com.example;
import org.springframework.web.bind.annotation.*;

public class Paths {
    public static final String HELLO = "/hello";
}

@RestController
@RequestMapping("/v1")
class HelloController {
    @GetMapping(path = Paths.HELLO)
    public String hello() { return "hi"; }
}
"""
    parser = get_parser("java")
    tree = parser.parse(src)
    extractor = JavaExtractor()
    result = extractor.extract(tree, src, "f1", "r1")

    get_eps = [e for e in result.endpoints if e["http_method"] == "GET"]
    assert len(get_eps) == 1
    assert get_eps[0]["path"] == "/v1/hello", (
        f"Expected /v1/hello (in-file constant), got {get_eps[0]['path']!r}"
    )
