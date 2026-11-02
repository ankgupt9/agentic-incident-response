import httpx
import pytest

from services.common.faults import FaultRegistry
from services.failure_injector.injector import clear_all, inject, load_catalog
from services.order_service.main import faults as order_faults
from services.pricing_service.main import faults as pricing_faults

KNOWN = {"order-service": order_faults.known, "pricing-service": pricing_faults.known}


def test_catalog_loads_and_every_fault_exists_in_its_service():
    catalog = load_catalog()
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
    inject(load_catalog()["F03"], client, urls)
    clear_all(client, urls)
    assert calls == [
        ("PUT", "/admin/faults/stale_quotes"),
        ("DELETE", "/admin/faults"),
        ("DELETE", "/admin/faults"),
    ]


def test_registry_rejects_unknown_fault():
    with pytest.raises(KeyError):
        FaultRegistry({"a"}).set("b")
