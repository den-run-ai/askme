# AskMe–pi v1: infrastructure qualification failed

On September 14, [the preregistered four-cell run](https://github.com/den-run-ai/askme/actions/runs/34811259408)
stopped after each cell's first hosted completion because the proxy could not
complete its generation-metadata audit. **This produced no usable task scores
and no comparison of model or harness capability.** All four original outcomes
remain failures of the evaluation infrastructure.

| Cell | Upstream completions | Observed work before halt | Patch | Task score |
|---|---|---|---|---|
| Gemma 4 31B / AskMe | 1, HTTP 200 | Plan received | Empty | N/A |
| Gemma 4 31B / pi | 1, HTTP 200 | Read-only directory listing | Empty | N/A |
| Qwen3.6-27B / pi | 1, HTTP 200 | Read-only file listing | Empty | N/A |
| Qwen3.6-27B / AskMe | 1, HTTP 200 | Plan received | Empty | N/A |

Every completion had valid response telemetry, but its metadata lookup raised
`ValueError` before a `generation_audit` record was saved. The proxy then rejected
further requests locally, without forwarding another paid completion. Pi's
automatic retries and AskMe's replanning therefore did not create additional
hosted calls. The first responses had already reached the agents: this was not
a no-inference run, and pi's read-only listing actions did execute.

The original code discarded the metadata HTTP status and detailed failure.
The retained artifact alone cannot distinguish temporary indexing delay, an
API error, or malformed metadata. It does not establish a dated-model identity
mismatch as the failure cause. Any later diagnostic belongs in a separate,
dated record and cannot make these original episodes valid.

The official evaluator completed its empty-patch path for each cell without
running target tests; its raw `pass_rate: 0.0` is **not a measured 0/13 result**.
Before inference, the independent controls qualified: gold resolved with 13/13
target tests and 387/387 preservation tests; the harmless nonempty patch applied
but remained unresolved at 0/13 and 387/387 respectively.

## Cost and retained evidence

The four responses reported **$0.00299138**, 13,364 prompt tokens, and 148
completion tokens. This is response telemetry, not an independently reconciled
account charge. The original ledger's `actual_usd: "0"` means no authoritative
generation-cost record settled; it must not be reported as free inference.
The proxy retained **$0.05043320** in conservative reservations after the audit
failures. That reservation is a budget allowance, not a charge, and remains
accounted for against the original $10 budget until reconciliation.

The [audit](audit.json) records the immutable protocol hash, workflow revision,
runtime revision, individual outcomes, source hashes, and cost distinctions.
The original [Actions artifact](https://github.com/den-run-ai/askme/actions/runs/34811259408/artifacts/10334658720)
has SHA-256 `efd861c64985d22897d4e5875f237207a151c98ba26c14923de411c0b593c526`
and a 90-day retention period. The compact receipts here remain available
after that artifact expires; raw trajectories have not been republished here.
All 99 files in the [original inventory](artifact-sha256.json), totaling 988,988
bytes, matched their retained SHA-256 hashes. A separate Gitleaks 8.30.1 scan
of the downloaded artifact found zero secrets.

All four workspace preflights recorded the same masked tree. The held-out test
file was removed; the original mask and gold evidence stayed outside inference
container mounts. The inspected trajectories show no held-out test or reference
patch contents. The proxy retains request hashes rather than complete request
bodies, so this audit does not claim a wire-level replay of every prompt.

At 06:06 UTC, a separate [availability-only diagnostic](https://github.com/den-run-ai/askme/actions/runs/34812086810)
made zero new model calls and checked only the four existing metadata endpoints.
All returned HTTP 200 with a metadata object present. It retained no returned
billing, provider or model values and uploaded no metadata artifact. This is
consistent with earlier temporary unavailability, but the original failure
status remains unknown and the account charge remains unreconciled.

V1 remains immutable and has no replacement attempts. A separately preregistered
v2 may test all four cells after a deterministic infrastructure repair, using
the remaining original budget and retaining every result. V1 cannot support a
ranking, a reliability estimate, or a conclusion that either harness failed the
coding task.
