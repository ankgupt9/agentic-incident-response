"""Wires gateway, MCP toolbox, tracing and the loop into one callable per alert."""

from collections.abc import Awaitable, Callable

from agent.anthropic_gateway import AnthropicGateway
from agent.config import Settings
from agent.loop import run_diagnosis
from agent.schemas import Alert, DiagnosisResult
from agent.toolbox import McpToolbox
from agent.tracing import AgentTelemetry, TracedGateway, TracedToolbox, setup_agent_telemetry

Runner = Callable[[Alert], Awaitable[DiagnosisResult]]


def build_runner(settings: Settings | None = None, telemetry: AgentTelemetry | None = None) -> Runner:
    settings = settings or Settings.from_env()
    telemetry = telemetry or setup_agent_telemetry()
    gateway = TracedGateway(AnthropicGateway(settings), telemetry, settings.model)

    async def runner(alert: Alert) -> DiagnosisResult:
        with telemetry.diagnosis_span(alert) as span:
            async with McpToolbox(env={"KNOWLEDGE_TOOLS": "on" if settings.knowledge_tools else "off"}) as toolbox:
                result = await run_diagnosis(alert, gateway, TracedToolbox(toolbox, telemetry))
            telemetry.record_diagnosis(span, result)
        return result

    return runner
