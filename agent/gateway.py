"""Model gateway contract: the only shape the rest of the agent knows about an LLM.

No vendor SDK is imported here. The Anthropic implementation lives in
agent/anthropic_gateway.py, the only file allowed to import the SDK (hard rule 4).
"""

from dataclasses import dataclass, field
from typing import Protocol


class GatewayError(Exception):
    """A model call failed after the gateway's own retries."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ModelResponse:
    text: str
    tool_calls: list[ToolCall]
    # "end_turn" | "tool_use" | "max_tokens" | "refusal" | "other"
    stop_reason: str
    usage: Usage
    cost_usd: float
    latency_s: float
    model: str
    # Raw assistant content blocks. Append these to the history verbatim on the next turn
    # (thinking blocks must round-trip unchanged).
    content: list[dict] = field(default_factory=list)
    request_id: str | None = None

    def assistant_message(self) -> dict:
        return {"role": "assistant", "content": self.content}


class ModelGateway(Protocol):
    async def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> ModelResponse:
        """One model call. `tools` use the Anthropic shape: {name, description, input_schema}."""
        ...


class FakeGateway:
    """Plays back scripted responses in order and records every call. Costs nothing."""

    def __init__(self, script: list[ModelResponse]):
        self._script = list(script)
        self.calls: list[dict] = []

    async def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> ModelResponse:
        self.calls.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools})
        if not self._script:
            raise GatewayError("FakeGateway script exhausted", retryable=False)
        return self._script.pop(0)


def text_response(text: str, *, stop_reason: str = "end_turn", usage: Usage | None = None) -> ModelResponse:
    return ModelResponse(
        text=text,
        tool_calls=[],
        stop_reason=stop_reason,
        usage=usage or Usage(100, 20),
        cost_usd=0.0,
        latency_s=0.0,
        model="fake",
        content=[{"type": "text", "text": text}],
    )


def tool_use_response(*calls: tuple[str, str, dict], text: str = "", usage: Usage | None = None) -> ModelResponse:
    """Build a response requesting one or more tool calls: (id, name, input) each."""
    content: list[dict] = [{"type": "text", "text": text}] if text else []
    content += [{"type": "tool_use", "id": cid, "name": name, "input": args} for cid, name, args in calls]
    return ModelResponse(
        text=text,
        tool_calls=[ToolCall(cid, name, args) for cid, name, args in calls],
        stop_reason="tool_use",
        usage=usage or Usage(100, 20),
        cost_usd=0.0,
        latency_s=0.0,
        model="fake",
        content=content,
    )
