"""HTTP clients for the observability backends. Read-only: GET requests only."""

import os
from dataclasses import dataclass, field

import httpx


@dataclass
class TelemetryBackends:
    loki_url: str = field(default_factory=lambda: os.getenv("LOKI_URL", "http://localhost:3100"))
    tempo_url: str = field(default_factory=lambda: os.getenv("TEMPO_URL", "http://localhost:3200"))
    prometheus_url: str = field(default_factory=lambda: os.getenv("PROMETHEUS_URL", "http://localhost:9090"))
    http: httpx.AsyncClient = field(default_factory=lambda: httpx.AsyncClient(timeout=10))

    async def get(self, base: str, path: str, params: dict | None = None) -> httpx.Response:
        return await self.http.get(f"{base}{path}", params=params)
