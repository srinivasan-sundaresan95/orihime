# Orihime — Post-v1.2 Backlog

## What v1.2 Already Covers

| Capability | SonarQube Coverage | Orihime |
|---|---|---|
| Cross-file taint (SAST) | Community ✓ | S4 ✓ |
| Custom sources/sinks YAML | Enterprise | S5 ✓ |
| Second-order injection | Neither | S6 ✓ |
| OWASP/CWE/PCI/STIG reports | Enterprise | S7 ✓ |

Orihime closes ~70% of Aikido's advantage over SonarQube Community within a static graph.

---

## Backlog (Priority Order)

---

### A1 — Claude Code Agent Skills for Orihime MCP (~4h, DONE)

Four global Claude Code skills installed in `~/.claude/skills/`:
- `orihime-call-flow` — trace controller→service→repo→upstream chains via MCP
- `orihime-security-audit` — OWASP taint audit with S8 reachability filtering
- `orihime-perf-analysis` — hotspots, capacity estimation, cascade risk
- `orihime-change-impact` — blast radius + test surface for any code change

Skills use MCP tools only (no source file reads). Target: 5–8 tool calls per task vs 36 for source-only analysis.

---

### G10 — I/O Fan-out + Serial/Parallel Classification (~25h)

**What**: For each entry-point method (HTTP handler, `@KafkaListener`, `@Scheduled`), count how many distinct I/O operations (DB calls, HTTP calls, cache reads) fire per invocation, and classify each as **serial** (latency adds) or **parallel** (latency = max of group).

**Why this matters**: Knowing an endpoint makes 9 I/O calls is useful. Knowing 7 are parallel and 2 are serial tells you the actual latency structure — the parallel block costs the slowest single call, not the sum. This is the difference between a 50ms and a 450ms latency floor, derivable statically without any runtime profiler.

This feature directly addresses a gap found during an Orihime vs baseline benchmark: Orihime's MCP tools identified call chains and repositories correctly but could not report how many I/O operations fire per request or whether they were parallelised.

**I/O call sites detected:**
- **DB**: JPA repository method calls (`findBy*`, `save`, `saveAll`, `delete`, `findAll`, `findById`, `count`, `existsById`); JDBC template calls (`execute`, `executeQuery`, `executeUpdate`, `query`, `queryForObject`, `queryForList`)
- **HTTP**: Already-resolved `RestCall` nodes; `RestTemplate` (`exchange`, `getForObject`, `postForObject`); WebClient (`retrieve`, `bodyToMono`, `block`)
- **Cache**: Spring `@Cacheable`/`@CacheEvict`; calls to `get`/`put`/`evict` on objects named `*cache*` or `cacheManager`

**Parallel wrapper detection:**
- Kotlin coroutines: `async { }` + `awaitAll()` / `coroutineScope { }`
- Java `CompletableFuture`: `supplyAsync`, `runAsync`, `allOf`, `.thenCompose`, `.thenCombine`
- Spring `@Async` annotation on the method
- Reactor: `Mono.zip`, `Mono.when`, `Flux.merge`, `Flux.zip`

**What becomes possible with perf data (G8 integration):**
If perf results have been ingested via `ingest_perf_results`, Orihime can combine the fan-out topology with actual p99s:
```
Latency floor = sum(serial p99s) + max(parallel p99s)
```
This gives a real estimated minimum latency per endpoint from static structure alone.

**Schema changes (SCHEMA_VERSION 11):**
Add to `Method` node:
- `io_fanout INT64 DEFAULT 0` — total I/O calls in method body
- `io_parallel_count INT64 DEFAULT 0`
- `io_serial_count INT64 DEFAULT 0`
- `io_parallel_wrapper STRING DEFAULT ''` — dominant wrapper: `coroutine` | `completable_future` | `reactor` | `spring_async` | `""`

**New module:** `orihime/io_fanout_pass.py` — tree-sitter AST pass, mirrors `complexity_pass.py` structure.

**New MCP tool:** `find_io_fanout(repo_name, min_total=2)` — returns entry points ranked by total I/O, with serial/parallel breakdown and optional latency floor if perf data available.

**Sub-steps:**
- G10.1 — `io_fanout_pass.py`: AST walker for I/O call sites + parallel wrapper detection (Kotlin + Java)
- G10.2 — `schema.py`: add 4 new Method fields, bump SCHEMA_VERSION 10 → 11
- G10.3 — `kotlin_extractor.py`: call `detect_io_fanout` and store 4 fields on each method dict
- G10.4 — `java_extractor.py`: same as G10.3 for Java
- G10.5 — `indexer.py`: include 4 new fields in Method node batch write
- G10.6 — `mcp_server.py`: `find_io_fanout` tool — query entry points, join with Endpoint nodes, optionally join with PerfSample for latency floor

**What G10 does NOT do:**
- Control-flow graph (CFG) — classifying branches and loops requires a full CFG, not just a syntax tree. Tree-sitter gives a CST/AST; CFG construction is a separate ~200h project. G10 counts I/O calls in the method body regardless of which branch they're on. This means `total` is a **ceiling**, not an exact count for every execution path.
- Dynamic dispatch — if the call goes through an interface (e.g. `userRepository.findById` where `userRepository` is injected), G10 detects it via naming heuristics, not resolved types. Type resolution would require a full type-inference pass (separate work item).

---

### S8 — Entry-Point Reachability Filtering (~80h)

**What**: Today S4–S7 report every taint path that exists structurally in the code, including paths through dead code and internal-only utilities that are never called from a real entry point. S8 suppresses those false positives.

A taint finding is only surfaced if there is a CALLS path from a known entry point to the taint source. Entry points are: HTTP handlers (`@GetMapping` etc.), Kafka consumers (`@KafkaListener`), scheduled tasks (`@Scheduled`), JMS/RabbitMQ listeners. Everything else is hidden by default (with an opt-in "show all" flag).

Expected impact: 30–50% reduction in alert volume with no new true positives missed.

**Implementation**:
- Tree-sitter pass: detect `@KafkaListener`, `@Scheduled`, `@JmsListener`, `@RabbitListener` → add `is_entry_point BOOLEAN` to Method node (HTTP handlers already indexed as Endpoints)
- `find_reachable_sinks(repo_name)` MCP tool — BFS from entry points, filter `find_taint_sinks` to reachable only
- UI: "Reachable only" toggle on security findings page

---

### G7 — Static Complexity Hints (~30h)

**What**: Tree-sitter structural analysis to detect high-complexity patterns in method bodies, tagged on Method nodes as `complexity_hint`. No runtime data needed — immediate value from static structure alone.

Patterns detected:
- Nested loops over collections → `O(n²)-candidate`
- `.contains()` / `.indexOf()` on `List` inside a loop → `O(n²)-list-scan` (should be a `Set`)
- Recursive call to self without memoization → `recursive`
- JPA collection fetch inside a loop → `n+1-risk` (extends existing `find_eager_fetches`)
- Unbounded JPQL/query called from an endpoint with no `Pageable` parameter → `unbounded-query`

**Implementation**:
- New `complexity_pass.py` running after the main extractor on each Method's body subtree
- Add `complexity_hint STRING DEFAULT ''` to Method node (SCHEMA_VERSION bump)
- MCP tool: `find_complexity_hints(repo_name, min_severity="medium")` — list methods with hints, sorted by call-graph degree (high-degree + O(n²) = highest risk)

---

### G8 — Perf Result Ingestion + Hotspot Correlation (~20h)

**What**: Accept load test results (JMeter XML, Gatling simulation.log, or a simple JSON) and correlate with the static graph and G7 complexity hints to identify confirmed hotspots and capacity ceilings.

**What becomes possible with perf data:**

*Single load test run:*
- Critical call chain: walk CALLS graph from endpoint, weight each hop by callee p99, find longest weighted path — where time actually goes
- Variance risk: methods where `p99/p50 > 4` are unstable under load; flag as `HIGH_VARIANCE`
- Little's Law ceiling per endpoint: `concurrency = RPS × p99`. From thread pool size → RPS at which endpoint saturates

*Multiple runs at different RPS (load sweep):*
- Inflection point: fit p99 vs RPS curve; where second derivative goes positive = saturation threshold
- Flag endpoints within 20% of their estimated saturation RPS

*Live Prometheus/Mon-aaS data:*
- Same analysis but continuously updated. Orihime already has Mon-aaS MCP available
- Drift detection: p99 growing week-over-week on a method with `O(n²)` hint = leading indicator of data-growth regression before it becomes an incident

*Cross-service cascade risk:*
- If Service A calls Service B (via CALLS_REST) and Service B saturates at 200 RPS, then Service A degrades at any load generating >200 RPS downstream — even if Service A's own code is fine. The cross-service call graph makes this visible in a way no single-service tool can.

**New graph nodes:**
```
PerfSample:       {id, method_fqn, p50_ms, p99_ms, rps, sample_time, source}
CapacityEstimate: {id, endpoint_fqn, saturation_rps, ceiling_concurrency, risk_level}
OBSERVED_AT:      Method → PerfSample
```

**MCP tools:**
- `ingest_perf_results(repo_name, file_path)` — load JMeter/Gatling/JSON into graph
- `find_hotspots(repo_name)` — static hints × p99, sorted by risk score
- `estimate_capacity(repo_name)` — Little's Law per endpoint, flags near-saturation
- `find_cascade_risk(repo_name)` — downstream saturation limits on upstream endpoints

*Note: No tool today does this. SonarQube, Datadog, Grafana, Gatling each see only their slice. Orihime is the join layer.*

---

### G4 — Security + Performance Findings UI Tab (~25h)

Add a dedicated tab in the web UI showing findings from S4–S8 and G7–G8 in a filterable table: OWASP category, file, line, severity, complexity hint, p99 if available. Currently findings are MCP-only.

---

### G5 — Batch DB Writes + Server Mode (~45h)

**Problem**: KuzuDB's single-writer constraint means Phase 2 (all INSERTs) is fully serial — one CREATE per node. For repos >500 files this is the indexing bottleneck. On a shared bare metal server it also means "UI running + CI re-indexing simultaneously" crashes one of them.

**Fix A — Batch INSERT (~20h, do first)**: Buffer all nodes from all ParseResults in memory, then write per table using KuzuDB's multi-row `COPY FROM` syntax. Expected 5–10× write speedup with no architecture change.

**Fix B — WAL staging (~25h additional, if A isn't enough)**: Write Phase 2 results to an Arrow/Parquet file first (in-memory, near-instant), then bulk-load into KuzuDB in one pass. Fully decouples parse speed from DB write speed.

**Fix C — Write-serialization server (~20h, for bare metal multi-repo)**: A thin FastAPI process that owns the KuzuDB connection singleton, serializes all writes via an asyncio queue, and exposes the same HTTP endpoints the UI and MCP server already use. Developers running locally open KuzuDB directly as today — they are completely unaware of this layer. The server deployment runs via the FastAPI process instead. KuzuDB stays embedded; no new database engine.

No alternative database engine is worth considering. FalkorDB (Redis module) and Apache AGE (PostgreSQL extension) both require a separate server daemon — developers would need to run Redis or PostgreSQL before Orihime works. KuzuDB's embedded model is the reason the developer experience is frictionless and must be preserved.

---

### G3 — Node.js / TypeScript / Next.js Extractor (~60h)

`tree-sitter-javascript` and `tree-sitter-typescript` are both MIT-licensed. What can be extracted for the frontend layer (Point Bank BFF frontend, `react-hello-world` PoC):

- **Next.js App Router**: `export async function GET/POST/PUT/DELETE` → Endpoint nodes
- **Next.js Pages Router**: `export default function handler(req, res)` → Endpoint nodes
- **Express/Fastify**: `app.get/post/put/delete(path, handler)` → Endpoint nodes
- **`fetch()`, `axios.get/post`**: → RestCall nodes
- ES6 classes and methods → Class + Method nodes
- `import` graph → future dependency analysis

Python support (Django/Flask) is lower priority given current stack; keep as a placeholder.

---

### G1 — Callee Name on CALLS Edges (~30h)

Store the method name being called on CALLS edges (not just source/target node IDs). Enables "what external library methods does this service call?" without needing the callee's source indexed. Required foundation for precise argument-level taint (G2).

---

### G2 — Argument Position on CALLS Edges (~40h)

Add `caller_arg_pos INT64` and `callee_param_pos INT64` to CALLS edges. Enables precise data-flow taint: "does argument 0 of method A flow to parameter 1 of method B?" Upgrades S4 from reachability-based to value-flow-based taint — closer to CodeQL's analysis depth.

---

### S11 — License Compliance (~30h, low priority)

Flag GPL/AGPL/LGPL dependencies in a commercial project. Parse Maven/Gradle dependency tree, look up SPDX identifiers from Maven Central metadata or OSS Index API.

MCP tool: `find_license_violations(repo_name, allowed=["MIT","Apache-2.0","BSD-2-Clause"])`

---


## Not Worth Building in Orihime

| Capability | Reason |
|---|---|
| FalkorDB / Apache AGE / any server DB | All require a separate daemon process (Redis or PostgreSQL); developers would need to run infrastructure before Orihime works. KuzuDB's embedded model is non-negotiable for frictionless local use. G5 Fix C solves the multi-user server case without leaving KuzuDB. |
| Hardcoded secrets detection (S9) | Detekt secrets plugin + SonarQube Community S6437 + GitHub push protection already cover this |
| Dependency CVE standalone (S10) | Dependabot, SonarQube Community SCA, Snyk all do this; Orihime's angle (reachability-aware CVE) is a follow-on to S8 + G9, not a standalone project |
| IDE LSP integration (S12) | SonarLint + Detekt IntelliJ plugin already provide inline warnings; a fourth source causes alert fatigue; CI/CD is the better Orihime integration point |
| Watch mode (G6) | A Git post-commit hook calling `python -m orihime index` is a one-liner; v1.1-A blob hash skipping makes it fast enough |
| Auto-patch generation | Requires LLM + CI/CD pipeline; separate agent workflow |
| Runtime vulnerability validation | Requires live app + network; fundamentally dynamic |
| Malware in dependencies | Requires behavioral sandboxing |
| Container/OS layer scanning | Trivy/Grype; out of scope |
| IaC misconfiguration | Checkov/tfsec; Rakuten uses OPA/Kyverno |
| Control-flow graph (CFG) extraction | Requires full dataflow analysis beyond what tree-sitter's CST provides. Tree-sitter is a parsing library, not a compiler frontend — it does not produce SSA form, dominator trees, or PHI nodes. Implementing a correct CFG pass in Python would be ~200h and would still be less accurate than a JVM bytecode-level tool (e.g. ASM, Soot, or WALA). The G10 `io_fanout` feature deliberately counts I/O calls as a ceiling (all branches) rather than per-path, which is sufficient for the latency budgeting use case without requiring a CFG. |
| Type-resolved call dispatch | Resolving virtual dispatch (interface → concrete class) without a type inference engine requires either a full type-inference pass or a class hierarchy analysis (CHA). CHA is already partially implemented via the `IMPLEMENTS`/`EXTENDS` edges, but resolving which concrete method a `repository.findById()` call dispatches to at runtime requires propagating type information through the call graph — a compiler-level problem. Current approach: heuristic name matching (method names ending in `Repository`, `Repo`, `Service` etc.) which covers ~90% of Spring Boot patterns correctly. |
| Branch-level I/O path analysis | Determining exactly which I/O calls fire on a given execution path (e.g. "only called if Phase2 flag is true") requires both CFG extraction and constraint propagation. This is the problem CodeQL and Semgrep solve with full program analysis. Out of scope for a static graph tool; the correct answer for branch-level analysis is to point users at CodeQL. |
| Runtime call count profiling | Counting actual I/O invocations per request under production load is a runtime concern — belongs in Grafana/Datadog APM, not a static graph tool. G8 (perf ingestion) is the right layer for runtime data: ingest Gatling/JMeter results and correlate with the static graph, rather than trying to derive runtime counts from source. |
