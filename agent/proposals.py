"""The model's side of the approval gate: tools that PROPOSE actions and never perform them."""

from agent.actions import ActionError, ActionService
from agent.toolbox import Toolbox, ToolResult

PROPOSE_SPECS = [
    {
        "name": "propose_replay_message",
        "description": (
            "Propose replaying ONE order message into the SANDBOX queue so a human can test a fix safely. "
            "This only queues a request for human approval; nothing is sent until a person approves it. "
            "message_json is a single order message as JSON with order_id, sku, quantity, customer_id, quoted_price."
        ),
        "input_schema": {"type": "object", "properties": {"message_json": {"type": "string"}, "reason": {"type": "string"}}, "required": ["message_json", "reason"]},
    },
    {
        "name": "propose_draft_pull_request",
        "description": (
            "Propose a DRAFT pull request that adds a markdown file describing the fix under proposed-fixes/. "
            "This only queues a request for human approval; nothing is opened until a person approves it."
        ),
        "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "body_markdown": {"type": "string"}, "reason": {"type": "string"}}, "required": ["title", "body_markdown", "reason"]},
    },
]
_TOOL = {"propose_replay_message": "replay_message", "propose_draft_pull_request": "draft_pull_request"}


class ProposalToolbox:
    def __init__(self, service: ActionService, diagnosis_id: str):
        self._service, self._diagnosis_id = service, diagnosis_id

    def specs(self) -> list[dict]:
        return list(PROPOSE_SPECS)

    async def call(self, name: str, args: dict) -> ToolResult:
        if name not in _TOOL:
            return ToolResult(f"unknown tool {name!r}", is_error=True)
        try:
            action = await self._service.propose(self._diagnosis_id, _TOOL[name], {k: v for k, v in args.items() if k != "reason"}, str(args.get("reason", "")))
        except ActionError as exc:
            return ToolResult(f"proposal rejected: {exc}", is_error=True)
        return ToolResult(f"queued for human approval as {action.id}. It has NOT been executed and will not run unless a person approves it.")


class CompositeToolbox:
    """Presents several toolboxes as one, routing each call by tool name."""

    def __init__(self, *parts: Toolbox):
        self._parts, self._route = parts, {}
        for part in parts:
            for spec in part.specs():
                self._route[spec["name"]] = part

    def specs(self) -> list[dict]:
        return [spec for part in self._parts for spec in part.specs()]

    async def call(self, name: str, args: dict) -> ToolResult:
        part = self._route.get(name)
        return await part.call(name, args) if part else ToolResult(f"unknown tool {name!r}", is_error=True)
