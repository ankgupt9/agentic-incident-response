"""Assembles the governance pieces one agent process needs: audit log, kill switch, approval store,
action service, guardrails. `memory=True` keeps everything in process (tests and evals)."""

import os
from dataclasses import dataclass

from agent.actions import ActionService, Executor, GitHubDraftPR, MemoryApprovalStore
from agent.approval_store_pg import PgApprovalStore
from agent.audit import AuditLog, MemoryAuditLog, PgAuditLog
from agent.config import Settings
from agent.guardrails import GuardrailConfig, Guardrails
from agent.killswitch import KillSwitch, MemoryKillSwitch, PgKillSwitch
from agent.prompts import KNOWLEDGE_TOOLS, PROPOSE_TOOLS
from agent.tracing import AgentTelemetry

TELEMETRY_TOOLS = frozenset({"query_logs", "get_trace", "query_metrics"})


def allowed_tools(settings: Settings) -> frozenset[str]:
    """The explicit allowlist. A tool exists for the agent only if it is named here for this configuration."""
    allowed = set(TELEMETRY_TOOLS)
    if settings.knowledge_tools:
        allowed |= KNOWLEDGE_TOOLS
    if settings.propose_actions:
        allowed |= PROPOSE_TOOLS
    return frozenset(allowed)


async def _rabbit_publisher(queue: str, body: bytes) -> None:
    import aio_pika

    connection = await aio_pika.connect_robust(os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/"))
    async with connection:
        channel = await connection.channel()
        await channel.declare_queue(queue, durable=True)
        await channel.default_exchange.publish(aio_pika.Message(body, content_type="application/json", delivery_mode=aio_pika.DeliveryMode.PERSISTENT), routing_key=queue)


def make_executor(settings: Settings) -> Executor:
    token, repo = os.getenv("GITHUB_TOKEN"), os.getenv("GITHUB_REPO", "ankgupt9/agentic-incident-response")
    github = GitHubDraftPR(token, repo) if token else None
    return Executor(dry_run=settings.actions_dry_run, publisher=_rabbit_publisher, github=github)


@dataclass
class Runtime:
    settings: Settings
    telemetry: AgentTelemetry
    audit: AuditLog
    kill_switch: KillSwitch
    actions: ActionService
    guardrails: Guardrails


def _on_action_event(telemetry: AgentTelemetry):
    def hook(kind: str, labels: dict) -> None:
        (telemetry.actions_proposed if kind == "proposed" else telemetry.actions_decided).add(1, labels)

    return hook


async def build_runtime(settings: Settings | None = None, telemetry: AgentTelemetry | None = None, memory: bool = False) -> Runtime:
    from agent.tracing import setup_agent_telemetry

    settings = settings or Settings.from_env()
    telemetry = telemetry or setup_agent_telemetry()
    if memory:
        audit: AuditLog = MemoryAuditLog()
        kill_switch: KillSwitch = MemoryKillSwitch(audit)
        store = MemoryApprovalStore()
    else:
        pg_audit, pg_kill, store = PgAuditLog(), None, PgApprovalStore()
        pg_kill = PgKillSwitch(pg_audit)
        await pg_audit.apply_schema()
        await pg_kill.apply_schema()
        await store.apply_schema()
        audit, kill_switch = pg_audit, pg_kill
    actions = ActionService(store, audit, make_executor(settings), on_event=_on_action_event(telemetry))
    guardrails = Guardrails(GuardrailConfig(
        allowed_tools=allowed_tools(settings),
        max_cost_per_diagnosis_usd=settings.max_cost_per_diagnosis_usd,
        daily_budget_usd=settings.daily_budget_usd,
    ))
    # Hard rule 10: the model version in use is audit-logged.
    await audit.record("model_pinned", model=settings.model, effort=settings.effort, knowledge_tools=settings.knowledge_tools,
                       propose_actions=settings.propose_actions, actions_dry_run=settings.actions_dry_run)
    return Runtime(settings, telemetry, audit, kill_switch, actions, guardrails)
