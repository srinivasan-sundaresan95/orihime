---
name: orihime-change-impact
description: >
  Use when the user asks "what breaks if I change X", "blast radius of modifying method Y",
  "what tests do I need to run if I change class Z", "impact analysis for refactoring X",
  or wants to understand the downstream consequences of a code change before making it.
  Uses Orihime MCP tools only — no source file reads unless the user explicitly asks to
  drill into the source after the graph analysis.
---

# Orihime Change Impact Skill

## Trigger conditions

- "What breaks if I change [method/class/field]?"
- "Blast radius of modifying [symbol]"
- "What tests do I need to run if I change [class]?"
- "Impact analysis for refactoring [X]"
- "What depends on [method/interface]?"
- "If I rename [method], what else needs updating?"
- "Which callers will break if I change the signature of [method]?"

---

## Step 1 — Locate the target symbol

```
mcp__orihime__search_symbol(query="<target_name>")
```

Returns both class and method matches. Pick the one matching the user's description.
The `fqn` field is what you'll use in all subsequent calls.

Confirm exact file and line:
```
mcp__orihime__get_file_location(fqn="<fully.qualified.ClassName.methodName>")
```

---

## Step 2 — Direct callers (d=1, WILL BREAK for signature changes)

```
mcp__orihime__find_callers(method_fqn="<fqn>")
```

These call the method directly. If the signature changes (parameter type, return type),
every caller in this list **must be updated**.

---

## Step 3 — Transitive blast radius (d=1 to d=N)

```
mcp__orihime__blast_radius(method_fqn="<fqn>", max_depth=3)
```

BFS of reverse CALLS edges. Result has a `depth` field:
- `depth=1` — direct callers (WILL BREAK on signature change)
- `depth=2` — callers of callers (LIKELY AFFECTED by behavioral changes)
- `depth=3` — transitive (MAY NEED REGRESSION TESTING)

---

## Step 4 — Downstream implementors (for interface/abstract class changes)

If the target is an interface or abstract class:
```
mcp__orihime__find_implementations(interface_fqn="<fqn>")
```

If the interface contract changes, every implementor must be updated.
Then for each implementor, call `find_callers` on the concrete method to see who calls it.

---

## Step 5 — Superclass impact (for class inheritance changes)

```
mcp__orihime__find_superclasses(class_fqn="<fqn>")
```

If modifying a subclass, shows the inheritance chain up.
If modifying a base class, use `find_implementations` to find all subclasses.

---

## Step 6 — Find test files

From the `find_callers` and `blast_radius` results, identify test files:
- `file_path` contains: `test/`, `Test`, `Spec`, `Mock`, `Stub`
- FQN class name ends in: `Test`, `Spec`, `IT`

These are the test suites to run. Present as a runnable list.

---

## Step 7 — Optional source drill-down

After the graph analysis, if the user wants to understand **why** a caller is affected
or verify the exact call site, use `get_file_location` to get the file path and line,
then read only those specific files:

```
mcp__orihime__get_file_location(fqn="<caller_fqn>")
```

Then: `Read(file_path=<file_path>, offset=<line_start - 5>, limit=30)`

This is the recommended 2-step pattern:
1. Graph analysis to identify WHAT is affected (5–7 tool calls, <1s)
2. Targeted source reads on only the specific files/lines that matter (~5 files vs 27)

---

## Presenting findings

```
## Change Impact Analysis — [target_name]

### Symbol Located
- `FullyQualified.methodName` @ path/to/File.kt:87

### Direct Callers (WILL BREAK on signature change)
- CallerA.methodX @ ServiceA.kt:201
- CallerB.handleRequest @ ControllerB.kt:45

### Transitive Impact
- d=2 (LIKELY AFFECTED): UpperServiceC.process @ ServiceC.kt:112
- d=3 (REGRESSION RISK): OrchestrationD.execute @ Orchestrator.kt:67

### Implementors (if interface)
- ConcreteImplA @ impl/ConcreteA.kt:33
- ConcreteImplB @ impl/ConcreteB.kt:78

### Test Files to Run
- ServiceATest.kt
- ControllerBTest.kt

### Total blast radius: N methods across M files
```

---

## Gotchas

### blast_radius max_depth
Default `max_depth=3` is right for most changes. For wide-impact changes (core utilities,
base classes), use `max_depth=5`. Never go beyond 10 — results become noise.

### Interface changes require find_implementations
`blast_radius` only follows CALLS edges — it will miss implementors.
For any interface or abstract class target, always run `find_implementations` too.

### search_symbol returns both classes AND methods
Always prefer the `"method"` type result's FQN for `find_callers`/`blast_radius`.
If the user asks about a class-level change (rename, move), use the `"class"` type FQN
for `find_implementations` and `find_superclasses`.

### The 2-step pattern is the right workflow
Graph first (Orihime tools) → targeted source reads (Read tool) for specific files.
This reduces token usage 80–95% vs reading source files first.
The graph gives you the exact file paths and line numbers — use them.

### Do NOT read source files proactively
Only read source files in Step 7 and only when the user explicitly needs to see the code.
