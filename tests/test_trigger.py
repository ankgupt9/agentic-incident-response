import asyncio

import httpx

from agent.schemas import DiagnosisResult
from agent.trigger import create_app, parse_grafana
from tests.test_schemas import VALID
from agent.schemas import Diagnosis

PAYLOAD = {"alerts": [
    {"status": "firing", "fingerprint": "abc", "labels": {"alertname": "OrderSubmitErrorRateHigh", "severity": "critical"},
     "annotations": {"summary": "error ratio > 20%"}, "startsAt": "2026-11-03T10:00:00Z"},
    {"status": "resolved", "fingerprint": "def", "labels": {"alertname": "OrderQueueBacklog"}},
]}


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def drain(app) -> None:
    await asyncio.gather(*list(app.state.tasks))


def test_parse_grafana_keeps_only_firing_alerts():
    parsed = parse_grafana(PAYLOAD)
    assert len(parsed) == 1
    fingerprint, alert = parsed[0]
    assert fingerprint == "abc" and alert.name == "OrderSubmitErrorRateHigh" and alert.summary == "error ratio > 20%"


async def test_alert_triggers_a_diagnosis_and_result_is_listed():
    async def runner(alert):
        return DiagnosisResult(alert=alert, status="complete", diagnosis=Diagnosis.model_validate(VALID))

    app = create_app(runner)
    async with client(app) as c:
        resp = await c.post("/alerts", json=PAYLOAD)
        assert resp.status_code == 202 and len(resp.json()["started"]) == 1
        await drain(app)
        listing = (await c.get("/diagnoses")).json()
        assert listing[0]["state"] == "done" and listing[0]["status"] == "complete"
        detail = (await c.get(f"/diagnoses/{listing[0]['id']}")).json()
        assert detail["result"]["diagnosis"]["category"] == "data_inconsistency"


async def test_repeat_notification_for_same_alert_is_not_rediagnosed():
    calls = []

    async def runner(alert):
        calls.append(alert.name)
        return DiagnosisResult(alert=alert, status="failed", error="x")

    app = create_app(runner)
    async with client(app) as c:
        await c.post("/alerts", json=PAYLOAD)
        second = await c.post("/alerts", json=PAYLOAD)
        await drain(app)
    assert calls == ["OrderSubmitErrorRateHigh"] and second.json()["skipped_duplicates"] == 1


async def test_runner_crash_is_recorded_as_failed_not_raised():
    async def runner(alert):
        raise NotImplementedError("loop not written")

    app = create_app(runner)
    async with client(app) as c:
        await c.post("/alerts", json=PAYLOAD)
        await drain(app)
        item = (await c.get("/diagnoses")).json()[0]
    assert item["status"] == "failed"
    job = next(iter(app.state.jobs.values()))
    assert "NotImplementedError" in job.result.error


async def test_unknown_diagnosis_is_404():
    async with client(create_app(lambda a: None)) as c:
        assert (await c.get("/diagnoses/nope")).status_code == 404
