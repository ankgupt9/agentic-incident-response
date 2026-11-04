from agent.toolbox import FakeToolbox, McpToolbox


async def test_fake_toolbox_records_calls_and_flags_errors():
    tb = FakeToolbox({"query_logs": lambda a: f"logs for {a['service']}", "get_trace": "boom"}, errors={"get_trace"})
    ok = await tb.call("query_logs", {"service": "order-service"})
    err = await tb.call("get_trace", {})
    missing = await tb.call("nope", {})
    assert ok.text == "logs for order-service" and not ok.is_error
    assert err.is_error and missing.is_error
    assert tb.calls[0] == ("query_logs", {"service": "order-service"})


async def test_mcp_toolbox_exposes_specs_and_returns_tool_errors_as_results():
    async with McpToolbox(args=["-m", "mcp_server.server"]) as tb:
        names = {s["name"] for s in tb.specs()}
        assert names == {"query_logs", "get_trace", "query_metrics"}
        assert all("input_schema" in s and s["description"] for s in tb.specs())
        result = await tb.call("get_trace", {"trace_id": "not-hex"})
        assert result.is_error and "32 hex" in result.text
