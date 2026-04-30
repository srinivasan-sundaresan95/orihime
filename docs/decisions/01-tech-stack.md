# Decision: Technology Stack

## Python

Python is the implementation language for Dedalus. The choice is driven by the ecosystem:

- **tree-sitter** has first-class Python bindings (`pip install tree-sitter`) — the most mature non-JS binding available
- **KuzuDB** provides official Python bindings — the only embedded graph DB that does
- **MCP SDK** (`mcp`) is Python-first
- Scripts are short-lived (indexer runs then exits) — startup overhead is irrelevant
- Python 3.11+ dataclasses, `Protocol`, and `typing` provide enough structure without a compiled language

---

## Tree-sitter (MIT)

Tree-sitter is a source-accurate, incremental parser generator. Chosen over alternatives for:

- **Source accuracy** — produces ASTs from actual source text, giving correct line numbers, annotations, and modifiers; bytecode tools (java-callgraph) lose this
- **Language grammars** — `tree-sitter-java` and `tree-sitter-kotlin` are maintained, installable via pip
- **MIT license** — no commercial use restriction
- **Extensibility** — adding a new language = `pip install tree-sitter-<lang>` + implement `LanguageExtractor`

Rejected alternatives:
- **java-callgraph** — bytecode only, no line numbers, no annotations, Java-only
- **srcML** — XML transform, awkward to query, less maintained
- **JavaParser/KotlinParser** — JVM-based, would require a subprocess bridge

---

## KuzuDB (MIT)

KuzuDB is an embedded property graph database with Cypher query support. Chosen over:

- **Neo4j** — GPL v3 for Community; cannot use at Rakuten without license review. Enterprise is commercial.
- **SQLite + adjacency table** — possible but requires manual graph traversal in Python; no Cypher; no built-in path queries (`CALLS*1..N`)
- **DuckDB** — columnar, not graph-native; Cypher path queries would need manual implementation
- **TigerGraph / Memgraph** — server-only, no embedded mode for local dev

KuzuDB provides:
- Embedded mode (single file, `~/.dedalus/dedalus.db`) — always available, no server needed for local dev
- Server mode — deploy on BMaaS for team-shared access
- Kuzu Explorer — built-in web UI, no custom frontend needed
- Cypher path queries — `MATCH p=(a)-[:CALLS*1..5]->(b)` works natively

---

## MCP Python SDK (`mcp`)

The Model Context Protocol SDK provides the server scaffolding for Claude Code integration. The Python SDK is the reference implementation and is MIT-licensed. The server wraps KuzuDB queries and exposes them as Claude-callable tools.

---

## Deployment model

| Mode | Config | Use case |
|------|--------|----------|
| Local embedded | `KUZU_LOCAL_PATH=~/.dedalus/dedalus.db` | Developer workstation, always available |
| Team server | `KUZU_SERVER_URL=http://bmaas:8000` | Shared read access; CI writes only |
| Both simultaneously | Both env vars set | MCP server holds two named connections |

Single writer rule: only the CI indexing job writes to the server DB. Developers write only to local.

---

## What is deliberately NOT included

- **Web frontend** — Kuzu Explorer (bundled with KuzuDB) is sufficient; no custom React/Vue app
- **Message queue** — indexing is a CLI command, not a daemon; no async pipeline needed
- **ORM** — raw Cypher strings are readable and precise; an abstraction layer adds no value here
