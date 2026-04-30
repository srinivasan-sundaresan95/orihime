# python -m indra --repo /path --name my-repo [--db ~/.indra/indra.db]
# python -m indra serve
# python -m indra ui [--port 7700] [--db ~/.indra/indra.db]
# python -m indra resolve [--db ~/.indra/indra.db]
import argparse
import sys
from pathlib import Path

from indra.indexer import index_repo

_DEFAULT_DB_PATH = str(Path.home() / ".indra" / "indra.db")


def main() -> None:
    parser = argparse.ArgumentParser(description="Indra code graph indexer / MCP server")
    subparsers = parser.add_subparsers(dest="command")

    # ---- index (default when no subcommand given for backwards compat) ----
    index_parser = subparsers.add_parser("index", help="Index a repository into the graph")
    index_parser.add_argument("--repo", required=True, help="Path to the repository root")
    index_parser.add_argument("--name", required=True, help="Logical name for the repository")
    index_parser.add_argument(
        "--db",
        default=_DEFAULT_DB_PATH,
        help="Path to the KuzuDB database file (default: ~/.indra/indra.db)",
    )
    index_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Re-parse every file even if git blob hash is unchanged (full re-index)",
    )

    # ---- serve ----
    subparsers.add_parser("serve", help="Start the Indra MCP server (stdio transport)")

    # ---- ui ----
    ui_parser = subparsers.add_parser("ui", help="Start the Indra web UI (browser)")
    ui_parser.add_argument(
        "--port",
        type=int,
        default=7700,
        help="TCP port to listen on (default: 7700)",
    )
    ui_parser.add_argument(
        "--db",
        default=None,
        help="Path to the KuzuDB database file (default: ~/.indra/indra.db)",
    )

    # ---- resolve ----
    resolve_parser = subparsers.add_parser(
        "resolve",
        help="Match RestCall URL patterns against Endpoints across all indexed repos",
    )
    resolve_parser.add_argument(
        "--db",
        default=_DEFAULT_DB_PATH,
        help="Path to the KuzuDB database file (default: ~/.indra/indra.db)",
    )

    # ---- legacy flat args: python -m indra --repo ... --name ... ----
    # Keep backwards compatibility: if the first arg starts with '--', treat
    # the whole invocation as an implicit 'index' command.
    args, remaining = parser.parse_known_args()

    if args.command == "serve":
        from indra.mcp_server import cli
        cli()
        return

    if args.command == "ui":
        from indra.ui_server import run_ui
        run_ui(port=args.port, db_path=args.db or _DEFAULT_DB_PATH)
        return

    if args.command == "resolve":
        import kuzu
        from indra.cross_resolver import run_cross_resolution, load_indexed_repos
        db = kuzu.Database(str(args.db))
        conn = kuzu.Connection(db)
        repos = load_indexed_repos(conn)
        print(f"Indexed repos: {repos}")
        result = run_cross_resolution(conn)
        print(f"  matched:          {result['matched']}")
        print(f"  unresolved:       {result['unresolved']}")
        print(f"  depends_on_edges: {result['depends_on_edges']}")
        return

    if args.command == "index":
        summary = index_repo(args.repo, args.name, args.db, force=args.force)
        skipped = summary.pop("files_skipped", 0)
        for k, v in summary.items():
            print(f"  {k}: {v}")
        if skipped:
            print(f"  files_skipped (unchanged): {skipped}")
        return

    # Legacy mode: re-parse with flat args
    legacy_parser = argparse.ArgumentParser(description="Indra code graph indexer")
    legacy_parser.add_argument("--repo", required=True, help="Path to the repository root")
    legacy_parser.add_argument("--name", required=True, help="Logical name for the repository")
    legacy_parser.add_argument(
        "--db",
        default=str(Path.home() / ".indra" / "indra.db"),
        help="Path to the KuzuDB database file (default: ~/.indra/indra.db)",
    )
    legacy_args = legacy_parser.parse_args()
    summary = index_repo(legacy_args.repo, legacy_args.name, legacy_args.db)
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
