"""Static complexity hint detection via Tree-sitter AST analysis."""
from __future__ import annotations

# Loop node types per language
_JAVA_LOOP_TYPES = frozenset({
    "for_statement",
    "enhanced_for_statement",
    "while_statement",
    "do_statement",
})

_KOTLIN_LOOP_TYPES = frozenset({
    "for_statement",
    "while_statement",
    "do_while_statement",
})

# JPA repository method name prefixes/names that signal a DB fetch
_JPA_FETCH_NAMES = frozenset({
    "findById", "findAll", "findOne", "getOne", "getById",
    "load", "fetch",
})

# Prefix for findBy* patterns (used as startswith check)
_JPA_FIND_BY_PREFIX = "findBy"


def _node_text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _walk(node):
    """Depth-first walk of all nodes."""
    yield node
    for child in node.children:
        yield from _walk(child)


def _loop_types(lang: str) -> frozenset:
    if lang == "kotlin":
        return _KOTLIN_LOOP_TYPES
    return _JAVA_LOOP_TYPES


def _get_invocation_method_name(node, src: bytes, lang: str) -> str | None:
    """Extract the called method name from a method_invocation (Java) or call_expression (Kotlin)."""
    if lang == "java":
        # method_invocation: the identifier just before argument_list is the method name
        children = node.children
        for i, c in enumerate(children):
            if c.type == "argument_list":
                for j in range(i - 1, -1, -1):
                    if children[j].type == "identifier":
                        return _node_text(children[j], src)
                break
        return None
    else:
        # Kotlin: call_expression has navigation_expression + value_arguments
        # navigation_expression: <expr> . <identifier>
        # OR simple: <identifier> value_arguments
        nav = None
        for child in node.children:
            if child.type == "navigation_expression":
                nav = child
                break
        if nav is not None:
            # Last identifier in navigation_expression is the called method
            last_id = None
            for child in nav.children:
                if child.type == "identifier":
                    last_id = _node_text(child, src)
            return last_id
        else:
            # simple call: first identifier is the function name
            for child in node.children:
                if child.type == "identifier":
                    return _node_text(child, src)
        return None


def _is_loop_node(node, lang: str) -> bool:
    return node.type in _loop_types(lang)


def _collect_direct_loop_bodies(loop_node, lang: str):
    """Return the immediate body/block nodes of a loop (not recursing into inner loops)."""
    # For Java for/enhanced_for/while/do, the body is typically the last block child
    # For Kotlin for/while/do_while, same pattern
    bodies = []
    for child in loop_node.children:
        if child.type == "block":
            bodies.append(child)
        elif child.type == "statement":
            bodies.append(child)
    # If no explicit block, collect all non-keyword children as potential body nodes
    if not bodies:
        for child in loop_node.children:
            if child.is_named and child.type not in _loop_types(lang):
                bodies.append(child)
    return bodies


def _find_invocations_in_subtree(node, src: bytes, lang: str):
    """Yield all method_invocation (Java) / call_expression (Kotlin) nodes in subtree."""
    inv_type = "method_invocation" if lang == "java" else "call_expression"
    for n in _walk(node):
        if n.type == inv_type:
            yield n


def _contains_nested_loop(body_node, lang: str) -> bool:
    """Return True if body_node (a loop's body) contains any nested loop at any depth."""
    loop_set = _loop_types(lang)
    for n in _walk(body_node):
        if n is body_node:
            continue
        if n.type in loop_set:
            return True
    return False


def _detect_nested_loops(method_body, src: bytes, lang: str) -> bool:
    """Detect O(n^2): a loop that itself contains another loop."""
    loop_set = _loop_types(lang)
    for node in _walk(method_body):
        if node.type not in loop_set:
            continue
        # Check if this loop's subtree (excluding itself at the root) has another loop
        for child in node.children:
            if _contains_nested_loop(child, lang):
                return True
    return False


def _detect_list_scan_in_loop(method_body, src: bytes, lang: str) -> bool:
    """Detect O(n^2)-list-scan: contains/indexOf call inside a loop body."""
    loop_set = _loop_types(lang)
    inv_type = "method_invocation" if lang == "java" else "call_expression"
    list_scan_names = frozenset({"contains", "indexOf"})

    for loop_node in _walk(method_body):
        if loop_node.type not in loop_set:
            continue
        # Walk everything inside this loop
        for inv_node in _walk(loop_node):
            if inv_node is loop_node:
                continue
            if inv_node.type != inv_type:
                continue
            name = _get_invocation_method_name(inv_node, src, lang)
            if name in list_scan_names:
                return True
    return False


def _detect_recursive(method_body, src: bytes, lang: str, method_name: str) -> bool:
    """Detect recursive call: method invokes itself by name."""
    if method_name in ("<init>", "init", ""):
        return False
    inv_type = "method_invocation" if lang == "java" else "call_expression"
    for node in _walk(method_body):
        if node.type != inv_type:
            continue
        name = _get_invocation_method_name(node, src, lang)
        if name == method_name:
            return True
    return False


def _detect_n_plus_1(method_body, src: bytes, lang: str) -> bool:
    """Detect n+1-risk: JPA fetch method call inside a loop."""
    loop_set = _loop_types(lang)
    inv_type = "method_invocation" if lang == "java" else "call_expression"

    for loop_node in _walk(method_body):
        if loop_node.type not in loop_set:
            continue
        for inv_node in _walk(loop_node):
            if inv_node is loop_node:
                continue
            if inv_node.type != inv_type:
                continue
            name = _get_invocation_method_name(inv_node, src, lang)
            if name is None:
                continue
            if (name in _JPA_FETCH_NAMES
                    or name.startswith(_JPA_FIND_BY_PREFIX)
                    or name in ("getOne", "load", "fetch")):
                return True
    return False


def _detect_unbounded_query(method_body, src: bytes, lang: str, param_names: list[str]) -> bool:
    """Detect unbounded-query: findAll/findBy* call and no Pageable parameter."""
    # Check if any param is a pageable
    has_pageable = any("pageable" in p.lower() for p in param_names)
    if has_pageable:
        return False

    inv_type = "method_invocation" if lang == "java" else "call_expression"
    for node in _walk(method_body):
        if node.type != inv_type:
            continue
        name = _get_invocation_method_name(node, src, lang)
        if name is None:
            continue
        if name == "findAll" or name.startswith("findAll") or name.startswith(_JPA_FIND_BY_PREFIX):
            return True
    return False


def detect_complexity_hints(
    method_node,
    src: bytes,
    method_name: str,
    param_names: list[str],
    lang: str,
) -> str:
    """Return comma-separated complexity hint tags, or '' if none detected.

    Args:
        method_node: tree-sitter Node for the method body (block/function_body).
        src: source bytes of the full file.
        method_name: name of the enclosing method.
        param_names: list of parameter names (used for Pageable detection).
        lang: "java" or "kotlin".
    """
    if method_node is None:
        return ""

    hints: list[str] = []

    if _detect_nested_loops(method_node, src, lang):
        hints.append("O(n2)-candidate")

    if _detect_list_scan_in_loop(method_node, src, lang):
        hints.append("O(n2)-list-scan")

    if _detect_recursive(method_node, src, lang, method_name):
        hints.append("recursive")

    if _detect_n_plus_1(method_node, src, lang):
        hints.append("n+1-risk")

    if _detect_unbounded_query(method_node, src, lang, param_names):
        hints.append("unbounded-query")

    return ",".join(hints)
