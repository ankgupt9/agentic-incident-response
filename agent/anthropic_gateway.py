"""Anthropic implementation of ModelGateway. The only file that imports the Anthropic SDK."""

import time

import anthropic

from agent.config import Settings, cost_usd
from agent.gateway import GatewayError, ModelResponse, ToolCall, Usage

_STOP_REASONS = {"end_turn", "tool_use", "max_tokens", "refusal"}


class AnthropicGateway:
    def __init__(self, settings: Settings | None = None, client: anthropic.AsyncAnthropic | None = None):
        self.settings = settings or Settings.from_env()
        # The SDK reads ANTHROPIC_API_KEY from the environment; the key is never handled here.
        self._client = client or anthropic.AsyncAnthropic(
            timeout=self.settings.timeout_s, max_retries=self.settings.max_retries
        )

    async def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> ModelResponse:
        s = self.settings
        kwargs: dict = {
            "model": s.model,
            "max_tokens": s.max_tokens,
            "system": system,
            "messages": messages,
            "output_config": {"effort": s.effort},
        }
        if tools:
            kwargs["tools"] = tools
        start = time.perf_counter()
        try:
            resp = await self._client.messages.create(**kwargs)
        except anthropic.APIStatusError as exc:
            raise GatewayError(f"model call failed: HTTP {exc.status_code}", retryable=exc.status_code >= 500 or exc.status_code == 429) from exc
        except anthropic.APIConnectionError as exc:
            raise GatewayError("model call failed: connection error", retryable=True) from exc
        latency = time.perf_counter() - start

        content = [block.model_dump(exclude_none=True) for block in resp.content]
        text = "".join(b["text"] for b in content if b["type"] == "text")
        tool_calls = [ToolCall(b["id"], b["name"], b["input"]) for b in content if b["type"] == "tool_use"]
        usage = Usage(resp.usage.input_tokens, resp.usage.output_tokens)
        return ModelResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=resp.stop_reason if resp.stop_reason in _STOP_REASONS else "other",
            usage=usage,
            cost_usd=cost_usd(s.model, usage.input_tokens, usage.output_tokens),
            latency_s=latency,
            model=resp.model,
            content=content,
            request_id=getattr(resp, "_request_id", None),
        )
