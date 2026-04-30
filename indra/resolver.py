"""Local symbol resolver for the Indra code knowledge graph.

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

    # When impl_index is active, restrict suffix matches to local methods to
    # prevent accidental wiring to unregistered impl classes.  Cross-file
    # resolution goes through the impl_index gate or becomes UNRESOLVED.
    _local_method_ids: set[str] = (
        {m["id"] for m in methods} if impl_index is not None else set()
    )

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
) -> None:
    """Emit one CallEdge for this invocation node."""
    name = _get_invocation_name(inv_node, source_bytes)
    if not name:
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
            callee_id = str(uuid.uuid4())
            edge_type = "UNRESOLVED_CALL"
    else:
        callee_id = str(uuid.uuid4())
        edge_type = "UNRESOLVED_CALL"

    edges.append(CallEdge(caller_id=caller_id, callee_id=callee_id, edge_type=edge_type, callee_name=name))
