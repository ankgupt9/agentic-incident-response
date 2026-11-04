import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from simulator.pricing_service.price_list import price_order
from simulator.pricing_service.main import app, handle_message


def test_quote_known_sku():
    resp = TestClient(app).get("/quote/SKU-1001")
    assert resp.status_code == 200
    assert resp.json() == {"sku": "SKU-1001", "unit_price": 19.99}


def test_quote_unknown_sku_is_404():
    assert TestClient(app).get("/quote/NOPE").status_code == 404


def test_price_order_total():
    order = {"sku": "SKU-1003", "quantity": 4, "quoted_price": 5.25}
    assert price_order(order) == 21.0


def test_price_order_detects_price_drift():
    with pytest.raises(ValueError, match="price mismatch"):
        price_order({"sku": "SKU-1003", "quantity": 1, "quoted_price": 9.99})


def test_price_order_unknown_sku():
    with pytest.raises(ValueError, match="unknown sku"):
        price_order({"sku": "NOPE", "quantity": 1, "quoted_price": 1.0})


def _message(body: dict | bytes) -> AsyncMock:
    msg = AsyncMock()
    msg.body = body if isinstance(body, bytes) else json.dumps(body).encode()
    return msg


async def test_handle_message_acks_valid_order():
    msg = _message({"order_id": "o1", "sku": "SKU-1001", "quantity": 1, "quoted_price": 19.99})
    await handle_message(msg)
    msg.ack.assert_awaited_once()
    msg.reject.assert_not_awaited()


async def test_handle_message_rejects_bad_order_without_requeue():
    msg = _message({"order_id": "o2", "sku": "SKU-1001", "quantity": 1, "quoted_price": 1.0})
    await handle_message(msg)
    msg.reject.assert_awaited_once_with(requeue=False)
    msg.ack.assert_not_awaited()


async def test_handle_message_rejects_malformed_json():
    msg = _message(b"not json")
    await handle_message(msg)
    msg.reject.assert_awaited_once_with(requeue=False)
