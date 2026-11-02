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

from services.common.telemetry import instrument_app, setup_telemetry
from services.pricing_service.catalog import get_price, price_order

SERVICE = "pricing-service"
QUEUE = "orders"
RABBITMQ_URL = os.getenv("RABBITMQ_URL")

setup_telemetry(SERVICE)
logger = logging.getLogger(SERVICE)
meter = metrics.get_meter(SERVICE)
orders_priced = meter.create_counter("orders_priced", description="Orders consumed from the queue, by outcome")


async def handle_message(message: AbstractIncomingMessage) -> None:
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
        await queue.consume(handle_message)
        logger.info("consuming queue=%s", QUEUE)
    yield
    if connection:
        await connection.close()


app = FastAPI(title=SERVICE, lifespan=lifespan)
instrument_app(app)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/quote/{sku}")
async def quote(sku: str) -> dict:
    try:
        price = get_price(sku)
    except KeyError:
        logger.warning("quote requested for unknown sku=%s", sku)
        raise HTTPException(status_code=404, detail=f"unknown sku {sku}") from None
    return {"sku": sku, "unit_price": price}
