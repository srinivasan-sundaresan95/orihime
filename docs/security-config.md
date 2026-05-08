# Security Configuration Reference

Orihime's taint analysis relies on a set of source, sink, and sanitizer definitions. The built-in defaults cover standard Spring MVC, JAX-RS, JDBC, and common Node.js patterns. You can extend these for your own codebase using a YAML configuration file.

---

## Overview

Built-in rules are always active and cannot be disabled. Your YAML overrides are appended additively — your rules extend the built-in set rather than replacing it. Duplicate entries are silently ignored.

The effective configuration at any time is:

```
effective_sources     = built_in_source_annotations + built_in_source_methods + user_sources
effective_sinks       = built_in_sink_methods       + user_sinks
effective_sanitizers  = built_in_sanitizer_methods  + user_sanitizers
```

---

## Config file location

Orihime looks for the security config file in this order:

1. The path in the `ORIHIME_SECURITY_CONFIG` environment variable (if set).
2. `~/.orihime/security.yml` (default).

If neither exists, only the built-in rules apply. The file is optional.

---

## Full YAML schema

```yaml
version: 1

sources:
  # Annotation-based: any method parameter carrying one of these annotations
  # is treated as user-controlled taint input.
  annotations:
    - "com.example.rpc.IncomingPayload"        # custom RPC input annotation
    - "org.springframework.web.bind.annotation.RequestParam"  # already built-in; harmless to repeat

  # Method-based: the return value of calls to these methods is tainted.
  methods:
    - "com.example.util.RequestContext.getUserInput"   # internal RPC context
    - "javax.servlet.http.HttpServletRequest.getParameter"    # already built-in

sinks:
  # Any call to these methods propagates taint into a dangerous operation.
  methods:
    - "com.example.legacy.ShellRunner.execute"   # legacy shell executor
    - "java.sql.Statement.execute"               # already built-in

sanitizers:
  # Calls to these methods are treated as sanitizers — taint stops propagating here.
  methods:
    - "com.example.util.Sanitizer.sanitizeForLegacy"
    - "org.springframework.web.util.HtmlUtils.htmlEscape"     # already built-in
```

All fields under `sources`, `sinks`, and `sanitizers` are optional. Omit any section you do not need.

---

## Built-in defaults

### Source annotations (Spring MVC + JAX-RS)

These annotation names cause the annotated parameter to be marked as a taint source:

| Framework | Annotations |
|---|---|
| Spring MVC | `RequestParam`, `PathVariable`, `RequestBody`, `RequestHeader`, `MatrixVariable`, `ModelAttribute` |
| JAX-RS | `QueryParam`, `PathParam`, `FormParam`, `HeaderParam`, `CookieParam` |

### Source methods

These method patterns cause the return value to be marked as tainted:

| Origin | Methods |
|---|---|
| Servlet API | `HttpServletRequest.getParameter`, `.getHeader`, `.getCookies`, `.getInputStream`, `.getReader` |
| Spring | `ServerHttpRequest.getBody` |
| Express / Node.js | `req.query`, `req.params`, `req.body`, `req.headers`, `request.query`, `request.params`, `request.body`, `request.headers` |
| Next.js / Web API | `searchParams.get`, `useSearchParams` |

### Sink methods

| Category | Methods |
|---|---|
| SQL | `Statement.execute`, `Statement.executeQuery`, `Statement.executeUpdate`, `PreparedStatement.execute`, `PreparedStatement.executeQuery` |
| Spring HTTP clients (SSRF) | `RestTemplate.getForEntity`, `.postForEntity`, `.exchange`, `.getForObject`, `.postForObject`, `WebClient.get`, `.post`, `.put`, `.delete`, `.patch` |
| Command execution | `Runtime.exec`, `ProcessBuilder.start` |
| Path traversal (Java) | `File.<init>`, `Files.readAllBytes`, `Files.newBufferedReader` |
| SQL (Node.js) | `Client.query`, `Pool.query`, `Connection.query`, `pool.query` |
| Command execution (Node.js) | `child_process.exec`, `.execSync`, `.spawn`, `.spawnSync` |
| Path traversal (Node.js) | `fs.readFile`, `.readFileSync`, `.createReadStream` |
| HTTP clients / SSRF (JS) | `axios.get`, `.post`, `.put`, `.delete`, `.patch`, `.request`, `fetch` |

### Sanitizer methods

| Method | Effect |
|---|---|
| `HtmlUtils.htmlEscape` | HTML encoding — stops XSS taint |
| `StringEscapeUtils.escapeHtml4` | HTML encoding — stops XSS taint |
| `ESAPI.encoder` | OWASP ESAPI encoding — stops XSS and injection taint |
| `Encode.forHtml` | OWASP Java Encoder — stops XSS taint |

---

## Matching rules

### Suffix matching (default)

For most patterns, Orihime matches on the **simple name** (the part after the last `.`). For example, `Statement.execute` matches any call where the receiver type ends with `Statement` and the method name is `execute`. This allows the pattern to match both `java.sql.Statement.execute` and `javax.persistence.Statement.execute` without listing both.

### `_ENDSWITH_ONLY_PATTERNS` — full-endswith matching

A subset of patterns use strict `endswith` matching to avoid false positives on Java codebases where short names like `get`, `query`, or `body` are extremely common method names. These patterns are:

- All JS/TS dotted source patterns: `req.query`, `req.body`, `request.params`, `searchParams.get`, etc.
- JS/TS SQL sink patterns: `Client.query`, `Pool.query`, `pool.query`, `Connection.query`
- All `axios.*` patterns
- Spring `WebClient.*` verb methods

For these patterns, the full pattern string must appear at the end of the resolved method name. A Java method named `SomeService.query` will **not** match `pool.query` because the full suffix `pool.query` is not present.

---

## JS/TS taint detection notes

JavaScript and TypeScript do not have parameter annotations. Taint is instead tracked via the handler function calling a source method. For example:

```typescript
export async function GET(request: NextRequest) {
  const q = request.nextUrl.searchParams.get("q");   // taint source: searchParams.get
  const results = await db.query(`SELECT * FROM t WHERE name = '${q}'`);  // sink
}
```

Orihime detects `searchParams.get` as the source and the SQL call as the sink and produces a finding.

**Limitation:** Indirect taint via a utility wrapper is missed. If your code does:

```typescript
function getQuery(req: Request) { return req.query.q; }
// ...
const q = getQuery(req);
db.execute(q);  // taint not detected — wrapped
```

The taint does not propagate through `getQuery` because Orihime's JS/TS pass does not track return-value taint across function boundaries. This is a known limitation; argument-level taint tracking (G2) is the backlog item that will address it.

---

## Verifying active config

To inspect the full set of active sources, sinks, and sanitizers (built-in + your overrides) at any time, call the MCP tool from within Claude Code:

```
list_security_config()
```

This returns three lists showing every active pattern exactly as Orihime will use them during analysis.

---

## Examples

### Adding a custom internal RPC source

Your service receives requests through an internal RPC framework that carries user-supplied data via a `@IncomingPayload` annotation:

```yaml
version: 1
sources:
  annotations:
    - "com.example.rpc.IncomingPayload"
```

After adding this, any method parameter annotated with `@IncomingPayload` will be treated as a taint source, and the full taint path to any sink will be reported.

### Adding a legacy shell executor sink

Your codebase contains a wrapper around `ProcessBuilder` that was not detected by the built-in rules:

```yaml
version: 1
sinks:
  methods:
    - "com.example.legacy.ShellRunner.execute"
```

All calls to `ShellRunner.execute` will now be treated as command injection sinks.

### Adding a custom sanitizer

Your team has a standardized input sanitization utility:

```yaml
version: 1
sanitizers:
  methods:
    - "com.example.util.InputSanitizer.clean"
```

Once added, taint paths that flow through `InputSanitizer.clean` will not produce findings — Orihime considers the taint neutralized at that point.
