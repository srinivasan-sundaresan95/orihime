---
name: orihime-design-review
description: >
  Use when the user asks for a code review focused on class structure, design patterns,
  OOP principles, or architecture quality. Reviews whether GoF/SOLID design patterns
  are properly applied, flags misapplications and missing patterns, suggests splitting
  bloated classes/methods, and recommends fortifying weak abstractions. Can scope to
  the latest git changes only (PR review mode) or review the full repository.
  Queries Orihime graph first to build the structural picture, then reads targeted source.
  Trigger phrases: "review the design", "check class structure", "design patterns",
  "is this SOLID", "should I split this", "code review", "OOP review", "architecture review",
  "review my PR", "review these changes".
---

# Orihime Design Review Skill

## Scope selection — ALWAYS decide this first

Before doing anything else, determine the review scope:

**Changed-files mode** (default for PR/branch reviews):
- User says "review my PR", "review these changes", "review what I just wrote", or is on a feature branch
- Run: `Bash("git diff --name-only main...HEAD")` or `git diff --name-only HEAD~1`
- Extract the changed class/method names from the diff
- Feed those specific FQNs into the graph analysis below — ignore everything else

**Full-repo mode** (explicit request or no branch context):
- User says "review the design of repo X", "audit the whole codebase", "full design review"
- Use `search_symbol` broadly and `find_complexity_hints` across the whole repo
- Prioritise by blast radius and complexity hint count

**Mixed mode** (changed files + their neighbourhood):
- User says "review these changes in context" or "check if my changes fit the existing design"
- Start with changed files (as above), then expand to their direct callers/callees via the graph
- This is the most useful mode for PR reviews — sees both what changed AND what it affects

When in doubt, ask: "Review just the changed files, or the full repo?"

## Trigger conditions

- "Review the design / class structure of [class/repo]"
- "Are design patterns being properly applied?"
- "Is this code SOLID?"
- "Should I split [class] up?"
- "This class feels too big — what should I do?"
- "Code review for [class/feature]"
- "How is [pattern] implemented in [repo]?"
- "Is this a good abstraction?"
- Any PR review or code review request against an indexed repo

---

## Phase 0 — Extract changed classes (changed-files mode only)

```bash
git diff --name-only main...HEAD          # files changed vs main
git diff --name-only HEAD~1               # files changed in last commit
git diff --name-only --cached             # staged files
```

From the changed file paths, derive the class names and FQNs:
- `src/main/kotlin/com/example/service/OrderService.kt` → `com.example.service.OrderService`
- Use `mcp__orihime__search_symbol(query="OrderService")` to confirm the FQN and get the graph node

Only the classes in the changed files enter the Phase 1 analysis.
Their callers and callees from the graph form the "neighbourhood" for mixed mode.

---

## Phase 1 — Build the structural picture from the graph (no source reads yet)

### 1.1 — Map the class hierarchy

```
mcp__orihime__search_symbol(query="<class_or_feature_name>")
```

For each relevant class, resolve its full inheritance chain:
```
mcp__orihime__find_superclasses(class_fqn="<fqn>")
mcp__orihime__find_implementations(interface_fqn="<fqn>")
```

**Graph signals to note:**
- Deep inheritance chains (>3 levels) → composition over inheritance risk
- Wide fan-out of implementors (>5) → interface may be over-specified
- Class with no parent and no interface → missing abstraction

---

### 1.2 — Measure method-level coupling and cohesion

```
mcp__orihime__find_callees(method_fqn="<fqn>")
mcp__orihime__find_callers(method_fqn="<fqn>")
```

For each class under review, run this on its key methods. Count:
- **Efferent coupling (Ce)**: how many distinct classes does this class call?
- **Afferent coupling (Ca)**: how many classes call this class?
- **Ratio**: high Ce + low Ca = unstable, change-prone class
- **Methods with 0 callers**: dead code or missing integration

```
mcp__orihime__blast_radius(method_fqn="<fqn>", max_depth=3)
```

A blast radius > 15 methods for a single utility method signals a God Class risk
or missing abstraction layer.

---

### 1.3 — Check complexity hints (structural rot signals)

```
mcp__orihime__find_complexity_hints(repo_name="<repo>", min_severity="low")
```

Map hint types to design problems:

| Hint | Design signal |
|---|---|
| `O(n²)-candidate` | Missing index structure or Strategy pattern |
| `O(n²)-list-scan` | Wrong data structure — should be Set/Map |
| `n+1-risk` | Missing Repository abstraction or lazy-load strategy |
| `unbounded-query` | Missing Specification/Query Object pattern |
| `recursive` | May benefit from Iterator or Composite pattern |

---

### 1.4 — Check I/O fan-out (orchestration design signals)

```
mcp__orihime__find_io_fanout(repo_name="<repo>", min_total=3)
```

Methods with 4+ I/O calls are orchestrators. Review whether:
- They belong in a dedicated Facade/Orchestrator class (not embedded in a Controller)
- Serial I/O that could be parallel signals missing async/reactive design
- The method is doing too much (violates SRP)

---

### 1.5 — ORM data model (JPA entity relationships)

```
mcp__orihime__list_entity_relations(repo_name="<repo>")
```

Interpret results using this signal table:

| Relationships on one class | Signal |
|---|---|
| 0–2 | Fine — focused domain object |
| 3–4 | Note — review fetch strategies |
| 5+ | Flag — candidate for decomposition into separate Aggregates |
| Any `@ManyToMany` | Flag — hidden join table, often missing owning-side discipline |
| `@OneToMany` with `fetch_type=EAGER` | Flag — guaranteed N+1 (also caught by `find_eager_fetches`) |

Map to findings:
- Class with 5+ relations → SPLIT finding (decompose into separate Aggregates)
- `@OneToMany EAGER` without `@BatchSize` / `@EntityGraph` → APPLY finding (add batch fetching strategy)
- `@ManyToMany` where both sides are `EAGER` → MISAPPLIED (dangerous combination)

---

### 1.6 — Check for cross-service coupling smells

```
mcp__orihime__find_repo_dependencies(repo_name="<repo>")
mcp__orihime__list_unresolved_calls(repo_name="<repo>")
```

Unresolved calls that directly reference internal class names (not URL paths) suggest
tight coupling between services — Anti-Corruption Layer or Gateway pattern missing.

---

## Phase 2 — Targeted source reads

Using file paths and line numbers from Phase 1, read only the classes flagged:

```
mcp__orihime__get_file_location(fqn="<class_or_method_fqn>")
```
Then: `Read(file_path=..., limit=200)`

Read order (most important first):
1. The class with the highest blast radius (most impactful to change)
2. The class with the most complexity hints
3. Any class with 4+ I/O fan-out
4. Interfaces with 5+ implementors

---

## Phase 3 — Pattern analysis

For each class read in Phase 2, evaluate against all 20 GoF patterns + SOLID principles.

---

### GoF Pattern Checklist

#### Creational
| Pattern | Correct use signal | Misuse / missing signal |
|---|---|---|
| **Factory Method** | Abstract creator returns product via overridable method | `new ConcreteClass()` scattered in callers; constructor logic in callers |
| **Abstract Factory** | Family of related objects created via interface | Multiple `if/switch` blocks creating families of objects |
| **Builder** | Complex object with many optional params built step-by-step | Constructor with 5+ params; multiple overloaded constructors |
| **Prototype** | Objects cloned to avoid expensive construction | Deep copy logic duplicated across callers |
| **Singleton** | One instance, global access, thread-safe | Multiple `static instance` fields; double-checked locking missing |

#### Structural
| Pattern | Correct use signal | Misuse / missing signal |
|---|---|---|
| **Adapter** | Incompatible interfaces bridged via wrapper | Direct casting between incompatible types; `instanceof` chains |
| **Bridge** | Abstraction and implementation vary independently | Explosion of subclasses combining two dimensions |
| **Composite** | Tree structures treated uniformly (leaf = composite) | Recursive `if (node instanceof Leaf)` checks |
| **Decorator** | Behaviour added at runtime via wrapping | Subclass explosion to add feature combinations |
| **Facade** | Simplified interface over complex subsystem | Controller/Service calling 5+ subsystems directly |
| **Flyweight** | Shared intrinsic state for many fine-grained objects | Large objects recreated per request when most state is shared |
| **Proxy** | Controlled access / lazy init / logging via surrogate | Cross-cutting concerns (logging, auth, caching) duplicated across classes |

#### Behavioural
| Pattern | Correct use signal | Misuse / missing signal |
|---|---|---|
| **Chain of Responsibility** | Request passes through handler chain; each may handle or pass | Nested `if/else` chains for request processing |
| **Command** | Action encapsulated as object; supports undo, queue | Methods with `execute(action, params)` switch/if block |
| **Iterator** | Traverse collection without exposing internals | `for(int i=0; i<list.size(); i++)` accessing internal structure |
| **Mediator** | Objects communicate through central coordinator | Many-to-many direct references between objects |
| **Observer** | State change notifies dependents automatically | Polling loops; manual notification calls scattered in methods |
| **State** | Object behaviour changes with internal state | Large `if/switch` on a status/state field across multiple methods |
| **Strategy** | Algorithm family encapsulated and interchangeable | `if/switch` selecting algorithm variant; algorithm logic in caller |
| **Template Method** | Algorithm skeleton in base; steps overridden by subclasses | Duplicated algorithm structure across sibling classes |

---

### SOLID Principles Checklist

| Principle | Graph signal | Source signal |
|---|---|---|
| **SRP** (Single Responsibility) | Method count > 20 in one class; blast_radius > 20 from one method | Class handles persistence + business logic + HTTP concerns |
| **OCP** (Open/Closed) | Adding a feature requires modifying a class with high Ca | `if/switch` on type/variant that grows with each feature |
| **LSP** (Liskov Substitution) | find_implementations returns impl that overrides base with throwing | Overridden method throws `UnsupportedOperationException` |
| **ISP** (Interface Segregation) | Interface with 10+ methods; many implementors stub half of them | Implementors with `throw new UnsupportedOperationException()` |
| **DIP** (Dependency Inversion) | find_callees shows class directly instantiating concrete deps | `new ConcreteService()` in business logic; no interface injection |

---

## Phase 4 — Formulate recommendations

For each finding, classify as one of:

| Severity | Meaning |
|---|---|
| **SPLIT** | Class/method is doing too much — decompose it |
| **FORTIFY** | Abstraction exists but is too thin or leaky — strengthen it |
| **APPLY** | A pattern is missing where one would clearly help |
| **MISAPPLIED** | A pattern is present but incorrectly implemented |
| **REMOVE** | Pattern applied where it adds complexity with no benefit |

---

## Output format

```
## Design Review — [class or repo name]

### Structural Summary (from graph)
- Classes reviewed: N
- Deepest inheritance chain: X levels ([chain])
- Highest blast radius: [method] → N methods
- Complexity hints: N flagged (X high, Y medium)
- I/O fan-out hotspots: N methods with 3+ I/O calls

---

### ORM Data Model
| Class | Relationship count | Notable relations |
|---|---|---|
| OrderEntity | 6 | 3× OneToMany (2 EAGER), 1× ManyToMany |

---

### Findings

#### [ClassName] — [Severity: SPLIT / FORTIFY / APPLY / MISAPPLIED]

**Issue**: [one sentence describing the problem]

**Evidence**:
- [Graph evidence: blast_radius=18, 4 I/O calls, 3-level inheritance]
- [Source evidence: line X–Y: method does persistence + business logic + HTTP response building]

**Pattern involved**: [e.g. Facade, SRP, Strategy]

**Recommendation**:
> [Specific, actionable suggestion. "Extract the persistence concern into a Repository class.
>  Move the HTTP response building into a ResponseMapper. The remaining service method
>  should only orchestrate between them."]

**Blast radius of this change**: [N methods from find_callers — list the ones that need updating]

---

[repeat for each finding]

### Priority Order
1. [Most impactful finding] — affects N callers
2. ...

### Patterns well-applied (worth noting)
- [e.g. PointCardRepository correctly uses Repository pattern]
- [e.g. PaymentGateway correctly wraps external service via Adapter]
```

---

## Gotchas

### Don't flag every pattern absence as a problem
Only flag a missing pattern when there is a concrete smell in the code — not speculatively.
A simple class with 3 methods doesn't need a Factory. Apply Occam's Razor.

### Blast radius is the best proxy for refactoring risk
Before recommending a SPLIT, always check `blast_radius`. If the method has 20+ transitive
callers, the split needs a migration plan — it's not a casual refactor. Flag this.

### MISAPPLIED is worse than MISSING
A Singleton without thread-safety, an Observer that doesn't remove listeners, or a Proxy that
bypasses the interface it wraps are all actively dangerous. Flag these as HIGH priority.

### SRP violations are the most common — don't over-report
Flag SRP only when a class clearly handles two distinct concerns (e.g. HTTP + persistence).
Don't flag "this class has 15 methods" as SRP without checking what they actually do.

### Read source files only after the graph tells you where to look
Phase 1 graph analysis takes ~5 tool calls and identifies the 2–3 classes that warrant deep
review. Don't read source files speculatively — the graph will tell you which ones matter.

### list_entity_relations returns declared relationships only
It does not tell you whether fetch strategies are overridden in JPQL queries
(e.g. `JOIN FETCH`). A class showing LAZY can still trigger N+1 if callers use
open-session-in-view or call getters outside a transaction. Use find_eager_fetches
in parallel to cross-reference — it specifically flags EAGER collections.
