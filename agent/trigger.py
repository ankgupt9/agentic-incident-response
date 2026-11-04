"""Alert trigger: the agent's entry point. Receives Grafana webhooks and runs one diagnosis job
per firing alert.

Run: uvicorn agent.trigger:app --port 8004
"""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from agent.runner import Runner
from agent.schemas import Alert, DiagnosisResult

logger = logging.getLogger("agent.trigger")
DEDUPE_WINDOW_S = 600
MAX_JOBS = 200
MAX_CONCURRENT = 2


class DiagnosisJob(BaseModel):
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
    jobs: dict[str, DiagnosisJob] = {}
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

    app = FastAPI(title="agent-trigger", lifespan=lifespan)
    app.state.runner = runner
    app.state.jobs = jobs
    app.state.tasks = tasks

    async def execute(job: DiagnosisJob) -> None:
        async with slots:
            job.state = "running"
            try:
                job.result = await app.state.runner(job.alert)
            except Exception as exc:
                logger.exception("diagnosis crashed id=%s", job.id)
                job.result = DiagnosisResult(alert=job.alert, status="failed", error=f"{type(exc).__name__}: {exc}")
            job.state = "done"
            logger.info("diagnosis done id=%s alert=%s status=%s", job.id, job.alert.name, job.result.status)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/alerts", status_code=202)
    async def receive(request: Request) -> dict:
        started, skipped = [], 0
        now = time.time()
        for fingerprint, alert in parse_grafana(await request.json()):
            recent = any(r.fingerprint == fingerprint and now - r.created_at < DEDUPE_WINDOW_S for r in jobs.values())
            if recent:
                skipped += 1
                continue
            job = DiagnosisJob(id=uuid.uuid4().hex[:12], fingerprint=fingerprint, created_at=now, alert=alert)
            jobs[job.id] = job
            while len(jobs) > MAX_JOBS:
                jobs.pop(next(iter(jobs)))
            task = asyncio.create_task(execute(job))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            started.append(job.id)
        return {"started": started, "skipped_duplicates": skipped}

    @app.get("/diagnoses")
    async def list_diagnoses() -> list[dict]:
        return [
            {"id": r.id, "alert": r.alert.name, "state": r.state, "status": r.result.status if r.result else None,
             "root_cause": r.result.diagnosis.root_cause if r.result and r.result.diagnosis else None}
            for r in reversed(jobs.values())
        ]

    @app.get("/diagnoses/{diagnosis_id}")
    async def get_diagnosis(diagnosis_id: str) -> DiagnosisJob:
        if diagnosis_id not in jobs:
            raise HTTPException(status_code=404, detail="unknown diagnosis")
        return jobs[diagnosis_id]

    return app


app = create_app()
