"""Alert trigger: receives Grafana webhooks and runs a diagnosis per firing alert.

Run: uvicorn agent.service:app --port 8004
"""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from agent.runner import Runner
from agent.schemas import Alert, DiagnosisResult

logger = logging.getLogger("agent.service")
DEDUPE_WINDOW_S = 600
MAX_RECORDS = 200
MAX_CONCURRENT = 2


class DiagnosisRecord(BaseModel):
    id: str
    fingerprint: str
    created_at: float
    state: str = "queued"  # queued | running | done
    alert: Alert
    result: DiagnosisResult | None = None


def parse_grafana(payload: dict) -> list[tuple[str, Alert]]:
    """Firing alerts from a Grafana webhook payload as (fingerprint, Alert). Resolved ones are skipped."""
    out = []
    for a in payload.get("alerts", []):
        if a.get("status") != "firing":
            continue
        labels = a.get("labels", {})
        fingerprint = a.get("fingerprint") or hashlib.sha1(json.dumps(labels, sort_keys=True).encode()).hexdigest()[:16]
        alert = Alert(
            name=labels.get("alertname", "unknown"),
            status="firing",
            labels=labels,
            summary=a.get("annotations", {}).get("summary", ""),
            starts_at=a.get("startsAt", ""),
        )
        out.append((fingerprint, alert))
    return out


def create_app(runner: Runner | None = None) -> FastAPI:
    records: dict[str, DiagnosisRecord] = {}
    tasks: set[asyncio.Task] = set()
    slots = asyncio.Semaphore(MAX_CONCURRENT)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.runner is None:
            from agent.runner import build_runner  # deferred: needs an API key and telemetry exporters

            app.state.runner = build_runner()
        yield
        for t in tasks:
            t.cancel()

    app = FastAPI(title="incident-agent", lifespan=lifespan)
    app.state.runner = runner
    app.state.records = records
    app.state.tasks = tasks

    async def execute(record: DiagnosisRecord) -> None:
        async with slots:
            record.state = "running"
            try:
                record.result = await app.state.runner(record.alert)
            except Exception as exc:
                logger.exception("diagnosis crashed id=%s", record.id)
                record.result = DiagnosisResult(alert=record.alert, status="failed", error=f"{type(exc).__name__}: {exc}")
            record.state = "done"
            logger.info("diagnosis done id=%s alert=%s status=%s", record.id, record.alert.name, record.result.status)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/alerts", status_code=202)
    async def receive(request: Request) -> dict:
        started, skipped = [], 0
        now = time.time()
        for fingerprint, alert in parse_grafana(await request.json()):
            recent = any(r.fingerprint == fingerprint and now - r.created_at < DEDUPE_WINDOW_S for r in records.values())
            if recent:
                skipped += 1
                continue
            record = DiagnosisRecord(id=uuid.uuid4().hex[:12], fingerprint=fingerprint, created_at=now, alert=alert)
            records[record.id] = record
            while len(records) > MAX_RECORDS:
                records.pop(next(iter(records)))
            task = asyncio.create_task(execute(record))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            started.append(record.id)
        return {"started": started, "skipped_duplicates": skipped}

    @app.get("/diagnoses")
    async def list_diagnoses() -> list[dict]:
        return [
            {"id": r.id, "alert": r.alert.name, "state": r.state, "status": r.result.status if r.result else None,
             "root_cause": r.result.diagnosis.root_cause if r.result and r.result.diagnosis else None}
            for r in reversed(records.values())
        ]

    @app.get("/diagnoses/{diagnosis_id}")
    async def get_diagnosis(diagnosis_id: str) -> DiagnosisRecord:
        if diagnosis_id not in records:
            raise HTTPException(status_code=404, detail="unknown diagnosis")
        return records[diagnosis_id]

    return app


app = create_app()
