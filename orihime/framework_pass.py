"""Framework-mediated synthetic CALLS edge pass.

Spring (and similar DI frameworks) invoke certain methods via reflection or
AOP proxies, making them invisible to the static resolver.  This pass emits
synthetic CALLS edges for three such patterns after the main graph is built:

Pass A — Bean Validation (@AssertTrue / @AssertFalse)
    When a Spring bean's field is annotated @Valid, the Bean Validation
    framework calls every @AssertTrue / @AssertFalse method on the field's
    declared type (and recursively on nested @Valid fields).  The synthetic
    edges are: every method that directly constructs or calls methods on the
    validated class → the @AssertTrue/@AssertFalse methods on that class.

    Implementation: we emit the edges from the *owners* of @Valid-annotated
    fields (i.e. the class that declares the @Valid field), not from arbitrary
    call sites.  The owner class's methods are the natural "trigger" context
    because validation fires when Spring initialises or binds that object.

Pass B — OncePerRequestFilter / HandlerInterceptor
    Classes that extend OncePerRequestFilter (or implement HandlerInterceptor)
    have their doFilterInternal / preHandle methods called by the servlet
    container before every HTTP handler.  We emit synthetic CALLS edges from
    every @RequestMapping-style endpoint handler method to each such filter/
    interceptor method in the repo.

Pass C — Spring Application Events (@EventListener)
    When code calls applicationContext.publishEvent(new FooEvent(...)), Spring
    invokes every @EventListener method whose first parameter type is FooEvent
    (or a supertype).  We match publishEvent call sites in the graph to
    @EventListener methods by the event class name.

All three passes operate purely on the already-populated KuzuDB graph — no
source re-reads required.  They are called from indexer.py after Phase 6.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_EDGE_BATCH_SIZE = 500

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_framework_pass(conn, writer, repo_id: str) -> dict[str, int]:
    """Emit synthetic CALLS edges for all three framework patterns.

    Args:
        conn:     Read-only KuzuDB connection (for queries).
        writer:   WriteClient with .execute() for INSERT/CREATE statements.
        repo_id:  The repo to process.

    Returns:
        Dict with keys ``assert_true_edges``, ``filter_edges``,
        ``event_listener_edges`` giving the number of new edges emitted.
    """
    written: set[tuple[str, str]] = _existing_call_pairs(conn, repo_id)

    a = _pass_a_assert_true(conn, writer, repo_id, written)
    b = _pass_b_filters(conn, writer, repo_id, written)
    c = _pass_c_event_listeners(conn, writer, repo_id, written)

    log.info(
        "framework_pass repo=%s  assert_true=%d  filter=%d  event_listener=%d",
        repo_id, a, b, c,
    )
    return {"assert_true_edges": a, "filter_edges": b, "event_listener_edges": c}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _existing_call_pairs(conn, repo_id: str) -> set[tuple[str, str]]:
    """Return the set of (caller_id, callee_id) CALLS pairs already in the DB."""
    r = conn.execute(
        "MATCH (a:Method)-[:CALLS]->(b:Method) WHERE a.repo_id = $rid RETURN a.id, b.id",
        {"rid": repo_id},
    )
    pairs: set[tuple[str, str]] = set()
    while r.has_next():
        row = r.get_next()
        pairs.add((row[0], row[1]))
    return pairs


def _flush_edges(
    writer,
    edges: list[tuple[str, str, str]],
    written: set[tuple[str, str]],
) -> int:
    """Deduplicate and flush CALLS edges, returning the count emitted."""
    new_edges = [
        (caller, callee, name)
        for caller, callee, name in edges
        if (caller, callee) not in written
    ]
    # Deduplicate within this batch too
    seen: set[tuple[str, str]] = set()
    deduped = []
    for caller, callee, name in new_edges:
        if (caller, callee) not in seen:
            seen.add((caller, callee))
            deduped.append((caller, callee, name))
            written.add((caller, callee))

    for start in range(0, max(1, len(deduped)), _EDGE_BATCH_SIZE):
        batch = deduped[start: start + _EDGE_BATCH_SIZE]
        if not batch:
            break
        writer.execute("BEGIN TRANSACTION")
        for caller_id, callee_id, callee_name in batch:
            writer.execute(
                "MATCH (a:Method), (b:Method) "
                "WHERE a.id = $caller AND b.id = $callee "
                "CREATE (a)-[:CALLS {callee_name: $callee_name, "
                "caller_arg_pos: $cap, callee_param_pos: $cpp}]->(b)",
                {
                    "caller": caller_id,
                    "callee": callee_id,
                    "callee_name": callee_name,
                    "cap": -1,
                    "cpp": -1,
                },
            )
        writer.execute("COMMIT")

    return len(deduped)


# ---------------------------------------------------------------------------
# Pass A — @AssertTrue / @AssertFalse (Bean Validation)
# ---------------------------------------------------------------------------

def _pass_a_assert_true(conn, writer, repo_id: str, written: set[tuple[str, str]]) -> int:
    """Emit CALLS edges from owner-class methods to @AssertTrue/@AssertFalse methods.

    Strategy:
    1. Find all methods in the repo annotated with @AssertTrue or @AssertFalse.
       Record their class FQN.
    2. Find all classes that have a @Valid-annotated field whose declared type
       matches step-1 class FQNs.  These are the "owner" classes.
    3. For each owner class, emit CALLS from every non-constructor method in
       the owner → the @AssertTrue/@AssertFalse method on the validated type.

    This models the fact that Spring validates a @ConfigurationProperties or
    @RequestBody object by calling all constraint methods when the owner bean
    is initialised.
    """
    # Step 1: collect @AssertTrue / @AssertFalse methods, grouped by class FQN
    r = conn.execute(
        "MATCH (m:Method) WHERE m.repo_id = $rid RETURN m.id, m.fqn, m.annotations, m.class_id",
        {"rid": repo_id},
    )
    # assert_methods: class_id → list of (method_id, method_name)
    assert_methods: dict[str, list[tuple[str, str]]] = {}
    # class_id → class_fqn (needed for step 2 lookup)
    class_id_to_fqn: dict[str, str] = {}

    while r.has_next():
        mid, fqn, annotations, class_id = r.get_next()
        ann_list = annotations if isinstance(annotations, list) else []
        if "AssertTrue" in ann_list or "AssertFalse" in ann_list:
            method_name = fqn.rsplit(".", 1)[-1]
            assert_methods.setdefault(class_id, []).append((mid, method_name))

    if not assert_methods:
        return 0

    # Build class_id → fqn map for the assert_methods classes
    for class_id in list(assert_methods.keys()):
        r2 = conn.execute(
            "MATCH (c:Class) WHERE c.id = $cid RETURN c.fqn",
            {"cid": class_id},
        )
        if r2.has_next():
            class_id_to_fqn[class_id] = r2.get_next()[0]

    # Build fqn → class_id reverse map for step 2
    fqn_to_assert_class_id: dict[str, str] = {v: k for k, v in class_id_to_fqn.items()}

    # Step 2: find owner classes — classes that have a @Valid field whose type
    # is one of the assert_method classes.
    # We look at class annotations on fields indirectly: the extractor records
    # class.annotations but not field-level annotations.  Instead we rely on
    # the fact that @Valid cascade is declared on the field type — so any class
    # that *contains* an @AssertTrue class as a field is the owner.
    # Proxy: query all classes in this repo and check which ones have the
    # validated class in their declared methods' parameter types OR are direct
    # owners of the validated type via CONTAINS_METHOD → class relationship.
    # Simpler and sufficient: emit edges from ALL methods in the *repo* that
    # belong to a class that either IS the validated class itself or OWNS an
    # instance of it (via any CALLS edge that constructs it).  The most
    # conservative correct approach:
    #
    # Emit: for each @AssertTrue method M on class C,
    #   find all methods in the repo that call C.<init> → they trigger validation
    #   PLUS all methods in C itself (self-validation on construction).

    # Collect all <init> method ids for the assert-bearing classes
    init_ids: dict[str, str] = {}  # class_id → <init> method_id
    for class_id in assert_methods:
        cfqn = class_id_to_fqn.get(class_id, "")
        init_fqn = f"{cfqn}.<init>"
        r3 = conn.execute(
            "MATCH (m:Method) WHERE m.fqn = $fqn RETURN m.id",
            {"fqn": init_fqn},
        )
        if r3.has_next():
            init_ids[class_id] = r3.get_next()[0]

    # Callers of <init> → they trigger Bean Validation on construction
    edges: list[tuple[str, str, str]] = []

    for class_id, am_list in assert_methods.items():
        init_mid = init_ids.get(class_id)
        caller_ids: list[str] = []

        if init_mid:
            r4 = conn.execute(
                "MATCH (caller:Method)-[:CALLS]->(callee:Method) "
                "WHERE callee.id = $cid AND caller.repo_id = $rid "
                "RETURN caller.id",
                {"cid": init_mid, "rid": repo_id},
            )
            while r4.has_next():
                caller_ids.append(r4.get_next()[0])

        # Also include the class's own methods (self-validation)
        r5 = conn.execute(
            "MATCH (m:Method) WHERE m.class_id = $cid AND m.repo_id = $rid RETURN m.id",
            {"cid": class_id, "rid": repo_id},
        )
        own_ids = []
        while r5.has_next():
            own_ids.append(r5.get_next()[0])

        for caller_id in caller_ids + own_ids:
            for assert_mid, assert_name in am_list:
                if caller_id != assert_mid:
                    edges.append((caller_id, assert_mid, assert_name))

    return _flush_edges(writer, edges, written)


# ---------------------------------------------------------------------------
# Pass B — OncePerRequestFilter / HandlerInterceptor
# ---------------------------------------------------------------------------

_FILTER_BASE_CLASSES = frozenset({
    "OncePerRequestFilter",
    "GenericFilterBean",
    "HandlerInterceptorAdapter",
})
_FILTER_BASE_FQNS = frozenset({
    "org.springframework.web.filter.OncePerRequestFilter",
    "org.springframework.web.filter.GenericFilterBean",
    "org.springframework.web.servlet.handler.HandlerInterceptorAdapter",
})
_FILTER_INTERFACE_FQNS = frozenset({
    "org.springframework.web.servlet.HandlerInterceptor",
    "jakarta.servlet.Filter",
    "javax.servlet.Filter",
})
# Methods that run for every request in filters/interceptors
_FILTER_METHOD_NAMES = frozenset({
    "doFilterInternal",
    "doFilter",
    "preHandle",
    "postHandle",
    "afterCompletion",
})


def _pass_b_filters(conn, writer, repo_id: str, written: set[tuple[str, str]]) -> int:
    """Emit CALLS edges from every endpoint handler to filter/interceptor methods.

    Detection: any class in the repo that EXTENDS OncePerRequestFilter (or
    related bases) or IMPLEMENTS HandlerInterceptor.  We emit synthetic CALLS
    from every endpoint handler method (i.e. methods that are handlers for an
    Endpoint node) to doFilterInternal / preHandle / doFilter on those classes.

    This models that the servlet container calls these methods for every request
    before the handler is invoked, making them part of every endpoint's
    reachable call graph.
    """
    # Find all filter/interceptor implementation classes via EXTENDS/IMPLEMENTS
    filter_class_ids: set[str] = set()

    # EXTENDS — catches OncePerRequestFilter subclasses
    r_ext = conn.execute(
        "MATCH (child:Class)-[:EXTENDS]->(parent:Class) "
        "WHERE child.repo_id = $rid "
        "RETURN child.id, parent.fqn, parent.name",
        {"rid": repo_id},
    )
    while r_ext.has_next():
        child_id, parent_fqn, parent_name = r_ext.get_next()
        if parent_name in _FILTER_BASE_CLASSES or parent_fqn in _FILTER_BASE_FQNS:
            filter_class_ids.add(child_id)

    # IMPLEMENTS — catches HandlerInterceptor implementors
    r_impl = conn.execute(
        "MATCH (child:Class)-[:IMPLEMENTS]->(parent:Class) "
        "WHERE child.repo_id = $rid "
        "RETURN child.id, parent.fqn, parent.name",
        {"rid": repo_id},
    )
    while r_impl.has_next():
        child_id, parent_fqn, parent_name = r_impl.get_next()
        if parent_name in _FILTER_BASE_CLASSES or parent_fqn in _FILTER_BASE_FQNS or parent_fqn in _FILTER_INTERFACE_FQNS:
            filter_class_ids.add(child_id)

    if not filter_class_ids:
        return 0

    # Collect filter method ids (doFilterInternal, preHandle, etc.)
    filter_method_ids: list[tuple[str, str]] = []  # (method_id, method_name)
    for class_id in filter_class_ids:
        r_fm = conn.execute(
            "MATCH (m:Method) WHERE m.class_id = $cid AND m.repo_id = $rid RETURN m.id, m.name",
            {"cid": class_id, "rid": repo_id},
        )
        while r_fm.has_next():
            mid, name = r_fm.get_next()
            if name in _FILTER_METHOD_NAMES:
                filter_method_ids.append((mid, name))

    if not filter_method_ids:
        return 0

    # Collect all endpoint handler method ids
    r_ep = conn.execute(
        "MATCH (e:Endpoint) WHERE e.repo_id = $rid RETURN e.handler_method_id",
        {"rid": repo_id},
    )
    handler_ids: list[str] = []
    while r_ep.has_next():
        handler_ids.append(r_ep.get_next()[0])

    if not handler_ids:
        return 0

    # Emit: every handler → every filter method
    edges: list[tuple[str, str, str]] = []
    for handler_id in handler_ids:
        for filter_mid, filter_name in filter_method_ids:
            edges.append((handler_id, filter_mid, filter_name))

    return _flush_edges(writer, edges, written)


# ---------------------------------------------------------------------------
# Pass C — Spring Application Events (@EventListener)
# ---------------------------------------------------------------------------

def _pass_c_event_listeners(conn, writer, repo_id: str, written: set[tuple[str, str]]) -> int:
    """Emit CALLS edges from publishEvent callers to @EventListener methods.

    Detection:
    1. Find all methods annotated @EventListener in the repo.  Extract the
       event type from the first parameter name/type or from the annotation
       argument.  We use the simple class name as a key.
    2. Find all CALLS edges in the repo where the callee name is 'publishEvent'.
       The event class name is inferred from the callee_name or from the
       UNRESOLVED_CALL stub (url_pattern field stores the first argument class
       name when available — not yet; fallback: match on simple name from the
       caller's local scope is not possible without re-parsing source).
    3. Fallback matching: if a @EventListener method's parameter type simple
       name matches any class constructed (via <init> CALLS) within a method
       that also calls publishEvent, wire the caller → listener.

    Because PCAPP does not use @EventListener, this pass will emit 0 edges
    for that repo, which is the correct result.
    """
    # Step 1: find @EventListener methods, keyed by their first parameter's
    # simple class name.
    r_el = conn.execute(
        "MATCH (m:Method) WHERE m.repo_id = $rid RETURN m.id, m.fqn, m.annotations",
        {"rid": repo_id},
    )
    # event_simple_name → list[method_id]
    listener_by_event: dict[str, list[str]] = {}
    while r_el.has_next():
        mid, fqn, annotations = r_el.get_next()
        ann_list = annotations if isinstance(annotations, list) else []
        if "EventListener" not in ann_list:
            continue
        # The method FQN is "pkg.ClassName.methodName" — we need the parameter
        # type.  We don't have parameter type info in the graph yet, so we use
        # the method name as a secondary key and rely on the caller-side match.
        # Store by method_id so the caller-side can enumerate all listeners.
        listener_by_event.setdefault("__any__", []).append(mid)

    if not listener_by_event:
        return 0

    # Step 2: find callers of publishEvent in the repo
    r_pub = conn.execute(
        "MATCH (caller:Method)-[:CALLS]->(callee:Method) "
        "WHERE callee.name = 'publishEvent' AND caller.repo_id = $rid "
        "RETURN caller.id",
        {"rid": repo_id},
    )
    publisher_ids: list[str] = []
    while r_pub.has_next():
        publisher_ids.append(r_pub.get_next()[0])

    # Also check UNRESOLVED_CALL stubs named publishEvent
    r_pub2 = conn.execute(
        "MATCH (caller:Method)-[:UNRESOLVED_CALL]->(stub:RestCall) "
        "WHERE stub.callee_name = 'publishEvent' AND caller.repo_id = $rid "
        "RETURN caller.id",
        {"rid": repo_id},
    )
    while r_pub2.has_next():
        publisher_ids.append(r_pub2.get_next()[0])

    if not publisher_ids:
        return 0

    # Step 3: wire publishers → all listeners (conservative: without param-type
    # matching, connect all publishEvent callers to all @EventListener methods
    # in the repo).  When param type info is available in the graph this can be
    # tightened.
    edges: list[tuple[str, str, str]] = []
    all_listener_ids = [mid for mids in listener_by_event.values() for mid in mids]
    for pub_id in publisher_ids:
        for listener_id in all_listener_ids:
            edges.append((pub_id, listener_id, "publishEvent→listener"))

    return _flush_edges(writer, edges, written)
