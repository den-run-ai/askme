# Automatic main-workflow evidence — 2026-09-08

This bundle preserves an **automatic post-merge hosted workflow**, not a new
registered capability experiment, matched-provider comparison, or local
performance measurement. No model call was made to prepare this audit. Negative
results remain negative; no test assertion or budget was changed.

Source: [workflow run 34205396477](https://github.com/den-run-ai/askme/actions/runs/34205396477),
main revision `461fdf01e8f644cc5dddad2eee77008c11447448`.
Its Git tree, `dbb7a80ef2c3a73ad6d0f0b698b2a4515ec40abb`, is identical to the
reviewed cleanup tip `d2b9c68054f5ebddcdcfa5de82e3dba40d29934b`.
That tip is post-extraction, not an independent pre-extraction control.

## Easy hosted smoke: one failed, two passed

[Job 101993600037](https://github.com/den-run-ai/askme/actions/runs/34205396477/job/101993600037)
reported **1 failed, 2 passed, 26 deselected in 76.88 seconds**. The
[test source is pinned to the execution revision](https://github.com/den-run-ai/askme/blob/461fdf01e8f644cc5dddad2eee77008c11447448/tests/test_agent_integration.py#L255).
The original [JSONL](smoke-google-gemma-4-26b-a4b-it.jsonl) contains all three
runs; [summary.json](summary.json) contains retrospective counts and limitations.

| Case | Pytest / agent status | Agent wall time | Selected / executed / skipped | Recorded responses | API-cost sum, USD |
| --- | --- | --- | --- | --- | --- |
| Create and read `hello.txt` | failed / exhausted | 31.75 s | 10 / 5 / 5 | 14 | 0.001700724 |
| Shell and write `os.txt` | passed / complete | 9.43 s | 5 / 3 / 1 | 6 | 0.000680640 |
| Compile and run `main.c` | passed / complete | 35.53 s | 18 / 10 / 5 | 20 | 0.002351816 |

Controller `done` selections explain why the passing cases' selected counts
exceed executed plus skipped counts. Individual agent wall times do not include
all pytest overhead.

### What failed

The first case never finished its first task in either of two plan attempts.
Its ten selected steps were three successful writes, two successful shell
actions, and five duplicate-write skips. The retained shell-command prefixes
show directory listing/setup, not file-content verification. No `read` or `done`
was selected. Both task-local replans were rejected as `passive_downgrade`; each
executor attempt used its five-step budget.

The terminal record is `exhausted` with `errors: []`. This is consistent with
successful individual actions but no completed task: the
[bounded attempt loop](https://github.com/den-run-ai/askme/blob/461fdf01e8f644cc5dddad2eee77008c11447448/loop.py#L2016)
does not invent an action error when its budget ends, and
[exhaustion remains terminal](https://github.com/den-run-ai/askme/blob/461fdf01e8f644cc5dddad2eee77008c11447448/policies.py#L1423).
The failed pytest assertion checks completion **before** checking `hello.txt`
content; that subsequent content assertion was not reached.

All 14 recorded responses in this case were first-attempt responses: four
`stop`, ten `tool_calls`. No HTTP, timeout, parsing, retry, or action error is
observed in the retained events and inspected job log. The native retry-budget
correction was not exercised. The evidence establishes bounded noncompletion,
not a demonstrated runtime regression or infrastructure failure. It does not
identify why the model chose that sequence or prove the absence of other bugs.

### Configuration, routing, and accounting

All three runs logged configuration hash `aaecbb735bf27043`: native tools,
`generic-feature-scale-v1`, heuristic step policy, gated reasoning, final
validation disabled, two plan attempts, at most three tasks, and five steps per
task attempt. Token caps were planner 768, executor 4096, write retry 8192, and
task-local replan 96. Full configuration is retained in the JSONL and summary.

The requested model was `google/gemma-4-26b-a4b-it`; all 40 responses reported
`google/gemma-4-26b-a4b-it-20260403`. The provider was **unpinned**, with fallbacks
enabled. Actual response providers were Cloudflare 33, NextBit 2, Darkbloom 2,
DeepInfra 1, Parasail 1, and Google 1. The failed case alone used Cloudflare 10,
NextBit 2, DeepInfra 1, and Darkbloom 1. The source's old Parasail/bf16 section
comment does not establish the route or quantization of this run.

All 40 recorded responses have usage and cost telemetry: 35,792 prompt tokens,
3,632 completion tokens, and an exact decimal sum of **$0.004733180** from
`tokens.openrouter_cost`. This is observed API telemetry, not a reconciled
account-balance charge. A single unpinned-provider run cannot establish a
stochastic reliability estimate, provider effect, or causal refactor effect.

### Original artifact and hashes

[Artifact 10047577233](https://api.github.com/repos/den-run-ai/askme/actions/artifacts/10047577233),
`openrouter-smoke-logs`, was created at `2026-09-08T08:37:53Z`; its declared
expiration was `2026-09-22T08:37:52Z`. The downloaded ZIP contains one member.

- Downloaded ZIP: 4,080 bytes; SHA-256 `e78ecf5036da8dd9e114ea78b03b63029e5d14656c18f701ef68843be3f0a23c`.
- Retained `smoke-google-gemma-4-26b-a4b-it.jsonl`: 41,354 original bytes, 132 JSONL records; SHA-256 `0c8974c1593041aee290441ca83a2f3c94aa51fe23fce8304d2233f90c0cf513`.

The ZIP, raw job log, credentials, cookies, and workflow headers are not copied
into this bundle. The original JSONL member is byte-for-byte preserved. There
are no full HTTP payloads, write contents, or final workspace in that artifact;
some action arguments are prefix-truncated. An exact response replay or final
workspace/content audit is therefore unavailable. No missing payload was
reconstructed.

## Separate Berkeley advisory job: three failed cells, one passed

[Job 101993600130](https://github.com/den-run-ai/askme/actions/runs/34205396477/job/101993600130)
ran four additional hosted cells. Its [separate evidence](berkeley/README.md),
[derived summary](berkeley/summary.json), and [artifact manifest](berkeley/manifest.json)
retain these outcomes independently of the easy smoke above. These automatic
advisory checks are not a formal Berkeley benchmark or a registered comparison.

| Model family | Test | Agent status | Pytest result | Raw-event API-cost sum, USD |
| --- | --- | --- | --- | --- |
| Gemma 26B-A4B | `test_replan_build_with_dependency` | exhausted | failed | 0.008352800 |
| Gemma 26B-A4B | `test_fix_python_syntax_error` | exhausted | failed | 0.003426190 |
| Qwen 3.6-27B | `test_replan_build_with_dependency` | complete | failed | 0.003197580 |
| Qwen 3.6-27B | `test_fix_python_syntax_error` | complete | passed | 0.004377375 |

In particular, the Qwen build cell's agent-reported completion did not make its
pytest acceptance pass: the job log reports that the expected executable `main`
was missing. Do not combine agent completion with external success.
The 95 retained response records sum to **$0.019353945**, with 96,810 prompt and
15,300 completion tokens. This exact decimal sum differs from the rounded CI
display total of $0.01936; original summaries, including floating-point tails,
are preserved rather than rewritten.

These cells requested `google/gemma-4-26b-a4b-it` or `qwen/qwen3.6-27b`; all
recorded served IDs matched their declared dated variants. Providers were
automatic/unpinned, but fallbacks were disabled here, unlike the easy smoke.

Across these two jobs only, the 135 retained response-cost records sum to
**$0.024087125**. This combines observed API telemetry, not reconciled account
charges, runner charges, or an estimate for other workflow runs. The easy-job
`summary.json` remains scoped only to its three cases; Berkeley data are separate.

## Deterministic characterization

Existing tests cover successful-shell exhaustion without false completion
(`test_exhaustion_never_reconciles_to_complete`) and frozen duplicate
write/observation-stall transcripts. The added
[`test_exhaustion_retains_empty_errors_after_success_and_duplicate_skips`](https://github.com/den-run-ai/askme/blob/35b04f4/tests/test_state_access.py)
uses a synthetic successful write and two duplicate skips, then asserts
`exhausted`, an empty error list, and the selected/executed/skipped counters.
This characterizes controller behavior without weakening the smoke gate; it
does not replay the missing hosted responses or demonstrate a model fix.
