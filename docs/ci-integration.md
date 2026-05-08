# CI Integration — GitHub Actions PR Review

Orihime integrates with GitHub Actions to automatically index pull request branches, compare findings against the master baseline, and post inline comments on new security or quality issues introduced by the PR.

---

## Overview

On each pull request open or push event, a GHA workflow indexes the PR branch into Orihime, then a reviewer workflow compares the resulting findings against the master-branch baseline. Only new findings introduced by the PR are surfaced as inline comments or PR-level annotations. When the PR is merged or closed, a cleanup workflow removes the branch index to keep the database compact.

The CI integration does not replace the local developer workflow — it adds a second layer of structured feedback at code review time, using the same MCP tools that developers use interactively.

---

## Architecture

### Port map

| Port | Service | Purpose |
|---|---|---|
| 7700 | UI server | Read-only web UI; not used by CI |
| 7701 | Write server | Receives index writes from GHA runners |
| 7702 | SSE MCP server | Exposes MCP tools to the reviewer workflow |

All three services share the same KuzuDB database volume. The write server serializes concurrent writes from CI (one index job per open PR branch) so KuzuDB's single-writer constraint is not violated.

### How GHA runners connect

The GHA runner calls the write server (`ORIHIME_WRITE_URL`) to POST index results, and calls the SSE MCP server (`ORIHIME_SSE_URL`) to run reviewer queries. Both URLs must be reachable from the GitHub Actions runner IP ranges. If your Orihime instance runs on a private BMaaS server, configure a firewall rule or use GitHub's [IP ranges](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-githubs-ip-addresses) to allow inbound connections.

---

## Prerequisites

1. Orihime running in Docker on a server reachable from GitHub Actions runners (see [Docker setup](docker.md)).
2. Two GitHub repository secrets configured:

| Secret | Example value | Description |
|---|---|---|
| `ORIHIME_WRITE_URL` | `http://your-server:7701` | Write server endpoint for index submissions |
| `ORIHIME_SSE_URL` | `http://your-server:7702` | SSE MCP server endpoint for reviewer queries |

---

## Workflow reference

| Workflow file | Trigger | What it does |
|---|---|---|
| `orihime-index.yml` | PR opened, PR synchronized (push) | Indexes the PR branch into Orihime via the write server |
| `orihime-review.yml` | After `orihime-index.yml` completes | Queries the SSE MCP server; diffs findings against master baseline; posts inline comments for new findings |
| `orihime-cleanup.yml` | PR closed (merged or abandoned) | Calls the write server to delete the PR branch index; re-indexes master if the PR was merged |

---

## Finding tiers

Not every finding type is appropriate for an inline PR comment. The table below defines how each category is surfaced.

| Finding type | Surfaced as | Notes |
|---|---|---|
| SQL injection (direct) | Inline comment on the offending line | High confidence; taint source to sink in same PR diff |
| Path traversal | Inline comment on the offending line | High confidence |
| Command injection | Inline comment on the offending line | High confidence |
| SSRF | Inline comment on the offending line | High confidence |
| Cross-service taint (cross-repo) | PR-level comment | Line number belongs to a different repo; inline not possible |
| Second-order injection | Weekly digest only | 30–50% false positive rate (S8 suppression not yet applied); surfacing inline would create noise |
| N+1 JPA risk | PR-level comment | Structural finding; not always actionable in isolation |
| O(n²) complexity hint | PR-level comment | Informational; does not block the PR |
| License violation | PR-level comment | Informational; requires human review before action |

---

## Delta logic

The reviewer workflow operates on the difference between the PR branch and the master branch, not on the total finding set.

**Steps:**

1. Query `find_taint_sinks(branch="master")` — store as baseline set B.
2. Query `find_taint_sinks(branch="<pr-branch>")` — store as candidate set C.
3. New findings = C minus B (by finding fingerprint: sink FQN + taint source annotation + OWASP category).
4. Post comments only for findings in the new set.

This means a pre-existing vulnerability in master does not generate a PR comment (it is the team's responsibility to address it separately). Only code introduced or modified by the PR can trigger a new finding comment.

---

## Branch lifecycle

| Event | Action |
|---|---|
| PR opened | Index PR branch → write server; run reviewer → post comments |
| PR push (new commit) | Re-index PR branch (incremental); re-run reviewer; update existing comments |
| PR merged | Re-index master branch; delete PR branch index |
| PR closed without merge | Delete PR branch index |

Branch indexes are stored under `branch_name = "<pr-branch>"` in the `File` nodes. The cleanup step issues a DELETE on all nodes where `branch_name` matches the closed branch.

---

## Customising sources and sinks

The findings surfaced in CI use the same security configuration as local analysis. To add custom taint sources, sinks, or sanitizers specific to your codebase, see [Security Config](security-config.md).

Custom rules take effect on the next index run — no workflow changes are needed.

---

## Troubleshooting

### Write server unreachable

**Symptom:** `orihime-index.yml` fails with a connection error or timeout on the index POST step.

**Fix:** Check that `ORIHIME_WRITE_URL` is set correctly in your repository secrets and that the server's firewall allows inbound connections from GitHub Actions IP ranges. Run `curl -s $ORIHIME_WRITE_URL/health` from a runner step to confirm connectivity before the index step.

### SSE URL returns 404

**Symptom:** `orihime-review.yml` fails with HTTP 404 when connecting to `ORIHIME_SSE_URL`.

**Fix:** Port 7702 is the SSE MCP server. If you mistakenly set `ORIHIME_SSE_URL` to port 7700 (the UI), the `/mcp` path will not exist on that server and you will get a 404. Update the secret to use port 7702.

### Stale branch indexes accumulating

**Symptom:** The KuzuDB database grows unboundedly; old PR branch names appear in `list_branches()` results.

**Fix:** Confirm that `orihime-cleanup.yml` is triggered on `pull_request` events with `types: [closed]`. If the workflow trigger is missing or the cleanup step fails silently, stale indexes will accumulate. Run a one-time cleanup by calling `DELETE FROM File WHERE branch_name NOT IN ('master', 'main')` via the write server's admin endpoint, or re-index from scratch if the database has grown too large.
