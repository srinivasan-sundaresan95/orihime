"""Java language extractor for the Orihime code knowledge graph."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .complexity_pass import detect_complexity_hints
from .language import ExtractResult, register
from .path_utils import compile_path_regex

# Spring endpoint annotation names mapped to HTTP methods
_ENDPOINT_ANNOTATIONS: dict[str, str] = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
    "RequestMapping": "GET",  # default; overridden by method= attribute if present
}

# Annotations that mark a method as a messaging/scheduling entry point
_ENTRY_POINT_ANNOTATIONS: frozenset[str] = frozenset(
    {"KafkaListener", "Scheduled", "JmsListener", "RabbitListener"}
)

# RestTemplate / WebClient / RestClient method names mapped to HTTP methods
_REST_METHOD_MAP: dict[str, str] = {
    "getForObject": "GET",
    "getForEntity": "GET",
    "postForObject": "POST",
    "postForEntity": "POST",
    "postForLocation": "POST",
    "put": "PUT",
    "delete": "DELETE",
    "exchange": "DYNAMIC",
    "execute": "DYNAMIC",
    # RestClient / WebClient fluent methods
    "get": "GET",
    "post": "POST",
    "retrieve": "GET",
}

# Variable names that indicate a REST client
_REST_CLIENT_VARS: frozenset[str] = frozenset(
    {"restTemplate", "restClient", "webClient"}
)


def _text(node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _find_children_of_type(node, *types: str):
    return [c for c in node.children if c.type in types]


def _find_first_child_of_type(node, *types: str):
    for c in node.children:
        if c.type in types:
            return c
    return None


def _walk_all(node):
    """Yield all descendant nodes (depth-first)."""
    yield node
    for child in node.children:
        yield from _walk_all(child)


def _extract_annotation_info(
    annotation_node, source_bytes: bytes, constant_index: "dict[str, str] | None" = None
) -> tuple[str, str]:
    """Return (annotation_name, first_string_value_or_empty) from an annotation node.

    If *constant_index* is provided (mapping ``ClassName.FIELD`` → path string),
    field_access annotation values are resolved via the index.
    """
    name = ""
    value = ""

    def _resolve_field_access(node) -> str:
        """Return the resolved path string for a field_access node, or its raw text."""
        raw = _text(node, source_bytes)  # e.g. "RequestMapping.WALLET_STATUS"
        if constant_index:
            return constant_index.get(raw, "")
        return ""

    for child in annotation_node.children:
        if child.type == "identifier":
            name = _text(child, source_bytes)
        elif child.type == "annotation_argument_list":
            # Look for a string_literal directly, or element_value_pair with key "value"
            for arg in child.children:
                if arg.type == "string_literal":
                    # grab string_fragment child
                    frag = _find_first_child_of_type(arg, "string_fragment")
                    if frag:
                        value = _text(frag, source_bytes)
                    break
                elif arg.type == "field_access":
                    # Direct field_access as annotation value: @PostMapping(RequestMapping.USER_INFO)
                    value = _resolve_field_access(arg)
                    break
                elif arg.type == "element_value_pair":
                    # check if key is "value" or "path"
                    pair_children = arg.children
                    key_nodes = [c for c in pair_children if c.type == "identifier"]
                    val_nodes = [
                        c
                        for c in pair_children
                        if c.type in ("string_literal", "array_initializer", "field_access")
                    ]
                    if key_nodes and key_nodes[0].is_named:
                        key_text = _text(key_nodes[0], source_bytes)
                        if key_text in ("value", "path") and val_nodes:
                            val_node = val_nodes[0]
                            if val_node.type == "field_access":
                                value = _resolve_field_access(val_node)
                            else:
                                frag = _find_first_child_of_type(
                                    val_node, "string_fragment"
                                )
                                if frag:
                                    value = _text(frag, source_bytes)
                            break
                        elif key_text == "method" and val_nodes:
                            # RequestMapping with explicit method=RequestMethod.GET etc.
                            pass
    return name, value


def _collect_annotations(modifiers_node, source_bytes: bytes) -> list[str]:
    """Collect annotation names from a modifiers node."""
    names = []
    if modifiers_node is None:
        return names
    for child in modifiers_node.children:
        if child.type in ("marker_annotation", "annotation"):
            ann_name = ""
            for cc in child.children:
                if cc.type == "identifier":
                    ann_name = _text(cc, source_bytes)
                    break
            if ann_name:
                names.append(ann_name)
    return names


def _infer_http_method_from_annotation(
    ann_name: str, annotation_node, source_bytes: bytes
) -> str:
    """Return HTTP method string for a Spring mapping annotation."""
    if ann_name == "RequestMapping":
        # Look for method= element_value_pair
        arg_list = _find_first_child_of_type(annotation_node, "annotation_argument_list")
        if arg_list:
            for arg in arg_list.children:
                if arg.type == "element_value_pair":
                    key_node = _find_first_child_of_type(arg, "identifier")
                    if key_node and _text(key_node, source_bytes) == "method":
                        # value is like RequestMethod.POST — take last identifier
                        val_text = _text(arg, source_bytes)
                        for m in ("POST", "PUT", "DELETE", "PATCH", "GET"):
                            if m in val_text.upper():
                                return m
        return "GET"
    return _ENDPOINT_ANNOTATIONS.get(ann_name, "GET")


def _extract_package(root, source_bytes: bytes) -> str:
    """Extract package name from the root program node."""
    for child in root.children:
        if child.type == "package_declaration":
            # package_declaration: package <scoped_identifier|identifier> ;
            for cc in child.children:
                if cc.type in ("scoped_identifier", "identifier"):
                    return _text(cc, source_bytes)
    return ""


def _get_string_fragment(node, source_bytes: bytes) -> str | None:
    frag = _find_first_child_of_type(node, "string_fragment")
    if frag:
        return _text(frag, source_bytes)
    return None


def _resolve_field_access_in_index(node, source_bytes: bytes, constant_index: dict) -> str | None:
    raw = _text(node, source_bytes)
    return constant_index.get(raw)


def _extract_url_from_binary_expression(
    node, source_bytes: bytes, constant_index: "dict[str, str] | None" = None
) -> "str | None":
    """Extract a URL pattern from a string concatenation binary_expression node.

    Returns the assembled url_pattern string, or None if the pattern cannot be
    resolved to a useful string (partial wildcard fallbacks are omitted because
    cross_resolver.py cannot match `*`-prefixed patterns against endpoint regexes).
    """
    if node.type != "binary_expression":
        return None
    if constant_index is None:
        constant_index = {}

    named_children = [c for c in node.children if c.is_named]
    if len(named_children) < 2:
        return None

    left = named_children[0]
    right = named_children[1]

    left_value: str | None = None
    right_value: str | None = None

    if left.type == "string_literal":
        left_value = _get_string_fragment(left, source_bytes)
    elif left.type == "field_access":
        left_value = _resolve_field_access_in_index(left, source_bytes, constant_index)

    if right.type == "string_literal":
        right_value = _get_string_fragment(right, source_bytes)
    elif right.type == "field_access":
        right_value = _resolve_field_access_in_index(right, source_bytes, constant_index)

    if left_value is not None and right_value is not None:
        return left_value + right_value

    return None


def _get_chain_root_identifier(inv_node, source_bytes: bytes) -> str:
    """Walk a method_invocation chain to its root and return the root identifier name.

    For UriComponentsBuilder.fromHttpUrl(...).path(...).build(), the root
    method_invocation has identifier 'UriComponentsBuilder' as its first child.
    Returns empty string if root cannot be determined.
    """
    current = inv_node
    while True:
        first_child = current.children[0] if current.children else None
        if first_child is None:
            return ""
        if first_child.type == "method_invocation":
            current = first_child
        elif first_child.type == "identifier":
            return _text(first_child, source_bytes)
        elif first_child.type == "field_access":
            # Walk field_access to root identifier
            fa = first_child
            while fa.type == "field_access":
                inner = fa.children[0] if fa.children else None
                if inner is None:
                    return ""
                fa = inner
            if fa.type == "identifier":
                return _text(fa, source_bytes)
            return ""
        else:
            return ""


def _extract_url_from_uri_builder(call_node, source_bytes: bytes) -> "str | None":
    """Extract URL from a UriComponentsBuilder chain: fromHttpUrl/fromUriString + optional .path().

    Returns the assembled URL string, or None if the chain is not recognised.
    """
    base_url: str | None = None
    path_segments: list[str] = []

    chain: list = []
    current = call_node
    while current is not None and current.type == "method_invocation":
        chain.append(current)
        first = current.children[0] if current.children else None
        if first is not None and first.type == "method_invocation":
            current = first
        else:
            break

    # reversed(): chain[0] is outermost (.toUri()), chain[-1] is innermost (.fromHttpUrl(...))
    for inv in reversed(chain):
        method_name = ""
        arg_list = None
        children = inv.children
        for i, c in enumerate(children):
            if c.type == "argument_list":
                arg_list = c
                for j in range(i - 1, -1, -1):
                    if children[j].type == "identifier":
                        method_name = _text(children[j], source_bytes)
                        break
                break

        if method_name in ("fromHttpUrl", "fromUriString"):
            if arg_list:
                for arg in arg_list.children:
                    if arg.type == "string_literal":
                        frag = _find_first_child_of_type(arg, "string_fragment")
                        if frag:
                            base_url = _text(frag, source_bytes)
                        break
        elif method_name == "path":
            if arg_list:
                for arg in arg_list.children:
                    if arg.type == "string_literal":
                        frag = _find_first_child_of_type(arg, "string_fragment")
                        if frag:
                            path_segments.append(_text(frag, source_bytes))
                        break

    if base_url is None and not path_segments:
        return None

    result = (base_url or "") + "".join(path_segments)
    return result if result else None


def _extract_static_final_strings(
    class_body_node, class_name: str, source_bytes: bytes
) -> dict[str, str]:
    """Return a mapping of ``ClassName.FIELD_NAME`` → path string for all
    ``public static final String FIELD = "..."`` declarations in *class_body_node*.
    """
    result: dict[str, str] = {}
    for child in class_body_node.children:
        if child.type != "field_declaration":
            continue
        # Check modifiers: must contain public, static, final
        modifiers_node = _find_first_child_of_type(child, "modifiers")
        if modifiers_node is None:
            continue
        modifier_text = _text(modifiers_node, source_bytes)
        if "public" not in modifier_text or "static" not in modifier_text or "final" not in modifier_text:
            continue
        # Check type is String
        type_node = _find_first_child_of_type(child, "type_identifier")
        if type_node is None or _text(type_node, source_bytes) != "String":
            continue
        # Extract variable declarator(s)
        for decl in child.children:
            if decl.type != "variable_declarator":
                continue
            name_node = _find_first_child_of_type(decl, "identifier")
            if name_node is None:
                continue
            field_name = _text(name_node, source_bytes)
            # Find the string_literal value
            for val_child in decl.children:
                if val_child.type == "string_literal":
                    frag = _find_first_child_of_type(val_child, "string_fragment")
                    if frag:
                        path_value = _text(frag, source_bytes)
                        result[f"{class_name}.{field_name}"] = path_value
                    break
    return result


# Spring stereotype annotations that mark a class as a managed bean
_SPRING_COMPONENT_ANNOTATIONS: frozenset[str] = frozenset(
    {"Service", "Component", "Repository", "Controller", "RestController"}
)

_LOMBOK_CLASS_ANNOTATIONS: frozenset[str] = frozenset({
    "Data", "Value", "Builder", "Getter", "Setter", "EqualsAndHashCode", "ToString"
})

_LOMBOK_GENERATED_NAMES: frozenset[str] = frozenset({
    "equals", "hashCode", "toString", "canEqual", "builder", "build"
})

_LOMBOK_GETTER_RE = re.compile(r'^(get|is)[A-Z]')
_LOMBOK_SETTER_RE = re.compile(r'^set[A-Z]')


def _is_lombok_generated(method_name: str, class_annotations: list[str]) -> bool:
    """Return True if method_name is likely Lombok-generated given the class annotations."""
    if not _LOMBOK_CLASS_ANNOTATIONS.intersection(class_annotations):
        return False
    if method_name in _LOMBOK_GENERATED_NAMES:
        return True
    if _LOMBOK_GETTER_RE.match(method_name):
        return True
    if _LOMBOK_SETTER_RE.match(method_name):
        return True
    return False


def _build_import_map(root, source_bytes: bytes) -> dict[str, str]:
    """Return simple_name → fqn from all import declarations under root."""
    import_map: dict[str, str] = {}
    for child in root.children:
        if child.type == "import_declaration":
            for cc in child.children:
                if cc.type in ("scoped_identifier", "identifier"):
                    fqn_text = _text(cc, source_bytes)
                    simple = fqn_text.rsplit(".", 1)[-1]
                    import_map[simple] = fqn_text
                    break
    return import_map


def _walk_excluding_type_args(node):
    """Like _walk_all but does not descend into type_arguments nodes."""
    yield node
    for child in node.children:
        if child.type != "type_arguments":
            yield from _walk_excluding_type_args(child)


def _extract_inheritance(
    node,
    source_bytes: bytes,
    class_fqn: str,
    class_id: str,
    import_map: dict[str, str],
    package: str,
) -> list[dict]:
    """Extract EXTENDS/IMPLEMENTS edges for a single class or interface declaration.

    Java AST confirmed field names:
    - class_declaration: "superclass" field → EXTENDS; "interfaces" field → IMPLEMENTS
      NOTE: tree-sitter-java uses "interfaces" (NOT "super_interfaces") for the implements clause.
    - interface_declaration: "extends_interfaces" field → IMPLEMENTS

    FQN resolution: import_map.get(simple, f"{package}.{simple}" if package else simple)
    Self-loop guard: skip if parent_fqn == class_fqn
    Type params: use _walk_excluding_type_args to avoid collecting type params as supertypes
    """
    edges = []
    node_type = node.type  # "class_declaration" or "interface_declaration"

    def _resolve(simple: str) -> str:
        return import_map.get(simple, f"{package}.{simple}" if package else simple)

    def _collect_type_identifiers(clause_node) -> list[str]:
        return [
            _text(n, source_bytes)
            for n in _walk_excluding_type_args(clause_node)
            if n.type == "type_identifier"
        ]

    if node_type == "class_declaration":
        # superclass → EXTENDS
        superclass_node = node.child_by_field_name("superclass")
        if superclass_node is not None:
            for simple in _collect_type_identifiers(superclass_node):
                parent_fqn = _resolve(simple)
                if parent_fqn != class_fqn:
                    edges.append({"child_id": class_id, "parent_fqn": parent_fqn, "edge_type": "EXTENDS"})
                break  # only one superclass

        # interfaces → IMPLEMENTS
        interfaces_node = node.child_by_field_name("interfaces")
        if interfaces_node is not None:
            for simple in _collect_type_identifiers(interfaces_node):
                parent_fqn = _resolve(simple)
                if parent_fqn != class_fqn:
                    edges.append({"child_id": class_id, "parent_fqn": parent_fqn, "edge_type": "IMPLEMENTS"})

    elif node_type == "interface_declaration":
        # extends_interfaces → IMPLEMENTS (interfaces extend other interfaces)
        # NOTE: tree-sitter-java exposes this as a child node by *type*, not as a named field.
        extends_node = node.child_by_field_name("extends_interfaces") or next(
            (c for c in node.children if c.type == "extends_interfaces"), None
        )
        if extends_node is not None:
            for simple in _collect_type_identifiers(extends_node):
                parent_fqn = _resolve(simple)
                if parent_fqn != class_fqn:
                    edges.append({"child_id": class_id, "parent_fqn": parent_fqn, "edge_type": "IMPLEMENTS"})

    return edges


_JPA_RELATION_ANNOTATIONS: frozenset[str] = frozenset({
    "OneToMany", "ManyToOne", "OneToOne", "ManyToMany",
})


def _extract_entity_relations(
    class_node,
    source_bytes: bytes,
    class_id: str,
    class_fqn: str,
    repo_id: str,
    import_map: dict[str, str],
    class_annotations: list[str],
) -> list[dict]:
    """Extract JPA relation fields from an @Entity class.

    Returns list of dicts matching the EntityRelation node schema.
    Only called when 'Entity' or 'MappedSuperclass' is in class_annotations.
    """
    import hashlib as _hashlib

    if "Entity" not in class_annotations and "MappedSuperclass" not in class_annotations:
        return []

    # Derive package from FQN (e.g. "com.example.Order" → "com.example")
    package = class_fqn.rsplit(".", 1)[0] if "." in class_fqn else ""

    relations = []
    body = class_node.child_by_field_name("body") or _find_first_child_of_type(class_node, "class_body")
    if body is None:
        return []

    for child in body.children:
        if child.type != "field_declaration":
            continue
        # Collect annotations on this field (both annotation and marker_annotation nodes)
        field_annots: list[str] = []
        for mod in child.children:
            if mod.type == "modifiers":
                for a in mod.children:
                    if a.type in ("annotation", "marker_annotation"):
                        name_node = a.child_by_field_name("name") or _find_first_child_of_type(a, "identifier")
                        if name_node:
                            field_annots.append(_text(name_node, source_bytes))

        relation_type = next((a for a in field_annots if a in _JPA_RELATION_ANNOTATIONS), None)
        if relation_type is None:
            continue

        # Extract fetch type from annotation arguments: fetch = FetchType.LAZY or EAGER
        fetch_type = "LAZY"  # JPA default
        for mod in child.children:
            if mod.type == "modifiers":
                for a in mod.children:
                    if a.type == "annotation":
                        name_node = a.child_by_field_name("name") or _find_first_child_of_type(a, "identifier")
                        if name_node and _text(name_node, source_bytes) == relation_type:
                            args = a.child_by_field_name("arguments") or _find_first_child_of_type(a, "annotation_argument_list")
                            if args:
                                args_text = _text(args, source_bytes)
                                if "EAGER" in args_text:
                                    fetch_type = "EAGER"

        # Extract field name and target type
        field_name = ""
        target_simple = ""
        for fc in child.children:
            if fc.type == "variable_declarator":
                name_n = fc.child_by_field_name("name") or _find_first_child_of_type(fc, "identifier")
                if name_n:
                    field_name = _text(name_n, source_bytes)
            elif fc.type in ("type_identifier", "generic_type"):
                # e.g. List<Order> or Order
                for ti in _walk_all(fc):
                    if ti.type == "type_identifier":
                        candidate = _text(ti, source_bytes)
                        if candidate not in ("List", "Set", "Collection", "Optional"):
                            target_simple = candidate
                            break

        if not target_simple:
            continue

        target_fqn = import_map.get(target_simple, f"{package}.{target_simple}" if package else target_simple)
        rel_id = _hashlib.md5(f"{class_id}:{field_name}:{relation_type}".encode()).hexdigest()

        relations.append({
            "id": rel_id,
            "source_class_id": class_id,
            "target_class_fqn": target_fqn,
            "field_name": field_name,
            "relation_type": relation_type,
            "fetch_type": fetch_type,
            "repo_id": repo_id,
        })

    return relations


def _extract_impl_map(root, source_bytes: bytes, package: str) -> dict[str, str]:
    # Build import map: simple_name → fully-qualified name
    import_map: dict[str, str] = _build_import_map(root, source_bytes)

    result: dict[str, str] = {}

    for node in _walk_all(root):
        if node.type != "class_declaration":
            continue

        # Check for Spring stereotype annotation
        modifiers_node = _find_first_child_of_type(node, "modifiers")
        if modifiers_node is None:
            continue
        ann_names = _collect_annotations(modifiers_node, source_bytes)
        if not _SPRING_COMPONENT_ANNOTATIONS.intersection(ann_names):
            continue

        # Get class name and FQN
        name_node = node.child_by_field_name("name") or _find_first_child_of_type(node, "identifier")
        if name_node is None:
            continue
        class_name = _text(name_node, source_bytes)
        class_fqn = f"{package}.{class_name}" if package else class_name

        # tree-sitter-java names the implements clause field "interfaces", not "super_interfaces"
        interfaces_node = node.child_by_field_name("interfaces")
        if interfaces_node is None:
            continue

        # Collect type_identifier nodes within the interfaces clause
        for iface_node in _walk_all(interfaces_node):
            if iface_node.type == "type_identifier":
                iface_simple = _text(iface_node, source_bytes)
                # Resolve to FQN: prefer import, fall back to same package
                if iface_simple in import_map:
                    iface_fqn = import_map[iface_simple]
                else:
                    iface_fqn = f"{package}.{iface_simple}" if package else iface_simple
                result[iface_fqn] = class_fqn

    return result


@dataclass
class JavaExtractor:
    language: str = "java"
    file_extensions: frozenset[str] = field(
        default_factory=lambda: frozenset({".java"})
    )

    def extract(
        self,
        tree,
        source_bytes: bytes,
        file_id: str,
        repo_id: str,
        constant_index: "dict[str, str] | None" = None,
    ) -> ExtractResult:
        result = ExtractResult()
        root = tree.root_node

        package = _extract_package(root, source_bytes)

        # Walk top-level children to find class declarations
        self._extract_classes(
            root, source_bytes, file_id, repo_id, package, result, constant_index
        )

        # Build interface → implementation mapping for Spring beans
        result.impl_map = _extract_impl_map(root, source_bytes, package)

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_classes(
        self,
        root,
        source_bytes: bytes,
        file_id: str,
        repo_id: str,
        package: str,
        result: ExtractResult,
        external_constant_index: "dict[str, str] | None" = None,
    ) -> None:
        """Find all class/interface declarations under root and populate result.

        Pass 1: accumulate ``public static final String`` constants from every class
        into a per-file *constant_index*.  Pass 2 uses that index when resolving
        annotation path references.
        """
        # --- Pass 1: build per-file constant index ---
        constant_index: dict[str, str] = {}
        if external_constant_index:
            constant_index.update(external_constant_index)

        for node in _walk_all(root):
            if node.type == "class_declaration":
                name_node = node.child_by_field_name("name") or _find_first_child_of_type(node, "identifier")
                if name_node is None:
                    continue
                class_name = _text(name_node, source_bytes)
                body_node = node.child_by_field_name("body") or _find_first_child_of_type(node, "class_body")
                if body_node:
                    constant_index.update(
                        _extract_static_final_strings(body_node, class_name, source_bytes)
                    )

        # Build import map once for the whole file (used by _extract_inheritance)
        import_map = _build_import_map(root, source_bytes)

        # --- Pass 2: extract classes/methods/endpoints with resolved constants ---
        for node in _walk_all(root):
            if node.type == "class_declaration":
                self._process_class(
                    node, source_bytes, file_id, repo_id, package, result,
                    is_interface=False, constant_index=constant_index, import_map=import_map
                )
            elif node.type == "interface_declaration":
                self._process_class(
                    node, source_bytes, file_id, repo_id, package, result,
                    is_interface=True, constant_index=constant_index, import_map=import_map
                )

    def _process_class(
        self,
        class_node,
        source_bytes: bytes,
        file_id: str,
        repo_id: str,
        package: str,
        result: ExtractResult,
        is_interface: bool,
        constant_index: "dict[str, str] | None" = None,
        import_map: "dict[str, str] | None" = None,
    ) -> None:
        # Get class name — identifier child
        name_node = class_node.child_by_field_name("name")
        if name_node is None:
            # fallback: find first identifier child
            name_node = _find_first_child_of_type(class_node, "identifier")
        if name_node is None:
            return
        class_name = _text(name_node, source_bytes)
        fqn = f"{package}.{class_name}" if package else class_name

        # Annotations are on the modifiers node (sibling, it is a child of class_declaration)
        modifiers_node = _find_first_child_of_type(class_node, "modifiers")
        annotations = _collect_annotations(modifiers_node, source_bytes)

        # Class-level @RequestMapping prefix
        class_path_prefix = ""
        if modifiers_node:
            for ann_child in modifiers_node.children:
                if ann_child.type in ("marker_annotation", "annotation"):
                    ann_name, ann_value = _extract_annotation_info(
                        ann_child, source_bytes, constant_index
                    )
                    if ann_name == "RequestMapping" and ann_value:
                        class_path_prefix = ann_value
                        break

        class_id = str(uuid.uuid4())
        result.classes.append(
            {
                "id": class_id,
                "name": class_name,
                "fqn": fqn,
                "file_id": file_id,
                "repo_id": repo_id,
                "is_interface": is_interface,
                "is_object": False,
                "enclosing_class_name": None,
                "annotations": annotations,
            }
        )

        # Synthetic <init> method — lets the resolver emit CALLS edges for
        # `new ClassName(...)` constructor calls without any schema changes.
        if not is_interface:
            result.methods.append(
                {
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
                }
            )

        # Extract EXTENDS/IMPLEMENTS inheritance edges
        inheritance = _extract_inheritance(
            class_node, source_bytes, fqn, class_id, import_map or {}, package
        )
        result.inheritance_edges.extend(inheritance)

        # Extract JPA entity relation fields
        entity_rels = _extract_entity_relations(
            class_node, source_bytes, class_id, fqn,
            repo_id, import_map or {}, annotations,
        )
        result.entity_relations.extend(entity_rels)

        # Find class body → process methods
        body_node = class_node.child_by_field_name("body")
        if body_node is None:
            body_node = _find_first_child_of_type(class_node, "class_body")
        if body_node:
            self._process_methods(
                body_node,
                source_bytes,
                file_id,
                repo_id,
                class_id,
                fqn,
                class_path_prefix,
                result,
                constant_index=constant_index,
                class_annotations=annotations,
            )

    def _process_methods(
        self,
        body_node,
        source_bytes: bytes,
        file_id: str,
        repo_id: str,
        class_id: str,
        class_fqn: str,
        class_path_prefix: str,
        result: ExtractResult,
        constant_index: "dict[str, str] | None" = None,
        class_annotations: "list[str] | None" = None,
    ) -> None:
        for child in body_node.children:
            if child.type == "method_declaration":
                self._process_method(
                    child,
                    source_bytes,
                    file_id,
                    repo_id,
                    class_id,
                    class_fqn,
                    class_path_prefix,
                    result,
                    constant_index=constant_index,
                    class_annotations=class_annotations,
                )

    def _process_method(
        self,
        method_node,
        source_bytes: bytes,
        file_id: str,
        repo_id: str,
        class_id: str,
        class_fqn: str,
        class_path_prefix: str,
        result: ExtractResult,
        constant_index: "dict[str, str] | None" = None,
        class_annotations: "list[str] | None" = None,
    ) -> None:
        name_node = method_node.child_by_field_name("name")
        if name_node is None:
            name_node = _find_first_child_of_type(method_node, "identifier")
        if name_node is None:
            return

        method_name = _text(name_node, source_bytes)
        method_fqn = f"{class_fqn}.{method_name}"
        line_start = method_node.start_point[0] + 1  # tree-sitter is 0-based

        modifiers_node = _find_first_child_of_type(method_node, "modifiers")
        annotations = _collect_annotations(modifiers_node, source_bytes)

        generated = _is_lombok_generated(method_name, class_annotations or [])

        # Determine if this method is an entry point:
        # - HTTP handler methods (have an endpoint annotation)
        # - Kafka consumers, scheduled tasks, JMS/RabbitMQ listeners
        ann_set = set(annotations)
        is_entry_point = bool(
            ann_set & _ENTRY_POINT_ANNOTATIONS
            or ann_set & set(_ENDPOINT_ANNOTATIONS.keys())
        )

        # Extract parameter names for complexity pass
        param_names: list[str] = []
        params_node = _find_first_child_of_type(method_node, "formal_parameters")
        if params_node:
            for param in params_node.children:
                if param.type == "formal_parameter":
                    pname = _find_first_child_of_type(param, "identifier")
                    if pname:
                        param_names.append(_text(pname, source_bytes))

        # Find method body for complexity pass
        body_node = method_node.child_by_field_name("body")
        if body_node is None:
            body_node = _find_first_child_of_type(method_node, "block")

        complexity_hint = detect_complexity_hints(
            body_node, source_bytes, method_name, param_names, "java"
        )

        method_id = str(uuid.uuid4())
        result.methods.append(
            {
                "id": method_id,
                "name": method_name,
                "fqn": method_fqn,
                "class_id": class_id,
                "file_id": file_id,
                "repo_id": repo_id,
                "line_start": line_start,
                "is_suspend": False,
                "annotations": annotations,
                "generated": generated,
                "is_entry_point": is_entry_point,
                "complexity_hint": complexity_hint,
            }
        )

        # Check for endpoint annotations
        if modifiers_node:
            for ann_child in modifiers_node.children:
                if ann_child.type in ("marker_annotation", "annotation"):
                    ann_name, ann_value = _extract_annotation_info(
                        ann_child, source_bytes, constant_index
                    )
                    if ann_name in _ENDPOINT_ANNOTATIONS:
                        http_method = _infer_http_method_from_annotation(
                            ann_name, ann_child, source_bytes
                        )
                        # Combine class prefix with method path
                        full_path = class_path_prefix.rstrip("/") + ann_value
                        result.endpoints.append(
                            {
                                "id": str(uuid.uuid4()),
                                "http_method": http_method,
                                "path": full_path,
                                "path_regex": compile_path_regex(full_path),
                                "handler_method_id": method_id,
                                "repo_id": repo_id,
                            }
                        )

        # Scan method body for RestTemplate/WebClient/RestClient calls
        if body_node:
            self._extract_rest_calls(
                body_node, source_bytes, method_id, repo_id, result,
                constant_index=constant_index,
            )

    def _extract_rest_calls(
        self,
        body_node,
        source_bytes: bytes,
        caller_method_id: str,
        repo_id: str,
        result: ExtractResult,
        constant_index: "dict[str, str] | None" = None,
    ) -> None:
        for node in _walk_all(body_node):
            if node.type == "method_invocation":
                if _get_chain_root_identifier(node, source_bytes) == "UriComponentsBuilder":
                    parent = node.parent
                    if parent is None or parent.type not in ("method_invocation", "argument_list"):
                        url_pattern = _extract_url_from_uri_builder(node, source_bytes)
                        result.rest_calls.append(
                            {
                                "id": str(uuid.uuid4()),
                                "http_method": "GET",
                                "url_pattern": url_pattern if url_pattern else "DYNAMIC",
                                "caller_method_id": caller_method_id,
                                "repo_id": repo_id,
                            }
                        )
                    continue

                self._process_method_invocation(
                    node, source_bytes, caller_method_id, repo_id, result,
                    constant_index=constant_index,
                )

    def _process_method_invocation(
        self,
        inv_node,
        source_bytes: bytes,
        caller_method_id: str,
        repo_id: str,
        result: ExtractResult,
        constant_index: "dict[str, str] | None" = None,
    ) -> None:
        """
        method_invocation structure variants:
          1. object.method(args)       → children: identifier(obj) . identifier(method) argument_list
          2. method(args)              → children: identifier(method) argument_list
          3. field_access.method(args) → children: field_access . identifier(method) argument_list
             e.g. Helper.INSTANCE.doWork() where first child is field_access node
        """
        children = inv_node.children
        # Find method name: it's the identifier just before argument_list
        method_name_node: Optional[object] = None
        object_name: str = ""
        arg_list_node = None

        for i, c in enumerate(children):
            if c.type == "argument_list":
                arg_list_node = c
                # The identifier right before argument_list is the method name
                for j in range(i - 1, -1, -1):
                    if children[j].type == "identifier":
                        method_name_node = children[j]
                        break
                # The identifier before the dot (if any) is the object.
                # Also handle field_access chain: extract root class name.
                for j in range(0, i):
                    if children[j].type == "identifier" and children[j] is not method_name_node:
                        object_name = _text(children[j], source_bytes)
                        break
                    elif children[j].type == "field_access":
                        # Walk field_access chain to find the root identifier
                        # e.g. "Helper.INSTANCE" → root is "Helper"
                        fa = children[j]
                        while fa.type == "field_access":
                            first = fa.children[0] if fa.children else None
                            if first is None:
                                break
                            fa = first
                        if fa.type == "identifier":
                            object_name = _text(fa, source_bytes)
                        break
                break

        if method_name_node is None:
            return

        method_name = _text(method_name_node, source_bytes)

        if method_name not in _REST_METHOD_MAP:
            return

        # Only capture if called on a known REST client variable
        # OR if the method is unambiguously REST-only (getForObject, postForObject, etc.)
        is_rest_only_method = method_name in (
            "getForObject", "getForEntity", "postForObject", "postForEntity",
            "postForLocation", "exchange", "execute",
        )
        called_on_rest_client = object_name in _REST_CLIENT_VARS

        if not (is_rest_only_method or called_on_rest_client):
            return

        http_method = _REST_METHOD_MAP[method_name]
        if http_method == "DYNAMIC" and method_name == "exchange":
            # Try to find HttpMethod argument
            if arg_list_node:
                for arg in arg_list_node.children:
                    arg_text = _text(arg, source_bytes).upper()
                    for m in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                        if m in arg_text:
                            http_method = m
                            break

        # Extract URL pattern from first string argument (or binary_expression)
        url_pattern = "DYNAMIC"
        if arg_list_node:
            for arg in arg_list_node.children:
                if arg.type == "string_literal":
                    frag = _find_first_child_of_type(arg, "string_fragment")
                    if frag:
                        url_pattern = _text(frag, source_bytes)
                    break
                elif arg.type == "binary_expression":
                    extracted = _extract_url_from_binary_expression(
                        arg, source_bytes, constant_index or {}
                    )
                    if extracted is not None:
                        url_pattern = extracted
                    break

        result.rest_calls.append(
            {
                "id": str(uuid.uuid4()),
                "http_method": http_method,
                "url_pattern": url_pattern,
                "caller_method_id": caller_method_id,
                "repo_id": repo_id,
            }
        )


register(JavaExtractor())
