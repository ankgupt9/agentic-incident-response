"""One governed diagnosis per alert: kill switch, budgets, audit, guardrails, tracing, then the loop."""

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

from agent.anthropic_gateway import AnthropicGateway
from agent.audit import AuditUnavailable
from agent.gateway import ModelGateway
from agent.governed import GovernedToolbox
from agent.loop import run_diagnosis
from agent.proposals import CompositeToolbox, ProposalToolbox
from agent.runtime import Runtime
from agent.schemas import Alert, DiagnosisResult
from agent.toolbox import McpToolbox, Toolbox
from agent.tracing import TracedGateway, TracedToolbox

Runner = Callable[[Alert, str], Awaitable[DiagnosisResult]]  # (alert, diagnosis id)
ToolboxFactory = Callable[[], AbstractAsyncContextManager[Toolbox]]


async def current_knowledge_version() -> str | None:
    try:
        from knowledge_store.db import KnowledgeStore
        import os

        return await KnowledgeStore(schema=os.getenv("KNOWLEDGE_SCHEMA", "public")).current_version()
    except Exception:
        return None


def make_runner(rt: Runtime, gateway: ModelGateway | None = None, toolbox_factory: ToolboxFactory | None = None) -> Runner:
    s, audit, tel = rt.settings, rt.audit, rt.telemetry
    gateway = gateway or TracedGateway(AnthropicGateway(s), tel, s.model)
    factory = toolbox_factory or (lambda: McpToolbox(env={"KNOWLEDGE_TOOLS": "on" if s.knowledge_tools else "off"}))

    def refused(alert: Alert, reason: str) -> DiagnosisResult:
        tel.diagnoses.add(1, {"status": "halted"})
        return DiagnosisResult(alert=alert, status="halted", error=reason)

    async def stop() -> str | None:
        return "kill switch is engaged" if await rt.kill_switch.engaged() else None

    async def runner(alert: Alert, diagnosis_id: str) -> DiagnosisResult:
        # Gate 1: the kill switch and the daily budget decide whether a diagnosis starts at all.
        reason = None
        if await rt.kill_switch.engaged():
            reason = "kill switch is engaged, so no new diagnosis was started"
        elif await audit.spend_today_usd() >= s.daily_budget_usd:
            reason = f"the daily budget of ${s.daily_budget_usd:.2f} is spent, so no new diagnosis was started"
        if reason:
            try:
                await audit.record("diagnosis_refused", diagnosis_id=diagnosis_id, alert=alert.name, reason=reason)
            except AuditUnavailable:
                pass
            return refused(alert, reason)
        try:
            await audit.record("diagnosis_started", diagnosis_id=diagnosis_id, alert=alert.name, model=s.model, effort=s.effort,
                               knowledge_tools=s.knowledge_tools, propose_actions=s.propose_actions)
        except AuditUnavailable:
            return refused(alert, "the audit log is unavailable, so no diagnosis was started (no audit, no action)")

        with tel.diagnosis_span(alert) as span:
            async with factory() as base:
                parts: list[Toolbox] = [base] + ([ProposalToolbox(rt.actions, diagnosis_id)] if s.propose_actions else [])
                governed = GovernedToolbox(
                    CompositeToolbox(*parts), rt.guardrails, audit, rt.kill_switch, diagnosis_id,
                    on_block=lambda kind, tool: tel.guardrail_blocks.add(1, {"kind": kind, "tool": tool}),
                )
                result = await run_diagnosis(alert, gateway, TracedToolbox(governed, tel), budget_usd=s.max_cost_per_diagnosis_usd, should_stop=stop)
            if s.knowledge_tools:
                result.knowledge_version = await current_knowledge_version()
            tel.record_diagnosis(span, result)

        dg = result.diagnosis
        try:
            await audit.record(
                "diagnosis_finished", diagnosis_id=diagnosis_id, alert=alert.name, status=result.status, model=s.model, steps=result.steps,
                tool_calls=result.tool_calls, input_tokens=result.input_tokens, output_tokens=result.output_tokens, cost_usd=result.cost_usd,
                latency_s=result.latency_s, knowledge_version=result.knowledge_version, error=result.error,
                category=dg.category if dg else None, confidence=dg.confidence if dg else None, knowledge_refs=dg.knowledge_refs if dg else None,
            )
        except AuditUnavailable:
            pass  # the diagnosis already happened; the missing record is visible as a gap in the audit trail
        return result

    return runner
