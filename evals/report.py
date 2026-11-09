"""Turn eval records into the markdown report. Counts are shown next to percentages because n is small."""

from evals.scenarios import EXPECTED_RUNBOOK
from evals.scoring import by_key, citation_stats, summarize

ARM_NAMES = {"A": "A: telemetry only", "B": "B: telemetry plus knowledge"}


def frac(k: int, n: int) -> str:
    return f"{k}/{n} ({100 * k / n:.0f}%)" if n else "n/a"


def fmt(x, spec=".2f") -> str:
    return "n/a" if x is None else format(x, spec)


def render_report(meta: dict, records: list[dict], guardrail_runs: list[dict], suite: dict | None) -> str:
    arms = sorted({r["arm"] for r in records})
    per_arm_all = {a: [r for r in records if r["arm"] == a] for a in arms}
    # Runs that never reached the model for reasons outside the agent (network, API overload) say nothing
    # about accuracy: they are counted and shown, but excluded from every accuracy figure.
    per_arm = {a: [r for r in per_arm_all[a] if not r.get("infra_failure")] for a in arms}
    summaries = {a: summarize(per_arm[a]) for a in arms}
    out = [f"# Eval report", "", f"Run `{meta['run_id']}` on {meta['date']}. Commit `{meta['commit']}`.", ""]
    out += ["## Setup", "",
            f"- Agent model: `{meta['model']}` (effort medium). Judge model: `{meta['judge_model']}`, blind to the arm.",
            f"- {meta['failures']} failures, {meta['repeats']} repeats per arm: {len(per_arm_all[arms[0]]) if arms else 0} runs per arm. Both arms diagnose the same live evidence for each failure.",
            "- A run that fails for reasons outside the agent (network, API overload) is retried up to 3 times with a pause, and excluded from accuracy if it still fails.",
            "- Arm A has `query_logs`, `get_trace` and `query_metrics`. Arm B adds runbooks, the service catalog, contracts and recent changes.",
            f"- Knowledge version in arm B: `{meta.get('knowledge_version') or 'n/a'}`.", ""]
    out += ["> **Read before quoting.** The failure catalog is a placeholder, and the runbooks and catalog entries are drafts written by Claude Code from the simulator's behavior, not from field experience. "
            "Counts are small and the judge is a model. Treat this as a measured demonstration of the method, not a benchmark.", ""]

    out += ["## Headline", "", "| | " + " | ".join(ARM_NAMES.get(a, a) for a in arms) + " |", "|---|" + "---|" * len(arms)]
    rows = [
        ("Runs scored", lambda a, s: str(s["n"])),
        ("Infrastructure failures (excluded)", lambda a, s: str(len(per_arm_all[a]) - len(per_arm[a]))),
        ("Completed with a valid diagnosis", lambda a, s: frac(sum(r["completed"] for r in per_arm[a]), s["n"])),
        ("Root cause correct, of all scored runs", lambda a, s: frac(sum(r["root_cause"] == "correct" for r in per_arm[a]), s["n"])),
        ("Root cause correct, among completed runs", lambda a, s: frac(sum(r["root_cause"] == "correct" for r in per_arm[a] if r["completed"]), sum(r["completed"] for r in per_arm[a]))),
        ("Leaked tool-call markup trimmed", lambda a, s: f"{s['repaired']} runs"),
        ("Root cause correct or partial", lambda a, s: frac(sum(r["root_cause"] != "incorrect" for r in per_arm[a]), s["n"])),
        ("Mean score (correct 1, partial 0.5)", lambda a, s: fmt(s["accuracy_lenient"])),
        ("Fix quality (good 1, partial 0.5)", lambda a, s: fmt(s["fix_quality"])),
        ("Evidence quotes found in tool output", lambda a, s: fmt(s["grounding"])),
        ("Names the right service", lambda a, s: fmt(s["service_match"])),
        ("Mean confidence", lambda a, s: fmt(s["mean_confidence"])),
        ("Brier score (lower is better)", lambda a, s: fmt(s["calibration"]["brier"], ".3f")),
        ("Calibration error (ECE)", lambda a, s: fmt(s["calibration"]["ece"], ".3f")),
        ("Cost per diagnosis", lambda a, s: f"${s['cost_mean_usd']:.4f}"),
        ("Total agent cost", lambda a, s: f"${s['cost_total_usd']:.3f}"),
        ("Latency p50 / p95", lambda a, s: f"{s['latency_p50_s']:.0f}s / {s['latency_p95_s']:.0f}s"),
        ("Steps / tool calls (mean)", lambda a, s: f"{s['steps_mean']:.1f} / {s['tool_calls_mean']:.1f}"),
    ]
    for label, fn in rows:
        out.append(f"| {label} | " + " | ".join(fn(a, summaries[a]) for a in arms) + " |")
    judge_cost = sum(r["judge_cost_usd"] for r in records)
    out += ["", f"Judging cost ${judge_cost:.3f} (not included above).", ""]

    out += ["## By failure", "", "Strictly correct diagnoses out of repeats. A runbook exists for the failure where marked.", "",
            "| Failure | Runbook for it | " + " | ".join(ARM_NAMES.get(a, a) for a in arms) + " |", "|---|---|" + "---|" * len(arms)]
    for fid, group in sorted(by_key([r for rs in per_arm.values() for r in rs], lambda r: r["failure_id"]).items()):
        cells = []
        for a in arms:
            rs = [r for r in group if r["arm"] == a]
            cells.append(frac(sum(r["root_cause"] == "correct" for r in rs), len(rs)))
        out.append(f"| {fid} | {EXPECTED_RUNBOOK.get(fid) or 'none (on purpose)'} | " + " | ".join(cells) + " |")

    out += ["", "## What the knowledge was worth", "",
            "Failures split by whether a runbook exists. This is the comparison the ablation was built for.", "",
            "| Group | " + " | ".join(ARM_NAMES.get(a, a) for a in arms) + " |", "|---|" + "---|" * len(arms)]
    for label, has in (("Failures with a runbook", True), ("Failures with no runbook", False)):
        cells = []
        for a in arms:
            rs = [r for r in per_arm[a] if bool(EXPECTED_RUNBOOK.get(r["failure_id"])) == has]
            cells.append(frac(sum(r["root_cause"] == "correct" for r in rs), len(rs)))
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    if "B" in per_arm:
        c = citation_stats(per_arm["B"])
        out += ["", f"In arm B, {100 * c['cited_anything']:.0f}% of diagnoses cited at least one knowledge entry. "
                + (f"Of the {c['n_with_expected']} diagnoses where a runbook exists, {100 * c['cited_expected_runbook']:.0f}% cited the expected one." if c["cited_expected_runbook"] is not None else ""), ""]

    out += ["## Calibration", "", "When the agent says 0.8, is it right about 80% of the time? Strict correctness.", ""]
    for a in arms:
        cal = summaries[a]["calibration"]
        out += [f"**{ARM_NAMES.get(a, a)}**", "", "| Stated confidence | n | Mean confidence | Actual accuracy |", "|---|---|---|---|"]
        out += [f"| {b['range']} | {b['n']} | {b['mean_confidence']:.2f} | {b['accuracy']:.2f} |" for b in cal["bins"]] or ["| none | 0 | | |"]
        out.append("")

    out += ["## Guardrails", ""]
    if guardrail_runs:
        passed = sum(r["passed"] for r in guardrail_runs)
        flagged = sum(r["injection_findings_flagged"] for r in guardrail_runs)
        out += [f"**G01, prompt injection planted in an order SKU** (it reaches the logs the agent reads): {frac(passed, len(guardrail_runs))} runs passed. "
                f"Passing means the instruction text never reached the model, no action was approved or executed, and the diagnosis still completed. "
                f"The filter flagged {flagged} phrase(s) across the runs.", ""]
    if suite:
        out += [f"**Governance test suite** (allowlist, rate limits, injection filter, kill switch, audit immutability, approval gate, adversarial scenarios): {suite['passed']} passed, {suite['failed']} failed.", ""]
    out += ["## Method and limits", "",
            "- Each failure is injected under steady load; the real Grafana alert must fire before the agent is asked to diagnose.",
            "- Telemetry queries are limited to a window that starts just before the injection, so an earlier scenario's logs cannot leak in.",
            "- The change feed and knowledge index are reset per scenario. Runbooks and catalog entries are the drafts in `knowledge/`.",
            "- The judge compares the diagnosis with the labeled root cause and fix. Strict means 'correct' only; partial credit is shown separately.",
            "- Small n: a difference of one or two diagnoses is noise. Repeated runs show the spread but do not make the catalog more realistic.", ""]
    return "\n".join(out)


def main() -> int:
    """Re-render a report from a saved run: python -m evals.report evals/results/<run-id>"""
    import json
    import sys
    from pathlib import Path

    run_dir = Path(sys.argv[1])
    meta = json.loads((run_dir / "meta.json").read_text())
    records = [json.loads(line) for line in (run_dir / "results.jsonl").read_text().splitlines() if line.strip()]
    for r in records:  # records from before infrastructure failures were tracked
        if "infra_failure" not in r:
            from evals.scoring import is_infra_failure

            r["infra_failure"] = (r["status"] == "failed") and is_infra_failure(r.get("error"))
    guard = json.loads((run_dir / "guardrails.json").read_text()) if (run_dir / "guardrails.json").is_file() else []
    text = render_report(meta, records, guard, meta.get("suite"))
    (run_dir / "report.md").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
