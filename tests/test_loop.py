"""Behavior the core loop must satisfy.

Each test scripts the model with FakeGateway and the tools with FakeToolbox, so the loop
is exercised with zero API spend. Assert on what the loop sends to the gateway, not just
on what it returns.
"""

import dataclasses

import pytest

from agent.gateway import FakeGateway, Usage, text_response, tool_use_response
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


async def test_step_cap_forces_a_final_answer_and_never_changes_the_tool_list():
    looping = [tool_use_response((f"t{i}", "query_logs", {})) for i in range(2)]
    gw = FakeGateway(looping + [submit()])
    result = await run(gw, max_steps=2)
    assert result.status == "forced" and result.diagnosis is not None
    assert len(gw.calls) == 3
    # Thinking blocks are bound to the conversation prefix, tools included. Changing the list after the
    # cap made the real API answer HTTP 400, so every call must declare exactly the same tools.
    assert all(call["tools"] == gw.calls[0]["tools"] for call in gw.calls)
    assert "Step limit reached" in str(gw.calls[-1]["messages"][-1]["content"])


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


async def test_text_only_reply_is_nudged_once_then_accepted_if_it_submits():
    gw = FakeGateway([text_response("Let me think."), submit()])
    result = await run(gw)
    assert result.status == "complete"
    assert "submit_diagnosis" in str(gw.calls[-1]["messages"][-1]["content"])


async def test_text_only_replies_twice_fail():
    result = await run(FakeGateway([text_response("hmm"), text_response("still hmm")]))
    assert result.status == "failed" and "without submitting" in result.error


async def test_bad_submission_alongside_another_tool_call_answers_both():
    bad = tool_use_response(("a", "query_logs", {}), ("s1", "submit_diagnosis", {**VALID, "confidence": 5}))
    gw = FakeGateway([bad, submit("s2")])
    tb = toolbox()
    result = await run(gw, tb)
    assert result.status == "complete" and tb.calls == [("query_logs", {})]
    history = gw.calls[-1]["messages"]
    assert [c["tool_use_id"] for c in history[-1]["content"]] == ["a", "s1"]
    assert_tool_calls_paired(history)


async def test_tool_that_raises_becomes_an_error_result():
    class Exploding(FakeToolbox):
        async def call(self, name, args):
            raise RuntimeError("backend exploded")

    gw = FakeGateway([tool_use_response(("t1", "query_logs", {})), submit()])
    result = await run(gw, Exploding({"query_logs": "x"}))
    assert result.status == "complete"
    block = gw.calls[-1]["messages"][-1]["content"][0]
    assert block["is_error"] is True and "RuntimeError" in block["content"]


async def test_investigation_tool_requested_after_the_cap_is_refused():
    gw = FakeGateway([
        tool_use_response(("t1", "query_logs", {})),
        tool_use_response(("t2", "query_logs", {})),  # asked for after the cap: only submit is offered
    ])
    tb = toolbox()
    result = await run(gw, tb, max_steps=1)
    assert result.status == "failed" and len(tb.calls) == 1


async def test_max_steps_must_be_positive():
    with pytest.raises(ValueError):
        await run_diagnosis(ALERT, FakeGateway([]), toolbox(), max_steps=0)


async def test_tools_requested_after_the_cap_are_refused_with_an_explanation_and_not_run():
    gw = FakeGateway([tool_use_response(("t1", "query_logs", {})), tool_use_response(("t2", "get_trace", {"trace_id": "x"})), submit("s9")])
    tb = toolbox()
    result = await run(gw, tb, max_steps=1)
    # t2 was refused (not executed); the model then submitted, but only one retry slot exists so the run still ends cleanly.
    assert [name for name, _ in tb.calls] == ["query_logs"]
    refusal = gw.calls[2]["messages"][-1]["content"][0]
    assert refusal["is_error"] is True and "step limit" in refusal["content"]
    assert result.status == "forced"


async def test_kill_switch_halts_the_run_before_the_next_model_call():
    calls = {"n": 0}

    async def should_stop():
        calls["n"] += 1
        return "kill switch engaged" if calls["n"] >= 2 else None

    gw = FakeGateway([tool_use_response(("t1", "query_logs", {})), submit()])
    result = await run_diagnosis(ALERT, gw, toolbox(), should_stop=should_stop)
    assert result.status == "halted" and "kill switch" in result.error
    assert len(gw.calls) == 1  # the second model call never happened


async def test_budget_forces_a_final_answer_like_the_step_cap():
    import dataclasses

    expensive = dataclasses.replace(tool_use_response(("t1", "query_logs", {})), cost_usd=0.02)
    gw = FakeGateway([expensive, submit("s1")])
    result = await run_diagnosis(ALERT, gw, toolbox(), budget_usd=0.01)
    assert result.status == "forced"
    assert "Budget limit reached" in str(gw.calls[-1]["messages"][-1]["content"])


def knowledge_toolbox():
    return FakeToolbox({"query_logs": "log line", "search_runbooks": "1 runbook(s) matched.\n[RB-001] Price mismatch | owner ankur | reviewed 2026-11-09"})


async def test_citing_a_knowledge_id_the_tools_returned_is_accepted():
    gw = FakeGateway([tool_use_response(("t1", "search_runbooks", {"query": "price"})), submit(knowledge_refs=["RB-001"])])
    result = await run(gw, knowledge_toolbox())
    assert result.status == "complete" and result.diagnosis.knowledge_refs == ["RB-001"]


async def test_citing_an_id_no_tool_returned_is_sent_back_once_for_correction():
    gw = FakeGateway([tool_use_response(("t1", "search_runbooks", {"query": "price"})), submit(knowledge_refs=["RB-099"]), submit("s2", knowledge_refs=["RB-001"])])
    result = await run(gw, knowledge_toolbox())
    assert result.status == "complete"
    feedback = gw.calls[-1]["messages"][-1]["content"][0]
    assert feedback["is_error"] is True and "RB-099" in feedback["content"] and "no tool returned" in feedback["content"]


async def test_citing_ids_that_were_never_retrieved_twice_fails_the_run():
    gw = FakeGateway([submit(knowledge_refs=["RB-001"]), submit("s2", knowledge_refs=["RB-001"])])  # no search was done
    result = await run(gw, knowledge_toolbox())
    assert result.status == "failed" and "knowledge_refs" in result.error


async def test_tool_outputs_are_kept_for_the_evals():
    gw = FakeGateway([tool_use_response(("t1", "query_logs", {})), submit()])
    result = await run(gw, knowledge_toolbox())
    assert result.observed == ["log line"] and "observed" not in result.model_dump()


async def test_prompt_guidance_appears_only_for_the_tool_groups_present():
    gw = FakeGateway([submit()])
    await run(gw, FakeToolbox({"query_logs": "x"}))
    assert "Knowledge tools" not in gw.calls[0]["system"] and "propose_" not in gw.calls[0]["system"]
    gw = FakeGateway([submit()])
    await run(gw, FakeToolbox({"query_logs": "x", "search_runbooks": "x", "propose_replay_message": "x"}))
    assert "Knowledge tools" in gw.calls[0]["system"] and "nothing runs until a person approves" in gw.calls[0]["system"]


async def test_leaked_tool_call_markup_is_trimmed_recorded_and_the_run_still_completes():
    leaked = "Quotes are served from a stale cache so the consumer rejects them.</root_cause>\n<parameter name=\"category\">stale"
    gw = FakeGateway([submit(root_cause=leaked)])
    result = await run(gw)
    assert result.status == "complete" and result.repairs == ["root_cause"]
    assert result.diagnosis.root_cause == "Quotes are served from a stale cache so the consumer rejects them."
    assert len(gw.calls) == 1  # no correction round was spent
