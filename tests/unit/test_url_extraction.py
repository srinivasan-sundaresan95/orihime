"""Unit tests for P3-2.1 and P3-2.2: improved URL extraction in java_extractor.py.

P3-2.1 — _extract_url_from_binary_expression:
  - string_literal + string_literal → concatenate
  - field_access + string_literal   → resolve field via constant_index, append right
  - string_literal + field_access   → left + resolve field via constant_index
  - unresolvable → None (caller emits 'DYNAMIC')

P3-2.2 — _extract_url_from_uri_builder:
  - Walks UriComponentsBuilder.fromHttpUrl("base").path("/sub").build() chains
  - Also handles .fromUriString("base")
  - Returns base + path, or None if no base found
"""
from __future__ import annotations

import dedalus.java_extractor  # noqa: F401 — triggers register()
from dedalus.java_extractor import JavaExtractor
from dedalus.language import get_parser


def _parse_and_extract(src: bytes) -> "ExtractResult":
    """Parse inline Java source and run JavaExtractor.extract."""
    parser = get_parser("java")
    tree = parser.parse(src)
    extractor = JavaExtractor()
    return extractor.extract(tree, src, "file1", "repo1")


# ---------------------------------------------------------------------------
# Test 1 — string_literal + string_literal → concatenated url_pattern
# ---------------------------------------------------------------------------

def test_string_concat_produces_combined_url():
    """P3-2.1: 'http://svc' + '/orders' → url_pattern 'http://svc/orders'."""
    src = b"""
package com.example;
public class TestClass {
    void testMethod() {
        restTemplate.getForObject("http://svc" + "/orders", String.class);
    }
}
"""
    result = _parse_and_extract(src)
    assert len(result.rest_calls) == 1
    assert result.rest_calls[0]["url_pattern"] == "http://svc/orders"
    assert result.rest_calls[0]["http_method"] == "GET"


# ---------------------------------------------------------------------------
# Test 2 — field_access (constant) + string_literal → resolved url_pattern
# ---------------------------------------------------------------------------

def test_base_url_constant_plus_path_resolved():
    """P3-2.1: BASE_URL (public static final String) + '/orders' → 'http://svc/orders'."""
    src = b"""
package com.example;
public class TestClass {
    public static final String BASE_URL = "http://svc";
    void testMethod() {
        restTemplate.getForObject(TestClass.BASE_URL + "/orders", String.class);
    }
}
"""
    result = _parse_and_extract(src)
    assert len(result.rest_calls) == 1
    assert result.rest_calls[0]["url_pattern"] == "http://svc/orders"


# ---------------------------------------------------------------------------
# Test 3 — string_literal + field_access (constant) → resolved url_pattern
# ---------------------------------------------------------------------------

def test_string_plus_path_suffix_constant_resolved():
    """P3-2.1: '/api' + PATH_SUFFIX (public static final String) → '/api/items'."""
    src = b"""
package com.example;
public class TestClass {
    public static final String PATH_SUFFIX = "/items";
    void testMethod() {
        restTemplate.getForObject("/api" + TestClass.PATH_SUFFIX, String.class);
    }
}
"""
    result = _parse_and_extract(src)
    assert len(result.rest_calls) == 1
    assert result.rest_calls[0]["url_pattern"] == "/api/items"


# ---------------------------------------------------------------------------
# Test 4 — unresolvable binary expression → 'DYNAMIC'
# ---------------------------------------------------------------------------

def test_dynamic_variable_concat_gives_dynamic():
    """P3-2.1: dynamicVar + '/orders' → url_pattern 'DYNAMIC' (no literal or resolvable field)."""
    src = b"""
package com.example;
public class TestClass {
    void testMethod() {
        restTemplate.getForObject(dynamicVar + "/orders", String.class);
    }
}
"""
    result = _parse_and_extract(src)
    assert len(result.rest_calls) == 1
    assert result.rest_calls[0]["url_pattern"] == "DYNAMIC"


# ---------------------------------------------------------------------------
# Test 5 — UCB fromHttpUrl + .path() assigned to variable, url_pattern resolved
# ---------------------------------------------------------------------------

def test_ucb_from_http_url_with_path_produces_combined_url():
    """P3-2.2: UCB.fromHttpUrl('http://svc').path('/wallet').build() → 'http://svc/wallet'."""
    src = b"""
package com.example;
public class TestClass {
    void testMethod() {
        String url = UriComponentsBuilder.fromHttpUrl("http://svc").path("/wallet").build().toUriString();
        restTemplate.getForObject(url, String.class);
    }
}
"""
    result = _parse_and_extract(src)
    ucb_calls = [rc for rc in result.rest_calls if rc["url_pattern"] == "http://svc/wallet"]
    assert len(ucb_calls) >= 1, (
        f"Expected at least 1 rest_call with url_pattern 'http://svc/wallet', "
        f"got: {[rc['url_pattern'] for rc in result.rest_calls]}"
    )


# ---------------------------------------------------------------------------
# Test 6 — UCB fromUriString without .path() → base URL only
# ---------------------------------------------------------------------------

def test_ucb_from_uri_string_no_path_produces_base_url():
    """P3-2.2: UCB.fromUriString('http://base').build().toUriString() (no .path()) → 'http://base'."""
    src = b"""
package com.example;
public class TestClass {
    void testMethod() {
        String url = UriComponentsBuilder.fromUriString("http://base").build().toUriString();
        restTemplate.getForObject(url, String.class);
    }
}
"""
    result = _parse_and_extract(src)
    ucb_calls = [rc for rc in result.rest_calls if rc["url_pattern"] == "http://base"]
    assert len(ucb_calls) >= 1, (
        f"Expected at least 1 rest_call with url_pattern 'http://base', "
        f"got: {[rc['url_pattern'] for rc in result.rest_calls]}"
    )


# ---------------------------------------------------------------------------
# Test 7 — UCB chain passed directly as inline arg → exactly ONE rest_call
# ---------------------------------------------------------------------------

def test_ucb_inline_arg_produces_exactly_one_rest_call():
    """P3-2.2: UCB chain as direct argument → exactly ONE rest_call with correct url_pattern."""
    src = b"""
package com.example;
public class TestClass {
    void testMethod() {
        restTemplate.getForObject(UriComponentsBuilder.fromHttpUrl("http://svc").path("/items").build().toUriString(), String.class);
    }
}
"""
    result = _parse_and_extract(src)
    assert len(result.rest_calls) == 1, (
        f"Expected exactly 1 rest_call when UCB is passed inline, "
        f"got {len(result.rest_calls)}: {[rc['url_pattern'] for rc in result.rest_calls]}"
    )
    # The UCB chain is an opaque method_invocation arg to getForObject; the
    # extractor cannot statically evaluate it at that call site → DYNAMIC.
    assert result.rest_calls[0]["url_pattern"] == "DYNAMIC", (
        f"Expected url_pattern 'DYNAMIC' for inline UCB arg, "
        f"got {result.rest_calls[0]['url_pattern']!r}"
    )


# ---------------------------------------------------------------------------
# Test 8 — plain string literal still works unchanged
# ---------------------------------------------------------------------------

def test_plain_string_literal_url_pattern():
    """Baseline: plain string literal → url_pattern equals literal value."""
    src = b"""
package com.example;
public class TestClass {
    void testMethod() {
        restTemplate.getForObject("http://svc/status", String.class);
    }
}
"""
    result = _parse_and_extract(src)
    assert len(result.rest_calls) == 1
    assert result.rest_calls[0]["url_pattern"] == "http://svc/status"
    assert result.rest_calls[0]["http_method"] == "GET"


# ---------------------------------------------------------------------------
# Test 9 — UCB fromHttpUrl with .path() inline: http_method is GET
# ---------------------------------------------------------------------------

def test_ucb_standalone_http_method_is_get():
    """P3-2.2: UCB rest_call emitted with http_method='GET'."""
    src = b"""
package com.example;
public class TestClass {
    void testMethod() {
        String url = UriComponentsBuilder.fromHttpUrl("http://svc").path("/check").build().toUriString();
    }
}
"""
    result = _parse_and_extract(src)
    ucb_calls = [rc for rc in result.rest_calls if rc["url_pattern"] == "http://svc/check"]
    assert len(ucb_calls) == 1
    assert ucb_calls[0]["http_method"] == "GET"


# ---------------------------------------------------------------------------
# Test 10 — binary expression with both operands unresolvable → 'DYNAMIC'
# ---------------------------------------------------------------------------

def test_two_dynamic_vars_concat_gives_dynamic():
    """P3-2.1: dynamicA + dynamicB → url_pattern 'DYNAMIC' (neither operand is literal/constant)."""
    src = b"""
package com.example;
public class TestClass {
    void testMethod() {
        restTemplate.getForObject(baseUrl + pathVar, String.class);
    }
}
"""
    result = _parse_and_extract(src)
    assert len(result.rest_calls) == 1
    assert result.rest_calls[0]["url_pattern"] == "DYNAMIC"


# ---------------------------------------------------------------------------
# Test 11 — field_access with constant NOT in index → DYNAMIC (not "*...")
# ---------------------------------------------------------------------------

def test_field_access_not_in_index_gives_dynamic():
    """P3-2.1: OtherClass.ENDPOINT + '/data' where constant is from another file → DYNAMIC.

    The '*' fallback was removed (cross_resolver cannot match it); the correct
    behavior when no entry exists in constant_index is to produce 'DYNAMIC'.
    """
    src = b"""
package com.example;
public class TestClass {
    void testMethod() {
        restTemplate.getForObject(OtherClass.ENDPOINT + "/data", String.class);
    }
}
"""
    result = _parse_and_extract(src)
    assert len(result.rest_calls) == 1
    url_pattern = result.rest_calls[0]["url_pattern"]
    assert url_pattern == "DYNAMIC", (
        f"Expected 'DYNAMIC' when field_access is not in constant_index, got {url_pattern!r}"
    )
