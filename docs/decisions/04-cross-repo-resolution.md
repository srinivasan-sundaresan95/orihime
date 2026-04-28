# Decision: Cross-repo REST Endpoint Resolution

## The problem

A BFF calls internal APIs over HTTP. `point-bank-bff` makes a `RestClient` call to `/bitcoin/wallet/status`. Without cross-repo resolution, this shows up only as an `UNRESOLVED_CALL` with `url_pattern = "/bitcoin/wallet/status"`. We don't know which repo owns that endpoint or which method handles it.

## Two-phase approach

**Phase 1 (per-repo indexing):** each repo is indexed independently. Endpoints and RestCalls are extracted and stored. No cross-repo matching happens yet.

**Phase 2 (cross-repo resolution):** after all repos are indexed, `run_cross_resolution(conn)` runs once:
1. Load all `Endpoint` nodes across all repos, compile each `path` into a `path_regex`
2. Load all `RestCall` nodes
3. Match: for each RestCall, find an Endpoint where `http_method` matches AND `path_regex` matches `url_pattern`
4. Write `CALLS_REST` edges (Method → Endpoint) for matches
5. Derive and write `DEPENDS_ON` edges (Repo → Repo)
6. Log unresolved RestCalls as warnings

## Path variable compilation

Spring path variables use `{variableName}` syntax. These are compiled to named regex capture groups:

```
/bitcoin/wallet/{easyId}  →  ^/bitcoin/wallet/(?P<easyId>[^/]+)$
/api/{version}/status     →  ^/api/(?P<version>[^/]+)/status$
/**                        →  ^(?:.*)$
```

The compiled regex is stored in `Endpoint.path_regex` at index time, not recomputed on every resolution run.

## Why not string equality?

String equality fails for any path with variables. `/bitcoin/wallet/{easyId}` would never match `/bitcoin/wallet/abc123`. Regex matching is the only correct approach for Spring-style paths.

## Unresolved calls are permanent, not errors

Some RestCalls will never resolve:
- Calls to external third-party APIs (Rakuten Pay, external partners)
- Calls to repos not yet indexed
- Calls where the URL is fully dynamic (stored as `DYNAMIC` sentinel)

These remain as `UNRESOLVED_CALL` edges and are surfaced via `list_unresolved_calls()`. They are intentional — removing them would hide real information.

## Idempotency

`run_cross_resolution()` can be re-run safely:
- It deletes all existing `CALLS_REST` and `DEPENDS_ON` edges before rewriting
- Adding a new repo, re-indexing, and re-running resolution produces the correct final state
