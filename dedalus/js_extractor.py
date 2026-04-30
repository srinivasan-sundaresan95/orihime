"""JavaScript / TypeScript extractor for the Dedalus code knowledge graph.

Handles .js, .jsx, .ts, .tsx files. The TypeScript grammar is a superset of
JavaScript's, so a single extractor covers all four extensions. TSX files use
the tsx grammar variant; plain JS/TS use the javascript/typescript variants.

Extracted graph nodes (same schema as java_extractor):
  - Class      : ES6 class declarations
  - Method     : class methods + top-level function declarations + const arrow fns
  - Endpoint   : Next.js App Router, Next.js Pages Router, Express/Fastify routes
  - RestCall   : fetch() + axios.get/post/put/delete/patch()
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .complexity_pass import detect_complexity_hints
from .language import ExtractResult, register

# ---------------------------------------------------------------------------
# HTTP verb sets
# ---------------------------------------------------------------------------

_NEXTJS_ROUTE_EXPORTS: frozenset[str] = frozenset(
    {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
)

_EXPRESS_METHODS: frozenset[str] = frozenset(
    {"get", "post", "put", "delete", "patch", "head", "options", "all"}
)

_AXIOS_METHODS: dict[str, str] = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "delete": "DELETE",
    "patch": "PATCH",
    "head": "HEAD",
    "options": "OPTIONS",
    "request": "DYNAMIC",
}

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_first_child(node, *types: str):
    for c in node.children:
        if c.type in types:
            return c
    return None


def _walk(node):
    """Depth-first walk of all descendant nodes."""
    yield node
    for child in node.children:
        yield from _walk(child)


def _class_name(class_node, src: bytes) -> str:
    """Return the class name; works for both JS (identifier) and TS (type_identifier)."""
    for c in class_node.children:
        if c.type in ("identifier", "type_identifier"):
            return _text(c, src)
    return ""


def _method_name(method_node, src: bytes) -> str:
    """Return the method/property name from a method_definition node."""
    for c in method_node.children:
        if c.type in ("property_identifier", "identifier"):
            return _text(c, src)
    return ""


def _first_string_arg(args_node, src: bytes) -> str:
    """Return the string_fragment text of the first string argument, or ''."""
    if args_node is None:
        return ""
    for c in args_node.children:
        if c.type == "string":
            frag = _find_first_child(c, "string_fragment")
            if frag:
                return _text(frag, src)
        elif c.type == "template_string":
            # crude: take literal fragment before any interpolation
            frag = _find_first_child(c, "template_characters")
            if frag:
                return _text(frag, src)
    return ""


def _collect_decorators(node, src: bytes) -> list[str]:
    """Collect decorator names immediately preceding a class_declaration or method_definition."""
    names: list[str] = []
    for c in node.children:
        if c.type == "decorator":
            # decorator children: '@' followed by identifier or call_expression
            for dc in c.children:
                if dc.type == "identifier":
                    names.append(_text(dc, src))
                    break
                elif dc.type == "call_expression":
                    fn = _find_first_child(dc, "identifier", "member_expression")
                    if fn:
                        names.append(_text(fn, src).split(".")[0])
                    break
    return names


# ---------------------------------------------------------------------------
# Path derivation helpers for Next.js routes
# ---------------------------------------------------------------------------

def _nextjs_path_from_file(file_path: str) -> str:
    """Derive the URL path from a Next.js file path.

    Examples:
      app/api/users/route.ts         → /api/users
      pages/api/users/index.ts       → /api/users
      pages/api/users/[id].ts        → /api/users/{id}
      app/api/users/[id]/route.ts    → /api/users/{id}
    """
    p = Path(file_path)
    parts = list(p.parts)

    # Normalise Windows backslashes
    parts = [pt.replace("\\", "/") for pt in parts]

    # Find the "app" or "pages" anchor
    anchor_idx = -1
    for i, part in enumerate(parts):
        if part in ("app", "pages"):
            anchor_idx = i
            break

    if anchor_idx == -1:
        # Fallback: use the stem stripped of 'route'/'index'
        stem = p.stem
        if stem in ("route", "index"):
            return "/" + p.parent.name
        return "/" + stem

    rel_parts = parts[anchor_idx + 1:]

    # Remove trailing 'route' or 'index' filename
    if rel_parts:
        stem = Path(rel_parts[-1]).stem
        if stem in ("route", "index"):
            rel_parts = rel_parts[:-1]
        else:
            rel_parts[-1] = stem

    # Convert [param] → {param}
    cleaned: list[str] = []
    for part in rel_parts:
        stem_part = Path(part).stem if "." in part else part
        if stem_part.startswith("[") and stem_part.endswith("]"):
            cleaned.append("{" + stem_part[1:-1] + "}")
        else:
            cleaned.append(stem_part)

    return "/" + "/".join(cleaned) if cleaned else "/"


# ---------------------------------------------------------------------------
# Per-scope extraction state
# ---------------------------------------------------------------------------

@dataclass
class _Scope:
    class_id: str
    class_fqn: str


# ---------------------------------------------------------------------------
# Main extractor
# ---------------------------------------------------------------------------

@dataclass
class JsExtractor:
    """Extracts graph nodes from JS/TS/JSX/TSX source files."""

    language: str = "javascript"
    file_extensions: frozenset[str] = field(
        default_factory=lambda: frozenset({".js", ".jsx", ".ts", ".tsx"})
    )

    def extract(
        self,
        tree,
        source_bytes: bytes,
        file_id: str,
        repo_id: str,
        file_path: str = "",
        **kwargs,
    ) -> ExtractResult:
        result = ExtractResult()
        root = tree.root_node
        src = source_bytes

        # Two-pass: first collect classes, then walk everything for methods/endpoints/rest
        self._extract_classes(root, src, file_id, repo_id, result)
        self._extract_top_level(root, src, file_id, repo_id, file_path, result)

        return result

    # ------------------------------------------------------------------
    # Class extraction (pass 1)
    # ------------------------------------------------------------------

    def _extract_classes(self, root, src: bytes, file_id: str, repo_id: str, result: ExtractResult) -> None:
        for node in _walk(root):
            if node.type == "class_declaration":
                self._process_class(node, src, file_id, repo_id, result)

    def _process_class(self, class_node, src: bytes, file_id: str, repo_id: str, result: ExtractResult) -> None:
        name = _class_name(class_node, src)
        if not name:
            return

        decorators = _collect_decorators(class_node, src)

        class_id = str(uuid.uuid4())
        result.classes.append({
            "id": class_id,
            "name": name,
            "fqn": name,
            "file_id": file_id,
            "repo_id": repo_id,
            "is_interface": False,
            "is_object": False,
            "enclosing_class_name": "",
            "annotations": decorators,
        })

        # Extract class methods
        body = _find_first_child(class_node, "class_body")
        if body:
            for child in body.children:
                if child.type == "method_definition":
                    self._process_method(
                        child, src, file_id, repo_id,
                        class_id=class_id, class_fqn=name, result=result,
                    )
                elif child.type == "decorator":
                    pass  # decorators are on the next sibling — already handled in _collect_decorators

    # ------------------------------------------------------------------
    # Top-level extraction: functions, endpoints, rest calls (pass 2)
    # ------------------------------------------------------------------

    def _extract_top_level(
        self,
        root,
        src: bytes,
        file_id: str,
        repo_id: str,
        file_path: str,
        result: ExtractResult,
    ) -> None:
        """Walk top-level statements for exported functions and Express routes."""
        # Synthesise a module-level pseudo-class for top-level functions
        module_name = Path(file_path).stem if file_path else "module"
        module_class_id = str(uuid.uuid4())

        # We defer creating the module pseudo-class until we find at least one top-level fn
        module_class_created = [False]

        def _ensure_module_class():
            if not module_class_created[0]:
                result.classes.append({
                    "id": module_class_id,
                    "name": module_name,
                    "fqn": module_name,
                    "file_id": file_id,
                    "repo_id": repo_id,
                    "is_interface": False,
                    "is_object": True,
                    "enclosing_class_name": "",
                    "annotations": [],
                })
                module_class_created[0] = True

        for stmt in root.children:
            # export async function GET(request) { ... }  — App Router
            # export default function handler(req, res) { ... }  — Pages Router
            if stmt.type == "export_statement":
                self._handle_export_statement(
                    stmt, src, file_id, repo_id, file_path,
                    module_class_id, module_name, _ensure_module_class, result,
                )

            # top-level function declaration
            elif stmt.type == "function_declaration":
                _ensure_module_class()
                self._process_function_declaration(
                    stmt, src, file_id, repo_id,
                    class_id=module_class_id, class_fqn=module_name,
                    is_exported=False, result=result,
                )

            # top-level const/let fn = () => {} or app.get(...)
            elif stmt.type in ("lexical_declaration", "variable_declaration"):
                self._handle_variable_declaration(
                    stmt, src, file_id, repo_id, file_path,
                    module_class_id, module_name, _ensure_module_class, result,
                )

            # app.get('/path', handler)  — standalone expression
            elif stmt.type == "expression_statement":
                call = _find_first_child(stmt, "call_expression")
                if call:
                    self._maybe_extract_express_endpoint(
                        call, src, file_id, repo_id, result
                    )
                    self._extract_rest_calls_in_tree(call, src, caller_method_id="", repo_id=repo_id, result=result)

    def _handle_export_statement(
        self, stmt, src: bytes, file_id: str, repo_id: str, file_path: str,
        module_class_id: str, module_name: str, ensure_module_class, result: ExtractResult,
    ) -> None:
        is_default = any(c.type == "default" for c in stmt.children)

        # export async function GET(...) / export function handler(...)
        fn_decl = _find_first_child(stmt, "function_declaration")
        if fn_decl:
            fn_name = _text(_find_first_child(fn_decl, "identifier") or fn_decl, src)
            if not fn_name:
                return
            ensure_module_class()
            is_entry = fn_name in _NEXTJS_ROUTE_EXPORTS or (is_default and fn_name == "handler")
            method_id = self._process_function_declaration(
                fn_decl, src, file_id, repo_id,
                class_id=module_class_id, class_fqn=module_name,
                is_exported=True, result=result, force_entry_point=is_entry,
            )
            if is_entry and method_id:
                http_method = fn_name if fn_name in _NEXTJS_ROUTE_EXPORTS else "GET"
                path = _nextjs_path_from_file(file_path)
                result.endpoints.append({
                    "id": str(uuid.uuid4()),
                    "http_method": http_method,
                    "path": path,
                    "path_regex": path,
                    "handler_method_id": method_id,
                    "repo_id": repo_id,
                })
            return

        # export const GET = async (request) => { ... }
        lex_decl = _find_first_child(stmt, "lexical_declaration", "variable_declaration")
        if lex_decl:
            self._handle_variable_declaration(
                lex_decl, src, file_id, repo_id, file_path,
                module_class_id, module_name, ensure_module_class, result,
                force_exported=True,
            )

    def _handle_variable_declaration(
        self, stmt, src: bytes, file_id: str, repo_id: str, file_path: str,
        module_class_id: str, module_name: str, ensure_module_class, result: ExtractResult,
        force_exported: bool = False,
    ) -> None:
        for decl in stmt.children:
            if decl.type != "variable_declarator":
                continue
            name_node = _find_first_child(decl, "identifier")
            if not name_node:
                continue
            var_name = _text(name_node, src)

            # RHS: arrow_function or function_declaration
            rhs = None
            for c in decl.children:
                if c.type in ("arrow_function", "function_expression", "function_declaration"):
                    rhs = c
                    break

            if rhs is None:
                # Check for call_expression (Express: app.get(...))
                call = _find_first_child(decl, "call_expression")
                if call:
                    self._maybe_extract_express_endpoint(call, src, file_id, repo_id, result)
                    self._extract_rest_calls_in_tree(call, src, caller_method_id="", repo_id=repo_id, result=result)
                continue

            ensure_module_class()
            is_entry = var_name in _NEXTJS_ROUTE_EXPORTS
            body = _find_first_child(rhs, "statement_block")
            param_names = self._collect_param_names(
                _find_first_child(rhs, "formal_parameters", "identifier"), src
            )
            complexity = detect_complexity_hints(body, src, var_name, param_names, "javascript")
            method_id = str(uuid.uuid4())
            result.methods.append({
                "id": method_id,
                "name": var_name,
                "fqn": f"{module_name}.{var_name}",
                "class_id": module_class_id,
                "file_id": file_id,
                "repo_id": repo_id,
                "line_start": rhs.start_point[0] + 1,
                "is_suspend": False,
                "annotations": [],
                "generated": False,
                "is_entry_point": is_entry,
                "complexity_hint": complexity,
            })
            if (is_entry or force_exported) and is_entry:
                http_method = var_name if var_name in _NEXTJS_ROUTE_EXPORTS else "GET"
                path = _nextjs_path_from_file(file_path)
                result.endpoints.append({
                    "id": str(uuid.uuid4()),
                    "http_method": http_method,
                    "path": path,
                    "path_regex": path,
                    "handler_method_id": method_id,
                    "repo_id": repo_id,
                })
            if body:
                self._extract_rest_calls_in_tree(body, src, caller_method_id=method_id, repo_id=repo_id, result=result)

    # ------------------------------------------------------------------
    # Method helpers
    # ------------------------------------------------------------------

    def _process_function_declaration(
        self,
        fn_node,
        src: bytes,
        file_id: str,
        repo_id: str,
        class_id: str,
        class_fqn: str,
        is_exported: bool,
        result: ExtractResult,
        force_entry_point: bool = False,
    ) -> Optional[str]:
        name_node = _find_first_child(fn_node, "identifier")
        if not name_node:
            return None
        fn_name = _text(name_node, src)
        body = _find_first_child(fn_node, "statement_block")
        param_names = self._collect_param_names(
            _find_first_child(fn_node, "formal_parameters"), src
        )
        complexity = detect_complexity_hints(body, src, fn_name, param_names, "javascript")
        is_entry = force_entry_point or (is_exported and fn_name in _NEXTJS_ROUTE_EXPORTS)
        method_id = str(uuid.uuid4())
        result.methods.append({
            "id": method_id,
            "name": fn_name,
            "fqn": f"{class_fqn}.{fn_name}",
            "class_id": class_id,
            "file_id": file_id,
            "repo_id": repo_id,
            "line_start": fn_node.start_point[0] + 1,
            "is_suspend": False,
            "annotations": [],
            "generated": False,
            "is_entry_point": is_entry,
            "complexity_hint": complexity,
        })
        if body:
            self._extract_rest_calls_in_tree(body, src, caller_method_id=method_id, repo_id=repo_id, result=result)
        return method_id

    def _process_method(
        self,
        method_node,
        src: bytes,
        file_id: str,
        repo_id: str,
        class_id: str,
        class_fqn: str,
        result: ExtractResult,
    ) -> str:
        name = _method_name(method_node, src)
        if not name:
            name = "anonymous"

        body = _find_first_child(method_node, "statement_block")
        param_names = self._collect_param_names(
            _find_first_child(method_node, "formal_parameters"), src
        )
        complexity = detect_complexity_hints(body, src, name, param_names, "javascript")
        method_id = str(uuid.uuid4())
        result.methods.append({
            "id": method_id,
            "name": name,
            "fqn": f"{class_fqn}.{name}",
            "class_id": class_id,
            "file_id": file_id,
            "repo_id": repo_id,
            "line_start": method_node.start_point[0] + 1,
            "is_suspend": False,
            "annotations": [],
            "generated": False,
            "is_entry_point": False,
            "complexity_hint": complexity,
        })
        if body:
            self._extract_rest_calls_in_tree(body, src, caller_method_id=method_id, repo_id=repo_id, result=result)
        return method_id

    @staticmethod
    def _collect_param_names(params_node, src: bytes) -> list[str]:
        if params_node is None:
            return []
        names: list[str] = []
        for c in params_node.children:
            if c.type == "identifier":
                names.append(_text(c, src))
            elif c.type in ("required_parameter", "optional_parameter"):
                id_node = _find_first_child(c, "identifier")
                if id_node:
                    names.append(_text(id_node, src))
        return names

    # ------------------------------------------------------------------
    # Express endpoint extraction
    # ------------------------------------------------------------------

    def _maybe_extract_express_endpoint(
        self, call_node, src: bytes, file_id: str, repo_id: str, result: ExtractResult
    ) -> None:
        """Detect app.get('/path', handler) style calls."""
        fn_node = _find_first_child(call_node, "member_expression")
        if fn_node is None:
            return

        # member_expression: obj . property
        obj_node = None
        method_prop = None
        for c in fn_node.children:
            if c.type == "identifier" and obj_node is None:
                obj_node = c
            elif c.type == "property_identifier":
                method_prop = c

        if method_prop is None:
            return
        verb = _text(method_prop, src)
        if verb not in _EXPRESS_METHODS:
            return

        args = _find_first_child(call_node, "arguments")
        if not args:
            return
        path = _first_string_arg(args, src)
        if not path:
            return

        http_method = verb.upper()
        if http_method == "ALL":
            http_method = "GET"

        result.endpoints.append({
            "id": str(uuid.uuid4()),
            "http_method": http_method,
            "path": path,
            "path_regex": path,
            "handler_method_id": "",
            "repo_id": repo_id,
        })

    # ------------------------------------------------------------------
    # REST call extraction
    # ------------------------------------------------------------------

    def _extract_rest_calls_in_tree(
        self, node, src: bytes, caller_method_id: str, repo_id: str, result: ExtractResult
    ) -> None:
        for n in _walk(node):
            if n.type != "call_expression":
                continue
            fn = n.children[0] if n.children else None
            if fn is None:
                continue

            args = _find_first_child(n, "arguments")

            # fetch('/url', {method: 'POST'})
            if fn.type == "identifier" and _text(fn, src) == "fetch":
                self._extract_fetch_call(n, args, src, caller_method_id, repo_id, result)
                continue

            # axios.post('/url') / axios.get('/url') etc.
            if fn.type == "member_expression":
                obj_c = fn.children[0] if fn.children else None
                prop_c = _find_first_child(fn, "property_identifier")
                if (obj_c is not None and obj_c.type == "identifier"
                        and _text(obj_c, src) == "axios"
                        and prop_c is not None):
                    verb = _text(prop_c, src)
                    if verb in _AXIOS_METHODS:
                        url = _first_string_arg(args, src)
                        result.rest_calls.append({
                            "id": str(uuid.uuid4()),
                            "http_method": _AXIOS_METHODS[verb],
                            "url_pattern": url if url else "DYNAMIC",
                            "caller_method_id": caller_method_id,
                            "repo_id": repo_id,
                        })

    def _extract_fetch_call(
        self, call_node, args_node, src: bytes,
        caller_method_id: str, repo_id: str, result: ExtractResult,
    ) -> None:
        if not args_node:
            return
        url = _first_string_arg(args_node, src)

        # Determine HTTP method from second argument object: {method: 'POST'}
        http_method = "GET"
        arg_children = [c for c in args_node.children if c.type not in (",", "(", ")")]
        if len(arg_children) >= 2:
            opts = arg_children[1]
            if opts.type == "object":
                for pair in opts.children:
                    if pair.type == "pair":
                        key = _find_first_child(pair, "property_identifier", "identifier")
                        if key and _text(key, src) == "method":
                            val = None
                            for c in pair.children:
                                if c.type == "string":
                                    val = c
                            if val:
                                frag = _find_first_child(val, "string_fragment")
                                if frag:
                                    http_method = _text(frag, src).upper()

        result.rest_calls.append({
            "id": str(uuid.uuid4()),
            "http_method": http_method,
            "url_pattern": url if url else "DYNAMIC",
            "caller_method_id": caller_method_id,
            "repo_id": repo_id,
        })


# Register for both language keys; walker maps .ts/.tsx → "typescript", .js/.jsx → "javascript"
_js_extractor = JsExtractor(
    language="javascript",
    file_extensions=frozenset({".js", ".jsx"}),
)
_ts_extractor = JsExtractor(
    language="typescript",
    file_extensions=frozenset({".ts", ".tsx"}),
)

register(_js_extractor)
register(_ts_extractor)
