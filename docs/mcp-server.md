# Orihime MCP Server

Orihime exposes its code knowledge graph as an [MCP](https://modelcontextprotocol.io) server so that AI assistants (Claude Code, Claude Desktop) can query it via natural language.

## Quick start

Index a repo first, then start the server:

```bash
# Index one or more repos
python -m orihime index --repo /path/to/my-repo --name my-repo

# Start the MCP server (stdio transport)
python -m orihime serve
```

## Claude Code registration

Add the following to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "orihime": {
      "type": "stdio",
      "command": "/path/to/dedalus/.venv/bin/python",
      "args": ["-m", "orihime", "serve"],
      "cwd": "/path/to/dedalus",
      "env": {
        "ORIHIME_DB_PATH": "/home/youruser/.orihime/orihime.db"
      }
    }
  }
}
```

After adding this entry, restart Claude Code. The `dedalus` MCP server will appear in the tool list.

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `ORIHIME_DB_PATH` | `~/.orihime/orihime.db` | Path to the KuzuDB database |
| `ORIHIME_SERVER_URL` | *(unset)* | Reserved for Phase 2 (remote KuzuDB HTTP endpoint) |

## Available tools

### `find_callers(method_fqn)`
Find all methods that directly call the given method.

```
find_callers("com.example.UserService.findById")
# → [{fqn: "com.example.UserController.getUser", file_id: "...", line_start: 42}]
```

### `find_callees(method_fqn)`
Find all methods called by the given method.

### `find_endpoint_callers(http_method, path_pattern)`
Find the handler method for an HTTP endpoint and all its upstream callers.

```
find_endpoint_callers("GET", "/api/users/{id}")
# → [{role: "handler", fqn: "...", ...}, {role: "caller", fqn: "...", ...}]
```

### `find_repo_dependencies(repo_name)`
Find all repositories that the given repository depends on (via cross-repo REST calls resolved by the cross-resolver).

### `blast_radius(method_fqn, max_depth=3)`
Find all methods transitively affected by changing the given method. Performs a BFS over reverse CALLS edges, capped at `max_depth` (maximum 10).

```
blast_radius("com.example.Pricing.calculate", max_depth=4)
# → [{fqn: "...", depth: 1}, {fqn: "...", depth: 2}, ...]
```

### `search_symbol(query)`
Case-insensitive substring search over class and method names.

```
search_symbol("interest")
# → [{type: "class", fqn: "...", file_id: "..."}, {type: "method", ...}]
```

### `get_file_location(fqn)`
Get the source file and line number for a method or class. Returns `null` if not found.

### `list_endpoints(repo_name="")`
List all HTTP endpoints in the graph. Pass a repo name to filter.

```
list_endpoints("point-bitcoin-internal-api")
# → [{http_method: "GET", path: "/bitcoin/api/v1/balance", handler_fqn: "...", repo_name: "..."}]
```

### `list_unresolved_calls(repo_name="")`
List outgoing REST calls that haven't been matched to a known endpoint. These are candidates for cross-repo resolution.

### `index_repo_tool(repo_path, repo_name)`
Index a repository directly from within Claude Code. Useful for indexing on-the-fly without a terminal.

```
index_repo_tool("/home/srini/repos/my-service", "my-service")
# → {repos: 1, files: 42, classes: 18, methods: 210, endpoints: 12, rest_calls: 30, call_edges: 85}
```

## Connection lifecycle

The server opens the KuzuDB lazily on the first tool call and reuses the connection for the lifetime of the process. After `index_repo_tool` completes, the connection is reset so subsequent queries see the freshly-indexed data.

If `ORIHIME_DB_PATH` doesn't exist yet (no repo indexed), all query tools return empty results gracefully instead of raising an error.

## Phase 2: Team / server mode

Set `ORIHIME_SERVER_URL` to a remote KuzuDB HTTP endpoint to share a single indexed graph across a team. The local file is ignored when this variable is set. Implementation is deferred to Phase 2.

## Known limitations

- `@BitcoinEndpoint` and other custom Spring controller annotations are not extracted as endpoints (Phase 2).
- Recursive variable-pattern Cypher is not used (KuzuDB 0.11.x compat); `blast_radius` uses iterative BFS in Python.
- `file_id` in results is a UUID internal to the graph; resolve it via `get_file_location` or query `File` nodes directly to get the actual path.
