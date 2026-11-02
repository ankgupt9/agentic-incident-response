"""Order service: quotes a price from the pricing service, then publishes the order to RabbitMQ."""

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

import aio_pika
import httpx
from fastapi import FastAPI, HTTPException, Request
from opentelemetry import metrics
from pydantic import BaseModel, Field

from services.common.telemetry import instrument_app, setup_telemetry

SERVICE = "order-service"
QUEUE = "orders"
PRICING_URL = os.getenv("PRICING_URL", "http://pricing-service:8000")
RABBITMQ_URL = os.getenv("RABBITMQ_URL")

setup_telemetry(SERVICE)
logger = logging.getLogger(SERVICE)
meter = metrics.get_meter(SERVICE)
orders_submitted = meter.create_counter("orders_submitted", description="Order submissions, by outcome")
submit_duration = meter.create_histogram("order_submit_duration", unit="ms", description="POST /orders latency")


class OrderIn(BaseModel):
    sku: str
    quantity: int = Field(gt=0)
    customer_id: str


def make_publisher(channel: aio_pika.abc.AbstractChannel):
    async def publish(body: dict) -> None:
        message = aio_pika.Message(
            json.dumps(body).encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await channel.default_exchange.publish(message, routing_key=QUEUE)

    return publish


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=5)
    connection = None
    if RABBITMQ_URL:
        connection = await aio_pika.connect_robust(RABBITMQ_URL)
        channel = await connection.channel()
        await channel.declare_queue(QUEUE, durable=True)
        app.state.publisher = make_publisher(channel)
    yield
    await app.state.http.aclose()
    if connection:
        await connection.close()


app = FastAPI(title=SERVICE, lifespan=lifespan)
instrument_app(app)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/orders", status_code=202)
async def create_order(order: OrderIn, request: Request) -> dict:
    start = time.perf_counter()
    order_id = str(uuid.uuid4())
    try:
        resp = await request.app.state.http.get(f"{PRICING_URL}/quote/{order.sku}")
        resp.raise_for_status()
        quoted_price = resp.json()["unit_price"]
        await request.app.state.publisher({**order.model_dump(), "order_id": order_id, "quoted_price": quoted_price})
    except (httpx.HTTPError, aio_pika.exceptions.AMQPException) as exc:
        orders_submitted.add(1, {"status": "error"})
        logger.error("order submission failed order_id=%s sku=%s error=%r", order_id, order.sku, exc)
        raise HTTPException(status_code=502, detail="order submission failed") from exc
    finally:
        submit_duration.record((time.perf_counter() - start) * 1000)
    orders_submitted.add(1, {"status": "ok"})
    logger.info("order submitted order_id=%s sku=%s quantity=%s", order_id, order.sku, order.quantity)
    return {"order_id": order_id, "status": "accepted"}
