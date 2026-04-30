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
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import kuzu

import dedalus.java_extractor  # noqa: F401 — side-effect: registers JavaExtractor
import dedalus.js_extractor  # noqa: F401 — side-effect: registers JsExtractor
import dedalus.kotlin_extractor  # noqa: F401 — side-effect: registers KotlinExtractor
from dedalus.language import get_extractor, get_parser
from dedalus.parse_result import ParseResult
from dedalus.resolver import build_fqn_index, resolve_calls
from dedalus.schema import init_schema
from dedalus.walker import walk_repo


# ---------------------------------------------------------------------------
# Top-level picklable worker function (must be at module level for pickle)
# ---------------------------------------------------------------------------

def _build_constant_index(work_items: list[tuple]) -> dict[str, str]:
    """Pre-pass: collect all public static final String constants from Java files.

    Returns a merged dict mapping "ClassName.FIELD" → "/path/string" across all
    Java files in the repo.  This enables cross-file constant resolution for
    endpoint annotations like @GetMapping(path = RequestMapping.WALLET_STATUS).
    """
    from pathlib import Path as _Path
    from dedalus.language import get_parser as _get_parser
    import dedalus.java_extractor as _jex

    index: dict[str, str] = {}
    for file_path_str, lang, _file_id, _repo_id in work_items:
        if lang != "java":
            continue
        try:
            src = _Path(file_path_str).read_bytes()
            parser = _get_parser("java")
            tree = parser.parse(src)
            root = tree.root_node
            for node in _jex._walk_all(root):
                if node.type in ("class_declaration", "interface_declaration"):
                    name_node = (
                        node.child_by_field_name("name")
                        or _jex._find_first_child_of_type(node, "identifier")
                    )
                    if name_node is None:
                        continue
                    class_name = _jex._text(name_node, src)
                    body = node.child_by_field_name("body") or _jex._find_first_child_of_type(node, "class_body")
                    if body:
                        index.update(_jex._extract_static_final_strings(body, class_name, src))
        except Exception:
            pass
    return index


def _parse_file(args: tuple) -> ParseResult:
    """Parse and extract a single source file — runs in a worker process.

    Parameters (packed in *args* to satisfy ProcessPoolExecutor.map style):
        file_path_str   : str            — absolute path of the file
        lang            : str            — language key (e.g. "java", "kotlin")
        file_id         : str            — pre-computed md5 hex digest of the path
        repo_id         : str            — pre-computed md5 hex digest of the repo name
        blob_hash       : str            — git blob hash of the file content
        branch_name     : str            — branch name this file is being indexed under
        constant_index  : dict[str, str] — cross-file constant index (may be empty)

    Returns:
        ParseResult with all extracted nodes as plain dicts.

    IMPORTANT: Do NOT import kuzu here.  kuzu objects are not picklable and
    the DB must stay in the main process.  All imports below are safe to
    run in a child process.
    """
    file_path_str, lang, file_id, repo_id, blob_hash, branch_name, constant_index = args

    # Import inside the function so child processes get a clean slate and we
    # avoid accidentally pickling module-level state.
    from pathlib import Path as _Path
    from dedalus.language import get_parser as _get_parser, get_extractor as _get_extractor
    # Trigger extractor registration in the child process
    import dedalus.java_extractor  # noqa: F401
    import dedalus.kotlin_extractor  # noqa: F401
    import dedalus.js_extractor  # noqa: F401

    file_path = _Path(file_path_str)
    result = ParseResult(
        file_id=file_id,
        file_path=file_path_str,
        lang=lang,
        repo_id=repo_id,
        blob_hash=blob_hash,
        branch_name=branch_name,
    )

    src = file_path.read_bytes()
    result.src_bytes = src

    parser = _get_parser(lang)
    tree = parser.parse(src)

    extractor = _get_extractor(lang)
    if extractor is None:
        return result  # src_bytes retained so main process can still resolve calls

    # Pass the cross-file constant index so endpoint annotations that reference
    # static fields defined in other files (e.g. RequestMapping.WALLET_STATUS)
    # are resolved to real path strings.
    extract_kwargs: dict = {}
    if constant_index and lang == "java":
        extract_kwargs["constant_index"] = constant_index
    if lang in ("javascript", "typescript"):
        extract_kwargs["file_path"] = file_path_str
    extract_result = extractor.extract(tree, src, file_id, repo_id, **extract_kwargs)
    result.classes = extract_result.classes
    result.methods = extract_result.methods
    result.endpoints = extract_result.endpoints
    result.rest_calls = extract_result.rest_calls
    result.impl_map = extract_result.impl_map
    result.inheritance_edges = extract_result.inheritance_edges
    result.entity_relations = extract_result.entity_relations

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_blob_hash(file_path: Path) -> str:
    """Return the git blob hash for *file_path*.

    Uses ``git hash-object <path>`` when the file is inside a git repo —
    this is the canonical content hash git uses internally, immune to
    mtime/metadata changes and identical to what ``git ls-files`` reports.

    Falls back to SHA-1 of raw file bytes when git is unavailable or the
    file is outside any git repo (e.g. temp fixtures in tests).
    """
    try:
        result = subprocess.run(
            ["git", "hash-object", str(file_path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Fallback: SHA-1 of raw bytes
    return hashlib.sha1(file_path.read_bytes()).hexdigest()


def _load_stored_hashes(conn: kuzu.Connection, repo_id: str) -> dict[str, str]:
    """Return {file_path: blob_hash} for all File nodes in *repo_id*."""
    r = conn.execute(
        "MATCH (f:File) WHERE f.repo_id = $rid RETURN f.path, f.blob_hash",
        {"rid": repo_id},
    )
    stored: dict[str, str] = {}
    while r.has_next():
        path, blob_hash = r.get_next()
        stored[path] = blob_hash or ""
    return stored


def _delete_file_data(conn: kuzu.Connection, file_path_str: str, repo_id: str) -> None:
    """Delete all graph data for a single file (used during incremental re-index)."""
    r = conn.execute(
        "MATCH (f:File) WHERE f.path = $path AND f.repo_id = $rid RETURN f.id",
        {"path": file_path_str, "rid": repo_id},
    )
    if not r.has_next():
        return
    file_id = r.get_next()[0]
    p = {"fid": file_id}

    # Collect method_ids and class_ids for this file (needed for node cleanup)
    r2 = conn.execute("MATCH (m:Method) WHERE m.file_id = $fid RETURN m.id", p)
    method_ids: list[str] = []
    while r2.has_next():
        method_ids.append(r2.get_next()[0])

    r3 = conn.execute("MATCH (c:Class) WHERE c.file_id = $fid RETURN c.id", p)
    class_ids: list[str] = []
    while r3.has_next():
        class_ids.append(r3.get_next()[0])

    # Edges on methods — KuzuDB requires each param dict to contain ONLY used params
    conn.execute("MATCH (m:Method)-[e:CALLS]->(:Method) WHERE m.file_id = $fid DELETE e", p)
    conn.execute("MATCH (:Method)-[e:CALLS]->(m:Method) WHERE m.file_id = $fid DELETE e", p)
    conn.execute("MATCH (m:Method)-[e:CALLS_REST]->(:Endpoint) WHERE m.file_id = $fid DELETE e", p)
    conn.execute("MATCH (m:Method)-[e:UNRESOLVED_CALL]->(:RestCall) WHERE m.file_id = $fid DELETE e", p)
    conn.execute("MATCH (:Class)-[e:CONTAINS_METHOD]->(m:Method) WHERE m.file_id = $fid DELETE e", p)
    # Inheritance and entity-relation edges on classes in this file
    conn.execute("MATCH (c:Class)-[e:EXTENDS]->(:Class) WHERE c.file_id = $fid DELETE e", p)
    conn.execute("MATCH (:Class)-[e:EXTENDS]->(c:Class) WHERE c.file_id = $fid DELETE e", p)
    conn.execute("MATCH (c:Class)-[e:IMPLEMENTS]->(:Class) WHERE c.file_id = $fid DELETE e", p)
    conn.execute("MATCH (:Class)-[e:IMPLEMENTS]->(c:Class) WHERE c.file_id = $fid DELETE e", p)
    conn.execute("MATCH (c:Class)-[e:HAS_RELATION]->(:EntityRelation) WHERE c.file_id = $fid DELETE e", p)
    conn.execute("MATCH (c:Class)-[e:CONTAINS_CLASS]->() WHERE c.file_id = $fid DELETE e", p)
    conn.execute("MATCH ()-[e:CONTAINS_CLASS]->(c:Class) WHERE c.file_id = $fid DELETE e", p)
    # Node cleanup — orphaned RestCalls/EntityRelations referencing this file's methods/classes
    for mid in method_ids:
        conn.execute(
            "MATCH (n:RestCall) WHERE n.caller_method_id = $mid DELETE n",
            {"mid": mid},
        )
        # Remove EXPOSES edges before deleting Endpoint nodes (KuzuDB requires edge-first delete)
        conn.execute(
            "MATCH (:Repo)-[e:EXPOSES]->(ep:Endpoint) WHERE ep.handler_method_id = $mid DELETE e",
            {"mid": mid},
        )
        conn.execute(
            "MATCH (n:Endpoint) WHERE n.handler_method_id = $mid DELETE n",
            {"mid": mid},
        )
    for cid in class_ids:
        conn.execute(
            "MATCH (n:EntityRelation) WHERE n.source_class_id = $cid DELETE n",
            {"cid": cid},
        )
    conn.execute("MATCH (m:Method) WHERE m.file_id = $fid DELETE m", p)
    conn.execute("MATCH (n:Class) WHERE n.file_id = $fid DELETE n", p)
    conn.execute("MATCH (f:File) WHERE f.id = $fid DELETE f", p)


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
    # Inheritance edges
    conn.execute(
        "MATCH (a:Class)-[r:EXTENDS]->(b:Class) WHERE a.repo_id = $rid DELETE r", rid
    )
    conn.execute(
        "MATCH (a:Class)-[r:IMPLEMENTS]->(b:Class) WHERE a.repo_id = $rid DELETE r", rid
    )

    # Entity relation edges and nodes
    conn.execute("MATCH (c:Class)-[r:HAS_RELATION]->(e:EntityRelation) WHERE c.repo_id = $rid DELETE r", rid)
    conn.execute("MATCH (n:EntityRelation) WHERE n.repo_id = $rid DELETE n", rid)

    # HAS_BRANCH edges and Branch nodes
    conn.execute("MATCH (r:Repo)-[e:HAS_BRANCH]->(b:Branch) WHERE r.id = $rid DELETE e", rid)
    conn.execute("MATCH (n:Branch) WHERE n.repo_id = $rid DELETE n", rid)

    # OBSERVED_AT edges and PerfSample/CapacityEstimate nodes
    conn.execute(
        "MATCH (m:Method)-[r:OBSERVED_AT]->(ps:PerfSample) WHERE m.repo_id = $rid DELETE r", rid
    )
    conn.execute("MATCH (n:PerfSample) WHERE n.repo_id = $rid DELETE n", rid)
    conn.execute("MATCH (n:CapacityEstimate) WHERE n.repo_id = $rid DELETE n", rid)

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
    force: bool = False,
    branch: str = "master",
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
    force:
        When False (default) files whose git blob hash is unchanged since the
        last index run are skipped — making re-index take seconds instead of
        minutes for typical code-review cycles.  Pass True to re-parse every
        file regardless of hash (equivalent to the old behaviour).
    branch:
        Branch name to tag indexed files with (default: ``"master"``).
        Allows the same repo to be indexed at multiple branches and queried
        separately via the ``--branch`` filter.

    Returns a summary dict::

        {
            "repos": 1,
            "files": N,        # total files on disk
            "files_skipped": N, # unchanged files skipped
            "classes": N,
            ...
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

    # 2. Stable repo id and branch id
    repo_id = hashlib.md5(repo_name.encode()).hexdigest()
    branch_id = hashlib.md5(f"{repo_id}:{branch}".encode()).hexdigest()

    # 3. Incremental mode: load hashes stored from the previous index run.
    #    On first index (or --force) stored_hashes is empty → all files parsed.
    stored_hashes: dict[str, str] = {} if force else _load_stored_hashes(conn, repo_id)

    # 4. Full delete only on --force or first-time index; otherwise delete
    #    only changed/removed files below.
    if force or not stored_hashes:
        _delete_repo_data(conn, repo_id)
        conn.execute(
            "CREATE (:Repo {id: $id, name: $name, root_path: $root_path})",
            {"id": repo_id, "name": repo_name, "root_path": str(repo_path)},
        )
    else:
        r = conn.execute("MATCH (r:Repo) WHERE r.id = $rid RETURN r.id", {"rid": repo_id})
        if not r.has_next():
            conn.execute(
                "CREATE (:Repo {id: $id, name: $name, root_path: $root_path})",
                {"id": repo_id, "name": repo_name, "root_path": str(repo_path)},
            )

    # Upsert the Branch node and HAS_BRANCH edge (idempotent — skip if already exists)
    rb = conn.execute("MATCH (b:Branch) WHERE b.id = $bid RETURN b.id", {"bid": branch_id})
    if not rb.has_next():
        conn.execute(
            "CREATE (:Branch {id: $id, name: $name, repo_id: $repo_id})",
            {"id": branch_id, "name": branch, "repo_id": repo_id},
        )
        conn.execute(
            "MATCH (r:Repo), (b:Branch) WHERE r.id = $rid AND b.id = $bid CREATE (r)-[:HAS_BRANCH]->(b)",
            {"rid": repo_id, "bid": branch_id},
        )

    # -----------------------------------------------------------------------
    # 5. Walk repo — compute blob hash per file; skip unchanged in incr. mode
    # -----------------------------------------------------------------------
    counters = {
        "repos": 1,
        "files": 0,
        "files_skipped": 0,
        "classes": 0,
        "methods": 0,
        "endpoints": 0,
        "rest_calls": 0,
        "call_edges": 0,
        "inheritance_edges": 0,
        "entity_relations": 0,
    }

    # work_items_base carries blob_hash as 5th element (used to update File node)
    work_items_base: list[tuple[str, str, str, str, str]] = []
    current_paths: set[str] = set()

    for file_path, lang in walk_repo(repo_path):
        file_path_str = str(file_path)
        current_paths.add(file_path_str)
        blob_hash = _git_blob_hash(file_path)
        file_id = hashlib.md5(file_path_str.encode()).hexdigest()

        if not force and stored_hashes.get(file_path_str) == blob_hash:
            counters["files_skipped"] += 1
            continue

        # Changed or new: purge stale data for this file before re-inserting
        if file_path_str in stored_hashes:
            _delete_file_data(conn, file_path_str, repo_id)

        work_items_base.append((file_path_str, lang, file_id, repo_id, blob_hash, branch))

    # Delete files removed from the repo entirely
    for removed_path in set(stored_hashes) - current_paths:
        _delete_file_data(conn, removed_path, repo_id)

    # Pre-pass: build cross-file constant index from all Java files (changed + unchanged).
    # We must pass unchanged files too so constants defined there remain resolvable.
    all_java_items: list[tuple[str, str, str, str]] = []
    for file_path, lang in walk_repo(repo_path):
        if lang == "java":
            fid = hashlib.md5(str(file_path).encode()).hexdigest()
            all_java_items.append((str(file_path), lang, fid, repo_id))
    constant_index = _build_constant_index(all_java_items)

    # Pack constant_index into each work item tuple for _parse_file.
    # Tuple layout: (path, lang, file_id, repo_id, blob_hash, branch_name, constant_index)
    work_items: list[tuple] = [(*item[:6], constant_index) for item in work_items_base]

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
    # Post Phase-1 — merge all per-file impl_maps into a global impl_index
    # -----------------------------------------------------------------------
    # last one wins — acceptable for P3-1.1; P3-1.2 uses this index to redirect UNRESOLVED edges
    impl_index: dict[str, str] = {}
    for pr in parse_results.values():
        impl_index.update(pr.impl_map)

    # -----------------------------------------------------------------------
    # Phase 2 — serial DB writes (main process only)
    #
    # G5 Fix A — Batch DB Writes (transaction-based)
    # -----------------------------------------------
    # Approach: wrap all per-table node CREATEs in a single BEGIN/COMMIT
    # transaction rather than running each as an autocommit statement.
    # This eliminates KuzuDB's per-row WAL flush overhead and delivers a
    # 10–15× write speedup for large repos (measured: 1 000 rows at
    # 3.39 s autocommit → 0.27 s batched = 12.4× on KuzuDB 0.11.3).
    #
    # Why not COPY FROM DataFrame (Approach A, preferred)?
    #   KuzuDB 0.11.3 raises an assertion error in numpy_type.cpp for any
    #   DataFrame with INT64 columns (KU_UNREACHABLE on line 86).  The bug
    #   also surfaces for all-STRING DataFrames when the parameter is resolved
    #   ("Parameter df not found" runtime error).  Transaction batching
    #   achieves the same order-of-magnitude speedup without the version-
    #   specific DataFrame API brittleness.  If a future KuzuDB release fixes
    #   the numpy assertion, this section should be revisited to use COPY FROM.
    #
    # Expected speedup: 5–10× for repos > 500 files / 10 000 nodes.
    # Relationship edges (CALLS, CONTAINS_CLASS, etc.) are kept as individual
    # executes but wrapped in 500-edge transactions in Phases 3–6.
    # -----------------------------------------------------------------------

    # Accumulate all methods across files for cross-file FQN resolution
    all_methods: list[dict] = []

    # Collect ALL node rows first, then flush each table in one transaction.
    file_rows: list[dict] = []
    class_rows: list[dict] = []
    method_rows: list[dict] = []
    endpoint_rows: list[dict] = []
    rest_call_rows: list[dict] = []
    entity_relation_rows: list[dict] = []

    for _file_path_str, _lang, file_id, _repo_id, _blob_hash, _branch_name, _const_idx in work_items:
        pr = parse_results[file_id]

        file_rows.append(
            {
                "id": file_id,
                "path": pr.file_path,
                "language": pr.lang,
                "repo_id": repo_id,
                "blob_hash": pr.blob_hash,
                "branch_name": pr.branch_name,
            }
        )
        counters["files"] += 1

        for cls in pr.classes:
            class_rows.append(cls)
            counters["classes"] += 1

        for method in pr.methods:
            method_rows.append(method)
            counters["methods"] += 1

        all_methods.extend(pr.methods)

        for ep in pr.endpoints:
            endpoint_rows.append(ep)
            counters["endpoints"] += 1

        for rc in pr.rest_calls:
            rest_call_rows.append(rc)
            counters["rest_calls"] += 1

        for er in pr.entity_relations:
            entity_relation_rows.append(er)
            counters["entity_relations"] += 1

    # --- Flush File nodes (one transaction for entire table) ---
    if file_rows:
        conn.execute("BEGIN TRANSACTION")
        for row in file_rows:
            conn.execute(
                "CREATE (:File {id: $id, path: $path, language: $language, "
                "repo_id: $repo_id, blob_hash: $blob_hash, branch_name: $branch_name})",
                row,
            )
        conn.execute("COMMIT")

    # --- Flush Class nodes ---
    if class_rows:
        conn.execute("BEGIN TRANSACTION")
        for row in class_rows:
            conn.execute(
                "CREATE (:Class {"
                "id: $id, name: $name, fqn: $fqn, file_id: $file_id, "
                "repo_id: $repo_id, is_interface: $is_interface, "
                "is_object: $is_object, enclosing_class_name: $enclosing_class_name, "
                "annotations: $annotations"
                "})",
                row,
            )
        conn.execute("COMMIT")

    # --- Flush Method nodes ---
    if method_rows:
        conn.execute("BEGIN TRANSACTION")
        for row in method_rows:
            conn.execute(
                "CREATE (:Method {"
                "id: $id, name: $name, fqn: $fqn, class_id: $class_id, "
                "file_id: $file_id, repo_id: $repo_id, line_start: $line_start, "
                "is_suspend: $is_suspend, annotations: $annotations, "
                "generated: $generated, is_entry_point: $is_entry_point, "
                "complexity_hint: $complexity_hint"
                "})",
                row,
            )
        conn.execute("COMMIT")

    # --- Flush Endpoint nodes ---
    if endpoint_rows:
        conn.execute("BEGIN TRANSACTION")
        for row in endpoint_rows:
            conn.execute(
                "CREATE (:Endpoint {"
                "id: $id, http_method: $http_method, path: $path, "
                "path_regex: $path_regex, handler_method_id: $handler_method_id, "
                "repo_id: $repo_id"
                "})",
                row,
            )
        conn.execute("COMMIT")

    # --- Flush RestCall nodes ---
    if rest_call_rows:
        conn.execute("BEGIN TRANSACTION")
        for row in rest_call_rows:
            conn.execute(
                "CREATE (:RestCall {"
                "id: $id, http_method: $http_method, url_pattern: $url_pattern, "
                "caller_method_id: $caller_method_id, repo_id: $repo_id"
                "})",
                row,
            )
        conn.execute("COMMIT")

    # --- Flush EntityRelation nodes ---
    if entity_relation_rows:
        conn.execute("BEGIN TRANSACTION")
        for row in entity_relation_rows:
            conn.execute(
                "CREATE (:EntityRelation {"
                "id: $id, source_class_id: $source_class_id, "
                "target_class_fqn: $target_class_fqn, field_name: $field_name, "
                "relation_type: $relation_type, fetch_type: $fetch_type, "
                "repo_id: $repo_id"
                "})",
                row,
            )
        conn.execute("COMMIT")

    # FQN → class_id for inheritance edge resolution (built after all Class nodes written)
    fqn_to_class_id: dict[str, str] = {}
    for pr in parse_results.values():
        for cls in pr.classes:
            fqn_to_class_id[cls["fqn"]] = cls["id"]

    # -----------------------------------------------------------------------
    # Phase 3 — resolve call edges (serial — needs full fqn_index)
    # -----------------------------------------------------------------------
    all_classes: list[dict] = []
    for pr in parse_results.values():
        all_classes.extend(pr.classes)
    # N1: pass classes=all_classes to resolve_calls once resolver.py accepts the parameter

    fqn_index = build_fqn_index(all_methods)
    method_id_set: set[str] = {m["id"] for m in all_methods}

    # Repo-level accumulator for all written CALLS pairs — used by Phase 6 to
    # avoid re-inserting duplicate edges during virtual dispatch fan-out.
    written_call_pairs: set[tuple[str, str]] = set()

    # Collect all resolved edges across all files, then flush in batched transactions.
    # Batch size of 500 edges per transaction balances memory and WAL flush overhead.
    _EDGE_BATCH_SIZE = 500

    calls_edges: list[tuple[str, str, str, int, int]] = []  # (caller_id, callee_id, callee_name, caller_arg_pos, callee_param_pos)
    unresolved_nodes: list[dict] = []                 # stub RestCall dicts
    unresolved_edges: list[tuple[str, str]] = []      # (caller_id, callee_id) for UNRESOLVED_CALL

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
            impl_index=impl_index,   # NEW — redirect UNRESOLVED calls to impl classes
            classes=all_classes,     # N1 — Kotlin object/companion resolution
        )

        for edge in edges:
            if edge.edge_type == "CALLS":
                if edge.callee_id not in method_id_set:
                    continue
                pair = (edge.caller_id, edge.callee_id)
                if pair in written_call_pairs:
                    continue
                written_call_pairs.add(pair)
                calls_edges.append((edge.caller_id, edge.callee_id, edge.callee_name, edge.caller_arg_pos, edge.callee_param_pos))
                counters["call_edges"] += 1
            else:
                # UNRESOLVED_CALL — collect stub RestCall node and edge
                unresolved_nodes.append(
                    {
                        "id": edge.callee_id,
                        "http_method": "UNKNOWN",
                        "url_pattern": "UNRESOLVED",
                        "callee_name": edge.callee_name,
                        "caller_method_id": edge.caller_id,
                        "repo_id": repo_id,
                    }
                )
                unresolved_edges.append((edge.caller_id, edge.callee_id))
                counters["call_edges"] += 1

    # Flush CALLS edges in 500-edge transactions
    for _batch_start in range(0, max(1, len(calls_edges)), _EDGE_BATCH_SIZE):
        batch = calls_edges[_batch_start: _batch_start + _EDGE_BATCH_SIZE]
        if not batch:
            break
        conn.execute("BEGIN TRANSACTION")
        for caller_id, callee_id, callee_name, caller_arg_pos, callee_param_pos in batch:
            conn.execute(
                "MATCH (a:Method), (b:Method) "
                "WHERE a.id = $caller AND b.id = $callee "
                "CREATE (a)-[:CALLS {callee_name: $callee_name, caller_arg_pos: $caller_arg_pos, callee_param_pos: $callee_param_pos}]->(b)",
                {"caller": caller_id, "callee": callee_id, "callee_name": callee_name,
                 "caller_arg_pos": caller_arg_pos, "callee_param_pos": callee_param_pos},
            )
        conn.execute("COMMIT")

    # Flush UNRESOLVED_CALL stub nodes (one transaction for all)
    if unresolved_nodes:
        conn.execute("BEGIN TRANSACTION")
        for row in unresolved_nodes:
            conn.execute(
                "CREATE (:RestCall {"
                "id: $id, http_method: $http_method, url_pattern: $url_pattern, "
                "callee_name: $callee_name, "
                "caller_method_id: $caller_method_id, repo_id: $repo_id"
                "})",
                row,
            )
        conn.execute("COMMIT")

    # Flush UNRESOLVED_CALL edges in 500-edge transactions
    for _batch_start in range(0, max(1, len(unresolved_edges)), _EDGE_BATCH_SIZE):
        batch = unresolved_edges[_batch_start: _batch_start + _EDGE_BATCH_SIZE]
        if not batch:
            break
        conn.execute("BEGIN TRANSACTION")
        for caller_id, callee_id in batch:
            conn.execute(
                "MATCH (a:Method), (b:RestCall) "
                "WHERE a.id = $caller AND b.id = $callee "
                "CREATE (a)-[:UNRESOLVED_CALL]->(b)",
                {"caller": caller_id, "callee": callee_id},
            )
        conn.execute("COMMIT")

    # -----------------------------------------------------------------------
    # Phase 4 — relationship edges (serial, batched transactions)
    # -----------------------------------------------------------------------

    # Collect CONTAINS_CLASS and CONTAINS_METHOD edges across all files,
    # then flush each edge type in 500-edge transactions.
    contains_class_edges: list[tuple[str, str]] = []   # (file_id, class_id)
    contains_method_edges: list[tuple[str, str]] = []  # (class_id, method_id)
    exposes_edges: list[str] = []                      # endpoint_id list
    has_relation_edges: list[tuple[str, str]] = []     # (class_id, entity_relation_id)

    for _file_id, pr in parse_results.items():
        for cls in pr.classes:
            contains_class_edges.append((cls["file_id"], cls["id"]))
        for method in pr.methods:
            contains_method_edges.append((method["class_id"], method["id"]))
        for ep in pr.endpoints:
            exposes_edges.append(ep["id"])
        for er in pr.entity_relations:
            has_relation_edges.append((er["source_class_id"], er["id"]))

    # Flush CONTAINS_CLASS edges
    for _batch_start in range(0, max(1, len(contains_class_edges)), _EDGE_BATCH_SIZE):
        batch = contains_class_edges[_batch_start: _batch_start + _EDGE_BATCH_SIZE]
        if not batch:
            break
        conn.execute("BEGIN TRANSACTION")
        for fid, cid in batch:
            conn.execute(
                "MATCH (f:File), (c:Class) "
                "WHERE f.id = $fid AND c.id = $cid "
                "CREATE (f)-[:CONTAINS_CLASS]->(c)",
                {"fid": fid, "cid": cid},
            )
        conn.execute("COMMIT")

    # Flush CONTAINS_METHOD edges
    for _batch_start in range(0, max(1, len(contains_method_edges)), _EDGE_BATCH_SIZE):
        batch = contains_method_edges[_batch_start: _batch_start + _EDGE_BATCH_SIZE]
        if not batch:
            break
        conn.execute("BEGIN TRANSACTION")
        for cid, mid in batch:
            conn.execute(
                "MATCH (c:Class), (m:Method) "
                "WHERE c.id = $cid AND m.id = $mid "
                "CREATE (c)-[:CONTAINS_METHOD]->(m)",
                {"cid": cid, "mid": mid},
            )
        conn.execute("COMMIT")

    # Flush EXPOSES edges (Repo → Endpoint)
    for _batch_start in range(0, max(1, len(exposes_edges)), _EDGE_BATCH_SIZE):
        batch = exposes_edges[_batch_start: _batch_start + _EDGE_BATCH_SIZE]
        if not batch:
            break
        conn.execute("BEGIN TRANSACTION")
        for eid in batch:
            conn.execute(
                "MATCH (r:Repo), (e:Endpoint) "
                "WHERE r.id = $rid AND e.id = $eid "
                "CREATE (r)-[:EXPOSES]->(e)",
                {"rid": repo_id, "eid": eid},
            )
        conn.execute("COMMIT")

    # Flush HAS_RELATION edges (Class → EntityRelation)
    for _batch_start in range(0, max(1, len(has_relation_edges)), _EDGE_BATCH_SIZE):
        batch = has_relation_edges[_batch_start: _batch_start + _EDGE_BATCH_SIZE]
        if not batch:
            break
        conn.execute("BEGIN TRANSACTION")
        for cid, eid in batch:
            conn.execute(
                "MATCH (c:Class), (e:EntityRelation) "
                "WHERE c.id = $cid AND e.id = $eid "
                "CREATE (c)-[:HAS_RELATION]->(e)",
                {"cid": cid, "eid": eid},
            )
        conn.execute("COMMIT")

    # -----------------------------------------------------------------------
    # Phase 5 — Inheritance edges (batched transactions)
    # -----------------------------------------------------------------------
    seen_inheritance: set[tuple[str, str, str]] = set()
    extends_edges: list[tuple[str, str]] = []    # (child_id, parent_id)
    implements_edges: list[tuple[str, str]] = []  # (child_id, parent_id)

    for pr in parse_results.values():
        for edge in pr.inheritance_edges:
            child_id   = edge["child_id"]
            parent_fqn = edge["parent_fqn"]
            edge_type  = edge["edge_type"]
            parent_id  = fqn_to_class_id.get(parent_fqn)
            if parent_id is None:
                continue
            key = (child_id, parent_fqn, edge_type)
            if key in seen_inheritance:
                continue
            seen_inheritance.add(key)
            if edge_type == "EXTENDS":
                extends_edges.append((child_id, parent_id))
            elif edge_type == "IMPLEMENTS":
                implements_edges.append((child_id, parent_id))
            counters["inheritance_edges"] += 1

    # Flush EXTENDS edges
    for _batch_start in range(0, max(1, len(extends_edges)), _EDGE_BATCH_SIZE):
        batch = extends_edges[_batch_start: _batch_start + _EDGE_BATCH_SIZE]
        if not batch:
            break
        conn.execute("BEGIN TRANSACTION")
        for cid, pid in batch:
            conn.execute(
                "MATCH (a:Class), (b:Class) WHERE a.id = $cid AND b.id = $pid CREATE (a)-[:EXTENDS]->(b)",
                {"cid": cid, "pid": pid},
            )
        conn.execute("COMMIT")

    # Flush IMPLEMENTS edges
    for _batch_start in range(0, max(1, len(implements_edges)), _EDGE_BATCH_SIZE):
        batch = implements_edges[_batch_start: _batch_start + _EDGE_BATCH_SIZE]
        if not batch:
            break
        conn.execute("BEGIN TRANSACTION")
        for cid, pid in batch:
            conn.execute(
                "MATCH (a:Class), (b:Class) WHERE a.id = $cid AND b.id = $pid CREATE (a)-[:IMPLEMENTS]->(b)",
                {"cid": cid, "pid": pid},
            )
        conn.execute("COMMIT")

    # -----------------------------------------------------------------------
    # Phase 6 — Virtual dispatch fan-out (depends on Phase 5 EXTENDS/IMPLEMENTS edges)
    # -----------------------------------------------------------------------
    # Build override_index: abstract_method_fqn → [concrete_override_method_ids]
    # For every concrete class C that IMPLEMENTS or EXTENDS an abstract class/interface A:
    #   for each method M in A that has an override in C (same name),
    #     add C's method to override_index[M.fqn]

    override_index: dict[str, list[str]] = {}

    # Build a class_id → methods map for fast lookup
    class_methods: dict[str, list[dict]] = {}
    for m in all_methods:
        class_methods.setdefault(m["class_id"], []).append(m)

    # Walk inheritance: for each (child_class_id, parent_fqn, edge_type) triple
    # recorded in seen_inheritance, find methods in parent that have a same-named
    # method in child and populate override_index.
    for (child_id, parent_fqn, _edge_type) in seen_inheritance:
        parent_id = fqn_to_class_id.get(parent_fqn)
        if parent_id is None:
            continue
        parent_methods = class_methods.get(parent_id, [])
        child_methods = class_methods.get(child_id, [])
        child_method_names: dict[str, str] = {m["name"]: m["id"] for m in child_methods}
        for pm in parent_methods:
            if pm["name"] in child_method_names:
                override_index.setdefault(pm["fqn"], []).append(child_method_names[pm["name"]])

    # Collect fan-out CALLS edges for overrides:
    # For every existing CALLS edge (A→B) where B.fqn is in override_index,
    # add CALLS edges (A→B1), (A→B2), etc. for each concrete override.
    r_calls = conn.execute(
        "MATCH (a:Method)-[c:CALLS]->(b:Method) WHERE a.repo_id = $rid RETURN a.id, b.id, b.fqn, c.callee_name",
        {"rid": repo_id},
    )
    fan_out_edges: list[tuple[str, str, str]] = []
    while r_calls.has_next():
        caller_id, callee_id, callee_fqn, edge_callee_name = r_calls.get_next()
        overrides = override_index.get(callee_fqn, [])
        for override_id in overrides:
            if override_id != callee_id and override_id in method_id_set:
                fan_out_edges.append((caller_id, override_id, edge_callee_name or ""))

    seen_fanout: set[tuple[str, str]] = set()
    dedup_fanout: list[tuple[str, str, str]] = []
    for caller_id, callee_id, callee_name in fan_out_edges:
        pair = (caller_id, callee_id)
        if pair in written_call_pairs or pair in seen_fanout:
            continue
        seen_fanout.add(pair)
        dedup_fanout.append((caller_id, callee_id, callee_name))
        counters["call_edges"] += 1

    # Flush fan-out CALLS edges in 500-edge transactions
    for _batch_start in range(0, max(1, len(dedup_fanout)), _EDGE_BATCH_SIZE):
        batch = dedup_fanout[_batch_start: _batch_start + _EDGE_BATCH_SIZE]
        if not batch:
            break
        conn.execute("BEGIN TRANSACTION")
        for caller_id, callee_id, callee_name in batch:
            conn.execute(
                "MATCH (a:Method), (b:Method) "
                "WHERE a.id = $caller AND b.id = $callee "
                "CREATE (a)-[:CALLS {callee_name: $callee_name, caller_arg_pos: $caller_arg_pos, callee_param_pos: $callee_param_pos}]->(b)",
                {"caller": caller_id, "callee": callee_id, "callee_name": callee_name,
                 "caller_arg_pos": -1, "callee_param_pos": -1},
            )
        conn.execute("COMMIT")

    return counters
