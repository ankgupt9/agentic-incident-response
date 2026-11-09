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

## Full eval, run 1 (2026-11-09): invalidated by infrastructure failures

First full run of `python -m evals.run`: 8 failures, 3 repeats, arms A and B, plus the prompt-injection scenario and the governance suite. The headline looked bad (arm A 38% correct, arm B 25%), but **24 of the 48 runs never reached the model**: `model call failed: connection error`, almost always on the first call, and concentrated in whole scenarios (F02 to F05 lost all six runs). Both arms were hit equally. I could not reproduce it afterwards (12 concurrent calls and a call after a 150-second idle gap all succeeded). Run 2 later showed the likely cause: the Mac running the eval was entering Maintenance Sleep, which freezes the harness and leaves stale network connections that surface as `connection error` (see below). It was not the agent or the harness's concurrency.

The lesson is about method: **an infrastructure failure must not be scored as a wrong diagnosis.** Counting those 24 runs as "incorrect" made both arms look far worse than they were. Among the runs that did reach the model, arm A completed 10 and got 9 right, and arm B completed 7 and got 6 right, which is far too few to compare the arms.

What the run did show, independent of the outage:

- **The prompt-injection scenario passed 3 of 3.** Instruction text planted in an order SKU never reached the model, nothing was approved or executed, and diagnoses still completed. The filter flagged 36 phrases across the runs. The 83 governance tests passed.
- **The model leaks tool-call markup into text fields fairly often.** Of the runs that produced a submission, 17% (arm A) and 42% (arm B) had `</root_cause><parameter ...>` fragments inside `root_cause`, which the validation rule I had just added rejected, failing the run after the one correction. The fragments are the next parameter written into the wrong place.
- **Calibration was reasonable.** When the agent completed a diagnosis its stated confidence (0.4 to 0.8) was lower than its accuracy (86% to 100% on the completed runs), so it is under-confident, not over-confident, which is the safer direction.

Fixes made before re-running:

| Problem | Fix |
|---|---|
| Infrastructure failures scored as wrong answers | The harness retries a run that fails for a reason outside the agent (connection error, HTTP 408, 429, 5xx) up to 3 times with a pause, then excludes any that still fail and reports the count. |
| Opaque "connection error" | The gateway now says what the underlying error was, and the SDK retries up to 4 times with backoff. |
| Leaked markup failing runs | The loop trims the field at the first leaked marker (when real text precedes it), records the repair on the result, and the report counts repairs. A field with nothing before the leak is still rejected. |
| Starting a 30-minute run into an outage | A preflight call aborts the run immediately if the API is unreachable. |
| Hiding the completion rate inside accuracy | The report now shows the completion rate and accuracy among completed runs separately. |


## Full eval, run 2 (2026-11-09): stopped, the machine kept sleeping

Run 2 used the fixes above and got through F01 to F03 with no infrastructure failures, but the log showed gaps of 15 to 18 minutes with no output at all, four times. `pmset -g log` showed the cause: the Mac, on battery, repeatedly entered Maintenance Sleep, waking for roughly 45 seconds every 15 minutes. A `caffeinate` assertion (with `-s`, and on behalf of the eval process) was active and the app's own keep-awake was on, yet it still slept: that signature means a closed lid or a forced sleep, which software cannot prevent. At that rate the remaining scenarios would have taken hours, and sleeping mid-scenario freezes the load generator, so I stopped the run, cleared the injected fault and dropped the temporary schema. The harness is resumable; finished scenarios are kept.

What this changed: the harness starts `caffeinate` itself, retries and excludes infrastructure failures, and the report says plainly that no valid full ablation exists yet (see [eval-report.md](eval-report.md)). It also showed, once more, that long-running measurements need a machine that stays awake.

Other things the live runs showed:

- **Real alert to containerized agent works.** Following the README quick start literally (load and injection run inside the container), the real Grafana alert reached the containerized agent, which diagnosed F03 citing `RB-001`, `svc:pricing-service` and `CHG-2003`, with 26 audit events and a $0.006 cost.
- **A gap the quick start would have hit:** the injector inside the container wrote change records to a local file, but the agent reads them from Postgres. The injector now publishes them to the change store too (best effort, reported).
- **The markup trimming fired in real output** (5 of 9 runs per arm in F01 to F03, and once in the container run).
