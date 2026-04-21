# Performance

Benchmark history and test-run matrices for NanAgent. Each entry is a point-in-time measurement against a specific build + model + config. Kept for comparison across changes, not as a current-truth reference.

**Staleness policy.** Each section is dated. If an entry is more than ~6 months old and the code that produced it has changed materially, treat it as historical only — re-run before citing numbers. Sections marked `[stale]` have known divergence from current code.

For architecture decisions and current constraints see [ARCHITECTURE.md](ARCHITECTURE.md). For model/server config see [gemma4-setup.md](gemma4-setup.md).

## Model Comparison

Empirical behavior across the three models used during development.

| Model | Architecture | "done" emission | Action looping | Speed | Test status |
|---|---|---|---|---|---|
| **Gemma 4 E4B** (local) | MoE 12B/4B active | Works after cross-task state fix | Duplicate write loops; write content truncation mitigated by `edit` | ~7 tok/s | 9/9 integration tests pass (easy 3:20, medium 20:20, hard 16:49) |
| **Gemma 4 26B-A4B** (OpenRouter) | MoE 26B/4B active | Reliable | Occasional write loops (up to 3x before done); prefers `edit` for fixes | ~1-2s/step | 9/9 integration tests pass (easy 38s, medium 87s, hard 176s) |
| Qwen 3.5 9B | Dense | Unreliable (think-tag issues) | N/A | ~3 tok/s | Legacy, no longer tested |

### Gemma 4 vs Qwen 3.5 — why we switched

Qwen 3.5 has always-on thinking (`<think>` blocks) that caused major reliability issues:
- With `reasoning_format: "deepseek"` (default), thinking goes to `reasoning_content` — but if `max_tokens` is exhausted during thinking, `content` stays empty.
- With `reasoning_format: "none"`, thinking leaks into `content` as literal `<think>` text.
- Required extensive workarounds: think-tag stripping, JSON extraction, higher `max_tokens`, retries.

Gemma 4 E4B has opt-in thinking (not always-on):
- No `reasoning_format` parameter needed.
- Think-tag stripping kept as safety net but rarely triggers.
- More reliable JSON output with lower `max_tokens`.
- MoE architecture (4B active) is faster than dense 9B in practice.

## Local Integration Test Results — 2026-04-07, build `941146b3f`

First full pass after thinking-on-retry + duplicate guard + cross-task state fix.

**Easy: 3/3 pass (3:20 total)**

| Test | Tasks | Steps | Thinking | Dup Guard | Time |
|------|-------|-------|----------|-----------|------|
| create_and_read_file | 2 | 4 (t1:3, t2:1) | 0 | 0 | 54s |
| shell_and_write | 2 | 4 (t1:3, t2:1) | 0 | 0 | 60s |
| multi_step_build | 3 | 6 (t1:2, t2:3, t3:1) | 0 | 1x write | 87s |

- Done emission works reliably (was broken due to empty state bug, not model limitation).
- Duplicate guard fired once (multi_step_build, same write loop seen on 26B).
- ~10x slower than OpenRouter 26B (~10s/step vs <1s).

**Medium: 3/3 pass (20:20 total)**

| Test | Replans | Tasks | Steps | Thinking | Dup Guard | Time |
|------|---------|-------|-------|----------|-----------|------|
| fix_python_syntax | 0 | 3 | 9 | 3x (med+high+med) | 0 | ~520s |
| fix_missing_include | 1 | 3+3 | 3+5 | 3x + 0 | 2x (write+shell) | ~660s |
| create_missing_file | 0 | 2 | 4 | 0 | 0 | ~37s |

Key observations:
- **fix_python_syntax**: 370s wasted on task 2 ("Correct the syntax error") which was already fixed by task 1. Model couldn't produce `{"action":"done"}` — generated verbose reasoning text that exhausted all 3 retry budgets (256→512→768). Only the final thinking=high retry succeeded.
- **fix_missing_include**: Path truncation root cause — model tried to reproduce long temp paths (`/private/var/folders/...`) in shell commands, exhausting max_tokens before closing JSON. After replan, simpler task descriptions let the model use relative paths. Dominated by thinking retries on truncated paths.
- **create_missing_file**: Clean, no error recovery needed.
- Duplicate guard saved fix_missing_include from infinite loops (auto-fail on same shell failing twice → triggered replan).

## Local Integration Test Results — 2026-04-07, build `0d049d6a9` (post Phase 1 update)

Build updated from `941146b3f` → `0d049d6a9` (18 commits). Includes Gemma 4 byte token fix (#21488) and checkpoint restore fix (#21510).

**Easy: 3/3 pass (2:12 total) — ~35% faster than pre-update**

| Test | Tasks | Steps | Thinking | Dup Guard | Time |
|------|-------|-------|----------|-----------|------|
| create_and_read_file | 2 | 5 (t1:3, t2:1) | 0 | 0 | ~41s |
| shell_and_write | 2 | 4 (t1:3, t2:1) | 0 | 0 | ~36s |
| multi_step_build | 3 | 6 (t1:2, t2:3, t3:1) | 0 | 1x write | ~55s |

**Medium: 3/3 pass (39:08 total)**

| Test | Replans | Tasks | Steps | Thinking Retries | Time |
|------|---------|-------|-------|------------------|------|
| fix_python_syntax | 0 | 3 | 10 | 5x | ~19min |
| fix_missing_include | 1 | 3+3 | 5+4 | 4x | ~19min |
| create_missing_file | 0 | 2 | 4 | 0 | ~15s |

Key observations:
- Easy tests ~35% faster — likely byte token fix (#21488) improving JSON output, fewer parse retries.
- Medium tests slower overall — fix_python_syntax 19min vs 8.7min, more JSON parse retries. The `</s>` EOS fix (#21492, not yet merged at time of test) may help.
- `--cache-reuse` confirmed broken — server now explicitly logs `cache_reuse is not supported by this context`. Manual slot save/restore workaround tested and **counterproductive** — same iSWA bug affects restore, making requests ~40% slower.
- Checkpoint restore verified — `TestServerConfig::test_slot_restore` passes.

**Hard: 3/3 pass (16:49 total)**

| Test | Replans | Tasks | Steps | Thinking | Dup Guard | Time |
|------|---------|-------|-------|----------|-----------|------|
| build_with_dependency | 1 | 3+2 | 8+2+2 | 5x (med+med+high+high+0) | 2x (write auto-done) | ~11min |
| fix_wrong_command | 0 | 3 | 4+3+2 | 2x (med+med) | 0 | ~4.5min |
| multi_step_recovery | 0 | 2 | 2+2 | 0 | 0 | ~1.5min |

Key observations:
- **build_with_dependency**: Most complex test. Plan 1 created files in task 1 (msg.h + main.c without `#include <stdio.h>`), task 2 auto-done via duplicate guard, task 3 hit 3 cascading failures: (1) `cc -o program main.c msg.h` — can't compile header directly; (2) thinking=medium fixed to `cc -o program main.c` — missing stdio.h; (3) model knew the fix (`#include <stdio.h>`) but write action JSON truncated at 512 tokens, retry at 768 also truncated, 3rd retry at 768 finally succeeded (303.6s for step 5 alone). After fixing main.c, compiled and ran successfully but exhausted 8/8 steps → triggered replan. Plan 2 clean: compile + run (2 steps each).
- **fix_wrong_command**: Model tried `datex` (failed), thinking=medium recovered with `date > path/today.txt` in a single command (combined date + redirect). Re-tried `datex` again (step 3), thinking=medium produced `done`. No replan needed despite test expecting one — recovered within task via thinking.
- **multi_step_recovery**: Cleanest hard test — 4 steps total, no errors, no thinking retries. Planner correctly identified dependency order without needing to fail first.
- Write-action JSON truncation — this observation motivated the `edit` action (see 2026-04-08 below).

## OpenRouter Integration Test Results — 2026-04-08 (post `edit` action)

After adding the `edit` action, the 26B model spontaneously prefers `edit` for localized fixes and `write` for new files.

**Easy: 3/3 pass (37.6s total)**

| Test | Tasks | Steps | Time |
|------|-------|-------|------|
| create_and_read_file | 2 | 4 (t1:3, t2:1) | ~10s |
| shell_and_write | 1 | 3 | ~7s |
| multi_step_build | 2 | 3 (t1:2, t2:2) | ~21s |

**Medium: 3/3 pass (86.9s total)**

| Test | Replans | Tasks | Steps | Edit used? | Time |
|------|---------|-------|-------|------------|------|
| fix_python_syntax | 0 | 2 | 7 (t1:5, t2:3) | Yes — `edit greet.py` (37 tok, 0.9s) | ~23s |
| fix_missing_include | 0 | 3 | 8 (t1:4, t2:4, t3:2) | Yes — `edit fix_me.c` (42 tok, 1.0s) | ~50s |
| create_missing_file | 0 | 2 | 4 (t1:3, t2:1) | No (new file, used write) | ~13s |

Key observations:
- **fix_python_syntax**: Model read the file, used `edit` to fix the missing parenthesis, ran successfully. No thinking retries. Previously required full-file `write`.
- **fix_missing_include**: `edit` added `#include <stdio.h>` in 42 tokens vs ~200+ tokens a full `write` would need. This was the exact bottleneck that caused 303.6s retries on local hard tests.

**Hard: 3/3 pass (176.0s total, 0 replans)**

| Test | Replans | Tasks | Steps | Edit used? | Time |
|------|---------|-------|-------|------------|------|
| build_with_dependency | 0 | 3 | 14 (t1:6, t2:8, t3:1) | No (new files, used write) | ~68s |
| fix_wrong_command | 0 | 1 | 3 | No (shell only) | ~33s |
| multi_step_recovery | 0 | 2 | 8 (t1:6, t2:2) | No (new file, used write) | ~75s |

Key observations:
- All 3 hard tests completed with 0 replans — planner reasoning + edit action eliminated the need for replanning.
- **build_with_dependency**: Write duplicate loops still present (model writes msg.h 3x before moving on), duplicate guard handles them. No write truncation because planner task descriptions include content hints (`#define MSG "REPLAN_OK"`).
- **multi_step_recovery**: Model truncated a path in one shell command (dropped `7s` from `b2q3fw8x7s`), recovered by checking pwd and retrying with relative paths.

## Thinking-on-Retry — Test Results (2026-04-07)

**Unit tests (`TestThinkingRetry`): 9/9 pass**
1. `test_thinking_retry_openrouter` — reasoning params added on retry
2. `test_thinking_retry_local` — `<|think|>` prepended + max_tokens bumped
3. `test_thinking_strips_channel_tags` — closed `<|channel>` blocks stripped
4. `test_thinking_strips_unclosed_channel_tags` — unclosed blocks stripped
5. `test_api_error_retries` — API error responses retried gracefully
6. `test_no_thinking_on_first_attempt` — no reasoning on first attempt
7. `test_think_true_enables_from_first_attempt` — caller can force thinking
8. `test_null_content_with_reasoning` — `content: null` handled (retries)
9. `test_think_escalates_to_high` — medium → high escalation

**OpenRouter hard tests: 3/3 pass**
- `test_replan_build_with_dependency` — **PASSES** (code hallucination fixed by thinking)
- `test_replan_fix_wrong_command` — **PASSES** (thinking helped debug, replanned successfully)
- `test_replan_multi_step_recovery` — **PASSES** (was failing — model output dict content `{"status": "SUCCESS"}` instead of escaped string; fixed by auto-serialization in `execute()`)

### Bugs found during thinking-on-retry implementation

1. `reasoning.effort` + `reasoning.max_tokens` mutually exclusive — OpenRouter returns 400 if both specified. Fixed: use `effort` only.
2. `content: null` with reasoning — Parasail provider's reasoning tokens count against `max_tokens`. At the original 512 max_tokens, reasoning used ~484 tokens leaving nothing for content. Fixed: bump to 1536/2048.
3. API error → KeyError — when OpenRouter returns `{"error": {...}}` instead of `{"choices": [...]}`, code crashed on `rj["choices"]`. Fixed: check for `"error"` key, log and retry.

## Duplicate Action Guard — Before/After Impact

| Scenario | Before | After |
|---|---|---|
| Write main.c loop (same content) | 5 writes → exhausted steps → replan | 1 write → auto-done |
| Write main.c then fix (different content) | N/A (would have been false positive) | allowed (content differs) |
| Shell datex failure loop | 8 attempts → exhausted steps → replan | 2 attempts → auto-fail → replan |
| Shell gcc after fixing source | N/A (would have been false positive) | allowed (last step is write, not shell) |
| Read same file twice | N/A (would have been false positive) | allowed (read excluded from guard) |
| data.txt write loop (same content) | 3 writes → done on step 4 | 1 write → auto-done |
| Normal execution (no loops) | No change | No change (guard never triggers) |

## Planner Reasoning — Impact and Cost Analysis

Expected impact when conditional planner thinking (off first, on replans) was introduced:

| Scenario | Before (retry-based) | After (planner reasoning) |
|---|---|---|
| build_with_dependency | 3 tasks, 8+2+2 steps, 1 replan, ~11min | 2-3 tasks, ~5 steps, 0 replans, ~3min |
| fix_python_syntax | 3 tasks (1 redundant), 9 steps, 370s wasted | 2 tasks (no overlap), ~5 steps, ~2min |
| fix_missing_include | 3+3 tasks (replan), path truncation, ~11min | 2 tasks, relative paths, ~2min |
| fix_wrong_command | 3 tasks (1 meta-task), ~4.5min | 2 tasks (actionable), ~2min |
| Easy tests (no errors) | No change (already fast) | No overhead (thinking off for first plan) |

**Cost asymmetry rationale.** Planner runs once per plan (~512 extra tokens = ~73s at 7 tok/s on local). Executor retries fire per failed step (256→512→768 tokens × N steps = 150-600s). Investing reasoning at the planning stage prevents multiple downstream retries.

**Status (2026-04-08, OpenRouter post-edit):** Hard test targets met — 0 replans on all 3 hard tests (target was ≤1). Medium tests improved — `edit` action eliminates write truncation retries (fix_python_syntax: 0 thinking retries, fix_missing_include: 0 replans).

### Benchmark summary that produced the decision

Benchmarked across 8 prompts on both OpenRouter (Gemma 4 26B, 48 calls) and local (Gemma 4 E4B, 22 calls, stopped early after decisive signal):
- `think=False` produced equal or better plan quality on every prompt in both backends.
- On local, `think=True` caused JSON truncation failures (thinking tokens consumed the 768-token budget, truncating task lists — 2/3 header_dep runs failed all retry attempts).
- On OpenRouter, `think=True` was the only mode with rubric failures (header_dep: 2 FAILs vs 0 for `think=False`).
- Speed: local `think=True` averaged 40-55s vs 3-12s for `think=False` (5-12x); OpenRouter 12.1s vs 1.1s (11x).
