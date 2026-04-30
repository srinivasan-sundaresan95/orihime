"""End-to-end integration test for cross-repo CALLS_REST resolution (P3-3.2).

Two temporary repos are created with inline Java source:
  - service-b: exposes GET /wallet/balance via WalletController
  - service-a: calls /wallet/balance via RestTemplate.getForObject

After indexing both repos into a shared KuzuDB, run_cross_resolution() is
called and the resulting edges are verified.

Mark: @pytest.mark.integration (deselect with -m "not integration")
"""
from __future__ import annotations

import os
import tempfile
import textwrap

import kuzu
import pytest

from indra.indexer import index_repo
from indra.cross_resolver import run_cross_resolution


# ---------------------------------------------------------------------------
# Inline Java source for the two mini-repos
# ---------------------------------------------------------------------------

# service-b: exposes GET /wallet/balance
_WALLET_CONTROLLER_JAVA = textwrap.dedent("""\
    package com.example.serviceb;

    import org.springframework.web.bind.annotation.GetMapping;
    import org.springframework.web.bind.annotation.RequestMapping;
    import org.springframework.web.bind.annotation.RestController;

    @RestController
    @RequestMapping("/wallet")
    public class WalletController {

        @GetMapping("/balance")
        public String getBalance() {
            return "100";
        }
    }
""")

# service-a: calls /wallet/balance via RestTemplate
# Note: url_pattern must be path-only (/wallet/balance) so it matches the
# path_regex compiled from /wallet/balance.  A full URL like
# "http://wallet-svc/wallet/balance" would NOT match the regex ^/wallet/balance$
# because re.match anchors at the start of the string.
_WALLET_CLIENT_JAVA = textwrap.dedent("""\
    package com.example.servicea;

    import org.springframework.web.client.RestTemplate;
    import org.springframework.stereotype.Component;

    @Component
    public class WalletClient {

        private final RestTemplate restTemplate = new RestTemplate();

        public String getBalance() {
            return restTemplate.getForObject("/wallet/balance", String.class);
        }
    }
""")

# service-a-fullurl: same as service-a but uses a full URL (scheme + host + path)
# to exercise the _strip_scheme_host fix in run_cross_resolution.
_WALLET_CLIENT_FULLURL_JAVA = textwrap.dedent("""\
    package com.example.serviceafullurl;

    import org.springframework.web.client.RestTemplate;
    import org.springframework.stereotype.Component;

    @Component
    public class WalletClientFullUrl {

        private final RestTemplate restTemplate = new RestTemplate();

        public String getBalance() {
            return restTemplate.getForObject("http://wallet-svc/wallet/balance", String.class);
        }
    }
""")


# ---------------------------------------------------------------------------
# Module-scoped fixture: create two temp repos, index both, run resolution
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cross_e2e():
    """Index service-a and service-b into a shared DB, run cross resolution."""
    with tempfile.TemporaryDirectory() as repo_a_dir:
        with tempfile.TemporaryDirectory() as repo_b_dir:
            with tempfile.TemporaryDirectory() as db_dir:

                # Write inline Java source files
                with open(os.path.join(repo_b_dir, "WalletController.java"), "w") as f:
                    f.write(_WALLET_CONTROLLER_JAVA)

                with open(os.path.join(repo_a_dir, "WalletClient.java"), "w") as f:
                    f.write(_WALLET_CLIENT_JAVA)

                db_path = os.path.join(db_dir, "cross_e2e.db")

                # Index service-b first (creates schema); max_workers=1 avoids
                # ProcessPoolExecutor overhead in tests
                stats_b = index_repo(
                    repo_path=repo_b_dir,
                    repo_name="service-b",
                    db_path=db_path,
                    max_workers=1,
                )
                print(f"\n[cross-e2e] service-b index stats: {stats_b}")

                # Index service-a into the same DB (schema already present)
                stats_a = index_repo(
                    repo_path=repo_a_dir,
                    repo_name="service-a",
                    db_path=db_path,
                    max_workers=1,
                )
                print(f"[cross-e2e] service-a index stats: {stats_a}")

                db = kuzu.Database(db_path)
                conn = kuzu.Connection(db)

                resolution_stats = run_cross_resolution(conn)
                print(f"[cross-e2e] resolution stats: {resolution_stats}")

                yield conn, resolution_stats, stats_a, stats_b

                # Release handles before temp dirs are cleaned up — KuzuDB
                # holds file locks that cause PermissionError on Windows/WSL2
                del conn, db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_cross_e2e_at_least_one_match(cross_e2e):
    """run_cross_resolution must find at least one matched RestCall."""
    _, stats, _, _ = cross_e2e
    assert stats["matched"] >= 1, (
        f"Expected matched >= 1, got matched={stats['matched']}; full stats={stats}"
    )


@pytest.mark.integration
def test_cross_e2e_calls_rest_edge_exists(cross_e2e):
    """A CALLS_REST edge must exist from the method in service-a to the endpoint in service-b."""
    conn, stats, _, _ = cross_e2e

    # KuzuDB does not support subquery expressions inside WHERE clauses.
    # Resolve repo IDs separately, then query edges with bound parameters.
    repo_a_result = conn.execute("MATCH (r:Repo {name: 'service-a'}) RETURN r.id")
    repo_a_id = repo_a_result.get_next()[0]

    repo_b_result = conn.execute("MATCH (r:Repo {name: 'service-b'}) RETURN r.id")
    repo_b_id = repo_b_result.get_next()[0]

    edge_result = conn.execute(
        "MATCH (m:Method)-[:CALLS_REST]->(e:Endpoint) "
        "WHERE m.repo_id = $ra AND e.repo_id = $rb "
        "RETURN count(*)",
        {"ra": repo_a_id, "rb": repo_b_id},
    )
    edge_count = edge_result.get_next()[0]

    assert edge_count >= 1, (
        f"Expected >= 1 CALLS_REST edge from service-a method to service-b endpoint; "
        f"got {edge_count}. resolution stats={stats}"
    )


@pytest.mark.integration
def test_cross_e2e_depends_on_edge_exists(cross_e2e):
    """A DEPENDS_ON edge must exist from the service-a Repo to the service-b Repo."""
    conn, stats, _, _ = cross_e2e

    depends_result = conn.execute(
        "MATCH (r1:Repo {name: 'service-a'})-[:DEPENDS_ON]->(r2:Repo {name: 'service-b'}) "
        "RETURN count(*)"
    )
    depends_count = depends_result.get_next()[0]

    assert depends_count >= 1, (
        f"Expected >= 1 DEPENDS_ON edge from service-a to service-b; "
        f"got {depends_count}. resolution stats={stats}"
    )


@pytest.mark.integration
def test_cross_e2e_depends_on_edges_in_stats(cross_e2e):
    """run_cross_resolution must report depends_on_edges >= 1."""
    _, stats, _, _ = cross_e2e
    assert stats["depends_on_edges"] >= 1, (
        f"Expected depends_on_edges >= 1, got {stats['depends_on_edges']}; full stats={stats}"
    )


@pytest.mark.integration
def test_cross_e2e_indexed_endpoint_in_service_b(cross_e2e):
    """service-b must have at least one indexed Endpoint node."""
    conn, _, _, stats_b = cross_e2e
    assert stats_b["endpoints"] >= 1, (
        f"Expected >= 1 endpoint indexed in service-b, got {stats_b['endpoints']}"
    )


@pytest.mark.integration
def test_cross_e2e_indexed_rest_call_in_service_a(cross_e2e):
    """service-a must have at least one indexed RestCall node."""
    conn, _, stats_a, _ = cross_e2e
    assert stats_a["rest_calls"] >= 1, (
        f"Expected >= 1 rest_call indexed in service-a, got {stats_a['rest_calls']}"
    )


# ---------------------------------------------------------------------------
# Full-URL caller fixture + test (P4-4)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cross_e2e_fullurl():
    """Index service-a-fullurl (uses full URL) and service-b into a shared DB,
    run cross resolution, and yield the connection + stats.

    This fixture verifies the _strip_scheme_host fix: a RestCall whose
    url_pattern is "http://wallet-svc/wallet/balance" must still match the
    Endpoint at "/wallet/balance" after the scheme+host prefix is stripped.
    """
    from indra.indexer import index_repo

    with tempfile.TemporaryDirectory() as repo_a_dir:
        with tempfile.TemporaryDirectory() as repo_b_dir:
            with tempfile.TemporaryDirectory() as db_dir:

                # Write inline Java source files
                with open(os.path.join(repo_b_dir, "WalletController.java"), "w") as f:
                    f.write(_WALLET_CONTROLLER_JAVA)

                with open(os.path.join(repo_a_dir, "WalletClientFullUrl.java"), "w") as f:
                    f.write(_WALLET_CLIENT_FULLURL_JAVA)

                db_path = os.path.join(db_dir, "cross_e2e_fullurl.db")

                stats_b = index_repo(
                    repo_path=repo_b_dir,
                    repo_name="service-b-fu",
                    db_path=db_path,
                    max_workers=1,
                )
                print(f"\n[cross-e2e-fullurl] service-b-fu index stats: {stats_b}")

                stats_a = index_repo(
                    repo_path=repo_a_dir,
                    repo_name="service-a-fullurl",
                    db_path=db_path,
                    max_workers=1,
                )
                print(f"[cross-e2e-fullurl] service-a-fullurl index stats: {stats_a}")

                db = kuzu.Database(db_path)
                conn = kuzu.Connection(db)

                resolution_stats = run_cross_resolution(conn)
                print(f"[cross-e2e-fullurl] resolution stats: {resolution_stats}")

                yield conn, resolution_stats, stats_a, stats_b

                del conn, db


@pytest.mark.integration
def test_cross_e2e_fullurl_creates_calls_rest_edge(cross_e2e_fullurl):
    """A caller that uses a full URL (http://wallet-svc/wallet/balance) must
    still produce a CALLS_REST edge after scheme+host stripping."""
    conn, stats, stats_a, _ = cross_e2e_fullurl

    repo_a_result = conn.execute(
        "MATCH (r:Repo {name: 'service-a-fullurl'}) RETURN r.id"
    )
    repo_a_id = repo_a_result.get_next()[0]

    repo_b_result = conn.execute(
        "MATCH (r:Repo {name: 'service-b-fu'}) RETURN r.id"
    )
    repo_b_id = repo_b_result.get_next()[0]

    edge_result = conn.execute(
        "MATCH (m:Method)-[:CALLS_REST]->(e:Endpoint) "
        "WHERE m.repo_id = $ra AND e.repo_id = $rb "
        "RETURN count(*)",
        {"ra": repo_a_id, "rb": repo_b_id},
    )
    edge_count = edge_result.get_next()[0]

    assert edge_count >= 1, (
        f"Expected >= 1 CALLS_REST edge from service-a-fullurl to service-b-fu "
        f"(full URL caller); got {edge_count}. "
        f"resolution stats={stats}, service-a stats={stats_a}"
    )
