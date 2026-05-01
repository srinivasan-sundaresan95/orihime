# Orihime — Codex Agent Setup Guide

## Setup

When the user asks to set up, install, or configure Orihime:

1. Run `pip install -e .`
2. Run `python -m orihime install-skills --agent codex` (appends skills to ~/AGENTS.md)
3. Ask which repositories to index, then run:
   `python -m orihime index --repo <path> --name <name>` for each
4. Run `python -m orihime resolve`
5. Tell the user the starting prompts (see below)

## Available MCP tools

Use `mcp__orihime__*` tools when the user asks about code structure, call flows,
security, performance, or wants to write code against an indexed repository.

Tools: `list_repos`, `find_callers`, `find_callees`, `blast_radius`, `search_symbol`,
`get_file_location`, `list_endpoints`, `find_implementations`, `find_superclasses`,
`find_taint_sinks`, `find_reachable_sinks`, `find_taint_flows`, `find_complexity_hints`,
`find_io_fanout`, `find_hotspots`, `estimate_capacity`, `find_cascade_risk`,
`ingest_perf_results`, `find_license_violations`, `index_repo_tool`

## When to use the graph (always before reading source files)

- Call flow / who calls what → `find_callers`, `find_callees`, `blast_radius`
- Find a class or method → `search_symbol`, `get_file_location`
- Security audit → `find_reachable_sinks`, `find_taint_flows`, `generate_security_report`
- Performance → `find_complexity_hints`, `find_io_fanout`, `find_hotspots`, `estimate_capacity`
- Change impact → `blast_radius`, `find_implementations`
- Writing new code → `search_symbol` first (check for duplication), then `find_superclasses`
- Design review → coupling via `find_callers`/`find_callees`, then `find_complexity_hints`
- Gatling/JMeter report → `ingest_perf_results` first, then `find_hotspots`, `estimate_capacity`

## Starting prompts for users

After setup, suggest:
- "Trace the call flow for GET /api/orders in order-service"
- "Run a security audit on payment-service"
- "Here's my Gatling simulation.log — find hotspots in order-service"
- "What breaks if I change OrderService.processPayment?"
- "Add a refund method to OrderService — check the class structure first"
- "Review the design of OrderController for pattern violations"
