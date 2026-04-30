"""Client for the Dedalus write-serialization server."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)


class WriteClient:
    """Sends write batches to the Dedalus write server via HTTP."""

    def __init__(self, server_url: str) -> None:
        self.base_url = server_url.rstrip("/")

    def is_available(self) -> bool:
        """Return True if the server is reachable."""
        try:
            req = urllib.request.Request(f"{self.base_url}/ping")
            with urllib.request.urlopen(req, timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    def execute(self, cypher: str, params: dict | None = None) -> None:
        """Send a single write statement to the server."""
        self.execute_batch([{"cypher": cypher, "params": params or {}}])

    def execute_batch(self, statements: list[dict]) -> None:
        """Send multiple write statements as a single atomic batch."""
        body = json.dumps({"statements": statements}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/write",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.loads(r.read())
            if not result.get("ok"):
                raise RuntimeError(f"Write server error: {result.get('error')}")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Write server unreachable at {self.base_url}: {exc}") from exc
