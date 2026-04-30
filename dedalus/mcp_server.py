"""Dedalus MCP Server — code knowledge graph query tools.

Exposes 11 tools over the MCP protocol (FastMCP):
  10 query tools + 1 index_repo_tool.

Connection modes
----------------
Phase 1 (local):
    Set DEDALUS_DB_PATH to point at a KuzuDB directory (default: ~/.dedalus/dedalus.db).
    The database is opened lazily on the first tool call and reused.

Phase 2 (team/server, future):
    Set DEDALUS_SERVER_URL to the KuzuDB HTTP endpoint.
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
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s [dedalus] %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_DEFAULT_DB_PATH = str(Path.home() / ".dedalus" / "dedalus.db")

# DEDALUS_SERVER_URL — reserved for Phase 2 (remote KuzuDB HTTP endpoint).
# Not used in Phase 1; present only for forward-compatibility.
_SERVER_URL: str = os.environ.get("DEDALUS_SERVER_URL", "")

_DB_PATH: str = os.environ.get("DEDALUS_DB_PATH", _DEFAULT_DB_PATH)

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
    name="dedalus",
    instructions=(
        "Dedalus is a code knowledge graph for Java/Kotlin Spring Boot repositories. "
        "Use these tools to answer questions about method call chains, REST endpoints, "
        "blast radius of changes, and cross-repo dependencies."
    ),
)

# ---------------------------------------------------------------------------
# Tool 1: find_callers
# ---------------------------------------------------------------------------

@mcp.tool()
def find_callers(method_fqn: str, exclude_generated: bool = False) -> list[dict]:
    """Find all methods that directly call the given method.

    Args:
        method_fqn: Fully-qualified method name, e.g. ``com.example.Foo.bar``.
        exclude_generated: When True, filter out Lombok/compiler-generated callers.

    Returns:
        List of dicts with keys ``fqn``, ``file_path``, ``line_start``.
        Empty list if the method is not found or has no callers.
    """
    conn = _get_connection()
    if conn is None:
        return []
    try:
        gen_filter = " AND caller.generated = false" if exclude_generated else ""
        result = conn.execute(
            "MATCH (caller:Method)-[:CALLS]->(callee:Method) "
            f"WHERE callee.fqn = $fqn{gen_filter} "
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
def find_callees(method_fqn: str, exclude_generated: bool = False) -> list[dict]:
    """Find all methods directly called by the given method.

    Args:
        method_fqn: Fully-qualified method name, e.g. ``com.example.Foo.bar``.
        exclude_generated: When True, filter out Lombok/compiler-generated callees.

    Returns:
        List of dicts with keys ``fqn``, ``file_path``, ``line_start``.
        Empty list if the method is not found or makes no calls.
    """
    conn = _get_connection()
    if conn is None:
        return []
    try:
        gen_filter = " AND callee.generated = false" if exclude_generated else ""
        result = conn.execute(
            "MATCH (caller:Method)-[:CALLS]->(callee:Method) "
            f"WHERE caller.fqn = $fqn{gen_filter} "
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
def blast_radius(method_fqn: str, max_depth: int = 3, exclude_generated: bool = False) -> list[dict]:
    """Find all methods transitively affected by changing the given method.

    Performs a breadth-first traversal of CALLS edges in reverse
    (callers of callers) up to *max_depth* hops.

    Args:
        method_fqn: FQN of the method being changed, e.g. ``com.example.Foo.bar``.
        max_depth:  Maximum number of hops to traverse (default 3, max 10).
        exclude_generated: When True, filter out Lombok/compiler-generated callers.

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

        gen_filter = " AND caller.generated = false" if exclude_generated else ""

        while queue:
            current_fqn, depth = queue.popleft()
            if depth >= max_depth:
                continue

            cypher = (
                "MATCH (caller:Method)-[:CALLS]->(callee:Method) "
                f"WHERE callee.fqn = $fqn{gen_filter} "
                "MATCH (f:File) WHERE f.id = caller.file_id "
                "RETURN caller.fqn AS fqn, f.path AS file_path"
            )
            result = conn.execute(cypher, {"fqn": current_fqn})
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

    These represent cross-repo or external HTTP calls that Dedalus has not yet
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
# Tool 11b: list_branches
# ---------------------------------------------------------------------------

@mcp.tool()
def list_branches(repo_name: str = "") -> list[dict]:
    """List all indexed branches, optionally filtered by repository.

    Args:
        repo_name: If provided, only return branches belonging to this repo.
                   Pass an empty string (the default) to list all repos.

    Returns:
        List of dicts with keys ``repo_name``, ``branch_name``.
    """
    conn = _get_connection()
    if conn is None:
        return []
    try:
        if repo_name:
            result = conn.execute(
                "MATCH (r:Repo)-[:HAS_BRANCH]->(b:Branch) "
                "WHERE r.name = $repo_name "
                "RETURN r.name AS repo_name, b.name AS branch_name",
                {"repo_name": repo_name},
            )
        else:
            result = conn.execute(
                "MATCH (r:Repo)-[:HAS_BRANCH]->(b:Branch) "
                "RETURN r.name AS repo_name, b.name AS branch_name",
            )
        return _rows(result, ["repo_name", "branch_name"])
    except Exception as exc:
        log.error("list_branches(%r): %s", repo_name, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Tool 12: find_implementations
# ---------------------------------------------------------------------------

@mcp.tool()
def find_implementations(interface_fqn: str) -> list[dict]:
    """Find all classes that directly implement the given interface (up to 10 hops via IMPLEMENTS).

    Args:
        interface_fqn: FQN of the interface, e.g. ``com.example.WalletService``.

    Returns:
        List of dicts with keys ``class_fqn``, ``class_name``, ``file_path``, ``repo_name``.
    """
    conn = _get_connection()
    if conn is None:
        return []
    try:
        result = conn.execute(
            "MATCH (impl:Class)-[:IMPLEMENTS*1..10]->(iface:Class) "
            "WHERE iface.fqn = $fqn "
            "MATCH (f:File) WHERE f.id = impl.file_id "
            "MATCH (r:Repo) WHERE r.id = impl.repo_id "
            "RETURN impl.fqn AS class_fqn, impl.name AS class_name, "
            "       f.path AS file_path, r.name AS repo_name",
            {"fqn": interface_fqn},
        )
        return _rows(result, ["class_fqn", "class_name", "file_path", "repo_name"])
    except Exception as exc:
        log.error("find_implementations(%r): %s", interface_fqn, exc)
        return []


# ---------------------------------------------------------------------------
# Tool 13: find_superclasses
# ---------------------------------------------------------------------------

@mcp.tool()
def find_superclasses(class_fqn: str, max_depth: int = 10) -> list[dict]:
    """Walk the EXTENDS chain upward from the given class (BFS, max depth 10).

    Returns:
        List of dicts with keys ``class_fqn``, ``depth``, ``repo_name``.
        Depth 1 = direct parent. Starting class not included.
    """
    conn = _get_connection()
    if conn is None:
        return []
    from collections import deque  # noqa: PLC0415
    max_depth = min(max_depth, 10)
    try:
        visited: dict[str, tuple[int, str]] = {}
        queue: deque[tuple[str, int]] = deque([(class_fqn, 0)])
        while queue:
            current_fqn, depth = queue.popleft()
            if depth >= max_depth:
                continue
            result = conn.execute(
                "MATCH (child:Class)-[:EXTENDS]->(parent:Class) "
                "WHERE child.fqn = $fqn "
                "MATCH (r:Repo) WHERE r.id = parent.repo_id "
                "RETURN parent.fqn, r.name",
                {"fqn": current_fqn},
            )
            while result.has_next():
                row = result.get_next()
                parent_fqn, parent_repo = row[0], row[1]
                if parent_fqn not in visited:
                    visited[parent_fqn] = (depth + 1, parent_repo)
                    queue.append((parent_fqn, depth + 1))
        return [
            {"class_fqn": fqn, "depth": d, "repo_name": repo}
            for fqn, (d, repo) in sorted(visited.items(), key=lambda kv: kv[1][0])
        ]
    except Exception as exc:
        log.error("find_superclasses(%r): %s", class_fqn, exc)
        return []


# ---------------------------------------------------------------------------
# Tool 14: list_entity_relations
# ---------------------------------------------------------------------------

@mcp.tool()
def list_entity_relations(repo_name: str) -> list[dict]:
    """List all JPA entity relationships in a repo.

    Returns list of dicts: source_class_fqn, field_name, relation_type,
    fetch_type, target_class_fqn.
    """
    conn = _get_connection()
    if conn is None:
        return []
    try:
        result = conn.execute(
            "MATCH (r:Repo) WHERE r.name = $repo_name RETURN r.id",
            {"repo_name": repo_name},
        )
        if not result.has_next():
            return []
        repo_id = result.get_next()[0]

        r = conn.execute(
            "MATCH (c:Class)-[:HAS_RELATION]->(er:EntityRelation) "
            "WHERE er.repo_id = $rid "
            "RETURN c.fqn, er.field_name, er.relation_type, er.fetch_type, er.target_class_fqn",
            {"rid": repo_id},
        )
        return _rows(r, ["source_class_fqn", "field_name", "relation_type", "fetch_type", "target_class_fqn"])
    except Exception as exc:
        log.error("list_entity_relations(%r): %s", repo_name, exc)
        return []


# ---------------------------------------------------------------------------
# Tool 15: find_eager_fetches
# ---------------------------------------------------------------------------

@mcp.tool()
def find_eager_fetches(repo_name: str) -> list[dict]:
    """Find all EAGER fetch relationships — potential N+1 query sources.

    Returns list of dicts with source_class_fqn, field_name, relation_type,
    target_class_fqn for all relations where fetch_type = 'EAGER'.
    """
    conn = _get_connection()
    if conn is None:
        return []
    try:
        result = conn.execute(
            "MATCH (r:Repo) WHERE r.name = $repo_name RETURN r.id",
            {"repo_name": repo_name},
        )
        if not result.has_next():
            return []
        repo_id = result.get_next()[0]

        r = conn.execute(
            "MATCH (c:Class)-[:HAS_RELATION]->(er:EntityRelation) "
            "WHERE er.repo_id = $rid AND er.fetch_type = 'EAGER' "
            "RETURN c.fqn, er.field_name, er.relation_type, er.target_class_fqn",
            {"rid": repo_id},
        )
        return _rows(r, ["source_class_fqn", "field_name", "relation_type", "target_class_fqn"])
    except Exception as exc:
        log.error("find_eager_fetches(%r): %s", repo_name, exc)
        return []


# ---------------------------------------------------------------------------
# Security Tool S4: find_cross_service_taint
# ---------------------------------------------------------------------------

@mcp.tool()
def find_cross_service_taint(repo_name: str, max_depth: int = 6) -> list[dict]:
    """Find taint paths from HTTP endpoint handler parameters to outgoing REST calls.

    This is an Dedalus-native equivalent of SonarQube Enterprise "Advanced SAST"
    cross-service taint analysis.

    A taint path is a call chain that starts at an HTTP endpoint handler method
    (whose parameters are user-controlled: @RequestParam, @PathVariable, @RequestBody)
    and ends at a method that issues an outgoing HTTP call (UNRESOLVED_CALL or
    CALLS_REST edge).  Intermediate hops are method CALLS edges.

    Args:
        repo_name: Repository to analyse.
        max_depth: Maximum call-chain depth to traverse (default 6).

    Returns:
        List of dicts, each describing one taint path::

            {
                "source_handler_fqn":   str,  # endpoint handler method
                "source_endpoint":       str,  # HTTP path e.g. GET /api/users/{id}
                "sink_method_fqn":       str,  # method that makes the outgoing call
                "sink_url_pattern":      str,  # URL pattern of the outgoing call
                "sink_http_method":      str,  # GET/POST/...
                "path_length":           int,  # number of hops
                "call_chain":            list, # [method_fqn, ...] from source to sink
            }
    """
    conn = _get_connection()
    if conn is None:
        return []
    try:
        # Resolve repo_id
        r = conn.execute(
            "MATCH (repo:Repo) WHERE repo.name = $name RETURN repo.id",
            {"name": repo_name},
        )
        if not r.has_next():
            return []
        repo_id = r.get_next()[0]

        # 1. Collect all endpoint handlers in this repo
        r_ep = conn.execute(
            "MATCH (repo:Repo)-[:EXPOSES]->(ep:Endpoint) "
            "WHERE repo.id = $rid "
            "RETURN ep.handler_method_id, ep.http_method, ep.path",
            {"rid": repo_id},
        )
        handlers: list[tuple[str, str, str]] = []
        while r_ep.has_next():
            mid, http_m, path = r_ep.get_next()
            handlers.append((mid, http_m, path))

        if not handlers:
            return []

        # 2. Build full in-repo CALLS adjacency list for BFS
        #    method_id → list of callee_ids
        r_calls = conn.execute(
            "MATCH (a:Method)-[:CALLS]->(b:Method) WHERE a.repo_id = $rid RETURN a.id, b.id",
            {"rid": repo_id},
        )
        adj: dict[str, list[str]] = {}
        while r_calls.has_next():
            src, dst = r_calls.get_next()
            adj.setdefault(src, []).append(dst)

        # 3. Collect all methods that issue outbound REST calls in this repo
        r_rc = conn.execute(
            "MATCH (m:Method)-[:UNRESOLVED_CALL]->(rc:RestCall) "
            "WHERE m.repo_id = $rid "
            "RETURN m.id, rc.url_pattern, rc.http_method",
            {"rid": repo_id},
        )
        sink_calls: dict[str, list[tuple[str, str]]] = {}
        while r_rc.has_next():
            mid, url, http_m = r_rc.get_next()
            sink_calls.setdefault(mid, []).append((url, http_m))
        # Also CALLS_REST (resolved calls)
        r_cr = conn.execute(
            "MATCH (m:Method)-[:CALLS_REST]->(ep:Endpoint) "
            "WHERE m.repo_id = $rid "
            "RETURN m.id, ep.path, ep.http_method",
            {"rid": repo_id},
        )
        while r_cr.has_next():
            mid, url, http_m = r_cr.get_next()
            sink_calls.setdefault(mid, []).append((url, http_m))

        if not sink_calls:
            return []

        # 4. Build fqn lookup for display
        r_fqn = conn.execute(
            "MATCH (m:Method) WHERE m.repo_id = $rid RETURN m.id, m.fqn",
            {"rid": repo_id},
        )
        id_to_fqn: dict[str, str] = {}
        while r_fqn.has_next():
            mid, fqn = r_fqn.get_next()
            id_to_fqn[mid] = fqn

        # 5. BFS from each handler toward sinks
        results: list[dict] = []
        seen_paths: set[tuple[str, str]] = set()  # (handler_id, sink_id)

        for handler_id, ep_http_method, ep_path in handlers:
            if handler_id not in id_to_fqn:
                continue
            # BFS
            queue: deque[tuple[str, list[str]]] = deque([(handler_id, [handler_id])])
            visited: set[str] = {handler_id}
            while queue:
                current, path = queue.popleft()
                if len(path) > max_depth + 1:
                    continue
                if current in sink_calls and current != handler_id:
                    key = (handler_id, current)
                    if key not in seen_paths:
                        seen_paths.add(key)
                        for url_pattern, sink_http_m in sink_calls[current]:
                            results.append({
                                "source_handler_fqn": id_to_fqn.get(handler_id, handler_id),
                                "source_endpoint": f"{ep_http_method} {ep_path}",
                                "sink_method_fqn": id_to_fqn.get(current, current),
                                "sink_url_pattern": url_pattern,
                                "sink_http_method": sink_http_m,
                                "path_length": len(path) - 1,
                                "call_chain": [id_to_fqn.get(m, m) for m in path],
                            })
                for callee in adj.get(current, []):
                    if callee not in visited:
                        visited.add(callee)
                        queue.append((callee, path + [callee]))

        results.sort(key=lambda x: x["path_length"])
        return results
    except Exception as exc:
        log.error("find_cross_service_taint(%r): %s", repo_name, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Security Tool S5: find_taint_sinks
# ---------------------------------------------------------------------------

@mcp.tool()
def find_taint_sinks(repo_name: str) -> list[dict]:
    """Find all calls to known dangerous sink methods in the given repository.

    Uses the built-in sink registry (SQL, HTTP clients, exec) merged with any
    custom sinks defined in ``~/.dedalus/security.yml``.  This is the custom
    sources/sinks equivalent of SonarQube Enterprise's configurable taint rules.

    Args:
        repo_name: Repository to analyse.

    Returns:
        List of dicts with keys:
            ``caller_fqn``, ``sink_method``, ``file_path``, ``line_start``.
    """
    from dedalus.security_config import get_security_config  # noqa: PLC0415
    conn = _get_connection()
    if conn is None:
        return []
    try:
        r = conn.execute(
            "MATCH (repo:Repo) WHERE repo.name = $name RETURN repo.id",
            {"name": repo_name},
        )
        if not r.has_next():
            return []
        repo_id = r.get_next()[0]

        cfg = get_security_config()
        results: list[dict] = []

        # Check UNRESOLVED_CALL nodes whose callee_name matches a known sink
        r_rc = conn.execute(
            "MATCH (m:Method)-[:UNRESOLVED_CALL]->(rc:RestCall) "
            "WHERE m.repo_id = $rid "
            "MATCH (f:File) WHERE f.id = m.file_id "
            "RETURN m.fqn, rc.callee_name, f.path, m.line_start",
            {"rid": repo_id},
        )
        while r_rc.has_next():
            caller_fqn, callee_name, file_path, line_start = r_rc.get_next()
            if callee_name and cfg.is_sink_method(callee_name):
                results.append({
                    "caller_fqn": caller_fqn,
                    "sink_method": callee_name,
                    "file_path": file_path,
                    "line_start": line_start,
                    "sink_category": "custom",
                })

        # Cross-service sinks via CALLS edges to methods whose FQN matches a known sink
        r_calls = conn.execute(
            "MATCH (m:Method)-[:CALLS]->(s:Method) "
            "WHERE m.repo_id = $rid "
            "MATCH (f:File) WHERE f.id = m.file_id "
            "RETURN m.fqn, s.fqn, s.name, f.path, m.line_start",
            {"rid": repo_id},
        )
        while r_calls.has_next():
            caller_fqn, callee_fqn, callee_name, file_path, line_start = r_calls.get_next()
            if cfg.is_sink_method(callee_fqn) or cfg.is_sink_method(callee_name):
                results.append({
                    "caller_fqn": caller_fqn,
                    "sink_method": callee_fqn,
                    "file_path": file_path,
                    "line_start": line_start,
                    "sink_category": "configured",
                })

        return results
    except Exception as exc:
        log.error("find_taint_sinks(%r): %s", repo_name, exc)
        return [{"error": str(exc)}]


@mcp.tool()
def list_security_config() -> dict:
    """Return the active security configuration (sources, sinks, sanitizers).

    Shows the merged built-in + user-defined rules currently in effect.
    Useful for verifying that custom ``~/.dedalus/security.yml`` rules were loaded.

    Returns:
        Dict with keys ``source_annotations``, ``source_methods``,
        ``sink_methods``, ``sanitizer_methods``.
    """
    from dedalus.security_config import get_security_config  # noqa: PLC0415
    cfg = get_security_config()
    return {
        "source_annotations": cfg.source_annotations,
        "source_methods": cfg.source_methods,
        "sink_methods": cfg.sink_methods,
        "sanitizer_methods": cfg.sanitizer_methods,
    }


# ---------------------------------------------------------------------------
# Security Tool S6: find_second_order_injection
# ---------------------------------------------------------------------------

@mcp.tool()
def find_second_order_injection(repo_name: str) -> list[dict]:
    """Detect second-order injection patterns: taint written to DB then read back unsanitized.

    A second-order (stored) injection occurs when:
      1. User-controlled data reaches a persistence write (JPA save/persist/merge).
      2. That same data is later read back from the DB and passed to a dangerous sink.

    Dedalus approximates this by finding:
      - Methods that write to a JPA entity (call to save/persist/merge on a Repository
        class or on an EntityManager).
      - Methods that read from the same entity type (findById/findAll/executeQuery) AND
        whose return value flows into a sink (detected via call chain analysis).

    This is a structural approximation — it is not full data-flow.  False positives are
    expected; use it to prioritise manual review, not as a definitive scanner.

    Args:
        repo_name: Repository to analyse.

    Returns:
        List of dicts with keys:
            ``entity_fqn``, ``write_method_fqn``, ``read_method_fqn``,
            ``read_file_path``, ``risk_level``.
    """
    conn = _get_connection()
    if conn is None:
        return []
    try:
        r = conn.execute(
            "MATCH (repo:Repo) WHERE repo.name = $name RETURN repo.id",
            {"name": repo_name},
        )
        if not r.has_next():
            return []
        repo_id = r.get_next()[0]

        # Persistence write patterns (method names that signal DB write)
        write_patterns = {"save", "saveAll", "persist", "merge", "insert", "saveAndFlush", "store"}
        # Persistence read patterns (signal a DB read)
        read_patterns = {"findById", "findAll", "findOne", "getById", "getOne",
                         "findAllById", "executeQuery", "createQuery", "query", "load", "get"}

        # Collect all methods and their CALLS targets
        r_m = conn.execute(
            "MATCH (m:Method)-[:CALLS]->(s:Method) "
            "WHERE m.repo_id = $rid "
            "RETURN m.fqn, m.id, s.name, s.fqn",
            {"rid": repo_id},
        )
        write_methods: dict[str, str] = {}   # method_fqn → callee_name (write)
        read_methods: dict[str, tuple[str, str]] = {}  # method_fqn → (callee_name, file_path)

        rows = []
        while r_m.has_next():
            rows.append(r_m.get_next())

        for caller_fqn, caller_id, callee_name, callee_fqn in rows:
            if callee_name in write_patterns:
                write_methods[caller_fqn] = callee_name
            if callee_name in read_patterns:
                read_methods[caller_fqn] = (callee_name, "")

        if not write_methods or not read_methods:
            return []

        # Build entity-to-writer and entity-to-reader maps via EntityRelation
        r_er = conn.execute(
            "MATCH (c:Class)-[:HAS_RELATION]->(er:EntityRelation) "
            "WHERE c.repo_id = $rid "
            "RETURN c.fqn, er.target_class_fqn",
            {"rid": repo_id},
        )
        entity_classes: set[str] = set()
        while r_er.has_next():
            src_fqn, tgt_fqn = r_er.get_next()
            entity_classes.add(src_fqn)
            entity_classes.add(tgt_fqn)

        # Get file paths for read methods
        r_fp = conn.execute(
            "MATCH (m:Method) WHERE m.repo_id = $rid "
            "MATCH (f:File) WHERE f.id = m.file_id "
            "RETURN m.fqn, f.path",
            {"rid": repo_id},
        )
        fqn_to_path: dict[str, str] = {}
        while r_fp.has_next():
            fqn, path = r_fp.get_next()
            fqn_to_path[fqn] = path

        # Pair up write_methods with read_methods that share a class hierarchy context
        # Since we don't have full type inference, we use heuristics:
        # both belong to classes that reference the same entity type
        results: list[dict] = []
        for write_fqn in write_methods:
            for read_fqn in read_methods:
                if write_fqn == read_fqn:
                    continue
                # Heuristic: share a class prefix (same service class)
                write_class = ".".join(write_fqn.split(".")[:-1])
                read_class = ".".join(read_fqn.split(".")[:-1])
                # Flag if they are in the same class or in a class hierarchy
                if write_class and read_class and (write_class == read_class or
                        write_class.split(".")[-1] in read_class or
                        read_class.split(".")[-1] in write_class):
                    results.append({
                        "entity_fqn": write_class,
                        "write_method_fqn": write_fqn,
                        "write_callee": write_methods[write_fqn],
                        "read_method_fqn": read_fqn,
                        "read_callee": read_methods[read_fqn][0],
                        "read_file_path": fqn_to_path.get(read_fqn, ""),
                        "risk_level": "HIGH" if write_class == read_class else "MEDIUM",
                    })

        # Deduplicate and sort by risk
        seen: set[tuple[str, str]] = set()
        deduped: list[dict] = []
        for r2 in results:
            key = (r2["write_method_fqn"], r2["read_method_fqn"])
            if key not in seen:
                seen.add(key)
                deduped.append(r2)
        deduped.sort(key=lambda x: (x["risk_level"], x["entity_fqn"]))
        return deduped
    except Exception as exc:
        log.error("find_second_order_injection(%r): %s", repo_name, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Security Tool S7: generate_security_report
# ---------------------------------------------------------------------------

# OWASP Top 10 2021 / CWE Top 25 / PCI DSS / STIG category mappings
_OWASP_MAPPINGS: dict[str, str] = {
    # method name fragment → OWASP category
    "execute":         "A03:2021-Injection",
    "executeQuery":    "A03:2021-Injection",
    "executeUpdate":   "A03:2021-Injection",
    "createQuery":     "A03:2021-Injection",
    "query":           "A03:2021-Injection",
    "getForEntity":    "A10:2021-Server-Side Request Forgery",
    "postForEntity":   "A10:2021-Server-Side Request Forgery",
    "exchange":        "A10:2021-Server-Side Request Forgery",
    "exec":            "A03:2021-Injection",
    "start":           "A03:2021-Injection",
    "readAllBytes":    "A01:2021-Broken Access Control",
    "newBufferedReader": "A01:2021-Broken Access Control",
}

_CWE_MAPPINGS: dict[str, str] = {
    "execute":         "CWE-89: SQL Injection",
    "executeQuery":    "CWE-89: SQL Injection",
    "createQuery":     "CWE-89: SQL Injection",
    "getForEntity":    "CWE-918: Server-Side Request Forgery",
    "postForEntity":   "CWE-918: Server-Side Request Forgery",
    "exchange":        "CWE-918: Server-Side Request Forgery",
    "exec":            "CWE-78: OS Command Injection",
    "start":           "CWE-78: OS Command Injection",
    "readAllBytes":    "CWE-22: Path Traversal",
}

_PCI_DSS_MAPPINGS: dict[str, str] = {
    "CWE-89: SQL Injection":                  "PCI DSS 6.3.1: Injection flaws",
    "CWE-918: Server-Side Request Forgery":   "PCI DSS 6.3.1: Other flaws",
    "CWE-78: OS Command Injection":           "PCI DSS 6.3.1: Injection flaws",
    "CWE-22: Path Traversal":                 "PCI DSS 6.3.1: Other flaws",
}

_STIG_MAPPINGS: dict[str, str] = {
    "CWE-89: SQL Injection":                  "APSC-DV-002560 CAT I",
    "CWE-918: Server-Side Request Forgery":   "APSC-DV-002510 CAT II",
    "CWE-78: OS Command Injection":           "APSC-DV-002560 CAT I",
    "CWE-22: Path Traversal":                 "APSC-DV-002300 CAT II",
}


@mcp.tool()
def generate_security_report(repo_name: str, framework: str = "owasp") -> list[dict]:
    """Generate a security findings report mapped to a compliance framework.

    This is the Dedalus equivalent of SonarQube Enterprise's OWASP / CWE /
    PCI DSS / STIG security reports.  It aggregates findings from the taint
    analysis and maps each to the requested framework's taxonomy.

    Args:
        repo_name: Repository to analyse.
        framework: One of ``owasp``, ``cwe``, ``pci``, ``stig`` (default: ``owasp``).

    Returns:
        List of dicts, each a finding with framework-specific keys.
        OWASP: ``category``, ``caller_fqn``, ``sink_method``, ``file_path``, ``line_start``.
        CWE:   ``cwe_id``, ``caller_fqn``, ``sink_method``, ``file_path``, ``line_start``.
        PCI:   ``requirement``, ``caller_fqn``, ``sink_method``, ``file_path``.
        STIG:  ``vuln_id``, ``caller_fqn``, ``sink_method``, ``file_path``.
    """
    conn = _get_connection()
    if conn is None:
        return []
    framework = framework.lower()
    if framework not in ("owasp", "cwe", "pci", "stig"):
        return [{"error": f"Unknown framework '{framework}'. Use: owasp, cwe, pci, stig"}]
    try:
        # Re-use find_taint_sinks to collect raw findings
        raw = find_taint_sinks(repo_name)
        results: list[dict] = []
        for finding in raw:
            if "error" in finding:
                continue
            sink = finding.get("sink_method", "")
            short = sink.split(".")[-1]

            owasp = _OWASP_MAPPINGS.get(short, "A00:2021-Uncategorized")
            cwe = _CWE_MAPPINGS.get(short, "CWE-000: Unclassified")
            pci = _PCI_DSS_MAPPINGS.get(cwe, "PCI DSS 6.3.1: Review required")
            stig = _STIG_MAPPINGS.get(cwe, "APSC-DV-000000 Review required")

            if framework == "owasp":
                results.append({
                    "category": owasp,
                    "caller_fqn": finding["caller_fqn"],
                    "sink_method": sink,
                    "file_path": finding["file_path"],
                    "line_start": finding["line_start"],
                })
            elif framework == "cwe":
                results.append({
                    "cwe_id": cwe,
                    "caller_fqn": finding["caller_fqn"],
                    "sink_method": sink,
                    "file_path": finding["file_path"],
                    "line_start": finding["line_start"],
                })
            elif framework == "pci":
                results.append({
                    "requirement": pci,
                    "caller_fqn": finding["caller_fqn"],
                    "sink_method": sink,
                    "file_path": finding["file_path"],
                })
            elif framework == "stig":
                results.append({
                    "vuln_id": stig,
                    "caller_fqn": finding["caller_fqn"],
                    "sink_method": sink,
                    "file_path": finding["file_path"],
                })

        # Sort by category/cwe_id/requirement/vuln_id
        sort_key = {"owasp": "category", "cwe": "cwe_id", "pci": "requirement", "stig": "vuln_id"}[framework]
        results.sort(key=lambda x: x.get(sort_key, ""))
        return results
    except Exception as exc:
        log.error("generate_security_report(%r, %r): %s", repo_name, framework, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Tool 11: index_repo_tool
# ---------------------------------------------------------------------------

@mcp.tool()
def index_repo_tool(
    repo_path: str,
    repo_name: str,
    branch: str = "master",
    force: bool = False,
) -> dict:
    """Index a source repository into the Dedalus knowledge graph.

    After indexing, all other query tools will reflect the new data.

    Args:
        repo_path: Absolute path to the repository root on disk.
        repo_name: Logical name to identify the repo in queries
                   (e.g. ``point-bank-bff``).
        branch: Branch name to tag this index run with (default: ``"master"``).
                Index the same repo under different branch names to compare
                branches side-by-side.
        force: When True, re-parse every file even if blob hashes are unchanged.

    Returns:
        Summary dict with counts: ``repos``, ``files``, ``classes``,
        ``methods``, ``endpoints``, ``rest_calls``, ``call_edges``.
        On failure, returns ``{"error": "<message>"}``.
    """
    try:
        from dedalus.indexer import index_repo  # noqa: PLC0415
    except ImportError as exc:
        return {"error": f"indexer not available: {exc}"}

    try:
        summary = index_repo(repo_path, repo_name, _DB_PATH, branch=branch, force=force)
        _reset_connection()
        log.info("Indexed %r@%r: %s", repo_name, branch, summary)
        return summary
    except Exception as exc:
        log.error("index_repo_tool(%r, %r): %s", repo_path, repo_name, exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def cli() -> None:
    """Entry point called from ``python -m dedalus serve``.

    Starts the MCP server using stdio transport (default for Claude Code).
    """
    log.info("Starting Dedalus MCP server (db=%s)", _DB_PATH)
    mcp.run()


if __name__ == "__main__":
    cli()
