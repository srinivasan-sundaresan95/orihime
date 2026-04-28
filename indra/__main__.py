# python -m indra --repo /path --name my-repo [--db ~/.indra/indra.db]
# python -m indra serve
import argparse
import sys
from pathlib import Path

from indra.indexer import index_repo


def main() -> None:
    parser = argparse.ArgumentParser(description="Indra code graph indexer / MCP server")
    subparsers = parser.add_subparsers(dest="command")

    # ---- index (default when no subcommand given for backwards compat) ----
    index_parser = subparsers.add_parser("index", help="Index a repository into the graph")
    index_parser.add_argument("--repo", required=True, help="Path to the repository root")
    index_parser.add_argument("--name", required=True, help="Logical name for the repository")
    index_parser.add_argument(
        "--db",
        default=str(Path.home() / ".indra" / "indra.db"),
        help="Path to the KuzuDB database file (default: ~/.indra/indra.db)",
    )

    # ---- serve ----
    subparsers.add_parser("serve", help="Start the Indra MCP server (stdio transport)")

    # ---- legacy flat args: python -m indra --repo ... --name ... ----
    # Keep backwards compatibility: if the first arg starts with '--', treat
    # the whole invocation as an implicit 'index' command.
    args, remaining = parser.parse_known_args()

    if args.command == "serve":
        from indra.mcp_server import cli
        cli()
        return

    if args.command == "index":
        summary = index_repo(args.repo, args.name, args.db)
        for k, v in summary.items():
            print(f"  {k}: {v}")
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
