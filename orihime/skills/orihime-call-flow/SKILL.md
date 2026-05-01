---
name: orihime-call-flow
description: >
  Use when the user asks "trace the call flow for X", "what does method Y call",
  "show me the call chain from controller to DB for endpoint Z", "who calls method X",
  or wants to understand how a request flows through the codebase.
  Uses Orihime MCP tools only — no source file reads.
---

# Orihime Call Flow Skill

## Trigger conditions

- "Trace the call flow for [method/endpoint]"
- "What does method X call?"
- "Show me the call chain from controller to DB"
- "Who calls method X?"
- "How does request [endpoint] flow through the service?"
- "Walk me through the code path for [feature]"

---

## Performance target

5–8 MCP tool calls, under 10 seconds.
Baseline (source reads): 36 tool calls, 27 files, ~4–5 min.

---

## Step 0 — Confirm the repo is indexed

```
mcp__orihime__list_repos()
```

Match the user's repo name. If not listed, stop and tell the user to run:
`python -m orihime index --repo <path> --name <name>`

If the user mentions a specific branch, also call:
```
mcp__orihime__list_branches(repo_name="<repo>")
```
Confirm the branch appears in the result before querying — Orihime stores files per branch, so an un-indexed branch returns empty results.

---

## Step 1 — Locate the entry point

**If the user gave a URL path** (e.g. `/v5/point_card`):
```
mcp__orihime__find_endpoint_callers(
  http_method="GET",   # or whatever the user specified
  path_pattern="/v5/point_card"
)
```
Returns `[{role, fqn, file_path, line_start}]`. The `role="handler"` entry is the controller method; `role="caller"` entries are its upstream callers. Use the handler's `fqn` for Step 2.

**If the user gave a method name** (e.g. `getPointCardInfo`):
```
mcp__orihime__search_symbol(query="<method_name>")
```
Pick the best match (prefer Controller/Service classes). Get the `fqn`.

**To confirm exact file location**:
```
mcp__orihime__get_file_location(fqn="<fully.qualified.ClassName.methodName>")
```

---

## Step 2 — Walk downstream callees

```
mcp__orihime__find_callees(method_fqn="<fqn>")
```

Returns all methods directly called. For each important callee (Controller → Service → Repository → upstream), call `find_callees` again:
```
mcp__orihime__find_callees(method_fqn="<callee_fqn>")
```

Focus on classes with names containing: `Controller`, `Service`, `Repository`, `Client`, `Adapter`, `Gateway`.

Stop at: JPA repository interfaces, Kafka producers, `RestTemplate`/`WebClient` calls.

Typical depth: 3–4 hops from controller to repository or external API.

---

## Step 3 — Find upstream callers (optional, when user asks "who calls X")

```
mcp__orihime__find_callers(method_fqn="<fqn>")
```

For blast radius (all transitive callers):
```
mcp__orihime__blast_radius(method_fqn="<fqn>", max_depth=3)
```

Returns results with `depth` field: depth=1 are direct callers, depth=2 are their callers, etc.

---

## Step 4 — Check cross-service calls

```
mcp__orihime__list_unresolved_calls(repo_name="<repo>")
```

Unresolved calls are outgoing REST calls not matched to an indexed endpoint.
These are external dependencies or services not yet indexed.

For cross-service calls that ARE resolved:
```
mcp__orihime__find_repo_dependencies(repo_name="<repo>")
```

---

## Step 5 — For interface/abstract class dispatch

```
mcp__orihime__find_implementations(interface_fqn="<fqn>")
```

When a service calls an interface method, find all concrete implementations.
Then call `find_callees` on the concrete impl's method.

---

## Step 6 — Present the call chain

```
## Call Flow: [endpoint or method name]

### Entry Point
- `ControllerMethod` @ ControllerClass.kt:42
  - Route: GET /v5/point_card

### Service Layer
- `serviceMethod` @ ServiceClass.kt:87
  - Called by: ControllerMethod

### Repository / Persistence
- `findByUserId` @ PointCardRepository.kt:15
  - Called by: serviceMethod

### Upstream API Calls
- `callExternalApi` (unresolved) → POST https://api.example.com/v2/cards
  - Called by: serviceMethod
```

Show `file:line` for every node. If `line_start` is 0, omit it.

---

## Gotchas

### FQN format
Orihime FQNs look like: `com.example.service.PointCardService.getPointCardInfo`
If `search_symbol` returns multiple matches with the same short name, use the full FQN — the one in the Controller or Service package is usually correct.

### search_symbol returns both classes and methods
The result has a `type` field: `"class"` or `"method"`. Always use the `"method"` type result's FQN for `find_callers`/`find_callees`.

### find_endpoint_callers vs search_symbol
Use `find_endpoint_callers` when the user supplies an HTTP method + path. Use `search_symbol` when the user supplies a method name. `find_endpoint_callers` already returns the handler and its callers in one call; feed the handler `fqn` into `find_callees` to trace downstream.

### Do NOT read source files
This skill uses MCP tools only. Do not call Read/Bash to open `.kt`, `.java` files.
