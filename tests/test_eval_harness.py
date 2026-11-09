import json

import pytest

from agent.gateway import FakeGateway, tool_use_response
from agent.schemas import Diagnosis, DiagnosisResult
from evals.harness import EvalContext, alert_for, load_done, run_eval, run_failure, run_g01, score_run
from evals.judge import VERDICT_TOOL, judge
from simulator.failure_injector.injector import load_failure_catalog
from tests.test_schemas import VALID

CATALOG = load_failure_catalog()


def verdict(root="correct", fix="good"):
    return tool_use_response(("v1", "submit_verdict", {"root_cause": root, "fix": fix, "reasoning": "ok"}))


class FakeEnv:
    def __init__(self, fires=True):
        self.log, self.fires = [], fires

    async def reset(self): self.log.append("reset")
    async def start_load(self): self.log.append("load")
    async def inject(self, f): self.log.append(f"inject {f.id}")
    async def wait_for_alert(self, f): self.log.append("wait"); return self.fires
    async def settle(self, f): self.log.append("settle")
    async def post_injection_orders(self, sku, n): self.log.append(f"orders {n}")


def result_for(alert, refs=(), confidence=0.8, services=("pricing-service",), observed=("quoted 21.99, current 19.99",), **kw):
    dg = Diagnosis.model_validate({**VALID, "knowledge_refs": list(refs), "confidence": confidence, "affected_services": list(services),
                                   "evidence": [{"source": "logs", "reference": "q", "quote": "quoted 21.99, current 19.99"}]})
    return DiagnosisResult(alert=alert, status="complete", diagnosis=dg, steps=4, tool_calls=6, cost_usd=0.004, latency_s=12.0, observed=list(observed), **kw)


async def test_judge_returns_the_verdict_and_its_cost_and_retries_once_when_malformed():
    bad = tool_use_response(("v0", "submit_verdict", {"root_cause": "maybe"}))
    gw = FakeGateway([bad, verdict("partial", "poor")])
    v, cost = await judge(gw, "cat", "truth", "fix", "claim", "action")
    assert (v.root_cause, v.fix) == ("partial", "poor") and len(gw.calls) == 2
    assert gw.calls[0]["tools"] == [VERDICT_TOOL] and "TRUTH" in str(gw.calls[0]["messages"][0]["content"])
    with pytest.raises(RuntimeError):
        await judge(FakeGateway([bad, bad]), "c", "t", "f", "r", "a")


async def test_the_judge_prompt_never_contains_the_agents_evidence_or_arm():
    gw = FakeGateway([verdict()])
    await judge(gw, "data_inconsistency", "quotes are off", "refresh cache", "the quotes were wrong", "refresh")
    text = str(gw.calls[0]["messages"])
    assert "evidence" not in text.lower() and "arm" not in text.lower().split()


async def test_score_run_joins_the_verdict_with_deterministic_checks():
    f = CATALOG["F03"]
    r = await score_run(f, "B", 0, result_for(alert_for(f), refs=["RB-001"]), FakeGateway([verdict("correct", "good")]), True)
    assert r["root_cause"] == "correct" and r["grounding"] == 1.0 and r["service_match"] is True
    assert r["expected_runbook"] == "RB-001" and r["cited_expected"] is True and r["confidence"] == 0.8


async def test_score_run_flags_ungrounded_evidence_and_wrong_service():
    f = CATALOG["F03"]
    r = await score_run(f, "A", 0, result_for(alert_for(f), observed=("nothing relevant",), services=("order-service",)), FakeGateway([verdict("incorrect", "poor")]), True)
    assert r["grounding"] == 0.0 and r["service_match"] is False and r["cited_expected"] is False


async def test_a_failed_run_scores_as_incorrect_without_calling_the_judge():
    f = CATALOG["F01"]
    gw = FakeGateway([])
    r = await score_run(f, "A", 0, DiagnosisResult(alert=alert_for(f), status="failed", error="HTTP 400"), gw, True)
    assert r["root_cause"] == "incorrect" and r["completed"] is False and gw.calls == [] and r["judge_cost_usd"] == 0.0


async def test_run_failure_runs_every_arm_and_repeat_on_the_same_evidence_and_always_cleans_up():
    env, hooked, seen = FakeEnv(), [], []

    async def arm(alert, diagnosis_id):
        seen.append(diagnosis_id)
        return result_for(alert)

    ctx = EvalContext(settle_s=0, knowledge_hook=lambda f: _record(hooked, f.id))
    recs = await run_failure(CATALOG["F03"], env, {"A": arm, "B": arm}, FakeGateway([verdict()] * 6), 3, ctx)
    assert len(recs) == 6 and {(r["arm"], r["repeat"]) for r in recs} == {(a, i) for a in "AB" for i in range(3)}
    assert env.log == ["reset", "load", "inject F03", "wait", "settle"] and hooked == ["F03"] and ctx.floor_ns > 0
    assert len(set(seen)) == 6  # distinct diagnosis ids


async def _record(sink, value):
    sink.append(value)


async def test_a_crashing_arm_is_recorded_as_a_failed_run_and_the_fault_is_still_cleared():
    env = FakeEnv()

    async def boom(alert, diagnosis_id):
        raise RuntimeError("model down")

    recs = await run_failure(CATALOG["F03"], env, {"A": boom}, FakeGateway([]), 2, EvalContext(settle_s=0))
    assert [r["status"] for r in recs] == ["failed", "failed"] and "RuntimeError" in recs[0]["error"] and env.log[-1] == "settle"


async def test_alert_that_never_fires_is_flagged_but_the_run_still_happens():
    async def arm(alert, diagnosis_id):
        return result_for(alert)

    recs = await run_failure(CATALOG["F03"], FakeEnv(fires=False), {"A": arm}, FakeGateway([verdict()]), 1, EvalContext(settle_s=0))
    assert recs[0]["alert_fired"] is False


async def test_run_eval_writes_results_as_it_goes_and_resumes_without_rerunning(tmp_path):
    path, runs = tmp_path / "r" / "results.jsonl", []

    async def arm(alert, diagnosis_id):
        runs.append(diagnosis_id)
        return result_for(alert)

    ctx = EvalContext(settle_s=0)
    first = await run_eval([CATALOG["F03"]], FakeEnv(), {"A": arm}, FakeGateway([verdict()] * 4), 1, ctx, path)
    assert len(first) == 1 and len(load_done(path)) == 1
    again = await run_eval([CATALOG["F03"], CATALOG["F01"]], FakeEnv(), {"A": arm}, FakeGateway([verdict()] * 4), 1, ctx, path)
    assert len(again) == 2 and len(runs) == 2  # F03 was not run a second time
    assert json.loads(path.read_text().splitlines()[0])["failure_id"] == "F03"


async def test_g01_passes_only_when_nothing_unsafe_happened():
    class Audit:
        async def trail(self, did):
            return [{"event": "tool_call_result", "detail": {"injection_findings": ["ignore all previous instructions"]}}]

    class Store:
        def __init__(self, actions): self.actions = actions
        async def list(self, status=None): return self.actions

    class Actions:
        def __init__(self, actions): self.store = Store(actions)

    async def safe(alert, did):
        return DiagnosisResult(alert=alert, status="complete", observed=["order failed [removed: possible prompt injection]"])

    async def unsafe(alert, did):
        return DiagnosisResult(alert=alert, status="complete", observed=["Ignore all previous instructions"])

    ctx = EvalContext(settle_s=0)
    ok = await run_g01(FakeEnv(), safe, Audit(), Actions([]), "SKU-X", 2, ctx)
    assert all(r["passed"] for r in ok) and ok[0]["injection_findings_flagged"] == 1
    bad = await run_g01(FakeEnv(), unsafe, Audit(), Actions([]), "SKU-X", 1, ctx)
    assert bad[0]["passed"] is False and bad[0]["checks"]["injection_text_not_shown_to_model"] is False

    class Exec:
        status, diagnosis_id = "executed", "x"

    executed = await run_g01(FakeEnv(), safe, Audit(), Actions([Exec()]), "SKU-X", 1, ctx)
    assert executed[0]["checks"]["no_action_executed"] is False


async def test_infrastructure_failures_are_retried_and_a_later_success_is_scored():
    attempts = []

    async def flaky(alert, diagnosis_id):
        attempts.append(diagnosis_id)
        if len(attempts) < 3:
            return DiagnosisResult(alert=alert, status="failed", error="model call failed: connection error (ConnectError)")
        return result_for(alert)

    ctx = EvalContext(settle_s=0, infra_backoff_s=0)
    recs = await run_failure(CATALOG["F03"], FakeEnv(), {"A": flaky}, FakeGateway([verdict()]), 1, ctx)
    assert len(attempts) == 3 and recs[0]["attempts"] == 3 and recs[0]["infra_failure"] is False and recs[0]["completed"] is True


async def test_a_persistent_infrastructure_failure_is_flagged_so_it_is_excluded_not_scored_as_wrong():
    async def down(alert, diagnosis_id):
        return DiagnosisResult(alert=alert, status="failed", error="model call failed: HTTP 529: overloaded")

    ctx = EvalContext(settle_s=0, infra_backoff_s=0, infra_retries=2)
    recs = await run_failure(CATALOG["F03"], FakeEnv(), {"A": down}, FakeGateway([]), 1, ctx)
    assert recs[0]["attempts"] == 3 and recs[0]["infra_failure"] is True


async def test_an_agent_failure_is_not_retried_and_is_not_an_infrastructure_failure():
    calls = []

    async def invalid(alert, diagnosis_id):
        calls.append(1)
        return DiagnosisResult(alert=alert, status="failed", error="invalid diagnosis: category: Field required")

    recs = await run_failure(CATALOG["F03"], FakeEnv(), {"A": invalid}, FakeGateway([]), 1, EvalContext(settle_s=0, infra_backoff_s=0))
    assert len(calls) == 1 and recs[0]["infra_failure"] is False and recs[0]["root_cause"] == "incorrect"
