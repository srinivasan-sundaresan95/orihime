"""Indra MCP Server — code knowledge graph query tools.

Exposes 11 tools over the MCP protocol (FastMCP):
  10 query tools + 1 index_repo_tool.

Connection modes
----------------
Phase 1 (local):
    Set INDRA_DB_PATH to point at a KuzuDB directory (default: ~/.indra/indra.db).
    The database is opened lazily on the first tool call and reused.

Phase 2 (team/server, future):
    Set INDRA_SERVER_URL to the KuzuDB HTTP endpoint.
    The local file will be ignored when this variable is set.
    Implementation is deferred; the env-var is documented here for forward-compat.
"""
from __future__ import annotations

import logging
import os
import sys
from collections import deque
from pathlib import Path
from typing import Optional

import kuzu
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s [indra] %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_DEFAULT_DB_PATH = str(Path.home() / ".indra" / "indra.db")

# INDRA_SERVER_URL — reserved for Phase 2 (remote KuzuDB HTTP endpoint).
# Not used in Phase 1; present only for forward-compatibility.
_SERVER_URL: str = os.environ.get("INDRA_SERVER_URL", "")

_DB_PATH: str = os.environ.get("INDRA_DB_PATH", _DEFAULT_DB_PATH)

# ---------------------------------------------------------------------------
# Lazy connection singleton
# ---------------------------------------------------------------------------
_db: Optional[kuzu.Database] = None
_conn: Optional[kuzu.Connection] = None


def _get_connection() -> Optional[kuzu.Connection]:
    """Return the shared KuzuDB connection, opening it lazily on first call.

    Returns None if the database directory does not yet exist (i.e. no repo
    has been indexed yet).  Tools must treat a None connection as "empty
    results", not an error.
    """
    global _db, _conn
    if _conn is not None:
        return _conn

    db_path = Path(_DB_PATH)

    # KuzuDB creates the directory itself when it first opens.
    # However if the parent doesn't exist we should not create it silently
    # as part of a query — only index_repo_tool should do that.
    if not db_path.exists():
        log.info("Database not found at %s — returning empty results until a repo is indexed.", db_path)
        return None

    try:
        _db = kuzu.Database(str(db_path))
        _conn = kuzu.Connection(_db)
        log.info("Opened KuzuDB at %s", db_path)
        return _conn
    except Exception as exc:
        log.error("Failed to open KuzuDB at %s: %s", db_path, exc)
        return None


def _reset_connection() -> None:
    """Close and forget the current connection (called after index_repo_tool)."""
    global _db, _conn
    _conn = None
    _db = None


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def _rows(result: kuzu.QueryResult, columns: list[str]) -> list[dict]:
    """Convert a KuzuDB QueryResult into a list of dicts keyed by *columns*."""
    rows: list[dict] = []
    while result.has_next():
        row = result.get_next()
        rows.append(dict(zip(columns, row)))
    return rows


# ---------------------------------------------------------------------------
# FastMCP instance
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="indra",
    instructions=(
        "Indra is a code knowledge graph for Java/Kotlin Spring Boot repositories. "
        "Use these tools to answer questions about method call chains, REST endpoints, "
        "blast radius of changes, and cross-repo dependencies."
    ),
)

# ---------------------------------------------------------------------------
# Tool 1: find_callers
# ---------------------------------------------------------------------------

@mcp.tool()
def find_callers(method_fqn: str) -> list[dict]:
    """Find all methods that directly call the given method.

    Args:
        method_fqn: Fully-qualified method name, e.g. ``com.example.Foo.bar``.

    Returns:
        List of dicts with keys ``fqn``, ``file_path``, ``line_start``.
        Empty list if the method is not found or has no callers.
    """
    conn = _get_connection()
    if conn is None:
        return []
    try:
        result = conn.execute(
            "MATCH (caller:Method)-[:CALLS]->(callee:Method) "
            "WHERE callee.fqn = $fqn "
            "MATCH (f:File) WHERE f.id = caller.file_id "
            "RETURN caller.fqn AS fqn, f.path AS file_path, caller.line_start AS line_start",
            {"fqn": method_fqn},
        )
        return _rows(result, ["fqn", "file_path", "line_start"])
    except Exception as exc:
        log.error("find_callers(%r): %s", method_fqn, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Tool 2: find_callees
# ---------------------------------------------------------------------------

@mcp.tool()
def find_callees(method_fqn: str) -> list[dict]:
    """Find all methods directly called by the given method.

    Args:
        method_fqn: Fully-qualified method name, e.g. ``com.example.Foo.bar``.

    Returns:
        List of dicts with keys ``fqn``, ``file_path``, ``line_start``.
        Empty list if the method is not found or makes no calls.
    """
    conn = _get_connection()
    if conn is None:
        return []
    try:
        result = conn.execute(
            "MATCH (caller:Method)-[:CALLS]->(callee:Method) "
            "WHERE caller.fqn = $fqn "
            "MATCH (f:File) WHERE f.id = callee.file_id "
            "RETURN callee.fqn AS fqn, f.path AS file_path, callee.line_start AS line_start",
            {"fqn": method_fqn},
        )
        return _rows(result, ["fqn", "file_path", "line_start"])
    except Exception as exc:
        log.error("find_callees(%r): %s", method_fqn, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Tool 3: find_endpoint_callers
# ---------------------------------------------------------------------------

@mcp.tool()
def find_endpoint_callers(http_method: str, path_pattern: str) -> list[dict]:
    """Find the handler method for an endpoint and all upstream callers.

    Args:
        http_method:  HTTP verb — GET, POST, PUT, DELETE, or PATCH (case-insensitive).
        path_pattern: Exact path of the endpoint, e.g. ``/api/users/{id}``.

    Returns:
        List of dicts with keys ``role`` (``"handler"`` or ``"caller"``),
        ``fqn``, ``file_path``, ``line_start``.
        Empty list if the endpoint is not found.
    """
    conn = _get_connection()
    if conn is None:
        return []
    try:
        # 1. Resolve the handler method via Endpoint.handler_method_id
        ep_result = conn.execute(
            "MATCH (e:Endpoint) "
            "WHERE e.http_method = $http_method AND e.path = $path "
            "RETURN e.handler_method_id AS handler_id",
            {"http_method": http_method.upper(), "path": path_pattern},
        )
        handler_ids: list[str] = []
        while ep_result.has_next():
            row = ep_result.get_next()
            if row[0] is not None:
                handler_ids.append(row[0])

        if not handler_ids:
            return []

        results: list[dict] = []

        for handler_id in handler_ids:
            # 2. Look up the handler Method node
            m_result = conn.execute(
                "MATCH (m:Method) WHERE m.id = $mid "
                "MATCH (f:File) WHERE f.id = m.file_id "
                "RETURN m.fqn AS fqn, f.path AS file_path, m.line_start AS line_start",
                {"mid": handler_id},
            )
            handler_rows = _rows(m_result, ["fqn", "file_path", "line_start"])
            for row in handler_rows:
                results.append({"role": "handler", **row})
                handler_fqn = row["fqn"]

                # 3. Find callers of the handler
                callers_result = conn.execute(
                    "MATCH (caller:Method)-[:CALLS]->(callee:Method) "
                    "WHERE callee.fqn = $fqn "
                    "MATCH (f:File) WHERE f.id = caller.file_id "
                    "RETURN caller.fqn AS fqn, f.path AS file_path, caller.line_start AS line_start",
                    {"fqn": handler_fqn},
                )
                for caller_row in _rows(callers_result, ["fqn", "file_path", "line_start"]):
                    results.append({"role": "caller", **caller_row})

        return results
    except Exception as exc:
        log.error("find_endpoint_callers(%r, %r): %s", http_method, path_pattern, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Tool 4: find_repo_dependencies
# ---------------------------------------------------------------------------

@mcp.tool()
def find_repo_dependencies(repo_name: str) -> list[dict]:
    """Find all repositories that the given repository directly depends on.

    Args:
        repo_name: The logical name of the repository as indexed (e.g. ``point-bank-bff``).

    Returns:
        List of dicts with key ``name`` for each dependency repo.
        Empty list if the repo is not found or has no declared dependencies.
    """
    conn = _get_connection()
    if conn is None:
        return []
    try:
        result = conn.execute(
            "MATCH (r:Repo)-[:DEPENDS_ON]->(dep:Repo) "
            "WHERE r.name = $repo_name "
            "RETURN dep.name AS name",
            {"repo_name": repo_name},
        )
        return _rows(result, ["name"])
    except Exception as exc:
        log.error("find_repo_dependencies(%r): %s", repo_name, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Tool 5: blast_radius
# ---------------------------------------------------------------------------

@mcp.tool()
def blast_radius(method_fqn: str, max_depth: int = 3) -> list[dict]:
    """Find all methods transitively affected by changing the given method.

    Performs a breadth-first traversal of CALLS edges in reverse
    (callers of callers) up to *max_depth* hops.

    Args:
        method_fqn: FQN of the method being changed, e.g. ``com.example.Foo.bar``.
        max_depth:  Maximum number of hops to traverse (default 3, max 10).

    Returns:
        List of dicts with keys ``fqn``, ``file_path``, and ``depth``.
        Depth 1 = direct callers, depth 2 = their callers, etc.
        The changed method itself is not included.
    """
    conn = _get_connection()
    if conn is None:
        return []

    # Guard against excessively deep traversals
    max_depth = min(max_depth, 10)

    try:
        visited: dict[str, tuple[int, str]] = {}  # fqn -> (depth, file_path)
        queue: deque[tuple[str, int]] = deque()
        queue.append((method_fqn, 0))

        while queue:
            current_fqn, depth = queue.popleft()
            if depth >= max_depth:
                continue

            result = conn.execute(
                "MATCH (caller:Method)-[:CALLS]->(callee:Method) "
                "WHERE callee.fqn = $fqn "
                "MATCH (f:File) WHERE f.id = caller.file_id "
                "RETURN caller.fqn AS fqn, f.path AS file_path",
                {"fqn": current_fqn},
            )
            while result.has_next():
                row = result.get_next()
                caller_fqn: str = row[0]
                caller_file_path: str = row[1]
                next_depth = depth + 1
                if caller_fqn not in visited:
                    visited[caller_fqn] = (next_depth, caller_file_path)
                    queue.append((caller_fqn, next_depth))

        return [
            {"fqn": fqn, "file_path": file_path, "depth": depth}
            for fqn, (depth, file_path) in sorted(visited.items(), key=lambda kv: kv[1][0])
        ]
    except Exception as exc:
        log.error("blast_radius(%r, %d): %s", method_fqn, max_depth, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Tool 6: search_symbol
# ---------------------------------------------------------------------------

@mcp.tool()
def search_symbol(query: str) -> list[dict]:
    """Search for classes or methods by name (case-insensitive substring match).

    Args:
        query: Substring to search for, e.g. ``InterestCalc`` or ``calculate``.

    Returns:
        List of dicts with keys ``type`` (``"class"`` or ``"method"``),
        ``fqn``, and ``file_path``.
        Results from both classes and methods are merged and returned together.
    """
    conn = _get_connection()
    if conn is None:
        return []

    # KuzuDB does not have a built-in ILIKE; use LOWER() for case-insensitive match.
    lower_query = query.lower()
    try:
        results: list[dict] = []

        class_result = conn.execute(
            "MATCH (c:Class) "
            "WHERE lower(c.name) CONTAINS $q "
            "MATCH (f:File) WHERE f.id = c.file_id "
            "RETURN c.fqn AS fqn, f.path AS file_path "
            "LIMIT 50",
            {"q": lower_query},
        )
        for row in _rows(class_result, ["fqn", "file_path"]):
            results.append({"type": "class", **row})

        method_result = conn.execute(
            "MATCH (m:Method) "
            "WHERE lower(m.name) CONTAINS $q "
            "MATCH (f:File) WHERE f.id = m.file_id "
            "RETURN m.fqn AS fqn, f.path AS file_path "
            "LIMIT 50",
            {"q": lower_query},
        )
        for row in _rows(method_result, ["fqn", "file_path"]):
            results.append({"type": "method", **row})

        return results
    except Exception as exc:
        log.error("search_symbol(%r): %s", query, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Tool 7: get_file_location
# ---------------------------------------------------------------------------

@mcp.tool()
def get_file_location(fqn: str) -> Optional[dict]:
    """Get the source file path and line number for a method or class by FQN.

    Tries Method first, then Class.

    Args:
        fqn: Fully-qualified name of the method or class.

    Returns:
        Dict with keys ``fqn``, ``file_path``, ``line_start``,
        or ``None`` if not found.
    """
    conn = _get_connection()
    if conn is None:
        return None
    try:
        # Try Method first
        method_result = conn.execute(
            "MATCH (m:Method) WHERE m.fqn = $fqn "
            "MATCH (f:File) WHERE f.id = m.file_id "
            "RETURN m.fqn AS fqn, f.path AS file_path, m.line_start AS line_start",
            {"fqn": fqn},
        )
        if method_result.has_next():
            row = method_result.get_next()
            return {"fqn": row[0], "file_path": row[1], "line_start": row[2]}

        # Fall back to Class (no line_start on Class, return 0)
        class_result = conn.execute(
            "MATCH (c:Class) WHERE c.fqn = $fqn "
            "MATCH (f:File) WHERE f.id = c.file_id "
            "RETURN c.fqn AS fqn, f.path AS file_path",
            {"fqn": fqn},
        )
        if class_result.has_next():
            row = class_result.get_next()
            return {"fqn": row[0], "file_path": row[1], "line_start": 0}

        return None
    except Exception as exc:
        log.error("get_file_location(%r): %s", fqn, exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Tool 8: list_endpoints
# ---------------------------------------------------------------------------

@mcp.tool()
def list_endpoints(repo_name: str = "") -> list[dict]:
    """List all HTTP endpoints in the graph, optionally filtered by repository.

    Args:
        repo_name: If provided, only return endpoints belonging to this repo.
                   Pass an empty string (the default) to list all repos.

    Returns:
        List of dicts with keys ``http_method``, ``path``,
        ``handler_fqn``, and ``repo_name``.
    """
    conn = _get_connection()
    if conn is None:
        return []
    try:
        if repo_name:
            result = conn.execute(
                "MATCH (r:Repo)-[:EXPOSES]->(e:Endpoint) "
                "WHERE r.name = $repo_name "
                "MATCH (m:Method) WHERE m.id = e.handler_method_id "
                "RETURN e.http_method AS http_method, e.path AS path, "
                "       m.fqn AS handler_fqn, r.name AS repo_name",
                {"repo_name": repo_name},
            )
        else:
            result = conn.execute(
                "MATCH (r:Repo)-[:EXPOSES]->(e:Endpoint) "
                "MATCH (m:Method) WHERE m.id = e.handler_method_id "
                "RETURN e.http_method AS http_method, e.path AS path, "
                "       m.fqn AS handler_fqn, r.name AS repo_name",
            )
        return _rows(result, ["http_method", "path", "handler_fqn", "repo_name"])
    except Exception as exc:
        log.error("list_endpoints(%r): %s", repo_name, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Tool 9: list_unresolved_calls
# ---------------------------------------------------------------------------

@mcp.tool()
def list_unresolved_calls(repo_name: str = "") -> list[dict]:
    """List outgoing REST calls that could not be matched to a known endpoint.

    These represent cross-repo or external HTTP calls that Indra has not yet
    resolved to an Endpoint node.

    Args:
        repo_name: If provided, only return unresolved calls from this repo.
                   Pass an empty string (the default) to list all repos.

    Returns:
        List of dicts with keys ``url_pattern``, ``http_method``,
        ``callee_name``, ``caller_fqn``, and ``repo_name``.
    """
    conn = _get_connection()
    if conn is None:
        return []
    try:
        if repo_name:
            result = conn.execute(
                "MATCH (caller:Method)-[:UNRESOLVED_CALL]->(rc:RestCall) "
                "MATCH (r:Repo) WHERE r.id = rc.repo_id AND r.name = $repo_name "
                "RETURN rc.url_pattern AS url_pattern, rc.http_method AS http_method, "
                "       rc.callee_name AS callee_name, "
                "       caller.fqn AS caller_fqn, r.name AS repo_name",
                {"repo_name": repo_name},
            )
        else:
            result = conn.execute(
                "MATCH (caller:Method)-[:UNRESOLVED_CALL]->(rc:RestCall) "
                "MATCH (r:Repo) WHERE r.id = rc.repo_id "
                "RETURN rc.url_pattern AS url_pattern, rc.http_method AS http_method, "
                "       rc.callee_name AS callee_name, "
                "       caller.fqn AS caller_fqn, r.name AS repo_name",
            )
        return _rows(result, ["url_pattern", "http_method", "callee_name", "caller_fqn", "repo_name"])
    except Exception as exc:
        log.error("list_unresolved_calls(%r): %s", repo_name, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Tool 10: list_repos
# ---------------------------------------------------------------------------

@mcp.tool()
def list_repos() -> list[dict]:
    """List all indexed repositories with their stats.

    Returns:
        List of dicts with keys: ``name``, ``root_path``, ``method_count``,
        ``endpoint_count``.
        Empty list if no repositories have been indexed yet.
    """
    conn = _get_connection()
    if conn is None:
        return []
    try:
        result = conn.execute(
            "MATCH (r:Repo) "
            "OPTIONAL MATCH (m:Method) WHERE m.repo_id = r.id "
            "OPTIONAL MATCH (e:Endpoint) WHERE e.repo_id = r.id "
            "RETURN r.name AS name, r.root_path AS root_path, "
            "       count(DISTINCT m) AS method_count, count(DISTINCT e) AS endpoint_count",
        )
        return _rows(result, ["name", "root_path", "method_count", "endpoint_count"])
    except Exception as exc:
        log.error("list_repos(): %s", exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Tool 11: index_repo_tool
# ---------------------------------------------------------------------------

@mcp.tool()
def index_repo_tool(repo_path: str, repo_name: str) -> dict:
    """Index a source repository into the Indra knowledge graph.

    After indexing, all other query tools will reflect the new data.

    Args:
        repo_path: Absolute path to the repository root on disk.
        repo_name: Logical name to identify the repo in queries
                   (e.g. ``point-bank-bff``).

    Returns:
        Summary dict with counts: ``repos``, ``files``, ``classes``,
        ``methods``, ``endpoints``, ``rest_calls``, ``call_edges``.
        On failure, returns ``{"error": "<message>"}``.
    """
    # Import here to avoid circular imports at module load time
    try:
        from indra.indexer import index_repo  # noqa: PLC0415
    except ImportError as exc:
        return {"error": f"indexer not available: {exc}"}

    try:
        summary = index_repo(repo_path, repo_name, _DB_PATH)
        # Reset connection so the next query picks up the freshly-written data
        _reset_connection()
        log.info("Indexed %r: %s", repo_name, summary)
        return summary
    except Exception as exc:
        log.error("index_repo_tool(%r, %r): %s", repo_path, repo_name, exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def cli() -> None:
    """Entry point called from ``python -m indra serve``.

    Starts the MCP server using stdio transport (default for Claude Code).
    """
    log.info("Starting Indra MCP server (db=%s)", _DB_PATH)
    mcp.run()


if __name__ == "__main__":
    cli()
