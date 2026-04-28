# Decision: Graph Schema

## Node types and their purpose

| Node | Purpose |
|------|---------|
| `Repo` | Root of each indexed repository. All other nodes carry a `repo_id` foreign key. |
| `File` | One node per source file. Carries language and path. |
| `Class` | A class, interface, object, or companion object declaration. |
| `Method` | A method or function. The primary unit of call graph analysis. |
| `Endpoint` | An HTTP endpoint exposed by a `@RestController` or equivalent. |
| `RestCall` | An outbound HTTP call made by application code (RestClient, WebClient, RestTemplate). |

## Edge types and their semantics

| Edge | From → To | Meaning |
|------|-----------|---------|
| `CONTAINS` | File→Class, Class→Method | Structural containment |
| `CALLS` | Method→Method | Intra-repo method invocation (resolved by local symbol resolver) |
| `UNRESOLVED_CALL` | Method→RestCall | Outbound HTTP call not yet matched to an Endpoint |
| `CALLS_REST` | Method→Endpoint | Outbound HTTP call resolved to a concrete Endpoint across repos |
| `EXPOSES` | Repo→Endpoint | A repo exposes this endpoint to the network |
| `DEPENDS_ON` | Repo→Repo | Derived: repo A makes at least one resolved call into repo B |

## Why UNRESOLVED_CALL is a first-class edge (not a flag)

Storing unresolved calls as a `RestCall` node + `UNRESOLVED_CALL` edge (rather than a nullable field on Method) means:

- They can be queried directly: `MATCH (m)-[:UNRESOLVED_CALL]->(rc)` to find all dead or external calls
- Resolution is idempotent: cross-repo resolver deletes `UNRESOLVED_CALL` and creates `CALLS_REST` in one transaction
- Unresolved calls to external third-party APIs are a valid permanent state, not an error

## Why DEPENDS_ON is derived, not asserted

`DEPENDS_ON` edges are written by the cross-repo resolver as a consequence of finding `CALLS_REST` matches. They are never asserted manually. This means the dependency graph is always consistent with the actual call graph — no stale "we depend on X" declarations.

## Field design principles

- Every node carries `repo_id` — enables fast per-repo queries without joins
- `Method.fqn` is the globally unique identifier for cross-repo resolution (format: `com.example.Foo.bar`)
- `Endpoint.path_regex` is computed and stored at index time (not at query time) — path variable conversion `{id}` → `(?P<id>[^/]+)` happens once
- `RestCall.url_pattern` stores the URL as extracted from source — may contain Spring template variables or the `DYNAMIC` sentinel if the URL is runtime-computed

## Schema evolution

When adding a new field to an existing node type:
1. Add the field to `schema.py` with a default value (KuzuDB supports `DEFAULT`)
2. Bump the schema version constant in `schema.py`
3. Update `init_schema()` to migrate existing DBs if needed (add column via `ALTER TABLE`)
4. Update the extractor that populates the field
5. Update `docs/schema.md` (the living reference, written after T4)
