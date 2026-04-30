"""Unit tests for the _strip_scheme_host helper and full-URL cross-resolution (P4-4).

Tests verify that:
  1. _strip_scheme_host correctly removes http:// + host prefixes.
  2. A full URL like "http://svc/wallet/balance" resolves to a CALLS_REST edge
     against an endpoint compiled from "/wallet/balance".
"""
from __future__ import annotations

import pytest

from indra.cross_resolver import _strip_scheme_host
from indra.path_utils import compile_path_regex, match_url_pattern


# ---------------------------------------------------------------------------
# Tests for _strip_scheme_host
# ---------------------------------------------------------------------------

def test_strip_scheme_host_http():
    """http:// scheme + host must be stripped, leaving the path."""
    assert _strip_scheme_host("http://svc/wallet/balance") == "/wallet/balance"


def test_strip_scheme_host_https():
    """https:// scheme + FQDN host must be stripped, leaving the path."""
    assert _strip_scheme_host("https://internal-svc.example.com/api/v1/items") == "/api/v1/items"


def test_strip_scheme_host_path_only_unchanged():
    """A path-only string must be returned unchanged."""
    assert _strip_scheme_host("/wallet/balance") == "/wallet/balance"


def test_strip_scheme_host_empty_path_returns_empty():
    """A URL with no path component (just scheme+host) must return an empty string."""
    assert _strip_scheme_host("http://svc") == ""


# ---------------------------------------------------------------------------
# Integration-style test: full URL resolves via match_url_pattern directly
# ---------------------------------------------------------------------------

def test_full_url_resolves_cross_repo():
    """A full URL, after stripping scheme+host, must match the compiled path regex."""
    path_regex = compile_path_regex("/wallet/balance")
    stripped = _strip_scheme_host("http://svc/wallet/balance")
    assert match_url_pattern(stripped, path_regex) is True
