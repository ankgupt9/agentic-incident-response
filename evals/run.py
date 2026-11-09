"""Run the evals against the live stack.

    python -m evals.run --failures all --repeats 3 --arms A,B
    python -m evals.run --failures F03,F02 --repeats 1 --skip-guardrails
    python -m evals.run --resume evals/results/<run-id>
"""

import argparse
import asyncio
import json
import random
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from agent.anthropic_gateway import AnthropicGateway
from agent.config import Settings, load_dotenv
from agent.runner import make_runner
from agent.runtime import build_runtime
from agent.toolbox import McpToolbox
from agent.tracing import TracedGateway, setup_agent_telemetry
from evals.harness import EvalContext, LiveEnvironment, load_done, run_eval, run_g01
from evals.report import render_report
from evals.scenarios import INJECTION_SKU
from knowledge_store.db import KnowledgeStore
from knowledge_store.ingest import ROOT, ingest, load_change_feed
from simulator.failure_injector.change_feed import ChangeFeed, record_change_events
from simulator.failure_injector.injector import load_failure_catalog

JUDGE_MODEL = "claude-sonnet-5-5"


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def governance_suite() -> dict:
    files = ["tests/test_guardrails.py", "tests/test_governed.py", "tests/test_actions.py", "tests/test_audit_killswitch.py", "tests/test_admin_api.py", "tests/test_runner.py"]
    out = subprocess.run([sys.executable, "-m", "pytest", *files, "-q"], capture_output=True, text=True, cwd=ROOT).stdout
    passed = re.search(r"(\d+) passed", out)
    failed = re.search(r"(\d+) failed", out)
    return {"passed": int(passed.group(1)) if passed else 0, "failed": int(failed.group(1)) if failed else 0}


async def main_async(args) -> int:
    load_dotenv()
    catalog = load_failure_catalog()
    ids = list(catalog) if args.failures == "all" else args.failures.split(",")
    run_dir = args.resume or Path("evals/results") / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    meta_path = run_dir / "meta.json"
    run_id = run_dir.name
    schema = f"eval_{re.sub(r'[^a-z0-9]', '', run_id.lower())}"

    store = KnowledgeStore(schema=schema)
    ingested = await ingest(ROOT / "knowledge", run_dir / "unused.jsonl", store)
    if ingested.errors:
        print("knowledge lint failed:", [str(e) for e in ingested.errors])
        return 1
    ctx = EvalContext(settle_s=args.settle)
    rng, feed = random.Random(42), ChangeFeed(run_dir / "change_events.jsonl")

    async def knowledge_hook(failure) -> None:
        feed.path.unlink(missing_ok=True)  # a fresh feed per scenario
        record_change_events(failure.change_event, feed, rng=rng)
        await store.clear_change_events()
        await store.import_change_events(load_change_feed(feed.path))

    ctx.knowledge_hook = knowledge_hook
    telemetry = setup_agent_telemetry()
    arms, runtimes = {}, {}
    for arm in args.arms.split(","):
        settings = Settings(model=args.model, knowledge_tools=(arm == "B"), propose_actions=False, daily_budget_usd=1000.0)
        rt = await build_runtime(settings, telemetry, memory=True)
        gateway = TracedGateway(AnthropicGateway(settings), telemetry, settings.model)

        def factory(arm=arm):
            return McpToolbox(env={"KNOWLEDGE_TOOLS": "on" if arm == "B" else "off", "KNOWLEDGE_SCHEMA": schema, "TELEMETRY_FLOOR_NS": str(ctx.floor_ns)})

        arms[arm], runtimes[arm] = make_runner(rt, gateway, factory), rt
    judge_gw = AnthropicGateway(Settings(model=args.judge_model, effort="low", max_tokens=1024))

    env = LiveEnvironment()
    results_path = run_dir / "results.jsonl"
    try:
        records = await run_eval([catalog[i] for i in ids], env, arms, judge_gw, args.repeats, ctx, results_path)
        guardrail_runs = []
        if not args.skip_guardrails:
            g_settings = Settings(model=args.model, knowledge_tools=True, propose_actions=True, daily_budget_usd=1000.0)
            g_rt = await build_runtime(g_settings, telemetry, memory=True)

            def g_factory():
                return McpToolbox(env={"KNOWLEDGE_TOOLS": "on", "KNOWLEDGE_SCHEMA": schema, "TELEMETRY_FLOOR_NS": str(ctx.floor_ns)})

            runner = make_runner(g_rt, TracedGateway(AnthropicGateway(g_settings), telemetry, g_settings.model), g_factory)
            guardrail_runs = await run_g01(env, runner, g_rt.audit, g_rt.actions, INJECTION_SKU, args.guardrail_runs, ctx)
            (run_dir / "guardrails.json").write_text(json.dumps(guardrail_runs, indent=2))
    finally:
        await store.drop_schema()

    suite = governance_suite() if not args.skip_suite else None
    meta = {"run_id": run_id, "date": time.strftime("%Y-%m-%d"), "commit": git_commit(), "model": args.model, "judge_model": args.judge_model,
            "failures": len(ids), "repeats": args.repeats, "knowledge_version": ingested.version}
    meta_path.write_text(json.dumps({**meta, "suite": suite}, indent=2))
    report = render_report(meta, records, guardrail_runs, suite)
    (run_dir / "report.md").write_text(report)
    print(report)
    print(f"\nresults: {results_path}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--failures", default="all")
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--arms", default="A,B")
    p.add_argument("--model", default="claude-haiku-5-5")
    p.add_argument("--judge-model", default=JUDGE_MODEL)
    p.add_argument("--settle", type=float, default=15.0, help="seconds to wait after the alert fires")
    p.add_argument("--guardrail-runs", type=int, default=3)
    p.add_argument("--skip-guardrails", action="store_true")
    p.add_argument("--skip-suite", action="store_true")
    p.add_argument("--resume", type=Path, default=None)
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
