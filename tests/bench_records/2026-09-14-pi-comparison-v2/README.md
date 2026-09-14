# AskMe–pi v2: four valid, unresolved feature attempts

The separately preregistered [September 14 run](https://github.com/den-run-ai/askme/actions/runs/34812520762)
completed all four cells with valid infrastructure, audited model/provider routes,
and independent evaluation. Every patch applied, but **none resolved the task**.

| Dense model | Harness | Target tests passed | Agent termination |
|---|---|---|---|
| Gemma 4 31B | AskMe | 8/13 | Exhausted |
| Gemma 4 31B | pi | 11/13 | Ended normally; task still unresolved |
| Qwen3.6-27B | AskMe | 8/13 | Exhausted |
| Qwen3.6-27B | pi | 11/13 | Stopped at conservative reservation limit |

All four patches passed **387/387 preservation tests**. These are one-attempt
observations on one previously used Seaborn task, not a reliability estimate,
general harness ranking, or model-family comparison. AskMe and pi used matched
routes within each model and equal external ceilings while retaining different
prompts, planning, tools, internal step limits, and per-call token requests.
This is a comparison of whole harnesses, not a causal component ablation.

## Cost and instrumented timing

| Cell | Hosted calls | Audited API cost | Episode wall time, seconds | Metadata polling, seconds |
|---|---:|---:|---:|---:|
| Gemma / AskMe | 29 | $0.01618668 | 591.514 | 390.119 |
| Gemma / pi | 10 | $0.01463832 | 243.898 | 135.930 |
| Qwen / AskMe | 36 | $0.05336300 | 650.873 | 498.430 |
| Qwen / pi | 36 | $0.14825000 | 1087.685 | 456.295 |

All 111 forwarded calls completed with valid audits. Their generation-metadata
charges and response telemetry agree exactly at **$0.23243800**. No call exceeded
its reserved ceiling, and no native reasoning tokens were reported.

Qwen/pi's next request would exceed the $2 cell reservation ceiling, so the proxy
rejected it without forwarding another completion. Its final reservation was
$1.95337710 despite audited usage of $0.14825000: the intentionally conservative
proxy never refunds dollar reservations. Its process exited with code 0, but
its final assistant event was an error; it did not complete the episode.

V2 retained $2.88893070 in reservations, which are allowances rather than charges.
Including v1's unreconciled $0.05043320 reservation gives $2.93936390 against the
original $10 budget. V1 response-only telemetry plus v2 audited charges total
$0.23542938; those two sources have different reconciliation status.

Metadata polling totals the final elapsed time once per generation lookup,
not every cumulative retry timestamp. It contributed 1,480.774 seconds across
the cells. These times describe this instrumented setup. Pi can overlap streamed
tool execution with auditing, while a nonstreaming client can wait for EOF;
neither the raw times nor their difference after subtraction establishes native
model latency or a harness speed advantage.

## What the agents checked

Gemma/AskMe executed two narrow `python -c` bootstrap sanity probes; Qwen/AskMe
executed no shell checks. Both exhausted their three planning attempts. The
frozen AskMe tool schema omitted the `read.limit` range enforced at runtime, and
out-of-range reads occurred in these trajectories. That interface limitation
must not be relabeled pure model inability; this study does not estimate its
causal contribution.

Gemma/pi's visible statistics suite finished at 69 passed and 3 skipped after
an earlier failure and an import repair. Qwen/pi recorded a visible regression
suite at 52 passed and 9 skipped and a later visible suite at 1,554 passed and
73 skipped. These were available tests, separate from held-out acceptance.
Piped shell commands can conceal a pytest exit failure, so these observations
use the recorded pytest summaries rather than the tool's error flag alone.

## Provenance and limits

The [audit](audit.json) records each outcome, source hashes, route evidence,
controls, and cost/timing calculations. All 123 original inventory files
(4,709,713 bytes) matched their [retained hashes](artifact-sha256.json).
The frozen source-manifest hash matches the [claim](claim.json); the runner's
[staged protocol](protocol.json) has identical JSON values with different
formatting. All six runtime modules match AskMe `3ec477f`.

Fresh controls qualified before inference: gold resolved at 13/13 target and
387/387 preservation tests; a harmless nonempty patch applied but remained
unresolved at 0/13 and 387/387. Every cell started from the same masked tree,
with the held-out test file removed. A Gitleaks 8.30.1 scan of the complete
downloaded artifact found zero secrets.

The original [Actions artifact](https://github.com/den-run-ai/askme/actions/runs/34812520762/artifacts/10336372214)
has 90-day retention. Its ZIP SHA-256 is
`0e03e8664d660917e90facbb1b36c23a8453a2959af9f0a7f963941a423f2029`.
This compact receipt preserves summaries and hashes; it does not republish the
raw archive. The earlier [v1 infrastructure failure](../2026-09-14-pi-comparison-v1/README.md)
remains separate and unchanged. A later runtime correction is not a reevaluation
of these frozen episodes.
