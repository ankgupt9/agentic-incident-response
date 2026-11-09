# Demo script (about 3 minutes)

Setup before recording: `docker compose up -d --build`, wait about a minute, open three tabs: Grafana *Order Flow*, Grafana *AI Health*, Phoenix. Keep a terminal ready.

| Time | Say | Show |
|---|---|---|
| 0:00 | "An AI agent that diagnoses integration failures, and a platform that governs and monitors the agent. Both halves matter." | The architecture diagram from the README |
| 0:20 | "Orders flow through two services and a queue. I'll break something." | Terminal: start load, then `inject F03`. Grafana *Order Flow*: the error ratio climbing |
| 0:50 | "Grafana fires the alert and calls the agent. No human involved yet." | Grafana alert state firing, then `curl localhost:8004/diagnoses` showing a running job |
| 1:10 | "The agent uses read-only tools: logs, traces, metrics, plus the team's runbooks and recent changes." | Phoenix: the trace of the diagnosis, expanding a tool call and the model call |
| 1:40 | "It reports a root cause with evidence, a confidence score, and which knowledge it relied on. Every citation is checked against what its tools returned." | The diagnosis JSON: root cause, evidence, `knowledge_refs`, confidence |
| 2:00 | "Everything it did is in an append-only audit log, and it cost under half a cent." | `GET /diagnoses/<id>/audit` (with the admin token); AI Health: cost and latency panels |
| 2:20 | "It can propose a fix, never perform one. A person approves, and even then it's a dry run by default." | The approvals page with a pending action |
| 2:40 | "And it can be stopped instantly." | Engage the kill switch; send another alert; the new diagnosis is halted; the change is in the audit log |
| 2:55 | "I measured it: accuracy with and without knowledge, whether its confidence can be trusted, and how it handles a prompt-injection attack." | The eval report headline table |

Keep the honest caveat on screen at the end: the failures and runbooks are stand-ins.
