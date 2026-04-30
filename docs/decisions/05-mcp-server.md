# Decision: MCP Server Design

## Tool selection rationale

The 9 MCP tools cover the full set of questions an engineer asks when navigating an unfamiliar codebase:

| Question type | Tool |
|---------------|------|
| Who calls this? | `find_callers` |
| What does this call? | `find_callees` |
| Which clients hit this endpoint? | `find_endpoint_callers` |
| What does this service depend on? | `find_repo_dependencies` |
| If I change this, what breaks? | `blast_radius` |
| Where is this class/method? | `search_symbol`, `get_file_location` |
| What APIs does this service expose? | `list_endpoints` |
| What external calls are untracked? | `list_unresolved_calls` |

## Dual connection model

The MCP server holds two named KuzuDB connections:

```
KUZU_LOCAL_PATH=~/.dedalus/dedalus.db    → always required (local embedded)
KUZU_SERVER_URL=http://bmaas:8000    → optional (team server)
```

All tools accept `source: str = "local"` parameter. Default is `"local"`. Pass `source="server"` to query the team-shared DB. If `KUZU_SERVER_URL` is not set and `source="server"` is requested, the tool returns a structured error with a helpful message rather than crashing.

## Why not auto-detect which source has fresher data?

Automatic freshness detection would require comparing index timestamps across two connections on every query. This adds latency and complexity for a marginal benefit. The developer knows which source they want — local (their own recent index) vs server (CI-maintained, shared state). Make it explicit.

## Error format

All tool errors return a consistent structure:

```json
{
  "error": {
    "code": "KuzuQueryError",
    "message": "MATCH (m:Method ...) — label Method not found"
  }
}
```

Claude Code parses this and surfaces it to the user rather than receiving an empty result or an exception traceback.

## Registration

The MCP server is registered in `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "dedalus": {
      "command": "python",
      "args": ["-m", "dedalus.mcp_server"],
      "env": {
        "KUZU_LOCAL_PATH": "/home/srini/.dedalus/dedalus.db"
      }
    }
  }
}
```

`KUZU_SERVER_URL` is added to `env` when the BMaaS server is deployed (Phase 2).
