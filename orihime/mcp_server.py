"""Orihime MCP Server — code knowledge graph query tools.

Exposes 11 tools over the MCP protocol (FastMCP):
  10 query tools + 1 index_repo_tool.

Connection modes
----------------
Phase 1 (local):
    Set ORIHIME_DB_PATH to point at a KuzuDB directory (default: ~/.orihime/orihime.db).
    The database is opened lazily on the first tool call and reused.

Phase 2 (team/server, future):
    Set ORIHIME_SERVER_URL to the KuzuDB HTTP endpoint.
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
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s [orihime] %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_DEFAULT_DB_PATH = str(Path.home() / ".orihime" / "orihime.db")

# ORIHIME_SERVER_URL — reserved for Phase 2 (remote KuzuDB HTTP endpoint).
# Not used in Phase 1; present only for forward-compatibility.
_SERVER_URL: str = os.environ.get("ORIHIME_SERVER_URL", "")

_DB_PATH: str = os.environ.get("ORIHIME_DB_PATH", _DEFAULT_DB_PATH)

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
    name="orihime",
    instructions=(
        "Orihime is a code knowledge graph for Java/Kotlin Spring Boot repositories. "
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

    These represent cross-repo or external HTTP calls that Orihime has not yet
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

    This is an Orihime-native equivalent of SonarQube Enterprise "Advanced SAST"
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
        #    method_id → list of (callee_id, callee_name) pairs
        r_calls = conn.execute(
            "MATCH (a:Method)-[c:CALLS]->(b:Method) WHERE a.repo_id = $rid RETURN a.id, b.id, c.callee_name",
            {"rid": repo_id},
        )
        adj: dict[str, list[tuple[str, str]]] = {}
        while r_calls.has_next():
            src, dst, cname = r_calls.get_next()
            adj.setdefault(src, []).append((dst, cname or ""))

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
        # path items are dicts: {method_id, callee_name (name used to reach this hop)}
        results: list[dict] = []
        seen_paths: set[tuple[str, str]] = set()  # (handler_id, sink_id)

        for handler_id, ep_http_method, ep_path in handlers:
            if handler_id not in id_to_fqn:
                continue
            # BFS — each path item: {"mid": str, "callee_name": str}
            start_hop = {"mid": handler_id, "callee_name": ""}
            queue: deque[tuple[str, list[dict]]] = deque([(handler_id, [start_hop])])
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
                            call_chain = [
                                {"fqn": id_to_fqn.get(hop["mid"], hop["mid"]),
                                 "callee_name": hop["callee_name"]}
                                for hop in path
                            ]
                            results.append({
                                "source_handler_fqn": id_to_fqn.get(handler_id, handler_id),
                                "source_endpoint": f"{ep_http_method} {ep_path}",
                                "sink_method_fqn": id_to_fqn.get(current, current),
                                "sink_url_pattern": url_pattern,
                                "sink_http_method": sink_http_m,
                                "path_length": len(path) - 1,
                                "call_chain": call_chain,
                            })
                for callee_id, callee_name in adj.get(current, []):
                    if callee_id not in visited:
                        visited.add(callee_id)
                        queue.append((callee_id, path + [{"mid": callee_id, "callee_name": callee_name}]))

        results.sort(key=lambda x: x["path_length"])
        return results
    except Exception as exc:
        log.error("find_cross_service_taint(%r): %s", repo_name, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# G1 Tool: find_external_calls
# ---------------------------------------------------------------------------

@mcp.tool()
def find_external_calls(repo_name: str) -> list[dict]:
    """Return all calls to methods NOT in the indexed repo (callee has no Method node).

    These are calls to external libraries, frameworks, or unindexed services.
    Returns [{caller_fqn, callee_name, call_count}] sorted by call_count descending.
    Useful for: "what external dependencies does this service actually call at runtime?"

    Args:
        repo_name: The logical name of the indexed repository.

    Returns:
        List of dicts with keys ``caller_fqn``, ``callee_name``, ``call_count``.
        Empty list if the repo is not found or has no external calls.
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

        # Calls where the callee belongs to a different repo (cross-repo CALLS edges)
        counts: dict[tuple[str, str], int] = {}

        r_cross = conn.execute(
            "MATCH (a:Method)-[c:CALLS]->(b:Method) "
            "WHERE a.repo_id = $rid AND b.repo_id <> $rid "
            "RETURN a.fqn, c.callee_name",
            {"rid": repo_id},
        )
        while r_cross.has_next():
            caller_fqn, callee_name = r_cross.get_next()
            key = (caller_fqn, callee_name or "")
            counts[key] = counts.get(key, 0) + 1

        # UNRESOLVED_CALLs are calls to methods not indexed at all
        r_unres = conn.execute(
            "MATCH (a:Method)-[:UNRESOLVED_CALL]->(rc:RestCall) "
            "WHERE a.repo_id = $rid "
            "RETURN a.fqn, rc.callee_name",
            {"rid": repo_id},
        )
        while r_unres.has_next():
            caller_fqn, callee_name = r_unres.get_next()
            key = (caller_fqn, callee_name or "")
            counts[key] = counts.get(key, 0) + 1

        results = [
            {"caller_fqn": caller_fqn, "callee_name": callee_name, "call_count": count}
            for (caller_fqn, callee_name), count in counts.items()
        ]
        results.sort(key=lambda x: x["call_count"], reverse=True)
        return results
    except Exception as exc:
        log.error("find_external_calls(%r): %s", repo_name, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Security Tool S5: find_taint_sinks
# ---------------------------------------------------------------------------

@mcp.tool()
def find_taint_sinks(repo_name: str) -> list[dict]:
    """Find all calls to known dangerous sink methods in the given repository.

    Uses the built-in sink registry (SQL, HTTP clients, exec) merged with any
    custom sinks defined in ``~/.orihime/security.yml``.  This is the custom
    sources/sinks equivalent of SonarQube Enterprise's configurable taint rules.

    Args:
        repo_name: Repository to analyse.

    Returns:
        List of dicts with keys:
            ``caller_fqn``, ``sink_method``, ``file_path``, ``line_start``.
    """
    from orihime.security_config import get_security_config  # noqa: PLC0415
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
                    "caller_arg_pos": -1,
                    "callee_param_pos": -1,
                })

        # Cross-service sinks via CALLS edges to methods whose FQN matches a known sink
        r_calls = conn.execute(
            "MATCH (m:Method)-[c:CALLS]->(s:Method) "
            "WHERE m.repo_id = $rid "
            "MATCH (f:File) WHERE f.id = m.file_id "
            "RETURN m.fqn, s.fqn, s.name, f.path, m.line_start, c.caller_arg_pos, c.callee_param_pos",
            {"rid": repo_id},
        )
        while r_calls.has_next():
            caller_fqn, callee_fqn, callee_name, file_path, line_start, cap, cpp = r_calls.get_next()
            if cfg.is_sink_method(callee_fqn) or cfg.is_sink_method(callee_name):
                results.append({
                    "caller_fqn": caller_fqn,
                    "sink_method": callee_fqn,
                    "file_path": file_path,
                    "line_start": line_start,
                    "sink_category": "configured",
                    "caller_arg_pos": cap if cap is not None else -1,
                    "callee_param_pos": cpp if cpp is not None else -1,
                })

        return results
    except Exception as exc:
        log.error("find_taint_sinks(%r): %s", repo_name, exc)
        return [{"error": str(exc)}]


@mcp.tool()
def find_taint_flows(repo_name: str) -> list[dict]:
    """Return confirmed taint flows where a tainted argument (position 0) flows to a known sink's first parameter.

    Stricter than find_taint_sinks — only returns findings where:
    1. The caller method has a @RequestParam/@RequestBody/@PathVariable parameter (taint source)
    2. The CALLS edge has caller_arg_pos=0 (first argument is passed)
    3. The callee method name matches a known sink

    Returns:
        List of dicts with keys:
            ``source_method_fqn``, ``sink_method_name``, ``caller_arg_pos``,
            ``callee_param_pos``, ``file_path``, ``line_start``, ``owasp_category``.
    """
    from orihime.security_config import get_security_config  # noqa: PLC0415
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

        # Find all methods with taint-source annotations in this repo
        r_sources = conn.execute(
            "MATCH (m:Method) WHERE m.repo_id = $rid AND size(m.annotations) > 0 "
            "MATCH (f:File) WHERE f.id = m.file_id "
            "RETURN m.id, m.fqn, m.annotations, f.path, m.line_start",
            {"rid": repo_id},
        )
        source_method_ids: set[str] = set()
        source_info: dict[str, dict] = {}  # method_id → {fqn, file_path, line_start}
        while r_sources.has_next():
            mid, fqn, annotations, file_path, line_start = r_sources.get_next()
            if any(cfg.is_source_annotation(ann) for ann in (annotations or [])):
                source_method_ids.add(mid)
                source_info[mid] = {"fqn": fqn, "file_path": file_path, "line_start": line_start}

        if not source_method_ids:
            return []

        # Follow CALLS edges from source methods where caller_arg_pos = 0
        results: list[dict] = []
        for source_id in source_method_ids:
            r_calls = conn.execute(
                "MATCH (m:Method)-[c:CALLS]->(s:Method) "
                "WHERE m.id = $mid AND c.caller_arg_pos = 0 "
                "RETURN s.name, s.fqn, c.caller_arg_pos, c.callee_param_pos",
                {"mid": source_id},
            )
            while r_calls.has_next():
                callee_name, callee_fqn, cap, cpp = r_calls.get_next()
                if cfg.is_sink_method(callee_fqn) or cfg.is_sink_method(callee_name or ""):
                    info = source_info[source_id]
                    short = (callee_name or "").split(".")[-1]
                    owasp = _OWASP_MAPPINGS.get(short, "A00:2021-Uncategorized")
                    results.append({
                        "source_method_fqn": info["fqn"],
                        "sink_method_name": callee_fqn or callee_name,
                        "caller_arg_pos": cap if cap is not None else 0,
                        "callee_param_pos": cpp if cpp is not None else 0,
                        "file_path": info["file_path"],
                        "line_start": info["line_start"],
                        "owasp_category": owasp,
                    })

        return results
    except Exception as exc:
        log.error("find_taint_flows(%r): %s", repo_name, exc)
        return [{"error": str(exc)}]


@mcp.tool()
def list_security_config() -> dict:
    """Return the active security configuration (sources, sinks, sanitizers).

    Shows the merged built-in + user-defined rules currently in effect.
    Useful for verifying that custom ``~/.orihime/security.yml`` rules were loaded.

    Returns:
        Dict with keys ``source_annotations``, ``source_methods``,
        ``sink_methods``, ``sanitizer_methods``.
    """
    from orihime.security_config import get_security_config  # noqa: PLC0415
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

    Orihime approximates this by finding:
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

    This is the Orihime equivalent of SonarQube Enterprise's OWASP / CWE /
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
# Security Tool S8a: find_entry_points
# ---------------------------------------------------------------------------

@mcp.tool()
def find_entry_points(repo_name: str) -> list[dict]:
    """Return all methods/endpoints marked as entry points (is_entry_point=true).

    Entry points include HTTP handler methods, @KafkaListener, @Scheduled,
    @JmsListener, and @RabbitListener methods.

    Args:
        repo_name: Repository to query.

    Returns:
        List of dicts with keys ``fqn``, ``file_path``, ``line_start``,
        ``annotations``.
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

        result = conn.execute(
            "MATCH (m:Method) WHERE m.repo_id = $rid AND m.is_entry_point = true "
            "MATCH (f:File) WHERE f.id = m.file_id "
            "RETURN m.fqn AS fqn, f.path AS file_path, m.line_start AS line_start, "
            "       m.annotations AS annotations",
            {"rid": repo_id},
        )
        return _rows(result, ["fqn", "file_path", "line_start", "annotations"])
    except Exception as exc:
        log.error("find_entry_points(%r): %s", repo_name, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Security Tool S8b: find_reachable_sinks
# ---------------------------------------------------------------------------

@mcp.tool()
def find_reachable_sinks(repo_name: str, show_all: bool = False) -> list[dict]:
    """Return taint sinks reachable from entry points via CALLS edges.

    When show_all=False (default), only returns sinks reachable from an entry
    point.  When show_all=True, returns all sinks (same as find_taint_sinks).

    Uses BFS from all entry points through CALLS edges to build a reachable
    method ID set, then filters find_taint_sinks results to only those whose
    caller method is reachable from an entry point.

    Args:
        repo_name: Repository to analyse.
        show_all:  When True, skip reachability filtering and return all sinks.

    Returns:
        List of dicts with keys ``caller_fqn``, ``sink_method``, ``file_path``,
        ``line_start``, ``sink_category``.
    """
    conn = _get_connection()
    if conn is None:
        return []

    if show_all:
        return find_taint_sinks(repo_name)

    try:
        r = conn.execute(
            "MATCH (repo:Repo) WHERE repo.name = $name RETURN repo.id",
            {"name": repo_name},
        )
        if not r.has_next():
            return []
        repo_id = r.get_next()[0]

        # 1. Collect seed method IDs from is_entry_point=true methods
        seed_ids: set[str] = set()
        r_ep_methods = conn.execute(
            "MATCH (m:Method) WHERE m.repo_id = $rid AND m.is_entry_point = true RETURN m.id",
            {"rid": repo_id},
        )
        while r_ep_methods.has_next():
            seed_ids.add(r_ep_methods.get_next()[0])

        # Also collect handler_method_ids from Endpoint nodes (belt-and-suspenders
        # in case is_entry_point was not set during indexing for older DBs)
        r_handlers = conn.execute(
            "MATCH (repo2:Repo)-[:EXPOSES]->(ep:Endpoint) WHERE repo2.id = $rid "
            "RETURN ep.handler_method_id",
            {"rid": repo_id},
        )
        while r_handlers.has_next():
            mid = r_handlers.get_next()[0]
            if mid:
                seed_ids.add(mid)

        if not seed_ids:
            return []

        # 2. Load full CALLS adjacency list for this repo (callee direction)
        r_calls = conn.execute(
            "MATCH (a:Method)-[:CALLS]->(b:Method) WHERE a.repo_id = $rid RETURN a.id, b.id",
            {"rid": repo_id},
        )
        adj: dict[str, list[str]] = {}
        while r_calls.has_next():
            src, dst = r_calls.get_next()
            adj.setdefault(src, []).append(dst)

        # 3. BFS to build reachable method ID set
        reachable: set[str] = set(seed_ids)
        queue: deque[str] = deque(seed_ids)
        while queue:
            current = queue.popleft()
            for callee in adj.get(current, []):
                if callee not in reachable:
                    reachable.add(callee)
                    queue.append(callee)

        # 4. Get all taint sinks (reuse find_taint_sinks logic directly to avoid
        #    a second DB open — we need the caller method IDs to filter)
        from orihime.security_config import get_security_config  # noqa: PLC0415
        cfg = get_security_config()
        results: list[dict] = []

        # Build fqn → id mapping to resolve caller_fqn back to caller_id
        r_fqn = conn.execute(
            "MATCH (m:Method) WHERE m.repo_id = $rid RETURN m.fqn, m.id",
            {"rid": repo_id},
        )
        fqn_to_id: dict[str, str] = {}
        while r_fqn.has_next():
            fqn, mid = r_fqn.get_next()
            fqn_to_id[fqn] = mid

        # Check UNRESOLVED_CALL nodes whose callee_name matches a known sink
        r_rc = conn.execute(
            "MATCH (m:Method)-[:UNRESOLVED_CALL]->(rc:RestCall) "
            "WHERE m.repo_id = $rid "
            "MATCH (f:File) WHERE f.id = m.file_id "
            "RETURN m.fqn, m.id, rc.callee_name, f.path, m.line_start",
            {"rid": repo_id},
        )
        while r_rc.has_next():
            caller_fqn, caller_id, callee_name, file_path, line_start = r_rc.get_next()
            if callee_name and cfg.is_sink_method(callee_name) and caller_id in reachable:
                results.append({
                    "caller_fqn": caller_fqn,
                    "sink_method": callee_name,
                    "file_path": file_path,
                    "line_start": line_start,
                    "sink_category": "custom",
                })

        # Cross-service sinks via CALLS edges to methods whose FQN matches a known sink
        r_c = conn.execute(
            "MATCH (m:Method)-[:CALLS]->(s:Method) "
            "WHERE m.repo_id = $rid "
            "MATCH (f:File) WHERE f.id = m.file_id "
            "RETURN m.fqn, m.id, s.fqn, s.name, f.path, m.line_start",
            {"rid": repo_id},
        )
        while r_c.has_next():
            caller_fqn, caller_id, callee_fqn, callee_name, file_path, line_start = r_c.get_next()
            if (cfg.is_sink_method(callee_fqn) or cfg.is_sink_method(callee_name)) and caller_id in reachable:
                results.append({
                    "caller_fqn": caller_fqn,
                    "sink_method": callee_fqn,
                    "file_path": file_path,
                    "line_start": line_start,
                    "sink_category": "configured",
                })

        return results
    except Exception as exc:
        log.error("find_reachable_sinks(%r): %s", repo_name, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Tool G7: find_complexity_hints
# ---------------------------------------------------------------------------

@mcp.tool()
def find_complexity_hints(repo_name: str, min_severity: str = "medium") -> list[dict]:
    """Return methods with complexity hints, sorted by CALLS in-degree descending.

    Detects static complexity patterns stored on Method nodes during indexing:
    O(n2)-candidate, O(n2)-list-scan, recursive, n+1-risk, unbounded-query.

    Args:
        repo_name:    Repository to query.
        min_severity: Severity filter:
                      ``"low"``    — include all hints
                      ``"medium"`` — exclude hints that are ONLY ``recursive``
                      ``"high"``   — only include hints containing ``O(n2)`` or ``n+1-risk``

    Returns:
        List of dicts: ``method_fqn``, ``file_path``, ``line_start``,
        ``complexity_hint``, ``call_degree``.
        Sorted by ``call_degree`` descending (most-called methods first).
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

        # Fetch all methods with a non-empty complexity_hint
        r_m = conn.execute(
            "MATCH (m:Method) WHERE m.repo_id = $rid AND m.complexity_hint <> '' "
            "MATCH (f:File) WHERE f.id = m.file_id "
            "RETURN m.id, m.fqn, f.path, m.line_start, m.complexity_hint",
            {"rid": repo_id},
        )
        methods_raw = _rows(r_m, ["method_id", "method_fqn", "file_path", "line_start", "complexity_hint"])

        if not methods_raw:
            return []

        # Apply severity filter
        filtered: list[dict] = []
        for row in methods_raw:
            hint = row["complexity_hint"]
            if min_severity == "high":
                if "O(n2)" not in hint and "n+1-risk" not in hint:
                    continue
            elif min_severity == "medium":
                # Exclude if hint is ONLY "recursive"
                tags = {t.strip() for t in hint.split(",") if t.strip()}
                if tags == {"recursive"}:
                    continue
            # "low" includes everything
            filtered.append(row)

        if not filtered:
            return []

        # Compute call in-degree (number of incoming CALLS edges) for each method
        r_deg = conn.execute(
            "MATCH (caller:Method)-[:CALLS]->(callee:Method) "
            "WHERE callee.repo_id = $rid "
            "RETURN callee.id, count(*) AS degree",
            {"rid": repo_id},
        )
        degree_map: dict[str, int] = {}
        while r_deg.has_next():
            mid, deg = r_deg.get_next()
            degree_map[mid] = deg

        results: list[dict] = []
        for row in filtered:
            results.append({
                "method_fqn": row["method_fqn"],
                "file_path": row["file_path"],
                "line_start": row["line_start"],
                "complexity_hint": row["complexity_hint"],
                "call_degree": degree_map.get(row["method_id"], 0),
            })

        results.sort(key=lambda x: x["call_degree"], reverse=True)
        return results
    except Exception as exc:
        log.error("find_complexity_hints(%r): %s", repo_name, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# G10 Tool: find_io_fanout
# ---------------------------------------------------------------------------

@mcp.tool()
def find_io_fanout(repo_name: str, min_total: int = 2) -> list[dict]:
    """Return entry-point methods ranked by I/O call count, with serial/parallel breakdown.

    For each HTTP/Kafka/Scheduled entry point, reports the total number of I/O
    operations (DB + HTTP + cache) detectable in its method body, split into
    serial (latency adds) and parallel (latency = max of group).

    If perf data has been ingested via ingest_perf_results, also estimates
    latency_floor_ms = sum(serial p99s) + max(parallel p99s).

    Args:
        repo_name: Repository to query.
        min_total: Only return methods with at least this many I/O calls (default 2).

    Returns:
        List of dicts: endpoint_path, http_method, handler_fqn, file_path,
        line_start, total_io, serial_io, parallel_io, parallel_wrapper, latency_floor_ms.
        Sorted by total_io descending.
    """
    conn = _get_connection()
    if conn is None:
        return []
    try:
        # 1. Resolve repo_id
        r = conn.execute(
            "MATCH (repo:Repo) WHERE repo.name = $name RETURN repo.id",
            {"name": repo_name},
        )
        if not r.has_next():
            return []
        repo_id = r.get_next()[0]

        # 2. Query all methods with io_fanout >= min_total, regardless of entry_point flag
        r_m = conn.execute(
            "MATCH (m:Method) WHERE "
            "m.repo_id = $rid AND m.io_fanout >= $min_total "
            "MATCH (f:File) WHERE f.id = m.file_id "
            "RETURN m.id, m.fqn, m.line_start, m.io_fanout, "
            "m.io_serial_count, m.io_parallel_count, m.io_parallel_wrapper, f.path",
            {"rid": repo_id, "min_total": min_total},
        )
        methods_raw = _rows(
            r_m,
            [
                "method_id", "handler_fqn", "line_start", "total_io",
                "serial_io", "parallel_io", "parallel_wrapper", "file_path",
            ],
        )

        if not methods_raw:
            return []

        # 3. For each method, look up its Endpoint node
        results: list[dict] = []
        for row in methods_raw:
            mid = row["method_id"]
            endpoint_path = ""
            http_method_str = ""

            r_ep = conn.execute(
                "MATCH (e:Endpoint) WHERE e.handler_method_id = $mid RETURN e.path, e.http_method",
                {"mid": mid},
            )
            if r_ep.has_next():
                ep_row = r_ep.get_next()
                endpoint_path = ep_row[0] or ""
                http_method_str = ep_row[1] or ""

            # 4. Try to compute latency_floor_ms via PerfSample OBSERVED_AT edges
            latency_floor_ms = None
            r_perf = conn.execute(
                "MATCH (m:Method)-[:OBSERVED_AT]->(ps:PerfSample) "
                "WHERE m.id = $mid RETURN ps.p99_ms",
                {"mid": mid},
            )
            perf_samples: list[float] = []
            while r_perf.has_next():
                p99 = r_perf.get_next()[0]
                if p99 is not None:
                    perf_samples.append(float(p99))

            if perf_samples:
                serial_count = row["serial_io"] or 0
                parallel_count = row["parallel_io"] or 0
                total_io = row["total_io"] or 0
                if total_io > 0 and perf_samples:
                    # Distribute p99 samples proportionally
                    # serial_floor = sum of serial p99s (estimate: avg p99 * serial_count)
                    avg_p99 = sum(perf_samples) / len(perf_samples)
                    serial_floor = avg_p99 * serial_count
                    parallel_floor = avg_p99 if parallel_count > 0 else 0.0
                    latency_floor_ms = serial_floor + parallel_floor

            results.append({
                "endpoint_path": endpoint_path,
                "http_method": http_method_str,
                "handler_fqn": row["handler_fqn"],
                "file_path": row["file_path"],
                "line_start": row["line_start"],
                "total_io": row["total_io"],
                "serial_io": row["serial_io"],
                "parallel_io": row["parallel_io"],
                "parallel_wrapper": row["parallel_wrapper"],
                "latency_floor_ms": latency_floor_ms,
            })

        results.sort(key=lambda x: x["total_io"] or 0, reverse=True)
        return results
    except Exception as exc:
        log.error("find_io_fanout(%r): %s", repo_name, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# G8 Tool 1: ingest_perf_results
# ---------------------------------------------------------------------------

@mcp.tool()
def ingest_perf_results(repo_name: str, file_path: str) -> dict:
    """Ingest a Gatling/JMeter/JSON perf results file into the graph.

    Creates PerfSample nodes and OBSERVED_AT edges to matching Method nodes.

    Args:
        repo_name: The logical name of the indexed repository.
        file_path: Absolute path to the perf results file
                   (.log = Gatling, .xml = JMeter, .json = simple JSON).

    Returns:
        Dict with keys ``ingested``, ``matched_methods``, ``unmatched``.
        On failure, returns ``{"error": "<message>"}``.
    """
    import hashlib as _hashlib  # noqa: PLC0415
    from orihime.perf_ingest import parse_perf_file  # noqa: PLC0415

    conn = _get_connection()
    if conn is None:
        return {"error": "No database found — index a repo first."}

    try:
        # Resolve repo_id
        r = conn.execute(
            "MATCH (repo:Repo) WHERE repo.name = $name RETURN repo.id",
            {"name": repo_name},
        )
        if not r.has_next():
            return {"error": f"Repo {repo_name!r} not found."}
        repo_id = r.get_next()[0]

        samples = parse_perf_file(file_path)

        ingested = 0
        matched_methods = 0
        unmatched = 0

        for sample in samples:
            endpoint_fqn = sample["endpoint_fqn"]
            sample_id = _hashlib.md5(f"{repo_id}:{endpoint_fqn}".encode()).hexdigest()

            # Upsert PerfSample node (delete old if exists to allow re-ingest)
            conn.execute(
                "MATCH (ps:PerfSample) WHERE ps.id = $id DELETE ps",
                {"id": sample_id},
            )
            conn.execute(
                "CREATE (:PerfSample {"
                "id: $id, endpoint_fqn: $endpoint_fqn, p50_ms: $p50_ms, "
                "p99_ms: $p99_ms, rps: $rps, sample_time: $sample_time, "
                "source: $source, repo_id: $repo_id"
                "})",
                {
                    "id": sample_id,
                    "endpoint_fqn": endpoint_fqn,
                    "p50_ms": float(sample["p50_ms"]),
                    "p99_ms": float(sample["p99_ms"]),
                    "rps": float(sample["rps"]),
                    "sample_time": sample["sample_time"],
                    "source": sample["source"],
                    "repo_id": repo_id,
                },
            )
            ingested += 1

            # Try exact FQN match first
            matched = False
            r_exact = conn.execute(
                "MATCH (m:Method) WHERE m.repo_id = $rid AND m.fqn = $fqn RETURN m.id",
                {"rid": repo_id, "fqn": endpoint_fqn},
            )
            method_ids: list[str] = []
            while r_exact.has_next():
                method_ids.append(r_exact.get_next()[0])

            # Substring match on method name if no exact FQN match
            if not method_ids:
                # endpoint_fqn may be just the method name or a partial path
                short_name = endpoint_fqn.split(".")[-1].split("/")[-1]
                if short_name:
                    r_sub = conn.execute(
                        "MATCH (m:Method) WHERE m.repo_id = $rid AND m.name = $name RETURN m.id",
                        {"rid": repo_id, "name": short_name},
                    )
                    while r_sub.has_next():
                        method_ids.append(r_sub.get_next()[0])

            for mid in method_ids:
                # Remove stale OBSERVED_AT edge for this method/sample pair before re-creating
                conn.execute(
                    "MATCH (m:Method)-[r:OBSERVED_AT]->(ps:PerfSample) "
                    "WHERE m.id = $mid AND ps.id = $psid DELETE r",
                    {"mid": mid, "psid": sample_id},
                )
                conn.execute(
                    "MATCH (m:Method), (ps:PerfSample) "
                    "WHERE m.id = $mid AND ps.id = $psid "
                    "CREATE (m)-[:OBSERVED_AT]->(ps)",
                    {"mid": mid, "psid": sample_id},
                )
                matched = True

            if matched:
                matched_methods += 1
            else:
                unmatched += 1

        return {"ingested": ingested, "matched_methods": matched_methods, "unmatched": unmatched}

    except Exception as exc:
        log.error("ingest_perf_results(%r, %r): %s", repo_name, file_path, exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# G8 Tool 2: find_hotspots
# ---------------------------------------------------------------------------

# Hint weight map used by find_hotspots
_HINT_WEIGHTS: dict[str, float] = {
    "O(n2)-candidate": 3.0,
    "O(n2)-list-scan": 2.5,
    "n+1-risk":        2.0,
    "unbounded-query": 1.5,
    "recursive":       1.0,
}


def _max_hint_weight(complexity_hint: str) -> float:
    """Return the highest hint weight from a comma-separated complexity_hint string."""
    if not complexity_hint:
        return 0.0
    tags = [t.strip() for t in complexity_hint.split(",") if t.strip()]
    return max((_HINT_WEIGHTS.get(tag, 0.5) for tag in tags), default=0.5)


@mcp.tool()
def find_hotspots(repo_name: str) -> list[dict]:
    """Return methods ranked by composite risk: complexity_hint x p99.

    Methods with both a complexity hint AND high p99 latency are ranked highest.
    Methods that have a complexity hint but no perf data are included with
    p99_ms=null and risk_score = hint_weight * 100.

    Args:
        repo_name: The logical name of the indexed repository.

    Returns:
        List of dicts: ``method_fqn``, ``complexity_hint``, ``p99_ms``,
        ``p50_ms``, ``risk_score``, ``file_path``, ``line_start``.
        Sorted by risk_score descending.
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

        # Methods with complexity hints
        r_m = conn.execute(
            "MATCH (m:Method) WHERE m.repo_id = $rid AND m.complexity_hint <> '' "
            "MATCH (f:File) WHERE f.id = m.file_id "
            "RETURN m.id, m.fqn, m.complexity_hint, f.path, m.line_start",
            {"rid": repo_id},
        )
        method_rows = _rows(r_m, ["method_id", "fqn", "complexity_hint", "file_path", "line_start"])

        if not method_rows:
            return []

        # PerfSample data keyed by method_id
        perf_by_method: dict[str, dict] = {}
        r_perf = conn.execute(
            "MATCH (m:Method)-[:OBSERVED_AT]->(ps:PerfSample) "
            "WHERE m.repo_id = $rid "
            "RETURN m.id, ps.p99_ms, ps.p50_ms",
            {"rid": repo_id},
        )
        while r_perf.has_next():
            mid, p99, p50 = r_perf.get_next()
            # Keep the worst (highest p99) reading per method
            if mid not in perf_by_method or p99 > perf_by_method[mid]["p99_ms"]:
                perf_by_method[mid] = {"p99_ms": p99, "p50_ms": p50}

        results: list[dict] = []
        for row in method_rows:
            mid = row["method_id"]
            hint = row["complexity_hint"]
            weight = _max_hint_weight(hint)
            perf = perf_by_method.get(mid)
            if perf is not None:
                p99 = perf["p99_ms"]
                p50 = perf["p50_ms"]
                risk_score = p99 * weight
            else:
                p99 = None
                p50 = None
                risk_score = weight * 100.0
            results.append({
                "method_fqn": row["fqn"],
                "complexity_hint": hint,
                "p99_ms": p99,
                "p50_ms": p50,
                "risk_score": risk_score,
                "file_path": row["file_path"],
                "line_start": row["line_start"],
            })

        results.sort(key=lambda x: x["risk_score"], reverse=True)
        return results
    except Exception as exc:
        log.error("find_hotspots(%r): %s", repo_name, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# G8 Tool 3: estimate_capacity
# ---------------------------------------------------------------------------

_THREAD_POOL_SIZE = 200  # Spring Boot Tomcat default


def _read_thread_pool_size(root_path: str) -> int:
    """Read the actual thread pool size from the repo's Spring Boot config files.

    Checks (in priority order):
      1. src/main/resources/application.properties
      2. src/main/resources/application.yml
      3. src/main/resources/application.yaml

    Keys checked (first match wins):
      - server.tomcat.threads.max
      - server.undertow.threads.worker
      - server.netty.worker-count
      - spring.task.execution.pool.max-size

    Returns _THREAD_POOL_SIZE (200) if no config file exists, the key is
    absent, the value cannot be parsed as int, or any error occurs.
    """
    if not root_path:
        return _THREAD_POOL_SIZE

    # --- .properties file ---
    try:
        props_path = root_path.rstrip("/\\") + "/src/main/resources/application.properties"
        import os as _os  # noqa: PLC0415
        if _os.path.isfile(props_path):
            _PROPS_KEYS = [
                "server.tomcat.threads.max",
                "server.undertow.threads.worker",
                "server.netty.worker-count",
                "spring.task.execution.pool.max-size",
            ]
            props: dict[str, str] = {}
            with open(props_path, encoding="utf-8") as _fh:
                for line in _fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, _, v = line.partition("=")
                        props[k.strip()] = v.strip()
            for key in _PROPS_KEYS:
                if key in props:
                    try:
                        return int(props[key])
                    except (ValueError, TypeError):
                        continue
    except Exception:
        pass

    # --- .yml / .yaml files ---
    try:
        import yaml as _yaml  # noqa: PLC0415
    except ImportError:
        return _THREAD_POOL_SIZE

    _YAML_KEY_PATHS = [
        # (dotted key, nested access lambda)
        ("server.tomcat.threads.max",
         lambda d: (d.get("server") or {}).get("tomcat", {}).get("threads", {}).get("max")),
        ("server.undertow.threads.worker",
         lambda d: (d.get("server") or {}).get("undertow", {}).get("threads", {}).get("worker")),
        ("server.netty.worker-count",
         lambda d: (d.get("server") or {}).get("netty", {}).get("worker-count")),
        ("spring.task.execution.pool.max-size",
         lambda d: ((d.get("spring") or {}).get("task") or {})
                   .get("execution", {}).get("pool", {}).get("max-size")),
    ]

    for yml_name in ("application.yml", "application.yaml"):
        try:
            yml_path = root_path.rstrip("/\\") + "/src/main/resources/" + yml_name
            if not _os.path.isfile(yml_path):
                continue
            with open(yml_path, encoding="utf-8") as _fh:
                data = _yaml.safe_load(_fh)
            if not isinstance(data, dict):
                continue
            for _key, accessor in _YAML_KEY_PATHS:
                try:
                    val = accessor(data)
                    if val is not None:
                        return int(val)
                except (ValueError, TypeError, AttributeError):
                    continue
        except Exception:
            continue

    return _THREAD_POOL_SIZE


@mcp.tool()
def estimate_capacity(repo_name: str) -> list[dict]:
    """Estimate capacity per endpoint using Little's Law.

    concurrency = RPS x (p99_ms / 1000)
    saturation_rps = thread_pool_size / (p99_ms / 1000)

    Risk levels (based on current_rps / saturation_rps):
      CRITICAL  > 80%
      HIGH      > 60%
      MEDIUM    > 40%
      LOW       otherwise

    Args:
        repo_name: The logical name of the indexed repository.

    Returns:
        List of dicts: ``endpoint_fqn``, ``current_rps``, ``p99_ms``,
        ``saturation_rps``, ``ceiling_concurrency``, ``risk_level``.
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

        r_root = conn.execute(
            "MATCH (repo:Repo) WHERE repo.id = $rid RETURN repo.root_path",
            {"rid": repo_id},
        )
        root_path = r_root.get_next()[0] if r_root.has_next() else ""
        thread_pool_size = _read_thread_pool_size(root_path)

        r_ps = conn.execute(
            "MATCH (ps:PerfSample) WHERE ps.repo_id = $rid "
            "RETURN ps.endpoint_fqn, ps.rps, ps.p99_ms",
            {"rid": repo_id},
        )
        rows = _rows(r_ps, ["endpoint_fqn", "current_rps", "p99_ms"])

        results: list[dict] = []
        for row in rows:
            p99_ms = row["p99_ms"]
            current_rps = row["current_rps"]
            if p99_ms is None or p99_ms <= 0:
                continue
            p99_s = p99_ms / 1000.0
            saturation_rps = thread_pool_size / p99_s
            ceiling_concurrency = current_rps * p99_s
            ratio = current_rps / saturation_rps if saturation_rps > 0 else 0.0
            if ratio > 0.8:
                risk_level = "CRITICAL"
            elif ratio > 0.6:
                risk_level = "HIGH"
            elif ratio > 0.4:
                risk_level = "MEDIUM"
            else:
                risk_level = "LOW"
            results.append({
                "endpoint_fqn": row["endpoint_fqn"],
                "current_rps": current_rps,
                "p99_ms": p99_ms,
                "saturation_rps": saturation_rps,
                "ceiling_concurrency": ceiling_concurrency,
                "risk_level": risk_level,
                "thread_pool_size": thread_pool_size,
            })

        results.sort(key=lambda x: x["current_rps"] / x["saturation_rps"] if x["saturation_rps"] > 0 else 0, reverse=True)
        return results
    except Exception as exc:
        log.error("estimate_capacity(%r): %s", repo_name, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# G8 Tool 4: find_cascade_risk
# ---------------------------------------------------------------------------

@mcp.tool()
def find_cascade_risk(repo_name: str) -> list[dict]:
    """Find upstream endpoints at cascade risk from saturated downstream services.

    Walks CALLS_REST edges: if Method A (in repo_name) calls Endpoint B (in
    another repo) and B's corresponding PerfSample has a lower saturation_rps
    than A's current_rps, A is flagged as being at cascade risk.

    saturation_rps for endpoint B = thread_pool_size / (p99_ms / 1000).

    Args:
        repo_name: The logical name of the upstream repository to analyse.

    Returns:
        List of dicts: ``upstream_method_fqn``, ``downstream_endpoint``,
        ``downstream_saturation_rps``, ``upstream_current_rps``,
        ``risk`` (``"SATURATED"`` or ``"NEAR_SATURATION"``).
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

        # Find all CALLS_REST edges from methods in this repo to Endpoints in other repos
        r_cr = conn.execute(
            "MATCH (m:Method)-[:CALLS_REST]->(ep:Endpoint) "
            "WHERE m.repo_id = $rid AND ep.repo_id <> $rid "
            "RETURN m.fqn, m.id, ep.path, ep.http_method, ep.repo_id",
            {"rid": repo_id},
        )
        calls_rest_rows = _rows(r_cr, ["method_fqn", "method_id", "ep_path", "ep_http_method", "ep_repo_id"])

        if not calls_rest_rows:
            return []

        # Load upstream PerfSamples (current_rps of methods in this repo)
        # We look for OBSERVED_AT on the upstream method
        r_up_perf = conn.execute(
            "MATCH (m:Method)-[:OBSERVED_AT]->(ps:PerfSample) "
            "WHERE m.repo_id = $rid "
            "RETURN m.id, ps.rps",
            {"rid": repo_id},
        )
        upstream_rps: dict[str, float] = {}
        while r_up_perf.has_next():
            mid, rps = r_up_perf.get_next()
            upstream_rps[mid] = rps

        # Load downstream PerfSamples (by endpoint_fqn matching ep path or method fqn)
        # Collect all downstream repo_ids
        downstream_repo_ids = {row["ep_repo_id"] for row in calls_rest_rows}

        down_perf: dict[str, dict] = {}  # endpoint_fqn -> {p99_ms, rps}
        for dr_id in downstream_repo_ids:
            r_dp = conn.execute(
                "MATCH (ps:PerfSample) WHERE ps.repo_id = $rid "
                "RETURN ps.endpoint_fqn, ps.p99_ms, ps.rps",
                {"rid": dr_id},
            )
            while r_dp.has_next():
                efqn, p99, rps = r_dp.get_next()
                if p99 and p99 > 0:
                    down_perf[efqn] = {"p99_ms": p99, "rps": rps}

        results: list[dict] = []
        for row in calls_rest_rows:
            method_fqn = row["method_fqn"]
            method_id = row["method_id"]
            ep_label = f"{row['ep_http_method']} {row['ep_path']}"
            ep_path = row["ep_path"]

            # Try to find downstream perf data by path substring match
            ds_data = down_perf.get(ep_path)
            if ds_data is None:
                # Try any key that contains the path
                for k, v in down_perf.items():
                    if ep_path in k or k in ep_path:
                        ds_data = v
                        break
            if ds_data is None:
                continue

            p99_ms = ds_data["p99_ms"]
            if p99_ms <= 0:
                continue
            saturation_rps = _THREAD_POOL_SIZE / (p99_ms / 1000.0)

            current_rps = upstream_rps.get(method_id, 0.0)
            if current_rps <= 0:
                continue

            ratio = current_rps / saturation_rps if saturation_rps > 0 else 0.0
            if ratio > 1.0:
                risk = "SATURATED"
            elif ratio > 0.8:
                risk = "NEAR_SATURATION"
            else:
                continue  # not at risk

            results.append({
                "upstream_method_fqn": method_fqn,
                "downstream_endpoint": ep_label,
                "downstream_saturation_rps": saturation_rps,
                "upstream_current_rps": current_rps,
                "risk": risk,
            })

        results.sort(key=lambda x: x["upstream_current_rps"] / x["downstream_saturation_rps"]
                     if x["downstream_saturation_rps"] > 0 else 0, reverse=True)
        return results
    except Exception as exc:
        log.error("find_cascade_risk(%r): %s", repo_name, exc)
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Security Tool S11: find_license_violations
# ---------------------------------------------------------------------------

@mcp.tool()
def find_license_violations(
    repo_name: str,
    allowed: list[str] | None = None,
    license_overrides: dict[str, str] | None = None,
) -> list[dict]:
    """Check dependencies in the repo for license compliance.

    Looks for pom.xml and build.gradle/build.gradle.kts in the repo root.
    Queries Maven Central for each dependency's license.

    Args:
        repo_name: The logical name of the indexed repository.
        allowed:   List of SPDX license IDs to allow
                   (default: MIT, Apache-2.0, BSD-*, ISC, etc.).
        license_overrides: Maps "group:artifact" to a license SPDX string to
                           bypass Maven Central lookups (useful for testing or
                           when a known license is not in Maven Central metadata).

    Returns:
        List of dicts [{group, artifact, version, license, status, reason}]
        where status is "OK", "VIOLATION", "WARNING", or "UNKNOWN".
        Only VIOLATION and WARNING items are returned (OK items filtered out).
    """
    from orihime.license_checker import (  # noqa: PLC0415
        DEFAULT_ALLOWED,
        check_licenses,
        parse_gradle,
        parse_pom_xml,
    )
    from pathlib import Path as _Path  # noqa: PLC0415

    conn = _get_connection()

    # Resolve repo root path from the graph
    root_path: str | None = None
    if conn is not None:
        try:
            r = conn.execute(
                "MATCH (repo:Repo) WHERE repo.name = $name RETURN repo.root_path",
                {"name": repo_name},
            )
            if r.has_next():
                root_path = r.get_next()[0]
        except Exception as exc:
            log.error("find_license_violations: DB lookup failed: %s", exc)

    if root_path is None:
        return [{"error": f"Repo {repo_name!r} not found in the graph. Index it first."}]

    root = _Path(root_path)
    deps: list[dict] = []

    # Collect dependencies from pom.xml and/or Gradle build files
    pom = root / "pom.xml"
    if pom.exists():
        try:
            deps.extend(parse_pom_xml(str(pom)))
        except Exception as exc:
            log.error("find_license_violations: failed to parse pom.xml: %s", exc)

    for gradle_name in ("build.gradle", "build.gradle.kts"):
        gradle = root / gradle_name
        if gradle.exists():
            try:
                deps.extend(parse_gradle(str(gradle)))
            except Exception as exc:
                log.error("find_license_violations: failed to parse %s: %s", gradle_name, exc)

    if not deps:
        return []

    # Deduplicate by group:artifact
    seen: set[str] = set()
    unique_deps: list[dict] = []
    for dep in deps:
        key = f"{dep['group']}:{dep['artifact']}"
        if key not in seen:
            seen.add(key)
            unique_deps.append(dep)

    allowed_set = frozenset(allowed) if allowed else DEFAULT_ALLOWED
    results = check_licenses(
        unique_deps,
        allowed=allowed_set,
        license_overrides=license_overrides,
    )

    # Filter to only non-OK results
    return [r for r in results if r["status"] != "OK"]


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
    """Index a source repository into the Orihime knowledge graph.

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
        from orihime.indexer import index_repo  # noqa: PLC0415
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
    """Entry point called from ``python -m orihime serve``.

    Starts the MCP server using stdio transport (default for Claude Code).
    """
    log.info("Starting Orihime MCP server (db=%s)", _DB_PATH)
    mcp.run()


if __name__ == "__main__":
    cli()
