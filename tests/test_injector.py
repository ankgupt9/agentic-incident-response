import httpx
import pytest

from simulator.common.faults import FaultRegistry
from simulator.failure_injector.injector import clear_all, inject, load_failure_catalog
from simulator.order_service.main import faults as order_faults
from simulator.pricing_service.main import faults as pricing_faults

KNOWN = {"order-service": order_faults.known, "pricing-service": pricing_faults.known}


def test_catalog_loads_and_every_fault_exists_in_its_service():
    catalog = load_failure_catalog()
    assert len(catalog) >= 8
    for failure in catalog.values():
        assert failure.fault.name in KNOWN[failure.fault.service], failure.id


def test_inject_and_clear_hit_the_right_admin_endpoints():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    urls = {"order-service": "http://o", "pricing-service": "http://p"}
    inject(load_failure_catalog()["F03"], client, urls)
    clear_all(client, urls)
    assert calls == [
        ("PUT", "/admin/faults/stale_quotes"),
        ("DELETE", "/admin/faults"),
        ("DELETE", "/admin/faults"),
    ]


def test_registry_rejects_unknown_fault():
    with pytest.raises(KeyError):
        FaultRegistry({"a"}).set("b")


def test_change_records_are_published_to_the_knowledge_store_and_failure_to_do_so_is_reported(monkeypatch):
    from datetime import datetime, timezone

    from simulator.failure_injector.change_feed import ChangeEvent
    from simulator.failure_injector.injector import publish_change_events

    event = ChangeEvent(id="CHG-1", ts=datetime(2026, 11, 10, tzinfo=timezone.utc).isoformat(), service="pricing-service", kind="config", summary="x", actor="a")
    seen = []

    class FakeStore:
        async def import_change_events(self, records):
            seen.extend(r.id for r in records)

    monkeypatch.setattr("knowledge_store.db.KnowledgeStore", FakeStore)
    assert publish_change_events([event]) == "published 1 change record(s) to the knowledge store" and seen == ["CHG-1"]

    class Down:
        async def import_change_events(self, records):
            raise ConnectionError("no database")

    monkeypatch.setattr("knowledge_store.db.KnowledgeStore", Down)
    assert "feed file only" in publish_change_events([event])
