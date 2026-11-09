# Solution Design: Agentic Incident Response Platform

An AI agent that investigates integration failures, and a platform that governs and monitors that agent.

| | |
|---|---|
| Status | Built end to end: simulated enterprise, observability, agent, knowledge layer, governance (audit log, guardrails, approval gate, kill switch) and the eval harness. Content written by Ankur (real failure catalog, runbooks, catalog entries) is still to come. Results are in [eval-report.md](eval-report.md). |
| Audience | Part A: business and product readers. Part B: engineers and interviewers. |
| Repo | <https://github.com/ankgupta9/agentic-incident-response> |

**How to read this document.** Part A explains the problem, the people, the process and the controls with no code. Part B explains how it is built. Part C covers decisions, status, operations and what comes next. In every diagram, solid boxes are built and dashed boxes are planned.

---

# Part A: Business view

## A1. The problem

In an enterprise integration landscape, orders flow through several systems connected by APIs and message queues. When something breaks, the symptom shows up far from the cause: an order is rejected in one system because a price was cached wrongly in another, or a message field was renamed upstream.

Today an on-call engineer has to:

1. Get paged with a symptom ("order error rate is high").
2. Open dashboards, search logs, follow a request across services, and read runbooks.
3. Form a theory, often after asking colleagues who know that system.
4. Propose a fix, get it approved, apply it and confirm recovery.

Steps 2 and 3 are slow, repetitive and dependent on tribal knowledge. An AI agent can take over the data gathering of step 2 directly. Step 3 depends on knowledge that lives in people's heads (how the systems are wired, what changed recently, what has gone wrong before), so the agent is only as good as the knowledge source it is given. Making that knowledge explicit, searchable and governed is the central design challenge of this project.

Off-the-shelf AI assistants in observability and developer tools can summarize an alert. This project targets three gaps that matter in an enterprise setting:

- **Control over what the AI looks at**, across your own logs, traces and runbooks.
- **Measured results**: accuracy, cost and speed scored on your failures, not on a vendor's demo.
- **Visibility and audit**: being able to see, constrain and review everything the AI did.

## A2. What this solution does

1. **Detects** a failure in a simulated order flow through ordinary monitoring alerts.
2. **Investigates** automatically: the agent reads logs, follows traces and checks metrics. With the knowledge tools it also consults runbooks, the service catalog and recent changes, which stand in for the colleague an engineer would ask.
3. **Reports** a structured diagnosis: root cause, evidence quoted from the data, confidence from 0 to 100%, and a recommended fix.
4. **Proposes** a fix as text. It can also propose a draft pull request or a sandbox replay of one message, but nothing runs unless guardrails pass and a person approves.
5. **Governs and monitors itself.** Every model call, tool call, cost and decision is traced and measured on a dashboard, and every action is audit-logged.
6. **Proves itself** through evaluations that replay known failures and score accuracy, cost, latency and how well its confidence matches reality.

The second half of that list is the point of the project: it is as much about governing AI as about using it.

## A3. People and responsibilities

| Role | What they do | Where they appear |
|---|---|---|
| On-call engineer | Receives the diagnosis, reviews evidence, decides whether to act | Reads the diagnosis; approves or rejects proposed actions |
| Approver | Authorizes any action that changes something | Human approval gate |
| Platform owner | Runs the stack, owns alert rules and runbooks | Grafana, runbook library |
| AI governance owner | Sets what the agent may do, reviews its behavior and cost | Guardrails, AI Health dashboard, audit log, evals |
| Developer (Ankur) | Writes the failure catalog, runbooks and catalog entries; reviews the core loop and every action tool and guardrail | Repository |

## A4. Business process flow

### Before: manual incident handling

```mermaid
flowchart LR
    A["Failure occurs"] --> B["Alert pages on-call"]
    B --> C["Engineer opens dashboards"]
    C --> D["Searches logs, follows trace"]
    D --> E["Reads runbooks,<br/>asks colleagues"]
    E --> F["Forms a theory"]
    F --> G["Proposes and applies fix"]
    G --> H["Confirms recovery"]
```

The time spent between "alert" and "theory" is the cost this project targets. The agent's time to diagnose is measured in the eval report; the human baseline is not measured here.

### After: agent-assisted incident handling

Lanes show who does what. The agent investigates without waiting for a person. Nothing that changes a system happens without a human.

```mermaid
flowchart TB
    subgraph PLAT["Order platform"]
        P1["Failure occurs in<br/>order flow"]
    end

    subgraph MON["Monitoring"]
        M1["Telemetry collected:<br/>logs, traces, metrics"]
        M2["Alert rule fires"]
    end

    subgraph AGT["AI agent"]
        A1["Receives alert"]
        A2["Investigates with<br/>read-only tools"]
        A3["Enough evidence?"]
        A4["Submits structured<br/>diagnosis"]
        A5["Proposes fix"]
    end

    subgraph GOV["Governance controls"]
        G1["Guardrails check<br/>(allowlist, rate limit,<br/>dry-run default)"]
        G2["Audit log<br/>records everything"]
        G3["Cost, latency and<br/>tool errors measured"]
    end

    subgraph PPL["People"]
        H1["On-call engineer<br/>reviews diagnosis"]
        H2["Approver decides"]
        H3["Fix executed in<br/>sandbox or as draft PR"]
        H4["Outcome feeds runbooks<br/>and evaluations"]:::planned
    end

    P1 --> M1 --> M2 --> A1 --> A2 --> A3
    A3 -- "no, look further" --> A2
    A3 -- "yes, or step limit" --> A4
    A4 --> H1
    H1 --> A5
    A5 --> G1
    G1 -- "pass" --> H2
    G1 -- "block" --> G2
    H2 -- "approve" --> H3
    H2 -- "reject" --> G2
    H3 --> G2
    H3 --> H4
    A2 -.-> G3
    A4 -.-> G3

    classDef planned stroke-dasharray: 5 5,fill:#f6f6f6,color:#555;
```

### Process rules

- The agent can only **look** today. All its tools are read-only.
- When it can act, the default is **dry-run**. Real changes need guardrails and explicit approval.
- A low-confidence diagnosis is a valid outcome. The agent is told to say so rather than guess.
- If the agent hits its step limit it must still answer, and the result is flagged as forced.
- A kill switch can stop the agent at any time.

## A5. Risks and controls

| Risk | Control | Evidence it works | Status |
|---|---|---|---|
| The agent changes something it should not | Read-only tools; actions default to dry-run; guardrail allowlist | Tests that nothing executes without an approval record | Built and tested |
| Log text tries to hijack the agent (prompt injection) | Logs labeled as untrusted data; system prompt forbids following them; filter hook in place | Adversarial test: a log line saying "ignore your instructions" | Built and tested; live prompt-injection scenario in the evals |
| The agent invents evidence | Evidence must be quoted from tool results; a check flags quotes never shown to the model | Ungrounded-evidence check, scored in evals | Check built; loop use is Ankur's call |
| Runaway spend | Step cap; per-call cost tracking; cost-per-diagnosis panel; alert on spikes | AI Health dashboard | Built, with alert rules on the agent |
| Overconfidence | Confidence is a required field; evals check calibration | Calibration report | Eval report |
| Silent model change | Model version pinned in config, never "latest"; changes audit-logged | Config and audit log | Built (model pinned and audited at startup) |
| No accountability | Audit log of every tool call, decision and approval | Postgres audit table | Built (append-only) |
| Sensitive data in traces | Content capture can be switched off; text truncated | Setting `AGENT_TRACE_CONTENT` | Built |
| Agent is down or misbehaving | Kill switch; failed diagnoses are recorded, not hidden | Kill-switch test | Built, fails closed |
| Stale or wrong knowledge (runbooks, catalog) leads to a confident wrong answer | Each knowledge entry has an owner and a last-reviewed date; diagnoses must cite the entry they used; eval ablation shows the knowledge's effect | Citations and ablation in evals | Built; ablation in the eval report |
| Simulator leaks the answer (fault toggles are logged) | Remove those log lines before the baseline; emit neutral change events | Eval baseline reviewed for leakage | Fixed; change feed and leak tests in place |
| Repeated alerts cause repeated spend | Same alert is not re-diagnosed within 10 minutes; at most 2 diagnoses run at once | Service tests | Built |

## A6. How success is measured

All measured by the evaluation harness over the labeled failures. Targets are set after the baseline run, not before.

| Measure | Question it answers |
|---|---|
| Diagnosis accuracy | How often is the root cause right? |
| Evidence grounding | Do quotes really come from the data? |
| Confidence calibration | When it says 90%, is it right about 90% of the time? |
| Cost per diagnosis | What does one investigation cost? |
| Latency | How long from alert to diagnosis? |
| Guardrail pass rate | Do attacks and unsafe requests get blocked? |
| Small vs large model | What does a bigger model buy, at what cost? |

## A7. Glossary

| Term | Meaning |
|---|---|
| Alert | An automatic notification that a monitored value crossed a threshold |
| Logs, traces, metrics | The three kinds of monitoring data: event text, the path one request took, and numeric time series |
| Trace | The recorded journey of one request across services |
| Root cause | The underlying reason for a failure, as opposed to its visible symptom |
| LLM | Large language model; here, Claude |
| Agent | Software that lets an LLM decide which tools to use, step by step, toward a goal |
| MCP | Model Context Protocol, a standard way to expose tools to an AI |
| Guardrail | A rule enforced in code that limits what the agent may do |
| Dry-run | A mode that shows what would happen without doing it |
| Eval | A repeatable test that scores the agent against known answers |
| Prompt injection | Text in the data that tries to give the AI new instructions |

---

# Part B: Technical view

## B1. Capability map

```mermaid
flowchart TB
    subgraph SIM["Simulate"]
        S1["Order and pricing services"]
        S2["Message queue"]
        S3["Failure injector"]
    end
    subgraph OBS["Observe the platform"]
        O1["Logs, traces, metrics"]
        O2["Dashboards"]
        O3["Alert rules and webhook"]
    end
    subgraph AGENT["Investigate"]
        G1["Alert trigger"]
        G2["Core loop"]
        G3["Model gateway"]
        G4["Read-only MCP tools"]
        G6["Knowledge tools: runbooks,<br/>catalog, recent changes"]
        G5["Structured diagnosis"]
    end
    subgraph GOVERN["Govern"]
        V1["Guardrails"]
        V2["Approval gate"]
        V3["Audit log"]
        V4["Kill switch, pinned model"]
    end
    subgraph WATCH["Observe the agent"]
        W1["Trace per model and tool call"]
        W2["AI Health dashboard"]
    end
    subgraph PROVE["Prove"]
        E1["Eval harness and scorer"]
        E2["Calibration and guardrail tests"]
    end

    SIM --> OBS --> AGENT
    AGENT --> WATCH
    AGENT -.-> GOVERN
    PROVE -.-> SIM
    PROVE -.-> AGENT

    classDef planned stroke-dasharray: 5 5,fill:#f6f6f6,color:#555;
```

## B2. Technical architecture

Seven layers. Layer 1 is the thing being monitored. Layers 3 to 7 are the agent and everything that governs, watches and tests it.

```mermaid
flowchart TB
    INJ["Failure injector<br/>(failures/catalog.yaml)"]

    subgraph L1["Layer 1: Simulated enterprise (Docker Compose)"]
        direction LR
        ORD["order-service"] -- "quote" --> PRC["pricing-service"]
        ORD -- "publish" --> MQ[("RabbitMQ")]
        MQ -- "consume" --> PRC
    end

    subgraph L2["Layer 2: Platform observability (OpenTelemetry)"]
        direction LR
        COL["OTel Collector"] --> STORES[("Loki - Tempo - Prometheus")] --> GRAF["Grafana<br/>dashboards + alerts"]
    end

    subgraph L3["Layer 3: Agent core (Python)"]
        direction LR
        SVC["Alert trigger"] --> LOOP["Core loop"] --> GW["Model gateway"]
    end

    CLAUDE{{"Claude API"}}

    subgraph L4["Layer 4: MCP tools and knowledge"]
        direction LR
        MCP["MCP server: query_logs,<br/>get_trace, query_metrics"]
        KN["Knowledge tools: runbooks,<br/>service catalog, recent changes"]
        ACT["Action tools: replay_message,<br/>draft_pull_request"]
    end

    subgraph L5["Layer 5: Governance"]
        direction LR
        GR["Guardrails"] --> AP["Human approval"] --> AUD[("Audit log")]
    end

    subgraph L6["Layer 6: Agent observability"]
        direction LR
        PHX["Phoenix traces"]
        AIH["AI Health dashboard"]
    end

    EVAL["Layer 7: Eval harness<br/>accuracy, cost, latency,<br/>confidence calibration"]

    INJ -. "inject faults" .-> L1
    L1 -- "logs, traces, metrics" --> L2
    GRAF -- "alert webhook" --> SVC
    GW -- "messages + tools" --> CLAUDE
    LOOP -- "tool calls" --> MCP
    MCP -- "queries" --> STORES
    LOOP -. "every action" .-> GR
    LOOP -. "knowledge lookups" .-> KN
    AP -. "approved only" .-> ACT
    LOOP -- "spans + metrics" --> L6
    EVAL -. "replays labeled failures,<br/>scores the loop" .-> INJ

    classDef planned stroke-dasharray: 5 5,fill:#f6f6f6,color:#555;
```

### Deployment view

Everything runs in Docker Compose, including the agent. The MCP server runs as a subprocess inside the agent container. For development you can run the agent on the host instead (`uvicorn agent.trigger:app --port 8004`) and point the Grafana receiver at `host.docker.internal:8004`.

```mermaid
flowchart LR
    CLAUDE{{"Claude API<br/>(internet)"}}
    PERSON["On-call engineer<br/>(browser)"]

    subgraph DC["Docker Compose network"]
        direction TB
        subgraph AGC["agent-trigger container :8004"]
            AG["Alert trigger<br/>+ governed runner"]
            MCPP["MCP server<br/>(stdio subprocess)"]
        end
        SVCS["order-service :8001<br/>pricing-service :8002<br/>alert-recorder :8003"]
        MQ["RabbitMQ :5672 / :15672"]
        PG[("Postgres :5432<br/>knowledge, audit log,<br/>approvals, kill switch")]
        COL["OTel Collector<br/>:4317 / :4318"]
        BE["Loki :3100<br/>Tempo :3200<br/>Prometheus :9090"]
        GRAF["Grafana :3000"]
        PHX["Phoenix :6006"]
    end

    AG -- "HTTPS" --> CLAUDE
    AG -- "stdio" --> MCPP
    MCPP -- "HTTP queries" --> BE
    MCPP -- "SQL" --> PG
    AG -- "audit, approvals" --> PG
    AG -- "OTLP spans + metrics" --> COL
    AG -- "OTLP spans" --> PHX
    GRAF -- "webhook" --> AG
    PERSON -- "approve / reject,<br/>kill switch (admin token)" --> AG
```

## B3. Runtime flow: failure to diagnosis

```mermaid
sequenceDiagram
    autonumber
    participant I as Failure injector
    participant S as order / pricing services
    participant O as Collector + Loki/Tempo/Prometheus
    participant G as Grafana
    participant A as Alert trigger
    participant L as Core loop
    participant M as Claude (via gateway)
    participant T as MCP tools
    participant P as Phoenix + AI Health

    I->>S: enable fault (e.g. schema drift)
    S->>O: logs, traces, metrics (OTLP)
    O->>G: error ratio crosses threshold
    G->>A: webhook (alert firing)
    A->>L: run_diagnosis(alert)
    loop until submit_diagnosis, step cap, or failure
        L->>M: history + tool list
        M-->>L: request tool call(s)
        L->>T: execute (query_logs / get_trace / query_metrics)
        T->>O: LogQL / trace lookup / PromQL
        O-->>T: results
        T-->>L: compact, truncated text
        L-->>P: span per model call and tool call
    end
    M-->>L: submit_diagnosis (validated by Pydantic)
    L-->>A: DiagnosisResult (root cause, evidence, confidence, cost)
    L-->>P: diagnosis span + metrics
```

## B4. The simulated enterprise (Layer 1)

An order flow with deliberate weak points, so the agent has realistic failures to diagnose.

- **order-service** accepts `POST /orders`, asks pricing-service for a quote (5 second timeout), then publishes the order to a durable RabbitMQ queue. Failures here return HTTP 502 and are counted as `orders_submitted_total{status="error"}`.
- **pricing-service** serves quotes over HTTP and consumes the queue. For each order it checks the quoted price against its catalog. Mismatches and malformed messages are rejected without requeue and counted as `orders_priced_total{status="error"}`.
- **RabbitMQ** carries the order messages.
- **Fault injection.** Each service exposes `/admin/faults`, mounted only when `ENABLE_FAULT_INJECTION=true`. The injector CLI toggles faults from a catalog.

### Failure catalog (placeholder, to be replaced by real failures)

| ID | Failure | Fires alert | Root-cause category |
|---|---|---|---|
| F01 | Pricing service outage | OrderSubmitErrorRateHigh | dependency_outage |
| F02 | Pricing service slow | OrderSubmitErrorRateHigh | dependency_latency |
| F03 | Stale price quotes | PricingConsumerErrorRateHigh | data_inconsistency |
| F04 | SKU missing from catalog | OrderSubmitErrorRateHigh | missing_reference_data |
| F05 | Message schema drift | PricingConsumerErrorRateHigh | schema_mismatch |
| F06 | Malformed message payload | PricingConsumerErrorRateHigh | malformed_payload |
| F07 | Broker connection lost on publish | OrderSubmitErrorRateHigh | messaging_failure |
| F08 | Stalled consumer, queue backlog | OrderQueueBacklog | consumer_stall |

These are stand-ins. The real catalog, drawn from enterprise integration experience, replaces them, and eval credibility depends on how realistic it is. All eight were verified end to end: each fires its expected alert and then resolves.

## B5. Platform observability (Layer 2)

| Component | Role | How data arrives |
|---|---|---|
| OTel Collector | Single entry point for telemetry; routes by type | Services push OTLP over HTTP |
| Loki | Log storage and LogQL queries | Collector pushes |
| Tempo | Trace storage | Collector pushes |
| Prometheus | Metrics storage and PromQL | Pulls from the collector's exporter and from RabbitMQ every 5 seconds |
| Grafana | Dashboards and alert rules | Reads the three stores |

Logs carry the trace id, so a log line links to its trace. This is what lets the agent jump from a log to the full request path.

**Alert rules** (all evaluate every 10 seconds, fire after 30 seconds):

| Alert | Condition |
|---|---|
| OrderSubmitErrorRateHigh | More than 20% of order submissions fail |
| PricingConsumerErrorRateHigh | More than 20% of consumed orders fail |
| OrderQueueBacklog | More than 20 messages waiting in the queue |

A Grafana contact point (`incident-notifications`) sends each alert to two webhooks: the alert trigger (the agent's entry point), and a small recorder (`alert-recorder`) that the verification script reads.

## B6. Agent core (Layer 3)

### Model gateway

The only path to any model. The rest of the code sees one interface and never a vendor SDK.

- `ModelGateway` is a protocol with one method, `complete(system, messages, tools)`, returning a normalized `ModelResponse`: text, tool calls, token usage, cost, latency, stop reason and the raw content blocks.
- `AnthropicGateway` is the only file that imports the Anthropic SDK. A test fails the build if any other file does.
- `FakeGateway` plays back scripted responses so the loop is tested with zero spend.
- The model is pinned in config (`claude-haiku-5-5` for development, override with `AGENT_MODEL`). Cost per call comes from a price table, and an unknown model raises rather than reporting zero.
- Requests carry no sampling parameters and no forced tool choice, because current models reject both. Thinking blocks round-trip unchanged.

### Core loop

The contract is `run_diagnosis(alert, gateway, toolbox, max_steps=8) -> DiagnosisResult`. The model never runs tools: it requests them and the loop executes them, so the loop is the control point for limits, safety and records.

```mermaid
stateDiagram-v2
    [*] --> Prompting: alert received
    Prompting --> CallingModel: build history
    CallingModel --> RunningTools: tool call requested
    RunningTools --> CallingModel: results returned
    CallingModel --> Validating: submit_diagnosis called
    Validating --> Complete: valid
    Validating --> CallingModel: invalid, one retry with error text
    Validating --> Failed: invalid again
    CallingModel --> Forced: step cap reached
    Forced --> Complete: valid diagnosis submitted
    Forced --> Failed: none obtained
    CallingModel --> Failed: gateway error
    Complete --> [*]
    Failed --> [*]
```

`tests/test_loop.py` holds ten scenarios the loop must satisfy: happy path, parallel tool calls answered in one message, tool errors passed to the model, unknown tools, step-cap forcing, failure after the cap, one retry on a bad diagnosis, failure after two, cost summing, and gateway failure. The loop in `agent/loop.py` passes all ten, and seven more cover a text-only reply, a bad submission sent alongside another tool call, a tool that raises, a tool requested after the cap, invalid `max_steps`, and a tool list that never changes during a run (a real-API failure the mocks could not show; see [baseline-log.md](baseline-log.md)).

### Alert trigger

`agent/trigger.py` (FastAPI, port 8004, run with `uvicorn agent.trigger:app --port 8004`).

- `POST /alerts` reads Grafana's payload, ignores resolved alerts, and starts one background diagnosis per firing alert.
- The same alert is not re-diagnosed within 10 minutes. At most 2 diagnoses run at once. 200 diagnosis jobs are kept, in memory (job records are in memory; the audit log, approvals and kill switch state are in Postgres).
- A crash in a diagnosis is recorded as a failed result, never an error to Grafana.
- `GET /diagnoses` and `GET /diagnoses/{id}` return results.

### Data contracts

Typed schemas guard every boundary: alert in, tool arguments, diagnosis out.

```mermaid
classDiagram
    class Alert {
        +str name
        +str status
        +dict labels
        +str summary
        +str starts_at
    }
    class Evidence {
        +source: logs, trace or metrics
        +str reference
        +str quote
    }
    class Diagnosis {
        +str root_cause
        +str category
        +list~Evidence~ evidence
        +float confidence
        +list~str~ affected_services
        +str recommended_action
    }
    class DiagnosisResult {
        +Alert alert
        +status: complete, forced or failed
        +Diagnosis diagnosis
        +str error
        +int steps
        +int tool_calls
        +int input_tokens
        +int output_tokens
        +float cost_usd
        +float latency_s
    }
    Diagnosis "1" *-- "1..*" Evidence
    DiagnosisResult --> Alert
    DiagnosisResult --> Diagnosis
```

`Diagnosis` is what the model must submit, through a `submit_diagnosis` tool whose input schema is generated from the model class. Validation checks structure: confidence between 0 and 1, at least one piece of evidence, at least one affected service. Whether the diagnosis is correct is decided by evals, not by validation.

## B7. MCP tools (Layer 4)

An MCP server (`mcp_server/server.py`, stdio) exposes three read-only tools. The agent reaches them through a `Toolbox` abstraction backed by an MCP client, so tests can swap in fakes.

| Tool | Inputs | Backend | Behavior |
|---|---|---|---|
| `query_logs` | service, level, contains, minutes (1-60), limit (1-100) | Loki | Identical messages collapse into one line with a count. Exception type and message are shown. Each line shows a trace id, chosen from a line at least 45 seconds old so Tempo has indexed the trace. Messages capped at 300 characters. Output labeled as untrusted data. |
| `get_trace` | trace_id (32 hex characters) | Tempo | Spans in time order with service, duration, error flag, HTTP status and exceptions. Up to 60 spans. |
| `query_metrics` | query (PromQL), minutes (1-120) | Prometheus | Each series summarized as last, min, max, first. Up to 10 series. |

Design rules shared by all tools:

- Inputs are validated before they reach a query language (service names against a pattern, trace ids against hex, quotes escaped), so the model cannot inject LogQL.
- Output is compact and size-capped so one call cannot flood the model's context.
- Failures return an error result the model can read, not an exception.
- `sanitize_log_text` truncates text; the prompt-injection filter runs on every tool result in the governed toolbox (B8).

Knowledge tools are built (see [knowledge-design.md](knowledge-design.md)). The two action tools exist only as propose-only tools (B8):

| Tool | Kind | Purpose |
|---|---|---|
| `search_runbooks` | Knowledge | Postgres full-text search over the runbooks Ankur writes |
| `get_service_info` | Knowledge | Service catalog: dependencies, message contracts, owners, known quirks, refresh schedules |
| `get_recent_changes` | Knowledge | Deploys, config and schema changes, the "what changed?" question people ask a colleague |
| `replay_message` | Action | Sandbox only, behind guardrails and approval |
| `draft_pull_request` | Action | Draft only, behind guardrails and approval |

Later: `search_past_incidents` (earlier diagnoses and how they were resolved) and `ask_human` (the agent poses a specific question to the on-call; question and answer are audit-logged). After each incident the engineer confirms or corrects the root cause, which feeds past incidents and becomes a new eval label, so tribal knowledge is written down once.

**Knowledge is the lever for step 3.** Telemetry explains what happened; knowledge explains why. The quality, ownership and freshness of these sources cap the agent's accuracy, so they are treated as governed assets: owned, dated, versioned in the repository and cited in diagnoses.

## B8. Governance (Layer 5)

Every proposed action passes this path. Nothing executes without an approval record.

```mermaid
flowchart LR
    D["Diagnosis +<br/>proposed action"] --> KS{"Kill switch<br/>off?"}
    KS -- "no" --> STOP1["Stop, log"]
    KS -- "yes" --> GR{"Guardrails<br/>allowlist, rate limit,<br/>dry-run default"}
    GR -- "block" --> STOP2["Reject, log,<br/>count block metric"]
    GR -- "pass" --> HUM{"Human approval"}
    HUM -- "deny" --> STOP3["Reject, log"]
    HUM -- "approve" --> EX["Execute<br/>(sandbox / draft only)"]
    EX --> AUD[("Audit log")]
    STOP1 --> AUD
    STOP2 --> AUD
    STOP3 --> AUD
```

What is built, where, and the property each part guarantees:

| Control | Code | Guarantee |
|---|---|---|
| Governed toolbox | `agent/governed.py` | Every tool call passes kill switch, allowlist and rate limits, is audited before and after, and has its output filtered. If the audit log is down the call is refused (fail closed). |
| Allowlist and limits | `agent/guardrails.py`, `agent/runtime.py` | A tool exists for the model only if it is named for this configuration. Per-tool, per-diagnosis and global rate limits. |
| Injection filter | `agent/guardrails.py` | Instruction-like phrases in logs and knowledge text are cut out and reported. A tripwire, not the guarantee: the guarantee is that nothing the model reads can execute an action. |
| Budgets | `agent/loop.py`, `agent/runner.py` | Per-diagnosis spend cap forces a final answer; a daily budget refuses new diagnoses. |
| Audit log | `agent/audit.py` | Append-only in Postgres (triggers refuse UPDATE, DELETE, TRUNCATE). Records tool calls, guardrail blocks, proposals, approvals, executions, model in use and kill switch changes. |
| Kill switch | `agent/killswitch.py`, `agent/admin_api.py` | Stored in Postgres, shared by every process, checked before each diagnosis and before each step. Reads as engaged if its state cannot be read. Every change is audited with actor and reason. |
| Propose-only actions | `agent/proposals.py`, `agent/actions.py` | The model's only action tools write a pending record. Replay targets the sandbox queue only; a draft PR can only add one markdown file under `proposed-fixes/`. Arguments are validated at proposal and again at execution. |
| Approval gate | `agent/actions.py`, `agent/approval_store_pg.py`, `agent/admin_api.py` | A decision happens once, atomically; expired proposals cannot be approved; the agent cannot approve its own proposal; execution is a dry run unless real execution is switched on as well. |
| Admin access | `agent/admin_api.py` | Protected by `AGENT_ADMIN_TOKEN`, compared in constant time. If the token is unset every admin endpoint refuses, so a forgotten setting disables approvals instead of opening them. |

Tests: allowlist, rate limits, injection patterns (with false-positive checks on ordinary logs), append-only enforcement against real Postgres, fail-closed audit and kill switch, the approval gate, and an end-to-end adversarial scenario in which the model "obeys" injected text and still cannot execute anything. A live scenario (G01) plants injection text in an order SKU so it reaches the logs the agent reads; the eval report shows the outcome.

## B9. Agent observability (Layer 6)

The agent is observed with the same machinery as the services it watches. Wrappers decorate the gateway and toolbox, so the loop itself needs no tracing code.

**Span hierarchy** (one tree per diagnosis):

```
diagnose <alert name>                  kind: AGENT   status, steps, cost, confidence, category
  chat <model>                         kind: LLM     model, tokens in/out, cost, finish reason, input, output
  execute_tool query_logs              kind: TOOL    tool name, arguments, result text, error flag
  chat <model>
  execute_tool get_trace
  chat <model>
```

Spans carry both OpenTelemetry GenAI attributes and OpenInference attributes, which is what lets Phoenix render them as LLM, tool and agent steps. Prompts and outputs are captured by default, truncated to 4,000 characters, and can be switched off with `AGENT_TRACE_CONTENT=false`.

**Where signals go**

| Signal | Destination | Used for |
|---|---|---|
| Spans | Phoenix (project `incident-agent`) | Replaying exactly what the agent did |
| Spans | Collector, then Tempo | Same traces inside Grafana |
| Metrics | Collector, then Prometheus | AI Health dashboard and alerts |

**Agent metrics**

| Metric | Labels | Meaning |
|---|---|---|
| `agent_diagnoses_total` | status | Diagnoses finished |
| `agent_diagnosis_duration_seconds` | | Wall time per diagnosis |
| `agent_model_calls_total` | model, outcome | Model calls |
| `agent_model_call_duration_seconds` | model | Model latency |
| `agent_tokens_total` | model, type | Input and output tokens |
| `agent_cost_usd_total` | model | Spend |
| `agent_tool_calls_total` | tool, outcome | Tool calls and errors |
| `agent_guardrail_blocks_total` | kind, tool | Blocks and cleanups: policy, kill_switch, injection |
| `agent_actions_proposed_total` | tool | Actions the agent proposed |
| `agent_actions_decided_total` | decision | Approvals and rejections |

The **AI Health** dashboard shows diagnoses by status, cost per diagnosis and total spend, tokens by type, model latency (p50, p95), tool calls by outcome, and diagnosis duration. Four alert rules watch the agent itself (cost per diagnosis, tool error rate, guardrail blocks, failure rate). They carry `source=agent` and route to a separate contact point, never to the agent trigger: an agent must not diagnose its own health.

## B10. Evaluation (Layer 7)

`python -m evals.run` replays labeled failures through the live stack and scores the result. The report is [eval-report.md](eval-report.md).

For each failure the harness injects it under load, waits for the real Grafana alert, then runs each arm several times against the same live evidence: **A** telemetry only, **B** plus runbooks, the service catalog, contracts and change records. Telemetry queries are limited to a window that starts just before the injection, and the change feed and knowledge index are reset per scenario, so scenarios cannot contaminate each other.

| Score | How |
|---|---|
| Root-cause accuracy | A judge model (different and larger than the agent, blind to the arm) compares the diagnosis with the labeled cause: correct, partial or incorrect |
| Fix quality | The same judge compares the recommended action with the labeled fix |
| Evidence grounding | Share of evidence quotes found in the tool results the model saw (quotes may join real excerpts with `;` or `...`) |
| Confidence calibration | Diagnoses bucketed by stated confidence against actual accuracy; Brier score and calibration error |
| Knowledge use | How often arm B cited a knowledge entry, and the one a person would expect |
| Cost and latency | From the result record |
| Guardrails | G01, a live prompt-injection scenario, plus the governance test suite |

The failure catalog and the knowledge content are placeholders and drafts, so the numbers demonstrate the method; they are not a benchmark. The report says so at the top.

---

# Part C: Decisions, status and operations

## C1. Design decisions

| Decision | Why | Trade-off |
|---|---|---|
| Claude only, through a thin gateway | Keeps one vendor to evaluate and secure, while the interface stays swappable | No local-model option |
| Custom agent loop first | The loop is the core of the system and must be understood and defended line by line | More code than a framework; a LangGraph port is a stretch goal |
| MCP for tools | A standard interface that other agents and clients can reuse | An extra process boundary |
| Investigation tools are read-only; actions are propose-only | The model can never change anything; a person approves every effect | The agent cannot fix anything by itself |
| Pydantic schemas at every boundary | Untrusted data (model output, log text, tool arguments) is checked before use | Validation proves shape, not truth |
| Diagnosis category as free text plus a short label | Does not leak answers to the agent or break when the real catalog replaces the placeholders | Harder to score than a fixed list; revisit when the real catalog exists |
| Phoenix over Langfuse | One container, no extra databases; Langfuse v3 needs Postgres, ClickHouse, Redis and object storage | Fewer enterprise features |
| Agent in a container, host mode for development | One command starts everything; the loop is still easy to debug on the host | A second way to run it |
| Tracing and metrics as wrappers | The loop stays free of observability code | One more layer to maintain |
| `mcp<2` pinned | Version 2 renamed the server API | Upgrade later on purpose |
| Same-alert dedupe and a concurrency cap | Grafana repeats notifications; model calls cost money | A real second incident for the same alert within 10 minutes is skipped |
| Small model first, large model for final evals | Cheap iteration, strong final numbers | Two runs to maintain |

## C2. Implementation status

| Area | Status | Where | Tests |
|---|---|---|---|
| Order, pricing services, queue | Built | `simulator/` | 23 |
| Telemetry stack, dashboard, alerts | Built | `observability/`, `docker-compose.yml` | verified by script |
| Failure injector and catalog | Built (placeholder catalog) | `simulator/failure_injector/`, `failures/` | included above |
| Model gateway | Built | `agent/gateway.py`, `agent/anthropic_gateway.py`, `agent/config.py` | 7 |
| MCP server and tools | Built | `mcp_server/`, `agent/toolbox.py` | 12 |
| Diagnosis schema | Built | `agent/schemas.py` | 9 |
| Alert trigger | Built | `agent/trigger.py` | 5 |
| Agent tracing and metrics | Built | `agent/tracing.py`, `agent/runner.py` | 5 |
| Phoenix and AI Health dashboard | Built | `docker-compose.yml`, `ai-health.json` | checked on live stack |
| Core loop | Built; Ankur to review | `agent/loop.py`, `docs/loop-checklist.md` | 16 |
| End-to-end run | Waiting on the loop | | |
| Knowledge machinery (store, ingestion, lint, five tools, off switch) | Built | `knowledge_store/`, `mcp_server/knowledge_tools.py` | 35 |
| Knowledge content (runbooks, catalog entries) | Ankur to write; catalog entries drafted | `knowledge/` | |
| Actions, governance, kill switch | Built | `agent/actions.py`, `proposals.py`, `governed.py`, `guardrails.py`, `audit.py`, `killswitch.py`, `admin_api.py`, `runtime.py` | 83 |
| Eval harness, ablation, report | Built; run results in the eval report | `evals/` | 17 |

Test totals: 244 tests (the tests that need Postgres skip themselves when it is not running).

## C3. Ports and endpoints

| Port | Service | Notes |
|---|---|---|
| 5432 | Postgres | Knowledge store (audit log, approvals, kill switch); persistent volume |
| 3000 | Grafana | Anonymous admin (POC); dashboards "Order Flow" and "AI Health" |
| 6006 | Phoenix | Trace viewer and OTLP/HTTP intake; auth off (POC) |
| 8001 | order-service | `POST /orders`, `/admin/faults` |
| 8002 | pricing-service | `/quote/{sku}`, `/admin/faults` |
| 8003 | alert-recorder | Records alerts for the verification script |
| 8004 | alert trigger (`agent/trigger.py`) | On the host, not in Compose |
| 3100, 3200, 9090 | Loki, Tempo, Prometheus | Queried by the MCP tools |
| 4317, 4318 | OTel Collector | OTLP in |
| 5672, 15672 | RabbitMQ | Broker and management UI |

## C4. How to run it

Prerequisites: Docker, Python 3.13 virtual environment (`.venv`), and an Anthropic key in a gitignored `.env`.

```bash
docker compose up -d --build
```

```bash
.venv/bin/pip install -r requirements-dev.txt
```

```bash
.venv/bin/python -m pytest tests -q
```

Run the alert trigger on the host:

```bash
.venv/bin/uvicorn agent.trigger:app --port 8004
```

Generate traffic and inject a failure (separate terminals):

```bash
PYTHONPATH=. .venv/bin/python scripts/load.py --rps 5 --seconds 180
```

```bash
.venv/bin/python -m simulator.failure_injector.injector inject F03 --duration 120
```

Then open Grafana (<http://localhost:3000>), Phoenix (<http://localhost:6006>) and `GET http://localhost:8004/diagnoses`.

## C5. From POC to production

What this proof of concept deliberately simplifies:

| Area | POC | Production |
|---|---|---|
| Runtime | Docker Compose on one machine | Kubernetes or managed containers |
| Storage | Container filesystem for Loki, Tempo, Prometheus; Docker volume for Phoenix | Object storage or managed backends with retention and backup |
| Broker | Single RabbitMQ node | Clustered or managed broker with dead-letter queues and idempotent consumers |
| Access | Anonymous Grafana, Phoenix without login | SSO, role-based access, mTLS |
| Secrets | `.env` file | Secrets manager and rotation |
| Alerts | Fixed thresholds | SLO-based, multi-window alerting |
| Telemetry volume | Everything sampled | Sampling and cost controls |
| Agent hosting | On the host | Containerized, autoscaled, with the kill switch behind access control |
| Agent state | In-memory diagnosis jobs | Durable store with the audit log |
| Database credentials | Throwaway default password in `docker-compose.yml` | Secrets manager and rotation |

## C6. Risks and open questions

- **Catalog realism.** The placeholder failures are cleaner than real ones. Accuracy numbers are only as credible as the catalog behind them. Replacing it is the single biggest lever on the project's credibility.
- **Sample size.** Eight to ten failures give a rough accuracy figure, not a statistically tight one. Say so when publishing.
- **Judge bias.** If a model scores fix quality, its bias needs a sanity check against human review of a sample.
- **Prompt-injection coverage.** Labeling log text as untrusted reduces risk but does not remove it. The filter and the adversarial tests exist for this reason.
- **Model changes.** The pinned model protects against silent changes, but a deliberate upgrade needs a rerun of the evals before adoption.
- **Open design question.** Fixed list versus free text for the root-cause category (see C1).

## C7. Status and what remains

All four planned days are built (see C2). What is left is content and judgment that only Ankur can supply:

| Item | Owner | Why it matters |
|---|---|---|
| Replace the placeholder failure catalog with real failures | Ankur | The credibility of every accuracy number depends on how realistic the failures are |
| Replace the draft runbooks and catalog entries with experience-based ones | Ankur | The ablation currently measures what Claude-written drafts are worth, not tribal knowledge |
| Review the action tools and guardrails line by line | Ankur | Hard rule: the person who owns the system reviews the code that can change it |
| Review `agent/loop.py` until it can be explained | Ankur | Hard rule 8: every piece must be defensible |
| Create a GitHub token scoped to this repo if real draft PRs are wanted | Ankur | Without it draft PRs stay dry-run |
| Re-run the evals on the real content and publish | Both | Results on the placeholder content are a demonstration of the method |

Not built: past-incident search and `ask_human` (stretch), the outcome-capture loop that turns a resolved incident into a runbook and an eval label, and the LangGraph port.
