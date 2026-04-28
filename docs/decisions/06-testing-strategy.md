# Decision: Testing Strategy

## Three test levels

### Unit tests — fixture-based
Each extractor, the resolver, and path_regex compiler are tested against hand-crafted fixture files in `tests/fixtures/`. Fixtures are minimal valid source files that exercise exactly the patterns being tested — no real repo files.

Why fixtures and not real repo files?
- Real repo files change; fixture files are stable
- A fixture proves the specific pattern works; a real file proves nothing specific
- Fixtures run fast with no filesystem traversal

### Integration tests — real repos
`test_integration_bff.py` and `test_integration_bitcoin.py` index the actual repos and assert sanity bounds (count > N, known endpoint exists at known path). These catch regressions in the full indexer pipeline that unit tests cannot.

### Contract tests — interface enforcement
Tests for the `LanguageExtractor` protocol verify that both `JavaExtractor` and `KotlinExtractor` satisfy the protocol shape. New language extractors must pass the same contract test.

## Test-first discipline

Tests are written **against the interface contract**, not reverse-engineered from the implementation. The contract (function signature, expected outputs) is agreed before both the coder and test writer start. This means:

- A failing test means the implementation is wrong
- A test is only rewritten if a tie-breaker agent determines the test itself was incorrectly specified
- Tests do not have `# TODO: fix this` comments accommodating unfinished implementation

## What is not tested

- KuzuDB internal query correctness — KuzuDB is a dependency, not our code
- Tree-sitter parse correctness — tree-sitter is a dependency
- MCP protocol wire format — the MCP SDK handles this

These are tested by their respective projects. We test our use of them.

## pytest configuration

```
tests/
  fixtures/
    Sample.java
    SampleController.kt
    CallChain.java        (A→B→C for resolver tests)
  unit/
    test_schema.py
    test_java_extractor.py
    test_kotlin_extractor.py
    test_resolver.py
    test_compile_path_regex.py
    test_walker.py
    test_language_registry.py
  integration/
    test_integration_bff.py
    test_integration_bitcoin.py
    test_cross_resolution.py
```

Integration tests are marked `@pytest.mark.integration` and skipped in CI unless the repos are available on the runner.
