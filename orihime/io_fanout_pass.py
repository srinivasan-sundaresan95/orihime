"""Static I/O fan-out detection via Tree-sitter AST analysis (G10)."""
from __future__ import annotations

# ---------------------------------------------------------------------------
# DB call method names
# ---------------------------------------------------------------------------
_DB_EXACT_NAMES: frozenset[str] = frozenset({
    "save", "saveAll", "delete", "deleteById", "findAll", "findById",
    "findOne", "getOne", "getById", "count", "existsById",
    "execute", "executeQuery", "executeUpdate", "prepareStatement",
    "query", "queryForObject", "queryForList",
})
_DB_STARTSWITH = "findBy"

# ---------------------------------------------------------------------------
# HTTP call method names
# ---------------------------------------------------------------------------
_HTTP_NAMES: frozenset[str] = frozenset({
    "exchange", "getForObject", "postForObject", "getForEntity",
    "postForEntity", "retrieve", "bodyToMono", "block",
})

# ---------------------------------------------------------------------------
# Cache call method names (only when receiver contains "cache"/"Cache" or
# equals "cacheManager", OR when @Cacheable/@CacheEvict is in annotations)
# ---------------------------------------------------------------------------
_CACHE_CALL_NAMES: frozenset[str] = frozenset({"get", "put", "evict"})
_CACHE_RECEIVER_TOKENS: frozenset[str] = frozenset({"cache", "Cache", "cacheManager"})

# ---------------------------------------------------------------------------
# Parallel wrapper detection
# ---------------------------------------------------------------------------
# Priority: coroutine > completable_future > reactor > spring_async
_KOTLIN_PARALLEL_NAMES: frozenset[str] = frozenset({"async", "coroutineScope", "withContext"})
_JAVA_PARALLEL_NAMES: frozenset[str] = frozenset({
    "supplyAsync", "runAsync", "allOf", "thenCompose", "thenCombine", "thenApply",
})
_REACTOR_PARALLEL_NAMES: frozenset[str] = frozenset({"zip", "merge", "when"})
_REACTOR_RECEIVERS: frozenset[str] = frozenset({"Mono", "Flux"})


def _node_text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _walk(node):
    """Depth-first walk of all nodes."""
    yield node
    for child in node.children:
        yield from _walk(child)


def _get_invocation_method_name(node, src: bytes, lang: str) -> str | None:
    """Extract the called method name from a method_invocation (Java) or call_expression (Kotlin)."""
    if lang == "java":
        children = node.children
        for i, c in enumerate(children):
            if c.type == "argument_list":
                for j in range(i - 1, -1, -1):
                    if children[j].type == "identifier":
                        return _node_text(children[j], src)
                break
        return None
    else:
        # Kotlin call_expression
        nav = None
        for child in node.children:
            if child.type == "navigation_expression":
                nav = child
                break
        if nav is not None:
            last_id = None
            for child in nav.children:
                if child.type == "identifier":
                    last_id = _node_text(child, src)
            return last_id
        else:
            for child in node.children:
                if child.type == "identifier":
                    return _node_text(child, src)
        return None


def _get_receiver_name(node, src: bytes, lang: str) -> str:
    """Extract the receiver/object name from a call expression."""
    if lang == "java":
        children = node.children
        for i, c in enumerate(children):
            if c.type == "argument_list":
                # The identifier before the dot (if any) is the receiver
                for j in range(0, i):
                    if children[j].type == "identifier":
                        method_name_node = None
                        for k in range(i - 1, -1, -1):
                            if children[k].type == "identifier":
                                method_name_node = children[k]
                                break
                        if children[j] is not method_name_node:
                            return _node_text(children[j], src)
                        break
                break
        return ""
    else:
        # Kotlin: look at navigation_expression
        for child in node.children:
            if child.type == "navigation_expression":
                # Walk left side to find root identifier
                for sub in child.children:
                    if sub.type == "identifier":
                        return _node_text(sub, src)
                    elif sub.type == "navigation_expression":
                        # nested nav, get leftmost
                        for subsub in sub.children:
                            if subsub.type == "identifier":
                                return _node_text(subsub, src)
                        break
                    break
        return ""


def _get_reactor_receiver(node, src: bytes, lang: str) -> str:
    """Get the Mono/Flux receiver name for reactor parallel wrapper detection."""
    if lang == "java":
        children = node.children
        for i, c in enumerate(children):
            if c.type == "argument_list":
                for j in range(0, i):
                    if children[j].type == "identifier":
                        method_name_node = None
                        for k in range(i - 1, -1, -1):
                            if children[k].type == "identifier":
                                method_name_node = children[k]
                                break
                        if children[j] is not method_name_node:
                            return _node_text(children[j], src)
                        break
                break
        return ""
    else:
        for child in node.children:
            if child.type == "navigation_expression":
                for sub in child.children:
                    if sub.type == "identifier":
                        return _node_text(sub, src)
                    break
        return ""


def _build_parallel_wrapper_node_ids(
    method_body_node,
    src: bytes,
    lang: str,
    method_annotations: list[str],
) -> tuple[set[int], str]:
    """Walk the AST and return (set_of_parallel_ancestor_node_ids, wrapper_type).

    wrapper_type is one of: "coroutine", "completable_future", "reactor",
    "spring_async", or "".

    Priority: coroutine > completable_future > reactor > spring_async.
    """
    parallel_node_ids: set[int] = set()
    found_types: set[str] = set()

    # @Async marks whole method as parallel
    ann_set = set(method_annotations)
    if "Async" in ann_set:
        # Mark all descendants of the method body as parallel
        for desc in _walk(method_body_node):
            parallel_node_ids.add(id(desc))
        found_types.add("spring_async")

    inv_type = "method_invocation" if lang == "java" else "call_expression"

    for node in _walk(method_body_node):
        if node.type != inv_type:
            continue
        name = _get_invocation_method_name(node, src, lang)
        if name is None:
            continue

        wrapper = None
        if lang == "kotlin":
            if name in _KOTLIN_PARALLEL_NAMES:
                wrapper = "coroutine"
        else:
            if name in _JAVA_PARALLEL_NAMES:
                wrapper = "completable_future"

        if wrapper is None and name in _REACTOR_PARALLEL_NAMES:
            receiver = _get_reactor_receiver(node, src, lang)
            if receiver in _REACTOR_RECEIVERS:
                wrapper = "reactor"

        if wrapper is not None:
            # All descendant node IDs of this node are "inside parallel"
            for desc in _walk(node):
                parallel_node_ids.add(id(desc))
            found_types.add(wrapper)

    # Determine dominant wrapper by priority
    wrapper_str = ""
    for priority_wrapper in ("coroutine", "completable_future", "reactor", "spring_async"):
        if priority_wrapper in found_types:
            wrapper_str = priority_wrapper
            break

    return parallel_node_ids, wrapper_str


def _is_cache_call(method_name: str, node, src: bytes, lang: str, method_annotations: list[str]) -> bool:
    """Return True if this is a cache-related I/O call."""
    ann_set = set(method_annotations)
    if "Cacheable" in ann_set or "CacheEvict" in ann_set:
        return True
    if method_name not in _CACHE_CALL_NAMES:
        return False
    receiver = _get_receiver_name(node, src, lang)
    # Check if receiver contains "cache"/"Cache" or equals "cacheManager"
    return (
        "cache" in receiver
        or "Cache" in receiver
        or receiver == "cacheManager"
    )


def _is_io_call(node, src: bytes, lang: str, method_annotations: list[str]) -> bool:
    """Return True if *node* is an I/O call site (DB, HTTP, or cache)."""
    inv_type = "method_invocation" if lang == "java" else "call_expression"
    if node.type != inv_type:
        return False

    name = _get_invocation_method_name(node, src, lang)
    if name is None:
        return False

    # DB call
    if name in _DB_EXACT_NAMES or name.startswith(_DB_STARTSWITH):
        return True

    # HTTP call
    if name in _HTTP_NAMES:
        return True

    # Cache call
    if _is_cache_call(name, node, src, lang, method_annotations):
        return True

    return False


def detect_io_fanout(
    method_body_node,
    src: bytes,
    lang: str,
    method_annotations: list[str],
) -> dict:
    """Detect I/O call sites in a method body and classify as parallel or serial.

    Returns:
        {
            "total": int,
            "parallel_count": int,
            "serial_count": int,
            "parallel_wrapper": str,  # "coroutine"|"completable_future"|"reactor"|"spring_async"|""
        }
    """
    if method_body_node is None:
        return {"total": 0, "parallel_count": 0, "serial_count": 0, "parallel_wrapper": ""}

    # Step 1: build the parallel wrapper ancestor set
    parallel_node_ids, parallel_wrapper = _build_parallel_wrapper_node_ids(
        method_body_node, src, lang, method_annotations
    )

    inv_type = "method_invocation" if lang == "java" else "call_expression"
    total = 0
    parallel_count = 0
    serial_count = 0

    for node in _walk(method_body_node):
        if not _is_io_call(node, src, lang, method_annotations):
            continue
        total += 1
        # Check if any ancestor ID is in the parallel set.
        # Since we stored ALL descendant IDs of each parallel wrapper,
        # we just check if id(node) itself is in the set.
        if id(node) in parallel_node_ids:
            parallel_count += 1
        else:
            serial_count += 1

    return {
        "total": total,
        "parallel_count": parallel_count,
        "serial_count": serial_count,
        "parallel_wrapper": parallel_wrapper,
    }
