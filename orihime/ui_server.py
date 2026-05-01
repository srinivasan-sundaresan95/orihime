"""Orihime Web UI — served by Starlette + uvicorn.

Start with:
    python -m orihime ui [--port 7700] [--db ~/.orihime/orihime.db]

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
  <span class="brand">&#9672; Orihime</span>
  <a href="/">Search</a>
  <a href="/graph">Graph</a>
  <a href="/endpoints">Endpoints</a>
  <a href="/findings">Findings</a>
  <a href="/index">Index Repo</a>
</nav>"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)} — Orihime</title>
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

    def repos(self) -> list[dict]:
        if self._conn is None:
            return []
        try:
            r = self._conn.execute("MATCH (r:Repo) RETURN r.id, r.name")
            return self._rows(r, ["id", "name"])
        except Exception as exc:
            log.error("repos(): %s", exc)
            return []

    def branches(self, repo_name: str = "") -> list[dict]:
        if self._conn is None:
            return []
        try:
            if repo_name:
                r = self._conn.execute(
                    "MATCH (repo:Repo)-[:HAS_BRANCH]->(b:Branch) WHERE repo.name = $n RETURN b.name",
                    {"n": repo_name},
                )
            else:
                r = self._conn.execute("MATCH (b:Branch) RETURN DISTINCT b.name")
            rows = []
            while r.has_next():
                rows.append({"name": r.get_next()[0]})
            return rows
        except Exception as exc:
            log.error("branches(%r): %s", repo_name, exc)
            return []

    def graph_data(self, repo_name: str, branch: str = "") -> dict:
        """Return ALL nodes+edges for a repo in a single payload.

        Kinds returned:
          class     — concrete class
          interface — Java/Kotlin interface
          method    — method node (with parent_class_id for grouping)
          external  — unresolved external call stub (callee_name)
        Edges:
          class_call   — class-to-class aggregated CALLS (weight = call count)
          method_call  — method-to-method CALLS
          ext_call     — class-to-external via UNRESOLVED_CALL
        """
        if self._conn is None:
            return {"nodes": [], "edges": []}
        try:
            r = self._conn.execute(
                "MATCH (r:Repo) WHERE r.name = $name RETURN r.id", {"name": repo_name}
            )
            if not r.has_next():
                return {"nodes": [], "edges": []}
            repo_id = r.get_next()[0]
            return self._graph_all(repo_id, branch_filter=branch)
        except Exception as exc:
            log.error("graph_data(%r): %s", repo_name, exc)
            return {"nodes": [], "edges": []}

    def _graph_all(self, repo_id: str, branch_filter: str = "") -> dict:
        nodes: list[dict] = []
        edges: list[dict] = []

        # ── Classes & interfaces ──────────────────────────────────────────
        branch_clause = " AND f.branch_name = $branch" if branch_filter else ""
        qparams: dict = {"rid": repo_id}
        if branch_filter:
            qparams["branch"] = branch_filter
        r = self._conn.execute(
            f"MATCH (f:File)-[:CONTAINS_CLASS]->(c:Class) WHERE c.repo_id = $rid{branch_clause} "
            "RETURN c.id, c.name, c.fqn, c.is_interface, f.path",
            qparams,
        )
        class_rows = self._rows(r, ["id", "name", "fqn", "is_interface", "file_path"])
        class_ids = {row["id"] for row in class_rows}

        def _is_test_path(path: str) -> bool:
            p = (path or "").replace("\\", "/").lower()
            # Match only actual test *directories* — never class names
            return "/test/" in p or "/tests/" in p or "/androidtest/" in p

        # ── Class-to-class call edges (aggregated) ────────────────────────
        r2 = self._conn.execute(
            "MATCH (ca:Class)-[:CONTAINS_METHOD]->(ma:Method)-[:CALLS]->(mb:Method)"
            "<-[:CONTAINS_METHOD]-(cb:Class) "
            "WHERE ca.repo_id = $rid AND cb.repo_id = $rid "
            "RETURN ca.id, cb.id, count(*) AS w",
            {"rid": repo_id},
        )
        cc_edges = self._rows(r2, ["from", "to", "weight"])
        class_degree: dict[str, int] = {}
        for e in cc_edges:
            if e["from"] != e["to"]:
                class_degree[e["from"]] = class_degree.get(e["from"], 0) + e["weight"]
                edges.append({"from": e["from"], "to": e["to"], "weight": e["weight"], "etype": "class_call"})

        # Class nodes built AFTER inheritance block so degree includes inheritance edges
        _class_rows_pending = class_rows

        # ── Methods ───────────────────────────────────────────────────────
        r3 = self._conn.execute(
            f"MATCH (f:File)-[:CONTAINS_CLASS]->(c:Class)-[:CONTAINS_METHOD]->(m:Method) "
            f"WHERE c.repo_id = $rid{branch_clause} "
            "RETURN m.id, m.name, m.fqn, c.id AS class_id, m.generated, f.path",
            qparams,
        )
        method_rows = self._rows(r3, ["id", "name", "fqn", "class_id", "generated", "file_path"])
        method_ids = {row["id"] for row in method_rows}
        method_degree: dict[str, int] = {}

        # ── Method-to-method call edges ───────────────────────────────────
        r4 = self._conn.execute(
            "MATCH (ma:Method)-[:CALLS]->(mb:Method) "
            "WHERE ma.repo_id = $rid AND mb.repo_id = $rid "
            "RETURN ma.id, mb.id",
            {"rid": repo_id},
        )
        seen_mm: set[tuple] = set()
        while r4.has_next():
            a, b = r4.get_next()
            if (a, b) not in seen_mm:
                seen_mm.add((a, b))
                method_degree[a] = method_degree.get(a, 0) + 1
                edges.append({"from": a, "to": b, "weight": 1, "etype": "method_call"})

        def _class_label(fqn: str) -> str:
            parts = fqn.rsplit(".", 2)
            return parts[-2] if len(parts) >= 2 else fqn

        for row in method_rows:
            nodes.append({
                "id": row["id"],
                "label": row["name"],
                "fqn": row["fqn"],
                "kind": "method",
                "degree": method_degree.get(row["id"], 0),
                "parent_id": row["class_id"],
                "group": _class_label(row["fqn"]),
                "generated": bool(row.get("generated", False)),
                "is_test": _is_test_path(row.get("file_path", "")),
            })

        # ── External dependency stubs (UNRESOLVED_CALL) ───────────────────
        ext_nodes: dict[str, dict] = {}
        seen_ext: set[tuple] = set()
        try:
            r5 = self._conn.execute(
                "MATCH (ca:Class)-[:CONTAINS_METHOD]->(ma:Method)-[:UNRESOLVED_CALL]->(rc:RestCall) "
                "WHERE ca.repo_id = $rid "
                "RETURN ca.id, rc.callee_name, count(*) AS w",
                {"rid": repo_id},
            )
            for row in self._rows(r5, ["class_id", "callee_name", "weight"]):
                name = row["callee_name"] or "unknown"
                ext_id = f"__ext__{name}"
                if ext_id not in ext_nodes:
                    ext_nodes[ext_id] = {
                        "id": ext_id, "label": name,
                        "fqn": f"[external] {name}", "kind": "external", "degree": 0,
                    }
                pair = (row["class_id"], ext_id)
                if pair not in seen_ext:
                    seen_ext.add(pair)
                    edges.append({"from": row["class_id"], "to": ext_id, "weight": row["weight"], "etype": "ext_call"})
        except Exception:
            pass  # old DB without callee_name — skip

        nodes.extend(ext_nodes.values())

        # Inheritance edges — also accumulate into class_degree so interfaces
        # with only IMPLEMENTS connections aren't treated as isolated by the layout
        try:
            r6 = self._conn.execute(
                "MATCH (child:Class)-[:EXTENDS]->(parent:Class) WHERE child.repo_id = $rid RETURN child.id, parent.id",
                {"rid": repo_id},
            )
            while r6.has_next():
                a, b = r6.get_next()
                class_degree[a] = class_degree.get(a, 0) + 1
                class_degree[b] = class_degree.get(b, 0) + 1
                edges.append({"from": a, "to": b, "weight": 1, "etype": "extends"})
            r7 = self._conn.execute(
                "MATCH (child:Class)-[:IMPLEMENTS]->(parent:Class) WHERE child.repo_id = $rid RETURN child.id, parent.id",
                {"rid": repo_id},
            )
            while r7.has_next():
                a, b = r7.get_next()
                class_degree[a] = class_degree.get(a, 0) + 1
                class_degree[b] = class_degree.get(b, 0) + 1
                edges.append({"from": a, "to": b, "weight": 1, "etype": "implements"})
        except Exception:
            pass  # old DB without inheritance tables — degrade gracefully

        # Build class nodes now — after inheritance degree is accumulated
        for row in _class_rows_pending:
            nodes.append({
                "id": row["id"],
                "label": row["name"],
                "fqn": row["fqn"],
                "kind": "interface" if row["is_interface"] else "class",
                "degree": class_degree.get(row["id"], 0),
                "is_test": _is_test_path(row.get("file_path", "")),
            })

        # Entity relation edges (Class → Class, via EntityRelation node — show as direct edge)
        try:
            r8 = self._conn.execute(
                "MATCH (c:Class)-[:HAS_RELATION]->(er:EntityRelation) WHERE er.repo_id = $rid "
                "MATCH (t:Class) WHERE t.fqn = er.target_class_fqn "
                "RETURN c.id, t.id, er.relation_type, er.fetch_type",
                {"rid": repo_id},
            )
            while r8.has_next():
                src_id, tgt_id, rel_type, fetch_type = r8.get_next()
                edges.append({"from": src_id, "to": tgt_id, "weight": 1,
                              "etype": "jpa_" + rel_type.lower(),
                              "fetch_type": fetch_type})
        except Exception:
            pass

        return {"nodes": nodes, "edges": edges}

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

    def findings(
        self,
        repo_name: str,
        finding_type: str = "all",
        min_severity: str = "low",
        reachable_only: bool = False,
    ) -> list[dict]:
        """Aggregate security and performance findings for a repo.

        finding_type: "all" | "security" | "perf"
        min_severity: "low" | "medium" | "high"
        reachable_only: if True, skip unreachable security sinks
        """
        if self._conn is None:
            return []
        try:
            # Resolve repo_id
            r = self._conn.execute(
                "MATCH (repo:Repo) WHERE repo.name = $name RETURN repo.id",
                {"name": repo_name},
            )
            if not r.has_next():
                return []
            repo_id = r.get_next()[0]
        except Exception as exc:
            log.error("findings() repo_id lookup: %s", exc)
            return []

        results: list[dict] = []

        # ── Security findings ─────────────────────────────────────────────
        if finding_type in ("all", "security"):
            try:
                from orihime.security_config import get_security_config  # noqa: PLC0415
                cfg = get_security_config()

                # OWASP severity mapping
                _OWASP_MAP: dict[str, str] = {
                    "execute": "A03:2021",
                    "executeQuery": "A03:2021",
                    "executeUpdate": "A03:2021",
                    "createQuery": "A03:2021",
                    "query": "A03:2021",
                    "getForEntity": "A10:2021",
                    "postForEntity": "A10:2021",
                    "exchange": "A10:2021",
                    "exec": "A03:2021",
                    "start": "A03:2021",
                    "readAllBytes": "A01:2021",
                    "newBufferedReader": "A01:2021",
                }
                _SEVERITY_MAP: dict[str, str] = {
                    "A01:2021": "HIGH",
                    "A03:2021": "HIGH",
                    "A10:2021": "MEDIUM",
                }

                # Optional reachability filter
                reachable_ids: set[str] | None = None
                if reachable_only:
                    try:
                        from collections import deque as _deque  # noqa: PLC0415
                        seed_ids: set[str] = set()
                        r_ep = self._conn.execute(
                            "MATCH (repo2:Repo)-[:EXPOSES]->(ep:Endpoint) WHERE repo2.id = $rid "
                            "RETURN ep.handler_method_id",
                            {"rid": repo_id},
                        )
                        while r_ep.has_next():
                            mid = r_ep.get_next()[0]
                            if mid:
                                seed_ids.add(mid)
                        r_entry = self._conn.execute(
                            "MATCH (m:Method) WHERE m.repo_id = $rid AND m.is_entry_point = true RETURN m.id",
                            {"rid": repo_id},
                        )
                        while r_entry.has_next():
                            seed_ids.add(r_entry.get_next()[0])
                        if seed_ids:
                            r_adj = self._conn.execute(
                                "MATCH (a:Method)-[:CALLS]->(b:Method) WHERE a.repo_id = $rid RETURN a.id, b.id",
                                {"rid": repo_id},
                            )
                            adj_fwd: dict[str, list[str]] = {}
                            while r_adj.has_next():
                                src, dst = r_adj.get_next()
                                adj_fwd.setdefault(src, []).append(dst)
                            reachable_ids = set(seed_ids)
                            q: deque = _deque(seed_ids)  # type: ignore[assignment]
                            while q:
                                cur = q.popleft()
                                for nxt in adj_fwd.get(cur, []):
                                    if nxt not in reachable_ids:
                                        reachable_ids.add(nxt)
                                        q.append(nxt)
                        else:
                            reachable_ids = set()
                    except Exception as exc2:
                        log.error("findings() reachability: %s", exc2)
                        reachable_ids = None  # skip filter on error

                # UNRESOLVED_CALL sinks
                r_rc = self._conn.execute(
                    "MATCH (m:Method)-[:UNRESOLVED_CALL]->(rc:RestCall) "
                    "WHERE m.repo_id = $rid "
                    "MATCH (f:File) WHERE f.id = m.file_id "
                    "RETURN m.fqn, m.id, rc.callee_name, f.path, m.line_start",
                    {"rid": repo_id},
                )
                while r_rc.has_next():
                    caller_fqn, caller_id, callee_name, file_path, line_start = r_rc.get_next()
                    if not callee_name or not cfg.is_sink_method(callee_name):
                        continue
                    if reachable_ids is not None and caller_id not in reachable_ids:
                        continue
                    short = callee_name.split(".")[-1]
                    owasp = _OWASP_MAP.get(short, "A00:2021")
                    sev = _SEVERITY_MAP.get(owasp, "MEDIUM")
                    results.append({
                        "type": "taint_sink",
                        "severity": sev,
                        "category": owasp,
                        "method_fqn": caller_fqn,
                        "file_path": file_path or "",
                        "line_start": line_start,
                        "detail": f"Call to sink: {callee_name}",
                        "owasp": owasp,
                        "complexity_hint": None,
                        "p99_ms": None,
                    })

                # CALLS edges to known sinks
                r_c = self._conn.execute(
                    "MATCH (m:Method)-[:CALLS]->(s:Method) "
                    "WHERE m.repo_id = $rid "
                    "MATCH (f:File) WHERE f.id = m.file_id "
                    "RETURN m.fqn, m.id, s.fqn, s.name, f.path, m.line_start",
                    {"rid": repo_id},
                )
                while r_c.has_next():
                    caller_fqn, caller_id, callee_fqn, callee_name, file_path, line_start = r_c.get_next()
                    if not (cfg.is_sink_method(callee_fqn) or cfg.is_sink_method(callee_name)):
                        continue
                    if reachable_ids is not None and caller_id not in reachable_ids:
                        continue
                    short = (callee_fqn or callee_name or "").split(".")[-1]
                    owasp = _OWASP_MAP.get(short, "A00:2021")
                    sev = _SEVERITY_MAP.get(owasp, "MEDIUM")
                    results.append({
                        "type": "taint_sink",
                        "severity": sev,
                        "category": owasp,
                        "method_fqn": caller_fqn,
                        "file_path": file_path or "",
                        "line_start": line_start,
                        "detail": f"Call to sink: {callee_fqn or callee_name}",
                        "owasp": owasp,
                        "complexity_hint": None,
                        "p99_ms": None,
                    })
            except Exception as exc:
                log.error("findings() security: %s", exc)

        # ── Complexity/perf findings ──────────────────────────────────────
        if finding_type in ("all", "perf"):
            try:
                r_m = self._conn.execute(
                    "MATCH (m:Method) WHERE m.repo_id = $rid AND m.complexity_hint <> '' "
                    "MATCH (f:File) WHERE f.id = m.file_id "
                    "RETURN m.fqn, f.path, m.line_start, m.complexity_hint",
                    {"rid": repo_id},
                )
                while r_m.has_next():
                    fqn, file_path, line_start, hint = r_m.get_next()
                    tags = {t.strip() for t in (hint or "").split(",") if t.strip()}
                    if "O(n2)" in hint or "n+1-risk" in hint:
                        sev = "HIGH"
                    elif tags == {"recursive"}:
                        sev = "LOW"
                    else:
                        sev = "MEDIUM"
                    results.append({
                        "type": "complexity_hint",
                        "severity": sev,
                        "category": "Performance",
                        "method_fqn": fqn,
                        "file_path": file_path or "",
                        "line_start": line_start,
                        "detail": hint,
                        "owasp": None,
                        "complexity_hint": hint,
                        "p99_ms": None,
                    })
            except Exception as exc:
                log.error("findings() perf: %s", exc)

        # Severity filter
        sev_order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        min_sev_val = {"high": 3, "medium": 2, "low": 1}.get(min_severity.lower(), 1)
        results = [r for r in results if sev_order.get(r["severity"], 0) >= min_sev_val]

        # Deduplicate by (type, method_fqn, detail)
        seen_keys: set[tuple] = set()
        deduped: list[dict] = []
        for r in results:
            key = (r["type"], r["method_fqn"], r["detail"])
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(r)

        deduped.sort(key=lambda x: (-sev_order.get(x["severity"], 0), x["method_fqn"]))
        return deduped

    def index_repo(self, repo_path: str, repo_name: str, db_path: str) -> dict:
        try:
            from orihime.indexer import index_repo  # noqa: PLC0415
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


def _page_findings(db: _DB, repo_name: str = "") -> str:
    repos = db.repos()
    repo_options = "".join(
        f'<option value="{_esc(r["name"])}" {"selected" if r["name"] == repo_name else ""}>'
        f'{_esc(r["name"])}</option>'
        for r in repos
    )
    if not repo_name and repos:
        repo_name = repos[0]["name"]

    return _html_page("Findings", f"""
<h1>Security &amp; Performance Findings</h1>

<!-- Filter toolbar -->
<div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:16px;">
  <select id="findingsRepo" style="background:#1e2130;border:1px solid #2d3148;border-radius:8px;padding:8px 14px;color:#e2e8f0;font-size:0.9rem;outline:none;">
    <option value="">-- select repo --</option>
    {repo_options}
  </select>

  <select id="findingsType" style="background:#1e2130;border:1px solid #2d3148;border-radius:8px;padding:8px 14px;color:#e2e8f0;font-size:0.9rem;outline:none;">
    <option value="all">All types</option>
    <option value="security">Security only</option>
    <option value="perf">Performance only</option>
  </select>

  <select id="findingsSev" style="background:#1e2130;border:1px solid #2d3148;border-radius:8px;padding:8px 14px;color:#e2e8f0;font-size:0.9rem;outline:none;">
    <option value="low">All severities</option>
    <option value="medium">Medium+</option>
    <option value="high">High only</option>
  </select>

  <label style="display:flex;align-items:center;gap:6px;color:#94a3b8;font-size:0.88rem;cursor:pointer;">
    <input type="checkbox" id="reachableOnly" style="cursor:pointer;accent-color:#6366f1;">
    Reachable only
  </label>

  <button id="refreshBtn" onclick="loadFindings()"
    style="background:#6366f1;color:#fff;border:none;border-radius:8px;padding:8px 18px;font-size:0.88rem;cursor:pointer;font-weight:600;">
    Refresh
  </button>

  <span id="findingsStatus" style="color:#64748b;font-size:0.8rem;margin-left:4px;"></span>

  <a id="exportLink" href="#" onclick="exportFindings(); return false;"
    style="margin-left:auto;font-size:0.82rem;color:#60a5fa;text-decoration:none;">
    &#8659; Export JSON
  </a>
</div>

<!-- Findings table -->
<div class="section" style="padding:0;overflow-x:auto;">
  <table id="findingsTable">
    <thead>
      <tr>
        <th>Type</th>
        <th>Severity</th>
        <th>Category</th>
        <th>Method</th>
        <th>File:Line</th>
        <th>Detail</th>
      </tr>
    </thead>
    <tbody id="findingsTbody">
      <tr><td colspan="6" class="empty" style="text-align:center;padding:24px;">Select a repository and click Refresh.</td></tr>
    </tbody>
  </table>
</div>

<script>
function _esc(s) {{
  return String(s)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}}

const SEV_BG = {{ HIGH: '#2d0a0a', MEDIUM: '#2d1a00', LOW: '#1a1e0a' }};
const SEV_COLOR = {{ HIGH: '#fca5a5', MEDIUM: '#fdba74', LOW: '#d9f99d' }};
const TYPE_LABEL = {{
  taint_sink:       'Taint Sink',
  cross_service_taint: 'X-Service Taint',
  second_order:     '2nd-Order Inj.',
  complexity_hint:  'Complexity',
}};

function loadFindings() {{
  const repo   = document.getElementById('findingsRepo').value;
  const type   = document.getElementById('findingsType').value;
  const sev    = document.getElementById('findingsSev').value;
  const reach  = document.getElementById('reachableOnly').checked;
  const status = document.getElementById('findingsStatus');
  const tbody  = document.getElementById('findingsTbody');

  if (!repo) {{
    tbody.innerHTML = '<tr><td colspan="6" class="empty" style="text-align:center;padding:24px;">Select a repository first.</td></tr>';
    status.textContent = '';
    return;
  }}

  status.textContent = 'Loading…';
  tbody.innerHTML    = '<tr><td colspan="6" class="empty" style="text-align:center;padding:24px;">Loading…</td></tr>';

  const url = `/api/findings?repo=${{encodeURIComponent(repo)}}&type=${{encodeURIComponent(type)}}&min_severity=${{encodeURIComponent(sev)}}&reachable_only=${{reach}}`;
  fetch(url)
    .then(r => r.json())
    .then(data => {{
      status.textContent = `${{data.length}} finding${{data.length !== 1 ? 's' : ''}}`;
      if (!data.length) {{
        tbody.innerHTML = '<tr><td colspan="6" class="empty" style="text-align:center;padding:24px;">No findings for the selected filters.</td></tr>';
        return;
      }}
      tbody.innerHTML = data.map(f => {{
        const bg  = SEV_BG[f.severity]  || '#1a1d27';
        const fc  = SEV_COLOR[f.severity] || '#e2e8f0';
        const typeLabel = TYPE_LABEL[f.type] || _esc(f.type);
        const fileLine = f.line_start ? `${{_esc(f.file_path)}}:${{f.line_start}}` : _esc(f.file_path);
        return `<tr style="background:${{bg}};">
          <td style="white-space:nowrap;">${{typeLabel}}</td>
          <td><span style="font-weight:700;color:${{fc}};">${{_esc(f.severity)}}</span></td>
          <td style="white-space:nowrap;">${{_esc(f.category)}}</td>
          <td style="font-family:monospace;word-break:break-all;">${{_esc(f.method_fqn)}}</td>
          <td style="font-family:monospace;font-size:0.78rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:260px;" title="${{_esc(fileLine)}}">${{_esc(fileLine)}}</td>
          <td style="font-size:0.82rem;">${{_esc(f.detail)}}</td>
        </tr>`;
      }}).join('');
    }})
    .catch(err => {{
      status.textContent = 'Error';
      tbody.innerHTML = `<tr><td colspan="6" class="empty" style="text-align:center;padding:24px;color:#fca5a5;">Error: ${{err}}</td></tr>`;
    }});
}}

function exportFindings() {{
  const repo  = document.getElementById('findingsRepo').value;
  const type  = document.getElementById('findingsType').value;
  const sev   = document.getElementById('findingsSev').value;
  const reach = document.getElementById('reachableOnly').checked;
  if (!repo) {{ alert('Select a repository first.'); return; }}
  const url = `/api/findings/export?repo=${{encodeURIComponent(repo)}}&type=${{encodeURIComponent(type)}}&min_severity=${{encodeURIComponent(sev)}}&reachable_only=${{reach}}`;
  window.location.href = url;
}}

// Auto-load if a repo is pre-selected
window.addEventListener('DOMContentLoaded', () => {{
  const sel = document.getElementById('findingsRepo');
  if (sel.value) loadFindings();
}});
document.getElementById('findingsRepo').onchange = loadFindings;
</script>
""")


def _page_graph(db: _DB, repo_name: str = "") -> str:
    repos = db.repos()
    repo_options = "".join(
        f'<option value="{_esc(r["name"])}" {"selected" if r["name"] == repo_name else ""}>'
        f'{_esc(r["name"])}</option>'
        for r in repos
    )
    if not repo_name and repos:
        repo_name = repos[0]["name"]
    branches = db.branches(repo_name)
    branch_options = '<option value="">All branches</option>' + "".join(
        f'<option value="{_esc(b["name"])}">{_esc(b["name"])}</option>'
        for b in branches
    )

    return _html_page("Call Graph", f"""
<h1>Call Graph</h1>

<!-- Toolbar -->
<div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:14px;">
  <select id="repoSelect" style="background:#1e2130;border:1px solid #2d3148;border-radius:8px;padding:8px 14px;color:#e2e8f0;font-size:0.9rem;outline:none;">
    {repo_options}
  </select>
  <select id="branchSelect" style="background:#1e2130;border:1px solid #2d3148;border-radius:8px;padding:8px 14px;color:#e2e8f0;font-size:0.9rem;outline:none;">
    {branch_options}
  </select>

  <!-- View presets: control which kinds are visible, not which data is fetched -->
  <div style="display:flex;gap:0;border:1px solid #2d3148;border-radius:8px;overflow:hidden;">
    <button class="view-btn active" data-view="all"        style="padding:7px 14px;font-size:0.82rem;background:#2d3148;color:#a5b4fc;border:none;cursor:pointer;font-weight:600;">All</button>
    <button class="view-btn"        data-view="classes"    style="padding:7px 14px;font-size:0.82rem;background:#1e2130;color:#94a3b8;border:none;cursor:pointer;">Classes</button>
    <button class="view-btn"        data-view="methods"    style="padding:7px 14px;font-size:0.82rem;background:#1e2130;color:#94a3b8;border:none;cursor:pointer;">Methods</button>
    <button class="view-btn"        data-view="deps"       style="padding:7px 14px;font-size:0.82rem;background:#1e2130;color:#94a3b8;border:none;cursor:pointer;">Dependencies</button>
    <button class="view-btn"        data-view="entities"   style="padding:7px 14px;font-size:0.82rem;background:#1e2130;color:#94a3b8;border:none;cursor:pointer;">Entities</button>
  </div>

  <!-- Per-kind filter toggles — auto-populated after load, start all ON -->
  <div id="filterToggles" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;"></div>

  <button id="fitBtn" style="padding:7px 12px;font-size:0.82rem;background:#1e2130;border:1px solid #2d3148;border-radius:8px;color:#94a3b8;cursor:pointer;" title="Fit graph to window">&#x26F6;</button>
  <button id="inheritanceToggle" data-active="1" style="padding:7px 12px;font-size:0.82rem;background:#1e2130;border:1px solid #7c3aed;border-radius:8px;color:#a78bfa;cursor:pointer;" title="Toggle inheritance edges">Inheritance</button>
  <span id="graphStatus" style="color:#64748b;font-size:0.8rem;margin-left:4px;"></span>
</div>

<div style="display:flex;gap:0;height:700px;">
  <div id="graph-container" style="flex:1;background:#0c0e14;border:1px solid #2d3148;border-radius:10px 0 0 10px;overflow:hidden;position:relative;">
    <div id="graph-loading" style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);color:#475569;font-size:0.9rem;">Loading…</div>
  </div>
  <div id="legend-panel" style="width:210px;background:#111318;border:1px solid #2d3148;border-left:none;border-radius:0 10px 10px 0;padding:16px 12px;overflow-y:auto;display:none;">
    <div style="font-size:0.75rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.07em;margin-bottom:10px;">Node Types</div>
    <div style="font-size:0.72rem;color:#475569;line-height:2;">
      <div><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#4f46e5;margin-right:6px;vertical-align:middle;border:1px solid #6366f1;"></span>Class</div>
      <div><span style="display:inline-block;width:10px;height:10px;background:#1e3a5f;margin-right:6px;vertical-align:middle;border:1px solid #38bdf8;transform:rotate(45deg);"></span>Interface</div>
      <div><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#92400e;margin-right:6px;vertical-align:middle;border:1px solid #f59e0b;"></span>Method</div>
      <div><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#92400e;margin-right:6px;vertical-align:middle;border:1px solid #f59e0b;opacity:0.4;"></span>Generated (Lombok)</div>
      <div><span style="display:inline-block;width:10px;height:10px;background:#3b0a0a;margin-right:6px;vertical-align:middle;border:1px solid #ef4444;"></span>External dep</div>
    </div>
    <div style="margin-top:14px;font-size:0.75rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px;">Packages</div>
    <div id="legend-items"></div>
    <div style="margin-top:14px;font-size:0.72rem;color:#475569;line-height:1.7;">
      Edge thickness = call count<br>
      Node size = out-degree<br>
      Click node → symbol detail
    </div>
    <div style="margin-top:10px;font-size:0.72rem;color:#475569;">
      <div style="margin-bottom:4px;"><span style="display:inline-block;width:20px;border-top:2px dashed #7c3aed;margin-right:6px;vertical-align:middle;"></span>Extends</div>
      <div><span style="display:inline-block;width:20px;border-top:2px dashed #0369a1;margin-right:6px;vertical-align:middle;"></span>Implements</div>
    </div>
  </div>
</div>

<div id="tooltip" style="position:fixed;background:#1a1d27;border:1px solid #2d3148;border-radius:6px;padding:8px 12px;font-size:0.78rem;font-family:monospace;color:#e2e8f0;pointer-events:none;display:none;z-index:1000;max-width:480px;word-break:break-all;box-shadow:0 4px 16px rgba(0,0,0,.5);"></div>

<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<script>
const container   = document.getElementById('graph-container');
const loadingDiv  = document.getElementById('graph-loading');
const tooltip     = document.getElementById('tooltip');
const status      = document.getElementById('graphStatus');
const legendPanel = document.getElementById('legend-panel');
const legendItems = document.getElementById('legend-items');

let network   = null;
let visNodes  = null;
let visEdges  = null;
let rawData   = null;           // full payload from server
let hiddenKinds = new Set();    // kinds toggled OFF by the user
let hiddenEdgeTypes = new Set();
let showGenerated = false;
let showTests = false;
let showOrphans = false;

// ── Colour palette ────────────────────────────────────────────────────────
const KIND_COLOR = {{
  class:     {{ bg: '#4f46e5', border: '#6366f1' }},
  interface: {{ bg: '#1e3a5f', border: '#38bdf8' }},
  method:    {{ bg: '#92400e', border: '#f59e0b' }},
  external:  {{ bg: '#3b0a0a', border: '#ef4444' }},
}};
const palette = ['#6366f1','#22d3ee','#f59e0b','#34d399','#f87171','#a78bfa',
                 '#fb923c','#38bdf8','#4ade80','#e879f9','#facc15','#60a5fa',
                 '#f472b6','#2dd4bf','#fb7185','#84cc16','#e11d48','#0ea5e9'];
const groupColors = {{}};
let colorIdx = 0;
function groupColor(g) {{
  if (!groupColors[g]) groupColors[g] = palette[colorIdx++ % palette.length];
  return groupColors[g];
}}

// ── View presets ──────────────────────────────────────────────────────────
const PRESET_KINDS = {{
  all:      null,                              // null = show everything
  classes:  new Set(['class','interface','external']),
  methods:  new Set(['method']),
  deps:     new Set(['class','interface','external']),
  entities: new Set(['class', 'interface']),
}};

document.querySelectorAll('.view-btn').forEach(btn => {{
  btn.onclick = () => {{
    document.querySelectorAll('.view-btn').forEach(b => {{
      b.style.background = '#1e2130'; b.style.color = '#94a3b8'; b.classList.remove('active');
    }});
    btn.style.background = '#2d3148'; btn.style.color = '#a5b4fc'; btn.classList.add('active');
    applyPreset(btn.dataset.view);
  }};
}});

function applyPreset(preset) {{
  if (!rawData) return;
  const allowed = PRESET_KINDS[preset];   // null = all, Set = whitelist
  hiddenKinds.clear();
  if (allowed) {{
    rawData.nodes.forEach(n => {{ if (!allowed.has(n.kind)) hiddenKinds.add(n.kind); }});
  }}
  // Sync filter toggle buttons
  document.querySelectorAll('[data-filter-kind]').forEach(btn => {{
    const k = btn.dataset.filterKind;
    const on = !hiddenKinds.has(k);
    btn.style.opacity  = on ? '1' : '0.35';
    btn.dataset.active = on ? '1' : '0';
  }});
  applyVisibility();
}}

// ── Visibility: hide/show nodes and edges that touch only hidden nodes ────
function applyVisibility() {{
  if (!visNodes || !visEdges) return;
  const nodeUpdates = visNodes.getIds().map(id => {{
    const n = visNodes.get(id);
    const hiddenByKind      = hiddenKinds.has(n.kind);
    const hiddenByGenerated = (n.generated === true && !showGenerated);
    const hiddenByTest      = (n.is_test === true && !showTests);
    const hiddenByOrphan    = (n.degree === 0 && !showOrphans && n.kind !== 'external');
    return {{ id, hidden: hiddenByKind || hiddenByGenerated || hiddenByTest || hiddenByOrphan }};
  }});
  visNodes.update(nodeUpdates);

  // Hide edges whose from or to node is hidden
  const hiddenNodeIds = new Set(
    visNodes.getIds().filter(id => visNodes.get(id).hidden)
  );
  const edgeUpdates = visEdges.getIds().map(id => {{
    const e = visEdges.get(id);
    return {{ id, hidden: hiddenNodeIds.has(e.from) || hiddenNodeIds.has(e.to) || hiddenEdgeTypes.has(e.etype) }};
  }});
  visEdges.update(edgeUpdates);

  // Update status count
  const visibleNodes = visNodes.getIds().filter(id => !visNodes.get(id).hidden).length;
  const visibleEdges = visEdges.getIds().filter(id => !visEdges.get(id).hidden).length;
  status.textContent = `${{visibleNodes}} nodes · ${{visibleEdges}} edges (of ${{rawData.nodes.length}} / ${{rawData.edges.length}})`;
}}

// ── Per-kind filter toggle buttons ────────────────────────────────────────
function buildFilterToggles(kinds) {{
  const panel = document.getElementById('filterToggles');
  panel.innerHTML = '';
  kinds.forEach(kind => {{
    const btn = document.createElement('button');
    btn.textContent = kind.charAt(0).toUpperCase() + kind.slice(1) + 's';
    const col = (KIND_COLOR[kind] || KIND_COLOR.class).border;
    btn.style.cssText = `padding:4px 10px;font-size:0.78rem;border-radius:6px;border:1px solid ${{col}};`
      + `background:transparent;color:${{col}};cursor:pointer;font-weight:600;opacity:1;`;
    btn.dataset.filterKind = kind;
    btn.dataset.active = '1';
    btn.onclick = () => {{
      const on = btn.dataset.active === '1';
      if (on) {{
        hiddenKinds.add(kind);
        btn.style.opacity  = '0.35';
        btn.dataset.active = '0';
      }} else {{
        hiddenKinds.delete(kind);
        btn.style.opacity  = '1';
        btn.dataset.active = '1';
      }}
      // Deactivate view preset (custom state)
      document.querySelectorAll('.view-btn').forEach(b => {{
        b.style.background = '#1e2130'; b.style.color = '#94a3b8'; b.classList.remove('active');
      }});
      applyVisibility();
    }};
    panel.appendChild(btn);
  }});
  const hasMethodNodes = rawData && rawData.nodes.some(n => n.kind === 'method');
  if (hasMethodNodes) {{
    const genBtn = document.createElement('button');
    genBtn.id             = 'generatedToggle';
    genBtn.textContent    = 'Generated';
    genBtn.dataset.active = '0';
    genBtn.style.cssText  = 'padding:4px 10px;font-size:0.78rem;border-radius:6px;'
      + 'border:1px dashed #475569;background:transparent;color:#475569;cursor:pointer;font-weight:600;opacity:0.35;';
    genBtn.onclick = () => {{
      showGenerated = genBtn.dataset.active !== '1';
      genBtn.dataset.active  = showGenerated ? '1' : '0';
      genBtn.style.opacity   = showGenerated ? '1' : '0.35';
      genBtn.style.borderColor = showGenerated ? '#f59e0b' : '#475569';
      genBtn.style.color       = showGenerated ? '#f59e0b' : '#475569';
      applyVisibility();
    }};
    panel.appendChild(genBtn);
  }}

  // Tests toggle (hidden by default)
  const hasTestNodes = rawData && rawData.nodes.some(n => n.is_test === true);
  if (hasTestNodes) {{
    const testBtn = document.createElement('button');
    testBtn.id             = 'testsToggle';
    testBtn.textContent    = 'Tests';
    testBtn.dataset.active = '0';
    testBtn.style.cssText  = 'padding:4px 10px;font-size:0.78rem;border-radius:6px;'
      + 'border:1px dashed #475569;background:transparent;color:#475569;cursor:pointer;font-weight:600;opacity:0.35;';
    testBtn.onclick = () => {{
      showTests = testBtn.dataset.active !== '1';
      testBtn.dataset.active   = showTests ? '1' : '0';
      testBtn.style.opacity    = showTests ? '1' : '0.35';
      testBtn.style.borderColor = showTests ? '#22d3ee' : '#475569';
      testBtn.style.color       = showTests ? '#22d3ee' : '#475569';
      applyVisibility();
    }};
    panel.appendChild(testBtn);
  }}

  // Orphans toggle (hidden by default)
  const hasOrphans = rawData && rawData.nodes.some(n => n.degree === 0 && n.kind !== 'external');
  if (hasOrphans) {{
    const orphanBtn = document.createElement('button');
    orphanBtn.id             = 'orphansToggle';
    orphanBtn.textContent    = 'Orphans';
    orphanBtn.dataset.active = '0';
    orphanBtn.style.cssText  = 'padding:4px 10px;font-size:0.78rem;border-radius:6px;'
      + 'border:1px dashed #475569;background:transparent;color:#475569;cursor:pointer;font-weight:600;opacity:0.35;';
    orphanBtn.onclick = () => {{
      showOrphans = orphanBtn.dataset.active !== '1';
      orphanBtn.dataset.active    = showOrphans ? '1' : '0';
      orphanBtn.style.opacity     = showOrphans ? '1' : '0.35';
      orphanBtn.style.borderColor = showOrphans ? '#94a3b8' : '#475569';
      orphanBtn.style.color       = showOrphans ? '#94a3b8' : '#475569';
      applyVisibility();
    }};
    panel.appendChild(orphanBtn);
  }}

  // Entity Relations toggle
  const hasJpaEdges = rawData && rawData.edges.some(e => e.etype && e.etype.startsWith('jpa_'));
  if (hasJpaEdges) {{
    const jpaBtn = document.createElement('button');
    jpaBtn.id             = 'entityRelationsToggle';
    jpaBtn.textContent    = 'Entity Relations';
    jpaBtn.dataset.active = '1';
    jpaBtn.style.cssText  = 'padding:4px 10px;font-size:0.78rem;border-radius:6px;'
      + 'border:1px solid #b45309;background:transparent;color:#b45309;cursor:pointer;font-weight:600;opacity:1;';
    jpaBtn.onclick = () => {{
      const on = jpaBtn.dataset.active === '1';
      const jpaEtypes = ['jpa_onetomany','jpa_manytoone','jpa_onetoone','jpa_manytomany'];
      if (on) {{
        jpaEtypes.forEach(t => hiddenEdgeTypes.add(t));
        jpaBtn.style.opacity  = '0.35';
        jpaBtn.dataset.active = '0';
      }} else {{
        jpaEtypes.forEach(t => hiddenEdgeTypes.delete(t));
        jpaBtn.style.opacity  = '1';
        jpaBtn.dataset.active = '1';
      }}
      applyVisibility();
    }};
    panel.appendChild(jpaBtn);
  }}
}}

function buildLegend(nodes) {{
  const groups = {{}};
  nodes.forEach(n => {{
    if (n.kind === 'external') return;
    const parts = (n.fqn || '').split('.');
    const pkg = parts.slice(0, Math.min(3, parts.length - 1)).join('.');
    if (pkg && !groups[pkg]) groups[pkg] = groupColor(pkg);
  }});
  legendItems.innerHTML = Object.entries(groups).map(([pkg, col]) =>
    `<div style="display:flex;align-items:center;gap:7px;margin-bottom:5px;font-size:0.7rem;color:#94a3b8;">`
    + `<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${{col}};flex-shrink:0;"></span>`
    + `<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${{pkg}}">${{pkg}}</span></div>`
  ).join('');
}}

// ── Node/edge visuals ─────────────────────────────────────────────────────
function nodeVisual(n) {{
  const kind = n.kind || 'class';
  const col  = KIND_COLOR[kind] || KIND_COLOR.class;
  const isExt = kind === 'external';
  const isGenerated = kind === 'method' && n.generated === true;
  const isTest = n.is_test === true;
  const size  = isExt ? 10 : isGenerated ? 6 : Math.max(10, Math.min(32, 10 + (n.degree || 0)));
  return {{
    id:    n.id,
    label: n.label,
    fqn:   n.fqn,
    kind,
    generated: n.generated || false,
    is_test:   isTest,
    shape: kind === 'interface' ? 'diamond' : (isExt ? 'square' : 'dot'),
    size,
    color: {{ background: isTest ? '#0f2231' : col.bg,
              border:     isTest ? '#22d3ee' : col.border,
              highlight: {{ background: '#fff', border: '#6366f1' }},
              hover:     {{ background: col.border, border: '#fff' }} }},
    font:   {{ color: isTest ? '#334155' : isGenerated ? '#64748b' : (isExt ? '#94a3b8' : '#f1f5f9'), size: isExt ? 9 : 11 }},
    opacity: isGenerated ? 0.4 : 1.0,
    hidden: hiddenKinds.has(kind) || (isGenerated && !showGenerated) || (isTest && !showTests) || (n.degree === 0 && !showOrphans && kind !== 'external'),
    group:  n.group || '',
  }};
}}

function edgeVisual(e, idx) {{
  const w = e.weight || 1;
  // Differentiate edge types visually
  const edgeColor = e.etype === 'method_call'     ? '#374151'
                  : e.etype === 'ext_call'         ? '#4c0519'
                  : e.etype === 'extends'           ? '#7c3aed'
                  : e.etype === 'implements'        ? '#0369a1'
                  : e.etype === 'jpa_onetomany'    ? '#b45309'
                  : e.etype === 'jpa_manytoone'    ? '#d97706'
                  : e.etype === 'jpa_onetoone'     ? '#f59e0b'
                  : e.etype === 'jpa_manytomany'   ? '#fbbf24'
                  : '#2d3148';
  const isDashed = e.etype === 'extends' || e.etype === 'implements';
  return {{
    id:     `e${{idx}}`,
    from:   e.from,
    to:     e.to,
    etype:  e.etype,
    arrows: {{ to: {{ enabled: true, scaleFactor: 0.45 }} }},
    color:  {{ color: edgeColor, highlight: '#818cf8', opacity: isDashed ? 0.6 : 0.75 }},
    width:  Math.min(4, 0.8 + w * 0.25),
    smooth: {{ type: 'curvedCW', roundness: 0.12 }},
    title:  isDashed ? e.etype : `${{w}} call${{w > 1 ? 's' : ''}}`,
    dashes: isDashed,
    hidden: false,
  }};
}}

// ── Load data ─────────────────────────────────────────────────────────────
function loadGraph() {{
  const repo   = document.getElementById('repoSelect').value;
  const branch = document.getElementById('branchSelect').value;
  if (!repo) return;
  loadingDiv.style.display = 'block';
  loadingDiv.textContent   = 'Loading…';
  status.textContent       = '';
  legendPanel.style.display = 'none';
  if (network) {{ network.destroy(); network = null; visNodes = null; visEdges = null; }}
  hiddenKinds.clear();
  hiddenEdgeTypes.clear();
  showGenerated = false;
  showTests = false;
  showOrphans = false;
  rawData = null;

  const branchParam = branch ? `&branch=${{encodeURIComponent(branch)}}` : '';
  fetch(`/api/graph?repo=${{encodeURIComponent(repo)}}${{branchParam}}`)
    .then(r => r.json())
    .then(data => {{
      rawData = data;
      loadingDiv.style.display = 'none';
      if (!data.nodes.length) {{
        loadingDiv.style.display = 'block';
        loadingDiv.textContent   = 'No data found for this repo.';
        return;
      }}

      const kinds = [...new Set(data.nodes.map(n => n.kind))];
      buildFilterToggles(kinds);
      buildLegend(data.nodes);
      legendPanel.style.display = 'block';

      visNodes = new vis.DataSet(data.nodes.map(nodeVisual));
      visEdges = new vis.DataSet(data.edges.map(edgeVisual));

      network = new vis.Network(container, {{ nodes: visNodes, edges: visEdges }}, {{
        physics: {{
          enabled: true,
          solver: 'forceAtlas2Based',
          forceAtlas2Based: {{
            gravitationalConstant: -60,
            centralGravity: 0.004,
            springLength: 120,
            springConstant: 0.04,
            damping: 0.6,
          }},
          stabilization: {{ iterations: 300, updateInterval: 25 }},
          maxVelocity: 80,
        }},
        interaction: {{ hover: true, tooltipDelay: 80, navigationButtons: true, keyboard: true, multiselect: true }},
        layout:      {{ improvedLayout: false }},
      }});

      network.on('hoverNode', p => {{
        const n = visNodes.get(p.node);
        if (n) {{ tooltip.innerHTML = `<b>${{n.label}}</b><br><span style="color:#64748b">${{n.fqn}}</span>`; tooltip.style.display = 'block'; }}
      }});
      network.on('blurNode', () => tooltip.style.display = 'none');
      network.on('click', p => {{
        if (p.nodes.length) {{
          const n = visNodes.get(p.nodes[0]);
          if (n && !n.id.startsWith('__ext__')) window.open('/symbol?fqn=' + encodeURIComponent(n.fqn), '_blank');
        }}
      }});
      network.once('stabilizationIterationsDone', () => {{
        network.setOptions({{ physics: false }});
        network.fit({{ animation: {{ duration: 400 }} }});
      }});

      applyVisibility();
    }})
    .catch(err => {{
      loadingDiv.style.display = 'block';
      loadingDiv.textContent   = 'Error: ' + err;
    }});
}}

// ── Wiring ────────────────────────────────────────────────────────────────
document.getElementById('repoSelect').onchange = () => {{
  // When repo changes, reload branch list then re-render graph
  const repo = document.getElementById('repoSelect').value;
  fetch(`/api/branches?repo=${{encodeURIComponent(repo)}}`)
    .then(r => r.json())
    .then(branches => {{
      const sel = document.getElementById('branchSelect');
      sel.innerHTML = '<option value="">All branches</option>' +
        branches.map(b => `<option value="${{b}}">${{b}}</option>`).join('');
    }})
    .catch(() => {{}})
    .finally(() => loadGraph());
}};
document.getElementById('branchSelect').onchange = loadGraph;
document.getElementById('fitBtn').onclick = () => network && network.fit({{ animation: true }});
document.getElementById('inheritanceToggle').onclick = () => {{
    const btn = document.getElementById('inheritanceToggle');
    const active = btn.dataset.active === '1';
    if (active) {{
        hiddenEdgeTypes.add('extends');
        hiddenEdgeTypes.add('implements');
        btn.style.opacity = '0.35';
        btn.dataset.active = '0';
    }} else {{
        hiddenEdgeTypes.delete('extends');
        hiddenEdgeTypes.delete('implements');
        btn.style.opacity = '1';
        btn.dataset.active = '1';
    }}
    applyVisibility();
}};
document.addEventListener('mousemove', e => {{
  tooltip.style.left = (e.clientX + 16) + 'px';
  tooltip.style.top  = (e.clientY - 8)  + 'px';
}});

// Auto-load on page open
if (document.getElementById('repoSelect').options.length) loadGraph();
</script>
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

    async def graph(request: Request) -> HTMLResponse:
        repo_name = request.query_params.get("repo", "")
        return HTMLResponse(_page_graph(db, repo_name))

    async def findings_page(request: Request) -> HTMLResponse:
        repo_name = request.query_params.get("repo", "")
        return HTMLResponse(_page_findings(db, repo_name))

    async def api_graph(request: Request):
        from starlette.responses import JSONResponse  # noqa: PLC0415
        repo_name = request.query_params.get("repo", "")
        branch = request.query_params.get("branch", "")
        data = db.graph_data(repo_name, branch=branch)
        return JSONResponse(data)

    async def api_branches(request: Request):
        from starlette.responses import JSONResponse  # noqa: PLC0415
        repo_name = request.query_params.get("repo", "")
        branches = db.branches(repo_name)
        return JSONResponse([b["name"] for b in branches])

    async def api_findings(request: Request):
        from starlette.responses import JSONResponse  # noqa: PLC0415
        repo_name = request.query_params.get("repo", "")
        finding_type = request.query_params.get("type", "all")
        min_severity = request.query_params.get("min_severity", "low")
        reachable_only = request.query_params.get("reachable_only", "false").lower() == "true"
        data = db.findings(
            repo_name,
            finding_type=finding_type,
            min_severity=min_severity,
            reachable_only=reachable_only,
        )
        return JSONResponse(data)

    async def api_findings_export(request: Request):
        import json as _json  # noqa: PLC0415
        from starlette.responses import Response  # noqa: PLC0415
        repo_name = request.query_params.get("repo", "")
        finding_type = request.query_params.get("type", "all")
        min_severity = request.query_params.get("min_severity", "low")
        reachable_only = request.query_params.get("reachable_only", "false").lower() == "true"
        data = db.findings(
            repo_name,
            finding_type=finding_type,
            min_severity=min_severity,
            reachable_only=reachable_only,
        )
        safe_name = (repo_name or "findings").replace("/", "_").replace("\\", "_")
        filename = f"orihime-findings-{safe_name}.json"
        body = _json.dumps(data, indent=2)
        return Response(
            content=body,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

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
        Route("/graph", graph),
        Route("/findings", findings_page),
        Route("/api/graph", api_graph),
        Route("/api/branches", api_branches),
        Route("/api/findings", api_findings),
        Route("/api/findings/export", api_findings_export),
        Route("/endpoints", endpoints),
        Route("/index", index_get, methods=["GET"]),
        Route("/index", index_post, methods=["POST"]),
    ]
    return Starlette(routes=routes)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_ui(port: int = 7700, db_path: str = "") -> None:
    """Start the Orihime web UI on *port*, opening a browser automatically."""
    if not db_path:
        db_path = os.environ.get("ORIHIME_DB_PATH", str(Path.home() / ".orihime" / "orihime.db"))

    import uvicorn  # noqa: PLC0415

    db = _DB(db_path)
    app = _make_app(db, db_path)

    url = f"http://localhost:{port}"
    log.info("Starting Orihime UI at %s (db=%s)", url, db_path)
    print(f"\n  Orihime UI  →  {url}\n  Press Ctrl+C to stop.\n")

    # Open browser after a short delay to allow the server to start
    Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
