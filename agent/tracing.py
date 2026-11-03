"""Agent observability: OpenTelemetry spans and metrics for every diagnosis, model call and tool call.

The wrappers decorate the gateway and toolbox, so the loop itself is untouched. Spans carry
OpenTelemetry GenAI attributes plus OpenInference ones (which Phoenix uses to render LLM/TOOL/AGENT
spans). Metrics feed the "AI health" Grafana dashboard through the same collector as the services.
"""

import json
import os
import time
from contextlib import contextmanager

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode

from agent.gateway import GatewayError, ModelGateway, ModelResponse
from agent.schemas import Alert, DiagnosisResult
from agent.toolbox import Toolbox, ToolResult

SERVICE = "incident-agent"
MAX_CONTENT_CHARS = 4000


def _clip(text: str) -> str:
    return text if len(text) <= MAX_CONTENT_CHARS else text[:MAX_CONTENT_CHARS] + "…[truncated]"


class AgentTelemetry:
    def __init__(self, tracer_provider: TracerProvider, meter_provider: MeterProvider, capture_content: bool = True):
        self.tracer = tracer_provider.get_tracer(SERVICE)
        self.capture_content = capture_content
        meter = meter_provider.get_meter(SERVICE)
        self.diagnoses = meter.create_counter("agent_diagnoses", description="Diagnoses finished, by status")
        self.diagnosis_duration = meter.create_histogram("agent_diagnosis_duration", unit="s", description="Diagnosis wall time")
        self.model_calls = meter.create_counter("agent_model_calls", description="Model calls, by model and outcome")
        self.model_duration = meter.create_histogram("agent_model_call_duration", unit="s", description="Model call latency")
        self.tokens = meter.create_counter("agent_tokens", description="Tokens used, by type and model")
        self.cost = meter.create_counter("agent_cost_usd", description="Model spend in USD")
        self.tool_calls = meter.create_counter("agent_tool_calls", description="Tool calls, by tool and outcome")
        self.guardrail_blocks = meter.create_counter("agent_guardrail_blocks", description="Actions blocked by guardrails (Day 3)")

    def content(self, text: str) -> str:
        return _clip(text) if self.capture_content else "[content capture disabled]"

    @contextmanager
    def diagnosis_span(self, alert: Alert):
        with self.tracer.start_as_current_span(f"diagnose {alert.name}") as span:
            span.set_attribute("openinference.span.kind", "AGENT")
            span.set_attribute("agent.alert.name", alert.name)
            span.set_attribute("input.value", self.content(f"{alert.name}: {alert.summary}"))
            yield span

    def record_diagnosis(self, span, result: DiagnosisResult) -> None:
        span.set_attribute("agent.status", result.status)
        span.set_attribute("agent.steps", result.steps)
        span.set_attribute("agent.tool_calls", result.tool_calls)
        span.set_attribute("agent.cost_usd", result.cost_usd)
        if result.diagnosis:
            span.set_attribute("agent.confidence", result.diagnosis.confidence)
            span.set_attribute("agent.category", result.diagnosis.category)
            span.set_attribute("output.value", self.content(result.diagnosis.root_cause))
        if result.status == "failed":
            span.set_status(Status(StatusCode.ERROR, result.error or "diagnosis failed"))
        self.diagnoses.add(1, {"status": result.status})
        self.diagnosis_duration.record(result.latency_s)


class TracedGateway:
    """Wraps any ModelGateway; one CLIENT span and metric sample per model call."""

    def __init__(self, inner: ModelGateway, telemetry: AgentTelemetry, model: str):
        self._inner, self._tel, self._model = inner, telemetry, model

    async def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> ModelResponse:
        tel = self._tel
        with tel.tracer.start_as_current_span(f"chat {self._model}", kind=SpanKind.CLIENT) as span:
            span.set_attribute("openinference.span.kind", "LLM")
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.system", "anthropic")
            span.set_attribute("gen_ai.request.model", self._model)
            span.set_attribute("llm.model_name", self._model)
            span.set_attribute("agent.messages_in", len(messages))
            span.set_attribute("input.value", tel.content(json.dumps(messages[-1], default=str)))
            start = time.perf_counter()
            try:
                resp = await self._inner.complete(system=system, messages=messages, tools=tools)
            except GatewayError as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                tel.model_calls.add(1, {"model": self._model, "outcome": "error"})
                tel.model_duration.record(time.perf_counter() - start, {"model": self._model})
                raise
            u = resp.usage
            span.set_attribute("gen_ai.response.model", resp.model)
            span.set_attribute("gen_ai.response.finish_reasons", [resp.stop_reason])
            span.set_attribute("gen_ai.usage.input_tokens", u.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", u.output_tokens)
            span.set_attribute("llm.token_count.prompt", u.input_tokens)
            span.set_attribute("llm.token_count.completion", u.output_tokens)
            span.set_attribute("llm.token_count.total", u.input_tokens + u.output_tokens)
            span.set_attribute("agent.cost_usd", resp.cost_usd)
            if resp.request_id:
                span.set_attribute("gen_ai.response.id", resp.request_id)
            requested = ", ".join(f"{c.name}({json.dumps(c.input, default=str)})" for c in resp.tool_calls)
            span.set_attribute("output.value", tel.content(resp.text or requested))
            labels = {"model": self._model}
            tel.model_calls.add(1, {**labels, "outcome": "ok"})
            tel.model_duration.record(resp.latency_s, labels)
            tel.tokens.add(u.input_tokens, {**labels, "type": "input"})
            tel.tokens.add(u.output_tokens, {**labels, "type": "output"})
            tel.cost.add(resp.cost_usd, labels)
            return resp


class TracedToolbox:
    """Wraps any Toolbox; one span and metric sample per tool call."""

    def __init__(self, inner: Toolbox, telemetry: AgentTelemetry):
        self._inner, self._tel = inner, telemetry

    def specs(self) -> list[dict]:
        return self._inner.specs()

    async def call(self, name: str, args: dict) -> ToolResult:
        tel = self._tel
        with tel.tracer.start_as_current_span(f"execute_tool {name}") as span:
            span.set_attribute("openinference.span.kind", "TOOL")
            span.set_attribute("gen_ai.operation.name", "execute_tool")
            span.set_attribute("gen_ai.tool.name", name)
            span.set_attribute("tool.name", name)
            span.set_attribute("input.value", tel.content(json.dumps(args, default=str)))
            result = await self._inner.call(name, args)
            span.set_attribute("output.value", tel.content(result.text))
            span.set_attribute("agent.tool.error", result.is_error)
            if result.is_error:
                span.set_status(Status(StatusCode.ERROR, result.text[:200]))
            tel.tool_calls.add(1, {"tool": name, "outcome": "error" if result.is_error else "ok"})
            return result


def setup_agent_telemetry() -> AgentTelemetry:
    """Real exporters: traces to the collector (-> Tempo) and Phoenix; metrics to the collector."""
    collector = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    phoenix = os.getenv("PHOENIX_URL", "http://localhost:6006")
    resource = Resource.create({"service.name": SERVICE})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{collector}/v1/traces")))
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{phoenix}/v1/traces")))
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{collector}/v1/metrics"), export_interval_millis=5000)],
    )
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(meter_provider)
    return AgentTelemetry(tracer_provider, meter_provider, capture_content=os.getenv("AGENT_TRACE_CONTENT", "true").lower() == "true")
