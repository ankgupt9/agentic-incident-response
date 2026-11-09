import uuid

import psycopg
import pytest

from knowledge_store.db import KnowledgeStore, default_dsn


def postgres_up() -> bool:
    try:
        psycopg.connect(default_dsn(), connect_timeout=2).close()
        return True
    except psycopg.Error:
        return False


needs_postgres = pytest.mark.skipif(not postgres_up(), reason="postgres not running (docker compose up -d postgres)")


@pytest.fixture
async def store():
    """A KnowledgeStore in its own throwaway schema, dropped afterwards."""
    s = KnowledgeStore(schema=f"t_{uuid.uuid4().hex[:10]}")
    await s.apply_schema()
    yield s
    await s.drop_schema()


@pytest.fixture
async def pg_audit():
    """A PgAuditLog in its own throwaway schema."""
    from agent.audit import PgAuditLog

    a = PgAuditLog(schema=f"t_{uuid.uuid4().hex[:10]}")
    await a.apply_schema()
    yield a
    await a.drop_schema()
