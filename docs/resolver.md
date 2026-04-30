# Dedalus Resolver

## What the resolver does

`dedalus/resolver.py` converts a tree-sitter AST for a single source file into a
list of directed call edges between methods. It matches call-site names against
a pre-built FQN index using a suffix strategy, emitting a `CALLS` edge when a
match is found and an `UNRESOLVED_CALL` edge otherwise. It does not perform
type inference; accuracy is best-effort.

---

## Algorithm

### 1. Build the suffix index (`build_fqn_index` + inline in `resolve_calls`)

`build_fqn_index` produces `{fqn: method_id}` from the method dicts that the
extractor supplies. Inside `resolve_calls` this is converted into a second
structure:

```
_suffix_index: {simple_name: [method_id, ...]}
```

For every FQN (e.g. `com.example.Foo.bar`), the part after the last `.`
(`bar`) is extracted with `rsplit(".", 1)[-1]` and used as the key. Multiple
FQNs can share the same simple name; all their IDs are stored in the list.

### 2. Walk the AST for method/function declarations

`_walk_all` performs a depth-first traversal of the root node, yielding every
node. `resolve_calls` collects nodes whose type is `method_declaration` (Java)
or `function_declaration` (Kotlin) and passes each to `_process_method_node`.

### 3. Identify the enclosing method (`_find_enclosing_method`)

For each declaration node, the function:

1. Reads the method name from the `name` field child (falls back to the first
   `identifier` child if the named field is absent).
2. Converts the tree-sitter 0-based start row to a 1-based line number.
3. Scans the provided `methods` list for a method with the same name and the
   closest `line_start`. This handles overloaded methods that appear in
   different files or at different positions without requiring a unique name.

If no match is found the method body is skipped entirely.

### 4. Find the method body

`_process_method_node` looks for a `body` field child. If absent it falls back
to the first child whose type is `block` or `function_body`.

### 5. Walk the body for call sites

Another `_walk_all` pass over the body collects every `method_invocation`
(Java) or `call_expression` (Kotlin) node.

### 6. Extract the callee name (`_get_invocation_name`)

The function handles three shapes:

| Pattern | Detection | Extraction |
|---|---|---|
| Java `foo.bar(args)` | child of type `argument_list` | identifier immediately before `argument_list` |
| Kotlin `foo.bar(args)` | child of type `value_arguments` | last identifier inside the node immediately before `value_arguments` (may be a `navigation_expression`) |
| Bare call `foo()` | neither of the above | first `identifier` child of the invocation node |

### 7. Match against the suffix index (`_process_invocation`)

The extracted simple name is looked up in `_suffix_index`:

- **Match found** — `callee_id = matches[0]` (first entry), `edge_type = "CALLS"`.
- **No match** — `callee_id = str(uuid.uuid4())`, `edge_type = "UNRESOLVED_CALL"`.

A `CallEdge` is appended to the output list in both cases.

### Why best-effort

The resolver never inspects variable types, import statements, or class
hierarchies. Two methods named `save` from unrelated classes look identical at
this stage. The suffix match trades precision for simplicity and speed,
accepting false positives and false negatives as the price of not needing a
full type solver.

---

## CallEdge fields

| Field | Type | Description |
|---|---|---|
| `caller_id` | `str` (UUID) | `Method.id` of the method that contains the call site |
| `callee_id` | `str` (UUID) | `Method.id` of the resolved callee for `CALLS`; a freshly generated UUID for `UNRESOLVED_CALL` |
| `edge_type` | `str` | `"CALLS"` or `"UNRESOLVED_CALL"` |

---

## CALLS vs UNRESOLVED_CALL

| | `CALLS` | `UNRESOLVED_CALL` |
|---|---|---|
| When emitted | The call-site name matches the suffix of at least one FQN in the index provided to `resolve_calls` | The name matches nothing in the index |
| `callee_id` points to | An existing `Method` node in KuzuDB | A transient UUID not yet stored anywhere |
| Meaning | Edge is fully resolved within the scope of the provided index | The target is unknown — could be an external library, a method in another repo, or a language built-in |
| Use in cross-repo resolution | Already complete; no further action needed | The UUID acts as a placeholder; the cross-repo resolver attempts to replace it with a real `Method.id` from a different repo's index |

`UNRESOLVED_CALL` edges are the primary input to cross-repo resolution. They
are stored in KuzuDB with their placeholder UUID so that the cross-repo pass
can query them, attempt an FQN match against the target repo's index, and
either upgrade the edge to `CALLS` or leave it tagged as externally
unresolvable.

---

## Limitations

**Overloaded methods — first-match wins.** When multiple FQNs share the same
simple name (e.g. `process` in `OrderService` and `PaymentService`), the
suffix index holds both IDs but `_process_invocation` unconditionally takes
`matches[0]`. Which entry lands first depends on dict insertion order
(Python 3.7+ preserves insertion order, so it is stable but arbitrary). This
can produce incorrect `CALLS` edges between unrelated classes.

**Dynamic dispatch.** Interface calls and virtual method calls resolve to
whichever concrete implementation appears first in the suffix index. The
resolver has no knowledge of declared types or runtime polymorphism.

**Lambda and closure calls.** Expressions like `val fn = ::save; fn()` or Java
method references are not traced. The `call_expression` / `method_invocation`
node shape for these differs from a named call, and `_get_invocation_name`
returns `None`, so no edge is emitted.

**Kotlin extension functions.** An extension function `fun String.clean()` is
declared as a `function_declaration` and indexed under the simple name `clean`.
A call `myString.clean()` is parsed as a `call_expression` with a
`navigation_expression` receiver. `_get_invocation_name` extracts `clean` from
the navigation expression, so the match succeeds — but the receiver type is not
checked. An unrelated method also named `clean` in any other class will produce
a collision under the first-match rule above.

**Constructor calls.** `object_creation_expression` (Java `new Foo(...)`) and
Kotlin constructor calls are not matched. No edge is emitted for constructors.

**Annotations and default parameter expressions.** Nodes inside annotation
arguments or default value expressions are walked as part of the method body if
they syntactically appear there, which can produce spurious edges.

---

## How cross-repo resolution uses this output

The orchestrator collects `UNRESOLVED_CALL` edges from all files in a repo and
stores them in KuzuDB. The cross-repo resolver (see `resolver_crossrepo.py`)
queries KuzuDB for all edges with `edge_type = "UNRESOLVED_CALL"`, builds a
combined FQN index from every known repo, and attempts a second suffix match.
Edges that resolve against a foreign repo's index are re-written as `CALLS`
with the cross-repo `Method.id` as the new `callee_id`. Edges that still
cannot be resolved are tagged `EXTERNAL_CALL` and left as dead-end nodes in
the graph.
