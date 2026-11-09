import psycopg
import pytest

from agent.audit import MemoryAuditLog
from agent.killswitch import MemoryKillSwitch, PgKillSwitch
from tests.conftest import needs_postgres


async def test_memory_kill_switch_audits_every_change():
    audit = MemoryAuditLog()
    ks = MemoryKillSwitch(audit)
    assert await ks.engaged() is False
    await ks.set(True, "ankur", "runaway cost")
    await ks.set(False, "ankur", "fixed")
    assert [e["detail"]["engaged"] for e in audit.of("kill_switch_changed")] == [True, False]
    assert audit.of("kill_switch_changed")[0]["detail"]["reason"] == "runaway cost" and audit.events[0]["actor"] == "ankur"


@needs_postgres
async def test_pg_audit_log_records_and_queries(pg_audit):
    await pg_audit.record("tool_call_requested", diagnosis_id="d1", tool="query_logs", args={"service": "x"})
    await pg_audit.record("diagnosis_finished", diagnosis_id="d1", cost_usd=0.004)
    await pg_audit.record("diagnosis_finished", diagnosis_id="d2", cost_usd=0.006)
    rows = await pg_audit.events(diagnosis_id="d1")
    assert [r["event"] for r in rows] == ["diagnosis_finished", "tool_call_requested"] and rows[1]["detail"]["tool"] == "query_logs"
    assert await pg_audit.spend_today_usd() == pytest.approx(0.010)


@needs_postgres
async def test_pg_audit_log_is_append_only(pg_audit):
    await pg_audit.record("x", diagnosis_id="d1")
    async with await pg_audit._connect() as conn:
        for statement in ("UPDATE audit_log SET event = 'tampered'", "DELETE FROM audit_log", "TRUNCATE audit_log"):
            with pytest.raises(psycopg.Error, match="append-only"):
                await conn.execute(statement)
    assert len(await pg_audit.events()) == 1


@needs_postgres
async def test_pg_kill_switch_persists_across_instances_and_is_audited(pg_audit):
    one = PgKillSwitch(pg_audit, schema=pg_audit.schema)
    await one.apply_schema()
    assert await one.engaged() is False
    await one.set(True, "ankur", "test")
    two = PgKillSwitch(pg_audit, schema=pg_audit.schema)  # a different process would see the same state
    assert await two.engaged() is True
    await two.set(False, "ankur", "done")
    assert [r["detail"]["engaged"] for r in await pg_audit.events(event="kill_switch_changed")] == [False, True]


async def test_kill_switch_fails_closed_when_the_database_is_unreachable():
    ks = PgKillSwitch(MemoryAuditLog(), dsn="postgresql://nobody@localhost:1/none")
    assert await ks.engaged() is True
