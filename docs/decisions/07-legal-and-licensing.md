# Decision: Legal and Licensing

## Why Orihime exists

GitNexus (https://github.com/abhigyanpatwari/GitNexus) provides similar functionality but is licensed under PolyForm Noncommercial. PolyForm Noncommercial prohibits use by for-profit corporations for any purpose. Using or forking GitNexus at Rakuten — even internally — would constitute a license violation.

Orihime is built from scratch using library documentation only (tree-sitter, KuzuDB, MCP SDK). No GitNexus source code was read during implementation.

## License of Orihime

Orihime uses only MIT/Apache-2.0 dependencies:

| Dependency | License |
|------------|---------|
| tree-sitter | MIT |
| tree-sitter-java | MIT |
| tree-sitter-kotlin | MIT |
| kuzu | MIT |
| mcp | MIT |
| pytest | MIT |

Orihime itself will be licensed MIT when shared.

## MOSS similarity check (Step 6 / T7)

Before sharing Orihime with the team or publishing it, a MOSS (Measure of Software Similarity) check is run comparing Orihime's `.py` files against GitNexus's `.js`/`.ts` source files. Threshold: 20% similarity per file pair.

The MOSS check result is documented in `MOSS_RESULT.md` at the repo root:
- Date of check
- Tool and version used
- Maximum similarity found (per file pair)
- Confirmation all pairs are below threshold

If any pair exceeds 20%, that file is rewritten before the check is considered passed.

## Why 20% and not 0%?

Some structural similarity is unavoidable when both tools solve the same problem (parse a Java class, find annotations, emit a node). The question is whether the similarity is algorithmic coincidence or copied text. MOSS measures token-level similarity — 20% is a conservative threshold that allows independent implementations to share common patterns without triggering false positives.
