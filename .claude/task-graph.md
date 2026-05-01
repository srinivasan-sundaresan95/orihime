# Orihime Task Graph

## Dependency chain (linear)

```
T4 (Schema)
  └── T21 (Lang setup + LanguageExtractor protocol + registry)  ← extensibility foundation
        └── T2 (File walker — driven by registry, not hardcoded extensions)
              ├── T3 (Java extractor — implements LanguageExtractor)
              └── T17 (Kotlin extractor — implements LanguageExtractor)
                    └─(both)─ T14 (Local resolver)
                                └── T1 (Orchestrator)
                                      └── T18 (Indexer integration tests)
                                            └── T20 (Cross-repo: load+compile)
                                                  └── T19 (Cross-repo: match+write)
                                                        └── T15 (Cross-repo: integration test)
                                                              └── T16 (MCP: dual connection)
                                                                    └── T5 (MCP: 9 tools)
                                                                          └── T6 (MCP: errors+register)
                                                                                └── T7 (MOSS check) ← legal gate
                                                                                      └── T8 (Benefit: baseline tokens)
                                                                                            └── T9 (Benefit: Orihime tokens)
                                                                                                  └── T10 (Benefit: accuracy)
                                                                                                        └── T11 (Benefit: speed)
                                                                                                              └── T12 (Benefit: PR cost estimate)
                                                                                                                    └── T13 (Benefit: write report)
```

## Multi-language extensibility design (baked in from T21)

**Protocol:** every language extractor implements `LanguageExtractor`:
```python
class LanguageExtractor(Protocol):
    language: str                          # e.g. "java", "kotlin", "python"
    file_extensions: frozenset[str]        # e.g. frozenset({".java"})
    def extract(self, tree, source_bytes: bytes, file_id: str, repo_id: str) -> ExtractResult: ...
```

**Registry** (in `language.py`):
```python
_REGISTRY: dict[str, LanguageExtractor] = {}

def register(extractor: LanguageExtractor) -> None:
    _REGISTRY[extractor.language] = extractor

def get_extractor(lang: str) -> LanguageExtractor | None:
    return _REGISTRY.get(lang)

def registered_extensions() -> dict[str, str]:   # ext → lang
    return {ext: e.language for e in _REGISTRY.values() for ext in e.file_extensions}
```

**Walker** reads `registered_extensions()` — no hardcoded `.java`/`.kt`. Adding Python = register + done.

**Adding a new language (future):** see `docs/adding-a-language.md` (written at end of T21).

## Documentation written at end of each major task

| After task | Doc written |
|------------|-------------|
| T4 | `docs/schema.md` — node/edge tables, field reference, how to evolve schema |
| T21 | `docs/adding-a-language.md` — LanguageExtractor protocol, registry, 6-step checklist |
| T2 | (merged into adding-a-language.md — walker is driven by registry, no separate doc needed) |
| T3 + T17 | `docs/extractors.md` — Java + Kotlin extractor internals, annotation detection, DYNAMIC sentinel |
| T14 | `docs/resolver.md` — local symbol resolution algorithm, fqn matching, UNRESOLVED_CALL meaning |
| T1 + T18 | `docs/indexer.md` — CLI usage, orchestration flow, idempotent re-index, integration test setup |
| T20+T19+T15 | `docs/cross-repo-resolution.md` — path_regex compilation, matching algorithm, DEPENDS_ON derivation |
| T16+T5+T6 | `docs/mcp-server.md` — all 9 tools reference, dual connection setup, error format |
| T7 | `MOSS_RESULT.md` — similarity check result, date, method |

## Task reference card

| ID | Area | Subject | Sub-steps |
|----|------|---------|-----------|
| T4 | Schema | Create all KuzuDB node+edge tables | 1.1–1.17 (17 steps) |
| T21 | Indexer 2.1 | Language setup + LanguageExtractor protocol + registry | 2.1.1–2.1.6 |
| T2 | Indexer 2.2 | File walker (registry-driven, skip build/) | 2.2.1–2.2.5 |
| T3 | Indexer 2.3 | Java extractor (implements LanguageExtractor) | 2.3.1–2.3.10 |
| T17 | Indexer 2.4 | Kotlin extractor (implements LanguageExtractor) | 2.4.1–2.4.10 |
| T14 | Indexer 2.5 | Local symbol resolver (CALLS + UNRESOLVED_CALL) | 2.5.1–2.5.6 |
| T1 | Indexer 2.6 | Orchestrator (wire all, CLI, idempotent upsert) | 2.6.1–2.6.6 |
| T18 | Indexer 2.7 | Integration tests (BFF + bitcoin, spot-check) | 2.7.1–2.7.4 |
| T20 | Cross-repo 3.1 | Load endpoints + compile path_regex | 3.1.1–3.1.4 |
| T19 | Cross-repo 3.2 | Match RestCalls, write CALLS_REST + DEPENDS_ON | 3.3.1–3.3.6 |
| T15 | Cross-repo 3.3 | Integration test (BFF→bitcoin ≥1 edge) | 3.6.1–3.6.3 |
| T16 | MCP 4.1 | Dual connection (local+server, KUZU_*) | 4.1.1–4.1.5 |
| T5 | MCP 4.2 | 9 query tools (find_callers … list_unresolved) | 4.2.1–4.2.9 |
| T6 | MCP 4.3 | Error handling + Claude Code registration | 4.3.1–4.3.4 |
| T7 | MOSS | Similarity check vs GitNexus, fix if >20% | 6.1–6.8 |
| T8 | Benefit | Token baseline: plain Claude, 5 questions | 16.1–16.7 |
| T9 | Benefit | Token with Orihime: same 5 questions via MCP | 17.1–17.5 |
| T10 | Benefit | Accuracy comparison + hallucination examples | 18.1–18.5 |
| T11 | Benefit | Speed comparison (p50/p95, amortized index) | 19.1–19.4 |
| T12 | Benefit | PR cost estimate: 4 repos, git log, sensitivity | 20.1–20.7 |
| T13 | Benefit | Write report → Downloads/dedalus-cost-savings-report.md | 21.1–21.6 |

## Quick lookup: step detail

### T4 Schema (1.1–1.17)
1.1 pip install kuzu; verify import
1.2–1.7 CREATE NODE TABLE: Repo, File, Class, Method, Endpoint, RestCall
1.8–1.13 CREATE REL TABLE: CALLS, CALLS_REST, UNRESOLVED_CALL, CONTAINS, EXPOSES, DEPENDS_ON
1.14 drop_schema()
1.15 init_schema() idempotent
1.16 test_schema.py — catalog assertions
1.17 git init + initial commit
→ END: write docs/schema.md

### T21 Language setup (2.1.1–2.1.6)  ← updated for extensibility
2.1.1 pip install tree-sitter tree-sitter-java tree-sitter-kotlin
2.1.2 verify Java + Kotlin parse round-trip (no ERROR node)
2.1.3 language.py — define LanguageExtractor Protocol (language, file_extensions, extract())
2.1.4 language.py — implement register(), get_extractor(), registered_extensions()
2.1.5 language.py — get_parser(lang: str) -> Parser; cache per language
2.1.6 test: register a mock extractor; assert registered_extensions() returns its extension
→ END: write docs/adding-a-language.md

### T2 File walker (2.2.1–2.2.5)  ← updated: registry-driven
2.2.1 walker.py — walk_repo(root: Path) -> Iterator[(Path, lang)]
2.2.2 call registered_extensions() to build ext→lang map; no hardcoded .java/.kt
2.2.3 skip dirs: build/ out/ generated/ .gradle/ .git/ node_modules/
2.2.4 test on point-bank-bff — count>0, no /build/, all returned langs are registered
2.2.5 test: register mock extractor for .xyz → walker yields .xyz files

### T3 Java extractor (2.3.1–2.3.10)
2.3.1 java_extractor.py — JavaExtractor dataclass implementing LanguageExtractor
       language="java", file_extensions=frozenset({".java"})
2.3.2 extract() — query class_declaration → Class nodes (name, fqn, annotations)
2.3.3 extract() — query method_declaration → Method nodes (name, fqn, line_start, annotations)
2.3.4 collect method annotations from preceding siblings
2.3.5 detect @Get/Post/Put/Delete/RequestMapping → Endpoint (http_method, path)
2.3.6 handle class-level @RequestMapping prefix
2.3.7 detect RestTemplate/WebClient call sites → RestCall (url_pattern)
2.3.8 handle UriComponentsBuilder → DYNAMIC sentinel
2.3.9 create fixture tests/fixtures/Sample.java
2.3.10 test_java_extractor.py — assert 1 Class, 2 Methods, 2 Endpoints, 1 RestCall
       register JavaExtractor() at module level
→ END (after T17): write docs/extractors.md

### T17 Kotlin extractor (2.4.1–2.4.10)
2.4.1 kotlin_extractor.py — KotlinExtractor dataclass implementing LanguageExtractor
       language="kotlin", file_extensions=frozenset({".kt", ".kts"})
2.4.2 extract() — class_declaration + object_declaration + companion_object → Class
2.4.3 extract() — function_declaration → Method; detect suspend modifier → is_suspend=True
2.4.4 collect annotations from function
2.4.5 detect Spring MVC annotations → Endpoint
2.4.6 handle class-level @RequestMapping prefix
2.4.7 detect RestClient/WebClient/RestTemplate call expressions → RestCall
2.4.8 Kotlin string templates → DYNAMIC sentinel
2.4.9 create fixture tests/fixtures/SampleController.kt
2.4.10 test_kotlin_extractor.py — assert 1 Class, 2 suspend Methods, 2 Endpoints, 1 RestCall
       register KotlinExtractor() at module level
→ END (both T3+T17): write docs/extractors.md

### T14 Local resolver (2.5.1–2.5.6)
2.5.1 build_fqn_index(methods) → dict[fqn, node_id]
2.5.2 resolve_calls(tree, src, fqn_index) → list[CallEdge]
2.5.3 emit CALLS edge on fqn match
2.5.4 emit UNRESOLVED_CALL on no match
2.5.5 fixture: A()→B()→C() same file
2.5.6 test: 2 CALLS edges, 0 UNRESOLVED
→ END: write docs/resolver.md

### T1 Orchestrator (2.6.1–2.6.6)
2.6.1 indexer.py — index_repo(repo_path, repo_name, db_path)
2.6.2 open/create KuzuDB; call init_schema if needed
2.6.3 upsert Repo node; delete existing nodes/edges for repo_id
2.6.4 walk → for each (path, lang) call get_extractor(lang).extract() → write nodes
2.6.5 call resolver → write CALLS + UNRESOLVED_CALL
2.6.6 __main__.py CLI: --repo --name --db (default ~/.orihime/orihime.db)

### T18 Indexer integration tests (2.7.1–2.7.4)
2.7.1 test_integration_bff.py — MATCH (m:Method) count > 50
2.7.2 MATCH (e:Endpoint) count > 5 for BFF
2.7.3 test_integration_bitcoin.py — counts sane
2.7.4 spot-check 3 known endpoint paths; line_start != 0
→ END (T1+T18): write docs/indexer.md

### T20 Cross-repo load+compile (3.1.1–3.1.4)
3.1.1 load_all_endpoints(conn) → list[Endpoint]
3.1.2 compile_path_regex: {var} → (?P<var>[^/]+); anchor ^...$
3.1.3 handle /** → (?:.*)
3.1.4 test_compile_path_regex.py — 5 cases: plain, single-var, multi-var, wildcard, trailing-slash

### T19 Cross-repo match+write (3.3.1–3.3.6)
3.3.1 load_all_rest_calls(conn)
3.3.2 match_calls(rest_calls, endpoints_with_regex) — http_method + regex match
3.3.3 write CALLS_REST edges
3.3.4 write DEPENDS_ON edges (unique repo pairs)
3.3.5 log unresolved RestCalls as WARNING
3.3.6 run_cross_resolution(conn) orchestrates all

### T15 Cross-repo integration test (3.6.1–3.6.3)
3.6.1 test_cross_resolution.py — index BFF + bitcoin; run resolution
3.6.2 assert CALLS_REST count ≥ 1
3.6.3 assert DEPENDS_ON count ≥ 1; verify repo names
→ END (T20+T19+T15): write docs/cross-repo-resolution.md

### T16 MCP dual connection (4.1.1–4.1.5)
4.1.1 mcp_server.py startup — read KUZU_LOCAL_PATH, open embedded KuzuDB
4.1.2 read KUZU_SERVER_URL — open remote if set
4.1.3 get_connection(source="local") → conn; ValueError if server not configured
4.1.4 test: local-only → server call raises ValueError mentioning KUZU_SERVER_URL
4.1.5 test: both envs → both connections succeed

### T5 MCP 9 tools (4.2.1–4.2.9)
4.2.1 find_callers(fqn, depth, source)
4.2.2 find_callees(fqn, depth, source)
4.2.3 find_endpoint_callers(http_method, path, source)
4.2.4 find_repo_dependencies(repo_name, source)
4.2.5 blast_radius(fqn, source)
4.2.6 search_symbol(query, source)
4.2.7 get_file_location(fqn, source)
4.2.8 list_endpoints(repo_name, source)
4.2.9 list_unresolved_calls(repo_name, source)

### T6 MCP errors+register (4.3.1–4.3.4)
4.3.1 wrap all handlers in try/except → structured error JSON
4.3.2 input validation (empty fqn/path)
4.3.3 add orihime to ~/.claude/settings.json mcpServers
4.3.4 test from Claude Code: list_endpoints("point-bank-bff") returns data
→ END (T16+T5+T6): write docs/mcp-server.md

### T7 MOSS (6.1–6.8)
6.1 install MOSS client
6.2 collect GitNexus .js/.ts into /tmp/gitnexus_src/
6.3 submit Orihime .py vs GitNexus via MOSS
6.4 review report; record similarity % per pair
6.5 flag pairs >20%
6.6 rewrite flagged segments
6.7 re-run MOSS; confirm all <20%
6.8 write MOSS_RESULT.md; commit

### T8–T13 Benefit Analysis
T8 16.1–16.7: 5 questions; full repo context to Claude; record input+output tokens+cost per question
T9 17.1–17.5: index bitcoin; same 5 questions via MCP; record tokens; compute reduction ratio
T10 18.1–18.5: ground-truth verification; score both; document hallucination examples
T11 19.1–19.4: wall-clock latency p50/p95 both; amortize indexing over 100q/month
T12 20.1–20.7: git log 4 repos; PRs/year; 3q/PR × savings; ¥ table; sensitivity low/mid/high
T13 21.1–21.6: compile report → Downloads/dedalus-cost-savings-report.md
