---
name: orihime-perf-analysis
description: >
  Use when the user asks "find performance hotspots", "which endpoints are at risk of saturation",
  "analyze capacity for repo X", "find N+1 queries", "what's the cascade risk if service X is slow",
  "find I/O fanout", or wants to understand performance ceilings and structural complexity risks
  in a codebase. Uses Orihime MCP tools only — no source file reads.
---

# Orihime Performance Analysis Skill

## Trigger conditions

- "Find performance hotspots in [repo]"
- "Which endpoints are at risk of saturation?"
- "Analyze capacity for [repo]"
- "Find N+1 queries"
- "What's the cascade risk if [service] is slow?"
- "What are the O(n²) candidates in [repo]?"
- "Show me the complexity warnings for [repo]"
- "Where will [repo] fall over under load?"
- "Find methods with too many I/O calls"
- "Which endpoints are doing serial I/O that could be parallelized?"

---

## Step 0 — Check if perf data has been ingested

Before running the full analysis, note whether perf data is available.
`find_hotspots`, `estimate_capacity`, and `find_cascade_risk` return richer results
when JMeter/Gatling perf data has been ingested. Without it, rankings are structural-only.

If no perf data: note at the end of the report:
> "No perf data ingested — hotspot ranking is structural only. To get p99-ranked hotspots,
> run: `mcp__orihime__ingest_perf_results(repo_name='<repo>', file_path='/path/to/simulation.log')`"

---

## Step 1 — Static complexity hints

```
mcp__orihime__find_complexity_hints(repo_name="<repo>", min_severity="medium")
```

Hint types and meanings:

| Hint | Risk |
|---|---|
| `O(n2)-candidate` | Nested loops over collections — quadratic time |
| `O(n2)-list-scan` | `.contains()`/`.indexOf()` on a List inside a loop |
| `recursive` | Self-recursive without memoization |
| `n+1-risk` | JPA collection fetch inside a loop |
| `unbounded-query` | JPQL/query with no `Pageable` on an endpoint |

Results are sorted by `call_degree` (most-called methods first).
High call_degree + O(n²) hint = highest structural risk.

---

## Step 2 — I/O fan-out analysis (serial vs parallel)

```
mcp__orihime__find_io_fanout(repo_name="<repo>", min_total=2)
```

Returns entry-point methods with 2+ I/O calls (DB, HTTP, cache), split by serial vs parallel.
- `serial_io` — operations that add latency (run sequentially)
- `parallel_io` — operations wrapped in Kotlin async{}/CompletableFuture/Reactor
- `parallel_wrapper` — the detected wrapper type: `coroutine`, `completable_future`, `reactor`, `spring_async`
- `latency_floor_ms` — estimated lower-bound latency if perf data is available

Use this to find endpoints doing 3+ serial I/O calls that could be parallelized.

---

## Step 3 — JPA eager fetch detection (N+1 sources)

```
mcp__orihime__find_eager_fetches(repo_name="<repo>")
```

Returns all EAGER-fetched JPA collections. EAGER fetch on a collection loaded in a loop
= guaranteed N+1. Results: `source_class_fqn`, `field_name`, `relation_type`, `target_class_fqn`.

---

## Step 4 — Hotspots (complexity × p99 correlation)

```
mcp__orihime__find_hotspots(repo_name="<repo>")
```

If perf data ingested: returns methods ranked by `hint_weight × p99_ms`.
`O(n²)` hint AND high p99 = confirmed hotspot.

Without perf data: returns complexity hints ranked by call-graph degree only.

Result keys: `method_fqn`, `complexity_hint`, `p99_ms`, `p50_ms`, `risk_score`, `file_path`, `line_start`

---

## Step 5 — Capacity estimation (Little's Law)

```
mcp__orihime__estimate_capacity(repo_name="<repo>")
```

For each endpoint with perf data:
`saturation_rps = thread_pool_size (200) / (p99_ms / 1000)`

Risk levels:
- `CRITICAL` — current_rps > 80% of saturation
- `HIGH` — > 60%
- `MEDIUM` — > 40%
- `LOW` — otherwise

Result keys: `endpoint_fqn`, `current_rps`, `p99_ms`, `saturation_rps`, `ceiling_concurrency`, `risk_level`

---

## Step 6 — Cross-service cascade risk

```
mcp__orihime__find_cascade_risk(repo_name="<repo>")
```

Finds cases where Service A calls Service B (CALLS_REST edge), and Service B has
a saturation ceiling lower than Service A's current RPS. Service A degrades even
if its own code is healthy — invisible to single-service tools.

Result keys: `upstream_method_fqn`, `downstream_endpoint`, `downstream_saturation_rps`,
`upstream_current_rps`, `risk` (`"SATURATED"` or `"NEAR_SATURATION"`)

---

## Step 7 — Ingest perf data (if user wants to add it)

```
mcp__orihime__ingest_perf_results(
  repo_name="<repo>",
  file_path="/path/to/gatling/simulation.log"
)
```

Supported formats: JMeter XML, Gatling `simulation.log`, simple JSON `{endpoint_fqn, p50_ms, p99_ms, rps}`.
Returns: `{ingested, matched_methods, unmatched}`.
After ingestion, re-run Steps 4–6 for ranked results.

---

## Presenting findings

```
## Performance Analysis — [repo_name]

### Static Complexity Hints
| Method | File:Line | Hint | Call-Graph Degree | Risk |
|---|---|---|---|---|
| methodA | Service.kt:87 | n+1-risk | 12 callers | HIGH |

### I/O Fan-Out (endpoints with 2+ I/O calls)
| Endpoint | Total I/O | Serial | Parallel | Wrapper | Latency Floor |
|---|---|---|---|---|---|
| GET /v5/point_card | 4 | 3 | 1 | coroutine | ~900ms est |

### JPA Eager Fetches (N+1 risk)
- OrderEntity.items (OneToMany, EAGER) → OrderItem

### Capacity Estimates (Little's Law)
[if perf data available]
| Endpoint | p99 (ms) | Saturation RPS | Risk |
|---|---|---|---|
| GET /v5/point_card | 1200ms | 167 RPS | HIGH |

### Cascade Risk Chains
[if cross-service calls exist with perf data]
- [repo-A] method → calls [repo-B] endpoint
  repo-B saturates at 200 RPS

### Hotspots (ranked)
[if perf data: hint × p99; if not: hint × call_degree]

[if no perf data]:
> "Perf data not ingested — rankings are structural-only."
```

---

## Gotchas

### find_hotspots needs perf data for true ranking
Without `ingest_perf_results`, `find_hotspots` sorts by call_degree × hint_weight only —
no actual latency data. Always clarify whether rankings are structural or perf-confirmed.

### find_cascade_risk needs perf data in BOTH repos
`find_cascade_risk` requires OBSERVED_AT edges in the upstream repo AND PerfSample nodes
for the downstream endpoints. If either is missing, results will be empty.

### find_io_fanout threshold
Default `min_total=2`. For large repos with many endpoints, raise to `min_total=3` or `4`
to focus on the most I/O-heavy methods first.

### cascade_risk is the unique differentiator
No single-service tool (SonarQube, Datadog, Gatling) can detect cross-service saturation.
Orihime is the only tool with the cross-service CALLS_REST graph. Highlight this when presenting.

### Do NOT read source files
All data comes from the graph. Do not open `.kt`, `.java`, or config files.
