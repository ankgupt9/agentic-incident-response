"""Guardrails: rules enforced in code on what the agent may call, how often, and what it reads.

Four checks. (1) Tool allowlist: only named tools may run. (2) Rate limits: per run and per minute.
(3) Budget: per-diagnosis spend is enforced by the loop; the daily budget is checked by the trigger.
(4) Injection filter: log and knowledge text is untrusted data, so phrases that try to give the model
new instructions are cut out and reported. The filter is deliberately a tripwire, not a guarantee:
the guarantee is that nothing the agent reads can execute an action (actions need human approval).
"""

import re
import time
from collections import Counter, deque
from dataclasses import dataclass, field

REMOVED = "[removed: possible prompt injection]"

# Phrases that address the model rather than describe a system event. Kept specific to avoid cutting
# ordinary log text such as "failed to price order" or "price mismatch".
INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier|your) (instructions?|prompts?|rules?)",
    r"disregard (all |any |the )?(previous|prior|above|earlier|your) (instructions?|prompts?|rules?)",
    r"forget (all |any |the )?(previous|prior|above|earlier|your) (instructions?|prompts?|rules?)",
    r"you are now\b",
    r"new (system )?instructions?\s*:",
    r"\bsystem prompt\b",
    r"</?\s*(system|assistant|user|instructions?)\s*>",
    r"\b(approve|execute|run|call|invoke)\b[^.\n]{0,60}\b(propose_\w+|submit_diagnosis|replay_message|draft_pull_request)\b",
    r"\bapprove (this|the|that) (action|fix|change|request)\b",
    r"\bdo not (tell|inform|alert) (the )?(user|human|engineer|on-?call)\b",
    r"\boverride (the )?(guardrails?|safety|approval)\b",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]


@dataclass(frozen=True)
class GuardrailConfig:
    allowed_tools: frozenset[str]
    max_calls_per_tool: int = 12
    max_calls_per_run: int = 30
    max_calls_per_minute: int = 120
    max_cost_per_diagnosis_usd: float = 0.05
    daily_budget_usd: float = 2.0


@dataclass
class RunState:
    """Counters for one diagnosis."""

    calls: int = 0
    by_tool: Counter = field(default_factory=Counter)


@dataclass(frozen=True)
class FilterResult:
    text: str
    findings: list[str]


class Guardrails:
    def __init__(self, config: GuardrailConfig, clock=time.monotonic):
        self.config, self._clock = config, clock
        self._recent: deque[float] = deque()

    def check_tool(self, run: RunState, name: str) -> str | None:
        """Reason the call must be refused, or None. Counts the call when it is allowed."""
        c = self.config
        if name not in c.allowed_tools:
            return f"tool {name!r} is not on the allowlist"
        if run.by_tool[name] >= c.max_calls_per_tool:
            return f"per-tool limit reached for {name} ({c.max_calls_per_tool} calls per diagnosis)"
        if run.calls >= c.max_calls_per_run:
            return f"per-diagnosis tool call limit reached ({c.max_calls_per_run})"
        now = self._clock()
        while self._recent and now - self._recent[0] > 60:
            self._recent.popleft()
        if len(self._recent) >= c.max_calls_per_minute:
            return f"global rate limit reached ({c.max_calls_per_minute} calls per minute)"
        run.calls += 1
        run.by_tool[name] += 1
        self._recent.append(now)
        return None

    @staticmethod
    def filter_output(text: str) -> FilterResult:
        """Cut instruction-like phrases out of untrusted text. Reports what was removed."""
        findings: list[str] = []
        for pattern in _COMPILED:
            def _cut(match: re.Match) -> str:
                findings.append(match.group(0)[:80])
                return REMOVED

            text = pattern.sub(_cut, text)
        return FilterResult(text, findings)

    def over_budget(self, cost_usd: float) -> bool:
        return cost_usd >= self.config.max_cost_per_diagnosis_usd
