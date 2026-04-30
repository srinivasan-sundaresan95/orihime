# Indra — Post-v1.2 Backlog

## Aikido vs SonarQube Gap Analysis

**Context**: We use SonarQube Community (SAST). Indra v1.2 already covers:
- S4: Cross-service taint (BFS from endpoint handlers to outbound HTTP calls)
- S5: Custom sources/sinks YAML (`~/.indra/security.yml`)
- S6: Second-order injection (write-to-DB + read-from-DB chain detection)
- S7: OWASP / CWE / PCI DSS / STIG compliance reports

| Capability | SonarQube Coverage | Indra v1.2 | Feasibility |
|---|---|---|---|
| Cross-file taint (SAST) | Community ✓ | S4 ✓ | Done |
| Custom sources/sinks | Enterprise | S5 ✓ | Done |
| Second-order injection | Neither | S6 ✓ | Done |
| OWASP/CWE/PCI/STIG reports | Enterprise | S7 ✓ | Done |
| **Reachability filtering** | Enterprise partial | ✗ | **High** |
| **Secrets detection** | Community ✓ | ✗ | **High** |
| Dependency CVE scoring | Enterprise SCA | ✗ | Medium |
| License compliance | Enterprise SCA | ✗ | Medium |
| IDE real-time feedback | Not SonarQube | ✗ | Medium |
| Auto-patch generation | Not SonarQube | ✗ | Low (LLM+CI) |
| Runtime validation | Not SonarQube | ✗ | Low (runtime) |
| Malware in deps | Not SonarQube | ✗ | Low (behavioral) |
| IaC scanning | Enterprise partial | N/A | N/A (K8s has OPA) |

**Estimated coverage**: Indra closes ~70% of Aikido's advantage over SonarQube Community.

---

## Proposed Backlog Items (Priority Order)

### S8 — Entry-Point Reachability Filtering (High, ~80h)

**What**: Extend Indra to identify all entry points — HTTP handlers, Kafka consumers (`@KafkaListener`), schedulers (`@Scheduled`), JMS listeners — then mark each taint finding as "reachable from entry point" or "dead code path". Reduces alert volume by 30–50%.

**Implementation**:
- Tree-sitter pass: detect `@KafkaListener`, `@Scheduled`, `@JmsListener`, `@RabbitListener` annotations → add `is_entry_point BOOLEAN` to Method node
- New MCP tool: `find_reachable_sinks(repo_name)` — filter `find_taint_sinks` results to only those reachable from an entry point via BFS on CALLS edges
- UI: add "Reachable only" toggle to security findings page

### S9 — Hardcoded Secrets Detection (High, ~20h)

**What**: Detect API keys, tokens, passwords, private keys hardcoded in source files. SonarQube Community already does basic pattern matching, but Indra can correlate secrets with the methods/classes that use them (blast radius).

**Implementation**:
- In `_parse_file`: scan raw `src_bytes` for high-entropy strings and common secret patterns (AWS keys, GCP JSON, JWT, PEM, Bearer tokens, password assignments)
- Store `SecretFinding` nodes (pattern, file_id, line_start, entropy_score)
- MCP tool: `find_secrets(repo_name)` returning findings sorted by entropy
- Exclude test files by default

### S10 — Dependency CVE Scoring (Medium, ~40h)

**What**: Parse `pom.xml` / `build.gradle` to extract dependency coordinates, enrich with NVD CVE data, combine with reachability to prioritise: "this CVE affects a library your code actually calls".

**Implementation**:
- New walker extension: detect `pom.xml` / `build.gradle.kts` → extract `{groupId, artifactId, version}`
- Post-index step: query NVD API (`https://services.nvd.nist.gov/rest/json/cves/2.0`) for each dependency
- Store `Dependency` node + `CVEFinding` node
- MCP tool: `find_vulnerable_dependencies(repo_name)` with optional reachability filter
- Cache CVE results to avoid hitting rate limits

### S11 — License Compliance (Medium, ~30h)

**What**: Flag dependencies with GPL/AGPL/LGPL licenses in a commercial project. Parse Maven/Gradle dependency tree, look up SPDX license identifiers.

**Implementation**:
- Extend S10 dependency walker with license lookup (Maven Central SPDX metadata or OSS Index API)
- MCP tool: `find_license_violations(repo_name, allowed_licenses=["MIT","Apache-2.0","BSD-2-Clause"])` 

### S12 — IDE LSP Integration (Medium, ~60h)

**What**: Language Server Protocol server that maps Indra findings back to source positions, enabling IntelliJ/VS Code to show taint warnings inline without re-running the full scan.

**Implementation**:
- Thin LSP server wrapping Indra MCP queries
- Map finding `file_path + line_start` to LSP `textDocument/publishDiagnostics` protocol
- Trigger re-check on file save (incremental re-index already in v1.1-A)

---

## Other General Improvements

### G1 — callee_name on CALLS edges (Medium, ~30h)
Store the method name being called on CALLS edges (not just source/target IDs). Enables "what external library methods does this service call?" queries without needing the callee's source indexed.

### G2 — Argument position tracking on CALLS (Medium, ~40h)
Add `caller_arg_pos INT64` and `callee_param_pos INT64` to CALLS edges. Required for precise data-flow taint (not just reachability). Enables "does arg 0 of method A flow to arg 1 of method B?"

### G3 — Python language support (Medium, ~50h)
Add `python_extractor.py` using `tree-sitter-python`. Django/Flask endpoint detection (`@app.route`, `@csrf_exempt`), function calls, imports. Needed if team has Python services.

### G4 — UI security findings page (High, ~20h)
Add a dedicated "Security" tab in the web UI that shows findings from S4–S9 in a table with OWASP category, file, line, severity, and a copy-link button. Currently findings are only available via MCP.

### G5 — Parallel DB writes via connection pool (Low, ~40h)
KuzuDB's single-writer constraint means Phase 2 is serial. Investigate whether KuzuDB's upcoming multi-writer support or a WAL-based approach can speed up the write phase for repos > 500 files.

### G6 — `--watch` mode (Low, ~30h)
Run `inotifywait` (Linux) or `FSEvents` (Mac) to trigger incremental re-index automatically on file save. Combined with v1.1-A blob hash skipping, this would keep the graph near-real-time.

---

## Not Worth Building in Indra

| Capability | Reason |
|---|---|
| Auto-patch generation | Requires LLM + CI/CD pipeline; belongs in a separate agent workflow |
| Runtime vulnerability validation | Requires live app + network; fundamentally dynamic |
| Malware in dependencies | Requires behavioral sandboxing (YARA rules, package registry feeds) |
| Container/OS layer scanning | Docker image scanning — separate tool (Trivy, Grype); out of Indra scope |
| IaC misconfiguration | Terraform/Helm scanning — separate tool (Checkov, tfsec); Rakuten uses OPA/Kyverno |
