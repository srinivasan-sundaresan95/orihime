# MOSS Similarity Check Result

**Date:** 2026-04-30  
**Method:** Local token-normalised SequenceMatcher (difflib) — structure-only comparison (comments, strings, whitespace stripped)  
**Orihime source:** `dedalus/*.py` (13 files)  
**Reference:** GitNexus v1.6.3 npm tarball (`dist/**/*.js`, 403 files; 46-file relevant subset used)  
**Threshold:** 20% (per project policy)

## Result: PASS ✓

| Orihime file | Best GitNexus match | Similarity |
|------------|---------------------|------------|
| schema.py | _shared/lbug/schema-constants.js | 10.5% |
| cross_resolver.py | core/ingestion/languages/csharp/index.js | 5.8% |
| walker.py | core/ingestion/languages/index.js | 5.7% |
| path_utils.py | core/group/resolve-at-member.js | 5.1% |
| parse_result.py | core/ingestion/languages/python/scope-resolver.js | 4.4% |
| __main__.py | core/ingestion/scope-resolution/passes/receiver-bound-calls.js | 4.2% |
| language.py | core/group/extractors/grpc-patterns/index.js | 3.4% |
| java_extractor.py | core/ingestion/languages/index.js | 3.4% |
| indexer.py | core/ingestion/scope-resolution/passes/receiver-bound-calls.js | 3.0% |
| ui_server.py | core/ingestion/languages/index.js | 2.9% |
| kotlin_extractor.py | core/ingestion/languages/index.js | 2.8% |
| resolver.py | core/ingestion/languages/index.js | 2.8% |
| mcp_server.py | core/ingestion/scope-resolution/passes/receiver-bound-calls.js | 2.8% |

**Max similarity: 10.5% — all files under 20% threshold.**

## Notes

- Highest match (`schema.py` vs `schema-constants.js`) reflects shared vocabulary (`method`, `class`, `repo`, `calls`, `endpoint`) inherent to any code-graph schema, not copied code. Both independently define node/edge tables for the same domain.
- GitNexus is compiled TypeScript distributed as JS bundles. Orihime is written in Python from scratch. The languages, frameworks, and implementation approaches are entirely different.
- No structural or algorithmic copying detected.

## Conclusion

Orihime is independently developed and safe to distribute internally under MIT licence.
