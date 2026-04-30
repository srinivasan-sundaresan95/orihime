"""Local symbol resolver for the Dedalus code knowledge graph.

Walks tree-sitter ASTs to resolve method call edges within a single file.
This is a best-effort name-based resolver — it does not perform full type
resolution.  The caller (orchestrator) invokes this after extraction.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass
class CallEdge:
    caller_id: str   # Method.id of the calling method
    callee_id: str   # Method.id (CALLS) or new uuid (UNRESOLVED_CALL)
    edge_type: str   # "CALLS" or "UNRESOLVED_CALL"
    callee_name: str = ""  # Simple method name at the call site
    caller_arg_pos: int = -1   # position of the first argument in the caller's call expression (-1 = not tracked)
    callee_param_pos: int = -1  # position of the matching parameter in the callee's signature (-1 = not tracked)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_fqn_index(methods: list[dict]) -> dict[str, str]:
    """Return ``{fqn: method_id}`` for all methods in *methods*.

    Args:
        methods: List of method dicts as produced by JavaExtractor /
            KotlinExtractor (each must have ``"fqn"`` and ``"id"`` keys).

    Returns:
        A mapping from fully-qualified method name to its UUID string id.
    """
    return {m["fqn"]: m["id"] for m in methods}


def resolve_calls(
    tree,
    source_bytes: bytes,
    methods: list[dict],
    fqn_index: dict[str, str],
    file_id: str,
    repo_id: str,
    impl_index: dict[str, str] | None = None,  # NEW — optional, defaults to None
    classes: list[dict] | None = None,   # N1 — Kotlin object/companion resolution
) -> list[CallEdge]:
    """Walk all method bodies in *tree* and emit call edges.

    For every ``method_invocation`` (Java) or ``call_expression`` (Kotlin)
    found inside a method body:

    * If the callee simple name matches the suffix ``.{name}`` of any entry
      in *fqn_index* → emit ``CallEdge(..., edge_type="CALLS")``.
    * Otherwise, if *impl_index* is provided, attempt to resolve via the
      implementation class: if any impl class in *impl_index* has a method
      ``{impl_fqn}.{name}`` in *fqn_index*, emit ``CALLS`` to that method.
    * Otherwise → emit ``CallEdge(..., callee_id=new_uuid,
      edge_type="UNRESOLVED_CALL")``.

    Args:
        tree:         tree-sitter ``Tree`` object for the source file.
        source_bytes: Raw UTF-8 bytes of the source file.
        methods:      Method dicts for methods declared in this file.
        fqn_index:    ``{fqn: method_id}`` index (may span multiple files /
                      repos — whatever the orchestrator provides).
        file_id:      ID of the current file (unused in edge output but
                      available for future filtering).
        repo_id:      ID of the current repo (same note).
        impl_index:   Optional ``{interface_fqn: impl_class_fqn}`` mapping
                      produced by P3-1.1.  When provided, UNRESOLVED calls
                      are redirected to impl-class methods when a unique
                      match exists.
        classes:      Optional list of class dicts (as produced by
                      KotlinExtractor).  When provided, an ``_object_index``
                      is built so that Kotlin ``object`` declarations and
                      companion object calls are resolved via a precise
                      ``ClassName.method`` key rather than falling through to
                      the ambiguous suffix index.  Pass ``None`` (default) to
                      preserve existing behavior.

    Returns:
        List of :class:`CallEdge` instances.
    """
    edges: list[CallEdge] = []

    # Build a fast lookup: (method_name, approximate_line) → method_id
    # We also keep a name→list mapping for quick name matching.
    _name_to_methods: dict[str, list[dict]] = {}
    for m in methods:
        _name_to_methods.setdefault(m["name"], []).append(m)

    # Build suffix index: simple_name → list[method_id] from fqn_index
    # e.g.  "com.example.Foo.bar" → simple name "bar"
    _suffix_index: dict[str, list[str]] = {}
    for fqn, mid in fqn_index.items():
        simple = fqn.rsplit(".", 1)[-1]
        _suffix_index.setdefault(simple, []).append(mid)

    # Build constructor index: "SimpleClassName.<init>" → list[method_id].
    # For FQN "com.example.Foo.<init>" the key is "Foo.<init>".
    # This is used by the constructor-call handlers so that `new Foo(...)` (Java)
    # and `Foo(...)` (Kotlin) can be resolved without ambiguity even when multiple
    # packages have a class with the same name.
    _ctor_index: dict[str, list[str]] = {}
    for fqn, mid in fqn_index.items():
        if fqn.endswith(".<init>"):
            # "pkg.ClassName.<init>" → split off last two segments
            parts = fqn.rsplit(".", 2)  # ["pkg", "ClassName", "<init>"]
            if len(parts) >= 2:
                class_simple = parts[-2]
                key = f"{class_simple}.<init>"
                _ctor_index.setdefault(key, []).append(mid)

    # N1: Build object index: "ClassName.method" and companion variants → [method_id]
    _object_index: dict[str, list[str]] = {}
    if classes is not None:
        _cls_by_name: dict[str, dict] = {c["name"]: c for c in classes}
        for fqn, mid in fqn_index.items():
            parts = fqn.rsplit(".", 1)
            if len(parts) != 2:
                continue
            class_fqn_part, method_name = parts
            class_simple = class_fqn_part.rsplit(".", 1)[-1]
            cls = _cls_by_name.get(class_simple)
            if cls is None or not cls.get("is_object", False):
                continue
            enclosing = cls.get("enclosing_class_name")
            if enclosing is None:
                # Standalone object_declaration
                key = f"{class_simple}.{method_name}"
                _object_index.setdefault(key, []).append(mid)
                if class_simple.endswith("Companion"):
                    base = class_simple[:-len("Companion")]
                    if base:
                        _object_index.setdefault(f"{base}.{method_name}", []).append(mid)
            else:
                # companion_object: register multiple key patterns
                _object_index.setdefault(f"{enclosing}.{class_simple}.{method_name}", []).append(mid)
                _object_index.setdefault(f"{enclosing}.Companion.{method_name}", []).append(mid)
                if class_simple.endswith("Companion"):
                    _object_index.setdefault(f"{enclosing}.{method_name}", []).append(mid)
                _object_index.setdefault(f"{class_simple}.{method_name}", []).append(mid)

    # When impl_index is active, restrict suffix matches to local methods to
    # prevent accidental wiring to unregistered impl classes.  Cross-file
    # resolution goes through the impl_index gate or becomes UNRESOLVED.
    _local_method_ids: set[str] = (
        {m["id"] for m in methods} if impl_index is not None else set()
    )

    # Build a lookup from simple class name → <init> method_id for
    # matching Java constructor_declaration nodes to their synthetic <init>.
    # E.g. "Address" → id-of-Address.<init>
    _init_by_class_name: dict[str, str] = {}
    for m in methods:
        if m["name"] == "<init>":
            # fqn is "pkg.ClassName.<init>" — extract ClassName
            parts = m["fqn"].rsplit(".", 2)
            if len(parts) >= 2:
                class_simple = parts[-2]
                _init_by_class_name[class_simple] = m["id"]

    root = tree.root_node

    # Walk the tree looking for method / function declarations
    for node in _walk_all(root):
        if node.type in ("method_declaration", "function_declaration"):
            _process_method_node(
                node,
                source_bytes,
                methods,
                _suffix_index,
                edges,
                fqn_index=fqn_index,
                impl_index=impl_index,
                local_method_ids=_local_method_ids,
                ctor_index=_ctor_index,
                object_index=_object_index,
            )
        elif node.type == "constructor_declaration":
            # Java constructor body: treat as its class's <init> method
            _process_constructor_body(
                node,
                source_bytes,
                _init_by_class_name,
                _suffix_index,
                edges,
                fqn_index=fqn_index,
                impl_index=impl_index,
                local_method_ids=_local_method_ids,
                ctor_index=_ctor_index,
                object_index=_object_index,
            )

    return edges


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _text(node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte: node.end_byte].decode("utf-8", errors="replace")


def _walk_all(node):
    """Yield *node* and all its descendants depth-first."""
    yield node
    for child in node.children:
        yield from _walk_all(child)


def _find_enclosing_method(
    method_node,
    source_bytes: bytes,
    methods: list[dict],
) -> str | None:
    """Return the method_id from *methods* that corresponds to *method_node*.

    Matching is done by method name + line number (tree-sitter line is
    0-based; stored line_start is 1-based).
    """
    # Get the name of the declared method
    name_node = method_node.child_by_field_name("name")
    if name_node is None:
        # Fallback: first identifier child
        for c in method_node.children:
            if c.type == "identifier":
                name_node = c
                break
    if name_node is None:
        return None

    method_name = _text(name_node, source_bytes)
    # tree-sitter start_point is (row, col), row is 0-based
    line_start = method_node.start_point[0] + 1  # convert to 1-based

    # Find the best match: same name, closest line_start
    best: dict | None = None
    best_delta = float("inf")
    for m in methods:
        if m["name"] == method_name:
            delta = abs(m["line_start"] - line_start)
            if delta < best_delta:
                best_delta = delta
                best = m
    return best["id"] if best is not None else None


def _process_method_node(
    method_node,
    source_bytes: bytes,
    methods: list[dict],
    suffix_index: dict[str, list[str]],
    edges: list[CallEdge],
    fqn_index: dict[str, str] | None,
    impl_index: dict[str, str] | None,
    local_method_ids: set[str],
    ctor_index: "dict[str, list[str]] | None" = None,
    object_index: "dict[str, list[str]] | None" = None,
) -> None:
    """Emit CallEdge objects for all call sites inside *method_node*."""
    caller_id = _find_enclosing_method(method_node, source_bytes, methods)
    if caller_id is None:
        return

    # Find the body block
    body_node = method_node.child_by_field_name("body")
    if body_node is None:
        # Fallback: look for a block or function_body child
        for c in method_node.children:
            if c.type in ("block", "function_body"):
                body_node = c
                break
    if body_node is None:
        return

    # Collect all invocation nodes inside the body
    for node in _walk_all(body_node):
        if node.type in ("method_invocation", "call_expression"):
            _process_invocation(
                node,
                source_bytes,
                caller_id,
                suffix_index,
                edges,
                fqn_index=fqn_index,
                impl_index=impl_index,
                local_method_ids=local_method_ids,
                ctor_index=ctor_index,
                object_index=object_index,
            )
        elif node.type == "object_creation_expression":
            # Java: `new ClassName(...)` — emit a CALLS edge to ClassName.<init>
            _process_constructor_call(
                node,
                source_bytes,
                caller_id,
                ctor_index or {},
                edges,
            )


def _get_invocation_name(inv_node, source_bytes: bytes) -> str | None:
    """Extract the simple method name from an invocation node.

    Handles both:
      * Java ``method_invocation``: children contain identifier nodes and an
        ``argument_list``; the identifier immediately before ``argument_list``
        is the method name.
      * Kotlin ``call_expression``: the first child is a navigation_expression
        or simple identifier; we take the last identifier before the
        ``value_arguments`` node.
    """
    children = inv_node.children

    # --- Java-style: look for identifier just before argument_list ---
    for i, c in enumerate(children):
        if c.type == "argument_list":
            for j in range(i - 1, -1, -1):
                if children[j].type == "identifier":
                    return _text(children[j], source_bytes)
            return None

    # --- Kotlin-style: look for identifier just before value_arguments ---
    for i, c in enumerate(children):
        if c.type == "value_arguments":
            for j in range(i - 1, -1, -1):
                if children[j].type == "identifier":
                    return _text(children[j], source_bytes)
                # navigation_expression: last identifier inside it
                if children[j].type in (
                    "navigation_expression",
                    "simple_identifier",
                ):
                    # Walk into it to find last identifier
                    for sub in reversed(list(_walk_all(children[j]))):
                        if sub.type in ("identifier", "simple_identifier"):
                            return _text(sub, source_bytes)
            return None

    # If the node itself is a simple call like `foo()` — first identifier child
    for c in children:
        if c.type == "identifier":
            return _text(c, source_bytes)

    return None


def _get_object_call_key(inv_node, source_bytes: bytes) -> str | None:
    """Return the navigation_expression text for an object-style call, or None."""
    for child in inv_node.children:
        if child.type == "navigation_expression":
            return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
    return None


def _count_call_arguments(inv_node) -> int:
    """Count the number of arguments in a method invocation / call expression node.

    For Java ``method_invocation``: looks for an ``argument_list`` child and
    counts non-punctuation children (skipping ``(`` ``,`` and ``)``) .

    For Kotlin ``call_expression``: looks for a ``value_arguments`` child and
    counts ``value_argument`` type children.

    Returns the argument count (>= 0), or 0 if the argument list node is not found.
    """
    # Java: argument_list child
    for child in inv_node.children:
        if child.type == "argument_list":
            # Count direct children that are not punctuation
            _SKIP = {"(", ")", ","}
            return sum(1 for c in child.children if c.type not in _SKIP)

    # Kotlin: value_arguments child
    for child in inv_node.children:
        if child.type == "value_arguments":
            return sum(1 for c in child.children if c.type == "value_argument")

    return 0


def _is_object_style_call(inv_node, source_bytes: bytes) -> bool:
    """Return True when the call is a qualified call with a type-like receiver.

    Heuristic: the call_expression contains a ``navigation_expression`` whose
    first identifier child starts with an uppercase letter.  This covers Kotlin
    ``object`` declarations, companion objects and Java-style static helpers
    (e.g. ``DateTimeUtil.isInTimePeriod(42)``).  Such calls are statically
    dispatched and cannot be DI-injected, so the impl_index restriction should
    not apply to them.
    """
    for child in inv_node.children:
        if child.type == "navigation_expression":
            # The receiver is the first identifier-like child
            for sub in child.children:
                if sub.type in ("identifier", "simple_identifier"):
                    text = _text(sub, source_bytes)
                    return bool(text) and text[0].isupper()
            break
    return False


def _process_invocation(
    inv_node,
    source_bytes: bytes,
    caller_id: str,
    suffix_index: dict[str, list[str]],
    edges: list[CallEdge],
    fqn_index: dict[str, str] | None = None,
    impl_index: dict[str, str] | None = None,
    local_method_ids: set[str] | None = None,
    ctor_index: "dict[str, list[str]] | None" = None,
    object_index: "dict[str, list[str]] | None" = None,
) -> None:
    """Emit one CallEdge for this invocation node."""
    name = _get_invocation_name(inv_node, source_bytes)
    if not name:
        return

    # G2: Count arguments at the call site to populate caller_arg_pos / callee_param_pos.
    # If the call has at least one argument, position 0 is tracked (first arg → first param).
    # Constructor calls (<init>) always use -1 (not tracked).
    _arg_count = _count_call_arguments(inv_node)
    _is_ctor = name.endswith("<init>") if name else False

    def _arg_pos() -> tuple[int, int]:
        """Return (caller_arg_pos, callee_param_pos) for the current call site."""
        if _is_ctor or _arg_count == 0:
            return -1, -1
        return 0, 0

    # N1: Object / companion call fast path
    if object_index and _is_object_style_call(inv_node, source_bytes):
        obj_key = _get_object_call_key(inv_node, source_bytes)
        if obj_key is not None:
            # The key from the AST includes the full navigation expression text,
            # e.g. "DateTimeUtil.format" or "ActiveCampaignInfo.of"
            # Strip any package prefix the key might have picked up
            # by trying progressively shorter suffixes
            obj_matches = object_index.get(obj_key, [])
            # Also try last two segments if full text didn't match
            if not obj_matches:
                parts = obj_key.rsplit(".", 2)
                if len(parts) >= 2:
                    obj_key2 = ".".join(parts[-2:])
                    obj_matches = object_index.get(obj_key2, [])
            if obj_matches:
                _cap, _cpp = _arg_pos()
                edges.append(CallEdge(
                    caller_id=caller_id,
                    callee_id=obj_matches[0],
                    edge_type="CALLS",
                    callee_name=name or obj_key,
                    caller_arg_pos=_cap,
                    callee_param_pos=_cpp,
                ))
                return

    # Suffix matches are restricted to local methods when impl_index is active
    # to prevent accidental wiring to unregistered impl classes; cross-file
    # resolution goes exclusively through the impl_index gate.
    #
    # Exception: Kotlin object/companion/static calls use a capitalised receiver
    # (e.g. ``DateTimeUtil.isInTimePeriod()``).  These are statically dispatched
    # and cannot be DI-injected, so we allow cross-file suffix matches for them.
    raw_matches = suffix_index.get(name, [])
    if impl_index is not None and not _is_object_style_call(inv_node, source_bytes):
        matches = [mid for mid in raw_matches if mid in local_method_ids]
    else:
        matches = raw_matches

    if matches:
        callee_id = matches[0]
        edge_type = "CALLS"
    elif impl_index is not None and fqn_index:
        # impl_index already has last-one-wins deduplication from P3-1.1, so
        # iteration order here is deterministic; stop at first hit.
        callee_id = None
        for _iface_fqn, impl_fqn in impl_index.items():
            candidate = f"{impl_fqn}.{name}"
            if candidate in fqn_index:
                callee_id = fqn_index[candidate]
                break
        if callee_id is not None:
            edge_type = "CALLS"
        else:
            # Kotlin constructor fallback: CapitalizedName() → look for
            # ClassName.<init> in the ctor_index.  This handles `Point(x, y)`
            # and `Rectangle(tl, br)` Kotlin call_expression nodes where the
            # callee resolves to a class constructor.
            if name and name[0].isupper() and ctor_index:
                init_name = f"{name}.<init>"
                init_matches = ctor_index.get(init_name, [])
                if init_matches:
                    for mid in init_matches:
                        edges.append(CallEdge(
                            caller_id=caller_id,
                            callee_id=mid,
                            edge_type="CALLS",
                            callee_name=init_name,
                            caller_arg_pos=-1,
                            callee_param_pos=-1,
                        ))
                    return
            callee_id = str(uuid.uuid4())
            edge_type = "UNRESOLVED_CALL"
    else:
        # Kotlin constructor fallback (no impl_index): CapitalizedName() →
        # look for ClassName.<init> in the ctor_index.
        if name and name[0].isupper() and ctor_index:
            init_name = f"{name}.<init>"
            init_matches = ctor_index.get(init_name, [])
            if init_matches:
                for mid in init_matches:
                    edges.append(CallEdge(
                        caller_id=caller_id,
                        callee_id=mid,
                        edge_type="CALLS",
                        callee_name=init_name,
                        caller_arg_pos=-1,
                        callee_param_pos=-1,
                    ))
                return
        callee_id = str(uuid.uuid4())
        edge_type = "UNRESOLVED_CALL"

    _cap, _cpp = _arg_pos()
    edges.append(CallEdge(
        caller_id=caller_id,
        callee_id=callee_id,
        edge_type=edge_type,
        callee_name=name,
        caller_arg_pos=_cap,
        callee_param_pos=_cpp,
    ))


def _process_constructor_call(
    ctor_node,
    source_bytes: bytes,
    caller_id: str,
    ctor_index: dict[str, list[str]],
    edges: list[CallEdge],
) -> None:
    """Emit CALLS edges for a Java ``object_creation_expression`` node.

    Resolves the class being constructed by extracting the ``type_identifier``
    from the ``type`` child (handles both ``type_identifier`` and
    ``generic_type``), then looks up ``ClassName.<init>`` in *ctor_index*
    (a secondary index keyed by ``SimpleClassName.<init>``).
    External classes (not in the index) are silently ignored — no UNRESOLVED
    edge is emitted so as not to pollute the graph with noise from stdlib types
    such as ``new ArrayList()``.
    """
    # The class name is either a direct type_identifier child or nested inside
    # a generic_type node (e.g. new HashMap<String, Integer>()).
    class_name: str | None = None
    for child in ctor_node.children:
        if child.type == "type_identifier":
            class_name = _text(child, source_bytes)
            break
        if child.type == "generic_type":
            # First type_identifier inside generic_type is the raw class name
            for sub in child.children:
                if sub.type == "type_identifier":
                    class_name = _text(sub, source_bytes)
                    break
            if class_name:
                break

    if not class_name:
        return

    init_name = f"{class_name}.<init>"
    init_matches = ctor_index.get(init_name, [])
    # Only emit CALLS when the <init> method is present in the index (i.e. the
    # class is declared in the indexed codebase).  External / stdlib classes are
    # silently skipped.
    for mid in init_matches:
        edges.append(CallEdge(
            caller_id=caller_id,
            callee_id=mid,
            edge_type="CALLS",
            callee_name=init_name,
        ))


def _process_constructor_body(
    ctor_decl_node,
    source_bytes: bytes,
    init_by_class_name: dict[str, str],
    suffix_index: dict[str, list[str]],
    edges: list[CallEdge],
    fqn_index: dict[str, str] | None,
    impl_index: dict[str, str] | None,
    local_method_ids: set[str],
    ctor_index: "dict[str, list[str]] | None" = None,
    object_index: "dict[str, list[str]] | None" = None,
) -> None:
    """Walk a Java ``constructor_declaration`` body and emit CALLS edges.

    The caller_id for all edges is the synthetic ``<init>`` method that
    corresponds to this constructor's class.  The constructor name is used to
    look up the ``<init>`` method_id in *init_by_class_name*.
    """
    # Resolve the class name from the constructor identifier child
    name_node = ctor_decl_node.child_by_field_name("name")
    if name_node is None:
        for c in ctor_decl_node.children:
            if c.type == "identifier":
                name_node = c
                break
    if name_node is None:
        return

    class_name = _text(name_node, source_bytes)
    caller_id = init_by_class_name.get(class_name)
    if caller_id is None:
        return

    # Find the constructor body (constructor_body field)
    body_node = ctor_decl_node.child_by_field_name("body")
    if body_node is None:
        for c in ctor_decl_node.children:
            if c.type in ("constructor_body", "block"):
                body_node = c
                break
    if body_node is None:
        return

    for node in _walk_all(body_node):
        if node.type in ("method_invocation", "call_expression"):
            _process_invocation(
                node,
                source_bytes,
                caller_id,
                suffix_index,
                edges,
                fqn_index=fqn_index,
                impl_index=impl_index,
                local_method_ids=local_method_ids,
                ctor_index=ctor_index,
                object_index=object_index,
            )
        elif node.type == "object_creation_expression":
            _process_constructor_call(
                node,
                source_bytes,
                caller_id,
                ctor_index or {},
                edges,
            )
