"""PostgreSQL store for the knowledge layer. The files are the source of truth; this is the index.

Search is Postgres full-text search with OR semantics: a model-written query rarely contains every
word of the right runbook, so any matching word counts and ts_rank_cd orders by how many and how well.
"""

import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from knowledge_store.models import ChangeRecord, KnowledgeDoc, SearchHit, knowledge_version

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS knowledge_docs (
    id text PRIMARY KEY,
    kind text NOT NULL,
    title text NOT NULL,
    body text NOT NULL,
    owner text NOT NULL DEFAULT '',
    last_reviewed date,
    applies_to text[] NOT NULL DEFAULT '{}',
    tags text[] NOT NULL DEFAULT '{}',
    status text NOT NULL DEFAULT 'active',
    source_path text NOT NULL DEFAULT '',
    content_hash text NOT NULL,
    tsv tsvector NOT NULL
);
CREATE INDEX IF NOT EXISTS knowledge_docs_tsv ON knowledge_docs USING gin (tsv);
CREATE TABLE IF NOT EXISTS change_events (
    id text PRIMARY KEY,
    ts timestamptz NOT NULL,
    service text NOT NULL,
    kind text NOT NULL,
    summary text NOT NULL,
    actor text NOT NULL DEFAULT '',
    ref text
);
CREATE INDEX IF NOT EXISTS change_events_ts ON change_events (ts DESC);
CREATE TABLE IF NOT EXISTS knowledge_versions (
    version text NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    doc_count integer NOT NULL
);
"""

UPSERT_DOC = """
INSERT INTO knowledge_docs (id, kind, title, body, owner, last_reviewed, applies_to, tags, status, source_path, content_hash, tsv)
VALUES (%(id)s, %(kind)s, %(title)s, %(body)s, %(owner)s, %(last_reviewed)s, %(applies_to)s, %(tags)s, %(status)s,
        %(source_path)s, %(content_hash)s,
        setweight(to_tsvector('english', %(title)s), 'A') || setweight(to_tsvector('english', %(tags_text)s), 'B')
        || setweight(to_tsvector('english', %(body)s), 'C'))
ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, title = EXCLUDED.title, body = EXCLUDED.body,
    owner = EXCLUDED.owner, last_reviewed = EXCLUDED.last_reviewed, applies_to = EXCLUDED.applies_to,
    tags = EXCLUDED.tags, status = EXCLUDED.status, source_path = EXCLUDED.source_path,
    content_hash = EXCLUDED.content_hash, tsv = EXCLUDED.tsv
"""


def default_dsn() -> str:
    if url := os.getenv("DATABASE_URL"):
        return url
    password = os.getenv("POSTGRES_PASSWORD") or "agent_dev"  # POC default; see docker-compose.yml
    return f"postgresql://agent:{password}@localhost:5432/agent"


def build_tsquery(text: str, max_words: int = 20) -> str:
    """OR together the alphanumeric words of a free-text query. Safe to pass to to_tsquery."""
    words: list[str] = []
    for w in re.findall(r"[A-Za-z0-9]+", text.lower()):
        if len(w) >= 2 and w not in words:
            words.append(w)
    return " | ".join(words[:max_words])


def _doc_row(doc: KnowledgeDoc) -> dict:
    row = doc.model_dump()
    row.update(content_hash=doc.content_hash, tags_text=" ".join(doc.tags))
    return row


class KnowledgeStore:
    def __init__(self, dsn: str | None = None, schema: str = "public"):
        self.dsn, self.schema = dsn or default_dsn(), schema

    @asynccontextmanager
    async def _conn(self):
        async with await psycopg.AsyncConnection.connect(self.dsn, autocommit=True, row_factory=dict_row) as conn:
            await conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            yield conn

    async def apply_schema(self) -> None:
        async with self._conn() as conn:
            await conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))
            await conn.execute(SCHEMA_DDL)

    async def drop_schema(self) -> None:
        async with self._conn() as conn:
            await conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(self.schema)))

    async def replace_docs(self, docs: list[KnowledgeDoc]) -> str:
        """Make the store hold exactly these documents. Returns the new knowledge version."""
        version = knowledge_version(docs)
        async with self._conn() as conn:
            async with conn.transaction():
                for d in docs:
                    await conn.execute(UPSERT_DOC, _doc_row(d))
                await conn.execute("DELETE FROM knowledge_docs WHERE NOT (id = ANY(%s))", ([d.id for d in docs],))
                await conn.execute("INSERT INTO knowledge_versions (version, doc_count) VALUES (%s, %s)", (version, len(docs)))
        return version

    async def import_change_events(self, records: list[ChangeRecord]) -> int:
        async with self._conn() as conn:
            async with conn.transaction():
                for r in records:
                    await conn.execute(
                        "INSERT INTO change_events (id, ts, service, kind, summary, actor, ref) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (id) DO UPDATE SET ts=EXCLUDED.ts, service=EXCLUDED.service, kind=EXCLUDED.kind, "
                        "summary=EXCLUDED.summary, actor=EXCLUDED.actor, ref=EXCLUDED.ref",
                        (r.id, r.ts, r.service, r.kind, r.summary, r.actor, r.ref),
                    )
        return len(records)

    async def current_version(self) -> str | None:
        async with self._conn() as conn:
            cur = await conn.execute("SELECT version FROM knowledge_versions ORDER BY ingested_at DESC LIMIT 1")
            row = await cur.fetchone()
        return row["version"] if row else None

    async def search_runbooks(self, query: str, service: str | None = None, limit: int = 5) -> list[SearchHit]:
        tsquery = build_tsquery(query)
        if not tsquery:
            return []
        async with self._conn() as conn:
            cur = await conn.execute(
                """
                SELECT id, title, owner, last_reviewed, status, ts_rank_cd(tsv, q) AS score,
                       ts_headline('english', body, q, 'MaxFragments=2, MinWords=6, MaxWords=28, StartSel=**, StopSel=**') AS snippet
                FROM knowledge_docs, to_tsquery('english', %s) q
                WHERE kind = 'runbook' AND status <> 'retired' AND tsv @@ q
                  AND (%s::text IS NULL OR %s = ANY(applies_to))
                ORDER BY score DESC, id LIMIT %s
                """,
                (tsquery, service, service, limit),
            )
            rows = await cur.fetchall()
        return [SearchHit(**r) for r in rows]

    async def get_doc(self, doc_id: str) -> KnowledgeDoc | None:
        async with self._conn() as conn:
            cur = await conn.execute(
                "SELECT id, kind, title, body, owner, last_reviewed, applies_to, tags, status, source_path FROM knowledge_docs WHERE id = %s",
                (doc_id,),
            )
            row = await cur.fetchone()
        return KnowledgeDoc(**row) if row else None

    async def list_ids(self, kind: str) -> list[str]:
        async with self._conn() as conn:
            cur = await conn.execute("SELECT id FROM knowledge_docs WHERE kind = %s ORDER BY id", (kind,))
            return [r["id"] for r in await cur.fetchall()]

    async def recent_changes(self, service: str | None = None, minutes: int = 1440, limit: int = 20, now: datetime | None = None) -> list[ChangeRecord]:
        since = (now or datetime.now(timezone.utc)) - timedelta(minutes=minutes)
        async with self._conn() as conn:
            cur = await conn.execute(
                "SELECT id, ts, service, kind, summary, actor, ref FROM change_events "
                "WHERE ts >= %s AND (%s::text IS NULL OR service = %s) ORDER BY ts DESC LIMIT %s",
                (since, service, service, limit),
            )
            return [ChangeRecord(**r) for r in await cur.fetchall()]
