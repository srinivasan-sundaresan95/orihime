"""Dedalus write-serialization server.

Run with:
    python -m dedalus write-server [--port 7701] [--db ~/.dedalus/dedalus.db]

Developers running locally do NOT need this — they open KuzuDB directly.
This process is only needed on shared bare-metal servers where the UI and
CI indexing jobs run simultaneously.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel
import kuzu

log = logging.getLogger(__name__)
app = FastAPI(title="Dedalus Write Server")

_db: kuzu.Database | None = None
_conn: kuzu.Connection | None = None
_lock = asyncio.Lock()


class WriteRequest(BaseModel):
    statements: list[dict[str, Any]]  # [{"cypher": str, "params": dict}, ...]


class WriteResponse(BaseModel):
    ok: bool
    rows_affected: int = 0
    error: str = ""


@app.on_event("startup")
async def _startup() -> None:
    global _db, _conn
    db_path = os.environ.get("DEDALUS_DB_PATH", str(Path.home() / ".dedalus" / "dedalus.db"))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    _db = kuzu.Database(str(db_path))
    _conn = kuzu.Connection(_db)
    log.info("Write server opened KuzuDB at %s", db_path)


@app.post("/write", response_model=WriteResponse)
async def write(req: WriteRequest) -> WriteResponse:
    async with _lock:
        try:
            n = 0
            for stmt in req.statements:
                _conn.execute(stmt["cypher"], stmt.get("params", {}))
                n += 1
            return WriteResponse(ok=True, rows_affected=n)
        except Exception as exc:
            return WriteResponse(ok=False, error=str(exc))


@app.get("/health")
async def health() -> dict:
    db_path = os.environ.get("DEDALUS_DB_PATH", str(Path.home() / ".dedalus" / "dedalus.db"))
    return {"status": "ok", "db_path": db_path}


@app.get("/ping")
async def ping() -> dict:
    return {"pong": True}
