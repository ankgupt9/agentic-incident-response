import json

import httpx
import pytest
from fastapi.testclient import TestClient

from simulator.order_service import main as order
from simulator.pricing_service import main as pricing
from simulator.pricing_service.price_list import price_order


@pytest.fixture(autouse=True)
def clean():
    yield
    order.faults.clear()
    pricing.faults.clear()


def test_quote_error_fault():
    pricing.faults.set("quote_error", {"status": 503})
    assert TestClient(pricing.app).get("/quote/SKU-1001").status_code == 503


def test_sku_missing_fault_only_affects_that_sku():
    pricing.faults.set("sku_missing", {"sku": "SKU-1003"})
    client = TestClient(pricing.app)
    assert client.get("/quote/SKU-1003").status_code == 404
    assert client.get("/quote/SKU-1001").status_code == 200


def test_stale_quotes_cause_consumer_price_mismatch():
    pricing.faults.set("stale_quotes", {"factor": 1.1})
    price = TestClient(pricing.app).get("/quote/SKU-1001").json()["unit_price"]
    with pytest.raises(ValueError, match="price mismatch"):
        price_order({"sku": "SKU-1001", "quantity": 1, "quoted_price": price})


def test_schema_drift_renames_sku():
    order.faults.set("schema_drift")
    body = order.apply_publish_faults({"sku": "SKU-1001", "order_id": "o"})
    assert "sku" not in body and body["item_sku"] == "SKU-1001"


def test_malformed_payload_is_not_valid_json():
    order.faults.set("malformed_payload")
    body = order.apply_publish_faults({"order_id": "o1"})
    with pytest.raises(json.JSONDecodeError):
        json.loads(body)


def test_publish_error_surfaces_as_502():
    order.faults.set("publish_error")
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"unit_price": 1.0}))
    order.app.state.http = httpx.AsyncClient(transport=transport)
    order.app.state.publisher = lambda body: None
    resp = TestClient(order.app).post("/orders", json={"sku": "SKU-1001", "quantity": 1, "customer_id": "c"})
    assert resp.status_code == 502


def test_no_faults_means_body_untouched():
    body = {"sku": "SKU-1001", "order_id": "o"}
    assert order.apply_publish_faults(dict(body)) == body


def test_admin_endpoints_absent_unless_enabled(monkeypatch):
    # Test env doesn't set ENABLE_FAULT_INJECTION, so the routes were never mounted.
    assert TestClient(pricing.app).get("/admin/faults").status_code == 404


def test_toggling_faults_is_silent(monkeypatch, caplog):
    """A log line naming the fault would hand the agent the answer (it reads logs through Loki)."""
    from fastapi import FastAPI

    from simulator.common.faults import FaultRegistry, mount_fault_admin

    monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
    app = FastAPI()
    mount_fault_admin(app, FaultRegistry({"stale_quotes"}))
    client = TestClient(app)
    with caplog.at_level("DEBUG"):
        client.put("/admin/faults/stale_quotes", json={"factor": 1.1})
        client.delete("/admin/faults")
    # Ignore the test client's own request logging (httpx) and event-loop noise; only the service's logs count.
    service_logs = [r for r in caplog.records if r.name not in ("httpx", "httpx2", "asyncio")]
    assert [r.getMessage() for r in service_logs] == []


def test_error_texts_do_not_reveal_that_a_fault_was_injected():
    order.faults.set("publish_error")
    with pytest.raises(Exception) as exc:
        order.apply_publish_faults({"sku": "SKU-1001", "order_id": "o"})
    assert "inject" not in str(exc.value).lower()
    pricing.faults.set("quote_error", {"status": 503})
    detail = TestClient(pricing.app).get("/quote/SKU-1001").json()["detail"]
    assert "inject" not in detail.lower()


def test_fault_admin_calls_are_excluded_from_tracing():
    import inspect

    from simulator.common import telemetry

    assert "/admin/faults" in inspect.getsource(telemetry.instrument_app)
