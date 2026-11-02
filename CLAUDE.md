# CLAUDE.md: Agentic Incident Response Platform

Repo: https://github.com/ankgupt9/agentic-incident-response (public, built to be shown on LinkedIn)
Owner: Ankur, Mac, VS Code, Docker 29.4.3, Git 2.50, system Python 3.14.5.

## Goal

Build an agent that detects integration failures in a simulated order flow, diagnoses them using logs, traces, and runbooks, and proposes a fix behind a human approval gate. It must be open source apart from the Claude API, model-agnostic through a thin gateway, and aimed at what enterprise AI job postings ask for: MCP tools, agent orchestration, evals, guardrails, agent observability.

Story for LinkedIn: I ran the off-the-shelf version (Datadog Bits AI, Copilot agent) in production. Here is what it could not do, and what I built to close the gap, with measured accuracy, cost, and latency.

## Hard rules (always follow)

1. All actions default to dry-run. No tool writes code or changes systems directly.
2. Any proposed action passes guardrails, then a human approval gate, before executing.
3. Every tool call and decision is written to an audit log.
4. The model gateway is the only path to any LLM. No direct SDK calls elsewhere.
5. Never put secrets in the repo. Keys live in a gitignored `.env`. Never print or log key values.
6. The repo is public. Before any commit, confirm `git status` does not show `.env`.
7. Keep tasks small and testable. Write tests with each component. Commit after each working milestone.
8. Explain generated code when asked. Ankur must be able to defend every piece in an interview.
9. Pin the project to Python 3.12 or 3.13 in a virtual environment. Do not use system Python 3.14.

## Architecture (7 layers, top to bottom)

1. Simulated enterprise, Docker Compose: order service, pricing service (FastAPI), RabbitMQ bus, failure injector.
2. Platform observability, OpenTelemetry: OTel Collector, Loki (logs), Tempo (traces), Prometheus (metrics), Grafana (dashboards and alerts).
3. Agent core, Python: agent loop triggered by Grafana webhook, model gateway, Claude API via the Anthropic SDK.
4. MCP tools and knowledge: query_logs, get_trace, search_runbooks, replay_message (sandbox only), draft_pull_request (draft only). Runbooks in PostgreSQL full-text search, optional pgvector later.
5. Governance: guardrails (allowlist, rate limits, dry-run default, prompt-injection filtering of log content), human approval gate (Slack bot or simple FastAPI page), audit log in Postgres, per-diagnosis cost and latency tracker.
6. Agent observability: OpenTelemetry GenAI spans for every model call and tool call, sent to Langfuse or Arize Phoenix (self-hosted, open source).
7. Evaluation harness: pytest plus custom scorer over labeled failure scenarios. Reports diagnosis accuracy, fix quality, cost, latency.

Flow: failures become telemetry, a Grafana alert wakes the agent, the agent calls Claude through the gateway and uses MCP tools to diagnose, any action goes through guardrails and approval, everything is traced and audit-logged, evals replay labeled failures.

## Stack

Python, FastAPI, RabbitMQ, OpenTelemetry Collector, Loki, Tempo, Prometheus, Grafana, PostgreSQL, MCP Python SDK, Anthropic Python SDK, Langfuse or Arize Phoenix, pytest, Docker Compose. Custom agent loop first; LangGraph port is a stretch goal. No Ollama or local models: Claude only.

## Decisions already made

- Claude API only (separate paid Anthropic Console account, key in `.env` as `ANTHROPIC_API_KEY`). Claude Code itself runs on Ankur's personal Claude subscription.
- Start development on a smaller, cheaper Claude model; run final evals on a larger one. Optionally publish a small-vs-large accuracy and cost comparison.
- Ankur writes the failure catalog (8 to 10 realistic failures with known root causes, from his Ciena integration experience), the 15 to 20 runbooks, and the core agent loop. Claude Code scaffolds everything else and reviews his loop.
- Ankur reviews every line of the action tools and guardrails himself.
- No fancy UI. Evals, governance, and tracing matter more than a dashboard.

## 4-day build plan

Day 1, platform and telemetry: scaffold FastAPI services, RabbitMQ, Docker Compose; OTel, Loki, Tempo, Prometheus, Grafana configs with one dashboard and an alert rule that fires a webhook; failure injector built from Ankur's failure catalog. Done when injected failures appear in Grafana and fire the webhook.

Day 2, agent core, gateway, read-only tools: model gateway on the Anthropic SDK; MCP server with query_logs and get_trace; Ankur writes the core loop (plan, call tool, observe, decide); strict Pydantic diagnosis output (root cause, evidence, confidence); OTel GenAI spans to Langfuse or Phoenix. Done when an alert triggers an end-to-end diagnosis replayable in the trace viewer.

Day 3, knowledge, actions, governance, cost: runbook ingestion and search_runbooks; replay_message (sandbox) and draft_pull_request (draft only, needs a GitHub token scoped to this repo, created on Day 3); approval gate, audit log, guardrails, cost and latency tracker. Done when a proposed fix waits for approval and only an approved action executes.

Day 4, evals, tuning, publish: pytest eval harness and scorer from the labeled catalog; baseline run, tune prompts, tool descriptions, retrieval, rerun, capture before-and-after; optional LangGraph port (cut this first if behind, never cut evals or the approval gate); README, one-command setup, architecture diagram, 2 to 3 minute demo; publish repo and LinkedIn post.

## Status (update this section as work progresses)

- [x] Docker, Python, Git installed and verified
- [x] GitHub repo created and cloned in VS Code
- [ ] Anthropic API key created, small credit added, monthly spend limit set, key saved in `.env` by Ankur
- [ ] `.gitignore` contains `.env` and `git status` confirms `.env` is ignored
- [ ] Claude Code installed and running in this repo
- [ ] Day 1 started

## First task for Claude Code

Read this file. Then: create `.gitignore` (include `.env`, `.venv`, `__pycache__`), create an empty `.env` with an `ANTHROPIC_API_KEY=` line for Ankur to fill in himself, set up a Python 3.12 or 3.13 virtual environment, and run `git status` to confirm `.env` is ignored. Then propose the repo folder layout and a Day 1 plan before writing any code.
