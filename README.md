# Orihime

A cross-repository code knowledge graph for Java/Kotlin/JavaScript/TypeScript codebases. Orihime indexes your source code into an embedded [KuzuDB](https://kuzudb.com/) graph database using [tree-sitter](https://tree-sitter.github.io/) and exposes the graph through an **MCP server** (for AI assistants), a local web UI, and a CLI.

> **Mythology**: Orihime (織姫) is Vega — the weaving princess who weaves the fabric of the cosmos. She weaves connections. The tool that weaves your codebase into a single graph.

---

## What It Does

- **Call graph across repositories** — who calls what, across service boundaries, including REST calls resolved to the endpoint they target
- **Cross-repo taint analysis** — track user-controlled data from HTTP/Kafka/JMS entry points through the call graph to dangerous sinks (SQL injection, path traversal, XXE, deserialization, SSRF, log injection, …)
- **Security reports** — OWASP Top 10, CWE, PCI DSS, STIG frameworks; second-order injection detection; custom sources/sinks via YAML
- **Entry-point reachability filtering** — suppress false positives from dead code; only surface findings reachable from real entry points (HTTP handlers, `@KafkaListener`, `@Scheduled`, `@JmsListener`, `@RabbitListener`)
- **Complexity hints** — static O(n²) loop detection, N+1 JPA risk, unbounded queries, recursive calls — no profiler needed
- **Performance correlation** — ingest Gatling/JMeter load test results; correlate with the call graph to find confirmed hotspots and Little's Law capacity ceilings per endpoint
- **License compliance** — scan Maven/Gradle dependencies against SPDX identifiers; flag GPL/AGPL/LGPL in commercial projects
- **Incremental re-index** — git blob-hash-based skip; only changed files are re-parsed on subsequent runs
- **Multi-language** — Java, Kotlin, JavaScript, TypeScript (Next.js, Express, React)

---

## Quick Start — AI-first (Claude Code)

The primary way to use Orihime is through an AI assistant via MCP. You index once, then ask questions in natural language — no Cypher, no grep, no reading source files.

### 1. Install

```bash
git clone https://github.com/srinivasan-sundaresan95/orihime.git
cd orihime
pip install -e .
```

### 2. Index your repositories

```bash
python -m orihime index --repo /path/to/your/service-a --name service-a
python -m orihime index --repo /path/to/your/service-b --name service-b
```

### 3. Register with Claude Code

Add to `~/.claude/settings.json` (or your MCP client's config):

```json
{
  "mcpServers": {
    "orihime": {
      "command": "python3",
      "args": ["-m", "orihime", "serve"],
      "cwd": "/path/to/orihime",
      "env": { "ORIHIME_DB_PATH": "/home/user/.orihime/orihime.db" }
    }
  }
}
```

Restart Claude Code. The `orihime` MCP tools are now available.

### 4. Ask questions

```
Trace the call flow for GET /api/orders in service-a
Find SQL injection risks in service-b
What breaks if I change OrderService.processPayment?
Which endpoints are approaching saturation?
```

No source file reads. No grep. Claude uses the graph directly — typically 5–8 tool calls vs 30+ for source-only analysis.

> **CLI alternative**: All operations above are also available as Python commands (`python -m orihime index`, `python -m orihime ui`, etc.) if you prefer working outside an AI assistant. See [CLI Reference](#cli-reference) below.

---

## Feature Comparison

| Capability | Orihime | GitNexus | SonarQube Community | SonarQube Developer | SonarQube Enterprise |
|---|---|---|---|---|---|
| Cross-repo call graph | ✓ | ✓ | ✗ | ✗ | ✗ |
| REST endpoint resolution | ✓ | ✓ | ✗ | ✗ | ✗ |
| MCP integration (AI assistants) | ✓ | ✓ | ✓¹ | ✓¹ | ✓¹ |
| Claude Code hooks + skills | ✓ | ✓ | ✗ | ✗ | ✗ |
| Cross-file taint (SAST / injection) | ✓ | ✗ | ✗ | ✓ | ✓ |
| Second-order injection | ✓ | ✗ | ✗ | ✗ | ✗ |
| Entry-point reachability filter | ✓ | ✗ | ✗ | ✗ | ✗ |
| Custom sources/sinks (YAML) | ✓ | ✗ | ✗ | ✗ | ✓² |
| OWASP/CWE/PCI/STIG compliance reports | ✓ | ✗ | ✗ | ✗ | ✓ |
| Argument-level taint (value-flow) | ✓ | ✗ | ✗ | ✗ | ✗ |
| Complexity hints (O(n²), N+1) | ✓ | ✗ | partial | partial | partial |
| I/O fan-out + serial/parallel analysis | ✓ | ✗ | ✗ | ✗ | ✗ |
| Perf ingestion + capacity model | ✓ | ✗ | ✗ | ✗ | ✗ |
| Cross-service cascade risk | ✓ | ✗ | ✗ | ✗ | ✗ |
| License compliance | ✓ | ✗ | ✗ | ✗ | ✓³ |
| Embedded DB (no server daemon) | ✓ | ✓ | ✗ | ✗ | ✗ |
| Indexes Java / Kotlin | ✓ | ✓ | ✓ | ✓ | ✓ |
| Indexes JS / TS | ✓ | ✓ | ✓ | ✓ | ✓ |
| License | MIT | PolyForm NC | LGPL | Commercial | Commercial |

> ¹ Via the official [sonarqube-mcp-server](https://github.com/SonarSource/sonarqube-mcp-server) (SonarSource, production-ready). Works with all SonarQube editions.
> ² Custom taint sources/sinks require the Advanced Security add-on (Enterprise+).
> ³ License compliance (SBOM + policy enforcement) requires the Advanced Security add-on (Enterprise+).
>
> **GitNexus** (PolyForm Non-Commercial) provides cross-repo call graphs and MCP integration across 14 languages including Java and Kotlin. It does not cover SAST, perf analysis, or compliance reporting.

---

## MCP Tools Reference

### Call Graph

| Tool | Description |
|---|---|
| `find_callers(method_fqn)` | All methods that call the given method |
| `find_callees(method_fqn)` | All methods called by the given method |
| `blast_radius(method_fqn, max_depth)` | Transitive set of callers up to N hops |
| `find_endpoint_callers(http_method, path_pattern)` | Trace back from an HTTP endpoint to its callers |
| `find_implementations(interface_fqn)` | All classes implementing an interface |
| `find_superclasses(class_fqn, max_depth)` | Inheritance chain |
| `find_external_calls(repo_name)` | All calls to methods outside the indexed repo |

### Discovery

| Tool | Description |
|---|---|
| `search_symbol(query)` | Full-text search across class/method FQNs |
| `get_file_location(fqn)` | File path and line number for any class or method |
| `list_repos()` | All indexed repositories |
| `list_branches(repo_name)` | All indexed branches for a repo |
| `list_endpoints(repo_name)` | All HTTP endpoints in a repo |
| `list_unresolved_calls(repo_name)` | REST calls that couldn't be matched to an endpoint |
| `find_repo_dependencies(repo_name)` | Cross-service DEPENDS_ON edges |

### ORM / JPA

| Tool | Description |
|---|---|
| `list_entity_relations(repo_name)` | All JPA entity relationships |
| `find_eager_fetches(repo_name)` | EAGER-fetched collections (N+1 risk) |

### Security (SAST)

| Tool | Description |
|---|---|
| `find_taint_sinks(repo_name)` | All taint sinks reachable in the call graph |
| `find_taint_flows(repo_name)` | Value-flow taint: argument → parameter across CALLS edges |
| `find_cross_service_taint(repo_name, max_depth)` | Taint that crosses service boundaries via REST |
| `find_second_order_injection(repo_name)` | Taint stored to DB then re-read and used as sink |
| `find_entry_points(repo_name)` | All HTTP/Kafka/Scheduled/JMS/RabbitMQ entry points |
| `find_reachable_sinks(repo_name, show_all)` | Taint sinks filtered to those reachable from entry points only |
| `generate_security_report(repo_name, framework)` | Report in OWASP / CWE / PCI / STIG format |
| `list_security_config()` | Show active sources, sinks, and sanitizers from YAML config |

### Complexity & Performance

| Tool | Description |
|---|---|
| `find_complexity_hints(repo_name, min_severity)` | Methods flagged with O(n²), N+1, unbounded-query, recursive |
| `ingest_perf_results(repo_name, file_path)` | Load Gatling simulation.log, JMeter XML, or JSON perf data |
| `find_hotspots(repo_name)` | Complexity hints × p99 latency, sorted by risk score |
| `estimate_capacity(repo_name)` | Little's Law capacity per endpoint; flags near-saturation |
| `find_cascade_risk(repo_name)` | Cross-service cascade: upstream endpoints limited by downstream saturation |

### License Compliance

| Tool | Description |
|---|---|
| `find_license_violations(repo_name, allowed, skip_lookup)` | Flag GPL/AGPL/LGPL dependencies via Maven Central |

### Index

| Tool | Description |
|---|---|
| `index_repo_tool(repo_path, repo_name)` | Trigger an index from within the MCP session |

---

## CLI Reference

All operations are also accessible directly without an AI assistant:

```
python -m orihime index        --repo PATH  --name NAME  [--db PATH] [--force] [--branch NAME]
python -m orihime ui           [--port 7700] [--db PATH]
python -m orihime serve
python -m orihime resolve      [--db PATH]
python -m orihime write-server [--port 7701] [--db PATH]
```

| Command | Description |
|---|---|
| `index` | Parse a repository and write its graph into KuzuDB |
| `ui` | Start the local web UI on port 7700 |
| `serve` | Start the MCP server on stdio (for Claude Code, Claude Desktop, any MCP client) |
| `resolve` | Match RestCall URL patterns against Endpoints across all indexed repos |
| `write-server` | Start the write-serialization server for team/server deployments |

---

## Web UI

```
http://localhost:7700
```

| Page | Description |
|---|---|
| `/` | Call graph explorer: search methods, trace callers/callees, visualize CALLS graph |
| `/findings` | Security + complexity findings table — filter by OWASP category, severity, file |
| `/api/…` | JSON endpoints backing the UI (also usable directly) |

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ORIHIME_DB_PATH` | `~/.orihime/orihime.db` | Path to KuzuDB database directory |
| `ORIHIME_SERVER_URL` | _(unset)_ | URL of the write-serialization server (team mode) |

### Custom Sources and Sinks

Create `~/.orihime/security_config.yaml` (or set `ORIHIME_SECURITY_CONFIG`):

```yaml
sources:
  - method_pattern: ".*getCustomUserInput"
    description: "Custom input source"

sinks:
  - method_pattern: ".*legacyExec"
    sink_type: "COMMAND_INJECTION"
    description: "Legacy shell executor"

sanitizers:
  - method_pattern: ".*sanitizeForLegacy"
```

The built-in config covers `HttpServletRequest`, `@RequestParam`, `@PathVariable`, `@RequestBody`, JDBC `execute*`, JPA native queries, `Runtime.exec`, `ProcessBuilder`, XML parsers, `ObjectInputStream`, `Files.get`, `Paths.get`, `new URL`, logging calls, and more.

---

## Team / Server Mode

KuzuDB has a single-writer constraint. In team deployments where multiple developers re-index simultaneously, run the write-serialization server:

```bash
# On the shared server — owns the KuzuDB connection
python -m orihime write-server --port 7701 --db /shared/orihime.db

# Each developer's indexer sends writes to the server
ORIHIME_SERVER_URL=http://server:7701 python -m orihime index --repo /path --name my-service
```

Developers running locally without `ORIHIME_SERVER_URL` open KuzuDB directly as always. The web UI and MCP server always read directly from KuzuDB (reads do not go through the write server).

---

## Architecture

```
Source files
    │
    ▼ tree-sitter (Java, Kotlin, JS, TS)
ParseResult (plain Python dicts, picklable)
    │
    ▼ ProcessPoolExecutor (parallel parse workers)
Phase 2: KuzuDB writes (batched by table, 500-edge transactions)
    │
    ▼
KuzuDB embedded graph  ←──────────────────────────────┐
    │                                                   │
    ├── MCP server (FastMCP, stdio)                     │
    ├── Web UI (FastAPI, port 7700)                     │
    └── Write server (FastAPI, port 7701, team mode) ──┘
```

**Graph schema** (SCHEMA_VERSION 10):

| Node | Key fields |
|---|---|
| `Repo` | id, name, root_path |
| `File` | path, language, blob_hash, branch_name |
| `Class` | fqn, annotations, is_interface |
| `Method` | fqn, line_start, annotations, is_entry_point, complexity_hint |
| `Endpoint` | http_method, path, path_regex |
| `RestCall` | http_method, url_pattern |
| `EntityRelation` | source_class, target_class, fetch_type, relation_type |
| `PerfSample` | endpoint_fqn, p50_ms, p99_ms, rps, source |
| `CapacityEstimate` | endpoint_fqn, saturation_rps, ceiling_concurrency, risk_level |

| Relationship | Description |
|---|---|
| `CALLS` | Method → Method; carries callee_name, caller_arg_pos, callee_param_pos |
| `CALLS_REST` | Method → Endpoint (resolved cross-service call) |
| `UNRESOLVED_CALL` | Method → RestCall (not yet resolved) |
| `CONTAINS_CLASS` | File → Class |
| `CONTAINS_METHOD` | Class → Method |
| `EXPOSES` | Repo → Endpoint |
| `DEPENDS_ON` | Repo → Repo (cross-service dependency) |
| `EXTENDS` | Class → Class |
| `IMPLEMENTS` | Class → Class |
| `HAS_RELATION` | Class → EntityRelation |
| `OBSERVED_AT` | Method → PerfSample |

---

## Performance

### Query performance (graph DB)

Benchmarked on an 845-file Java/Kotlin service:

| Operation | Time |
|---|---|
| Cold index | ~67s |
| Incremental re-index (no changes) | ~34s |
| `find_callers` | <5ms |
| `blast_radius` (depth 3) | <15ms |
| `find_taint_sinks` (full repo) | <25ms |

Batch write speedup vs naive per-row writes: **12×**.

---

### AI assistant benchmark — tracing a single call flow

#### Java/Kotlin codebase (845 + 224 files, measured)

Benchmarked on `pointclubapp-api` (845 Kotlin files) and `point-bitcoin-internal-api` (224 Java files), tracing one controller endpoint through service → repositories → upstream APIs. GitNexus v1.6.3, Orihime v1.9, and a grep+source-read baseline were all measured on the same codebase on the same hardware (WSL2/Ubuntu, Intel i7, 2026-04-30).

| Approach | Cold index | Query latency | Avg tokens/query | Files read |
|---|---|---|---|---|
| **Baseline** — Claude reads source files directly | — | ~4–5 min | ~14,000 | 27 |
| **GitNexus v1.6.3** | 51.4s | 2–10s⁴ | ~1,490 | 0 |
| **Orihime v1.9** | **66.6s** | **3–22ms** | **~683** | **0** |

**Orihime vs baseline: 95% fewer tokens · 200–1,400× faster queries**  
**Orihime vs GitNexus: 2.2× fewer tokens · 200–1,400× faster queries · MCP-native**

The 7 Orihime tool calls produced ~80% of the structural picture (full controller→service→repo→upstream chain, 27 test methods surfaced, resilience wiring discovered automatically). The remaining ~20% — upstream API URLs, auth headers, branch-level control flow — requires targeted source reads, scoped to ~5 specific files rather than 27.

GitNexus's cold index is ~1.3× faster on NTFS (Node.js parse throughput advantage). On native Linux this gap narrows to near parity.

> ⁴ GitNexus query latency is dominated by live GitHub API round trips (1–3 per query × 500–2,000ms each, rate-limit dependent). Blast radius returned results in the wrong direction (upstream imports rather than downstream dependents).

---

## License

MIT
