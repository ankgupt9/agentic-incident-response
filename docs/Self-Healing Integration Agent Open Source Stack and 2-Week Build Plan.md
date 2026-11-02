# Agentic Incident Response Platform: Claude-Powered Architecture, Prerequisites, and 4-Day Build Plan (v3)

**Goal:** Build an agent that detects integration failures in an order flow, diagnoses them using logs, traces, and runbooks, and proposes a fix behind a human approval gate. It runs on Claude through a thin model gateway, with every other component open source, and it targets what enterprise AI postings ask for: MCP tools, agent orchestration, evals, guardrails, and agent observability.

**LinkedIn pitch:** I ran the off-the-shelf version at Ciena with Datadog and Copilot. Here is what it could not do, and here is what I built to close the gap, with measured accuracy, cost, and latency.

## 1. What changed from v2

| Change | Why |
| --- | --- |
| Ollama and local models removed | You are using Claude, so no local model downloads and much lighter hardware needs. |
| Embeddings: Postgres full-text search by default | Claude has no embeddings model. Twenty runbooks do not need vectors. pgvector with a small local sentence-transformers model is the optional upgrade. |
| Model gateway kept, Claude only | Cheap to build and shows good design. Optionally compare Claude model sizes, such as Haiku versus Sonnet, for a cost and accuracy story. |
| Prerequisites checklist added | Everything you need to install or sign up for, in one place. |

## 2. Prerequisites: What You Need Access To

**Subscription versus API:** Your personal Claude subscription covers the Claude app and Claude Code, which writes the project. The agent you are building calls Claude from your own Python code, and that runs on the Anthropic API, a separate pay-as-you-go account with its own key and billing. Plan terms change, so check Anthropic's current pricing page to see whether your plan covers any of the agent's own calls. The safe default is a small API credit with a monthly spend limit.

| Item | Type | Cost | Needed for |
| --- | --- | --- | --- |
| Docker Desktop | Install | Free for personal use | Runs every service as a container |
| Python 3.11 or newer | Install | Free | Agent, services, MCP server, evals |
| Git | Install | Free | Version control |
| GitHub account | Account | Free | Repo and draft pull requests, plus a personal access token |
| Claude Code | Install, covered by your personal Claude subscription | No extra cost beyond your existing subscription | Writes the project code and scaffolding |
| Anthropic API key | Separate account in the Anthropic developer console, not part of your Claude subscription | Pay per use, roughly 10 to 30 dollars total (my estimate) | The running agent's own model calls and evals. Your subscription does not cover these. |
| Slack workspace and bot | Account | Free tier | Approval gate. Optional: use a simple FastAPI web page instead. |
| Langfuse or Arize Phoenix | Container | Free, self-hosted | Agent trace replay. Runs in Docker, no signup. |

Everything else (RabbitMQ, PostgreSQL, Loki, Tempo, Prometheus, Grafana, OpenTelemetry Collector) runs as a Docker container, so there is nothing to install or sign up for.

**Setup process, about two hours:**

1. Install Docker Desktop, Python, and Git, and confirm each runs from the terminal.
2. Create the GitHub repo and a personal access token limited to that repo.
3. In the Anthropic developer console, which is separate from your Claude subscription, create an API key, add a small credit balance, set a low monthly spend limit, and store the key in a local .env file that is gitignored.
4. Install Claude Code and open it in the repo.
5. Write CLAUDE.md with the architecture and rules, then let Claude Code generate the Docker Compose file for the rest.
6. Optional: create the Slack app and bot token.

**Hardware:** Without a local model, a laptop with 8 GB of RAM can run the lightweight services, but the full observability stack is happier with 16 GB. If you have less, drop Tempo.

## 3. Architecture

The architecture has nine layers: the simulated integration layer, platform observability, agent core, the Claude model provider, the knowledge layer, the MCP tool layer, governance and safety, agent observability, and the evaluation harness. The drawing below shows them top to bottom, and the paragraph after it describes the same flow in words.

&#91;embedded content: architecture · 7 layers, top to bottom\]

Read top to bottom: failures in the simulated services become telemetry, a Grafana alert wakes the agent, the agent uses MCP tools through the Claude gateway, and governance sits between any proposed action and its execution. Agent observability and evals watch the whole run.

**Flow in one paragraph:** Services emit logs, traces, and metrics through OpenTelemetry into Loki, Tempo, and Prometheus. A Grafana alert fires a webhook that wakes the agent. The orchestrator calls Claude through the gateway, then uses MCP tools to query logs, fetch traces, and search runbooks, and forms a diagnosis. Any proposed action passes guardrails and a human approval gate before a tool replays a message in a sandbox or opens a draft pull request. Every reasoning step becomes an OpenTelemetry span, every action is audit-logged, and cost and latency are recorded per diagnosis. The eval harness replays labeled failures and scores accuracy, cost, and latency.

## 4. Tech Stack

| Layer | Tool | Notes |
| --- | --- | --- |
| LLM | Claude API via the Anthropic Python SDK | Native tool calling. Start on a smaller, cheaper model while iterating. |
| Model gateway | Thin Python interface | Swap model sizes by config |
| Orchestration | Custom Python loop, then LangGraph | Build your own first, then port |
| Tool protocol | MCP Python SDK | Cross-vendor standard |
| Simulated services | FastAPI | Easy failure injection |
| Message bus | RabbitMQ | Mimics enterprise integration queues |
| Telemetry pipeline | OpenTelemetry Collector | Vendor-neutral standard |
| Logs, traces, metrics | Loki, Tempo, Prometheus | Free replacements for Datadog |
| Dashboards and alerts | Grafana | Alert webhook triggers the agent |
| Agent tracing | OpenTelemetry GenAI spans with Langfuse or Arize Phoenix | Open source, self-hosted trace replay |
| Knowledge base | PostgreSQL full-text search, optional pgvector | Also holds the audit log |
| Approval UI | Slack bot or simple FastAPI page | Human in the loop |
| Code actions | GitHub API | Draft pull requests only |
| Evals | pytest with a custom scorer | Accuracy, cost, latency |
| Packaging | Docker Compose | One command to run everything |

## 5. Four-Day Build Plan with Claude Code

Claude Code scaffolds boilerplate, configs, tools, and tests. You own the design, the failure catalog, the runbooks, the agent loop, and the review of every action tool and guardrail.

**Before Day 1 (about two hours):** Complete the setup process in section 2. In CLAUDE.md, set these rules: all actions default to dry-run, no direct code writes, every tool call is logged, the model gateway is the only path to any LLM, and no secrets in the repo.

### Day 1: Platform and telemetry

- Claude Code scaffolds the three FastAPI services, RabbitMQ, and Docker Compose.
- It generates the OpenTelemetry Collector, Loki, Tempo, Prometheus, and Grafana configs, plus one dashboard and an alert rule with a webhook.
- **You:** Write the failure catalog from your Ciena experience, 8 to 10 failure types with known root causes. Claude Code builds the injector from it.
- **Done when:** Injected failures appear in Grafana and fire the webhook.

### Day 2: Agent core, model gateway, and read-only tools

- Claude Code builds the model gateway on the Anthropic SDK and the MCP server with query\_logs and get\_trace.
- **You:** Write the core agent loop yourself (plan, call tool, observe, decide). Claude Code reviews it. This is the piece you will be asked about in interviews.
- Add strict Pydantic output for diagnosis: root cause, evidence, confidence.
- Add OpenTelemetry GenAI spans around every model call and tool call, and send them to Langfuse or Phoenix.
- **Done when:** An alert triggers an end-to-end diagnosis, and you can replay every step in the trace viewer.

### Day 3: Knowledge, actions, governance, and cost tracking

- **You:** Write 15 to 20 runbooks and incident notes in your own words.
- Claude Code builds runbook ingestion and search\_runbooks (full-text search), replay\_message (sandbox only), and draft\_pull\_request (draft only).
- Claude Code builds the approval gate, audit log, guardrails (allowlist, rate limits, dry-run default, prompt-injection filtering of log content), and the per-diagnosis cost and latency tracker.
- **Review closely:** Read every line of the action tools and guardrails yourself. This is your safety story.
- **Done when:** A proposed fix waits for approval, and only an approved action executes.

### Day 4: Evals, tuning, LangGraph port, and publish

- Claude Code builds the pytest eval harness and scorer from your labeled catalog, reporting accuracy, cost, and latency.
- Run a baseline, tune prompts, tool descriptions, and retrieval, then rerun and capture before-and-after scores. Optionally compare a smaller Claude model against a larger one on accuracy versus cost.
- Port the orchestrator to LangGraph if time allows. If not, put it first on the stretch list rather than cutting evals.
- Polish the README, setup script, and diagram. Record a 2 to 3 minute demo. Publish the repo and the LinkedIn post.

### Working tips for Claude Code

- Keep tasks small and testable, and ask for tests with each component.
- Use plan mode before each day, and commit after each working milestone.
- Ask it to explain any generated code you cannot explain yourself. If you cannot defend it in an interview, rewrite or simplify it.
- Do not skip the eval harness to save time. It is what makes the project stand out.

## 6. LinkedIn Post Angle

- **Hook:** Everyone is configuring AI agents. Few are building the layer underneath.
- **Story:** What you ran at Ciena with Datadog and Copilot, where it hit limits, and what you built.
- **Proof:** Architecture diagram, demo video, and measured diagnosis accuracy, cost, and latency.
- **Takeaway:** Enterprise AI value comes from governance, evals, observability, and integration, not just prompts.

## 7. Risks and Scope Control

- **API cost:** Set a spend limit in the Anthropic console, iterate on a smaller model, and cache or cap eval runs.
- **Scope creep:** No fancy UI. Evals, governance, and tracing matter more than a dashboard.
- **Market check limits:** The architecture was checked against industry guides and posting summaries, not a full audit of individual job descriptions. Treat the fit as strong but directional.
- **Four days is aggressive.** Day 2 and Day 3 are the heaviest. If you slip, cut the LangGraph port, not the evals or the approval gate.

## 8. Stretch Goals

- LangGraph port, if not done on Day 4.
- A second agent that reviews the first agent's proposed fix.
- A MuleSoft Anypoint-style flow definition so the story ties back to your real background.
