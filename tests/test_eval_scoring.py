import pytest

from evals.report import frac, render_report
from evals.scoring import calibration, citation_stats, percentile, summarize


def rec(failure="F03", arm="A", root="correct", fix="good", conf=0.8, completed=True, cost=0.004, latency=10.0, **kw):
    base = dict(failure_id=failure, arm=arm, root_cause=root, fix=fix, confidence=conf if completed else None, completed=completed, cost_usd=cost,
                latency_s=latency, steps=5, tool_calls=8, grounding=1.0, service_match=True, judge_cost_usd=0.005, knowledge_refs=[], cited_expected=False,
                expected_runbook=None)
    return {**base, **kw}


def test_percentile_and_frac():
    assert percentile([1, 2, 3, 4, 5], 0.5) == 3 and percentile([], 0.5) == 0.0 and percentile([7], 0.95) == 7
    assert frac(3, 4) == "3/4 (75%)" and frac(0, 0) == "n/a"


def test_summary_accuracy_strict_versus_lenient_and_costs():
    s = summarize([rec(root="correct"), rec(root="partial", fix="partial"), rec(root="incorrect", fix="poor"), rec(completed=False, root="incorrect", fix="poor", cost=0.002)])
    assert s["n"] == 4 and s["accuracy_strict"] == 0.25 and s["accuracy_lenient"] == pytest.approx(0.375)
    assert s["completed"] == 0.75 and s["fix_quality"] == pytest.approx(0.375) and s["cost_total_usd"] == pytest.approx(0.014)


def test_calibration_is_perfect_when_confidence_matches_accuracy():
    records = [rec(root="correct", conf=0.9)] * 9 + [rec(root="incorrect", conf=0.9)]
    cal = calibration(records)
    assert cal["bins"][0]["accuracy"] == pytest.approx(0.9) and cal["ece"] == pytest.approx(0.0) and cal["brier"] == pytest.approx(0.09)


def test_overconfidence_shows_up_as_calibration_error():
    cal = calibration([rec(root="incorrect", conf=0.95)] * 4)
    assert cal["ece"] == pytest.approx(0.95) and cal["bins"][0]["range"] == "0.9-1.0"


def test_calibration_ignores_runs_without_a_confidence():
    assert calibration([rec(completed=False, root="incorrect")])["n"] == 0


def test_citation_stats_for_the_knowledge_arm():
    records = [rec(expected_runbook="RB-001", knowledge_refs=["RB-001"], cited_expected=True), rec(expected_runbook="RB-002", knowledge_refs=["svc:x"], cited_expected=False), rec()]
    c = citation_stats(records)
    assert c["cited_anything"] == pytest.approx(2 / 3) and c["cited_expected_runbook"] == 0.5 and c["n_with_expected"] == 2


def test_report_renders_both_arms_the_split_and_the_provenance_warning():
    records = [rec("F03", "A", "incorrect", conf=0.3), rec("F03", "B", "correct", conf=0.8, knowledge_refs=["RB-001"], expected_runbook="RB-001", cited_expected=True),
               rec("F02", "A", "partial"), rec("F02", "B", "partial")]
    meta = {"run_id": "r1", "date": "2026-11-10", "commit": "abc", "model": "m", "judge_model": "j", "failures": 2, "repeats": 1, "knowledge_version": "v1"}
    text = render_report(meta, records, [{"scenario": "G01", "passed": True, "injection_findings_flagged": 2}], {"passed": 90, "failed": 0})
    for needle in ("A: telemetry only", "B: telemetry plus knowledge", "Failures with a runbook", "none (on purpose)", "drafts written by Claude Code", "G01", "90 passed", "1/1 (100%)", "Calibration"):
        assert needle in text, needle
