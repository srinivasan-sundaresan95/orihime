from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from dedalus.language import registered_extensions

_SKIP_DIRS = {
    "build", "out", "generated", ".gradle", ".git",
    "node_modules", ".venv", "__pycache__", "target",
}


def walk_repo(root: Path) -> Iterator[tuple[Path, str]]:
    ext_map = registered_extensions()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for filename in filenames:
            suffix = os.path.splitext(filename)[1]
            lang = ext_map.get(suffix)
            if lang is not None:
                yield Path(dirpath) / filename, lang
