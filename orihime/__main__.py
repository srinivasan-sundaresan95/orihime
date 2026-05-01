# python -m orihime --repo /path --name my-repo [--db ~/.orihime/orihime.db]
# python -m orihime serve
# python -m orihime ui [--port 7700] [--db ~/.orihime/orihime.db]
# python -m orihime resolve [--db ~/.orihime/orihime.db]
# python -m orihime install-skills
# python -m orihime register
import argparse
import json
import shutil
import sys
from pathlib import Path

from orihime.indexer import index_repo

_DEFAULT_DB_PATH = str(Path.home() / ".orihime" / "orihime.db")


def _install_skills() -> None:
    """Copy bundled Claude Code skills into ~/.claude/skills/."""
    src_root = Path(__file__).parent / "skills"
    dst_root = Path.home() / ".claude" / "skills"

    if not src_root.exists():
        print("ERROR: skills directory not found in the Orihime package.")
        sys.exit(1)

    dst_root.mkdir(parents=True, exist_ok=True)
    installed = []
    for skill_dir in sorted(src_root.iterdir()):
        if not skill_dir.is_dir():
            continue
        dst = dst_root / skill_dir.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(skill_dir, dst)
        installed.append(skill_dir.name)

    print(f"Installed {len(installed)} skill(s) to {dst_root}:")
    for name in installed:
        print(f"  /{name}")
    print("\nRestart Claude Code to activate.")


def _register_mcp(db_path: str, python: str) -> None:
    """Add or update the orihime MCP server entry in ~/.claude/settings.json."""
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            print(f"WARNING: could not parse {settings_path} — will overwrite mcpServers only.")

    orihime_root = str(Path(__file__).parent.parent.resolve())
    settings.setdefault("mcpServers", {})["orihime"] = {
        "type": "stdio",
        "command": python,
        "args": ["-m", "orihime", "serve"],
        "cwd": orihime_root,
        "env": {"ORIHIME_DB_PATH": db_path},
    }

    settings_path.write_text(json.dumps(settings, indent=2))
    print(f"Registered Orihime MCP server in {settings_path}")
    print(f"  python:  {python}")
    print(f"  cwd:     {orihime_root}")
    print(f"  db:      {db_path}")
    print("\nRestart Claude Code to connect.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Orihime code graph indexer / MCP server")
    subparsers = parser.add_subparsers(dest="command")

    # ---- index (default when no subcommand given for backwards compat) ----
    index_parser = subparsers.add_parser("index", help="Index a repository into the graph")
    index_parser.add_argument("--repo", required=True, help="Path to the repository root")
    index_parser.add_argument("--name", required=True, help="Logical name for the repository")
    index_parser.add_argument(
        "--db",
        default=_DEFAULT_DB_PATH,
        help="Path to the KuzuDB database file (default: ~/.orihime/orihime.db)",
    )
    index_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Re-parse every file even if git blob hash is unchanged (full re-index)",
    )
    index_parser.add_argument(
        "--branch",
        default="master",
        help="Branch name to tag indexed files with (default: master)",
    )

    # ---- serve ----
    subparsers.add_parser("serve", help="Start the Orihime MCP server (stdio transport)")

    # ---- ui ----
    ui_parser = subparsers.add_parser("ui", help="Start the Orihime web UI (browser)")
    ui_parser.add_argument(
        "--port",
        type=int,
        default=7700,
        help="TCP port to listen on (default: 7700)",
    )
    ui_parser.add_argument(
        "--db",
        default=None,
        help="Path to the KuzuDB database file (default: ~/.orihime/orihime.db)",
    )

    # ---- resolve ----
    resolve_parser = subparsers.add_parser(
        "resolve",
        help="Match RestCall URL patterns against Endpoints across all indexed repos",
    )
    resolve_parser.add_argument(
        "--db",
        default=_DEFAULT_DB_PATH,
        help="Path to the KuzuDB database file (default: ~/.orihime/orihime.db)",
    )

    # ---- install-skills ----
    subparsers.add_parser(
        "install-skills",
        help="Install Orihime Claude Code skills into ~/.claude/skills/",
    )

    # ---- register ----
    register_parser = subparsers.add_parser(
        "register",
        help="Register the Orihime MCP server in ~/.claude/settings.json",
    )
    register_parser.add_argument(
        "--db",
        default=_DEFAULT_DB_PATH,
        help="Path to the KuzuDB database file (default: ~/.orihime/orihime.db)",
    )
    register_parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter to use (default: current interpreter)",
    )

    # ---- write-server ----
    ws_parser = subparsers.add_parser(
        "write-server",
        help="Start the write-serialization server (server mode only)",
    )
    ws_parser.add_argument("--port", type=int, default=7701)
    ws_parser.add_argument(
        "--db",
        default=_DEFAULT_DB_PATH,
        help="Path to the KuzuDB database file (default: ~/.orihime/orihime.db)",
    )

    # ---- legacy flat args: python -m orihime --repo ... --name ... ----
    # Keep backwards compatibility: if the first arg starts with '--', treat
    # the whole invocation as an implicit 'index' command.
    args, remaining = parser.parse_known_args()

    if args.command == "serve":
        from orihime.mcp_server import cli
        cli()
        return

    if args.command == "ui":
        from orihime.ui_server import run_ui
        run_ui(port=args.port, db_path=args.db or _DEFAULT_DB_PATH)
        return

    if args.command == "resolve":
        import kuzu
        from orihime.cross_resolver import run_cross_resolution, load_indexed_repos
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
        summary = index_repo(args.repo, args.name, args.db, force=args.force, branch=args.branch)
        skipped = summary.pop("files_skipped", 0)
        for k, v in summary.items():
            print(f"  {k}: {v}")
        if skipped:
            print(f"  files_skipped (unchanged): {skipped}")
        return

    if args.command == "install-skills":
        _install_skills()
        return

    if args.command == "register":
        _register_mcp(args.db, args.python)
        return

    if args.command == "write-server":
        import uvicorn
        import os as _os
        from orihime.write_server import app
        _os.environ.setdefault("ORIHIME_DB_PATH", args.db)
        print(f"\n  Orihime Write Server  ->  http://localhost:{args.port}\n  Press Ctrl+C to stop.\n")
        uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")
        return

    # Legacy mode: re-parse with flat args
    legacy_parser = argparse.ArgumentParser(description="Orihime code graph indexer")
    legacy_parser.add_argument("--repo", required=True, help="Path to the repository root")
    legacy_parser.add_argument("--name", required=True, help="Logical name for the repository")
    legacy_parser.add_argument(
        "--db",
        default=str(Path.home() / ".orihime" / "orihime.db"),
        help="Path to the KuzuDB database file (default: ~/.orihime/orihime.db)",
    )
    legacy_args = legacy_parser.parse_args()
    summary = index_repo(legacy_args.repo, legacy_args.name, legacy_args.db)
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
