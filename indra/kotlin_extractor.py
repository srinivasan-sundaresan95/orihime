"""Kotlin tree-sitter extractor for the Indra code knowledge graph."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from indra.language import ExtractResult, register

# ---------------------------------------------------------------------------
# Spring endpoint annotation → HTTP method mapping
# ---------------------------------------------------------------------------
_MAPPING_TO_METHOD: dict[str, str] = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
    "RequestMapping": "GET",  # default; overridden by method= if present
}

# RestClient / WebClient / RestTemplate chain method → HTTP verb
_CHAIN_METHOD_TO_HTTP: dict[str, str] = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "delete": "DELETE",
    "patch": "PATCH",
    "head": "HEAD",
    "options": "OPTIONS",
    "exchange": "GET",
}

_REST_CLIENT_ROOTS = {"restClient", "webClient", "restTemplate", "RestClient", "WebClient", "RestTemplate"}

_KOTLIN_DATA_GENERATED_NAMES: frozenset[str] = frozenset({
    "copy", "toString", "hashCode", "equals"
})
_KOTLIN_COMPONENT_RE = re.compile(r'^component\d+$')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _node_text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _child_by_type(node, type_: str):
    for child in node.children:
        if child.type == type_:
            return child
    return None


def _children_by_type(node, type_: str):
    return [c for c in node.children if c.type == type_]


def _simple_identifier(node, src: bytes) -> str | None:
    """Return the first 'identifier' direct child text, or None."""
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child, src)
    return None


def _collect_annotations(modifiers_node, src: bytes) -> list[str]:
    """Extract annotation name strings from a modifiers node."""
    if modifiers_node is None:
        return []
    result: list[str] = []
    for child in modifiers_node.children:
        if child.type == "annotation":
            # child structure:  '@' user_type  OR  '@' constructor_invocation
            for sub in child.children:
                if sub.type == "user_type":
                    name = _simple_identifier(sub, src)
                    if name:
                        result.append(name)
                    break
                elif sub.type == "constructor_invocation":
                    user_type = _child_by_type(sub, "user_type")
                    if user_type:
                        name = _simple_identifier(user_type, src)
                        if name:
                            result.append(name)
                    break
    return result


def _annotation_arg(annotation_node, src: bytes) -> str | None:
    """Return the first string literal argument of an annotation, if any."""
    for child in annotation_node.children:
        if child.type == "constructor_invocation":
            val_args = _child_by_type(child, "value_arguments")
            if val_args:
                for va in val_args.children:
                    if va.type == "value_argument":
                        for vac in va.children:
                            if vac.type == "string_literal":
                                # Extract content between quotes
                                parts = []
                                for sc in vac.children:
                                    if sc.type == "string_content":
                                        parts.append(_node_text(sc, src))
                                return "".join(parts)
    return None


def _is_suspend(modifiers_node, src: bytes) -> bool:
    if modifiers_node is None:
        return False
    for child in modifiers_node.children:
        if child.type == "function_modifier":
            if _node_text(child, src).strip() == "suspend":
                return True
    return False


def _is_data_class(modifiers_node, src: bytes) -> bool:
    """Return True if the modifiers include the 'data' keyword modifier."""
    if modifiers_node is None:
        return False
    for child in modifiers_node.children:
        if child.type in ("class_modifier", "modifier") and _node_text(child, src).strip() == "data":
            return True
    return False


def _is_kotlin_data_generated(method_name: str, is_data_class: bool) -> bool:
    """Return True if method_name is compiler-generated for a Kotlin data class."""
    if not is_data_class:
        return False
    if method_name in _KOTLIN_DATA_GENERATED_NAMES:
        return True
    if _KOTLIN_COMPONENT_RE.match(method_name):
        return True
    return False


def _package_name(root_node, src: bytes) -> str:
    """Extract package name from package_header node."""
    pkg_header = _child_by_type(root_node, "package_header")
    if pkg_header is None:
        return ""
    # qualified_identifier contains the dotted name
    qi = _child_by_type(pkg_header, "qualified_identifier")
    if qi:
        return _node_text(qi, src).strip()
    # fallback: identifier
    ident = _child_by_type(pkg_header, "identifier")
    if ident:
        return _node_text(ident, src).strip()
    return ""


from .path_utils import compile_path_regex as _path_regex


# ---------------------------------------------------------------------------
# Inheritance extraction
# ---------------------------------------------------------------------------

def _extract_kotlin_supertypes(
    class_node,
    source_bytes: bytes,
    class_fqn: str,
    class_id: str,
    package: str,
) -> list[dict]:
    """Extract EXTENDS/IMPLEMENTS edges for a Kotlin class_declaration or object_declaration.

    delegation_specifiers children:
    - delegation_specifier with constructor_invocation → EXTENDS
    - delegation_specifier with user_type directly → IMPLEMENTS

    FQN: f"{package}.{simple}" if package else simple.
    NOT called for interface_declaration or companion_object.
    """
    edges = []
    delegation_specs = None
    for child in class_node.children:
        if child.type == "delegation_specifiers":
            delegation_specs = child
            break
    if delegation_specs is None:
        return []

    def _resolve(simple: str) -> str:
        return f"{package}.{simple}" if package else simple

    for spec in delegation_specs.children:
        if spec.type != "delegation_specifier":
            continue
        edge_type = None
        simple_name = None
        for child in spec.children:
            if child.type == "constructor_invocation":
                # class extension: BaseService()
                edge_type = "EXTENDS"
                for cc in child.children:
                    if cc.type == "user_type":
                        for id_node in cc.children:
                            if id_node.type == "identifier":
                                simple_name = source_bytes[id_node.start_byte:id_node.end_byte].decode("utf-8", errors="replace")
                                break
                        break
                break
            elif child.type == "user_type":
                # interface implementation
                edge_type = "IMPLEMENTS"
                for id_node in child.children:
                    if id_node.type == "identifier":
                        simple_name = source_bytes[id_node.start_byte:id_node.end_byte].decode("utf-8", errors="replace")
                        break
                break
        if simple_name and edge_type:
            parent_fqn = _resolve(simple_name)
            if parent_fqn != class_fqn:
                edges.append({"child_id": class_id, "parent_fqn": parent_fqn, "edge_type": edge_type})

    return edges


# ---------------------------------------------------------------------------
# RestClient / WebClient chain detection
# ---------------------------------------------------------------------------

def _extract_chain_info(call_expr_node, src: bytes) -> tuple[str | None, str | None]:
    """
    Walk a call_expression chain and extract (http_method, url).

    Looks for a pattern like:
        <root>.get().uri("http://...").retrieve()
    Returns (http_verb, url_string) or (None, None).
    """
    # Collect all navigation identifiers and string args along the chain
    chain_methods: list[str] = []
    url: str | None = None

    def walk(node):
        nonlocal url
        if node.type == "call_expression":
            nav = _child_by_type(node, "navigation_expression")
            val_args = _child_by_type(node, "value_arguments")
            if nav:
                walk(nav)
                # The last identifier in nav is the called method
                last_id = None
                for child in nav.children:
                    if child.type == "identifier":
                        last_id = _node_text(child, src)
                if last_id:
                    chain_methods.append(last_id)
                    # If this is .uri(...) grab the string arg
                    if last_id == "uri" and val_args:
                        for va in val_args.children:
                            if va.type == "value_argument":
                                for vac in va.children:
                                    if vac.type == "string_literal":
                                        parts = [
                                            _node_text(sc, src)
                                            for sc in vac.children
                                            if sc.type == "string_content"
                                        ]
                                        url = "".join(parts)
        elif node.type == "navigation_expression":
            # Walk the left side
            for child in node.children:
                if child.type in ("call_expression", "navigation_expression"):
                    walk(child)
                    break
            # Collect the rightmost identifier
            last_id = None
            for child in node.children:
                if child.type == "identifier":
                    last_id = _node_text(child, src)
            if last_id and last_id not in chain_methods:
                chain_methods.append(last_id)

    walk(call_expr_node)

    # Determine if this is a RestClient/WebClient chain
    has_rest_root = bool(_REST_CLIENT_ROOTS & set(chain_methods))
    if not has_rest_root:
        # Could be builder pattern: check if chain includes retrieve/exchange
        rest_signals = {"retrieve", "exchange", "execute"}
        if not (rest_signals & set(chain_methods)):
            return None, None

    # Find HTTP verb
    http_method: str | None = None
    for m in chain_methods:
        if m in _CHAIN_METHOD_TO_HTTP:
            http_method = _CHAIN_METHOD_TO_HTTP[m]
            break

    return http_method, url


def _find_rest_calls_in_node(node, src: bytes, caller_method_id: str, repo_id: str) -> list[dict]:
    """Recursively search a function body for RestClient/WebClient call chains."""
    results: list[dict] = []

    def walk(n):
        if n.type == "call_expression":
            http_method, url = _extract_chain_info(n, src)
            if url is not None:
                results.append({
                    "id": str(uuid.uuid4()),
                    "http_method": http_method or "GET",
                    "url_pattern": url,
                    "caller_method_id": caller_method_id,
                    "repo_id": repo_id,
                })
                # Don't recurse into this subtree to avoid duplicate matches
                return
        for child in n.children:
            walk(child)

    walk(node)
    return results


# ---------------------------------------------------------------------------
# Main extractor
# ---------------------------------------------------------------------------

@dataclass
class KotlinExtractor:
    language: str = "kotlin"
    file_extensions: frozenset[str] = field(
        default_factory=lambda: frozenset({".kt", ".kts"})
    )

    def extract(self, tree, source_bytes: bytes, file_id: str, repo_id: str) -> ExtractResult:
        src = source_bytes
        root = tree.root_node

        classes: list[dict] = []
        methods: list[dict] = []
        endpoints: list[dict] = []
        rest_calls: list[dict] = []
        inheritance_edges: list[dict] = []

        package = _package_name(root, src)

        # Collect top-level and nested class-like declarations
        for class_node in _iter_class_nodes(root):
            modifiers = _child_by_type(class_node, "modifiers")
            class_annotations = _collect_annotations(modifiers, src)
            data_class = _is_data_class(modifiers, src)

            class_name = _resolve_class_name(class_node, src)
            if class_name is None:
                continue
            fqn = f"{package}.{class_name}" if package else class_name

            is_interface = class_node.type == "interface_declaration"

            class_id = str(uuid.uuid4())
            classes.append({
                "id": class_id,
                "name": class_name,
                "fqn": fqn,
                "file_id": file_id,
                "repo_id": repo_id,
                "is_interface": is_interface,
                "annotations": class_annotations,
            })

            # Synthetic <init> method — lets the resolver emit CALLS edges for
            # `ClassName(...)` constructor calls without any schema changes.
            # Only regular classes can be instantiated this way; object
            # declarations (singletons), companion objects, and interfaces
            # cannot be constructed via a call_expression.
            if class_node.type == "class_declaration":
                methods.append({
                    "id": str(uuid.uuid4()),
                    "name": "<init>",
                    "fqn": f"{fqn}.<init>",
                    "class_id": class_id,
                    "file_id": file_id,
                    "repo_id": repo_id,
                    "line_start": 0,
                    "is_suspend": False,
                    "annotations": [],
                    "generated": False,
                })

            # Extract EXTENDS/IMPLEMENTS inheritance edges (class and object only)
            if class_node.type in ("class_declaration", "object_declaration"):
                inh = _extract_kotlin_supertypes(class_node, src, fqn, class_id, package)
                inheritance_edges.extend(inh)

            # Class-level @RequestMapping prefix
            class_prefix = ""
            for ann_node in _iter_annotation_nodes(modifiers):
                ann_name = _annotation_name(ann_node, src)
                if ann_name == "RequestMapping":
                    val = _annotation_arg(ann_node, src)
                    if val:
                        class_prefix = val
                    break

            # Find function declarations in this class body
            class_body = _child_by_type(class_node, "class_body")
            if class_body is None:
                continue

            for fn_node in _iter_function_nodes(class_body):
                fn_modifiers = _child_by_type(fn_node, "modifiers")
                fn_annotations = _collect_annotations(fn_modifiers, src)

                fn_name_node = _child_by_type(fn_node, "identifier")
                if fn_name_node is None:
                    continue
                fn_name = _node_text(fn_name_node, src).strip()
                fn_fqn = f"{fqn}.{fn_name}"

                is_suspend = _is_suspend(fn_modifiers, src)
                line_start = fn_node.start_point[0] + 1  # 1-based
                generated = _is_kotlin_data_generated(fn_name, data_class)

                method_id = str(uuid.uuid4())
                methods.append({
                    "id": method_id,
                    "name": fn_name,
                    "fqn": fn_fqn,
                    "class_id": class_id,
                    "file_id": file_id,
                    "repo_id": repo_id,
                    "line_start": line_start,
                    "is_suspend": is_suspend,
                    "annotations": fn_annotations,
                    "generated": generated,
                })

                # Detect endpoint annotations
                for ann_node in _iter_annotation_nodes(fn_modifiers):
                    ann_name = _annotation_name(ann_node, src)
                    if ann_name in _MAPPING_TO_METHOD:
                        http_method = _MAPPING_TO_METHOD[ann_name]
                        ann_path = _annotation_arg(ann_node, src) or ""
                        full_path = class_prefix.rstrip("/") + "/" + ann_path.lstrip("/") if ann_path else class_prefix
                        full_path = full_path or "/"
                        endpoints.append({
                            "id": str(uuid.uuid4()),
                            "http_method": http_method,
                            "path": full_path,
                            "path_regex": _path_regex(full_path),
                            "handler_method_id": method_id,
                            "repo_id": repo_id,
                        })

                # Detect RestClient / WebClient calls in the function body
                fn_body = _child_by_type(fn_node, "function_body")
                if fn_body:
                    rc = _find_rest_calls_in_node(fn_body, src, method_id, repo_id)
                    rest_calls.extend(rc)

        # Collect top-level function declarations (not inside any class/object)
        # These include extension functions and plain top-level functions.
        # We emit a synthetic "<FileNameKt>" class for them.
        top_level_fns = _iter_top_level_function_nodes(root)
        fn_list = list(top_level_fns)
        if fn_list:
            kt_class_name = _synthetic_kt_class_name(file_id)
            kt_fqn = f"{package}.{kt_class_name}" if package else kt_class_name
            kt_class_id = str(uuid.uuid4())
            classes.append({
                "id": kt_class_id,
                "name": kt_class_name,
                "fqn": kt_fqn,
                "file_id": file_id,
                "repo_id": repo_id,
                "is_interface": False,
                "annotations": [],
            })
            for fn_node in fn_list:
                fn_modifiers = _child_by_type(fn_node, "modifiers")
                fn_annotations = _collect_annotations(fn_modifiers, src)

                fn_name_node = _child_by_type(fn_node, "identifier")
                if fn_name_node is None:
                    continue
                fn_name = _node_text(fn_name_node, src).strip()
                fn_fqn = f"{kt_fqn}.{fn_name}"

                is_suspend = _is_suspend(fn_modifiers, src)
                line_start = fn_node.start_point[0] + 1  # 1-based

                method_id = str(uuid.uuid4())
                methods.append({
                    "id": method_id,
                    "name": fn_name,
                    "fqn": fn_fqn,
                    "class_id": kt_class_id,
                    "file_id": file_id,
                    "repo_id": repo_id,
                    "line_start": line_start,
                    "is_suspend": is_suspend,
                    "annotations": fn_annotations,
                    "generated": False,
                })

                # Detect RestClient / WebClient calls in top-level function body
                fn_body = _child_by_type(fn_node, "function_body")
                if fn_body:
                    rc = _find_rest_calls_in_node(fn_body, src, method_id, repo_id)
                    rest_calls.extend(rc)

        return ExtractResult(
            classes=classes,
            methods=methods,
            endpoints=endpoints,
            rest_calls=rest_calls,
            inheritance_edges=inheritance_edges,
        )


# ---------------------------------------------------------------------------
# Tree traversal helpers
# ---------------------------------------------------------------------------

_CLASS_NODE_TYPES = frozenset({
    "class_declaration",
    "object_declaration",
    "companion_object",
    "interface_declaration",
})


def _resolve_class_name(class_node, src: bytes) -> str | None:
    """Return the class/object name for a class-like node.

    Handles:
    - ``class_declaration``, ``object_declaration``, ``interface_declaration``:
      have a direct ``identifier`` child that holds the name.
    - ``companion_object`` with an explicit name: has an ``identifier`` child.
    - ``companion_object`` without a name (anonymous): synthesize name as
      ``<EnclosingClassName>Companion`` by walking up to the enclosing class.
    """
    name_node = _child_by_type(class_node, "identifier")
    if name_node is not None:
        return _node_text(name_node, src).strip()

    # Anonymous companion object — derive name from enclosing class
    if class_node.type == "companion_object":
        # parent is class_body, grandparent is the enclosing class declaration
        parent = class_node.parent  # class_body
        if parent is not None:
            grandparent = parent.parent  # class_declaration (or object_declaration)
            if grandparent is not None:
                enclosing_name_node = _child_by_type(grandparent, "identifier")
                if enclosing_name_node is not None:
                    enclosing_name = _node_text(enclosing_name_node, src).strip()
                    return f"{enclosing_name}Companion"
        return "Companion"  # fallback if no enclosing class found

    return None


def _synthetic_kt_class_name(file_id: str) -> str:
    """Derive a synthetic Kotlin top-level class name from a file identifier.

    If *file_id* looks like a ``.kt`` filename (e.g. ``ExtensionFunctions.kt``),
    return ``ExtensionFunctionsKt``.  Otherwise return ``TopLevelFunctionsKt``.
    """
    if file_id.endswith(".kt"):
        stem = file_id[:-3]
        # Strip any leading path components
        stem = stem.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        return f"{stem}Kt"
    return "TopLevelFunctionsKt"


def _iter_class_nodes(node):
    """Yield all class/object/interface declarations at any depth."""
    for child in node.children:
        if child.type in _CLASS_NODE_TYPES:
            yield child
            yield from _iter_class_nodes(child)
        else:
            yield from _iter_class_nodes(child)


def _iter_top_level_function_nodes(root_node):
    """Yield ``function_declaration`` nodes that are direct children of the source file root.

    These represent top-level Kotlin functions (including extension functions)
    that are NOT enclosed in any class, object, or interface body.
    """
    for child in root_node.children:
        if child.type == "function_declaration":
            yield child


def _iter_function_nodes(class_body_node):
    """Yield direct function_declaration children of a class_body."""
    for child in class_body_node.children:
        if child.type == "function_declaration":
            yield child


def _iter_annotation_nodes(modifiers_node):
    """Yield annotation nodes from a modifiers node."""
    if modifiers_node is None:
        return
    for child in modifiers_node.children:
        if child.type == "annotation":
            yield child


def _annotation_name(annotation_node, src: bytes) -> str | None:
    """Return the annotation class name (e.g. 'GetMapping')."""
    for child in annotation_node.children:
        if child.type == "user_type":
            return _simple_identifier(child, src)
        if child.type == "constructor_invocation":
            user_type = _child_by_type(child, "user_type")
            if user_type:
                return _simple_identifier(user_type, src)
    return None


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------
register(KotlinExtractor())
