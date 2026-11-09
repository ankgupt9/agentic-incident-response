"""Audit log: an append-only record of every tool call, decision, approval and control change.

Hard rule 3. The Postgres table refuses UPDATE, DELETE and TRUNCATE (triggers), so history cannot be
rewritten through the application. If the log cannot be written the caller must fail closed: no
audit record, no action.
"""

import json
from datetime import datetime, timezone
from typing import Protocol

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from knowledge_store.db import default_dsn


class AuditUnavailable(Exception):
    """The audit log could not be written. Callers refuse the action."""


class AuditLog(Protocol):
    async def record(self, event: str, *, diagnosis_id: str | None = None, actor: str = "agent", **detail) -> None: ...


class MemoryAuditLog:
    """In-memory log for tests and for running without Postgres."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def record(self, event: str, *, diagnosis_id: str | None = None, actor: str = "agent", **detail) -> None:
        self.events.append({"ts": datetime.now(timezone.utc), "event": event, "diagnosis_id": diagnosis_id, "actor": actor, "detail": detail})

    def of(self, event: str) -> list[dict]:
        return [e for e in self.events if e["event"] == event]


AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id bigserial PRIMARY KEY,
    ts timestamptz NOT NULL DEFAULT now(),
    diagnosis_id text,
    event text NOT NULL,
    actor text NOT NULL,
    detail jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS audit_log_diagnosis ON audit_log (diagnosis_id);
CREATE INDEX IF NOT EXISTS audit_log_event_ts ON audit_log (event, ts DESC);
CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'audit_log is append-only'; END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_log_no_change ON audit_log;
CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log;
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log FOR EACH STATEMENT EXECUTE FUNCTION audit_log_immutable();
"""


class PgAuditLog:
    def __init__(self, dsn: str | None = None, schema: str = "public"):
        self.dsn, self.schema = dsn or default_dsn(), schema

    async def _connect(self):
        conn = await psycopg.AsyncConnection.connect(self.dsn, autocommit=True, row_factory=dict_row)
        await conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        return conn

    async def apply_schema(self) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn, autocommit=True) as conn:
            await conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))
            await conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            await conn.execute(AUDIT_DDL)

    async def drop_schema(self) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn, autocommit=True) as conn:
            await conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(self.schema)))

    async def record(self, event: str, *, diagnosis_id: str | None = None, actor: str = "agent", **detail) -> None:
        try:
            async with await self._connect() as conn:
                await conn.execute(
                    "INSERT INTO audit_log (diagnosis_id, event, actor, detail) VALUES (%s, %s, %s, %s::jsonb)",
                    (diagnosis_id, event, actor, json.dumps(detail, default=str)),
                )
        except psycopg.Error as exc:
            raise AuditUnavailable(type(exc).__name__) from exc

    async def events(self, diagnosis_id: str | None = None, event: str | None = None, limit: int = 200) -> list[dict]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id, ts, diagnosis_id, event, actor, detail FROM audit_log "
                "WHERE (%s::text IS NULL OR diagnosis_id = %s) AND (%s::text IS NULL OR event = %s) ORDER BY id DESC LIMIT %s",
                (diagnosis_id, diagnosis_id, event, event, limit),
            )
            return await cur.fetchall()

    async def spend_today_usd(self) -> float:
        """Sum of cost recorded by diagnosis_finished events since UTC midnight (for the daily budget)."""
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT COALESCE(SUM((detail->>'cost_usd')::float), 0) AS usd FROM audit_log "
                "WHERE event = 'diagnosis_finished' AND ts >= date_trunc('day', now() AT TIME ZONE 'utc') AT TIME ZONE 'utc'"
            )
            return (await cur.fetchone())["usd"]
