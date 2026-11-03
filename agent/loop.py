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
    "Step limit reached. Do not request any more tools. Call submit_diagnosis now with your best "
    "assessment from the evidence gathered so far, and use a low confidence if the evidence is thin."
)
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
) -> DiagnosisResult:
    if max_steps < 1:
        raise ValueError("max_steps must be at least 1")

    started = time.perf_counter()
    result = DiagnosisResult(alert=alert, status="failed")
    messages: list[dict] = [
        {"role": "user", "content": alert_prompt(alert.name, alert.status, alert.labels, alert.summary, alert.starts_at)}
    ]
    submit_tool = submit_diagnosis_tool()
    tools = toolbox.specs() + [submit_tool]

    forced = False  # True once the step cap is hit: only submit_diagnosis is offered from then on
    corrections = 1  # one chance, in total, to fix a bad submission or a reply with no submission

    while True:
        # Step cap: stop offering investigation tools and demand an answer. This is a deliberate
        # final turn, not an extra step, so it is not counted against max_steps.
        if not forced and result.steps >= max_steps:
            forced = True
            tools = [submit_tool]
            messages.append({"role": "user", "content": STEP_LIMIT_MESSAGE})

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
            # Only submit_diagnosis was offered and the model asked for something else.
            result.error = "step limit reached without a diagnosis"
            break

        messages.append({"role": "user", "content": tool_results})

    result.latency_s = time.perf_counter() - started
    return result
