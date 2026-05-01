---
name: orihime-security-audit
description: >
  Use when the user asks "run a security audit on X", "find SQL injection risks",
  "check taint paths", "any OWASP issues in repo X", "find injection vulnerabilities",
  "check license compliance", or wants to understand what untrusted input can reach
  dangerous sinks in a codebase. Uses Orihime MCP tools only — no source file reads.
---

# Orihime Security Audit Skill

## Trigger conditions

- "Run a security audit on [repo]"
- "Find SQL injection / XSS / injection risks"
- "Check taint paths in [repo]"
- "Any OWASP issues in [repo]?"
- "What can an attacker reach from a public endpoint?"
- "Find injection vulnerabilities"
- "Show me the security findings for [repo]"
- "Check license compliance for [repo]"

---

## Step 1 — Enumerate all entry points

```
mcp__orihime__find_entry_points(repo_name="<repo>")
```

Entry points include:
- HTTP handlers (`@GetMapping`, `@PostMapping`, etc.)
- Kafka consumers (`@KafkaListener`)
- Scheduled tasks (`@Scheduled`)
- JMS/RabbitMQ listeners

Note the total count — this is the attack surface.

---

## Step 2 — Find reachable taint sinks (entry-point filtered)

```
mcp__orihime__find_reachable_sinks(repo_name="<repo>")
```

This is the S8-filtered view: only sinks reachable from real entry points via CALLS paths.
Suppresses dead-code and internal-utility false positives (typically 30–50% reduction).

When to use unfiltered instead (user asks "all sinks" or "unfiltered"):
```
mcp__orihime__find_taint_sinks(repo_name="<repo>")
```

---

## Step 3 — Value-flow taint (stricter, argument-level)

```
mcp__orihime__find_taint_flows(repo_name="<repo>")
```

> **Limitation**: single-hop only — finds source-annotated methods that
> directly call a sink (caller_arg_pos=0). Misses handler → service → sink
> patterns (depth 2+) and taint passed as second+ argument.
>
> **Workaround for deeper paths**: use `find_reachable_sinks` to identify all
> sink-reachable methods from entry points, then call
> `find_callers(sink_method_fqn)` iteratively to trace backward toward the
> entry point. This reconstructs the path manually hop by hop.

Returns only findings where a tainted argument (@RequestParam/@RequestBody/@PathVariable)
flows directly into a known sink's first parameter via a CALLS edge.
This is stricter than find_reachable_sinks — fewer results, higher confidence.
Use both: reachable_sinks for breadth, taint_flows for high-confidence findings.

---

## Step 4 — Cross-service taint paths

```
mcp__orihime__find_cross_service_taint(repo_name="<repo>")
```

Taint paths where untrusted HTTP input crosses a service boundary — e.g. Service A's
request parameter flows to Service B's SQL sink via a REST call.
Higher severity because they bypass per-service input validation.

---

## Step 5 — Second-order injection

```
mcp__orihime__find_second_order_injection(repo_name="<repo>")
```

> ⚠️ **Manual review required** — second-order findings are structural
> approximations: a write method and read method in the same class are assumed
> to share a tainted entity. Each finding must be manually verified to confirm
> a real taint path exists before treating it as a confirmed vulnerability.

Detects patterns where user-controlled data is stored to DB (save/persist/merge)
and then read back and used as a sink (query/execute). This is a structural
approximation — use for prioritizing manual review, not as definitive findings.

---

## Step 6 — Generate framework-mapped report

```
mcp__orihime__generate_security_report(repo_name="<repo>", framework="owasp")
```

Framework options: `owasp`, `cwe`, `pci`, `stig`

OWASP result keys: `category`, `caller_fqn`, `sink_method`, `file_path`, `line_start`
CWE result keys: `cwe_id`, `caller_fqn`, `sink_method`, `file_path`, `line_start`
PCI result keys: `requirement`, `caller_fqn`, `sink_method`, `file_path`
STIG result keys: `vuln_id`, `caller_fqn`, `sink_method`, `file_path`

---

## Step 7 — License compliance (if requested)

```
mcp__orihime__find_license_violations(repo_name="<repo>")
```

Reads pom.xml / build.gradle from the repo root, queries Maven Central for each
dependency's SPDX license. Returns only VIOLATION and WARNING items (OK items filtered).

Result keys: `group`, `artifact`, `version`, `license`, `status`, `reason`
Status values: `VIOLATION` (GPL/AGPL/LGPL found), `WARNING` (unknown/ambiguous), `UNKNOWN`

---

## Step 8 — Verify active security config

```
mcp__orihime__list_security_config()
```

Shows the merged built-in + user-defined rules currently in effect. Use this if
results seem missing or unexpected — confirm the expected sources/sinks are loaded.

---

## Mapping sink types to OWASP categories

| Sink type | OWASP category |
|---|---|
| SQL query construction (execute, executeQuery) | A03 — Injection |
| JPQL/HQL string concat (createQuery) | A03 — Injection |
| `Runtime.exec()` / ProcessBuilder | A03 — Injection (command) |
| HttpServletResponse.sendRedirect | A01 — Open Redirect |
| File path construction (readAllBytes, newBufferedReader) | A01 — Path Traversal |
| WebClient/RestTemplate exchange | A10 — SSRF |
| Cross-service taint | A01/A03 + cross-boundary |

---

## Presenting findings

```
## Security Audit — [repo_name]

### Attack Surface
- Entry points: N (HTTP: X, Kafka: Y, Scheduled: Z)

### Findings Summary (S8 reachability-filtered)
| OWASP Category | Findings |
|---|---|
| A03 — Injection | X |
| A10 — SSRF | Y |

### Value-Flow Taint (high confidence)
- N findings where tainted args flow directly to sinks

### Cross-Service Taint
- N paths crossing service boundaries (higher severity)

### Second-Order Injection Candidates
- N structural candidates for manual review

### License Violations
- N violations (GPL/AGPL/LGPL detected)

### Top HIGH Findings
[List caller_fqn → sink_method @ file:line]
```

---

## Gotchas

### find_reachable_sinks vs find_taint_sinks
Always use `find_reachable_sinks` for a normal audit. Only fall back to `find_taint_sinks`
when the user explicitly wants unfiltered results OR when `find_reachable_sinks` returns
zero (which may mean the S8 pass hasn't run for this repo — re-index to fix).

### find_taint_flows is NOT a superset of find_reachable_sinks
`find_taint_flows` is stricter: it only catches arg-pos=0 flows. It can miss injection
paths where the tainted value is passed as the second argument. Always run both.

### find_second_order_injection is not value-flow
The tool detects write-then-read patterns within the same class by method name
heuristics (save/persist → findById/findAll in the same class). It does NOT
trace actual data flow. Expect 30–50% false positive rate on typical Java
Spring codebases. Use it to generate a shortlist for manual review, not as a
scanner output.

### find_taint_flows vs find_reachable_sinks — use both
find_taint_flows: confirmed value-flow (source annotation → direct sink call),
low false positives, misses depth 2+ paths.
find_reachable_sinks: reachability only (entry point → any sink via call graph),
higher coverage, no call chain returned, no sanitizer awareness.
For a thorough audit: run both. Findings in find_reachable_sinks but not in
find_taint_flows are candidates for manual taint tracing.

### Do NOT read source files
This skill uses MCP tools only. All findings are in the graph.
