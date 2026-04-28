# Extractors

## What extractors do

An extractor parses a single source file using a Tree-sitter parse tree and produces an `ExtractResult` containing four lists: classes, methods, HTTP endpoint declarations, and outbound REST call sites. The data is language-agnostic from that point forward — the graph loader stores it in KuzuDB without knowing which extractor produced it. Each extractor is registered once at module load time via `register()` and is looked up by language name or file extension when the indexer walks a repository.

---

## Java extractor

**File:** `indra/java_extractor.py`  
**Handles:** `.java`

### What it detects

- All `class_declaration` and `interface_declaration` nodes at any depth in the file.
- All `method_declaration` nodes that are direct children of a class body.
- Spring MVC endpoint annotations on methods: `GetMapping`, `PostMapping`, `PutMapping`, `DeleteMapping`, `PatchMapping`, `RequestMapping`.
- Outbound REST calls via `RestTemplate`, `RestClient`, and `WebClient`.

### Annotation detection approach

Annotations live inside a `modifiers` node that is a direct child of the class or method declaration. The extractor walks the `modifiers` children looking for nodes of type `marker_annotation` or `annotation`. For each, it calls `_extract_annotation_info` which:

1. Reads the annotation name from the `identifier` child.
2. Reads the path value from the `annotation_argument_list`, handling three forms:
   - Bare string: `@GetMapping("/users")`
   - Named `value=`: `@GetMapping(value = "/users")`
   - Named `path=`: `@RequestMapping(path = "/users")`

For `RequestMapping`, the HTTP method defaults to `GET` but is overridden when a `method=` element-value pair is present (e.g., `method = RequestMethod.POST`). The extractor scans the value text for the uppercase verb names `GET POST PUT DELETE PATCH`.

### Class-level `@RequestMapping` prefix handling

After collecting the class's modifiers, the extractor checks whether any annotation is `RequestMapping` and stores its path value as `class_path_prefix`. When a method endpoint is found, the final path is:

```python
full_path = class_path_prefix.rstrip("/") + ann_value
```

Example: class `@RequestMapping("/api")` + method `@GetMapping("/users/{id}")` → `/api/users/{id}`.

If the method annotation has no path argument the prefix is used as-is (which can produce a path ending without a slash — this is intentional for methods that handle the root of a prefix).

### RestTemplate / WebClient detection

The extractor walks every `method_invocation` node inside a method body. A call is captured when **either**:

- The method name is unambiguously a REST-only call (`getForObject`, `getForEntity`, `postForObject`, `postForEntity`, `postForLocation`, `exchange`, `execute`), **or**
- The receiver object name matches `restTemplate`, `restClient`, or `webClient`.

This two-part check avoids false positives from unrelated `get()` or `post()` calls on non-REST objects.

The HTTP method is taken from `_REST_METHOD_MAP`. For `exchange` calls, the extractor additionally scans argument text for `GET POST PUT DELETE PATCH` to narrow the method where possible.

The URL is taken from the first `string_literal` argument. If the first argument is not a string literal (e.g., a variable or expression), the URL is recorded as `DYNAMIC`.

### `DYNAMIC` sentinel

`url_pattern = "DYNAMIC"` is written when:

- The first argument to a REST call is not a compile-time string literal.
- The `exchange` / `execute` methods are used and the HTTP method cannot be inferred from argument text.

The graph loader stores `DYNAMIC` as-is; the cross-repo resolver skips `DYNAMIC` entries when building call edges.

---

## Kotlin extractor

**File:** `indra/kotlin_extractor.py`  
**Handles:** `.kt`, `.kts`

### What it detects

- `class_declaration`, `object_declaration`, `companion_object`, and `interface_declaration` nodes at any depth.
- `function_declaration` nodes that are direct children of a `class_body`.
- Spring MVC endpoint annotations: `GetMapping`, `PostMapping`, `PutMapping`, `DeleteMapping`, `PatchMapping`, `RequestMapping`.
- Outbound REST calls via `RestClient`, `WebClient`, and `RestTemplate` fluent chains.

### Annotation detection approach

Kotlin annotation nodes use a different structure from Java. The extractor's `_annotation_name` handles two forms that Tree-sitter produces:

- `@GetMapping("/users")` → `annotation > constructor_invocation > user_type > identifier`
- `@RestController` (marker) → `annotation > user_type > identifier`

Path extraction (`_annotation_arg`) walks into `constructor_invocation > value_arguments > value_argument > string_literal` and concatenates `string_content` children (handles escape sequences correctly).

For `RequestMapping`, the HTTP method defaults to `GET`. Unlike the Java extractor, the Kotlin extractor does not currently parse the `method=` named argument to override the verb — it always stores `GET` for `@RequestMapping`.

### Class-level `@RequestMapping` prefix handling

Same semantic as Java. The class prefix is combined with the method path:

```python
full_path = class_prefix.rstrip("/") + "/" + ann_path.lstrip("/")
```

When the method annotation has no path, `full_path` is set to the class prefix alone (or `"/"` if both are empty). The extra slash-normalisation (`lstrip("/")`) prevents double-slash for paths like `@RequestMapping("/api")` + `@GetMapping("/users")`.

### Suspend detection

The `_is_suspend` function checks the `modifiers` node for a child of type `function_modifier` whose text is `suspend`. The result is stored in `methods[*].is_suspend`. This flag is used downstream by the MCP query layer to annotate coroutine-capable endpoints.

### RestClient chain traversal approach

Kotlin services typically use `RestClient` (or `WebClient`) as a fluent chain:

```kotlin
restClient.get().uri("http://user-service/internal/users/{id}").retrieve().body(...)
```

The extractor's `_extract_chain_info` function recursively walks `call_expression` and `navigation_expression` nodes, collecting the sequence of identifiers as `chain_methods` and the string argument of any `.uri(...)` call as `url`. A chain is considered a REST call when:

1. One of the identifier names matches a known root (`restClient`, `webClient`, `restTemplate`, `RestClient`, `WebClient`, `RestTemplate`), **or**
2. The chain contains any of `retrieve`, `exchange`, or `execute` (builder-pattern without a named root variable).

The HTTP verb is taken from the **first** matching entry in `_CHAIN_METHOD_TO_HTTP` (e.g., `get` → `GET`, `post` → `POST`). When a match is found in a subtree, the recursion does not descend further to avoid recording the same chain multiple times.

If `uri(...)` is never called with a string literal, `url` remains `None` and no `rest_call` record is written for that chain.

---

## Shared `ExtractResult` schema

`ExtractResult` is defined in `indra/language.py` as a dataclass with four `list[dict]` fields.

### `classes`

| Field | Type | Description |
|---|---|---|
| `id` | `str` (UUID4) | Primary key used as foreign key in `methods` |
| `name` | `str` | Simple class name (e.g., `SampleController`) |
| `fqn` | `str` | Fully-qualified name (`package.ClassName`) |
| `file_id` | `str` | ID of the file record in the graph |
| `repo_id` | `str` | ID of the repository |
| `is_interface` | `bool` | `True` for `interface` declarations |
| `annotations` | `list[str]` | Annotation names present on the class (e.g., `["RestController", "RequestMapping"]`) |

### `methods`

| Field | Type | Description |
|---|---|---|
| `id` | `str` (UUID4) | Primary key used as foreign key in `endpoints` and `rest_calls` |
| `name` | `str` | Simple method/function name |
| `fqn` | `str` | `package.ClassName.methodName` |
| `class_id` | `str` | FK → `classes.id` |
| `file_id` | `str` | ID of the file record |
| `repo_id` | `str` | ID of the repository |
| `line_start` | `int` | 1-based line number of the method declaration |
| `is_suspend` | `bool` | `True` if Kotlin `suspend` modifier is present (always `False` in Java) |
| `annotations` | `list[str]` | Annotation names on the method |

### `endpoints`

| Field | Type | Description |
|---|---|---|
| `id` | `str` (UUID4) | Primary key |
| `http_method` | `str` | `GET`, `POST`, `PUT`, `DELETE`, `PATCH` |
| `path` | `str` | Full path after prefix concatenation (e.g., `/api/users/{id}`) |
| `path_regex` | `str` | Regex form of the path (Kotlin only; empty string in Java). Path variables become `[^/]+`. |
| `handler_method_id` | `str` | FK → `methods.id` |
| `repo_id` | `str` | ID of the repository |

### `rest_calls`

| Field | Type | Description |
|---|---|---|
| `id` | `str` (UUID4) | Primary key |
| `http_method` | `str` | `GET`, `POST`, `PUT`, `DELETE`, `PATCH`, or `DYNAMIC` |
| `url_pattern` | `str` | URL string literal, or `"DYNAMIC"` if not a compile-time constant |
| `caller_method_id` | `str` | FK → `methods.id` |
| `repo_id` | `str` | ID of the repository |

---

## Known limitations

**Both extractors:**

- **Dynamic URLs are not resolved.** Variables, constants, string concatenation, and template expressions all produce `url_pattern = "DYNAMIC"`. The cross-repo resolver cannot match these to endpoints.
- **Inherited annotations are not followed.** If a class extends a base class that carries `@RequestMapping`, the subclass extractor sees no prefix. Interface default methods with mapping annotations are likewise missed.
- **Only direct class-body methods are scanned.** Methods inside anonymous classes, local classes, or lambda bodies inside a method are not extracted as independent method records.
- **Single-value path arrays are not handled.** `@GetMapping({"/a", "/b"})` or `@RequestMapping(path = {"/a", "/b"})` — the extractor reads only the first `string_fragment` and silently ignores additional paths.
- **`method=` override for `@RequestMapping` is Java-only.** The Kotlin extractor always records `GET` for `@RequestMapping` regardless of a `method=` argument.

**Java extractor:**

- **Fluent RestClient chains are not detected.** The Java extractor only looks for `method_invocation` nodes with a simple `object.method(args)` structure. A chain like `restClient.get().uri(...).retrieve()` is not matched because the intermediate `.get()` call is not on a direct `restTemplate`/`restClient` variable reference at the top level.

**Kotlin extractor:**

- **`exchange` defaults to `GET`.** `_CHAIN_METHOD_TO_HTTP` maps `exchange` to `GET`. A `webClient.exchange(...)` call will be recorded as `GET` even when it performs a different method.
- **`restTemplate.getForObject(...)` style calls are not detected.** The Kotlin extractor only uses chain traversal; it has no equivalent of the Java extractor's `_REST_METHOD_MAP` simple invocation check.

---

## Adding support for more Spring annotations

### Java extractor (`indra/java_extractor.py`)

Add the annotation name and HTTP verb to `_ENDPOINT_ANNOTATIONS` at the top of the file:

```python
_ENDPOINT_ANNOTATIONS: dict[str, str] = {
    "GetMapping": "GET",
    # add here, e.g.:
    "HttpExchange": "GET",
}
```

If the new annotation supports a `method=` attribute for verb override, extend `_infer_http_method_from_annotation` to handle it (currently only `RequestMapping` gets special treatment there).

### Kotlin extractor (`indra/kotlin_extractor.py`)

Add the annotation name and HTTP verb to `_MAPPING_TO_METHOD` at the top of the file:

```python
_MAPPING_TO_METHOD: dict[str, str] = {
    "GetMapping": "GET",
    # add here, e.g.:
    "HttpExchange": "GET",
}
```

The annotation name is matched by `_annotation_name`, which handles both `@Foo` (marker) and `@Foo(...)` (with arguments) forms, so no further changes are needed for standard annotation shapes.
