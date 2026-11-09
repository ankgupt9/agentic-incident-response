"""Runtime kill switch (hard rule 10). Engaged means the agent must not start or continue work.

State lives in Postgres so it survives restarts and is shared by every agent process. If the state
cannot be read the switch reads as engaged: an agent that cannot check its own brake does not run.
Every change is written to the audit log.
"""

import time
from typing import Protocol

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from agent.audit import AuditLog
from knowledge_store.db import default_dsn

CACHE_SECONDS = 2.0


class KillSwitch(Protocol):
    async def engaged(self) -> bool: ...
    async def set(self, engaged: bool, actor: str, reason: str) -> None: ...


class MemoryKillSwitch:
    def __init__(self, audit: AuditLog | None = None, engaged: bool = False):
        self._engaged, self._audit = engaged, audit

    async def engaged(self) -> bool:
        return self._engaged

    async def set(self, engaged: bool, actor: str, reason: str) -> None:
        if self._audit:
            await self._audit.record("kill_switch_changed", actor=actor, engaged=engaged, reason=reason)
        self._engaged = engaged


CONTROLS_DDL = """
CREATE TABLE IF NOT EXISTS agent_controls (
    key text PRIMARY KEY,
    value text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text NOT NULL DEFAULT ''
);
"""


class PgKillSwitch:
    def __init__(self, audit: AuditLog, dsn: str | None = None, schema: str = "public"):
        self._audit, self.dsn, self.schema = audit, dsn or default_dsn(), schema
        self._cached: tuple[float, bool] | None = None

    async def _connect(self):
        conn = await psycopg.AsyncConnection.connect(self.dsn, autocommit=True, row_factory=dict_row, connect_timeout=3)
        await conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        return conn

    async def apply_schema(self) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn, autocommit=True) as conn:
            await conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))
            await conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            await conn.execute(CONTROLS_DDL)

    async def engaged(self) -> bool:
        now = time.monotonic()
        if self._cached and now - self._cached[0] < CACHE_SECONDS:
            return self._cached[1]
        try:
            async with await self._connect() as conn:
                cur = await conn.execute("SELECT value FROM agent_controls WHERE key = 'kill_switch'")
                row = await cur.fetchone()
            value = bool(row) and row["value"] == "engaged"
        except psycopg.Error:
            return True  # fail closed
        self._cached = (now, value)
        return value

    async def set(self, engaged: bool, actor: str, reason: str) -> None:
        # Audit first: a change that cannot be recorded is not made.
        await self._audit.record("kill_switch_changed", actor=actor, engaged=engaged, reason=reason)
        async with await self._connect() as conn:
            await conn.execute(
                "INSERT INTO agent_controls (key, value, updated_by) VALUES ('kill_switch', %s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now(), updated_by = EXCLUDED.updated_by",
                ("engaged" if engaged else "released", actor),
            )
        self._cached = None
