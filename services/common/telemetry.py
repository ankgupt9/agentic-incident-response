"""Shared OpenTelemetry setup: traces, metrics and logs all go to the OTel Collector.

If OTEL_EXPORTER_OTLP_ENDPOINT is unset (unit tests, bare local runs) nothing is
exported and the services only log to the console.
"""

import logging
import os

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.aio_pika import AioPikaInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def setup_telemetry(service_name: str) -> bool:
    """Configure logging and, when a collector is configured, OTel exporters.

    Returns True if exporters were enabled.
    """
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False

    resource = Resource.create({SERVICE_NAME: service_name})

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=5000)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(logger_provider)
    # The handler stamps each log record with the active trace_id/span_id,
    # which is what lets Loki logs link to Tempo traces.
    logging.getLogger().addHandler(LoggingHandler(level=logging.INFO, logger_provider=logger_provider))

    HTTPXClientInstrumentor().instrument()
    AioPikaInstrumentor().instrument()
    return True


def instrument_app(app) -> None:
    FastAPIInstrumentor.instrument_app(app)
