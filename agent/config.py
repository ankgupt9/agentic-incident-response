"""Agent settings: pinned model, limits, and the price table used for cost tracking."""

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE lines into os.environ without overriding real env vars. Never prints values."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if value.strip():
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens. Optional higher tier for prompts above long_threshold input tokens."""

    input: float
    output: float
    long_threshold: int | None = None
    long_input: float | None = None
    long_output: float | None = None


# Prices as of 2026-10. Unknown models raise on purpose: a silent $0 would corrupt cost tracking.
PRICES: dict[str, ModelPrice] = {
    "claude-haiku-5-5": ModelPrice(0.10, 0.50, 100_000, 0.50, 2.50),
    "claude-sonnet-5-5": ModelPrice(2.00, 10.00),
    "claude-opus-5-5": ModelPrice(4.00, 20.00),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price = PRICES[model]
    if price.long_threshold is not None and input_tokens > price.long_threshold:
        in_rate, out_rate = price.long_input, price.long_output
    else:
        in_rate, out_rate = price.input, price.output
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


@dataclass(frozen=True)
class Settings:
    # Pinned in config, never "latest" (hard rule 10). Override with AGENT_MODEL.
    model: str = "claude-haiku-5-5"
    max_tokens: int = 4096
    effort: str = "medium"
    timeout_s: float = 60.0
    max_retries: int = 2

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        defaults = cls()
        return cls(
            model=os.getenv("AGENT_MODEL", defaults.model),
            max_tokens=int(os.getenv("AGENT_MAX_TOKENS", defaults.max_tokens)),
            effort=os.getenv("AGENT_EFFORT", defaults.effort),
        )
