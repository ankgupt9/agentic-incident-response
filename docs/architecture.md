# Architecture

Two systems share one telemetry stack: a simulated order flow that breaks on purpose, and an agent that diagnoses the breakage. The same Prometheus, Grafana and Tempo that watch the orders also watch the agent.

Solid boxes are built. Dashed boxes are planned (Days 2 to 4).

## 1. System overview

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
        ACT["Action tools: replay_message,<br/>draft_pull_request"]:::planned
    end

    subgraph L5["Layer 5: Governance (Day 3)"]
        direction LR
        GR["Guardrails"]:::planned --> AP["Human approval"]:::planned --> AUD[("Audit log")]:::planned
    end

    subgraph L6["Layer 6: Agent observability"]
        direction LR
        PHX["Phoenix traces"]
        AIH["AI Health dashboard"]
    end

    EVAL["Layer 7: Eval harness (Day 4)<br/>accuracy, cost, latency,<br/>confidence calibration"]:::planned

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

## 2. From failure to diagnosis

What happens when a failure is injected, in order.

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

## 3. Governance control flow (Day 3, planned)

The agent can only look today. When actions arrive (`replay_message`, `draft_pull_request`), every one passes this path. Nothing executes without an approval record.

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

    classDef planned stroke-dasharray: 5 5,fill:#f6f6f6,color:#555;
    class D,KS,GR,HUM,EX,AUD,STOP1,STOP2,STOP3 planned;
```

## Where things live

| Layer | Code and config |
|---|---|
| 1. Simulated enterprise | `simulator/`, `failures/catalog.yaml`, `docker-compose.yml` |
| 2. Platform observability | `observability/` |
| 3. Agent core | `agent/gateway.py`, `agent/anthropic_gateway.py`, `agent/loop.py`, `agent/trigger.py`, `agent/runner.py` |
| 4. MCP tools | `mcp_server/`, `agent/toolbox.py` |
| 4. Knowledge | `knowledge/` (content), `knowledge_store/` (lint, ingest, search), `mcp_server/knowledge_tools.py` |
| 6. Agent observability | `agent/tracing.py`, `observability/grafana/dashboards/ai-health.json` |
