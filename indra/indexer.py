"""Orchestrator: index a repository into KuzuDB.

Public API
----------
index_repo(repo_path, repo_name, db_path, max_workers=None) -> dict

Architecture
------------
Indexing is split into two phases to exploit parallelism while respecting
KuzuDB's single-writer constraint:

Phase 1 — parse+extract (parallel, ProcessPoolExecutor):
    Each worker calls _parse_file(file_path_str, lang, file_id, repo_id) and
    returns a ParseResult containing only plain Python objects (dicts / bytes).
    Workers must NOT import kuzu or hold any DB state.

Phase 2 — write (serial, main process):
    All KuzuDB INSERT calls are made from the main thread after the parallel
    phase completes.  resolve_calls() also runs here (it needs the full
    fqn_index built from all parsed files).

NOTE ON PICKLING: tree-sitter Tree/Parser objects are NOT picklable.  Workers
therefore re-parse inside _parse_file (imports happen inside the function body
to avoid stale module-level state in child processes) and return only the raw
source bytes alongside the extracted dicts.  The main process re-parses each
file cheaply when it needs to run resolve_calls().
"""
from __future__ import annotations

import hashlib
import multiprocessing
import os
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import kuzu

import indra.java_extractor  # noqa: F401 — side-effect: registers JavaExtractor
import indra.kotlin_extractor  # noqa: F401 — side-effect: registers KotlinExtractor
from indra.language import get_extractor, get_parser
from indra.parse_result import ParseResult
from indra.resolver import build_fqn_index, resolve_calls
from indra.schema import init_schema
from indra.walker import walk_repo


# ---------------------------------------------------------------------------
# Top-level picklable worker function (must be at module level for pickle)
# ---------------------------------------------------------------------------

def _parse_file(args: tuple) -> ParseResult:
    """Parse and extract a single source file — runs in a worker process.

    Parameters (packed in *args* to satisfy ProcessPoolExecutor.map style):
        file_path_str : str   — absolute path of the file
        lang          : str   — language key (e.g. "java", "kotlin")
        file_id       : str   — pre-computed md5 hex digest of the path
        repo_id       : str   — pre-computed md5 hex digest of the repo name

    Returns:
        ParseResult with all extracted nodes as plain dicts.

    IMPORTANT: Do NOT import kuzu here.  kuzu objects are not picklable and
    the DB must stay in the main process.  All imports below are safe to
    run in a child process.
    """
    file_path_str, lang, file_id, repo_id = args

    # Import inside the function so child processes get a clean slate and we
    # avoid accidentally pickling module-level state.
    from pathlib import Path as _Path
    from indra.language import get_parser as _get_parser, get_extractor as _get_extractor
    # Trigger extractor registration in the child process
    import indra.java_extractor  # noqa: F401
    import indra.kotlin_extractor  # noqa: F401

    file_path = _Path(file_path_str)
    result = ParseResult(
        file_id=file_id,
        file_path=file_path_str,
        lang=lang,
        repo_id=repo_id,
    )

    src = file_path.read_bytes()
    result.src_bytes = src

    parser = _get_parser(lang)
    tree = parser.parse(src)

    extractor = _get_extractor(lang)
    if extractor is None:
        return result  # src_bytes retained so main process can still resolve calls

    extract_result = extractor.extract(tree, src, file_id, repo_id)
    result.classes = extract_result.classes
    result.methods = extract_result.methods
    result.endpoints = extract_result.endpoints
    result.rest_calls = extract_result.rest_calls

    return result


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
    max_workers: int | None = None,
) -> dict:
    """Index *repo_path* into a KuzuDB database at *db_path*.

    Parameters
    ----------
    repo_path:
        Root directory of the repository to index.
    repo_name:
        Human-readable name used to derive the stable ``repo_id``.
    db_path:
        Directory path for the KuzuDB database.
    max_workers:
        Number of worker processes for the parallel parse+extract phase.
        ``None`` (default) uses ``os.cpu_count()``.
        Pass ``1`` in tests to avoid ProcessPoolExecutor startup overhead.

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
    # 5. Walk repo — collect (file_path, lang, file_id, repo_id) tuples
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

    work_items: list[tuple[str, str, str, str]] = []
    for file_path, lang in walk_repo(repo_path):
        file_id = hashlib.md5(str(file_path).encode()).hexdigest()
        work_items.append((str(file_path), lang, file_id, repo_id))

    # -----------------------------------------------------------------------
    # Phase 1 — parse+extract in parallel worker processes
    # -----------------------------------------------------------------------
    # Results keyed by file_id for the serial write phase.
    parse_results: dict[str, ParseResult] = {}

    if max_workers == 1 or len(work_items) <= 1:
        # Fast path: avoid ProcessPoolExecutor overhead for small repos or tests.
        for item in work_items:
            pr = _parse_file(item)
            parse_results[pr.file_id] = pr
    else:
        # Parallel path: each worker gets one (file_path, lang, file_id, repo_id) tuple.
        # max_workers=None lets the executor choose based on os.cpu_count().
        #
        # Use "spawn" start method explicitly to avoid fork+thread deadlocks on Linux.
        # The default "fork" method is unsafe when the parent process is multi-threaded
        # (e.g. pytest running multiple tests in sequence) because forking a
        # multi-threaded process can deadlock child processes that try to acquire
        # locks held by threads that were not forked.
        mp_ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=max_workers, mp_context=mp_ctx) as executor:
            futures = {executor.submit(_parse_file, item): item for item in work_items}
            for future in as_completed(futures):
                pr = future.result()  # re-raises any worker exception
                parse_results[pr.file_id] = pr

    # -----------------------------------------------------------------------
    # Phase 2 — serial DB writes (main process only)
    # -----------------------------------------------------------------------

    # Accumulate all methods across files for cross-file FQN resolution
    all_methods: list[dict] = []

    for _file_path_str, _lang, file_id, _repo_id in work_items:
        pr = parse_results[file_id]

        # Insert File node
        conn.execute(
            "CREATE (:File {id: $id, path: $path, language: $language, repo_id: $repo_id})",
            {
                "id": file_id,
                "path": pr.file_path,
                "language": pr.lang,
                "repo_id": repo_id,
            },
        )
        counters["files"] += 1

        # Insert Class nodes
        for cls in pr.classes:
            conn.execute(
                "CREATE (:Class {"
                "id: $id, name: $name, fqn: $fqn, file_id: $file_id, "
                "repo_id: $repo_id, is_interface: $is_interface, annotations: $annotations"
                "})",
                cls,
            )
            counters["classes"] += 1

        # Insert Method nodes
        for method in pr.methods:
            conn.execute(
                "CREATE (:Method {"
                "id: $id, name: $name, fqn: $fqn, class_id: $class_id, "
                "file_id: $file_id, repo_id: $repo_id, line_start: $line_start, "
                "is_suspend: $is_suspend, annotations: $annotations"
                "})",
                method,
            )
            counters["methods"] += 1

        all_methods.extend(pr.methods)

        # Insert Endpoint nodes
        for ep in pr.endpoints:
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
        for rc in pr.rest_calls:
            conn.execute(
                "CREATE (:RestCall {"
                "id: $id, http_method: $http_method, url_pattern: $url_pattern, "
                "caller_method_id: $caller_method_id, repo_id: $repo_id"
                "})",
                rc,
            )
            counters["rest_calls"] += 1

    # -----------------------------------------------------------------------
    # Phase 3 — resolve call edges (serial — needs full fqn_index)
    # -----------------------------------------------------------------------
    fqn_index = build_fqn_index(all_methods)
    method_id_set: set[str] = {m["id"] for m in all_methods}

    for file_id, pr in parse_results.items():
        if not pr.methods and not pr.src_bytes:
            continue  # nothing to resolve

        # Re-parse from the cached src_bytes (no disk I/O; tree-sitter is fast).
        # We cannot pass tree objects across process boundaries (not picklable),
        # so we re-parse here in the main process.
        parser = get_parser(pr.lang)
        tree = parser.parse(pr.src_bytes)

        edges = resolve_calls(
            tree,
            pr.src_bytes,
            pr.methods,
            fqn_index,
            file_id,
            repo_id,
        )

        seen_edges: set[tuple[str, str]] = set()
        for edge in edges:
            if edge.edge_type == "CALLS":
                if edge.callee_id not in method_id_set:
                    continue
                pair = (edge.caller_id, edge.callee_id)
                if pair in seen_edges:
                    continue
                seen_edges.add(pair)
                conn.execute(
                    "MATCH (a:Method), (b:Method) "
                    "WHERE a.id = $caller AND b.id = $callee "
                    "CREATE (a)-[:CALLS]->(b)",
                    {"caller": edge.caller_id, "callee": edge.callee_id},
                )
                counters["call_edges"] += 1
            else:
                # UNRESOLVED_CALL — insert a stub RestCall node as the target
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
    # Phase 4 — relationship edges (serial)
    # -----------------------------------------------------------------------

    # CONTAINS_CLASS edges (File → Class) and CONTAINS_METHOD edges (Class → Method)
    for file_id, pr in parse_results.items():
        for cls in pr.classes:
            conn.execute(
                "MATCH (f:File), (c:Class) "
                "WHERE f.id = $fid AND c.id = $cid "
                "CREATE (f)-[:CONTAINS_CLASS]->(c)",
                {"fid": cls["file_id"], "cid": cls["id"]},
            )
        for method in pr.methods:
            conn.execute(
                "MATCH (c:Class), (m:Method) "
                "WHERE c.id = $cid AND m.id = $mid "
                "CREATE (c)-[:CONTAINS_METHOD]->(m)",
                {"cid": method["class_id"], "mid": method["id"]},
            )

    # EXPOSES edges (Repo → Endpoint)
    for file_id, pr in parse_results.items():
        for ep in pr.endpoints:
            conn.execute(
                "MATCH (r:Repo), (e:Endpoint) "
                "WHERE r.id = $rid AND e.id = $eid "
                "CREATE (r)-[:EXPOSES]->(e)",
                {"rid": repo_id, "eid": ep["id"]},
            )

    return counters
