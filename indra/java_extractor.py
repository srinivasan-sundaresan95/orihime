"""Java language extractor for the Indra code knowledge graph."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

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
    annotation_node, source_bytes: bytes
) -> tuple[str, str]:
    """Return (annotation_name, first_string_value_or_empty) from an annotation node."""
    name = ""
    value = ""
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
                elif arg.type == "element_value_pair":
                    # check if key is "value" or "path"
                    pair_children = arg.children
                    key_nodes = [c for c in pair_children if c.type == "identifier"]
                    val_nodes = [
                        c
                        for c in pair_children
                        if c.type in ("string_literal", "array_initializer")
                    ]
                    if key_nodes and key_nodes[0].is_named:
                        key_text = _text(key_nodes[0], source_bytes)
                        if key_text in ("value", "path") and val_nodes:
                            frag = _find_first_child_of_type(
                                val_nodes[0], "string_fragment"
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


@dataclass
class JavaExtractor:
    language: str = "java"
    file_extensions: frozenset[str] = field(
        default_factory=lambda: frozenset({".java"})
    )

    def extract(
        self, tree, source_bytes: bytes, file_id: str, repo_id: str
    ) -> ExtractResult:
        result = ExtractResult()
        root = tree.root_node

        package = _extract_package(root, source_bytes)

        # Walk top-level children to find class declarations
        self._extract_classes(root, source_bytes, file_id, repo_id, package, result)

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
    ) -> None:
        """Find all class/interface declarations under root and populate result."""
        for node in _walk_all(root):
            if node.type == "class_declaration":
                self._process_class(
                    node, source_bytes, file_id, repo_id, package, result, is_interface=False
                )
            elif node.type == "interface_declaration":
                self._process_class(
                    node, source_bytes, file_id, repo_id, package, result, is_interface=True
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
                    ann_name, ann_value = _extract_annotation_info(ann_child, source_bytes)
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
                "annotations": annotations,
            }
        )

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
            }
        )

        # Check for endpoint annotations
        if modifiers_node:
            for ann_child in modifiers_node.children:
                if ann_child.type in ("marker_annotation", "annotation"):
                    ann_name, ann_value = _extract_annotation_info(ann_child, source_bytes)
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
        body_node = method_node.child_by_field_name("body")
        if body_node is None:
            body_node = _find_first_child_of_type(method_node, "block")
        if body_node:
            self._extract_rest_calls(body_node, source_bytes, method_id, repo_id, result)

    def _extract_rest_calls(
        self,
        body_node,
        source_bytes: bytes,
        caller_method_id: str,
        repo_id: str,
        result: ExtractResult,
    ) -> None:
        for node in _walk_all(body_node):
            if node.type == "method_invocation":
                self._process_method_invocation(
                    node, source_bytes, caller_method_id, repo_id, result
                )

    def _process_method_invocation(
        self,
        inv_node,
        source_bytes: bytes,
        caller_method_id: str,
        repo_id: str,
        result: ExtractResult,
    ) -> None:
        """
        method_invocation structure variants:
          1. object.method(args)  → children: identifier(obj) . identifier(method) argument_list
          2. method(args)         → children: identifier(method) argument_list
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
                # The identifier before the dot (if any) is the object
                for j in range(0, i):
                    if children[j].type == "identifier" and children[j] is not method_name_node:
                        object_name = _text(children[j], source_bytes)
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

        # Extract URL pattern from first string argument
        url_pattern = "DYNAMIC"
        if arg_list_node:
            for arg in arg_list_node.children:
                if arg.type == "string_literal":
                    frag = _find_first_child_of_type(arg, "string_fragment")
                    if frag:
                        url_pattern = _text(frag, source_bytes)
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
