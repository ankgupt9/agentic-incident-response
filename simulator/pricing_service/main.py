"""Pricing service: serves quotes over HTTP and prices orders consumed from RabbitMQ."""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

import aio_pika
from aio_pika.abc import AbstractIncomingMessage
from fastapi import FastAPI, HTTPException
from opentelemetry import metrics

from simulator.common.faults import FaultRegistry, mount_fault_admin
from simulator.common.telemetry import instrument_app, setup_telemetry
from simulator.pricing_service.price_list import get_price, price_order

SERVICE = "pricing-service"
QUEUE = "orders"
RABBITMQ_URL = os.getenv("RABBITMQ_URL")

setup_telemetry(SERVICE)
logger = logging.getLogger(SERVICE)
faults = FaultRegistry({"quote_error", "quote_delay", "stale_quotes", "sku_missing", "consumer_delay"})
meter = metrics.get_meter(SERVICE)
orders_priced = meter.create_counter("orders_priced", description="Orders consumed from the queue, by outcome")


async def handle_message(message: AbstractIncomingMessage) -> None:
    if (fault := faults.get("consumer_delay")) is not None:
        await asyncio.sleep(fault.get("seconds", 5))
    try:
        order = json.loads(message.body)
        total = price_order(order)
    except Exception:
        orders_priced.add(1, {"status": "error"})
        logger.exception("failed to price order")
        await message.reject(requeue=False)
        return
    orders_priced.add(1, {"status": "ok"})
    logger.info("order priced order_id=%s sku=%s total=%s", order["order_id"], order["sku"], total)
    await message.ack()


@asynccontextmanager
async def lifespan(app: FastAPI):
    connection = None
    if RABBITMQ_URL:
        connection = await aio_pika.connect_robust(RABBITMQ_URL)
        channel = await connection.channel()
        queue = await channel.declare_queue(QUEUE, durable=True)
        await channel.set_qos(prefetch_count=1)
        await queue.consume(handle_message)
        logger.info("consuming queue=%s", QUEUE)
    yield
    if connection:
        await connection.close()


app = FastAPI(title=SERVICE, lifespan=lifespan)
instrument_app(app)
mount_fault_admin(app, faults)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/quote/{sku}")
async def quote(sku: str) -> dict:
    if (fault := faults.get("quote_error")) is not None:
        raise HTTPException(status_code=fault.get("status", 503), detail="price source unavailable")
    if (fault := faults.get("quote_delay")) is not None:
        await asyncio.sleep(fault.get("seconds", 6))
    try:
        if (fault := faults.get("sku_missing")) is not None and fault.get("sku") == sku:
            raise KeyError(sku)
        price = get_price(sku)
    except KeyError:
        logger.warning("quote requested for unknown sku=%s", sku)
        raise HTTPException(status_code=404, detail=f"unknown sku {sku}") from None
    if (fault := faults.get("stale_quotes")) is not None:
        # Quote cache is out of sync with the master price list used by the consumer.
        price = round(price * fault.get("factor", 1.1), 2)
    return {"sku": sku, "unit_price": price}
