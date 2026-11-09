# Baseline log

A running record of live runs of the agent: what was run, what happened, and what it taught us. Numbers here are informal (one person scoring a few runs), not the Day 4 eval. They show the shape of the problem and which fixes mattered.

## Arm A, first live run: F03 (stale price quotes), telemetry only

Date: 2026-11-09. Model: `claude-haiku-5-5`, effort medium, 8-step cap. Knowledge tools switched off (`AGENT_KNOWLEDGE_TOOLS=off`), so the agent had `query_logs`, `get_trace` and `query_metrics` only. Failure: F03 injected under steady traffic; the real Grafana alert `PricingConsumerErrorRateHigh` fired about 50 seconds later and woke the agent through the webhook. Labeled truth: the quote endpoint serves prices 10% off the price list the consumer validates against (category `data_inconsistency`).

### Result: before and after the fixes below

| | First run (2 diagnoses) | After the tool fixes only (4 diagnoses) | After all fixes (14 diagnoses) |
|---|---|---|---|
| Found the root cause (quotes about 10% above the consumer's price list) | 0 of 2: localized to the pricing consumer, said the cause was "not established" | 4 of 4 in substance, but 2 failed validation (a required field was missing) | 4 of 4 read in full and correct. The other 10 were only checked for completion and field presence, not read for substance |
| Completed with a valid diagnosis | 1 of 2 (the other died on HTTP 400) | 2 of 4 | 14 of 14 |
| Confidence | 0.3 | 0.6 | 0.6 to 0.7 |
| Cost per diagnosis | about $0.005 | $0.005 to $0.007 | $0.002 to $0.005 |
| Time per diagnosis | about 15 seconds | 17 to 22 seconds | 15 to 19 seconds |

The first run was honest about its uncertainty: a wrong-or-incomplete answer with low confidence is the behavior the calibration eval wants to reward.

### What the first live run found (none of it visible to the mocked tests)

1. **A tool defect hid the answer.** The error record in Loki carried `exception_message = price mismatch for SKU-1001: quoted 21.99, current 19.99`, but `query_logs` printed only the log line, "failed to price order". A person looking at the log in Grafana sees those fields. **Fix:** `query_logs` now shows the exception type and message, and different exceptions are not collapsed together. *Lesson for the ablation: a baseline is only fair if the tools expose what a human would see.*
2. **Fresh traces are not retrievable.** The agent chose the newest log line, whose trace Tempo had not indexed yet (404). **Fix:** the trace id shown with a grouped log line now belongs to the newest line at least 45 seconds old, and the 404 message says to try an older line.
3. **Changing the tool list mid-run breaks the conversation.** At the step cap the loop removed the investigation tools to force an answer. The API rejected the next call (HTTP 400, "thinking block is bound to a different conversation"), but only when the model had thought before a tool call, which is why it looked random. **Fix:** the tool list never changes during a run; the loop tells the model to submit and refuses further tool calls itself. Reproduced on demand with a 2-step cap before fixing.
4. **The model sometimes left out a required field.** In 1 of 5 runs it omitted `category`, twice in a row even after being told. **Fix:** the `submit_diagnosis` tool is now marked `strict`, which makes the API guarantee the input matches the schema. Strict mode only accepts a restricted schema, so a strict-safe copy is generated (constraints moved into the description) while Pydantic keeps enforcing the full rules. After the change, 5 of 5 runs had every field.
5. **Present is not meaningful.** One diagnosis carried `category: "x"`. **Fix:** the category must now be a snake_case label of 3 to 40 characters, and a violation triggers the loop's one correction.
6. **The gateway hid the real error.** It reported only "HTTP 400". It now includes the API's error text, which is what made item 3 diagnosable.

### Caveats

- One failure (F03), a handful of runs, scored by the author. This is a smoke test of the whole path, not a measurement.
- F03 may be too easy for telemetry alone: once the exception message is visible, the cause is in the logs. The knowledge ablation will only discriminate on failures where telemetry shows the symptom but not the cause (for example F02 slow quotes, F08 stalled consumer, or failures from the real catalog). Expect arm A to look strong here and say little about what tribal knowledge is worth.
- Replayed alerts used the same payload shape as Grafana's but were sent by hand; two runs in this record came from real Grafana notifications.
- Other incidents were visible in the logs from earlier test runs (an older broker-connection failure). The agent noticed it and did not confuse it with this one, which is a small positive sign.
