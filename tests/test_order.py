import httpx
import pytest
from fastapi.testclient import TestClient

from services.order_service.main import app

ORDER = {"sku": "SKU-1001", "quantity": 2, "customer_id": "c1"}


def pricing_transport(status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json={"sku": "SKU-1001", "unit_price": 19.99})

    return httpx.MockTransport(handler)


@pytest.fixture
def published():
    return []


@pytest.fixture
def client(published):
    async def publisher(body: dict) -> None:
        published.append(body)

    # No `with` block: lifespan (which needs RabbitMQ) is skipped; state is injected.
    app.state.publisher = publisher
    app.state.http = httpx.AsyncClient(transport=pricing_transport())
    return TestClient(app)


def test_order_is_quoted_and_published(client, published):
    resp = client.post("/orders", json=ORDER)
    assert resp.status_code == 202
    assert len(published) == 1
    assert published[0]["quoted_price"] == 19.99
    assert published[0]["order_id"] == resp.json()["order_id"]


def test_pricing_failure_returns_502_and_publishes_nothing(client, published):
    app.state.http = httpx.AsyncClient(transport=pricing_transport(status=500))
    assert client.post("/orders", json=ORDER).status_code == 502
    assert published == []


def test_invalid_quantity_rejected(client):
    assert client.post("/orders", json={**ORDER, "quantity": 0}).status_code == 422
