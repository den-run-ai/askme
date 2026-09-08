# One hosted Gemma MoE Requests repair, registered 2026-09-08

This is a separate, single-attempt hosted control, not a rerun of the retired
[Qwen macOS v1](../requests_pickle_v1/README.md). The prompt, gold patch and
21-check held-out acceptance script are byte-identical to v1. Selection is
deliberate and historical; training contamination is possible. No local task was
started because neither physical serving configuration passed its frozen gate.

The [protocol](protocol.json) pins merged runtime `6a212cf` and its two source
hashes, the Requests revision, one trial, 720-second outer deadline, seed, route,
budgets and acceptance rule. Hosted Gemma 4 26B-A4B is MoE; the local Gemma E4B
serving cell is dense PLE. Differences in model, route, quantization and output
budgets prohibit a causal comparison with the local runs.

Use only OpenRouter's official API, DeepInfra, fp8, required parameters and no
fallbacks. The canonical returned model alias is pinned; no claim is made about
verified hosted weight bytes. Catalog prices were frozen before inference at
$0.07/M prompt and $0.34/M completion tokens. Before every HTTP attempt the
runner reserves a conservative request-body-byte token estimate plus 16,384
overhead tokens and maximum output tokens. Missing usage retains the reservation;
missing cost, mismatched route/model or an exceeded estimate fail closed. The
$0.45 task cap plus both preceding preflight caps is below $0.50, within the
user's $10 allowance. This relies on unchanged prices, not a provider-side cap.

The existing `generic-feature-scale-v1` profile is used: 4096 step, 8192 write,
768 planner/validator and 96 task-replanner output tokens; default reasoning
floors are `(1024, 1536, 2048)`, unused with policy `off`. Runtime defaults for
guard thresholds and retry/request timeouts are frozen by the runtime hash and
recorded in resolved run configuration. The wrapper injects the seed and
credentials; it removes the API key from the worker environment before actions.
This is credential hygiene, **not a sandbox**. Model-generated shell commands
still have the launching user's host permissions; prompt network/install
policies and separate evaluator workspaces are not OS enforcement.

The successful [v2 serving gate](../../bench_records/2026-09-08-gemma-moe-hosted-qualification-v2/qualification.json)
is hash-pinned. Failed hosted preflight v1 is preserved separately: its expected
response ID mistakenly used the catalog's dated display name; the guard stopped
after one planner request. The corrected v2 preflight did not change the route.

Before the one task attempt, publish this protocol and qualify three fresh
workspaces: baseline and harmless non-empty controls must fail, gold must pass.
The model receives only the prompt and a clean upstream checkout, not gold or
held-out outputs. Capture the whole workspace and diff against its initial Git
commit. Apply that patch to another clean checkout and run all held-out checks
after the attempt; never send that evidence back as recovery context.

From the repository root, with a separate clean `6a212cf` harness worktree and
a local clone containing the pinned Requests revision:

```bash
uv run --locked python tests/external_repo_trial.py prepare \
  --source /path/to/requests-source --harness /path/to/clean-runtime \
  --output /path/to/new-record-directory \
  --task-dir tests/external_repo/requests_pickle_hosted_gemma_v2
# Only after controls pass, with OPENROUTER_API_KEY intentionally in the environment:
uv run --locked python tests/external_repo_trial.py run \
  --source /path/to/requests-source --harness /path/to/clean-runtime \
  --output /path/to/new-record-directory \
  --task-dir tests/external_repo/requests_pickle_hosted_gemma_v2
```

The exclusive attempt marker prevents overwriting or retrying an outcome.
Retain registration, controls, complete HTTP bodies/usage/cost ledger, JSONL,
stdout/stderr, any terminal result, patch, workspace and independent acceptance.
Report infrastructure validity, harness termination and accepted repair separately.
Even a passing patch resolves only this one predeclared bug; it is not evidence
of reliable real-world coding or of a causal harness benefit.
