# Hosted Gemma MoE Requests attempt — 2026-09-08

**Made applying edits but did not resolve.** The one registered attempt exhausted
its step/replan limits after 73.707 seconds, without a transport or outer timeout.
It added two reproduction scripts and never modified the production implementation.
The patch applies, but its root-level script violates the declared change scope;
fresh held-out acceptance also still reproduces the original unpickling TypeError.
The harness correctly reports `exhausted`, not completion.

## Frozen experiment

The [task protocol](../../external_repo/requests_pickle_hosted_gemma_v2/README.md)
was published at [`3635c09`](https://github.com/den-run-ai/askme/commit/3635c09)
before inference. The successful serving preflight, task registration and
baseline/no-op/gold control outputs were published at
[`2f7d656`](https://github.com/den-run-ai/askme/commit/2f7d656) before the task.
Baseline and harmless non-empty edits fail; gold passes all 21 checks.
The runtime is exact clean main `6a212cf`; its `askme.py` and `actions.py`
hashes match the earlier CI Qwen trial. Runtime bytes were not changed for
this attempt. The recorded runner is a separate evaluation adapter.

The selected model is **Gemma 4 26B-A4B MoE**, OpenRouter/DeepInfra, fp8 requested,
no fallbacks, required parameters and reasoning off. The top-level API response
model is `google/gemma-4-26b-a4b-it`; provider metadata supplies the dated
`google/gemma-4-26b-a4b-it-20260403` alias recorded by AskMe. Neither proves
hosted weight bytes. The local E4B cell is dense PLE, not this MoE.

The same historical Requests bug, prompt, upstream revision and independent
acceptance as Qwen v1 are retained. The hosted profile uses existing generic
4096/8192 step/write budgets, not Qwen v1's 512/1024 profile. Model, precision,
provider, hardware and budgets differ: this is not a causal comparison.
Historical task selection may overlap training data. One attempt cannot
estimate reliability or establish a benefit from the harness.

## Outcome and cost

| Dimension | Observed result |
|---|---|
| Process | Exit 0; no timeout; 73.706719 seconds |
| Agent | `exhausted`; 23 selected, 21 executed, 2 skipped steps |
| Patch | Applies; adds `repro_json_error_pickle.py` and `tests/repro_json_error_pickle.py` only |
| Production repair | None; `src/requests/exceptions.py` unchanged |
| Scope | Fails because of the added root-level script |
| Independent acceptance | Fails on the original pickle TypeError |
| Requests and tokens | 27 completed HTTP responses; 27,605 prompt + 3,130 completion tokens |
| Task API cost | $0.00299655, from all response `usage.cost` values |
| Entire hosted study | $0.00321032 including both preflights; below the $0.50 ceiling |

The serving gate did its job: inference returned promptly and valid native
actions reached execution. The task then stalled in reproduction/directory
inspection and repeated script writes. It never read the target implementation
or attempted its repair. This distinguishes the hosted task-level failure
from the original CI serving timeout, but does not isolate a model or harness
cause. No replacement trial or post-outcome budget increase was performed.

The failed [hosted preflight v1](../2026-09-08-gemma-moe-hosted-qualification/README.md)
cost $0.00004135; the separately registered, successful
[v2 preflight](../2026-09-08-gemma-moe-hosted-qualification-v2/README.md) cost
$0.00017242. V1 stopped because its registration incorrectly expected the
catalog's dated display name as the top-level API model identifier. Preserve
that setup failure; it was not a failed coding attempt or provider substitution.

## Evidence and reporting correction

[summary.json](summary.json) is the original unmodified runner output.
Its legacy `runner_cost` default incorrectly describes a public macOS CI runner
with no API charge. This task used the existing physical Mac for shell actions
and **paid remote inference**, whose actual charge is correctly recorded in
`api_cost_usd`. Its hardcoded conservative `usage_complete: false` is also
clarified by the [offline audit](offline-audit.json): all 27 HTTP attempts have
complete usage. These are reporting clarifications, not an amended task score.
The current reusable runner corrects the hosted cost description under a
deterministic regression; its frozen copy here remains unchanged.

The complete [HTTP ledger](http.jsonl), [agent JSONL](agent.jsonl),
[structured result](agent-result.json), [patch](candidate.patch),
[whole workspace](workspace.tar.gz), stdout/stderr, control outputs,
registration and frozen runner/evaluator are retained. The offline audit
checks the whole archived workspace and independently reapplies/scores its
patch. Account balance observations are supplemental snapshots; response
costs are the study's accounting basis. No API credential was retained.
The [later account check](account-cost-check-later.json) reconciles exactly:
remaining balance fell from $9.50080671 to $9.49759639, a $0.00321032 change.
The earlier, not-yet-updated balance snapshot is retained too.
The candidate patch preserves model-generated trailing whitespace, and
`patch-apply.txt` retains Git's corresponding warnings. It applied successfully;
raw evidence was not reformatted to silence whitespace diagnostics.

AskMe is not a sandbox. The API key was removed from the worker environment
before actions; that is hygiene, not protection against adversarial host access.
Held-out checks were external and run only after the agent stopped. Their
outputs were never returned to it as recovery context.
