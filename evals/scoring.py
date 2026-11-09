"""Scoring: pure functions over run records. No network, so they are unit-tested."""

import statistics
from collections import defaultdict

from agent.schemas import Diagnosis, ungrounded_evidence

ROOT_SCORE = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}
FIX_SCORE = {"good": 1.0, "partial": 0.5, "poor": 0.0}
BINS = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01)]


def grounding(diagnosis: Diagnosis, observed: list[str]) -> float:
    """Share of evidence quotes that really appear in tool results the model saw."""
    if not diagnosis.evidence:
        return 0.0
    return 1 - len(ungrounded_evidence(diagnosis, observed)) / len(diagnosis.evidence)


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def calibration(records: list[dict]) -> dict:
    """Does stated confidence match how often the diagnosis is right? Strict: only 'correct' counts."""
    pairs = [(r["confidence"], 1.0 if r["root_cause"] == "correct" else 0.0) for r in records if r.get("confidence") is not None]
    if not pairs:
        return {"n": 0, "brier": None, "ece": None, "bins": []}
    bins, ece = [], 0.0
    for lo, hi in BINS:
        members = [(c, y) for c, y in pairs if lo <= c < hi]
        if not members:
            continue
        mean_conf, acc = statistics.fmean(c for c, _ in members), statistics.fmean(y for _, y in members)
        bins.append({"range": f"{lo:.1f}-{min(hi, 1.0):.1f}", "n": len(members), "mean_confidence": mean_conf, "accuracy": acc})
        ece += len(members) / len(pairs) * abs(mean_conf - acc)
    return {"n": len(pairs), "brier": statistics.fmean((c - y) ** 2 for c, y in pairs), "ece": ece, "bins": bins}


def summarize(records: list[dict]) -> dict:
    """Aggregate one arm's records."""
    n = len(records)
    if n == 0:
        return {"n": 0}
    done = [r for r in records if r["completed"]]
    return {
        "n": n,
        "completed": len(done) / n,
        "accuracy_strict": sum(r["root_cause"] == "correct" for r in records) / n,
        "accuracy_lenient": statistics.fmean(ROOT_SCORE[r["root_cause"]] for r in records),
        "fix_quality": statistics.fmean(FIX_SCORE[r["fix"]] for r in records),
        "mean_confidence": statistics.fmean(r["confidence"] for r in done) if done else None,
        "grounding": statistics.fmean(r["grounding"] for r in done) if done else None,
        "service_match": statistics.fmean(1.0 if r["service_match"] else 0.0 for r in done) if done else None,
        "cost_mean_usd": statistics.fmean(r["cost_usd"] for r in records),
        "cost_total_usd": sum(r["cost_usd"] for r in records),
        "latency_p50_s": percentile([r["latency_s"] for r in records], 0.5),
        "latency_p95_s": percentile([r["latency_s"] for r in records], 0.95),
        "steps_mean": statistics.fmean(r["steps"] for r in records),
        "tool_calls_mean": statistics.fmean(r["tool_calls"] for r in records),
        "calibration": calibration(records),
    }


def by_key(records: list[dict], key) -> dict:
    groups: dict = defaultdict(list)
    for r in records:
        groups[key(r)].append(r)
    return dict(groups)


def citation_stats(records: list[dict]) -> dict:
    """For arms that have knowledge tools: how often did a diagnosis cite the entry a person would expect?"""
    expected = [r for r in records if r.get("expected_runbook")]
    cited_any = [r for r in records if r.get("knowledge_refs")]
    return {
        "cited_anything": len(cited_any) / len(records) if records else 0.0,
        "cited_expected_runbook": (sum(bool(r["cited_expected"]) for r in expected) / len(expected)) if expected else None,
        "n_with_expected": len(expected),
    }
