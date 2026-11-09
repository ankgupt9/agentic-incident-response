import base64
import sys
import time

import httpx
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client

from mcp_server import tools
from mcp_server.telemetry_backends import TelemetryBackends
from mcp_server.tools import ToolError


def telemetry_backends(handler) -> TelemetryBackends:
    return TelemetryBackends(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def loki_payload(*lines: tuple[str, str, str, str]) -> dict:
    """lines: (service, level, trace_id, message)"""
    now = time.time_ns()
    result = [
        {"stream": {"service_name": svc, "detected_level": lvl, "trace_id": tid}, "values": [[str(now - i), msg]]}
        for i, (svc, lvl, tid, msg) in enumerate(lines)
    ]
    return {"status": "success", "data": {"result": result}}


def test_build_logql_rejects_injection_and_escapes_text():
    with pytest.raises(ToolError):
        tools.build_logql('x"} | drop', None, None)
    with pytest.raises(ToolError):
        tools.build_logql(None, "fatal", None)
    q = tools.build_logql("order-service", "error", 'say "hi"\\')
    assert q == '{service_name="order-service"} | detected_level="error" |= "say \\"hi\\"\\\\"'


async def test_query_logs_collapses_repeats_and_keeps_trace_id():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(req.url.params)
        return httpx.Response(200, json=loki_payload(
            ("order-service", "error", "a" * 32, "order submission failed order_id=1 sku=SKU-1"),
            ("order-service", "error", "b" * 32, "order submission failed order_id=2 sku=SKU-1"),
            ("pricing-service", "warn", "", "quote requested for unknown sku=SKU-9"),
        ))

    out = await tools.query_logs(telemetry_backends(handler), service="order-service", level="error", minutes=5, limit=500)
    assert seen["limit"] == "100"  # clamped
    assert "3 lines, 2 distinct" in out
    assert "(x2)" in out and "trace=" + "a" * 32 in out
    assert "untrusted log data" in out


def loki_payload_with_exceptions(rows: list[tuple[str, str, str, str, str, int]]) -> dict:
    """rows: (service, trace_id, message, exception_type, exception_message, age_seconds)"""
    now = time.time_ns()
    result = [
        {
            "stream": {"service_name": svc, "detected_level": "error", "trace_id": tid, "exception_type": et, "exception_message": em},
            "values": [[str(now - age * 10**9), msg]],
        }
        for svc, tid, msg, et, em, age in rows
    ]
    return {"status": "success", "data": {"result": result}}


async def test_query_logs_shows_exception_fields_and_keeps_different_exceptions_apart():
    payload = loki_payload_with_exceptions([
        ("pricing-service", "a" * 32, "failed to price order", "ValueError", "price mismatch for SKU-1001: quoted 21.99, current 19.99", 90),
        ("pricing-service", "b" * 32, "failed to price order", "KeyError", "'sku'", 90),
    ])
    out = await tools.query_logs(telemetry_backends(lambda r: httpx.Response(200, json=payload)))
    assert "ValueError: price mismatch for SKU-1001: quoted 21.99, current 19.99" in out
    assert "KeyError: 'sku'" in out and "2 distinct" in out


async def test_query_logs_exemplar_is_old_enough_for_its_trace_to_be_indexed():
    payload = loki_payload_with_exceptions([
        ("pricing-service", "1" * 32, "failed to price order", "ValueError", "price mismatch for SKU-1001: quoted 21.99, current 19.99", 5),
        ("pricing-service", "2" * 32, "failed to price order", "ValueError", "price mismatch for SKU-1001: quoted 21.99, current 19.99", 120),
        ("pricing-service", "3" * 32, "failed to price order", "ValueError", "price mismatch for SKU-1001: quoted 21.99, current 19.99", 300),
    ])
    out = await tools.query_logs(telemetry_backends(lambda r: httpx.Response(200, json=payload)))
    assert "(x3)" in out and "trace=" + "2" * 32 in out  # newest line that is at least 45s old
    assert "1" * 32 not in out


async def test_query_logs_truncates_long_messages_and_handles_empty():
    long_line = "x" * 5000
    out = await tools.query_logs(telemetry_backends(lambda r: httpx.Response(200, json=loki_payload(("s", "info", "", long_line)))))
    assert len(out) < 800
    empty = await tools.query_logs(telemetry_backends(lambda r: httpx.Response(200, json=loki_payload())))
    assert "no matching logs" in empty


async def test_query_logs_backend_failure_becomes_tool_error():
    with pytest.raises(ToolError, match="Loki query failed"):
        await tools.query_logs(telemetry_backends(lambda r: httpx.Response(503)))


def _id(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def tempo_payload() -> dict:
    def attr(k, v):
        return {"key": k, "value": {"stringValue": v}}

    return {"batches": [{
        "resource": {"attributes": [attr("service.name", "order-service")]},
        "scopeSpans": [{"spans": [
            {"spanId": _id(b"\x01" * 8), "name": "POST /orders", "startTimeUnixNano": "1000000000", "endTimeUnixNano": "9000000000",
             "attributes": [attr("http.method", "POST")], "status": {"code": "STATUS_CODE_ERROR", "message": "502"}},
            {"spanId": _id(b"\x02" * 8), "name": "publish", "startTimeUnixNano": "2000000000", "endTimeUnixNano": "3000000000",
             "events": [{"name": "exception", "attributes": [attr("exception.type", "AMQPConnectionError"), attr("exception.message", "broker lost")]}],
             "status": {}},
        ]}],
    }]}


async def test_get_trace_renders_spans_errors_and_exceptions():
    out = await tools.get_trace(telemetry_backends(lambda r: httpx.Response(200, json=tempo_payload())), "a" * 32)
    assert "2 spans, 1 with errors" in out
    assert "ERROR order-service POST /orders" in out
    assert "exception=AMQPConnectionError: broker lost" in out


async def test_get_trace_validates_id_and_handles_missing():
    with pytest.raises(ToolError):
        await tools.get_trace(telemetry_backends(lambda r: httpx.Response(200)), "../etc/passwd")
    out = await tools.get_trace(telemetry_backends(lambda r: httpx.Response(404)), "a" * 32)
    assert "not found" in out


async def test_query_metrics_summarizes_series():
    body = {"status": "success", "data": {"result": [
        {"metric": {"status": "error"}, "values": [[1, "0"], [2, "0.5"], [3, "0.9"]]},
        {"metric": {"status": "ok"}, "values": [[1, "1"], [2, "NaN"], [3, "1"]]},
    ]}}
    out = await tools.query_metrics(telemetry_backends(lambda r: httpx.Response(200, json=body)), "rate(x[1m])", 5)
    assert "status=error: last=0.9 min=0 max=0.9 first=0" in out
    assert "status=ok: last=1 min=1 max=1" in out


async def test_query_metrics_rejects_bad_input_and_prometheus_errors():
    with pytest.raises(ToolError):
        await tools.query_metrics(telemetry_backends(lambda r: httpx.Response(200)), "x" * 501)
    bad = httpx.Response(400, json={"status": "error", "error": "parse error"})
    with pytest.raises(ToolError, match="parse error"):
        await tools.query_metrics(telemetry_backends(lambda r: bad), "((")


TELEMETRY_TOOLS = {"query_logs", "get_trace", "query_metrics"}
KNOWLEDGE_TOOLS = {"search_runbooks", "get_runbook", "get_service_info", "get_contract", "get_recent_changes"}


async def _list_tools(knowledge: str | None):
    env = {**get_default_environment(), **({"KNOWLEDGE_TOOLS": knowledge} if knowledge else {})}
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            result = await session.call_tool("get_trace", {"trace_id": "not-hex"})
            return names, result


async def test_mcp_server_offers_telemetry_and_knowledge_tools_and_reports_errors_over_stdio():
    names, result = await _list_tools(None)
    assert names == TELEMETRY_TOOLS | KNOWLEDGE_TOOLS
    assert result.isError is True


async def test_knowledge_tools_can_be_switched_off_for_the_telemetry_only_arm():
    names, _ = await _list_tools("off")
    assert names == TELEMETRY_TOOLS


def _stack_up() -> bool:
    try:
        return all(httpx.get(f"{u}/ready", timeout=1).status_code == 200 for u in ("http://localhost:3100", "http://localhost:3200"))
    except httpx.HTTPError:
        return False


@pytest.mark.skipif(not _stack_up(), reason="observability stack not running")
async def test_live_tools_against_the_local_stack():
    b = TelemetryBackends()
    assert "window: 15m" in await tools.query_logs(b, minutes=15, limit=5)
    assert "window: 5m" in await tools.query_metrics(b, "sum(rate(orders_submitted_total[1m]))", 5)
