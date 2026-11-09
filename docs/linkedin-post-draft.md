# LinkedIn post draft (not posted)

Everything in the post is something the repo can back up. Replace the bracketed parts once the full ablation has run on your real runbooks and failures. Do not quote an accuracy number from the placeholder content.

---

I ran off-the-shelf AI incident tools in production. They could summarize an alert. They couldn't show me what they looked at, prove they wouldn't act on a poisoned log line, or tell me what a wrong answer would cost. So I built the version I wanted, and treated governing the AI as half of the project.

What it is: an agent that investigates integration failures in a simulated order flow (logs, traces, metrics, runbooks), reports a root cause with quoted evidence and a confidence score, and proposes a fix that a person must approve. Around it, a platform that watches the agent the way it watches the services.

What's in it:
- Read-only tools over MCP. The model can only propose actions; a human approves; execution is a dry run by default.
- An append-only audit log (the database refuses edits), a kill switch that fails closed, budgets, an allowlist, and a filter for instructions hidden in logs.
- Every model and tool call traced to Phoenix; cost, latency and guardrail blocks on a Grafana dashboard; alert rules on the agent itself.
- 250+ tests, including an adversarial one where the model "obeys" injected text and still can't execute anything. In a live test I planted instructions in an order's SKU so they landed in the logs the agent reads: 3 of 3 runs never showed them to the model, nothing was approved, and the diagnoses still completed.
- A diagnosis costs about half a cent and takes 15 to 30 seconds on Haiku.

What I learned by actually running it (none of it showed up in the mocked tests):
- My log tool hid the exception message that contained the answer. A baseline is only fair if the tools show what a person would see.
- Changing the tool list mid-run silently breaks the model's reasoning state (an HTTP 400 that looked random).
- The model sometimes leaves required fields out, or leaks its own tool-call markup into a text field. Schema enforcement and validation matter more than prompts.
- Two of my eval runs were lost to the laptop sleeping and a network blip. Infrastructure failures must never be scored as wrong answers.

The honest caveats: the failures are a placeholder catalog and the runbooks are drafts, so I'm not publishing an accuracy number. [After the full run: what knowledge was worth, with real content, and what it cost.]

Repo: https://github.com/ankgupt9/agentic-incident-response

#AIAgents #MCP #AIGovernance #Observability #SRE
