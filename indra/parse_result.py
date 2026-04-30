"""ParseResult dataclass — the picklable output of a parallel parse worker.

Workers (running in child processes via ProcessPoolExecutor) parse a single
source file with tree-sitter and run the language extractor.  They cannot
touch KuzuDB (not picklable; DB state must stay in the main process).  They
return a ParseResult which contains only plain Python objects (dicts, lists,
strings, bytes) so that multiprocessing can pickle and transfer it back.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParseResult:
    """All data extracted from a single source file, ready for DB insertion."""

    file_id: str
    file_path: str        # str, not Path — easier to pickle across processes
    lang: str
    repo_id: str

    # Raw source bytes — kept so the main process can re-parse for resolve_calls
    # without re-reading from disk (avoids TOCTOU and second I/O).
    src_bytes: bytes = field(default_factory=bytes)

    # Extracted graph nodes (plain dicts matching the KuzuDB schema)
    classes: list[dict] = field(default_factory=list)
    methods: list[dict] = field(default_factory=list)
    endpoints: list[dict] = field(default_factory=list)
    rest_calls: list[dict] = field(default_factory=list)
    impl_map: dict[str, str] = field(default_factory=dict)
    # Inheritance edges: list of {"child_id": str, "parent_fqn": str, "edge_type": "EXTENDS"|"IMPLEMENTS"}
    inheritance_edges: list[dict] = field(default_factory=list)
