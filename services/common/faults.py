"""Runtime fault switches for the simulated services.

The admin endpoints are mounted only when ENABLE_FAULT_INJECTION=true, so a
service started without that flag has no way to be broken from outside.
"""

import logging
import os

from fastapi import APIRouter, FastAPI, HTTPException

logger = logging.getLogger("faults")


class FaultRegistry:
    def __init__(self, known: set[str]):
        self.known = known
        self._active: dict[str, dict] = {}

    def set(self, name: str, params: dict | None = None) -> None:
        if name not in self.known:
            raise KeyError(name)
        self._active[name] = params or {}

    def clear(self, name: str | None = None) -> None:
        if name is None:
            self._active.clear()
        else:
            self._active.pop(name, None)

    def get(self, name: str) -> dict | None:
        """Params of the fault if it is active, else None."""
        return self._active.get(name)

    def active(self) -> dict[str, dict]:
        return dict(self._active)


def mount_fault_admin(app: FastAPI, registry: FaultRegistry) -> None:
    if os.getenv("ENABLE_FAULT_INJECTION", "").lower() != "true":
        return
    router = APIRouter(prefix="/admin/faults")

    @router.get("")
    async def list_faults() -> dict:
        return {"known": sorted(registry.known), "active": registry.active()}

    @router.put("/{name}")
    async def set_fault(name: str, params: dict | None = None) -> dict:
        try:
            registry.set(name, params)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown fault {name}") from None
        logger.warning("fault enabled name=%s params=%s", name, params or {})
        return {"active": registry.active()}

    @router.delete("")
    async def clear_faults(name: str | None = None) -> dict:
        registry.clear(name)
        logger.warning("fault cleared name=%s", name or "all")
        return {"active": registry.active()}

    app.include_router(router)
