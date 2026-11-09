"""The core agent loop.

    run_diagnosis(alert, gateway, toolbox, max_steps=8) -> DiagnosisResult

Claude never runs a tool. It asks for one; this loop runs it, hands the result back, and decides
when to stop. That makes the loop the control point for limits, safety and records.

One pass of the loop:

    call the model  ->  model asks for tools  ->  run them  ->  add results to history  ->  repeat
                    \\->  model calls submit_diagnosis  ->  validate  ->  done

The model remembers nothing between calls, so `messages` is the whole conversation and is sent
again every turn. Full design and test scenarios: docs/loop-checklist.md, tests/test_loop.py.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from agent.gateway import GatewayError, ModelGateway, ModelResponse, ToolCall
from agent.prompts import SYSTEM_PROMPT, alert_prompt
from agent.schemas import (
    SUBMIT_DIAGNOSIS,
    Alert,
    DiagnosisResult,
    parse_diagnosis,
    submit_diagnosis_tool,
    validation_message,
)
from agent.toolbox import Toolbox, ToolResult

STEP_LIMIT_MESSAGE = (
    "Step limit reached. Do not request any more tools; they will be refused. Call submit_diagnosis now "
    "with your best assessment from the evidence gathered so far, and use a low confidence if the "
    "evidence is thin."
)
BUDGET_MESSAGE = (
    "Budget limit reached. Do not request any more tools; they will be refused. Call submit_diagnosis now "
    "with your best assessment from the evidence gathered so far, and use a low confidence if the "
    "evidence is thin."
)
STEP_LIMIT_REFUSAL = "step limit reached: tool calls are no longer available. Call submit_diagnosis now."
NUDGE_MESSAGE = "You have not submitted a diagnosis. Call submit_diagnosis to finish."


async def run_tool(toolbox: Toolbox, call: ToolCall, offered: set[str]) -> dict:
    """Run one requested tool and return the tool_result block for the history.

    Failures become error results the model can read, never exceptions, so one bad tool call
    cannot crash an investigation. A tool the model was not offered is refused here, before it
    reaches the toolbox. Guardrail checks and audit logging attach to this function (Day 3):
    it is the single place every tool call passes through.
    """
    if call.name not in offered:
        result = ToolResult(f"unknown tool {call.name!r}", is_error=True)
    else:
        try:
            result = await toolbox.call(call.name, call.input)
        except Exception as exc:
            result = ToolResult(f"tool call failed: {type(exc).__name__}", is_error=True)
    return {
        "type": "tool_result",
        "tool_use_id": call.id,
        "content": result.text,
        "is_error": result.is_error,
    }


def record_usage(result: DiagnosisResult, response: ModelResponse) -> None:
    result.steps += 1
    result.input_tokens += response.usage.input_tokens
    result.output_tokens += response.usage.output_tokens
    result.cost_usd += response.cost_usd


async def run_diagnosis(
    alert: Alert,
    gateway: ModelGateway,
    toolbox: Toolbox,
    max_steps: int = 8,
    budget_usd: float | None = None,
    should_stop: Callable[[], Awaitable[str | None]] | None = None,
) -> DiagnosisResult:
    """should_stop returns a reason when the run must end now (the kill switch); budget_usd caps spend."""
    if max_steps < 1:
        raise ValueError("max_steps must be at least 1")

    started = time.perf_counter()
    result = DiagnosisResult(alert=alert, status="failed")
    messages: list[dict] = [
        {"role": "user", "content": alert_prompt(alert.name, alert.status, alert.labels, alert.summary, alert.starts_at)}
    ]
    # The tool list is fixed for the whole run. The model's thinking blocks are bound to the
    # conversation prefix, and the tool list is part of it: changing the list mid-run makes the next
    # call fail with HTTP 400 ("thinking block is bound to a different conversation").
    tools = toolbox.specs() + [submit_diagnosis_tool()]

    forced = False  # True once the step cap is hit: investigation tools are refused from then on
    corrections = 1  # one chance, in total, to fix a bad submission or a reply with no submission

    while True:
        # Step cap: tell the model to answer now and refuse any further investigation tools. The tools
        # stay declared (see above); the loop is what enforces the cap. This is a deliberate final
        # turn, not an extra step, so it is not counted against max_steps.
        if should_stop is not None and (reason := await should_stop()):
            result.status, result.error = "halted", reason
            break

        over_budget = budget_usd is not None and result.cost_usd >= budget_usd
        if not forced and (result.steps >= max_steps or over_budget):
            forced = True
            messages.append({"role": "user", "content": BUDGET_MESSAGE if over_budget and result.steps < max_steps else STEP_LIMIT_MESSAGE})

        try:
            response = await gateway.complete(system=SYSTEM_PROMPT, messages=messages, tools=tools)
        except GatewayError as exc:
            result.error = str(exc)
            break
        record_usage(result, response)

        # The assistant turn goes into the history exactly as received (thinking blocks included).
        messages.append(response.assistant_message())

        # A reply with no tool call is not a diagnosis. Nudge once, then give up.
        if not response.tool_calls:
            result.error = "model ended without submitting a diagnosis"
            if forced or corrections == 0:
                break
            corrections -= 1
            messages.append({"role": "user", "content": NUDGE_MESSAGE})
            continue

        submission = next((c for c in response.tool_calls if c.name == SUBMIT_DIAGNOSIS), None)
        investigation = [c for c in response.tool_calls if c is not submission]

        # Every tool_use needs a tool_result in the very next user message, and results for
        # parallel calls must travel together in ONE message. gather keeps the original order.
        if forced:
            tool_results = [
                {"type": "tool_result", "tool_use_id": c.id, "content": STEP_LIMIT_REFUSAL, "is_error": True}
                for c in investigation
            ]
        else:
            offered = {t["name"] for t in tools}
            result.tool_calls += len(investigation)
            tool_results = list(await asyncio.gather(*(run_tool(toolbox, c, offered) for c in investigation)))

        if submission is not None:
            try:
                result.diagnosis = parse_diagnosis(submission.input)
            except ValidationError as exc:
                problem = validation_message(exc)
                result.error = f"invalid diagnosis: {problem}"
                if corrections == 0:
                    break
                corrections -= 1
                # Send the validation errors back so the model can correct its submission.
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": submission.id, "content": problem, "is_error": True}
                )
            else:
                result.status = "forced" if forced else "complete"
                result.error = None
                break
        elif forced:
            # Past the cap the model asked for tools instead of submitting. The refusal below tells it
            # to submit; it gets the one shared correction, then the run fails.
            result.error = "step limit reached without a diagnosis"
            if corrections == 0:
                break
            corrections -= 1

        messages.append({"role": "user", "content": tool_results})

    result.latency_s = time.perf_counter() - started
    return result
