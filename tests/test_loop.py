"""Behavior the core loop must satisfy. Skipped until agent/loop.py is implemented.

Each test scripts the model with FakeGateway and the tools with FakeToolbox, so the loop
is exercised with zero API spend. Assert on what the loop sends to the gateway, not just
on what it returns.
"""

import dataclasses

import pytest

from agent.gateway import FakeGateway, GatewayError, Usage, text_response, tool_use_response
from agent.loop import run_diagnosis
from agent.schemas import Alert
from agent.toolbox import FakeToolbox
from tests.test_schemas import VALID

ALERT = Alert(name="OrderSubmitErrorRateHigh", labels={"severity": "critical"}, summary="order submit error ratio > 20%")


def submit(call_id: str = "s1", **override) -> object:
    return tool_use_response((call_id, "submit_diagnosis", {**VALID, **override}))


def toolbox(**kw) -> FakeToolbox:
    return FakeToolbox({"query_logs": "order submission failed sku=SKU-1", "get_trace": "trace ok", **kw})


async def run(gateway, tb=None, **kw):
    try:
        return await run_diagnosis(ALERT, gateway, tb or toolbox(), **kw)
    except NotImplementedError:
        pytest.skip("loop not implemented yet")


def assert_tool_calls_paired(messages: list[dict]) -> None:
    """Every tool_use in an assistant turn is answered by a tool_result in the very next user turn."""
    for i, m in enumerate(messages):
        if m["role"] != "assistant" or isinstance(m["content"], str):
            continue
        ids = [b["id"] for b in m["content"] if b["type"] == "tool_use"]
        if not ids:
            continue
        nxt = messages[i + 1]
        assert nxt["role"] == "user"
        answered = [b["tool_use_id"] for b in nxt["content"] if b["type"] == "tool_result"]
        assert sorted(answered) == sorted(ids)


async def test_happy_path_investigates_then_submits():
    gw = FakeGateway([tool_use_response(("t1", "query_logs", {"service": "order-service"})), submit()])
    tb = toolbox()
    result = await run(gw, tb)
    assert result.status == "complete" and result.diagnosis.category == "data_inconsistency"
    assert tb.calls == [("query_logs", {"service": "order-service"})]
    assert result.tool_calls == 1
    first = gw.calls[0]
    assert ALERT.name in str(first["messages"][0]["content"])  # alert is shown to the model
    assert {t["name"] for t in first["tools"]} >= {"query_logs", "get_trace", "submit_diagnosis"}
    assert_tool_calls_paired(gw.calls[-1]["messages"])


async def test_parallel_tool_calls_are_answered_in_one_user_message():
    gw = FakeGateway([
        tool_use_response(("a", "query_logs", {}), ("b", "get_trace", {"trace_id": "x"})),
        submit(),
    ])
    tb = toolbox()
    await run(gw, tb)
    history = gw.calls[-1]["messages"]
    results_msg = history[-1]
    assert results_msg["role"] == "user" and len(results_msg["content"]) == 2
    assert {c for c, _ in tb.calls} == {"query_logs", "get_trace"}
    assert_tool_calls_paired(history)


async def test_tool_errors_reach_the_model_and_the_loop_continues():
    tb = FakeToolbox({"get_trace": "trace_id must be 32 hex characters"}, errors={"get_trace"})
    gw = FakeGateway([tool_use_response(("t1", "get_trace", {"trace_id": "bad"})), submit()])
    result = await run(gw, tb)
    assert result.status == "complete"
    tool_result = gw.calls[-1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True and "32 hex" in str(tool_result["content"])


async def test_unknown_tool_is_reported_to_the_model_not_raised():
    gw = FakeGateway([tool_use_response(("t1", "delete_everything", {})), submit()])
    result = await run(gw)
    assert result.status == "complete"
    assert gw.calls[-1]["messages"][-1]["content"][0]["is_error"] is True


async def test_step_cap_forces_a_final_answer_with_only_submit_available():
    looping = [tool_use_response((f"t{i}", "query_logs", {})) for i in range(2)]
    gw = FakeGateway(looping + [submit()])
    result = await run(gw, max_steps=2)
    assert result.status == "forced" and result.diagnosis is not None
    assert len(gw.calls) == 3
    assert [t["name"] for t in gw.calls[-1]["tools"]] == ["submit_diagnosis"]


async def test_step_cap_with_no_valid_answer_fails_cleanly():
    gw = FakeGateway([tool_use_response(("t1", "query_logs", {})), text_response("I could not tell.")])
    result = await run(gw, max_steps=1)
    assert result.status == "failed" and result.diagnosis is None and result.error


async def test_invalid_diagnosis_gets_one_retry_with_the_validation_error():
    gw = FakeGateway([submit(confidence=1.7), submit("s2")])
    result = await run(gw)
    assert result.status == "complete"
    feedback = gw.calls[-1]["messages"][-1]["content"][0]
    assert feedback["is_error"] is True and "confidence" in str(feedback["content"])


async def test_two_invalid_diagnoses_fail():
    gw = FakeGateway([submit(confidence=1.7), submit("s2", confidence=2)])
    result = await run(gw)
    assert result.status == "failed" and "confidence" in (result.error or "")


async def test_usage_and_cost_are_summed_across_model_calls():
    a = dataclasses.replace(tool_use_response(("t1", "query_logs", {}), usage=Usage(1000, 100)), cost_usd=0.001)
    b = dataclasses.replace(submit(), cost_usd=0.002)
    result = await run(FakeGateway([a, b]))
    assert result.input_tokens == 1000 + 100 and result.output_tokens == 100 + 20
    assert result.cost_usd == pytest.approx(0.003)
    assert result.steps == 2


async def test_gateway_failure_returns_a_failed_result_instead_of_raising():
    result = await run(FakeGateway([]))
    assert result.status == "failed" and result.error
