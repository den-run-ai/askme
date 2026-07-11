# OpenRouter Smoke Eval — Measured Results

This is a limited integration smoke for the Berkeley talk, not a leaderboard or reliability estimate.

## Protocol

**Date:** 2026-07-10
**AskMe implementation commit:** `04033b4750b4b4f0d2f31697dd2f65841307f870`
**Run HEAD:** `88442e88b91d846fb48bcd6b4d43bd4c055252bc`, clean before every cell
**Provider:** `siliconflow`, strict routing (`allow_fallbacks=false`, `require_parameters=true`)
**Endpoint precision evidence:** authenticated catalog match; details below
**Trials:** one per model/task cell

Original matrix models:

- `google/gemma-4-26b-a4b-it`
- `qwen/qwen3.6-27b`
- `qwen/qwen3.6-35b-a3b`

### Predeclared Gemma 4 31B Follow-up

Declared at `2026-07-10T20:36:49Z`, after the original six outcomes were frozen and before any 31B model response. The follow-up adds `google/gemma-4-31b-it` to the same build and repair tasks with one trial per cell, the same AskMe implementation, and the same strict SiliconFlow routing policy. The original six cells will not be rerun or replaced.

The follow-up was originally motivated as a descriptive size-and-architecture contrast. The design cannot support that inference: model selection was post-hoc, architecture and active compute also changed, and every cell has one unseeded run. The current talk treats these cells only as additional trajectory receipts.

Tasks:

| Task | Harness selector | Independent acceptance check |
|---|---|---|
| Multi-file C build | `hard / test_replan_build_with_dependency` | Generated `main` exists, exits zero, and prints `REPLAN_OK` |
| Repair Python syntax | `medium / test_fix_python_syntax_error` | `python3 greet.py` exits zero and prints `hello` |

Pytest disables AskMe's LLM final-validator fixture during these integration tests. A reported pass requires pytest success, `agent_complete`, and a separately repeated deterministic acceptance check.

The no-think path explicitly sends `reasoning.enabled=false`, which prevents Qwen's default reasoning mode from changing the policy. The existing retry ladder may enable medium/high reasoning after malformed JSON or semantic failure.

## Commands

Run both commands for each model, changing `MODEL` and `SLUG` together:

```bash
MODEL=google/gemma-4-26b-a4b-it
SLUG=gemma4-26b

python3 tests/bench_harness.py --backend openrouter --suite hard \
  --test test_replan_build_with_dependency --trials 1 \
  --model "$MODEL" --provider siliconflow \
  --log-dir "/tmp/berkeley-eval-20260710/$SLUG/build"

python3 tests/bench_harness.py --backend openrouter --suite medium \
  --test test_fix_python_syntax_error --trials 1 \
  --model "$MODEL" --provider siliconflow \
  --log-dir "/tmp/berkeley-eval-20260710/$SLUG/repair"
```

Then repeat with:

| `MODEL` | `SLUG` |
|---|---|
| `qwen/qwen3.6-27b` | `qwen36-27b` |
| `qwen/qwen3.6-35b-a3b` | `qwen36-35b-a3b` |
| `google/gemma-4-31b-it` | `gemma4-31b` |

## Predeclared Repeat Rule

- Retain and report semantic failures; do not replace them with a better run.
- Repeat once only for an infrastructure failure before an LLM response, such as authentication, routing, or provider outage.
- Keep the failed attempt in the audit note. Zero-token authentication failures are not model results.

## Reporting

For every cell, publish:

- pytest pass and `agent_complete` separately
- independent acceptance result
- wall time, steps, replans, thinking retry responses, and usage-bearing responses
- prompt/completion tokens and OpenRouter billed credits
- requested model/provider plus observed served model/provider and catalog endpoint metadata
- git commit and dirty state captured before the run

“Usage-bearing responses” means JSONL `tokens` events. It is not a raw count of chat-completions HTTP attempts, which this runner did not instrument. A response can still have caused a JSON or semantic retry. Billed credits are the sum of response `usage.cost` values.

## Measured Outcomes

`P/A/X` means pytest, agent completion, and the independently repeated acceptance check.

| Model | Build (P/A/X) | Repair (P/A/X) | Usage responses | Billed credits |
|---|---:|---:|---:|---:|
| Gemma 4 26B A4B | **PASS** (✓/✓/✓; 603.64s) | **PASS** (✓/✓/✓; 20.00s) | 39 | $0.00414692 |
| Gemma 4 31B | **PASS** (✓/✓/✓; 66.50s) | **PASS** (✓/✓/✓; 22.36s) | 19 | $0.00132133 |
| Qwen3.6-27B | **PASS** (✓/✓/✓; 47.88s) | **PASS** (✓/✓/✓; 23.04s) | 22 | $0.00496840 |
| Qwen3.6-35B-A3B | **FAIL** (✗/✓/✗; 17.67s) | **PASS** (✓/✓/✓; 11.84s) | 14 | $0.00187520 |

Seven of eight cells passed all three criteria. All eight agent runs reported `complete`. In the failed build, Qwen3.6-35B-A3B created correct `main.c` and `msg.h`, compiled and ran `/tmp/test`, and reported completion, but it did not leave the required `main` executable. The semantic failure was retained under the predeclared rule.

## Per-Cell Metrics

| Cell | Wall | Steps (failed) | Full replans | Local replans | Thinking retry responses | Usage responses | Prompt / completion tokens | Credits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Gemma 26B A4B build | 603.64s | 19 (1) | 1 | 1 | 1 | 29 | 15,731 / 3,778 | $0.00339892 |
| Gemma 26B A4B repair | 20.00s | 7 (1) | 0 | 0 | 0 | 10 | 3,780 / 736 | $0.00074800 |
| Gemma 31B build | 66.50s | 4 (0) | 0 | 0 | 0 | 8 | 3,976 / 375 | $0.00066688 |
| Gemma 31B repair | 22.36s | 5 (1) | 0 | 1 | 0 | 11 | 3,825 / 393 | $0.00065445 |
| Qwen 27B build | 47.88s | 6 (0) | 0 | 0 | 0 | 11 | 4,880 / 397 | $0.00273440 |
| Qwen 27B repair | 23.04s | 6 (0) | 0 | 0 | 0 | 11 | 3,756 / 346 | $0.00223400 |
| Qwen 35B-A3B build | 17.67s | 3 (0) | 0 | 0 | 0 | 7 | 3,187 / 304 | $0.00112380 |
| Qwen 35B-A3B repair | 11.84s | 4 (0) | 0 | 0 | 0 | 7 | 2,301 / 182 | $0.00075140 |
| **Total** | **812.93s** | **54 (3)** | **1** | **2** | **1** | **94** | **41,436 / 6,511** | **$0.01231185** |

The original six-cell response cost exactly matched its API key usage delta: $0.01099052. The 31B extension added $0.00132133 across 19 usage-bearing responses and also reconciled exactly; the combined key-usage delta was $0.01231185. Raw chat-completions HTTP attempts remain `null`; they are not inferred from the 94 token events.

## Descriptive Gemma Follow-up — No Size Inference

Both Gemma variants passed both observed tasks. Their trajectories differed:

| Task | Gemma 4 26B A4B | Gemma 4 31B | Descriptive difference |
|---|---:|---:|---|
| Build | 603.64s · 29 responses · 19,509 tokens · $0.00339892 | 66.50s · 8 responses · 4,351 tokens · $0.00066688 | 31B was 9.1x shorter, with 21 fewer responses and 77.7% fewer tokens. |
| Repair | 20.00s · 10 responses · 4,516 tokens · $0.00074800 | 22.36s · 11 responses · 4,218 tokens · $0.00065445 | 31B was 2.36s slower and used one more response, but 298 fewer tokens. |

These numbers are retained as descriptive provenance, not as a talk result or an estimate of model-size impact. Gemma 4 31B is dense with 30.7B parameters; 26B A4B is MoE with 25.2B total and 3.8B active per token. The added model was chosen after the first six outcomes, each cell has one unseeded trial, the runs occurred at different provider-load times, and the models took different trajectories. The build difference and the slightly reversed repair timing demonstrate why this design cannot support a size or architecture conclusion.

## Endpoint and Git Provenance

Every request specified provider order `siliconflow`, `allow_fallbacks=false`, and `require_parameters=true`. The response logs reported the expected dated model ID and `SiliconFlow` for every usage-bearing response.

The request did not include a quantization filter. At `2026-07-10T16:04:30Z`, an authenticated `GET /api/v1/models/{model_id}/endpoints` snapshot contained exactly one SiliconFlow match for each requested model, and each match reported `quantization=fp8` and `tag=siliconflow/fp8`:

| Requested model | Observed model / unique SiliconFlow endpoint |
|---|---|
| `google/gemma-4-26b-a4b-it` | `google/gemma-4-26b-a4b-it-20260403` |
| `qwen/qwen3.6-27b` | `qwen/qwen3.6-27b-20260422` |
| `qwen/qwen3.6-35b-a3b` | `qwen/qwen3.6-35b-a3b-20260415` |

A separate snapshot at `2026-07-10T20:37:08Z`, verified unchanged after the extension, contained one SiliconFlow match for `google/gemma-4-31b-it`: `google/gemma-4-31b-it-20260402`, also tagged `siliconflow/fp8` with `quantization=fp8`.

FP8 is therefore a time-stamped catalog attribution matched by model and provider, not a direct runtime precision measurement. Full endpoint names, response hashes, per-cell routes, metrics, and acceptance observations are in [`draft-results.json`](draft-results.json).

The harness recorded HEAD `88442e88b91d846fb48bcd6b4d43bd4c055252bc` and `git_dirty=false` before every cell. The AskMe implementation at that HEAD is commit `04033b4750b4b4f0d2f31697dd2f65841307f870`; the later commit only pinned this protocol.

## Infrastructure Audit

Before the recorded matrix, an earlier setup attempt at commit `b9b92b6a2ff8a4d83b36b03ee6819804b083574d` used `easy / test_multi_step_build` with Gemma. The old key returned `401 User not found` twice before any model response; the authenticated endpoint lookup returned the same status. The attempt produced zero token events and zero billed credits.

The first 31B build invocation also stopped before test collection or an API call because the detached worktree resolved bare `python3` to a Python 3.14 environment without pytest. The permitted infrastructure repeat used the established Miniforge interpreter and a fresh log directory. Both events are retained in [`draft-results.json`](draft-results.json), excluded from every result total, and are not measured cells.

Latency is provider/network dependent. The model families and architectures differ, so this is a systems compatibility smoke, not a controlled scaling study. No fresh local Mac LLM runs are part of these results.
