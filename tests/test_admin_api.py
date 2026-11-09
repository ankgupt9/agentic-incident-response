import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI

from agent.actions import ActionService, Executor, MemoryApprovalStore, ProposedAction
from agent.admin_api import APPROVAL_PAGE, build_admin_router
from agent.audit import MemoryAuditLog
from agent.killswitch import MemoryKillSwitch
from tests.conftest import needs_postgres

ORDER = {"order_id": "o1", "sku": "SKU-1001", "quantity": 2, "customer_id": "c1", "quoted_price": 19.99}
TOKEN = {"X-Admin-Token": "s3cret"}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AGENT_ADMIN_TOKEN", "s3cret")
    audit = MemoryAuditLog()
    service = ActionService(MemoryApprovalStore(), audit, Executor(dry_run=True))
    kill = MemoryKillSwitch(audit)
    app = FastAPI()
    app.include_router(build_admin_router(service, kill, audit))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return client, service, kill, audit


async def propose(service):
    return await service.propose("d1", "replay_message", {"message_json": json.dumps(ORDER)}, "test")


async def test_admin_endpoints_are_disabled_when_no_token_is_configured(env, monkeypatch):
    client, *_ = env
    monkeypatch.delenv("AGENT_ADMIN_TOKEN")
    assert (await client.get("/approvals", headers=TOKEN)).status_code == 503
    assert (await client.post("/admin/kill-switch", json={"engaged": True, "actor": "a", "reason": "r"}, headers=TOKEN)).status_code == 503


@pytest.mark.parametrize("headers", [{}, {"X-Admin-Token": "wrong"}])
async def test_wrong_or_missing_token_is_rejected(env, headers):
    client, service, *_ = env
    a = await propose(service)
    for call in (client.get("/approvals", headers=headers), client.post(f"/approvals/{a.id}/approve", json={"actor": "ankur"}, headers=headers)):
        assert (await call).status_code == 401
    assert (await service.store.get(a.id)).status == "pending"


async def test_list_approve_and_reject_through_the_api(env):
    client, service, _, audit = env
    a, b = await propose(service), await propose(service)
    listed = (await client.get("/approvals", params={"status": "pending"}, headers=TOKEN)).json()
    assert {x["id"] for x in listed} == {a.id, b.id}
    ok = await client.post(f"/approvals/{a.id}/approve", json={"actor": "ankur", "comment": "ok"}, headers=TOKEN)
    assert ok.status_code == 200 and ok.json()["status"] == "executed" and ok.json()["result"].startswith("DRY RUN")
    no = await client.post(f"/approvals/{b.id}/reject", json={"actor": "ankur"}, headers=TOKEN)
    assert no.json()["status"] == "rejected"
    assert (await client.post(f"/approvals/{a.id}/approve", json={"actor": "ankur"}, headers=TOKEN)).status_code == 409
    assert len(audit.of("approval_decided")) == 2


async def test_the_agent_actor_cannot_approve_through_the_api(env):
    client, service, *_ = env
    a = await propose(service)
    assert (await client.post(f"/approvals/{a.id}/approve", json={"actor": "agent"}, headers=TOKEN)).status_code == 409


async def test_kill_switch_can_be_read_and_changed_and_is_audited(env):
    client, _, kill, audit = env
    assert (await client.get("/admin/kill-switch", headers=TOKEN)).json() == {"engaged": False}
    await client.post("/admin/kill-switch", json={"engaged": True, "actor": "ankur", "reason": "cost spike"}, headers=TOKEN)
    assert await kill.engaged() is True
    assert audit.of("kill_switch_changed")[0]["detail"]["reason"] == "cost spike"


async def test_the_approval_page_needs_no_token_and_never_uses_innerhtml(env):
    client, *_ = env
    page = await client.get("/approvals/ui")
    assert page.status_code == 200 and "Proposed actions" in page.text
    assert "innerHTML" not in APPROVAL_PAGE and "textContent" in APPROVAL_PAGE
    assert "s3cret" not in page.text and "?token" not in APPROVAL_PAGE


@needs_postgres
async def test_pg_approval_store_decides_exactly_once_and_survives_reads(pg_approvals):
    store = pg_approvals
    a = ProposedAction(diagnosis_id="d1", tool="replay_message", args={"queue": "orders.sandbox"}, reason="r")
    await store.create(a)
    assert (await store.get(a.id)).status == "pending"
    first = await store.decide(a.id, True, "ankur", "ok")
    second = await store.decide(a.id, True, "someone-else", None)
    assert first.status == "approved" and first.decided_by == "ankur" and second is None
    await store.finish(a.id, True, "DRY RUN: would publish")
    done = await store.get(a.id)
    assert done.status == "executed" and done.result.startswith("DRY RUN")
    assert [x.id for x in await store.list("executed")] == [a.id] and await store.list("pending") == []


@needs_postgres
async def test_pg_approval_store_refuses_to_decide_an_expired_action(pg_approvals):
    a = ProposedAction(diagnosis_id="d1", tool="replay_message", args={}, reason="r", expires_at=datetime.now(timezone.utc) - timedelta(seconds=5))
    await pg_approvals.create(a)
    assert await pg_approvals.decide(a.id, True, "ankur", None) is None
    assert (await pg_approvals.get(a.id)).status == "pending"
