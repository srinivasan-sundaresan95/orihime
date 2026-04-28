"""Indra Web UI — served by Starlette + uvicorn.

Start with:
    python -m indra ui [--port 7700] [--db ~/.indra/indra.db]

Pages
-----
GET /                   Home / search
GET /symbol?fqn=...     Symbol detail (callers, callees, blast radius)
GET /endpoints          All HTTP endpoints table
GET /index              Index-repo form
POST /index             Trigger indexing
"""
from __future__ import annotations

import logging
import os
import urllib.parse
import webbrowser
from collections import deque
from pathlib import Path
from threading import Timer
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Inline CSS (dark theme)
# ---------------------------------------------------------------------------
_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0f1117;
    color: #e2e8f0;
    min-height: 100vh;
}
a { color: #60a5fa; text-decoration: none; }
a:hover { text-decoration: underline; }

nav {
    background: #1a1d27;
    border-bottom: 1px solid #2d3148;
    padding: 0 24px;
    display: flex;
    align-items: center;
    gap: 32px;
    height: 52px;
}
nav .brand { font-size: 1.1rem; font-weight: 700; color: #818cf8; letter-spacing: 0.04em; }
nav a { color: #94a3b8; font-size: 0.9rem; }
nav a:hover { color: #e2e8f0; text-decoration: none; }

.container { max-width: 1100px; margin: 0 auto; padding: 32px 24px; }

h1 { font-size: 1.6rem; font-weight: 700; color: #f1f5f9; margin-bottom: 20px; }
h2 { font-size: 1.1rem; font-weight: 600; color: #cbd5e1; margin-bottom: 12px; margin-top: 28px; }
h3 { font-size: 0.95rem; font-weight: 600; color: #94a3b8; margin-bottom: 8px; }

.search-box {
    display: flex;
    gap: 10px;
    margin-bottom: 28px;
}
.search-box input {
    flex: 1;
    background: #1e2130;
    border: 1px solid #2d3148;
    border-radius: 8px;
    padding: 10px 16px;
    color: #e2e8f0;
    font-size: 1rem;
    outline: none;
    transition: border-color 0.15s;
}
.search-box input:focus { border-color: #6366f1; }
.search-box button {
    background: #6366f1;
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 10px 22px;
    font-size: 0.95rem;
    cursor: pointer;
    font-weight: 600;
    transition: background 0.15s;
}
.search-box button:hover { background: #4f46e5; }

.card {
    background: #1a1d27;
    border: 1px solid #2d3148;
    border-radius: 10px;
    padding: 14px 18px;
    margin-bottom: 10px;
    display: flex;
    align-items: baseline;
    gap: 12px;
    transition: border-color 0.15s;
}
.card:hover { border-color: #6366f1; }
.badge {
    font-size: 0.7rem;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 4px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    white-space: nowrap;
}
.badge-class  { background: #312e81; color: #a5b4fc; }
.badge-method { background: #164e63; color: #7dd3fc; }
.badge-get    { background: #14532d; color: #86efac; }
.badge-post   { background: #7c2d12; color: #fdba74; }
.badge-put    { background: #713f12; color: #fde68a; }
.badge-delete { background: #4c0519; color: #fca5a5; }
.badge-patch  { background: #1e3a5f; color: #93c5fd; }

.fqn { font-size: 0.9rem; font-family: 'Cascadia Code', 'Fira Code', monospace; color: #e2e8f0; }
.sub { font-size: 0.78rem; color: #64748b; font-family: 'Cascadia Code', 'Fira Code', monospace; margin-left: auto; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 500px; }

.detail-header {
    background: #1a1d27;
    border: 1px solid #2d3148;
    border-radius: 10px;
    padding: 20px;
    margin-bottom: 24px;
}
.detail-header .type-label { font-size: 0.75rem; color: #64748b; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 6px; }
.detail-header .fqn-big { font-size: 1.05rem; font-family: 'Cascadia Code', 'Fira Code', monospace; color: #a5b4fc; word-break: break-all; }
.detail-header .meta { font-size: 0.82rem; color: #64748b; margin-top: 8px; font-family: 'Cascadia Code', 'Fira Code', monospace; }

.section {
    background: #1a1d27;
    border: 1px solid #2d3148;
    border-radius: 10px;
    padding: 18px;
    margin-bottom: 16px;
}
.section-title {
    font-size: 0.85rem;
    font-weight: 700;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
}
.section-title .count {
    background: #2d3148;
    color: #94a3b8;
    font-size: 0.75rem;
    padding: 1px 7px;
    border-radius: 10px;
}

.caller-row {
    padding: 8px 10px;
    border-radius: 6px;
    font-size: 0.85rem;
    font-family: 'Cascadia Code', 'Fira Code', monospace;
    color: #cbd5e1;
    display: flex;
    align-items: center;
    gap: 10px;
}
.caller-row:hover { background: #232638; }
.depth-badge {
    font-size: 0.7rem;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 4px;
    background: #2d3148;
    color: #64748b;
    white-space: nowrap;
}
.depth-1 { background: #2d1f00; color: #fbbf24; }
.depth-2 { background: #1e2d1e; color: #4ade80; }
.depth-3 { background: #1a2040; color: #60a5fa; }

details summary {
    cursor: pointer;
    user-select: none;
    color: #818cf8;
    font-size: 0.85rem;
    font-weight: 600;
    padding: 4px 0;
    outline: none;
}
details summary:hover { color: #a5b4fc; }
details[open] summary { margin-bottom: 10px; }

table { width: 100%; border-collapse: collapse; font-size: 0.875rem; }
thead tr { border-bottom: 1px solid #2d3148; }
th { text-align: left; padding: 10px 14px; color: #64748b; font-weight: 600; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.06em; }
td { padding: 10px 14px; color: #cbd5e1; border-bottom: 1px solid #1e2130; font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: 0.82rem; }
tr:hover td { background: #1e2130; }

.form-group { margin-bottom: 16px; }
.form-group label { display: block; font-size: 0.85rem; color: #94a3b8; margin-bottom: 6px; }
.form-group input {
    width: 100%;
    background: #1e2130;
    border: 1px solid #2d3148;
    border-radius: 8px;
    padding: 10px 14px;
    color: #e2e8f0;
    font-size: 0.95rem;
    outline: none;
}
.form-group input:focus { border-color: #6366f1; }

.btn {
    display: inline-block;
    background: #6366f1;
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 10px 24px;
    font-size: 0.95rem;
    cursor: pointer;
    font-weight: 600;
}
.btn:hover { background: #4f46e5; }

.alert-success {
    background: #052e16;
    border: 1px solid #166534;
    border-radius: 8px;
    padding: 14px 18px;
    color: #86efac;
    margin-bottom: 20px;
    font-size: 0.9rem;
}
.alert-error {
    background: #2d0a0a;
    border: 1px solid #7f1d1d;
    border-radius: 8px;
    padding: 14px 18px;
    color: #fca5a5;
    margin-bottom: 20px;
    font-size: 0.9rem;
}
.empty { color: #475569; font-style: italic; font-size: 0.875rem; }
.back-link { font-size: 0.85rem; color: #64748b; margin-bottom: 20px; display: block; }
.back-link:hover { color: #94a3b8; }
"""

# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _html_page(title: str, body: str) -> str:
    nav = """
<nav>
  <span class="brand">&#9672; Indra</span>
  <a href="/">Search</a>
  <a href="/endpoints">Endpoints</a>
  <a href="/index">Index Repo</a>
</nav>"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)} — Indra</title>
<style>{_CSS}</style>
</head>
<body>
{nav}
<div class="container">
{body}
</div>
</body>
</html>"""


def _esc(s: str) -> str:
    """Minimal HTML escaping."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _badge(kind: str) -> str:
    return f'<span class="badge badge-{kind.lower()}">{_esc(kind)}</span>'


def _fqn_link(fqn: str) -> str:
    encoded = urllib.parse.quote(fqn, safe="")
    return f'<a href="/symbol?fqn={encoded}" class="fqn">{_esc(fqn)}</a>'


# ---------------------------------------------------------------------------
# DB query layer (mirrors mcp_server.py but scoped to UI needs)
# ---------------------------------------------------------------------------

class _DB:
    """Thin wrapper around a KuzuDB connection for UI queries."""

    def __init__(self, db_path: str) -> None:
        import kuzu  # noqa: PLC0415
        path = Path(db_path)
        if not path.exists():
            self._conn: Optional[object] = None
            log.warning("DB not found at %s — UI will show empty results.", db_path)
            return
        db = kuzu.Database(str(path))
        self._conn = kuzu.Connection(db)
        log.info("UI opened KuzuDB at %s", db_path)

    def _rows(self, result, columns: list[str]) -> list[dict]:
        rows: list[dict] = []
        while result.has_next():
            row = result.get_next()
            rows.append(dict(zip(columns, row)))
        return rows

    def search(self, q: str) -> list[dict]:
        if self._conn is None:
            return []
        lower_q = q.lower()
        results: list[dict] = []
        try:
            r = self._conn.execute(
                "MATCH (c:Class) WHERE lower(c.name) CONTAINS $q "
                "RETURN c.fqn AS fqn, c.file_id AS file_id LIMIT 50",
                {"q": lower_q},
            )
            for row in self._rows(r, ["fqn", "file_id"]):
                results.append({"type": "class", **row})

            r = self._conn.execute(
                "MATCH (m:Method) WHERE lower(m.name) CONTAINS $q "
                "RETURN m.fqn AS fqn, m.file_id AS file_id LIMIT 50",
                {"q": lower_q},
            )
            for row in self._rows(r, ["fqn", "file_id"]):
                results.append({"type": "method", **row})
        except Exception as exc:
            log.error("search(%r): %s", q, exc)
        return results

    def file_path(self, file_id: str) -> str:
        if self._conn is None or not file_id:
            return ""
        try:
            r = self._conn.execute(
                "MATCH (f:File) WHERE f.id = $id RETURN f.path", {"id": file_id}
            )
            if r.has_next():
                return r.get_next()[0] or ""
        except Exception as exc:
            log.error("file_path(%r): %s", file_id, exc)
        return ""

    def symbol_detail(self, fqn: str) -> Optional[dict]:
        """Returns dict with keys: fqn, type, file_id, file_path, line_start."""
        if self._conn is None:
            return None
        try:
            r = self._conn.execute(
                "MATCH (m:Method) WHERE m.fqn = $fqn "
                "RETURN m.fqn, m.file_id, m.line_start",
                {"fqn": fqn},
            )
            if r.has_next():
                row = r.get_next()
                fp = self.file_path(row[1] or "")
                return {"fqn": row[0], "type": "method", "file_id": row[1], "file_path": fp, "line_start": row[2]}

            r = self._conn.execute(
                "MATCH (c:Class) WHERE c.fqn = $fqn "
                "RETURN c.fqn, c.file_id",
                {"fqn": fqn},
            )
            if r.has_next():
                row = r.get_next()
                fp = self.file_path(row[1] or "")
                return {"fqn": row[0], "type": "class", "file_id": row[1], "file_path": fp, "line_start": 0}
        except Exception as exc:
            log.error("symbol_detail(%r): %s", fqn, exc)
        return None

    def callers(self, fqn: str) -> list[dict]:
        if self._conn is None:
            return []
        try:
            r = self._conn.execute(
                "MATCH (a:Method)-[:CALLS]->(b:Method) WHERE b.fqn = $fqn "
                "RETURN a.fqn, a.file_id, a.line_start",
                {"fqn": fqn},
            )
            return self._rows(r, ["fqn", "file_id", "line_start"])
        except Exception as exc:
            log.error("callers(%r): %s", fqn, exc)
            return []

    def callees(self, fqn: str) -> list[dict]:
        if self._conn is None:
            return []
        try:
            r = self._conn.execute(
                "MATCH (a:Method)-[:CALLS]->(b:Method) WHERE a.fqn = $fqn "
                "RETURN b.fqn, b.file_id, b.line_start",
                {"fqn": fqn},
            )
            return self._rows(r, ["fqn", "file_id", "line_start"])
        except Exception as exc:
            log.error("callees(%r): %s", fqn, exc)
            return []

    def blast_radius(self, fqn: str, max_depth: int = 3) -> list[dict]:
        if self._conn is None:
            return []
        max_depth = min(max_depth, 10)
        visited: dict[str, int] = {}
        queue: deque = deque([(fqn, 0)])
        try:
            while queue:
                current, depth = queue.popleft()
                if depth >= max_depth:
                    continue
                r = self._conn.execute(
                    "MATCH (a:Method)-[:CALLS]->(b:Method) WHERE b.fqn = $fqn RETURN a.fqn",
                    {"fqn": current},
                )
                while r.has_next():
                    caller = r.get_next()[0]
                    if caller not in visited:
                        visited[caller] = depth + 1
                        queue.append((caller, depth + 1))
        except Exception as exc:
            log.error("blast_radius(%r): %s", fqn, exc)
        return [{"fqn": f, "depth": d} for f, d in sorted(visited.items(), key=lambda kv: kv[1])]

    def endpoints(self) -> list[dict]:
        if self._conn is None:
            return []
        try:
            r = self._conn.execute(
                "MATCH (r:Repo)-[:EXPOSES]->(e:Endpoint) "
                "MATCH (m:Method) WHERE m.id = e.handler_method_id "
                "RETURN e.http_method, e.path, m.fqn, r.name "
                "ORDER BY r.name, e.http_method, e.path",
            )
            return self._rows(r, ["http_method", "path", "handler_fqn", "repo_name"])
        except Exception as exc:
            log.error("endpoints(): %s", exc)
            return []

    def index_repo(self, repo_path: str, repo_name: str, db_path: str) -> dict:
        try:
            from indra.indexer import index_repo  # noqa: PLC0415
            summary = index_repo(repo_path, repo_name, db_path)
            # Reopen connection to pick up fresh data
            import kuzu  # noqa: PLC0415
            db = kuzu.Database(db_path)
            self._conn = kuzu.Connection(db)
            return summary
        except Exception as exc:
            log.error("index_repo(%r, %r): %s", repo_path, repo_name, exc)
            return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Page renderers
# ---------------------------------------------------------------------------

def _page_home(db: _DB, query: str = "") -> str:
    results = db.search(query) if query.strip() else []

    cards_html = ""
    if query.strip() and not results:
        cards_html = '<p class="empty">No results found for &ldquo;' + _esc(query) + '&rdquo;.</p>'
    elif results:
        for r in results:
            badge = _badge(r["type"])
            link = _fqn_link(r["fqn"])
            file_path = db.file_path(r.get("file_id", ""))
            sub = f'<span class="sub">{_esc(file_path)}</span>' if file_path else ""
            cards_html += f'<div class="card">{badge} {link} {sub}</div>\n'

    count_note = f' <span style="color:#475569;font-size:0.85rem;">({len(results)} results)</span>' if results else ""

    return _html_page("Search", f"""
<h1>Search Symbols{count_note}</h1>
<form class="search-box" method="get" action="/">
  <input type="text" name="q" placeholder="Class or method name&hellip;" value="{_esc(query)}" autofocus>
  <button type="submit">Search</button>
</form>
{cards_html}
""")


def _page_symbol(db: _DB, fqn: str) -> str:
    detail = db.symbol_detail(fqn)
    if detail is None:
        body = f'<p class="empty">Symbol not found: <code>{_esc(fqn)}</code></p>'
        return _html_page("Not Found", f'<a href="/" class="back-link">&larr; Back to search</a>{body}')

    callers_list = db.callers(fqn) if detail["type"] == "method" else []
    callees_list = db.callees(fqn) if detail["type"] == "method" else []
    blast = db.blast_radius(fqn) if detail["type"] == "method" else []

    # Header
    line_info = f"  line {detail['line_start']}" if detail.get("line_start") else ""
    header = f"""
<a href="/" class="back-link">&larr; Back to search</a>
<div class="detail-header">
  <div class="type-label">{_esc(detail['type'].upper())}</div>
  <div class="fqn-big">{_esc(detail['fqn'])}</div>
  <div class="meta">{_esc(detail.get('file_path', '') or 'file unknown')}{_esc(line_info)}</div>
</div>"""

    def _rows_html(rows: list[dict]) -> str:
        if not rows:
            return '<p class="empty">None found.</p>'
        html = ""
        for row in rows:
            line_label = f":{row['line_start']}" if row.get("line_start") else ""
            fp = db.file_path(row.get("file_id", ""))
            sub = f'<span class="sub">{_esc(fp)}{_esc(line_label)}</span>' if fp else ""
            html += f'<div class="caller-row">{_fqn_link(row["fqn"])} {sub}</div>\n'
        return html

    # Blast radius grouped by depth
    blast_by_depth: dict[int, list[str]] = {}
    for item in blast:
        blast_by_depth.setdefault(item["depth"], []).append(item["fqn"])

    blast_html = ""
    if not blast:
        blast_html = '<p class="empty">No upstream callers found.</p>'
    else:
        for depth in sorted(blast_by_depth):
            label = f"Depth {depth}"
            rows_html = "".join(
                f'<div class="caller-row"><span class="depth-badge depth-{min(depth,3)}">{label}</span>'
                f'{_fqn_link(fqn_item)}</div>\n'
                for fqn_item in blast_by_depth[depth]
            )
            blast_html += rows_html

    sections = f"""
<div class="section">
  <div class="section-title">Callers <span class="count">{len(callers_list)}</span></div>
  {_rows_html(callers_list)}
</div>

<div class="section">
  <div class="section-title">Callees <span class="count">{len(callees_list)}</span></div>
  {_rows_html(callees_list)}
</div>

<div class="section">
  <div class="section-title">Blast Radius (up to depth&nbsp;3) <span class="count">{len(blast)}</span></div>
  <details{"" if len(blast) <= 10 else ""}>
    <summary>{"Show " + str(len(blast)) + " affected methods" if blast else "No affected methods"}</summary>
    {blast_html}
  </details>
</div>
"""
    return _html_page(fqn, header + sections)


def _page_endpoints(db: _DB) -> str:
    rows = db.endpoints()

    if not rows:
        table_html = '<p class="empty">No endpoints found. Index a repository first.</p>'
    else:
        method_badges = {
            "GET": "get", "POST": "post", "PUT": "put",
            "DELETE": "delete", "PATCH": "patch",
        }
        tbody = ""
        for r in rows:
            verb = (r.get("http_method") or "").upper()
            badge_cls = method_badges.get(verb, "get")
            badge_html = f'<span class="badge badge-{badge_cls}">{_esc(verb)}</span>'
            path_html = _esc(r.get("path") or "")
            handler_html = _fqn_link(r.get("handler_fqn") or "")
            repo_html = _esc(r.get("repo_name") or "")
            tbody += f"<tr><td>{badge_html}</td><td>{path_html}</td><td>{handler_html}</td><td>{repo_html}</td></tr>\n"

        table_html = f"""
<table>
  <thead><tr><th>Method</th><th>Path</th><th>Handler FQN</th><th>Repo</th></tr></thead>
  <tbody>{tbody}</tbody>
</table>"""

    return _html_page("Endpoints", f"""
<h1>HTTP Endpoints <span style="color:#475569;font-size:0.85rem;">({len(rows)} total)</span></h1>
<div class="section">{table_html}</div>
""")


def _page_index_form(message: str = "", is_error: bool = False) -> str:
    alert = ""
    if message:
        cls = "alert-error" if is_error else "alert-success"
        alert = f'<div class="{cls}">{_esc(message)}</div>'

    return _html_page("Index Repo", f"""
<h1>Index Repository</h1>
{alert}
<div class="section">
  <form method="post" action="/index">
    <div class="form-group">
      <label>Repository Path (absolute)</label>
      <input type="text" name="repo_path" placeholder="/home/user/myproject" required>
    </div>
    <div class="form-group">
      <label>Repository Name (logical identifier)</label>
      <input type="text" name="repo_name" placeholder="my-service" required>
    </div>
    <button type="submit" class="btn">Index</button>
  </form>
</div>
""")


def _page_index_result(db: _DB, repo_path: str, repo_name: str, db_path: str) -> str:
    summary = db.index_repo(repo_path, repo_name, db_path)
    if "error" in summary:
        return _page_index_form(message=f"Error: {summary['error']}", is_error=True)

    lines = [f"{k}: {v}" for k, v in summary.items()]
    msg = "Indexed successfully.\n" + "\n".join(lines)
    return _page_index_form(message=msg, is_error=False)


# ---------------------------------------------------------------------------
# Starlette application factory
# ---------------------------------------------------------------------------

def _make_app(db: _DB, db_path: str):
    """Return a Starlette ASGI app."""
    from starlette.applications import Starlette  # noqa: PLC0415
    from starlette.requests import Request  # noqa: PLC0415
    from starlette.responses import HTMLResponse, RedirectResponse  # noqa: PLC0415
    from starlette.routing import Route  # noqa: PLC0415

    async def home(request: Request) -> HTMLResponse:
        q = request.query_params.get("q", "")
        return HTMLResponse(_page_home(db, q))

    async def symbol(request: Request) -> HTMLResponse:
        fqn = request.query_params.get("fqn", "")
        if not fqn:
            return RedirectResponse("/")
        return HTMLResponse(_page_symbol(db, fqn))

    async def endpoints(request: Request) -> HTMLResponse:
        return HTMLResponse(_page_endpoints(db))

    async def index_get(request: Request) -> HTMLResponse:
        return HTMLResponse(_page_index_form())

    async def index_post(request: Request) -> HTMLResponse:
        form = await request.form()
        repo_path = (form.get("repo_path") or "").strip()
        repo_name = (form.get("repo_name") or "").strip()
        if not repo_path or not repo_name:
            return HTMLResponse(_page_index_form("Both fields are required.", is_error=True))
        return HTMLResponse(_page_index_result(db, repo_path, repo_name, db_path))

    routes = [
        Route("/", home),
        Route("/symbol", symbol),
        Route("/endpoints", endpoints),
        Route("/index", index_get, methods=["GET"]),
        Route("/index", index_post, methods=["POST"]),
    ]
    return Starlette(routes=routes)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_ui(port: int = 7700, db_path: str = "") -> None:
    """Start the Indra web UI on *port*, opening a browser automatically."""
    if not db_path:
        db_path = os.environ.get("INDRA_DB_PATH", str(Path.home() / ".indra" / "indra.db"))

    import uvicorn  # noqa: PLC0415

    db = _DB(db_path)
    app = _make_app(db, db_path)

    url = f"http://localhost:{port}"
    log.info("Starting Indra UI at %s (db=%s)", url, db_path)
    print(f"\n  Indra UI  →  {url}\n  Press Ctrl+C to stop.\n")

    # Open browser after a short delay to allow the server to start
    Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
