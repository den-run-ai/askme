# Automatic Berkeley smoke — 2026-09-08

Three cells failed pytest and one passed. These are retained automatic hosted
smoke outcomes from main `461fdf01e8f644cc5dddad2eee77008c11447448`, not a new
registered capability experiment or a formal Berkeley benchmark score.
No model call or task re-evaluation was made while preparing this bundle.

[Job 101993600130](https://github.com/den-run-ai/askme/actions/runs/34205396477/job/101993600130)
returned success because the execution revision's
[push-event gate](https://github.com/den-run-ai/askme/blob/461fdf01e8f644cc5dddad2eee77008c11447448/.github/workflows/llm.yml#L280)
treats valid negative cell outcomes as advisory. That is not four passing tasks.
The separate easy smoke failed; see the [parent bundle](../README.md).

| Requested model | Test | Agent status | Pytest | Recorded response cost, USD |
| --- | --- | --- | --- | --- |
| `google/gemma-4-26b-a4b-it` | `test_replan_build_with_dependency` | exhausted | failed | 0.00835280 |
| `google/gemma-4-26b-a4b-it` | `test_fix_python_syntax_error` | exhausted | failed | 0.00342619 |
| `qwen/qwen3.6-27b` | `test_replan_build_with_dependency` | complete | failed | 0.00319758 |
| `qwen/qwen3.6-27b` | `test_fix_python_syntax_error` | complete | passed | 0.004377375 |

The Qwen build failure was specifically an absent executable `main`: source,
header and agent-status assertions had passed before pytest checked for the
executable. Its retained
[diagnostic tail](original/qwen-qwen3-6-27b-qwen-qwen3-6-27b-20260422/build/test_replan_build_with_dependency_trial1_pytest.txt)
does not turn the completion claim into acceptance. No final workspace is
available to reconstruct or audit additional files. The Qwen syntax case's
single pytest pass supports only that narrow hosted smoke outcome.

## Identity, configuration, and accounting

All 72 Gemma response records report the expected served model
`google/gemma-4-26b-a4b-it-20260403`; all 23 Qwen response records report
`qwen/qwen3.6-27b-20260422`. The original requested aliases and expected served
identities remain distinct in [summary.json](summary.json) and the originals.
All four original summaries report valid route/contract evidence, complete
usage, no log parse error, and no trial timeout.

Providers were automatic and unpinned. Recorded provider sets differ: Gemma
build used Cloudflare, DeepInfra, Google, Parasail, SiliconFlow and Venice;
Gemma syntax used Cloudflare, DeepInfra, NextBit and Parasail; Qwen build used
Chutes, Phala and SiliconFlow; Qwen syntax used Chutes, SiliconFlow and Venice.
The logged effective configuration disabled provider fallbacks and required
provider parameter support. Original summary request overrides are null;
they are not the effective values in `run_start`.

The cells used native tools, `generic-feature-scale-v1`, gated reasoning,
heuristic step policy, compile repair enabled and final LLM validation disabled.
The build and syntax tasks used different plan/task limits; each model/task
cell has its own recorded configuration hash. Full effective settings and
per-provider response counts are indexed in the derived summary. This is not
a matched-provider/configuration causal comparison or local-model measurement.

The 95 `tokens` records total 96,810 prompt tokens and 15,300 completion tokens
(112,110 total), with **$0.019353945** in response-cost telemetry. Costs were
summed using `decimal.Decimal` over the original JSON numeric text, not the
rounded CI table. The original Qwen build summary contains a binary-float
rounding tail; it is preserved unchanged. These are observed API costs, not
reconciled account charges or runner billing.

## Retention and limits

[manifest.json](manifest.json) records the downloaded ZIP's digest, every
retained member's length and SHA-256, and relevant source-file hashes at the
execution revision. All 11 original members (108,228 bytes) are byte-identical
to the downloaded artifact: four summaries, four JSONL logs and three bounded
pytest diagnostic tails. Four summaries retain their original absent final
newline. The successful syntax cell had no diagnostic text member.

The ZIP itself, signed download URL, raw workflow job log and credentials are
not retained here. The artifact does not contain full HTTP payloads, full write
contents or final workspaces; some logged action arguments are abbreviated.
No missing content has been reconstructed. Original summary replan counters
and terminal `run_end.replans` differ in the exhausted Gemma cells; both are
preserved rather than silently harmonized. Secret scanning found no matches
in the original members, but cannot prove the absence of sensitive data.

The summary is a retrospective index of these exact records, not a re-score,
reliability estimate, net-time-savings result, or proof of a causal harness
benefit. No budget, historical record or failed outcome was changed.
