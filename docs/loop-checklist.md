# agent/loop.py: what it does and why

Goal: `run_diagnosis(alert, gateway, toolbox, max_steps=8) -> DiagnosisResult`.
`agent/loop.py` implements this checklist and passes `tests/test_loop.py`. Review it against the list below; each item is something the loop must do and you should be able to explain why.

## What the loop does

Claude requests tools; the loop runs them. Claude remembers nothing between calls, so the loop owns the history.

1. Build the first user message from the alert (`agent.prompts.alert_prompt`).
2. Offer `toolbox.specs() + [submit_diagnosis_tool()]`.
3. Call `gateway.complete(system=SYSTEM_PROMPT, messages=..., tools=...)`.
4. Append `response.assistant_message()` to the history **verbatim** (thinking blocks must round-trip unchanged).
5. If the model called `submit_diagnosis`: validate with `parse_diagnosis`. Valid means done.
6. Otherwise run each requested tool through `toolbox.call` and append the results.
7. Repeat until done, out of steps, or failed.

## Things that trip people up

- **Pairing.** Every `tool_use` id needs a `tool_result` with the same `tool_use_id` in the very next user message, or the API returns a 400.
- **Parallel calls.** One response can hold several `tool_use` blocks. Return all results in ONE user message.
- **Tool errors are data.** Return `{"type": "tool_result", "tool_use_id": ..., "content": text, "is_error": True}` and keep going. Never let a failing tool crash the loop.
- **Unknown tool names.** The model can request a tool you did not offer. Answer with an error result.
- **Bad submission.** If validation fails, send the error text back as an `is_error` tool_result so the model can fix it. Allow exactly one retry.
- **Step cap.** At `max_steps`, add a message saying the limit is reached and refuse any further investigation tools yourself (return an error result). Mark a valid answer `forced`. If the model still asks for tools or gives no diagnosis after one correction, `failed`.
- **Never change the tool list during a run.** The model's thinking blocks are bound to the conversation prefix, and the tool list is part of it. Removing tools to force an answer made the real API return HTTP 400 ("thinking block is bound to a different conversation") whenever the model had thought before a tool call. Only append to the history, and enforce limits in the loop, not by editing what you declared. (Found in the first live run; mocks cannot catch it.)
- **Model ends without submitting.** A text-only reply with no tool call is not a diagnosis. Nudge once or fail; decide and defend your choice.
- **Gateway errors.** `GatewayError` should produce a `failed` result with the message, not an exception.
- **Bookkeeping.** Sum tokens, cost and latency into the result; count steps (model calls) and tool calls.

## Optional, worth considering

- `agent.schemas.ungrounded_evidence` flags evidence quotes that never appeared in any tool result. Do you reject, retry, or lower confidence? Day 4 evals will tell you which is right.
- Run parallel tool calls with `asyncio.gather`.

## Not yours yet (Day 3)

Guardrail checks, audit log entries, kill switch and tracing spans plug into this loop later. Keep tool execution in one place so they have a single seam to hook.
