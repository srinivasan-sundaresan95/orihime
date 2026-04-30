"""Cross-repository resolver.

Matches RestCall.url_pattern against Endpoint.path_regex across all indexed
repos, creates CALLS_REST edges, and derives DEPENDS_ON repo-level edges.

Public API
----------
run_cross_resolution(conn) -> dict
load_indexed_repos(conn) -> list[str]
"""
from __future__ import annotations

import re as _re
import sys

import kuzu

from indra.path_utils import compile_path_regex, match_url_pattern

_SCHEME_HOST_RE = _re.compile(r'^https?://[^/]+')


def _strip_scheme_host(url: str) -> str:
    """Remove scheme and host from a URL, returning just the path (and query).

    Examples
    --------
    >>> _strip_scheme_host("http://svc/wallet/balance")
    '/wallet/balance'
    >>> _strip_scheme_host("https://internal-svc.example.com/api/v1/items")
    '/api/v1/items'
    >>> _strip_scheme_host("/wallet/balance")
    '/wallet/balance'
    >>> _strip_scheme_host("http://svc")
    ''
    """
    return _SCHEME_HOST_RE.sub('', url)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def load_indexed_repos(conn: kuzu.Connection) -> list[str]:
    """Return list of repo names currently indexed."""
    result = conn.execute("MATCH (r:Repo) RETURN r.name")
    names: list[str] = []
    while result.has_next():
        names.append(result.get_next()[0])
    return names


# ---------------------------------------------------------------------------
# Main resolution
# ---------------------------------------------------------------------------

def run_cross_resolution(conn: kuzu.Connection) -> dict:
    """Match RestCall.url_pattern against Endpoint.path_regex across all repos.

    For every RestCall that matches an Endpoint:
      - Deletes the existing UNRESOLVED_CALL edge (if any)
      - Creates a CALLS_REST edge from the caller Method to the Endpoint
      - Creates/merges a DEPENDS_ON edge between the caller Repo and
        callee Repo (only when they differ)

    Returns
    -------
    dict with keys:
        "matched"          -- number of RestCalls that matched at least one Endpoint
        "unresolved"       -- number of RestCalls with no match
        "depends_on_edges" -- number of cross-repo DEPENDS_ON edges created/merged
    """

    # ------------------------------------------------------------------
    # 1. Load all Endpoint nodes
    # ------------------------------------------------------------------
    ep_result = conn.execute(
        "MATCH (e:Endpoint) RETURN e.id, e.http_method, e.path, e.path_regex, e.repo_id"
    )
    endpoints: list[dict] = []
    while ep_result.has_next():
        row = ep_result.get_next()
        eid, http_method, path, path_regex, repo_id = row
        # 2. Compute path_regex on the fly if empty
        if not path_regex:
            path_regex = compile_path_regex(path)
        endpoints.append(
            {
                "id": eid,
                "http_method": (http_method or "").upper(),
                "path": path,
                "path_regex": path_regex,
                "repo_id": repo_id,
            }
        )

    # ------------------------------------------------------------------
    # 3. Load all RestCall nodes
    # ------------------------------------------------------------------
    rc_result = conn.execute(
        "MATCH (rc:RestCall) RETURN rc.id, rc.http_method, rc.url_pattern, rc.caller_method_id, rc.repo_id"
    )
    rest_calls: list[dict] = []
    while rc_result.has_next():
        row = rc_result.get_next()
        rcid, http_method, url_pattern, caller_method_id, repo_id = row
        rest_calls.append(
            {
                "id": rcid,
                "http_method": (http_method or "").upper(),
                "url_pattern": url_pattern or "",
                "caller_method_id": caller_method_id or "",
                "repo_id": repo_id,
            }
        )

    # ------------------------------------------------------------------
    # 4. Match each RestCall against every Endpoint
    # ------------------------------------------------------------------
    matched = 0
    unresolved = 0
    # Track DEPENDS_ON pairs we've already MERGEd to avoid redundant Cypher calls
    depends_on_seen: set[tuple[str, str]] = set()
    depends_on_count = 0

    for rc in rest_calls:
        # Skip dynamic / unresolvable calls
        if rc["url_pattern"] == "DYNAMIC":
            unresolved += 1
            continue

        caller_mid = rc["caller_method_id"]
        if not caller_mid:
            unresolved += 1
            continue

        # Strip scheme+host from the url_pattern so that full URLs like
        # "http://svc/wallet/balance" match anchored regexes like
        # ^/wallet/balance$.  Do NOT mutate rc["url_pattern"].
        url_to_match = _strip_scheme_host(rc["url_pattern"])
        if not url_to_match:
            unresolved += 1
            continue

        found_match = False
        for ep in endpoints:
            # HTTP method must match (case-insensitive); both sides already uppercased
            if rc["http_method"] and ep["http_method"]:
                if rc["http_method"] != ep["http_method"]:
                    continue

            # URL pattern must match endpoint path regex
            if not match_url_pattern(url_to_match, ep["path_regex"]):
                continue

            found_match = True

            # -- Delete existing UNRESOLVED_CALL edge (if any) --
            conn.execute(
                "MATCH (m:Method {id: $mid})-[r:UNRESOLVED_CALL]->(rc:RestCall {id: $rcid}) DELETE r",
                {"mid": caller_mid, "rcid": rc["id"]},
            )

            # -- Create CALLS_REST edge --
            conn.execute(
                "MATCH (m:Method {id: $mid}), (e:Endpoint {id: $eid}) CREATE (m)-[:CALLS_REST]->(e)",
                {"mid": caller_mid, "eid": ep["id"]},
            )

            # -- 5. Derive DEPENDS_ON if cross-repo --
            caller_repo = rc["repo_id"]
            callee_repo = ep["repo_id"]
            if caller_repo and callee_repo and caller_repo != callee_repo:
                pair = (caller_repo, callee_repo)
                if pair not in depends_on_seen:
                    conn.execute(
                        "MATCH (r1:Repo {id: $r1}), (r2:Repo {id: $r2}) MERGE (r1)-[:DEPENDS_ON]->(r2)",
                        {"r1": caller_repo, "r2": callee_repo},
                    )
                    depends_on_seen.add(pair)
                    depends_on_count += 1

        if found_match:
            matched += 1
        else:
            # 6. Log unresolved to stderr
            print(
                f"[cross_resolver] UNRESOLVED: RestCall id={rc['id']} "
                f"method={rc['http_method']} url={rc['url_pattern']} "
                f"caller_method={caller_mid}",
                file=sys.stderr,
            )
            unresolved += 1

    return {
        "matched": matched,
        "unresolved": unresolved,
        "depends_on_edges": depends_on_count,
    }
