"""Kotlin tree-sitter extractor for the Orihime code knowledge graph."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from orihime.complexity_pass import detect_complexity_hints
from orihime.io_fanout_pass import detect_io_fanout
from orihime.language import ExtractResult, register

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

# Annotations that mark a Kotlin method as a messaging/scheduling entry point
_ENTRY_POINT_ANNOTATIONS: frozenset[str] = frozenset(
    {"KafkaListener", "Scheduled", "JmsListener", "RabbitListener",
     "PostConstruct", "PreDestroy", "Bean"}
)

_KOTLIN_DATA_GENERATED_NAMES: frozenset[str] = frozenset({
    "copy", "toString", "hashCode", "equals"
})
_KOTLIN_COMPONENT_RE = re.compile(r'^component\d+$')

# Spring stereotype annotations that mark a class as a DI-managed bean.
# When one of these is present on a Kotlin class, each IMPLEMENTS edge is
# recorded in impl_map so the resolver can redirect interface-typed call sites
# to the concrete implementation.
_SPRING_COMPONENT_ANNOTATIONS: frozenset[str] = frozenset({
    "Service", "Component", "Repository", "Controller", "RestController",
})


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


def _string_literal_text(node, src: bytes) -> str:
    """Extract string content from a string_literal node."""
    parts = []
    for sc in node.children:
        if sc.type == "string_content":
            parts.append(_node_text(sc, src))
    return "".join(parts)


def _annotation_arg(annotation_node, src: bytes) -> str | None:
    """Return the first string literal argument of an annotation, or None.

    Use _annotation_args() to get all values (e.g. multi-path @GetMapping).
    """
    args = _annotation_args(annotation_node, src)
    return args[0] if args else None


def _annotation_args(annotation_node, src: bytes) -> list[str]:
    """Return ALL string literal arguments of an annotation.

    Handles:
    1. @GetMapping("/path")                      — single positional string
    2. @GetMapping("/v5/path", "/v6/path")        — multiple positional strings
    3. @GetMapping(value = ["/v5/p", "/v6/p"])   — named array arg
    4. @GetMapping(path = ["/v5/p", "/v6/p"])    — named array arg
    """
    results: list[str] = []
    for child in annotation_node.children:
        if child.type == "constructor_invocation":
            val_args = _child_by_type(child, "value_arguments")
            if val_args:
                for va in val_args.children:
                    if va.type != "value_argument":
                        continue
                    label: str | None = None
                    expr_node = None
                    for vac in va.children:
                        if vac.type == "simple_identifier" and label is None:
                            label = _node_text(vac, src)
                        elif vac.type == "string_literal":
                            results.append(_string_literal_text(vac, src))
                        elif vac.type == "collection_literal":
                            expr_node = vac
                    if expr_node is not None and expr_node.type == "collection_literal":
                        if label is None or label in ("value", "path"):
                            for cl_child in expr_node.children:
                                if cl_child.type == "string_literal":
                                    results.append(_string_literal_text(cl_child, src))
    return results


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


def _import_map(root_node, src: bytes) -> dict[str, str]:
    """Return {simple_name: fully_qualified_name} from import declarations.

    Handles:
    - ``import com.example.Foo``            → {"Foo": "com.example.Foo"}
    - ``import com.example.Foo as Bar``     → {"Bar": "com.example.Foo"}
    - ``import com.example.Foo.method``     → {"method": "com.example.Foo.method"}
                                               AND {"Foo": "com.example.Foo"} (parent class)

    Wildcard imports (``import com.example.*``) are intentionally skipped —
    we cannot safely resolve them without a full classpath.
    """
    result: dict[str, str] = {}
    for child in root_node.children:
        # tree-sitter-kotlin uses node type "import" for import declarations
        if child.type not in ("import_header", "import"):
            continue
        # The qualified_identifier child holds the full dotted name
        qi = _child_by_type(child, "qualified_identifier")
        if qi is None:
            continue
        fqn = _node_text(qi, src).strip()
        # Skip wildcard imports
        if fqn.endswith(".*") or fqn.endswith("*"):
            continue
        # Check for alias: import_alias child
        alias: str | None = None
        import_alias = _child_by_type(child, "import_alias")
        if import_alias:
            for alias_sub in import_alias.children:
                if alias_sub.type in ("identifier", "simple_identifier"):
                    alias = _node_text(alias_sub, src).strip()
        simple = alias if alias else fqn.rsplit(".", 1)[-1]
        result[simple] = fqn
    return result


from .path_utils import compile_path_regex as _path_regex


# ---------------------------------------------------------------------------
# Field type extraction (for property-chain call resolution)
# ---------------------------------------------------------------------------

_LIST_TYPES = frozenset({"List", "MutableList", "Collection", "Iterable", "Set", "MutableSet"})


def _simple_type_from_node(type_node, source_bytes: bytes) -> str | None:
    """Extract the simple type name from a Kotlin type AST node.

    For collection types (List<Foo>, etc.), returns the element type Foo
    rather than "List" — this enables resolving `it.method()` inside lambdas
    like `list.forEach { it.method() }`.
    For non-collection types, returns the outermost simple name.
    """
    if type_node is None:
        return None
    if type_node.type == "nullable_type":
        for child in type_node.children:
            if child.type in ("user_type", "type_reference"):
                return _simple_type_from_node(child, source_bytes)
        return None
    if type_node.type in ("user_type", "type_reference"):
        outer_name = None
        for child in type_node.children:
            if child.type in ("identifier", "type_identifier", "simple_identifier"):
                outer_name = source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
                break
        if outer_name in _LIST_TYPES:
            for child in type_node.children:
                if child.type == "type_arguments":
                    for arg in child.children:
                        if arg.type == "type_projection":
                            for sub in arg.children:
                                if sub.type in ("user_type", "nullable_type", "type_reference"):
                                    elem = _simple_type_from_node(sub, source_bytes)
                                    if elem:
                                        return elem
        return outer_name
    return None


def _extract_kotlin_field_types(
    class_node,
    source_bytes: bytes,
    import_map: dict[str, str],
    package: str,
) -> dict[str, str]:
    """Return {field_name: simple_type_name} for all val/var properties of a class.

    Covers two declaration sites:
    1. Primary constructor parameters with val/var (class Foo(val bar: Bar))
    2. Body property_declaration nodes (val baz: Baz = ...)

    Only the simple type name is extracted (not FQN) — the resolver uses the
    import_map / class_by_simple_name index to resolve FQNs at resolution time.
    Nullable types (Bar?) and generic types (List<Bar>) are handled; for generics
    the outer container name (e.g. "List") is stored since the resolver won't
    follow into generics anyway.
    """
    result: dict[str, str] = {}

    def _stype(node) -> str | None:
        return _simple_type_from_node(node, source_bytes)

    # 1. Primary constructor class_parameters
    primary_ctor = None
    for child in class_node.children:
        if child.type == "primary_constructor":
            primary_ctor = child
            break
    if primary_ctor is not None:
        for child in _walk_children(primary_ctor):
            if child.type == "class_parameters":
                for param in child.children:
                    if param.type != "class_parameter":
                        continue
                    # Only val/var parameters become properties
                    has_val_var = any(c.type in ("val", "var") for c in param.children)
                    if not has_val_var:
                        continue
                    name_node = None
                    type_node = None
                    for c in param.children:
                        if c.type in ("identifier", "simple_identifier") and name_node is None:
                            name_node = c
                        elif c.type in ("user_type", "nullable_type", "type_reference") and type_node is None:
                            type_node = c
                    if name_node is not None and type_node is not None:
                        field_name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
                        simple_type = _stype(type_node)
                        if field_name and simple_type:
                            result[field_name] = simple_type

    # 2. Class body property_declaration nodes
    class_body = _child_by_type(class_node, "class_body")
    if class_body is not None:
        for child in class_body.children:
            if child.type != "property_declaration":
                continue
            # Find variable_declaration child → identifier + type
            var_decl = None
            for c in child.children:
                if c.type == "variable_declaration":
                    var_decl = c
                    break
            if var_decl is None:
                continue
            name_node = None
            type_node = None
            for c in var_decl.children:
                if c.type in ("identifier", "simple_identifier") and name_node is None:
                    name_node = c
                elif c.type in ("user_type", "nullable_type", "type_reference") and type_node is None:
                    type_node = c
            # type annotation is sometimes a sibling of variable_declaration, not a child
            if type_node is None:
                for c in child.children:
                    if c.type in ("user_type", "nullable_type", "type_reference"):
                        type_node = c
                        break
            if name_node is not None and type_node is not None:
                field_name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
                simple_type = _stype(type_node)
                if field_name and simple_type:
                    result[field_name] = simple_type

    return result


def _extract_fn_param_types(fn_node, source_bytes: bytes) -> dict[str, str]:
    """Return {param_name: simple_type_name} for all typed parameters of a function.

    Only parameters with explicit type annotations are included; untyped params
    (rare in Kotlin but possible for lambdas) are skipped silently.
    """
    result: dict[str, str] = {}
    fn_params = _child_by_type(fn_node, "function_value_parameters")
    if fn_params is None:
        return result
    for fp in fn_params.children:
        if fp.type != "function_value_parameter":
            continue
        param_node = _child_by_type(fp, "parameter")
        if param_node is None:
            continue
        name_node = None
        type_node = None
        for c in param_node.children:
            if c.type in ("simple_identifier", "identifier") and name_node is None:
                name_node = c
            elif c.type in ("user_type", "nullable_type", "type_reference") and type_node is None:
                type_node = c
        if name_node is not None and type_node is not None:
            param_name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
            simple_type = _simple_type_from_node(type_node, source_bytes)
            if param_name and simple_type:
                result[param_name] = simple_type
    return result


def _walk_children(node):
    """Shallow walk: yield node and direct children only."""
    yield node
    for child in node.children:
        yield child


# ---------------------------------------------------------------------------
# Inheritance extraction
# ---------------------------------------------------------------------------

def _extract_kotlin_supertypes(
    class_node,
    source_bytes: bytes,
    class_fqn: str,
    class_id: str,
    package: str,
    import_map: dict[str, str] | None = None,
) -> list[dict]:
    """Extract EXTENDS/IMPLEMENTS edges for a Kotlin class_declaration or object_declaration.

    delegation_specifiers children:
    - delegation_specifier with constructor_invocation → EXTENDS
    - delegation_specifier with user_type directly → IMPLEMENTS

    FQN resolution order:
    1. import_map lookup (exact match on simple name) → use the imported FQN
    2. Same package as current file → f"{package}.{simple}"
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
        if import_map and simple in import_map:
            return import_map[simple]
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
        impl_map: dict[str, str] = {}
        class_field_types: dict[str, dict[str, str]] = {}
        method_param_types: dict[str, dict[str, str]] = {}

        package = _package_name(root, src)
        imports = _import_map(root, src)

        # Collect top-level and nested class-like declarations
        for class_node in _iter_class_nodes(root):
            modifiers = _child_by_type(class_node, "modifiers")
            class_annotations = _collect_annotations(modifiers, src)
            data_class = _is_data_class(modifiers, src)

            class_name = _resolve_class_name(class_node, src)
            if class_name is None:
                continue
            fqn = f"{package}.{class_name}" if package else class_name

            # tree-sitter-kotlin represents `interface Foo` as a class_declaration
            # with an `interface` keyword child, NOT as interface_declaration.
            is_interface = (
                class_node.type == "interface_declaration"
                or (
                    class_node.type == "class_declaration"
                    and any(c.type == "interface" for c in class_node.children)
                )
            )
            is_object = class_node.type in ("object_declaration", "companion_object")
            enclosing_class_name: str | None = (
                _companion_enclosing_class_name(class_node, src)
                if class_node.type == "companion_object"
                else None
            )

            class_id = str(uuid.uuid4())
            classes.append({
                "id": class_id,
                "name": class_name,
                "fqn": fqn,
                "file_id": file_id,
                "repo_id": repo_id,
                "is_interface": is_interface,
                "is_object": is_object,
                "enclosing_class_name": enclosing_class_name,
                "annotations": class_annotations,
            })

            # Synthetic <init> method — lets the resolver emit CALLS edges for
            # `ClassName(...)` constructor calls without any schema changes.
            # Only regular classes can be instantiated this way; object
            # declarations (singletons), companion objects, and interfaces
            # cannot be constructed via a call_expression.
            if class_node.type == "class_declaration" and not is_interface:
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
                    "generated": True,
                    "is_entry_point": False,
                    "complexity_hint": "",
                    "io_fanout": 0,
                    "io_parallel_count": 0,
                    "io_serial_count": 0,
                    "io_parallel_wrapper": "",
                })

            # Extract EXTENDS/IMPLEMENTS inheritance edges (class and object only)
            if class_node.type in ("class_declaration", "object_declaration"):
                inh = _extract_kotlin_supertypes(class_node, src, fqn, class_id, package, import_map=imports)
                inheritance_edges.extend(inh)
                # Build impl_map for Spring beans: interface_fqn → impl_class_fqn.
                # Used by the resolver to redirect interface-typed call sites to
                # the concrete implementation (same as java_extractor does for Java).
                if _SPRING_COMPONENT_ANNOTATIONS.intersection(class_annotations):
                    for edge in inh:
                        if edge["edge_type"] == "IMPLEMENTS":
                            impl_map[edge["parent_fqn"]] = fqn

            # Extract field types for property-chain call resolution
            if class_node.type in ("class_declaration", "object_declaration"):
                ft = _extract_kotlin_field_types(class_node, src, imports, package)
                if ft:
                    class_field_types[fqn] = ft

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

            fn_name_count: dict[str, int] = {}
            for fn_node in _iter_function_nodes(class_body):
                fn_modifiers = _child_by_type(fn_node, "modifiers")
                fn_annotations = _collect_annotations(fn_modifiers, src)

                fn_name_node = _child_by_type(fn_node, "identifier")
                if fn_name_node is None:
                    continue
                fn_name = _node_text(fn_name_node, src).strip()
                _fn_n = fn_name_count.get(fn_name, 0) + 1
                fn_name_count[fn_name] = _fn_n
                fn_fqn = f"{fqn}.{fn_name}" if _fn_n == 1 else f"{fqn}.{fn_name}#{_fn_n}"

                is_suspend = _is_suspend(fn_modifiers, src)
                line_start = fn_node.start_point[0] + 1  # 1-based
                generated = _is_kotlin_data_generated(fn_name, data_class)

                # Determine if this method is an entry point
                fn_ann_set = set(fn_annotations)
                fn_is_entry_point = bool(
                    fn_ann_set & _ENTRY_POINT_ANNOTATIONS
                    or fn_ann_set & set(_MAPPING_TO_METHOD.keys())
                )

                # Extract parameter names for complexity pass
                fn_param_names: list[str] = []
                fn_params = _child_by_type(fn_node, "function_value_parameters")
                if fn_params:
                    for fp in fn_params.children:
                        if fp.type == "function_value_parameter":
                            param_node = _child_by_type(fp, "parameter")
                            if param_node:
                                sid = _child_by_type(param_node, "simple_identifier")
                                if sid:
                                    fn_param_names.append(_node_text(sid, src).strip())

                # Extract parameter types for resolver param-receiver resolution
                fn_pt = _extract_fn_param_types(fn_node, src)
                if fn_pt:
                    method_param_types[fn_fqn] = fn_pt

                # Find function body for complexity pass
                fn_body = _child_by_type(fn_node, "function_body")
                fn_complexity_hint = detect_complexity_hints(
                    fn_body, src, fn_name, fn_param_names, "kotlin"
                )
                fn_io = detect_io_fanout(fn_body, src, "kotlin", fn_annotations)

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
                    "is_entry_point": fn_is_entry_point,
                    "complexity_hint": fn_complexity_hint,
                    "io_fanout": fn_io["total"],
                    "io_parallel_count": fn_io["parallel_count"],
                    "io_serial_count": fn_io["serial_count"],
                    "io_parallel_wrapper": fn_io["parallel_wrapper"],
                })

                # Detect endpoint annotations — emit one Endpoint per path value
                # (@GetMapping(["/v5/foo", "/v6/foo"]) → two Endpoint nodes)
                for ann_node in _iter_annotation_nodes(fn_modifiers):
                    ann_name = _annotation_name(ann_node, src)
                    if ann_name in _MAPPING_TO_METHOD:
                        http_method = _MAPPING_TO_METHOD[ann_name]
                        ann_paths = _annotation_args(ann_node, src) or [""]
                        for ann_path in ann_paths:
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
                "is_object": False,
                "enclosing_class_name": None,
                "annotations": [],
            })
            tl_name_count: dict[str, int] = {}
            for fn_node in fn_list:
                fn_modifiers = _child_by_type(fn_node, "modifiers")
                fn_annotations = _collect_annotations(fn_modifiers, src)

                fn_name_node = _child_by_type(fn_node, "identifier")
                if fn_name_node is None:
                    continue
                fn_name = _node_text(fn_name_node, src).strip()
                _tl_n = tl_name_count.get(fn_name, 0) + 1
                tl_name_count[fn_name] = _tl_n
                fn_fqn = f"{kt_fqn}.{fn_name}" if _tl_n == 1 else f"{kt_fqn}.{fn_name}#{_tl_n}"

                is_suspend = _is_suspend(fn_modifiers, src)
                line_start = fn_node.start_point[0] + 1  # 1-based

                # Determine if this top-level function is an entry point
                tl_ann_set = set(fn_annotations)
                tl_is_entry_point = bool(
                    tl_ann_set & _ENTRY_POINT_ANNOTATIONS
                    or tl_ann_set & set(_MAPPING_TO_METHOD.keys())
                )

                # Extract parameter names for complexity pass
                tl_param_names: list[str] = []
                tl_fn_params = _child_by_type(fn_node, "function_value_parameters")
                if tl_fn_params:
                    for fp in tl_fn_params.children:
                        if fp.type == "function_value_parameter":
                            param_node = _child_by_type(fp, "parameter")
                            if param_node:
                                sid = _child_by_type(param_node, "simple_identifier")
                                if sid:
                                    tl_param_names.append(_node_text(sid, src).strip())

                # Extract parameter types for resolver param-receiver resolution
                tl_pt = _extract_fn_param_types(fn_node, src)
                if tl_pt:
                    method_param_types[fn_fqn] = tl_pt

                # Find function body for complexity pass
                tl_fn_body = _child_by_type(fn_node, "function_body")
                tl_complexity_hint = detect_complexity_hints(
                    tl_fn_body, src, fn_name, tl_param_names, "kotlin"
                )
                tl_io = detect_io_fanout(tl_fn_body, src, "kotlin", fn_annotations)

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
                    "is_entry_point": tl_is_entry_point,
                    "complexity_hint": tl_complexity_hint,
                    "io_fanout": tl_io["total"],
                    "io_parallel_count": tl_io["parallel_count"],
                    "io_serial_count": tl_io["serial_count"],
                    "io_parallel_wrapper": tl_io["parallel_wrapper"],
                })

                # Detect RestClient / WebClient calls in top-level function body
                if tl_fn_body:
                    rc = _find_rest_calls_in_node(tl_fn_body, src, method_id, repo_id)
                    rest_calls.extend(rc)

        # Expose per-file import map for resolver RC-A disambiguation
        file_import_maps: dict[str, dict[str, str]] = {file_id: imports}

        return ExtractResult(
            classes=classes,
            methods=methods,
            endpoints=endpoints,
            rest_calls=rest_calls,
            impl_map=impl_map,
            inheritance_edges=inheritance_edges,
            class_field_types=class_field_types,
            method_param_types=method_param_types,
            file_import_maps=file_import_maps,
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


def _companion_enclosing_class_name(class_node, src: bytes) -> str | None:
    """Return the name of the class that directly contains a companion_object node.

    Walk: companion_object → parent (class_body) → grandparent (class_declaration).
    Returns the identifier text of the enclosing class, or None if not found.
    """
    parent = class_node.parent  # class_body
    if parent is None:
        return None
    grandparent = parent.parent  # class_declaration
    if grandparent is None:
        return None
    name_node = _child_by_type(grandparent, "identifier")
    if name_node is None:
        return None
    return _node_text(name_node, src).strip()


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
