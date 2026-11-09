"""PostgreSQL storage for proposed actions (durable, so a restart never loses a pending approval)."""

import json
from datetime import datetime

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from agent.actions import ProposedAction
from knowledge_store.db import default_dsn

DDL = """
CREATE TABLE IF NOT EXISTS proposed_actions (
    id text PRIMARY KEY,
    diagnosis_id text NOT NULL,
    tool text NOT NULL,
    args jsonb NOT NULL,
    reason text NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    decided_by text,
    decided_at timestamptz,
    decision_comment text,
    result text
);
"""
COLUMNS = "id, diagnosis_id, tool, args, reason, status, created_at, expires_at, decided_by, decided_at, decision_comment, result"


class PgApprovalStore:
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
            await conn.execute(DDL)

    async def drop_schema(self) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn, autocommit=True) as conn:
            await conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(self.schema)))

    async def create(self, a: ProposedAction) -> None:
        async with await self._connect() as conn:
            await conn.execute(
                f"INSERT INTO proposed_actions ({COLUMNS}) VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s)",
                (a.id, a.diagnosis_id, a.tool, json.dumps(a.args), a.reason, a.status, a.created_at, a.expires_at, a.decided_by, a.decided_at, a.decision_comment, a.result),
            )

    async def get(self, action_id: str) -> ProposedAction | None:
        async with await self._connect() as conn:
            row = await (await conn.execute(f"SELECT {COLUMNS} FROM proposed_actions WHERE id = %s", (action_id,))).fetchone()
        return ProposedAction(**row) if row else None

    async def list(self, status: str | None = None) -> list[ProposedAction]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM proposed_actions WHERE (%s::text IS NULL OR status = %s) ORDER BY created_at DESC LIMIT 200", (status, status)
            )
            return [ProposedAction(**r) for r in await cur.fetchall()]

    async def decide(self, action_id: str, approved: bool, actor: str, comment: str | None) -> ProposedAction | None:
        # One atomic statement: only a pending, unexpired action can be decided, and only once.
        async with await self._connect() as conn:
            row = await (await conn.execute(
                f"UPDATE proposed_actions SET status = %s, decided_by = %s, decided_at = now(), decision_comment = %s "
                f"WHERE id = %s AND status = 'pending' AND expires_at > now() RETURNING {COLUMNS}",
                ("approved" if approved else "rejected", actor, comment, action_id),
            )).fetchone()
        return ProposedAction(**row) if row else None

    async def finish(self, action_id: str, ok: bool, result: str) -> None:
        async with await self._connect() as conn:
            await conn.execute("UPDATE proposed_actions SET status = %s, result = %s WHERE id = %s", ("executed" if ok else "failed", result, action_id))
