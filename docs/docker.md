# Docker Setup

Orihime ships with a `docker-compose.yml` that starts all three services — UI, write server, and SSE MCP server — behind a shared KuzuDB volume.

---

## Quick start

```bash
docker compose up -d
```

That is all. The three containers start, the KuzuDB volume is created if it does not exist, and Orihime is ready to accept index submissions and queries.

To stop:

```bash
docker compose down
```

---

## Port map

| Port | Service | Purpose |
|---|---|---|
| 7700 | `orihime-ui` | Web UI — browse the call graph, view security findings |
| 7701 | `orihime-write` | Write-serialization server — receives index submissions |
| 7702 | `orihime-sse` | SSE MCP server — MCP tool access for CI runners and remote clients |

All three ports are exposed on the host by default. Restrict them to specific source IPs at the firewall or reverse proxy level for production deployments.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `ORIHIME_DB_PATH` | `/data/orihime.db` (inside container) | Path to the KuzuDB database directory; mapped to the shared `orihime-data` volume |
| `ORIHIME_SERVER_URL` | `http://orihime-write:7701` | Used by the UI and SSE containers to route write requests through the write server |

Override these in a `.env` file or by setting them in your shell before running `docker compose up`.

---

## docker-compose.yml walkthrough

### `orihime-ui`

Serves the Starlette web UI on port 7700. This container is read-only with respect to KuzuDB — it queries the graph directly via the shared volume and never calls the write server itself. Restart policy: `unless-stopped`.

### `orihime-write`

Owns the KuzuDB connection singleton. All index writes from CI runners and from `docker exec` calls go through this container. It serializes concurrent write requests via an asyncio queue so KuzuDB's single-writer constraint is never violated. Exposed on port 7701. Restart policy: `unless-stopped`.

### `orihime-sse`

Runs the MCP server with SSE transport on port 7702. CI reviewer workflows and any remote MCP client (Claude Desktop over network, CI agents) connect here to run graph queries. The SSE container reads KuzuDB directly from the shared volume; only writes are routed via `orihime-write`. Restart policy: `unless-stopped`.

### Shared volume

All three containers mount the same named volume `orihime-data` at `/data`. This is the directory where KuzuDB stores its database files. The volume persists independently of the containers — stopping and recreating the containers does not destroy indexed data.

---

## Indexing from outside the container

To index a repository on the host into the containerized Orihime instance, mount the repository directory as a bind mount and run the indexer via `docker exec`:

```bash
docker run --rm \
  -v /path/to/my-service:/repos/my-service:ro \
  --network orihime_default \
  -e ORIHIME_SERVER_URL=http://orihime-write:7701 \
  orihime:latest \
  python -m orihime index --repo /repos/my-service --name my-service
```

Or, if you prefer to run the indexer on the host (not inside the container) and send writes to the containerized write server:

```bash
ORIHIME_SERVER_URL=http://localhost:7701 \
  python -m orihime index --repo /path/to/my-service --name my-service
```

The write server at `localhost:7701` receives the index payload and writes it to the shared KuzuDB volume.

---

## BMaaS / self-hosted server deployment

When running Orihime on a BMaaS or on-premise server for team use:

1. **Firewall rules** — expose port 7701 and 7702 only to GitHub Actions runner IP ranges (or your VPN CIDR). Port 7700 can be restricted to your internal network if you want to limit UI access.

2. **TLS / reverse proxy** — if you need HTTPS, place an nginx or Caddy reverse proxy in front of the three containers. Map `https://orihime.internal/write` → `localhost:7701` and `https://orihime.internal/sse` → `localhost:7702`. Update `ORIHIME_WRITE_URL` and `ORIHIME_SSE_URL` secrets in GitHub accordingly.

3. **Persistent storage** — by default the `orihime-data` volume is stored at Docker's default volume location (`/var/lib/docker/volumes/`). For BMaaS deployments, bind-mount to a dedicated data disk instead:
   ```yaml
   volumes:
     orihime-data:
       driver: local
       driver_opts:
         type: none
         o: bind
         device: /data/orihime
   ```

---

## Upgrading

Pull the new image, then restart the containers. The KuzuDB volume is not affected.

```bash
docker compose pull
docker compose down
docker compose up -d
```

If a schema migration is required (check the release notes for `SCHEMA_VERSION` bumps), the write server will run the migration automatically on startup before accepting any connections. No manual migration steps are needed for patch and minor version upgrades.
