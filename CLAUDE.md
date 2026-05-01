# Orihime — Claude Code Setup Guide

This file is automatically loaded by Claude Code when you open this directory.

## If the user asks to set up, install, or configure Orihime

Run the setup procedure below. Do not ask for confirmation on each step — complete the whole flow and only pause where indicated.

### Setup procedure

**Step 1 — Install dependencies**
```bash
pip install -e .
```
Confirm it completes without error. If it fails, diagnose and fix before continuing.

**Step 2 — Register the MCP server**
```bash
python -m orihime register
```

**Step 3 — Install skills for the user's AI assistant(s)**

Ask: "Which AI assistant(s) are you using? (Claude Code / Cursor / Copilot / Codex / all)"

Then run the matching command:
- Claude Code: `python -m orihime install-skills --agent claude`
- Cursor: `python -m orihime install-skills --agent cursor`
- Copilot: `python -m orihime install-skills --agent copilot --repo <path>`  ← ask for repo path
- Codex: `python -m orihime install-skills --agent codex`
- All: `python -m orihime install-skills --agent all`

**Step 4 — Index repositories**

Ask: "Which repositories would you like to index first? Please provide the paths."

For each path the user provides, run:
```bash
python -m orihime index --repo <path> --name <short-name>
```
Use the last directory component as the short name (e.g. `/home/user/projects/order-service` → `order-service`).

After all repos are indexed, run `python -m orihime resolve` to link cross-service REST calls.

**Step 5 — Verify**
```bash
python -m orihime index --repo <first_repo_path> --name <name>
```
(Re-run one repo to confirm incremental mode works — should be faster than cold index.)

**Step 6 — Tell the user what's available**

After setup completes, tell the user:

---
**Orihime is ready.** Here's what you can ask:

**Call flow tracing**
- "Trace the call flow for GET /api/orders"
- "Who calls OrderService.processPayment?"
- "Show me the full call chain from the controller to the database"

**Security analysis**
- "Run a security audit on order-service"
- "Find SQL injection risks in payment-service"
- "Any OWASP Top 10 issues in my codebase?"

**Performance analysis**
- "Find performance hotspots in order-service"
- "Which endpoints are approaching saturation?"
- "Here's my Gatling simulation.log — analyze it against the call graph"

**Change impact**
- "What breaks if I change OrderService.calculateTotal?"
- "Blast radius of modifying the PaymentRepository"

**Code assistance**
- "Add a refund method to the OrderService — check the existing structure first"
- "What interface should I implement to add a new payment provider?"

**Design review**
- "Review the design of OrderController"
- "Is OrderService SOLID?"
- "Review my PR for design pattern issues"
---

## Orihime MCP tools available in this session

Once the MCP server is registered and Claude Code is restarted, the following tools are available inline:
`list_repos`, `find_callers`, `find_callees`, `blast_radius`, `search_symbol`, `get_file_location`, `list_endpoints`, `find_implementations`, `find_superclasses`, `find_taint_sinks`, `find_reachable_sinks`, `find_taint_flows`, `find_cross_service_taint`, `find_second_order_injection`, `generate_security_report`, `find_entry_points`, `find_complexity_hints`, `find_io_fanout`, `find_hotspots`, `estimate_capacity`, `find_cascade_risk`, `ingest_perf_results`, `find_license_violations`, `index_repo_tool`
