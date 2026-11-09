"""Actions and the approval gate. REVIEW EVERY LINE: this is the only code that can change anything.

Flow: the model proposes (writes a pending record, executes nothing) -> a human approves or rejects
-> only an approved action is executed -> execution is a dry run unless real execution is switched on.

Two action tools exist, both narrow on purpose:
  replay_message       publishes ONE order message to the sandbox queue `orders.sandbox`, never the real queue.
  draft_pull_request   opens a DRAFT pull request that adds ONE markdown file under `proposed-fixes/`.
Arguments are validated when proposed and again when executed.
"""

import base64
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from agent.audit import AuditLog
from simulator.common.contracts import OrderMessage

SANDBOX_QUEUE = "orders.sandbox"
PR_DIR = "proposed-fixes/"
PENDING_TTL = timedelta(hours=24)
AGENT_ACTOR = "agent"
ActionTool = Literal["replay_message", "draft_pull_request"]
Status = Literal["pending", "approved", "rejected", "executed", "failed", "expired"]


class ActionError(Exception):
    """An action is invalid, not allowed, or cannot be carried out. The message is safe to show."""


class ProposedAction(BaseModel):
    id: str = Field(default_factory=lambda: f"ACT-{uuid.uuid4().hex[:8]}")
    diagnosis_id: str
    tool: ActionTool
    args: dict
    reason: str
    status: Status = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc) + PENDING_TTL)
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_comment: str | None = None
    result: str | None = None


# ---------------------------------------------------------------- validation (used twice)

def validate_replay(args: dict) -> dict:
    """Exactly one valid order message, destined for the sandbox queue."""
    if args.get("queue", SANDBOX_QUEUE) != SANDBOX_QUEUE:
        raise ActionError(f"replay is only allowed to the sandbox queue {SANDBOX_QUEUE!r}")
    raw = args.get("message_json", "")
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 2000:
        raise ActionError("message_json must be a JSON string of at most 2000 characters")
    try:
        message = OrderMessage.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ActionError("message_json must be a single valid order message (see the order-message contract)") from exc
    return {"queue": SANDBOX_QUEUE, "message_json": message.model_dump_json()}


def validate_draft_pr(args: dict, diagnosis_id: str) -> dict:
    """A draft PR that adds one markdown file under proposed-fixes/. Never touches code."""
    title, body = str(args.get("title", "")).strip(), str(args.get("body_markdown", "")).strip()
    path = str(args.get("path") or f"{PR_DIR}{diagnosis_id}.md")
    if not title or len(title) > 120:
        raise ActionError("title must be 1 to 120 characters")
    if not body or len(body) > 6000:
        raise ActionError("body_markdown must be 1 to 6000 characters")
    if not path.startswith(PR_DIR) or not path.endswith(".md") or ".." in path or path.count("/") != 1 or not re.fullmatch(r"[A-Za-z0-9._/-]+", path):
        raise ActionError(f"path must be a single markdown file directly under {PR_DIR}")
    return {"title": title, "body_markdown": body, "path": path}


def validate_action(tool: str, args: dict, diagnosis_id: str) -> dict:
    if tool == "replay_message":
        return validate_replay(args)
    if tool == "draft_pull_request":
        return validate_draft_pr(args, diagnosis_id)
    raise ActionError(f"unknown action tool {tool!r}")


# ---------------------------------------------------------------- execution

class GitHubDraftPR:
    """Opens a draft PR through the GitHub REST API. Needs GITHUB_TOKEN (scoped to this repo) and GITHUB_REPO."""

    def __init__(self, token: str, repo: str, client: httpx.AsyncClient | None = None):
        self.repo = repo
        self._http = client or httpx.AsyncClient(base_url="https://api.github.com", timeout=15)
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    async def _call(self, method: str, path: str, **kw) -> dict:
        resp = await self._http.request(method, path, headers=self._headers, **kw)
        if resp.status_code >= 400:
            raise ActionError(f"GitHub returned HTTP {resp.status_code} for {method} {path.split('?')[0]}")
        return resp.json()

    async def create(self, title: str, body_markdown: str, path: str, branch: str) -> str:
        repo = (await self._call("GET", f"/repos/{self.repo}"))["default_branch"]
        sha = (await self._call("GET", f"/repos/{self.repo}/git/ref/heads/{repo}"))["object"]["sha"]
        await self._call("POST", f"/repos/{self.repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha})
        content = base64.b64encode(body_markdown.encode()).decode()
        await self._call("PUT", f"/repos/{self.repo}/contents/{path}", json={"message": f"Proposed fix: {title}", "content": content, "branch": branch})
        pr = await self._call("POST", f"/repos/{self.repo}/pulls", json={"title": title, "head": branch, "base": repo, "body": body_markdown, "draft": True})
        return pr["html_url"]


Publisher = Callable[[str, bytes], Awaitable[None]]


class Executor:
    """Carries out an approved action. Dry run by default (hard rule 1): real effects need dry_run=False."""

    def __init__(self, dry_run: bool = True, publisher: Publisher | None = None, github: GitHubDraftPR | None = None):
        self.dry_run, self._publisher, self._github = dry_run, publisher, github

    async def execute(self, action: ProposedAction) -> str:
        args = validate_action(action.tool, action.args, action.diagnosis_id)  # validated again at execution time
        if action.tool == "replay_message":
            if self.dry_run:
                return f"DRY RUN: would publish 1 message to {args['queue']}"
            if self._publisher is None:
                raise ActionError("no broker publisher configured")
            await self._publisher(args["queue"], args["message_json"].encode())
            return f"published 1 message to {args['queue']}"
        if self.dry_run:
            return f"DRY RUN: would open a draft pull request adding {args['path']}"
        if self._github is None:
            raise ActionError("no GitHub token configured, so no pull request can be opened")
        url = await self._github.create(args["title"], args["body_markdown"], args["path"], f"agent/{action.id.lower()}")
        return f"opened draft pull request {url}"


# ---------------------------------------------------------------- storage

class ApprovalStore(Protocol):
    async def create(self, action: ProposedAction) -> None: ...
    async def get(self, action_id: str) -> ProposedAction | None: ...
    async def list(self, status: str | None = None) -> list[ProposedAction]: ...
    async def decide(self, action_id: str, approved: bool, actor: str, comment: str | None) -> ProposedAction | None:
        """Move pending -> approved/rejected atomically. None if it was not pending or has expired."""
    async def finish(self, action_id: str, ok: bool, result: str) -> None: ...


class MemoryApprovalStore:
    def __init__(self) -> None:
        self._actions: dict[str, ProposedAction] = {}

    async def create(self, action: ProposedAction) -> None:
        self._actions[action.id] = action

    async def get(self, action_id: str) -> ProposedAction | None:
        return self._actions.get(action_id)

    async def list(self, status: str | None = None) -> list[ProposedAction]:
        return [a for a in self._actions.values() if status in (None, a.status)]

    async def decide(self, action_id: str, approved: bool, actor: str, comment: str | None) -> ProposedAction | None:
        a = self._actions.get(action_id)
        if a is None or a.status != "pending":
            return None
        if a.expires_at <= datetime.now(timezone.utc):
            a.status = "expired"
            return None
        a.status, a.decided_by, a.decided_at, a.decision_comment = ("approved" if approved else "rejected"), actor, datetime.now(timezone.utc), comment
        return a

    async def finish(self, action_id: str, ok: bool, result: str) -> None:
        a = self._actions[action_id]
        a.status, a.result = ("executed" if ok else "failed"), result


# ---------------------------------------------------------------- the gate

class ActionService:
    """Propose, decide, execute. The only path from a model suggestion to an effect."""

    def __init__(self, store: ApprovalStore, audit: AuditLog, executor: Executor, on_event: Callable[[str, dict], None] | None = None):
        self.store, self.audit, self.executor, self._on_event = store, audit, executor, on_event

    def _emit(self, kind: str, **labels) -> None:
        if self._on_event:
            self._on_event(kind, labels)

    async def propose(self, diagnosis_id: str, tool: str, args: dict, reason: str) -> ProposedAction:
        clean = validate_action(tool, args, diagnosis_id)
        action = ProposedAction(diagnosis_id=diagnosis_id, tool=tool, args=clean, reason=reason[:500])
        await self.audit.record("action_proposed", diagnosis_id=diagnosis_id, actor=AGENT_ACTOR, action_id=action.id, tool=tool, args=clean, reason=action.reason)
        await self.store.create(action)
        self._emit("proposed", tool=tool)
        return action

    async def approve(self, action_id: str, actor: str, comment: str | None = None) -> ProposedAction:
        if not actor or actor == AGENT_ACTOR:
            raise ActionError("an action cannot be approved by the agent")
        action = await self.store.decide(action_id, True, actor, comment)
        if action is None:
            raise ActionError("action is not pending (unknown, already decided, or expired)")
        await self.audit.record("approval_decided", diagnosis_id=action.diagnosis_id, actor=actor, action_id=action.id, approved=True, comment=comment)
        self._emit("decided", decision="approved")
        try:
            result = await self.executor.execute(action)
            await self.store.finish(action.id, True, result)
            await self.audit.record("action_executed", diagnosis_id=action.diagnosis_id, actor=actor, action_id=action.id, dry_run=self.executor.dry_run, result=result)
        except ActionError as exc:
            await self.store.finish(action.id, False, str(exc))
            await self.audit.record("action_failed", diagnosis_id=action.diagnosis_id, actor=actor, action_id=action.id, error=str(exc))
        return (await self.store.get(action.id))  # type: ignore[return-value]

    async def reject(self, action_id: str, actor: str, comment: str | None = None) -> ProposedAction:
        if not actor:
            raise ActionError("actor is required")
        action = await self.store.decide(action_id, False, actor, comment)
        if action is None:
            raise ActionError("action is not pending (unknown, already decided, or expired)")
        await self.audit.record("approval_decided", diagnosis_id=action.diagnosis_id, actor=actor, action_id=action.id, approved=False, comment=comment)
        self._emit("decided", decision="rejected")
        return action
