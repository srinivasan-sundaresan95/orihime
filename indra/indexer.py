"""Orchestrator: index a repository into KuzuDB.

Public API
----------
index_repo(repo_path, repo_name, db_path) -> dict
"""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import kuzu

import indra.java_extractor  # noqa: F401 — side-effect: registers JavaExtractor
import indra.kotlin_extractor  # noqa: F401 — side-effect: registers KotlinExtractor
from indra.language import get_extractor, get_parser
from indra.resolver import build_fqn_index, resolve_calls
from indra.schema import init_schema
from indra.walker import walk_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_tables(conn: kuzu.Connection) -> bool:
    """Return True if any tables already exist in the database."""
    result = conn.execute("CALL show_tables() RETURN name")
    return result.has_next()


def _delete_repo_data(conn: kuzu.Connection, repo_id: str) -> None:
    """Delete all graph data for the given repo_id (idempotent)."""
    rid = {"rid": repo_id}

    # --- relationship tables ---
    # Edges FROM Method
    conn.execute(
        "MATCH (a:Method)-[r:CALLS]->(b:Method) WHERE a.repo_id = $rid DELETE r", rid
    )
    conn.execute(
        "MATCH (a:Method)-[r:CALLS_REST]->(b:Endpoint) WHERE a.repo_id = $rid DELETE r", rid
    )
    conn.execute(
        "MATCH (a:Method)-[r:UNRESOLVED_CALL]->(b:RestCall) WHERE a.repo_id = $rid DELETE r", rid
    )
    # Edges FROM File and Class
    conn.execute(
        "MATCH (f:File)-[r:CONTAINS_CLASS]->(c:Class) WHERE f.repo_id = $rid DELETE r", rid
    )
    conn.execute(
        "MATCH (c:Class)-[r:CONTAINS_METHOD]->(m:Method) WHERE c.repo_id = $rid DELETE r", rid
    )
    # Edges FROM Repo
    conn.execute(
        "MATCH (r:Repo)-[e:EXPOSES]->(ep:Endpoint) WHERE r.id = $rid DELETE e", rid
    )

    # --- node tables ---
    conn.execute("MATCH (n:RestCall) WHERE n.repo_id = $rid DELETE n", rid)
    conn.execute("MATCH (n:Endpoint) WHERE n.repo_id = $rid DELETE n", rid)
    conn.execute("MATCH (n:Method) WHERE n.repo_id = $rid DELETE n", rid)
    conn.execute("MATCH (n:Class) WHERE n.repo_id = $rid DELETE n", rid)
    conn.execute("MATCH (n:File) WHERE n.repo_id = $rid DELETE n", rid)
    conn.execute("MATCH (n:Repo) WHERE n.id = $rid DELETE n", rid)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def index_repo(
    repo_path: "Path | str",
    repo_name: str,
    db_path: "Path | str",
) -> dict:
    """Index *repo_path* into a KuzuDB database at *db_path*.

    Returns a summary dict::

        {
            "repos": 1,
            "files": N,
            "classes": N,
            "methods": N,
            "endpoints": N,
            "rest_calls": N,
            "call_edges": N,
        }
    """
    repo_path = Path(repo_path)
    db_path = Path(db_path)

    # Ensure the parent directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)

    # 1. Initialise schema if tables are absent
    if not _has_tables(conn):
        init_schema(conn)

    # 2. Stable repo id
    repo_id = hashlib.md5(repo_name.encode()).hexdigest()

    # 3. Delete stale data for this repo (idempotent re-index)
    _delete_repo_data(conn, repo_id)

    # 4. Insert Repo node
    conn.execute(
        "CREATE (:Repo {id: $id, name: $name, root_path: $root_path})",
        {"id": repo_id, "name": repo_name, "root_path": str(repo_path)},
    )

    # -----------------------------------------------------------------------
    # 5. Walk repo and extract nodes
    # -----------------------------------------------------------------------
    counters = {
        "repos": 1,
        "files": 0,
        "classes": 0,
        "methods": 0,
        "endpoints": 0,
        "rest_calls": 0,
        "call_edges": 0,
    }

    # Accumulate all methods across files for cross-file FQN resolution
    all_methods: list[dict] = []

    # Per-file extraction results keyed by file_id
    file_results: dict[str, tuple[object, bytes, object]] = {}
    # {file_id: (tree, src_bytes, extract_result)}

    for file_path, lang in walk_repo(repo_path):
        file_id = hashlib.md5(str(file_path).encode()).hexdigest()

        # Insert File node
        conn.execute(
            "CREATE (:File {id: $id, path: $path, language: $language, repo_id: $repo_id})",
            {
                "id": file_id,
                "path": str(file_path),
                "language": lang,
                "repo_id": repo_id,
            },
        )
        counters["files"] += 1

        # Parse and extract
        src = file_path.read_bytes()
        parser = get_parser(lang)
        tree = parser.parse(src)

        extractor = get_extractor(lang)
        if extractor is None:
            file_results[file_id] = (tree, src, None)
            continue

        result = extractor.extract(tree, src, file_id, repo_id)
        file_results[file_id] = (tree, src, result)

        # Insert Class nodes
        for cls in result.classes:
            conn.execute(
                "CREATE (:Class {"
                "id: $id, name: $name, fqn: $fqn, file_id: $file_id, "
                "repo_id: $repo_id, is_interface: $is_interface, annotations: $annotations"
                "})",
                cls,
            )
            counters["classes"] += 1

        # Insert Method nodes
        for method in result.methods:
            conn.execute(
                "CREATE (:Method {"
                "id: $id, name: $name, fqn: $fqn, class_id: $class_id, "
                "file_id: $file_id, repo_id: $repo_id, line_start: $line_start, "
                "is_suspend: $is_suspend, annotations: $annotations"
                "})",
                method,
            )
            counters["methods"] += 1

        all_methods.extend(result.methods)

        # Insert Endpoint nodes
        for ep in result.endpoints:
            conn.execute(
                "CREATE (:Endpoint {"
                "id: $id, http_method: $http_method, path: $path, "
                "path_regex: $path_regex, handler_method_id: $handler_method_id, "
                "repo_id: $repo_id"
                "})",
                ep,
            )
            counters["endpoints"] += 1

        # Insert RestCall nodes
        for rc in result.rest_calls:
            conn.execute(
                "CREATE (:RestCall {"
                "id: $id, http_method: $http_method, url_pattern: $url_pattern, "
                "caller_method_id: $caller_method_id, repo_id: $repo_id"
                "})",
                rc,
            )
            counters["rest_calls"] += 1

    # -----------------------------------------------------------------------
    # 6. Resolve call edges
    # -----------------------------------------------------------------------
    fqn_index = build_fqn_index(all_methods)

    # Build a set of valid Method ids for CALLS edges
    method_id_set: set[str] = {m["id"] for m in all_methods}

    for file_id, (tree, src, result) in file_results.items():
        if result is None:
            continue

        edges = resolve_calls(
            tree,
            src,
            result.methods,
            fqn_index,
            file_id,
            repo_id,
        )

        for edge in edges:
            if edge.edge_type == "CALLS":
                # Both caller and callee must exist as Method nodes
                if edge.callee_id not in method_id_set:
                    continue
                conn.execute(
                    "MATCH (a:Method), (b:Method) "
                    "WHERE a.id = $caller AND b.id = $callee "
                    "CREATE (a)-[:CALLS]->(b)",
                    {"caller": edge.caller_id, "callee": edge.callee_id},
                )
                counters["call_edges"] += 1
            else:
                # UNRESOLVED_CALL — callee_id is a fresh uuid, not in DB yet
                # Insert a stub RestCall node to act as the target
                conn.execute(
                    "CREATE (:RestCall {"
                    "id: $id, http_method: $http_method, url_pattern: $url_pattern, "
                    "caller_method_id: $caller_method_id, repo_id: $repo_id"
                    "})",
                    {
                        "id": edge.callee_id,
                        "http_method": "UNKNOWN",
                        "url_pattern": "UNRESOLVED",
                        "caller_method_id": edge.caller_id,
                        "repo_id": repo_id,
                    },
                )
                conn.execute(
                    "MATCH (a:Method), (b:RestCall) "
                    "WHERE a.id = $caller AND b.id = $callee "
                    "CREATE (a)-[:UNRESOLVED_CALL]->(b)",
                    {"caller": edge.caller_id, "callee": edge.callee_id},
                )
                counters["call_edges"] += 1

    # -----------------------------------------------------------------------
    # 7. CONTAINS_CLASS edges (File → Class)
    # -----------------------------------------------------------------------
    for _file_id, (_tree, _src, result) in file_results.items():
        if result is None:
            continue
        for cls in result.classes:
            conn.execute(
                "MATCH (f:File), (c:Class) "
                "WHERE f.id = $fid AND c.id = $cid "
                "CREATE (f)-[:CONTAINS_CLASS]->(c)",
                {"fid": cls["file_id"], "cid": cls["id"]},
            )

        # CONTAINS_METHOD edges (Class → Method)
        for method in result.methods:
            conn.execute(
                "MATCH (c:Class), (m:Method) "
                "WHERE c.id = $cid AND m.id = $mid "
                "CREATE (c)-[:CONTAINS_METHOD]->(m)",
                {"cid": method["class_id"], "mid": method["id"]},
            )

    # -----------------------------------------------------------------------
    # 8. EXPOSES edges (Repo → Endpoint)
    # -----------------------------------------------------------------------
    for _file_id, (_tree, _src, result) in file_results.items():
        if result is None:
            continue
        for ep in result.endpoints:
            conn.execute(
                "MATCH (r:Repo), (e:Endpoint) "
                "WHERE r.id = $rid AND e.id = $eid "
                "CREATE (r)-[:EXPOSES]->(e)",
                {"rid": repo_id, "eid": ep["id"]},
            )

    return counters
