import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.anthropic_gateway import AnthropicGateway
from agent.config import Settings, cost_usd
from agent.gateway import FakeGateway, GatewayError, text_response, tool_use_response

ROOT = Path(__file__).resolve().parent.parent
SDK_IMPORT = re.compile(r"^\s*(import|from)\s+anthropic\b", re.MULTILINE)
ALLOWED = {ROOT / "agent" / "anthropic_gateway.py"}


def test_only_the_gateway_imports_the_anthropic_sdk():
    offenders = []
    for path in ROOT.rglob("*.py"):
        if ".venv" in path.parts or path in ALLOWED or path == Path(__file__):
            continue
        if SDK_IMPORT.search(path.read_text()):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_cost_uses_price_table_and_long_prompt_tier():
    assert cost_usd("claude-haiku-5-5", 100_000, 0) == pytest.approx(0.01)  # standard tier: $0.10/M
    assert cost_usd("claude-haiku-5-5", 0, 1_000_000) == pytest.approx(0.50)
    assert cost_usd("claude-haiku-5-5", 200_000, 0) == pytest.approx(0.10)  # over 100K: $0.50/M


def test_unknown_model_fails_loudly():
    with pytest.raises(KeyError):
        cost_usd("claude-made-up", 1, 1)


async def test_fake_gateway_plays_script_in_order_and_records_calls():
    gw = FakeGateway([tool_use_response(("t1", "query_logs", {"service": "order-service"})), text_response("done")])
    first = await gw.complete(system="s", messages=[{"role": "user", "content": "hi"}], tools=[])
    second = await gw.complete(system="s", messages=[], tools=[])
    assert first.tool_calls[0].name == "query_logs" and first.stop_reason == "tool_use"
    assert second.text == "done"
    assert len(gw.calls) == 2
    with pytest.raises(GatewayError):
        await gw.complete(system="s", messages=[], tools=[])


def test_assistant_message_round_trips_raw_blocks():
    resp = tool_use_response(("t1", "get_trace", {"trace_id": "abc"}), text="checking")
    msg = resp.assistant_message()
    assert msg["role"] == "assistant"
    assert [b["type"] for b in msg["content"]] == ["text", "tool_use"]


class _Block:
    def __init__(self, **data):
        self._data = data

    def model_dump(self, **_):
        return dict(self._data)


class _FakeMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.kwargs = response, error, None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def _client(messages):
    return SimpleNamespace(messages=messages)


async def test_anthropic_gateway_normalizes_response_and_sends_pinned_model():
    resp = SimpleNamespace(
        content=[_Block(type="text", text="looking"), _Block(type="tool_use", id="t1", name="query_logs", input={"x": 1})],
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        model="claude-haiku-5-5",
        _request_id="req_1",
    )
    msgs = _FakeMessages(response=resp)
    gw = AnthropicGateway(Settings(), client=_client(msgs))
    out = await gw.complete(system="sys", messages=[{"role": "user", "content": "a"}], tools=[{"name": "query_logs"}])
    assert msgs.kwargs["model"] == "claude-haiku-5-5"
    assert msgs.kwargs["output_config"] == {"effort": "medium"}
    assert "temperature" not in msgs.kwargs  # sampling params are rejected by current models
    assert out.text == "looking" and out.tool_calls[0].input == {"x": 1}
    assert out.usage.input_tokens == 1000 and out.cost_usd == pytest.approx(cost_usd("claude-haiku-5-5", 1000, 200))
    assert out.request_id == "req_1" and out.stop_reason == "tool_use"


async def test_anthropic_gateway_wraps_sdk_errors():
    import anthropic  # noqa: PLC0415  (test-only; the repo guard test exempts this file)

    req = SimpleNamespace(method="POST", url="https://x")
    err = anthropic.APIConnectionError(request=req)
    gw = AnthropicGateway(Settings(), client=_client(_FakeMessages(error=err)))
    with pytest.raises(GatewayError) as exc:
        await gw.complete(system="s", messages=[], tools=[])
    assert exc.value.retryable is True
