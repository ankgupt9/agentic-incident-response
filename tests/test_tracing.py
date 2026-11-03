import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent.gateway import FakeGateway, GatewayError, text_response, tool_use_response
from agent.schemas import Alert, Diagnosis, DiagnosisResult
from agent.toolbox import FakeToolbox
from agent.tracing import AgentTelemetry, TracedGateway, TracedToolbox
from tests.test_schemas import VALID


@pytest.fixture
def tel():
    exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(exporter))
    return AgentTelemetry(tp, MeterProvider(metric_readers=[reader])), exporter, reader


def metric_points(reader) -> dict[str, list]:
    out: dict[str, list] = {}
    data = reader.get_metrics_data()
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                out[m.name] = list(m.data.data_points)
    return out


async def test_model_call_emits_genai_span_and_metrics(tel):
    telemetry, spans, reader = tel
    gw = TracedGateway(FakeGateway([tool_use_response(("t1", "query_logs", {"service": "order-service"}))]), telemetry, "claude-haiku-5-5")
    await gw.complete(system="s", messages=[{"role": "user", "content": "hi"}], tools=[])
    (span,) = spans.get_finished_spans()
    a = span.attributes
    assert span.name == "chat claude-haiku-5-5"
    assert a["gen_ai.request.model"] == "claude-haiku-5-5" and a["gen_ai.usage.input_tokens"] == 100
    assert a["openinference.span.kind"] == "LLM" and "query_logs" in a["output.value"]
    pts = metric_points(reader)
    assert pts["agent_model_calls"][0].value == 1
    assert sum(p.value for p in pts["agent_tokens"]) == 120


async def test_gateway_failure_marks_span_error_and_reraises(tel):
    telemetry, spans, reader = tel
    gw = TracedGateway(FakeGateway([]), telemetry, "claude-haiku-5-5")
    with pytest.raises(GatewayError):
        await gw.complete(system="s", messages=[{"role": "user", "content": "hi"}], tools=[])
    assert spans.get_finished_spans()[0].status.status_code.name == "ERROR"
    assert metric_points(reader)["agent_model_calls"][0].attributes["outcome"] == "error"


async def test_tool_call_span_and_error_outcome(tel):
    telemetry, spans, reader = tel
    tb = TracedToolbox(FakeToolbox({"get_trace": "bad id"}, errors={"get_trace"}), telemetry)
    await tb.call("get_trace", {"trace_id": "x"})
    (span,) = spans.get_finished_spans()
    assert span.name == "execute_tool get_trace" and span.attributes["agent.tool.error"] is True
    assert metric_points(reader)["agent_tool_calls"][0].attributes == {"tool": "get_trace", "outcome": "error"}


async def test_content_is_truncated_and_can_be_disabled(tel):
    telemetry, spans, _ = tel
    await TracedToolbox(FakeToolbox({"query_logs": "x" * 10000}), telemetry).call("query_logs", {})
    assert len(spans.get_finished_spans()[0].attributes["output.value"]) < 4100
    telemetry.capture_content = False
    await TracedToolbox(FakeToolbox({"query_logs": "secret"}), telemetry).call("query_logs", {})
    assert spans.get_finished_spans()[1].attributes["output.value"] == "[content capture disabled]"


async def test_diagnosis_span_is_parent_of_model_and_tool_spans(tel):
    telemetry, spans, reader = tel
    alert = Alert(name="OrderSubmitErrorRateHigh", summary="s")
    with telemetry.diagnosis_span(alert) as root:
        await TracedGateway(FakeGateway([text_response("ok")]), telemetry, "m").complete(system="", messages=[{"role": "user", "content": "x"}], tools=[])
        await TracedToolbox(FakeToolbox({"query_logs": "r"}), telemetry).call("query_logs", {})
        telemetry.record_diagnosis(root, DiagnosisResult(
            alert=alert, status="complete", diagnosis=Diagnosis.model_validate(VALID), latency_s=2.0, cost_usd=0.01, steps=2))
    finished = {s.name: s for s in spans.get_finished_spans()}
    parent = finished["diagnose OrderSubmitErrorRateHigh"]
    assert finished["chat m"].parent.span_id == parent.context.span_id
    assert finished["execute_tool query_logs"].parent.span_id == parent.context.span_id
    assert parent.attributes["agent.confidence"] == 0.8
    assert metric_points(reader)["agent_diagnoses"][0].attributes == {"status": "complete"}
