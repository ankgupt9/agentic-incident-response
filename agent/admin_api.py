"""Admin surface: the approval gate and the kill switch. Protected by AGENT_ADMIN_TOKEN.

If the token is not configured every endpoint refuses with 503, so a forgotten setting disables
approvals instead of leaving them open. The token is compared in constant time and is never logged.
"""

import hmac
import os

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agent.actions import ActionError, ActionService
from agent.audit import AuditLog
from agent.killswitch import KillSwitch


class Decision(BaseModel):
    actor: str
    comment: str | None = None


class KillSwitchChange(BaseModel):
    engaged: bool
    actor: str
    reason: str


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    expected = os.getenv("AGENT_ADMIN_TOKEN")
    if not expected:
        raise HTTPException(503, "AGENT_ADMIN_TOKEN is not configured, so admin endpoints are disabled")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(401, "invalid admin token")


def build_admin_router(service: ActionService, kill_switch: KillSwitch, audit: AuditLog) -> APIRouter:
    router = APIRouter()

    @router.get("/approvals", dependencies=[Depends(require_admin)])
    async def list_approvals(status: str | None = None) -> list[dict]:
        actions = await service.store.list(status)
        return [a.model_dump(mode="json") for a in actions]

    @router.post("/approvals/{action_id}/approve", dependencies=[Depends(require_admin)])
    async def approve(action_id: str, body: Decision) -> dict:
        try:
            return (await service.approve(action_id, body.actor, body.comment)).model_dump(mode="json")
        except ActionError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/approvals/{action_id}/reject", dependencies=[Depends(require_admin)])
    async def reject(action_id: str, body: Decision) -> dict:
        try:
            return (await service.reject(action_id, body.actor, body.comment)).model_dump(mode="json")
        except ActionError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/admin/kill-switch", dependencies=[Depends(require_admin)])
    async def kill_switch_state() -> dict:
        return {"engaged": await kill_switch.engaged()}

    @router.post("/admin/kill-switch", dependencies=[Depends(require_admin)])
    async def change_kill_switch(body: KillSwitchChange) -> dict:
        await kill_switch.set(body.engaged, body.actor, body.reason)
        return {"engaged": body.engaged}

    @router.get("/approvals/ui", response_class=HTMLResponse)
    async def page() -> str:
        return APPROVAL_PAGE

    return router


# A deliberately plain page. The token is typed into the page and sent as a header; it is never put in a URL.
# Dynamic text is written with textContent, never innerHTML, so proposal text cannot inject markup.
APPROVAL_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Approvals</title>
<style>body{font:15px system-ui;margin:2rem auto;max-width:56rem;padding:0 1rem}fieldset{margin:1rem 0}
pre{white-space:pre-wrap;background:#f4f4f4;padding:.6rem;border-radius:4px}button{margin-right:.5rem}.meta{color:#555}</style></head>
<body><h1>Proposed actions</h1>
<p>Nothing runs until you approve it. Actions run as a dry run unless real execution is enabled on the server.</p>
<label>Admin token <input id="token" type="password" autocomplete="off"></label>
<label>Your name <input id="actor"></label> <button id="load">Load</button>
<div id="list"></div><p id="msg" class="meta"></p>
<script>
const $ = id => document.getElementById(id);
const headers = () => ({"X-Admin-Token": $("token").value, "Content-Type": "application/json"});
function el(tag, text, cls){const e=document.createElement(tag); if(text!==undefined) e.textContent=text; if(cls) e.className=cls; return e;}
async function decide(id, verb){
  const r = await fetch(`/approvals/${id}/${verb}`, {method:"POST", headers:headers(), body:JSON.stringify({actor:$("actor").value})});
  $("msg").textContent = r.ok ? `${verb}d ${id}` : `error ${r.status}: ${(await r.json()).detail}`; load();
}
async function load(){
  const r = await fetch("/approvals", {headers:headers()});
  const box = $("list"); box.replaceChildren();
  if(!r.ok){ $("msg").textContent = `error ${r.status}: ${(await r.json()).detail}`; return; }
  for(const a of await r.json()){
    const f = el("fieldset"); f.append(el("legend", `${a.id} - ${a.tool} - ${a.status}`));
    f.append(el("div", `diagnosis ${a.diagnosis_id}, created ${a.created_at}`, "meta"));
    f.append(el("pre", `reason: ${a.reason}\\n\\nargs: ${JSON.stringify(a.args, null, 2)}` + (a.result ? `\\n\\nresult: ${a.result}` : "")));
    if(a.status === "pending"){
      const ok = el("button", "Approve"); ok.onclick = () => decide(a.id, "approve");
      const no = el("button", "Reject"); no.onclick = () => decide(a.id, "reject");
      f.append(ok, no);
    }
    box.append(f);
  }
}
$("load").onclick = load;
</script></body></html>
"""
