"""Unit tests for dedalus.js_extractor — JsExtractor on inline JS/TS source."""
from __future__ import annotations

import pytest

import dedalus.js_extractor  # noqa: F401 — triggers register()
from dedalus.js_extractor import JsExtractor
from dedalus.language import get_parser

FILE_ID = "file1"
REPO_ID = "repo1"


def _parse_js(src: bytes):
    parser = get_parser("javascript")
    return parser.parse(src)


def _parse_ts(src: bytes):
    parser = get_parser("typescript")
    return parser.parse(src)


def _extract_js(src: bytes, file_path: str = "module.js"):
    tree = _parse_js(src)
    extractor = JsExtractor(language="javascript", file_extensions=frozenset({".js", ".jsx"}))
    return extractor.extract(tree, src, FILE_ID, REPO_ID, file_path=file_path)


def _extract_ts(src: bytes, file_path: str = "module.ts"):
    tree = _parse_ts(src)
    extractor = JsExtractor(language="typescript", file_extensions=frozenset({".ts", ".tsx"}))
    return extractor.extract(tree, src, FILE_ID, REPO_ID, file_path=file_path)


# ---------------------------------------------------------------------------
# Test 1: ES6 class extracted as Class node
# ---------------------------------------------------------------------------

_CLASS_SRC = b"""
class UserService {
  getUser(id) { return id; }
}
"""


def test_class_extracted():
    result = _extract_js(_CLASS_SRC)
    class_names = [c["name"] for c in result.classes]
    assert "UserService" in class_names


def test_class_has_required_fields():
    result = _extract_js(_CLASS_SRC)
    cls = next(c for c in result.classes if c["name"] == "UserService")
    assert cls["id"]
    assert cls["fqn"] == "UserService"
    assert cls["file_id"] == FILE_ID
    assert cls["repo_id"] == REPO_ID
    assert cls["is_interface"] is False
    assert "annotations" in cls


# ---------------------------------------------------------------------------
# Test 2: Class method extracted as Method node
# ---------------------------------------------------------------------------

def test_class_method_extracted():
    result = _extract_js(_CLASS_SRC)
    method_names = [m["name"] for m in result.methods]
    assert "getUser" in method_names


def test_class_method_has_required_fields():
    result = _extract_js(_CLASS_SRC)
    method = next(m for m in result.methods if m["name"] == "getUser")
    assert method["id"]
    assert method["file_id"] == FILE_ID
    assert method["repo_id"] == REPO_ID
    assert "is_entry_point" in method
    assert "complexity_hint" in method
    assert "generated" in method
    assert "is_suspend" in method
    assert method["line_start"] > 0


# ---------------------------------------------------------------------------
# Test 3: Next.js App Router GET handler → Endpoint with http_method=GET
# ---------------------------------------------------------------------------

_NEXTJS_APP_ROUTER_SRC = b"""
export async function GET(request) {
  return Response.json({ ok: true });
}
"""


def test_nextjs_app_router_endpoint_extracted():
    result = _extract_js(
        _NEXTJS_APP_ROUTER_SRC,
        file_path="app/api/users/route.js",
    )
    assert len(result.endpoints) >= 1
    ep = result.endpoints[0]
    assert ep["http_method"] == "GET"


def test_nextjs_app_router_endpoint_path():
    result = _extract_js(
        _NEXTJS_APP_ROUTER_SRC,
        file_path="app/api/users/route.js",
    )
    ep = result.endpoints[0]
    assert ep["path"] == "/api/users"


def test_nextjs_app_router_endpoint_has_handler_method_id():
    result = _extract_js(
        _NEXTJS_APP_ROUTER_SRC,
        file_path="app/api/users/route.js",
    )
    ep = result.endpoints[0]
    assert ep["handler_method_id"]  # non-empty, links to the GET method


# ---------------------------------------------------------------------------
# Test 4: Express app.get('/path', fn) → Endpoint
# ---------------------------------------------------------------------------

_EXPRESS_SRC = b"""
const app = express();

app.get('/users', async (req, res) => {
  res.json([]);
});

app.post('/users', createHandler);
"""


def test_express_get_endpoint():
    result = _extract_js(_EXPRESS_SRC)
    endpoints = result.endpoints
    get_eps = [e for e in endpoints if e["http_method"] == "GET"]
    assert any(e["path"] == "/users" for e in get_eps)


def test_express_post_endpoint():
    result = _extract_js(_EXPRESS_SRC)
    post_eps = [e for e in result.endpoints if e["http_method"] == "POST"]
    assert any(e["path"] == "/users" for e in post_eps)


# ---------------------------------------------------------------------------
# Test 5: fetch('/api/x') → RestCall with http_method=GET
# ---------------------------------------------------------------------------

_FETCH_SRC = b"""
async function loadData() {
  const r = await fetch('/api/x');
  return r.json();
}
"""


def test_fetch_default_get():
    result = _extract_js(_FETCH_SRC)
    assert len(result.rest_calls) >= 1
    call = result.rest_calls[0]
    assert call["http_method"] == "GET"
    assert call["url_pattern"] == "/api/x"


# ---------------------------------------------------------------------------
# Test 6: axios.post('/api/y') → RestCall with http_method=POST
# ---------------------------------------------------------------------------

_AXIOS_SRC = b"""
async function createUser(payload) {
  const res = await axios.post('/api/users', payload);
  return res.data;
}
"""


def test_axios_post():
    result = _extract_js(_AXIOS_SRC)
    assert len(result.rest_calls) >= 1
    call = result.rest_calls[0]
    assert call["http_method"] == "POST"
    assert call["url_pattern"] == "/api/users"


# ---------------------------------------------------------------------------
# Test 7: TypeScript class with typed methods extracted correctly
# ---------------------------------------------------------------------------

_TS_CLASS_SRC = b"""
class OrderService {
  private db: Database;

  async findOrder(id: string): Promise<Order> {
    return this.db.find(id);
  }

  async createOrder(data: CreateOrderDto): Promise<Order> {
    return this.db.save(data);
  }
}
"""


def test_ts_class_extracted():
    result = _extract_ts(_TS_CLASS_SRC)
    class_names = [c["name"] for c in result.classes]
    assert "OrderService" in class_names


def test_ts_methods_extracted():
    result = _extract_ts(_TS_CLASS_SRC)
    method_names = [m["name"] for m in result.methods]
    assert "findOrder" in method_names
    assert "createOrder" in method_names


def test_ts_method_fields_complete():
    result = _extract_ts(_TS_CLASS_SRC)
    for m in result.methods:
        assert "is_entry_point" in m
        assert "complexity_hint" in m
        assert "generated" in m
        assert "is_suspend" in m


# ---------------------------------------------------------------------------
# Test 8: Standalone async function extracted as Method
# ---------------------------------------------------------------------------

_STANDALONE_FN_SRC = b"""
async function processPayment(orderId) {
  return { status: 'ok' };
}
"""


def test_standalone_function_extracted_as_method():
    result = _extract_js(_STANDALONE_FN_SRC)
    method_names = [m["name"] for m in result.methods]
    assert "processPayment" in method_names


def test_standalone_function_has_correct_fields():
    result = _extract_js(_STANDALONE_FN_SRC)
    m = next(m for m in result.methods if m["name"] == "processPayment")
    assert m["file_id"] == FILE_ID
    assert m["repo_id"] == REPO_ID
    assert m["is_entry_point"] is False
    assert m["generated"] is False
    assert m["is_suspend"] is False


# ---------------------------------------------------------------------------
# Test 9: Next.js App Router handler has is_entry_point=True
# ---------------------------------------------------------------------------

_NEXTJS_ENTRY_SRC = b"""
export async function POST(request) {
  const body = await request.json();
  return Response.json({ created: true });
}
"""


def test_nextjs_handler_is_entry_point():
    result = _extract_js(
        _NEXTJS_ENTRY_SRC,
        file_path="app/api/orders/route.js",
    )
    methods = result.methods
    post_handler = next((m for m in methods if m["name"] == "POST"), None)
    assert post_handler is not None
    assert post_handler["is_entry_point"] is True


def test_nextjs_post_endpoint_http_method():
    result = _extract_js(
        _NEXTJS_ENTRY_SRC,
        file_path="app/api/orders/route.js",
    )
    post_eps = [e for e in result.endpoints if e["http_method"] == "POST"]
    assert len(post_eps) >= 1


# ---------------------------------------------------------------------------
# Additional: fetch with explicit method option
# ---------------------------------------------------------------------------

_FETCH_POST_SRC = b"""
async function submitForm(data) {
  const r = await fetch('/api/submit', { method: 'POST' });
  return r.json();
}
"""


def test_fetch_with_method_option():
    result = _extract_js(_FETCH_POST_SRC)
    assert len(result.rest_calls) >= 1
    call = result.rest_calls[0]
    assert call["http_method"] == "POST"
    assert call["url_pattern"] == "/api/submit"


# ---------------------------------------------------------------------------
# Additional: axios.get
# ---------------------------------------------------------------------------

_AXIOS_GET_SRC = (
    b"async function getUser(id) {\n"
    b"  const res = await axios.get(`/api/users/${id}`);\n"
    b"  return res.data;\n"
    b"}\n"
)


def test_axios_get():
    result = _extract_js(_AXIOS_GET_SRC)
    # template string URL won't resolve to a plain string — url_pattern may be DYNAMIC or partial
    calls = [c for c in result.rest_calls if c["http_method"] == "GET"]
    assert len(calls) >= 1


# ---------------------------------------------------------------------------
# Additional: Pages Router default export handler
# ---------------------------------------------------------------------------

_PAGES_ROUTER_SRC = b"""
export default function handler(req, res) {
  res.json({ ok: true });
}
"""


def test_pages_router_handler_extracted_as_method():
    result = _extract_js(
        _PAGES_ROUTER_SRC,
        file_path="pages/api/users/index.js",
    )
    method_names = [m["name"] for m in result.methods]
    assert "handler" in method_names


def test_pages_router_handler_endpoint_extracted():
    result = _extract_js(
        _PAGES_ROUTER_SRC,
        file_path="pages/api/users/index.js",
    )
    # handler is a default export, treated as an endpoint
    assert len(result.endpoints) >= 1
