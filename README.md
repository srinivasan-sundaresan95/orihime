# Orihime

A cross-repository code knowledge graph for Java/Kotlin/JavaScript/TypeScript codebases. Orihime indexes your source code into an embedded [KuzuDB](https://kuzudb.com/) graph database using [tree-sitter](https://tree-sitter.github.io/) and exposes the graph through an MCP server, a CLI, and a local web UI.

> **Mythology**: Orihime (織姫) is Vega — the weaving princess who weaves the fabric of the cosmos. She weaves connections. The tool that weaves your codebase into a single graph.

---

## What It Does

- **Call graph across repositories** — who calls what, across service boundaries, including REST calls resolved to the endpoint they target
- **Cross-repo taint analysis** — track user-controlled data from HTTP/Kafka/JMS entry points through the call graph to dangerous sinks (SQL injection, path traversal, XXE, deserialization, SSRF, log injection, …)
- **Security reports** — OWASP Top 10, CWE, PCI DSS, STIG frameworks; second-order injection detection; custom sources/sinks via YAML
- **Entry-point reachability filtering** — suppress false positives from dead code; only surface findings reachable from real entry points (HTTP handlers, @KafkaListener, @Scheduled, @JmsListener, @RabbitListener)
- **Complexity hints** — static O(n²) loop detection, N+1 JPA risk, unbounded queries, recursive calls — no profiler needed
- **Performance correlation** — ingest Gatling/JMeter load test results; correlate with the call graph to find confirmed hotspots and Little's Law capacity ceilings per endpoint
- **License compliance** — scan Maven/Gradle dependencies against SPDX identifiers; flag GPL/AGPL/LGPL in commercial projects
- **Incremental re-index** — git blob-hash-based skip; only changed files are re-parsed on subsequent runs
- **Multi-language** — Java, Kotlin, JavaScript, TypeScript (Next.js, Express, React)

---

## Quick Start

```bash
# Install
pip install -e .

# Index a repository
python -m orihime index --repo /path/to/your/repo --name my-service

# Start the web UI
python -m orihime ui          # http://localhost:7700

# Start the MCP server (for Claude / any MCP client)
python -m orihime serve
```

### Prerequisites

- Python 3.11+
- `pip install kuzu tree-sitter fastmcp fastapi uvicorn`

---

## CLI Reference

```
python -m orihime index  --repo PATH  --name NAME  [--db PATH] [--force] [--branch NAME]
python -m orihime ui     [--port 7700] [--db PATH]
python -m orihime serve
python -m orihime resolve  [--db PATH]
python -m orihime write-server  [--port 7701] [--db PATH]
```

| Command | Description |
|---|---|
| `index` | Parse a repository and write its graph into KuzuDB |
| `ui` | Start the local web UI on port 7700 |
| `serve` | Start the MCP server on stdio (for Claude Code, Claude Desktop) |
| `resolve` | Match RestCall URL patterns against Endpoints across all indexed repos |
| `write-server` | Start the write-serialization server for team/server deployments |

### Incremental Re-index

By default, `index` skips any file whose git blob hash matches the stored hash. Pass `--force` to re-parse everything.

### Branch Support

```bash
python -m orihime index --repo /path --name my-service --branch feature/my-branch
```

Graph nodes are tagged with `branch_name` for multi-branch analysis.

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

The Findings page aggregates:
- Taint sinks reachable from entry points (SQL injection, path traversal, XXE, …)
- Complexity hints (O(n²) loops, N+1 risk, unbounded queries)
- OWASP category, CWE ID, file, line number, severity per finding

---

## MCP Tools

Connect Claude Code (or any MCP client) to the Orihime MCP server with:

```json
{
  "mcpServers": {
    "orihime": {
      "command": "python3",
      "args": ["-m", "orihime", "serve"],
      "cwd": "/path/to/indra",
      "env": { "ORIHIME_DB_PATH": "/home/user/.orihime/orihime.db" }
    }
  }
}
```

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

### Security (S4–S8)

| Tool | Description |
|---|---|
| `find_taint_sinks(repo_name)` | All taint sinks reachable in the call graph |
| `find_taint_flows(repo_name)` | Value-flow taint: argument position → parameter position across CALLS edges |
| `find_cross_service_taint(repo_name, max_depth)` | Taint that crosses service boundaries via REST |
| `find_second_order_injection(repo_name)` | Taint stored to DB then re-read and used as sink |
| `find_entry_points(repo_name)` | All HTTP/Kafka/Scheduled/JMS/RabbitMQ entry points |
| `find_reachable_sinks(repo_name, show_all)` | Taint sinks filtered to those reachable from entry points only |
| `generate_security_report(repo_name, framework)` | Report in OWASP / CWE / PCI / STIG format |
| `list_security_config()` | Show active sources, sinks, and sanitizers from YAML config |

### Complexity & Performance (G7–G8)

| Tool | Description |
|---|---|
| `find_complexity_hints(repo_name, min_severity)` | Methods flagged with O(n²)-candidate, n+1-risk, unbounded-query, recursive |
| `ingest_perf_results(repo_name, file_path)` | Load Gatling simulation.log, JMeter XML, or JSON perf data |
| `find_hotspots(repo_name)` | Complexity hints × p99 latency, sorted by risk score |
| `estimate_capacity(repo_name)` | Little's Law capacity per endpoint; flags near-saturation |
| `find_cascade_risk(repo_name)` | Cross-service cascade: upstream endpoints limited by downstream saturation |

### License Compliance (S11)

| Tool | Description |
|---|---|
| `find_license_violations(repo_name, allowed, skip_lookup)` | Flag GPL/AGPL/LGPL dependencies; lookup via Maven Central |

### Index

| Tool | Description |
|---|---|
| `index_repo_tool(repo_path, repo_name)` | Trigger an index from within the MCP session |

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

## Team / Server Mode (G5 Fix-C)

KuzuDB has a single-writer constraint. In team deployments where multiple developers re-index simultaneously, run the write-serialization server:

```bash
# On the shared server — owns the KuzuDB connection
python -m orihime write-server --port 7701 --db /shared/orihime.db

# Each developer's indexer sends writes to the server
ORIHIME_SERVER_URL=http://server:7701 python -m orihime index --repo /path --name my-service
```

Developers running locally do not set `ORIHIME_SERVER_URL` — they open KuzuDB directly as always.

The web UI and MCP server always read directly from KuzuDB (reads do not go through the write server).

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

Benchmarked on `pointclubapp-api` (845 Java/Kotlin files):

| Operation | Time |
|---|---|
| Cold index | ~67s |
| Incremental re-index (no changes) | ~34s |
| `find_callers` | <5ms |
| `blast_radius` (depth 3) | <15ms |
| `find_taint_sinks` (full repo) | <25ms |

Batch write speedup vs naive per-row writes: **12×** (G5 Fix-A).

---

## Feature Comparison

| Capability | Orihime | SonarQube Community | SonarQube Enterprise |
|---|---|---|---|
| Cross-file taint (SAST) | ✓ | ✓ | ✓ |
| Custom sources/sinks | ✓ (YAML) | ✗ | ✓ |
| Second-order injection | ✓ | ✗ | ✗ |
| OWASP/CWE/PCI/STIG reports | ✓ | ✗ | ✓ |
| Entry-point reachability filter | ✓ | ✗ | ✗ |
| Complexity hints (O(n²), N+1) | ✓ | partial | partial |
| Perf ingestion + capacity model | ✓ | ✗ | ✗ |
| Cross-repo call graph | ✓ | ✗ | ✗ |
| REST endpoint resolution | ✓ | ✗ | ✗ |
| License compliance | ✓ | ✗ | ✓ |
| Argument-level taint (G2) | ✓ | ✗ | ✗ |
| MCP integration (Claude) | ✓ | ✗ | ✗ |
| Embedded DB (no server daemon) | ✓ | N/A | N/A |
| License | MIT | LGPL | Commercial |

---

## Platform Context

Orihime is the **code-structure explanation layer** in a wider observability platform:

| Project | Role |
|---|---|
| **Orihime** (this repo) | Code knowledge graph — what the code does structurally |
| **Styx** | SLI aggregator — what the running system is doing right now |
| **Sibyl** | Daily early-warning: Styx yesterday × Orihime → performance risk report |
| **Charon** | Incident analyzer: PagerDuty alert × Styx × Kibana × Orihime → correlated root-cause |

Orihime is deliberately free of Rakuten-specific integrations so it can be open-sourced. Sibyl and Charon are separate projects.

---

## License

MIT
