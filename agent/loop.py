"""The core agent loop. Owner: Ankur.

Contract (tests/test_loop.py defines the behavior it must satisfy):

    run_diagnosis(alert, gateway, toolbox, max_steps=8) -> DiagnosisResult

The model never runs tools. It requests them; this loop executes each request through the
toolbox, feeds the results back, and decides when to stop. See docs/loop-checklist.md.
"""

from agent.gateway import ModelGateway
from agent.schemas import Alert, DiagnosisResult
from agent.toolbox import Toolbox


async def run_diagnosis(
    alert: Alert,
    gateway: ModelGateway,
    toolbox: Toolbox,
    max_steps: int = 8,
) -> DiagnosisResult:
    raise NotImplementedError("Ankur: write the loop (see docs/loop-checklist.md)")
