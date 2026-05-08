#!/usr/bin/env python3
"""orihime_review.py — Post Orihime security findings as GitHub PR comments.

Connects to an Orihime MCP/SSE server via direct JSON-RPC HTTP calls (no mcp SDK),
computes the delta between master baseline and the PR branch, then posts findings
as inline review comments (where in-diff) or a summary PR comment.

Usage:
    python orihime_review.py \\
        --pr 42 \\
        --repo owner/repo \\
        --sse-url https://orihime.example.com:7702 \\
        --github-token ghp_... \\
        --branch feature/my-branch \\
        --base-branch master \\
        [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from typing import Any

import httpx

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(levelname)s [orihime-review] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MCP / SSE JSON-RPC helpers
# FastMCP SSE transport:
#   GET  /sse           — opens the event stream (we use it only to get session_id)
#   POST /messages      — sends a JSON-RPC request; returns result synchronously
#                         (FastMCP 1.x with SSE transport replies inline)
#
# We use the simpler approach: POST /messages directly with a request_id and
# read the JSON response body.  FastMCP returns the JSON-RPC result in the
# HTTP response body when called this way.
# ---------------------------------------------------------------------------

_JSONRPC_ID = 1


def _next_id() -> int:
    global _JSONRPC_ID
    _JSONRPC_ID += 1
    return _JSONRPC_ID


def call_tool(
    client: httpx.Client,
    sse_url: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> Any:
    """Call an MCP tool via FastMCP's POST /messages JSON-RPC endpoint.

    FastMCP SSE transport accepts direct JSON-RPC 2.0 calls at POST /messages.
    The response body contains the JSON-RPC result (content list with text/json).

    Returns the parsed content of the first text item, or raises on error.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": _next_id(),
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    url = sse_url.rstrip("/") + "/messages"
    log.info("Calling tool %s with %s", tool_name, arguments)
    resp = client.post(url, json=payload, timeout=120.0)
    resp.raise_for_status()
    body = resp.json()

    if "error" in body:
        raise RuntimeError(f"MCP tool error for {tool_name}: {body['error']}")

    result = body.get("result", {})
    content = result.get("content", [])
    if not content:
        return []

    # FastMCP returns content as list of {type, text} items
    first = content[0]
    if first.get("type") == "text":
        raw = first["text"]
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return content


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

GITHUB_API = "https://api.github.com"


def gh_get(client: httpx.Client, path: str) -> Any:
    resp = client.get(f"{GITHUB_API}{path}")
    resp.raise_for_status()
    return resp.json()


def gh_post(client: httpx.Client, path: str, payload: dict[str, Any]) -> Any:
    resp = client.post(f"{GITHUB_API}{path}", json=payload)
    resp.raise_for_status()
    return resp.json()


def get_pr_diff_positions(
    github_client: httpx.Client, repo: str, pr_number: int
) -> dict[tuple[str, int], int]:
    """Return a mapping of (file_path, line_number) → diff_position.

    Parses the unified diff from the GitHub PR diff API to map source file
    line numbers to their diff position (which is what the review comments API
    requires for ``position``).
    """
    headers = {"Accept": "application/vnd.github.v3.diff"}
    resp = github_client.get(
        f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}",
        headers=headers,
    )
    resp.raise_for_status()
    diff_text = resp.text

    positions: dict[tuple[str, int], int] = {}
    current_file: str | None = None
    diff_pos = 0
    right_line = 0

    for raw_line in diff_text.splitlines():
        # New file header
        if raw_line.startswith("diff --git"):
            current_file = None
            diff_pos = 0
            right_line = 0
            continue

        if raw_line.startswith("+++ b/"):
            current_file = raw_line[6:]
            diff_pos = 0
            right_line = 0
            continue

        if current_file is None:
            continue

        if raw_line.startswith("@@ "):
            # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
            m = re.search(r"\+(\d+)(?:,\d+)?", raw_line)
            if m:
                right_line = int(m.group(1)) - 1
            diff_pos += 1
            continue

        if raw_line.startswith("-"):
            diff_pos += 1
            # Removed lines don't advance right_line
            continue

        if raw_line.startswith("+"):
            right_line += 1
            diff_pos += 1
            positions[(current_file, right_line)] = diff_pos
            continue

        # Context line
        if raw_line.startswith(" ") or raw_line == "":
            right_line += 1
            diff_pos += 1

    return positions


# ---------------------------------------------------------------------------
# Delta computation
# ---------------------------------------------------------------------------

def sink_key(sink: dict[str, Any]) -> tuple[str, str]:
    """Stable identity key for a reachable sink finding."""
    return (sink.get("caller_fqn", ""), sink.get("sink_method", ""))


def compute_new_sinks(
    baseline: list[dict[str, Any]],
    pr_sinks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return sinks present in pr_sinks but not in baseline."""
    baseline_keys = {sink_key(s) for s in baseline}
    return [s for s in pr_sinks if sink_key(s) not in baseline_keys]


# ---------------------------------------------------------------------------
# Comment formatting
# ---------------------------------------------------------------------------

OWASP_LINKS: dict[str, str] = {
    "A01": "https://owasp.org/Top10/A01_2021-Broken_Access_Control/",
    "A02": "https://owasp.org/Top10/A02_2021-Cryptographic_Failures/",
    "A03": "https://owasp.org/Top10/A03_2021-Injection/",
    "A04": "https://owasp.org/Top10/A04_2021-Insecure_Design/",
    "A05": "https://owasp.org/Top10/A05_2021-Security_Misconfiguration/",
}


def _owasp_link(category: str) -> str:
    code = (category or "").upper()
    for prefix, url in OWASP_LINKS.items():
        if prefix in code:
            return f"[{category}]({url})"
    return category or "unknown"


def format_inline_comment(sink: dict[str, Any]) -> str:
    caller = sink.get("caller_fqn", "unknown caller")
    sink_method = sink.get("sink_method", "unknown sink")
    category = sink.get("sink_category", "")
    return (
        f"**Orihime security finding** — new taint sink introduced\n\n"
        f"- **Caller:** `{caller}`\n"
        f"- **Sink:** `{sink_method}`\n"
        f"- **Category:** {_owasp_link(category)}\n\n"
        f"*Detected by [Orihime](https://github.com/search?q=orihime+code+graph) "
        f"static taint analysis.*"
    )


def format_taint_path_comment(path: dict[str, Any]) -> str:
    source = path.get("source_method_fqn", "unknown source")
    sink = path.get("sink_method_fqn", "unknown sink")
    sink_type = path.get("sink_type", "")
    chain = path.get("call_chain", [])
    length = path.get("path_length", len(chain))
    chain_str = " → ".join(chain) if chain else "N/A"
    return (
        f"**Orihime taint path** — user-controlled data reaches a dangerous sink\n\n"
        f"- **Source:** `{source}`\n"
        f"- **Sink:** `{sink}` ({sink_type})\n"
        f"- **Path length:** {length} hops\n"
        f"- **Call chain:** `{chain_str}`\n\n"
        f"*Detected by [Orihime](https://github.com/search?q=orihime+code+graph) "
        f"multi-hop taint analysis.*"
    )


def format_summary_comment(
    new_sinks: list[dict[str, Any]],
    out_of_diff_taint_paths: list[dict[str, Any]],
    cross_service: list[dict[str, Any]],
) -> str:
    parts: list[str] = ["## Orihime Security Review\n"]

    if not new_sinks and not out_of_diff_taint_paths and not cross_service:
        return (
            "## Orihime Security Review\n\n"
            "No new security findings introduced by this PR.\n\n"
            "*Powered by [Orihime](https://github.com/search?q=orihime+code+graph) "
            "code knowledge graph.*"
        )

    total = len(new_sinks) + len(out_of_diff_taint_paths) + len(cross_service)
    parts.append(
        f"> **{total} new finding(s)** detected in this PR vs the master baseline.\n"
    )

    if new_sinks:
        parts.append(f"\n### Reachable Taint Sinks ({len(new_sinks)} new)\n")
        for s in new_sinks:
            caller = s.get("caller_fqn", "?")
            sink = s.get("sink_method", "?")
            category = s.get("sink_category", "")
            file_path = s.get("file_path", "")
            line = s.get("line_start", "")
            location = f"`{file_path}:{line}`" if file_path else ""
            parts.append(
                f"- **`{caller}`** calls sink **`{sink}`** "
                f"({_owasp_link(category)}) {location}\n"
            )

    if out_of_diff_taint_paths:
        parts.append(f"\n### Taint Paths (out-of-diff, {len(out_of_diff_taint_paths)})\n")
        for p in out_of_diff_taint_paths:
            source = p.get("source_method_fqn", "?")
            sink = p.get("sink_method_fqn", "?")
            length = p.get("path_length", "?")
            parts.append(f"- `{source}` → `{sink}` ({length} hops)\n")

    if cross_service:
        parts.append(f"\n### Cross-Service Taint ({len(cross_service)} finding(s))\n")
        for c in cross_service:
            src_repo = c.get("source_repo", "?")
            dst_repo = c.get("sink_repo", "?")
            method = c.get("sink_method_fqn", "?")
            parts.append(f"- `{src_repo}` → `{dst_repo}`: `{method}`\n")

    parts.append(
        "\n---\n*Powered by [Orihime](https://github.com/search?q=orihime+code+graph) "
        "code knowledge graph static analysis.*"
    )
    return "".join(parts)


# ---------------------------------------------------------------------------
# Main review logic
# ---------------------------------------------------------------------------

def run_review(
    pr_number: int,
    repo: str,
    sse_url: str,
    github_token: str,
    branch: str,
    base_branch: str,
    dry_run: bool,
) -> None:
    gh_headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    mcp_client = httpx.Client(timeout=120.0)
    github_client = httpx.Client(headers=gh_headers, timeout=30.0)

    try:
        # ------------------------------------------------------------------
        # 1. Fetch baseline (master) and PR branch sinks
        # ------------------------------------------------------------------
        log.info("Fetching master baseline sinks (repo=%s, branch=%s)", repo, base_branch)
        baseline_sinks: list[dict] = call_tool(
            mcp_client, sse_url, "find_reachable_sinks",
            {"repo_name": f"{repo}@{base_branch}"},
        )
        if not isinstance(baseline_sinks, list):
            log.warning("find_reachable_sinks(master) returned non-list: %r", baseline_sinks)
            baseline_sinks = []

        log.info("Fetching PR branch sinks (repo=%s, branch=%s)", repo, branch)
        pr_sinks: list[dict] = call_tool(
            mcp_client, sse_url, "find_reachable_sinks",
            {"repo_name": f"{repo}@{branch}"},
        )
        if not isinstance(pr_sinks, list):
            log.warning("find_reachable_sinks(PR) returned non-list: %r", pr_sinks)
            pr_sinks = []

        # ------------------------------------------------------------------
        # 2. Fetch taint paths for the PR branch
        # ------------------------------------------------------------------
        log.info("Fetching taint paths for PR branch")
        pr_taint_paths: list[dict] = call_tool(
            mcp_client, sse_url, "find_taint_paths",
            {"repo_name": f"{repo}@{branch}"},
        )
        if not isinstance(pr_taint_paths, list):
            log.warning("find_taint_paths returned non-list: %r", pr_taint_paths)
            pr_taint_paths = []

        # ------------------------------------------------------------------
        # 3. Fetch taint paths for master baseline (to compute delta)
        # ------------------------------------------------------------------
        log.info("Fetching taint paths for master baseline")
        baseline_taint_paths: list[dict] = call_tool(
            mcp_client, sse_url, "find_taint_paths",
            {"repo_name": f"{repo}@{base_branch}"},
        )
        if not isinstance(baseline_taint_paths, list):
            baseline_taint_paths = []

        baseline_path_keys = {
            (p.get("source_method_fqn", ""), p.get("sink_method_fqn", ""))
            for p in baseline_taint_paths
        }
        new_taint_paths = [
            p for p in pr_taint_paths
            if (p.get("source_method_fqn", ""), p.get("sink_method_fqn", ""))
            not in baseline_path_keys
        ]

        # ------------------------------------------------------------------
        # 4. Cross-service taint (PR branch only — no baseline subtraction;
        #    cross-service taint is repo-wide, not branch-scoped)
        # ------------------------------------------------------------------
        log.info("Fetching cross-service taint for PR branch")
        cross_service: list[dict] = call_tool(
            mcp_client, sse_url, "find_cross_service_taint",
            {"repo_name": f"{repo}@{branch}"},
        )
        if not isinstance(cross_service, list):
            cross_service = []

        # ------------------------------------------------------------------
        # 5. Compute delta
        # ------------------------------------------------------------------
        new_sinks = compute_new_sinks(baseline_sinks, pr_sinks)
        log.info(
            "Delta: %d new sinks, %d new taint paths, %d cross-service findings",
            len(new_sinks), len(new_taint_paths), len(cross_service),
        )

        # ------------------------------------------------------------------
        # 6. Get PR diff positions for inline comments
        # ------------------------------------------------------------------
        if not dry_run:
            log.info("Fetching PR diff for inline comment positions")
            diff_positions = get_pr_diff_positions(github_client, repo, pr_number)
        else:
            diff_positions = {}

        # ------------------------------------------------------------------
        # 7. Partition new_sinks into in-diff (inline) vs out-of-diff (summary)
        # ------------------------------------------------------------------
        inline_sinks: list[dict] = []
        out_of_diff_sinks: list[dict] = []
        for sink in new_sinks:
            file_path = sink.get("file_path", "")
            line = sink.get("line_start")
            if file_path and line and (file_path, int(line)) in diff_positions:
                inline_sinks.append(sink)
            else:
                out_of_diff_sinks.append(sink)

        # Taint paths: in-diff inline, else summary
        inline_taint_paths: list[dict] = []
        out_of_diff_taint_paths: list[dict] = []
        for path in new_taint_paths:
            file_path = path.get("file_path", "")
            line = path.get("line_start")
            if file_path and line and (file_path, int(line)) in diff_positions:
                inline_taint_paths.append(path)
            else:
                out_of_diff_taint_paths.append(path)

        # ------------------------------------------------------------------
        # 8. Build and post / print comments
        # ------------------------------------------------------------------
        review_comments: list[dict] = []

        for sink in inline_sinks:
            file_path = sink["file_path"]
            line = int(sink["line_start"])
            position = diff_positions[(file_path, line)]
            review_comments.append({
                "path": file_path,
                "position": position,
                "body": format_inline_comment(sink),
            })

        for path in inline_taint_paths:
            file_path = path.get("file_path", "")
            line = path.get("line_start")
            if file_path and line:
                position = diff_positions.get((file_path, int(line)))
                if position:
                    review_comments.append({
                        "path": file_path,
                        "position": position,
                        "body": format_taint_path_comment(path),
                    })

        has_findings = bool(new_sinks or new_taint_paths or cross_service)
        summary_body = format_summary_comment(
            out_of_diff_sinks, out_of_diff_taint_paths, cross_service
        )

        if dry_run:
            print("=== DRY RUN — no comments will be posted ===\n")
            print(f"PR #{pr_number} | branch: {branch} | base: {base_branch}")
            print(f"New sinks: {len(new_sinks)} | New taint paths: {len(new_taint_paths)} | Cross-service: {len(cross_service)}")
            print(f"\n--- Summary comment ---\n{summary_body}\n")
            if review_comments:
                print(f"--- {len(review_comments)} inline comment(s) ---")
                for c in review_comments:
                    print(f"\n  {c['path']} @ pos {c['position']}:\n  {c['body'][:200]}...")
            return

        # Post inline review comments + summary via PR review API
        if review_comments or has_findings:
            review_event = "COMMENT"
            review_body = summary_body if (out_of_diff_sinks or out_of_diff_taint_paths or cross_service or not new_sinks) else ""

            review_payload: dict[str, Any] = {
                "event": review_event,
                "comments": review_comments,
            }
            if review_body:
                review_payload["body"] = review_body

            log.info(
                "Posting PR review with %d inline comment(s) to PR #%d",
                len(review_comments), pr_number,
            )
            gh_post(github_client, f"/repos/{repo}/pulls/{pr_number}/reviews", review_payload)

        elif not has_findings:
            # Post a clean summary comment if no findings at all
            log.info("No new findings — posting clean summary to PR #%d", pr_number)
            gh_post(
                github_client,
                f"/repos/{repo}/issues/{pr_number}/comments",
                {"body": summary_body},
            )

        log.info("Review posted successfully for PR #%d", pr_number)

    finally:
        mcp_client.close()
        github_client.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Post Orihime security findings as GitHub PR comments."
    )
    parser.add_argument("--pr", required=True, type=int, help="Pull request number")
    parser.add_argument("--repo", required=True, help="GitHub repository (owner/repo)")
    parser.add_argument("--sse-url", required=True, help="Orihime MCP SSE server base URL")
    parser.add_argument("--github-token", required=True, help="GitHub token for posting comments")
    parser.add_argument("--branch", required=True, help="PR head branch name")
    parser.add_argument("--base-branch", default="master", help="Base branch for baseline (default: master)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print comments without posting",
    )
    args = parser.parse_args()

    run_review(
        pr_number=args.pr,
        repo=args.repo,
        sse_url=args.sse_url,
        github_token=args.github_token,
        branch=args.branch,
        base_branch=args.base_branch,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
