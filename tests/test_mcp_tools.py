import base64
import sys
import time

import httpx
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from mcp_server import tools
from mcp_server.backends import Backends
from mcp_server.tools import ToolError


def backends(handler) -> Backends:
    return Backends(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


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

    out = await tools.query_logs(backends(handler), service="order-service", level="error", minutes=5, limit=500)
    assert seen["limit"] == "100"  # clamped
    assert "3 lines, 2 distinct" in out
    assert "(x2)" in out and "trace=" + "a" * 32 in out
    assert "untrusted log data" in out


async def test_query_logs_truncates_long_messages_and_handles_empty():
    long_line = "x" * 5000
    out = await tools.query_logs(backends(lambda r: httpx.Response(200, json=loki_payload(("s", "info", "", long_line)))))
    assert len(out) < 800
    empty = await tools.query_logs(backends(lambda r: httpx.Response(200, json=loki_payload())))
    assert "no matching logs" in empty


async def test_query_logs_backend_failure_becomes_tool_error():
    with pytest.raises(ToolError, match="Loki query failed"):
        await tools.query_logs(backends(lambda r: httpx.Response(503)))


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
    out = await tools.get_trace(backends(lambda r: httpx.Response(200, json=tempo_payload())), "a" * 32)
    assert "2 spans, 1 with errors" in out
    assert "ERROR order-service POST /orders" in out
    assert "exception=AMQPConnectionError: broker lost" in out


async def test_get_trace_validates_id_and_handles_missing():
    with pytest.raises(ToolError):
        await tools.get_trace(backends(lambda r: httpx.Response(200)), "../etc/passwd")
    out = await tools.get_trace(backends(lambda r: httpx.Response(404)), "a" * 32)
    assert "not found" in out


async def test_query_metrics_summarizes_series():
    body = {"status": "success", "data": {"result": [
        {"metric": {"status": "error"}, "values": [[1, "0"], [2, "0.5"], [3, "0.9"]]},
        {"metric": {"status": "ok"}, "values": [[1, "1"], [2, "NaN"], [3, "1"]]},
    ]}}
    out = await tools.query_metrics(backends(lambda r: httpx.Response(200, json=body)), "rate(x[1m])", 5)
    assert "status=error: last=0.9 min=0 max=0.9 first=0" in out
    assert "status=ok: last=1 min=1 max=1" in out


async def test_query_metrics_rejects_bad_input_and_prometheus_errors():
    with pytest.raises(ToolError):
        await tools.query_metrics(backends(lambda r: httpx.Response(200)), "x" * 501)
    bad = httpx.Response(400, json={"status": "error", "error": "parse error"})
    with pytest.raises(ToolError, match="parse error"):
        await tools.query_metrics(backends(lambda r: bad), "((")


async def test_mcp_server_lists_tools_and_reports_errors_over_stdio():
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            assert {t.name for t in listed.tools} == {"query_logs", "get_trace", "query_metrics"}
            result = await session.call_tool("get_trace", {"trace_id": "not-hex"})
            assert result.isError is True


def _stack_up() -> bool:
    try:
        return all(httpx.get(f"{u}/ready", timeout=1).status_code == 200 for u in ("http://localhost:3100", "http://localhost:3200"))
    except httpx.HTTPError:
        return False


@pytest.mark.skipif(not _stack_up(), reason="observability stack not running")
async def test_live_tools_against_the_local_stack():
    b = Backends()
    assert "window: 15m" in await tools.query_logs(b, minutes=15, limit=5)
    assert "window: 5m" in await tools.query_metrics(b, "sum(rate(orders_submitted_total[1m]))", 5)
