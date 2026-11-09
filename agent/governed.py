"""GovernedToolbox: every tool call passes the kill switch, the guardrails and the audit log.

It wraps any Toolbox, so the loop and the tools stay unaware of governance. Order of checks:
kill switch, allowlist and rate limits, audit (before the call), the call, output filter, audit
(after the call). If the audit log cannot be written the call is refused: no audit, no action.
"""

from collections.abc import Callable

from agent.audit import AuditLog, AuditUnavailable
from agent.guardrails import Guardrails, RunState
from agent.killswitch import KillSwitch
from agent.toolbox import Toolbox, ToolResult

BlockHook = Callable[[str, str], None]  # (kind, tool name), for metrics


class GovernedToolbox:
    def __init__(
        self,
        inner: Toolbox,
        guardrails: Guardrails,
        audit: AuditLog,
        kill_switch: KillSwitch,
        diagnosis_id: str,
        on_block: BlockHook | None = None,
    ):
        self._inner, self._guard, self._audit, self._kill = inner, guardrails, audit, kill_switch
        self._diagnosis_id, self._on_block = diagnosis_id, on_block
        self._run = RunState()

    def specs(self) -> list[dict]:
        # Tools off the allowlist are never even declared to the model.
        return [s for s in self._inner.specs() if s["name"] in self._guard.config.allowed_tools]

    async def _block(self, kind: str, name: str, reason: str) -> ToolResult:
        if self._on_block:
            self._on_block(kind, name)
        try:
            await self._audit.record("guardrail_block", diagnosis_id=self._diagnosis_id, kind=kind, tool=name, reason=reason)
        except AuditUnavailable:
            pass  # the call is refused either way
        return ToolResult(f"refused: {reason}", is_error=True)

    async def call(self, name: str, args: dict) -> ToolResult:
        if await self._kill.engaged():
            return await self._block("kill_switch", name, "kill switch is engaged")
        reason = self._guard.check_tool(self._run, name)
        if reason:
            return await self._block("policy", name, reason)
        try:
            await self._audit.record("tool_call_requested", diagnosis_id=self._diagnosis_id, tool=name, args=args)
        except AuditUnavailable:
            return ToolResult("refused: audit log unavailable, so the call was not made", is_error=True)

        try:
            result = await self._inner.call(name, args)
        except Exception as exc:
            result = ToolResult(f"tool call failed: {type(exc).__name__}", is_error=True)

        filtered = self._guard.filter_output(result.text)
        if filtered.findings:
            if self._on_block:
                self._on_block("injection", name)
            text = filtered.text + f"\n[guardrail: {len(filtered.findings)} instruction-like phrase(s) were removed from this output]"
        else:
            text = filtered.text
        try:
            await self._audit.record(
                "tool_call_result", diagnosis_id=self._diagnosis_id, tool=name, is_error=result.is_error,
                output_chars=len(result.text), injection_findings=filtered.findings,
            )
        except AuditUnavailable:
            return ToolResult("refused: audit log unavailable, so the result was withheld", is_error=True)
        return ToolResult(text, result.is_error)
