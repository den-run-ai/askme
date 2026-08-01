# pi harness ablation (exploratory — not a canary)

This directory holds a one-shot harness ablation: the same frozen FeatureBench
task, models, dated SiliconFlow endpoints, image digest, and wall-clock budget
as the v4 AskMe canaries, but executed by the
[pi coding agent](https://github.com/earendil-works/pi) instead of AskMe.
The design and pins are frozen in
[`pi-ablation-protocol.json`](pi-ablation-protocol.json).

**Why.** The v4 canaries produced two different empty-patch failures at the
harness boundary: Gemma 4 31B repeatedly attempted the implementation write and
was truncated at AskMe's 1536-token retry cap; Qwen3.6 27B never selected a
write action at all despite zero truncation pressure. This ablation asks the
narrow diagnostic question: *can each model produce a nonempty patch on this
task under a minimal free-form harness* (read/write/edit/bash tools, 8192
output tokens, no action-JSON protocol, no step budgets)?

**What it is not.** One attempt per cell, no controls rerun, no command guard,
no registered canary protocol version. Results are hypothesis-generating
contrasts with the v4 cells — not a FeatureBench score, not a harness
comparison result, not a model comparison.

## Mechanics

- `openrouter_pin_proxy.py` — host-side proxy. pi (in the container) talks to
  it via `host.docker.internal` using a per-run random secret; the proxy
  injects OpenRouter's strict `provider` block (`siliconflow`, no fallbacks,
  required parameters), restricts the request model to the cell's pinned ID,
  caps total calls, forwards with the real key, and tees every body for the
  post-run route audit. The real `OPENROUTER_API_KEY` never enters the
  container or any retained artifact.
- `run_pi_cell.sh gemma|qwen [smoke]` — single-shell cell runner: verifies the
  FeatureBench checkout, extracts the hash-verified problem statement from the
  pinned dataset snapshot, boots a fresh digest-pinned container, installs
  node `v22.23.2` + `@earendil-works/pi-coding-agent@0.83.0`, configures a
  custom `models.json` provider pointing at the proxy, runs one `pi -p`
  attempt under a 3540 s wall cap, extracts `git diff --cached --binary` as
  the patch, audits the route from the proxy tee, greps all artifacts for the
  real key, builds the prediction JSONL, and runs the official
  `fb eval --include-failed`. `smoke` mode uses a trivial prompt and skips the
  evaluator.
- Reasoning is disabled in both cells so the ablation varies the harness, not
  the reasoning policy.

Run from the repository root with Docker running and `.env` containing
`OPENROUTER_API_KEY`:

```sh
tests/featurebench/pi-ablation/run_pi_cell.sh gemma smoke   # cheap end-to-end check
tests/featurebench/pi-ablation/run_pi_cell.sh gemma         # one paid attempt + eval
tests/featurebench/pi-ablation/run_pi_cell.sh qwen
```

## Security boundary

Weaker than the canary adapter, deliberately and only for this pinned, trusted
task: pi's bash tool is unguarded inside the container, and the per-run proxy
secret is visible to model-issued commands (bounded by the proxy's
allowed-model pin and call cap). Do not reuse this setup on untrusted
repositories or tasks.

## Results (2026-08-01)

Both cells ran one masked attempt each (records in `results/`). Both models —
which produced empty patches under AskMe v4 — delivered clean-applying,
from-scratch implementations under pi. Neither fully resolved the task.

| Cell | AskMe v4 outcome | pi outcome | F2P | P2P | Cost | Agent time |
|---|---|---|---|---|---|---|
| Gemma 4 31B | empty patch (16/33 responses truncated) | applied, unresolved | 11/13 (84.6%) | 387/387 | $0.0056 | 83 s |
| Qwen3.6 27B | empty patch (0 writes in 27 steps) | applied, unresolved | 10/13 (76.9%) | 387/387 | $0.1916 | 16 min |

Interpretation within the claim boundary: on this one task, the AskMe action
interface — the 1536-token retry cap for Gemma, the action-selection stall for
Qwen — was the binding constraint, not model capability. Both routes were
fully pinned (every generation served by SiliconFlow as the exact dated
model). The first Gemma attempt was invalid (unmasked `/testbed`; see the
`masked-init-fix` amendment in the protocol) and is retained separately.

Two incidental findings worth noting: FeatureBench's official inference
runtime leaves the mask patch readable at `/tmp/mask.patch` inside the agent
container (this runner deletes it after applying), and SiliconFlow's qwen3.6
endpoint emitted 1,528 native reasoning tokens despite reasoning being
disabled in the request config.
