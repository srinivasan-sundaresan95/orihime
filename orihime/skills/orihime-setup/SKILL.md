---
name: orihime-setup
description: >
  Use when the user asks to "set up Orihime", "install Orihime", "configure Orihime",
  "index a new repository", "add a repo to Orihime", or "what can Orihime do".
  Runs the full installation flow: dependencies, MCP registration, skill installation,
  repository indexing, and presents starting prompts. Also handles adding new repos
  to an already-installed Orihime.
---

# Orihime Setup Skill

## Trigger conditions

- "Set up Orihime" / "Install Orihime" / "Configure Orihime"
- "I want to use Orihime"
- "Index a new repository" / "Add a repo to Orihime"
- "What can Orihime do?"
- First time opening a project after cloning Orihime

---

## Step 0 — Detect existing installation

```
mcp__orihime__list_repos()
```

- If this succeeds and returns repos → Orihime is already installed. Skip to Step 4 (index new repos) or Step 6 (show capabilities).
- If this fails or returns empty → fresh install, run all steps.

---

## Step 1 — Install dependencies

```bash
pip install -e .
```

Run from the orihime directory. If the orihime directory is not the current working directory, ask the user for the path.

Confirm it completes without error before continuing.

---

## Step 2 — Register MCP server

```bash
python -m orihime register
```

Then ask: **"Which AI assistant(s) are you using? Claude Code / Cursor / Copilot / Codex / all"**

Run the matching install-skills command:
```bash
python -m orihime install-skills --agent claude     # Claude Code
python -m orihime install-skills --agent cursor     # Cursor
python -m orihime install-skills --agent codex      # Codex
python -m orihime install-skills --agent copilot --repo <path>   # Copilot (needs repo)
python -m orihime install-skills --agent all        # All except copilot
```

Tell the user: **"Please restart your AI assistant now, then come back and tell me which repositories to index."**

---

## Step 3 — Index repositories

Ask: **"Which repositories would you like to index? Please provide the full paths."**

For each path provided:
```bash
python -m orihime index --repo <path> --name <short-name>
```

Derive `short-name` from the last directory component of the path.
Example: `/home/user/projects/order-service` → `order-service`

After all repos are indexed:
```bash
python -m orihime resolve
```

This links cross-service REST calls between repos.

---

## Step 4 — Adding a new repo to an existing installation

If Orihime is already installed (Step 0 detected existing repos):

```bash
python -m orihime index --repo <new_path> --name <name>
python -m orihime resolve
```

Then verify with:
```
mcp__orihime__list_repos()
```

---

## Step 5 — Verify the index

Run one repo incrementally to confirm the graph is queryable:
```
mcp__orihime__list_repos()
mcp__orihime__list_endpoints(repo_name="<first_indexed_repo>")
```

If `list_endpoints` returns results, the index is healthy.

---

## Step 6 — Present capabilities and starting prompts

Tell the user:

---
**Orihime is ready.** Indexed repos: [list from list_repos].

**Here's what you can ask me:**

**Trace call flows** (no source file reads needed)
- "Trace the call flow for GET /api/orders in [repo]"
- "Who calls [MethodName]?"
- "Show me the call chain from the controller to the database for [endpoint]"

**Security analysis**
- "Run a security audit on [repo]"
- "Find SQL injection risks"
- "Generate an OWASP Top 10 report for [repo]"
- "Check license compliance for [repo]"

**Performance**
- "Find performance hotspots in [repo]"
- "Which endpoints are approaching saturation?"
- "Here's my Gatling simulation.log — analyze it" ← provide the file path

**Change impact**
- "What breaks if I change [MethodName]?"
- "What's the blast radius of modifying [ClassName]?"
- "What tests do I need to run if I change [class]?"

**Code assistance** (checks existing structure before writing)
- "Add a [feature] to [class]"
- "What interface should I implement for [purpose]?"
- "Does a method for [concept] already exist?"

**Design review**
- "Review the design of [class]"
- "Is [class] SOLID?"
- "Review my PR for design pattern issues"
- "Should I split [class] up?"

---
