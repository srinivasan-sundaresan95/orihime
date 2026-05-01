---
name: orihime-code-assist
description: >
  Use when the user asks to add a new method, class, or feature to an indexed codebase,
  wants to know "where should I add this?", "does a method for X already exist?",
  "what interface should I implement?", "what does this class extend?", "how is Y
  done elsewhere in the codebase?", or needs the existing class structure checked
  before writing new code. Queries Orihime FIRST, reads source only as needed.
---

# Orihime Code Assist Skill

## Trigger conditions

- "Add a method to [class]" / "Add a feature to [repo]"
- "Where should I put this code?"
- "Does a method for X already exist?"
- "What interface should I implement for Y?"
- "What does [class] extend / implement?"
- "How is [pattern/feature] done elsewhere in the codebase?"
- "What other classes are similar to [class]?"
- "Check the class structure before I write this"
- "Don't break the existing patterns"
- Any new code request against an indexed repository

---

## Why graph-first matters for coding

Writing code without checking the graph risks:
- Duplicating a method that already exists
- Implementing an interface that doesn't match what callers expect
- Breaking a class hierarchy (extending the wrong parent)
- Missing an existing utility that should be reused
- Adding a method to the wrong class

The graph answers all of these in <1 second. Source reads come after, and only for specific files.

---

## Step 1 — Check if the concept already exists

Before writing a single line of code:

```
mcp__orihime__search_symbol(query="<concept_keyword>")
```

Examples: `search_symbol("payment")`, `search_symbol("validator")`, `search_symbol("cache")`

Look at what comes back:
- If a method with the right name already exists → show the user and ask if they want to extend it or if this is a different concern
- If a class with the right name exists → use it as the base or check if the new method belongs there
- If nothing exists → proceed to design from scratch

---

## Step 2 — Understand the inheritance chain of the target class

If the user is adding to an existing class:

```
mcp__orihime__find_superclasses(class_fqn="<fully.qualified.ClassName>")
```

This shows what the class already inherits. Critical for:
- Knowing what methods are available from the parent
- Avoiding conflicts with overridden methods
- Understanding what the class is expected to be

If the class implements an interface:
```
mcp__orihime__find_implementations(interface_fqn="<interface.FQN>")
```
— to see all other implementations and understand the pattern.

---

## Step 3 — Find the right interface to implement

If the user is creating a new class that should plug into an existing framework:

```
mcp__orihime__search_symbol(query="<interface_concept>")
```

Filter results to `type: "class"` entries with `is_interface: true` in the FQN or name pattern.
Then:
```
mcp__orihime__find_implementations(interface_fqn="<interface.FQN>")
```

Read one existing implementation to understand the expected method signatures — this is the one targeted source read that's always worth it:
```
mcp__orihime__get_file_location(fqn="<existing_implementation_fqn>")
```
Then: `Read(file_path=..., offset=line_start-2, limit=60)`

---

## Step 4 — Check what callers expect from this class

If the user is modifying an existing class, the callers define the contract:

```
mcp__orihime__find_callers(method_fqn="<method_being_changed>")
```

Look at how callers use the method:
- What arguments do they pass?
- What do they do with the return value?
- Is there a pattern across all callers that the new code must maintain?

For the most important caller, get the file location and read just that call site:
```
mcp__orihime__get_file_location(fqn="<caller_fqn>")
```

---

## Step 5 — Find similar implementations as a pattern reference

When the user is adding something new, find analogous existing code:

```
mcp__orihime__search_symbol(query="<similar_concept>")
```

For example, if adding a new `@KafkaListener`, search for existing `@KafkaListener` handlers:
```
mcp__orihime__find_entry_points(repo_name="<repo>")
```
— filter for Kafka entry points to see the established pattern.

If adding a new REST endpoint, use:
```
mcp__orihime__list_endpoints(repo_name="<repo>")
```
— to see the naming conventions, path patterns, and which controller classes own them.

---

## Step 6 — Targeted source read (only the files that matter)

By now you know exactly which files to read. Read only:
1. The target class (where the new code goes)
2. One representative example of the pattern being followed
3. The interface being implemented (if applicable)

```
mcp__orihime__get_file_location(fqn="<target_class_fqn>")
```
Then: `Read(file_path=..., limit=200)`

Do NOT read files speculatively. The graph already told you what exists and where.

---

## Step 7 — Write the code

With the graph analysis complete, you now know:
- The exact class and file to add to
- The inheritance/interface chain to respect
- The naming patterns used elsewhere
- Whether the method already exists (and should be extended vs replaced vs left alone)
- The caller contract to maintain

Write code that is **consistent with the existing patterns** observed in Steps 1–6.

---

## Step 8 — Verify blast radius before finishing

After writing, confirm the change doesn't break callers:

```
mcp__orihime__find_callers(method_fqn="<modified_method_fqn>")
```

If the signature changed: list every caller and note that they need updating.
If behavior-only change: note d=2 callers from `blast_radius` as regression test candidates.

---

## Presenting the pre-coding analysis

Before writing code, always show:

```
## Code Analysis — [task description]

### Existing symbols related to this task
- [list from search_symbol — confirm no duplication]

### Target class structure
- [ClassName] extends [ParentClass] implements [Interface]
- Current methods relevant to this task: [list]

### Pattern from existing implementations
- [ExistingImpl.kt:42] — [brief description of the pattern]

### Files to read
1. [file_path:line] — target class
2. [file_path:line] — pattern reference (optional)

### Confirmed: no duplication, proceeding to implement
```

---

## Gotchas

### search_symbol is a substring match
`search_symbol("process")` will return everything with "process" in the name.
Narrow by adding more of the concept: `search_symbol("processPayment")`.
If still ambiguous, use `get_file_location` on the candidates to pick the right one.

### find_implementations finds subtypes, not the interface itself
To find the interface: `search_symbol("InterfaceName")` → filter `type: "class"`.
To find all classes implementing it: `find_implementations(interface_fqn=...)`.

### Callers define the contract, not the implementation
When in doubt about what a method should do or return, check its callers first.
The callers are the ground truth for expected behavior.

### Do not read the entire codebase
The graph exists specifically to avoid this. Max 3–5 targeted source reads per coding task.
If you find yourself wanting to read more, use another `search_symbol` or `find_callees` call instead.
