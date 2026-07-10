# Draft OpenRouter Smoke Eval

This is a limited integration smoke for the Berkeley talk, not a leaderboard or reliability estimate.

## Protocol

**Date:** 2026-07-10  
**NanAgent implementation commit:** `04033b4750b4b4f0d2f31697dd2f65841307f870`
**Provider:** `siliconflow`, strict routing (`allow_fallbacks=false`, `require_parameters=true`)
**Endpoint precision:** FP8, as reported by OpenRouter endpoint metadata on the run date  
**Trials:** one per model/task cell

Models:

- `google/gemma-4-26b-a4b-it`
- `qwen/qwen3.6-27b`
- `qwen/qwen3.6-35b-a3b`

Tasks:

| Task | Harness selector | Independent postcondition |
|---|---|---|
| Multi-file C build | `hard / test_replan_build_with_dependency` | Generated `main` exists, exits zero, and prints `REPLAN_OK` |
| Repair Python syntax | `medium / test_fix_python_syntax_error` | `python3 greet.py` exits zero and prints `hello` |

Pytest disables NanAgent's LLM final-validator fixture during these integration tests. The independent executable checks above are therefore the authority for pass/fail. A reported pass requires both pytest success and `agent_complete`.

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

## Predeclared Repeat Rule

- Retain and report semantic failures; do not replace them with a better run.
- Repeat once only for an infrastructure failure before an LLM response, such as authentication, routing, or provider outage.
- Keep the failed attempt in the audit note. Zero-token authentication failures are not model results.

## Reporting

For every cell, publish:

- pytest pass and `agent_complete` separately
- deterministic postcondition result
- wall time, steps, replans, thinking retries, and LLM calls
- prompt/completion tokens and OpenRouter billed credits
- requested model/provider plus selected endpoint metadata
- git commit and dirty state captured before the run

Latency is provider/network dependent. The three model families and architectures differ, so this is a systems compatibility smoke, not a controlled scaling study. No fresh local Mac runs are part of this draft.

## Current Status

The first Gemma build attempt stopped before any model response or billing: OpenRouter returned `401 User not found` for the key in `.env`. The authenticated endpoints returned the same status. No model result is recorded until an active key is supplied.
