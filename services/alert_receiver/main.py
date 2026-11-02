"""Stub for the Day 2 agent: receives Grafana alert webhooks and logs them."""

import logging
from collections import deque

from fastapi import FastAPI, Request

from services.common.telemetry import setup_telemetry

SERVICE = "alert-receiver"
setup_telemetry(SERVICE)
logger = logging.getLogger(SERVICE)

app = FastAPI(title=SERVICE)
received: deque[dict] = deque(maxlen=50)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/alerts")
async def alerts(request: Request) -> dict:
    payload = await request.json()
    received.append(payload)
    for alert in payload.get("alerts", []):
        logger.warning(
            "alert %s status=%s labels=%s", alert.get("labels", {}).get("alertname"), alert.get("status"), alert.get("labels")
        )
    return {"received": len(payload.get("alerts", []))}


@app.get("/alerts/recent")
async def recent() -> list[dict]:
    return list(received)


@app.delete("/alerts/recent")
async def reset() -> dict:
    received.clear()
    return {"received": 0}
