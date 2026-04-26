# Experiments

Active backlog of experiments for `askme.py`. Curated from observations in [PERFORMANCE.md](PERFORMANCE.md) and constraints in [ARCHITECTURE.md](ARCHITECTURE.md). Sized for ~15 active items — this is a working backlog, not a wish list.

## Conventions

- **Priority.** Importance of the change. `P0` = prerequisite, blocks others. `P1` = high expected yield. `P2` = gated on a P1 result. `P3` = redesign-scale, deferred until P1/P2 close.
- **Wave.** When to run it. Execution sequence, not importance. See [Waves](#waves) below.
- **Effort.** `S` ≤ 2h, `M` half-day, `L` 1–2 days.
- **Status.** `planned` → `running` → `done` (moved to PERFORMANCE.md) or `archived`.

## Waves

Execution is sequenced in waves so cheap-but-high-information experiments run first and reprice the rest of the backlog.

- **Wave 1 — no-code baselines.** Server-flag / model-swap trials that can obviate downstream code work. Run first.
- **Wave 2 — cheap code wins.** S-effort, mostly-independent prompt and parse changes.
- **Wave 3 — structural.** M-effort changes that build on Wave 2.
- **Wave 4 — gated.** P2 items; need data from earlier waves or free budget from E02.
- **Wave 5 — deferred.** P3 redesigns. Do not start until Wave 2–4 close.

## Staleness policy

- Experiments not started within 4 weeks of being added are either re-justified or removed.
- When an experiment is run, its result row moves to [PERFORMANCE.md](PERFORMANCE.md) as a dated entry. The entry here flips to `done` with a link to the PERFORMANCE.md section, or is deleted.
- `archived` entries that sit more than 8 weeks are deleted — keep the doc scannable.

## Ranking

Ordered by execution sequence (Wave, then within-wave order). For a topic-based view, see the section headers below.

| Run | ID  | Experiment                                              | Wave | Priority | Effort | Status   |
|-----|-----|---------------------------------------------------------|------|----------|--------|----------|
| 1   | E01 | 3-trial test harness on top of existing `AGENT_RUN_LOG` | 1    | P0       | S      | done     |
| —   | E08 | `--checkpoint-every-n-tokens` trial on E4B              | 1    | P1       | S      | archived |
| 2   | E05 | Error-class-specific retry policy                       | 2    | P1       | M      | done     |
| 3   | E06 | Typed recovery templates by `error_type`                | 2    | P1       | M      | done     |
| 4   | E16 | Compiler-aware shell error classification               | 2    | P1       | S      | planned  |
| 5   | E03 | Tiered retry contract + JSON repair                     | 2    | P1       | S      | planned  |
| 6   | E02 | Shrink `SYSTEM_PLAN` / `SYSTEM_STEP` 25-40%             | 2    | P1       | S      | planned  |
| 7   | E07 | Deterministic verification before LLM validator         | 2    | P1       | S      | planned  |
| 8   | E11 | Task-local replan before full replan                    | 3    | P1       | M      | planned  |
| 9   | E04 | Deterministic `search` action (ripgrep)                 | 3    | P1       | M      | planned  |
| 10  | E09 | Q8_0 model trial on medium/hard tests                   | 3    | P1       | S      | planned  |
| 11  | E12 | Split planner vs executor retry budgets                 | 4    | P2       | S      | planned  |
| 12  | E15 | Command-family timeout ladder                           | 4    | P2       | S      | planned  |
| 13  | E13 | Planner critique pass on redundancy-risk plans          | 4    | P2       | M      | planned  |
| 14  | E14 | Typed planner output with `success_criteria`            | 4    | P2       | M      | planned  |
| 15  | E10 | Batched actions (2-3 atomic actions per LLM call)       | 5    | P3       | L      | planned  |

### Wave ordering rationale

Updated 2026-04-26 based on local E4B JSONL time-breakdown analysis and the E05/E06 rerun in PERFORMANCE.md. E05/E06 removed the targeted edit-recovery waste; `ask_llm` parse-retry thinking escalation is now the highest-leverage remaining scaffold fix.

- **E05/E06 completed; E16 is a small hardening follow-up, then E03.** E05/E06 validated the targeted edit-recovery mechanism: failed edit recovery fell from 140–253s thinking-inflated reads to ~36s total in both rerun trials. End-to-end wall time was roughly break-even because `ask_llm` internal parse-retry thinking escalation became the dominant bottleneck.
- **E09 stays after E03.** E09 could reduce the underlying edit failure rate from the model side, but E03 targets the larger observed scaffold bottleneck first.
- **Wave 3 clusters E11, E04** — E11 (task-local replan) saves ~60–90s per replan on local but is lower leverage than the current E03 parse-retry target. E04 is independent.
- **Wave 4 is effort-ascending then dependency** — E12 and E15 are S-effort standalones. E13 needs redundancy-baseline data from prior waves. E14 is gated on E02 freeing planner-budget headroom.
- **Wave 5 (E10) stays deferred** — redesign-scale; do not start until Waves 2–4 close and the harness can detect reliability regressions.
- **Recommended execution order within waves:** E16 → E03 → E02/E11 → E09. Data-backed: E16 is a small correctness hardening pass; after E05/E06, parse-retry escalation costs 150–230s on single failed attempts; E11 targets ~60–90s replans; E09 reduces root-cause edit failure rate.

## Prerequisite

### E01 — 3-trial harness on top of existing `AGENT_RUN_LOG`

- **Context.** JSONL trace emission is already implemented: `AGENT_RUN_LOG=/path` emits events with timings + token usage. Do not re-propose trace emission. Definition at `askme.py:173` (`RUN_LOG_PATH`) and `askme.py:176` (`_run_log`). Call sites:
  - `askme.py:636` — `run_start`
  - `askme.py:661` — `plan_error` (planner transport failure)
  - `askme.py:669` — `plan`
  - `askme.py:788` — `step`
  - `askme.py:805` — `task_complete`
  - `askme.py:810` — `task_failed`
  - `askme.py:826` — `validation` (valid=false)
  - `askme.py:832` — `validation` (valid=true)
  - `askme.py:836` — `run_end` (status=complete)
  - `askme.py:845` — `run_end` (status=exhausted)

  Only the multi-trial harness is missing.
- **Hypothesis.** Run-to-run variance on local is high enough (planner thinking alone varies 45-89s, PERFORMANCE.md:379) that single-trial measurements can't distinguish a real effect from noise.
- **Change.** Add a small harness that runs each integration test N=3 times with `AGENT_RUN_LOG` pointed at a per-trial file, then reports median + range from the trace JSONL. No changes to `askme.py`.
- **Metric.** Harness produces reliable deltas (median ± range) across N=3.
- **Upside.** Unlocks the rest of the backlog. Makes PERFORMANCE.md entries directly reproducible.
- **Risk.** Low. Harness is additive; no production code changes.
- **Code.** `tests/bench_harness.py` (standalone CLI). No `askme.py` changes.
- **Effort.** S.
- **Status.** Done (2026-04-26). Harness discovers tests via `pytest --collect-only`, runs N trials as subprocesses with per-trial `AGENT_RUN_LOG`, parses JSONL, reports median+range for wall time, replans, steps, thinking retries, LLM calls, and tokens. Saves `summary.json` for programmatic comparison. Documented in README.md and CLAUDE.md.

## Prompts / output format

### E02 — Shrink `SYSTEM_PLAN` / `SYSTEM_STEP` 25-40%

- **Hypothesis.** While `--cache-reuse` is broken for Gemma 4 iSWA ([#21468](https://github.com/ggml-org/llama.cpp/issues/21468)), every token in the system prompt is re-processed on every `ask_llm` call. Shrinking them yields a linear speedup across every call in every test.
- **Change.** Compress policy/rule prose into terse symbolic bullets. Drop filler like "No markdown, no explanation" where grammar/format retries already handle it.
- **Metric.** Total test time, per-call prompt eval tokens (from `usage.prompt_tokens`).
- **Upside.** Medium-test time is dominated by prompt eval overhead — 10-20% reduction plausible.
- **Risk.** Low — easy to A/B. Watch for quality regression on medium tests (E01's harness gates this).
- **Code.** `askme.py:143` (`SYSTEM_PLAN`), `askme.py:189` (`SYSTEM_STEP`).
- **Effort.** S.

### E03 — Tiered retry contract + JSON repair

- **Hypothesis.** Parse retries dominate medium-test time (PERFORMANCE.md:82, 5× retries on `fix_python_syntax`). Most failures are truncation or verbose-reasoning leaks, not semantic errors. Current retry only changes thinking level, not the output contract.
- **Evidence (2026-04-26).** Local E4B: 2–3 failed edit attempts per `fix_missing_include` trial at ~30–60s each. If JSON repair salvages even one, that's one fewer thinking retry saved.
- **Evidence after E05/E06 (2026-04-26).** `fix_missing_include` Trial 1 spent 303s on the first shell compile step (73s parse-failed attempt + 230s thinking retry) and later hit a 217s read step from the same `ask_llm` retry ladder. Trial 2 repeated the first-step pattern (109s + 183s = 292s). E05/E06 removed step-level thinking after `edit_failed`; parse-retry thinking is now the highest-leverage remaining scaffold bottleneck.
- **Change.** On parse fail:
  1. First retry: same contract, same thinking.
  2. Second retry: strict contract — "Output only the JSON object, shortest possible, no reasoning".
  3. Before retrying, attempt JSON repair: close missing brace, trim trailing commas, strip partial key.
- **Metric.** Parse-retry count across integration tests; total test time; per-call retry wall time.
- **Upside.** Could cut medium-test time materially. Current observed parse-retry inflation is 150–230s on single local E4B attempts.
- **Risk.** Low. Repair is idempotent — if repair succeeds, no model call was wasted.
- **Code.** `askme.py:205` (`ask_llm`), `askme.py:326` (`except json.JSONDecodeError` — parse block).
- **Effort.** S.

## Tools / action model

### E04 — Deterministic `search` action

- **Hypothesis.** Every medium/hard test burns shell calls on `grep`/`find`/`ls` patterns the LLM generates. These are fragile (path truncation, quoting) and token-heavy. A first-class `search` action backed by `rg` is deterministic and cheap.
- **Change.** Add `search` action: `{action: "search", arg: "pattern", path: ".", type?: "py"}`. Wraps `rg --json` and returns bounded match list.
- **Metric.** Number of shell calls used for search in integration tests; shell-generated search failures in logs.
- **Upside.** Reduces LLM call count on file-nav-heavy tasks; improves reliability.
- **Risk.** Adds an action the planner must learn. Mitigate by adding a one-line hint in `SYSTEM_STEP` once E02 lands.
- **Code.** `askme.py:550` (`execute`), `askme.py:189` (`SYSTEM_STEP` — actions list).
- **Effort.** M.

### E10 — Batched actions per LLM call **[P3 — redesign-scale]**

- **Hypothesis.** While #21468 is live, per-call overhead dominates. Many step chains are deterministic (write → chmod → run, or read → edit). Emitting 2-3 atomic actions per LLM call halves or thirds per-chain latency.
- **Change.** Allow executor to emit `{actions: [a1, a2, a3]}` in addition to single `{action: ...}`. Execute sequentially; stop on first failure; feed combined result back as one step entry.
- **Metric.** Total LLM calls per integration test; total test time.
- **Upside.** High if it works.
- **Risk.** High. The whole step loop, duplicate guard, step-history shape, and recovery logic are built around one action per step (`askme.py:686` step loop, `askme.py:715` duplicate guard, `askme.py:788` step log). Batched failure attribution breaks error classification. This is a redesign, not a drop-in — do not run before most P1s are done and the harness can measure whether it regresses reliability on medium/hard tests.
- **Code.** `askme.py:686` (step loop), `askme.py:715` (duplicate guard), `askme.py:189` (`SYSTEM_STEP`).
- **Effort.** L.

### E15 — Command-family timeout ladder

- **Hypothesis.** `_get_shell_timeout` has two buckets (30s / 120s) keyed off substring match. Many tests would benefit from higher defaults for `pytest`, `cargo build`, `make test` without the model having to specify a hint.
- **Change.** Extend `_LONG_TIMEOUT_PATTERNS` to a keyed ladder: `{pytest: 120, cargo build: 300, make test: 180, ...}`. Fall back to existing behavior.
- **Metric.** Timeout-caused retries in integration logs.
- **Upside.** Low-medium — only helps the long-tail timeout cases.
- **Risk.** Low.
- **Code.** `askme.py:529` (`_LONG_TIMEOUT_PATTERNS`), `askme.py:539` (`_get_shell_timeout`).
- **Effort.** S.

## Error recovery

### E05 — Error-class-specific retry policy

- **Hypothesis.** Current retry always escalates thinking (medium → high) regardless of error class. But `missing_tool` is not fixable by more thinking; `timeout` wants a longer timeout; `compile_error` wants to re-read the file first.
- **Evidence (2026-04-26).** JSONL analysis of `fix_missing_include` on local E4B: failed edit → thinking escalation → next `read` takes 140–253s because thinking tokens consume budget. This pattern accounts for ~250–300s per trial (~45% of wall time). The edit failure doesn't need more thinking — it needs to read the file first.
- **Change.** Deterministically tag edit mismatch/ambiguous/empty-find failures as `edit_failed`; skip step-level thinking escalation for structural errors (`edit_failed`, `missing_file`, `timeout`, `missing_tool`, `permission_denied`). Semantic failures (`compile_error`, `unknown`) still escalate.
- **Metric.** Wasted-thinking-time on unrecoverable failures; edit recovery path latency; replan count.
- **Result (2026-04-26).** Done. Two `fix_missing_include` rerun trials reduced the targeted edit recovery path to 36s in both trials, down from 140–253s thinking-inflated reads in baseline. Overall wall time was mixed (686.6s, 552.9s vs 609.1s baseline median) because `ask_llm` parse-retry thinking inflation dominated unrelated steps.
- **Upside.** Validated targeted scaffold fix. Does not reduce the underlying edit failure rate.
- **Follow-up caveat.** The result is robust for deterministic edit scaffold errors. Shell-origin errors still use `classify_error()` heuristics; compiler diagnostics containing `No such file or directory` can be misclassified as `missing_file`, which skips thinking. See E16.
- **Risk.** Low for edit-origin failures. Shell-origin structural/semantic classification needs E16 hardening.
- **Code.** `askme.py:512` (`classify_error`), `askme.py:785` (error handling in step loop), `askme.py:205` (`ask_llm` — retry ladder).
- **Effort.** M.
- **Status.** Done (2026-04-26). See PERFORMANCE.md E05/E06 Edit Recovery.

### E06 — Typed recovery templates by `error_type`

- **Hypothesis.** After a `compile_error`, the next action is almost always `read` the offending file, then `edit`. After `missing_file`, the next action is often `search` or `ls`. Encoding this as a template is cheaper than asking the model to rediscover it.
- **Evidence (2026-04-26).** In all 9 `fix_missing_include` trials (local E4B), the successful recovery pattern is always: failed edit → read file → successful edit. But the scaffold currently lets the model rediscover this at ~150s cost per cycle (thinking-inflated). A template injection would short-circuit to the read immediately.
- **Change.** On failed step, inject a short per-error-type observation into `last_steps` that nudges the next action. Current hints: `edit_failed` → read the file first and retry exact text; `missing_file` → check filename with `ls`.
- **Metric.** Steps-to-recovery after typed failure; replan count.
- **Result (2026-04-26).** Done. In both rerun trials, failed edit recovery followed the intended cheap pattern: failed edit, no-thinking read at ~7.6s, then edit retry at ~16–17s. Trial 2 also confirmed repeated post-`edit_failed` retries stayed cheap until the consecutive failed-edit guard triggered replan.
- **Upside.** Compounds with E05. Addresses the `fix_python_syntax` / `fix_missing_include` slow-recovery pattern directly.
- **Risk.** Low. If template is wrong, model can still override.
- **Code.** `askme.py:785` (error handling — `state["errors"].append`), `askme.py:512` (`classify_error`).
- **Effort.** M.
- **Status.** Done (2026-04-26). See PERFORMANCE.md E05/E06 Edit Recovery.

### E16 — Compiler-aware shell error classification

- **Hypothesis.** E05's no-thinking policy is only safe when the error type is correct. Shell-origin `missing_file` is ambiguous: real missing files are structural, but compiler/header diagnostics like `stdio.h: No such file or directory` are semantic compile failures and should usually escalate thinking.
- **Evidence (2026-04-26).** Direct checks found `stdio.h: No such file or directory` classifies as `missing_file` because `classify_error()` checks `"no such file"` before `"error:"`. Since `missing_file` is in `_NO_THINK_ERRORS`, this can skip thinking on compiler errors. Edit-origin failures are unaffected because they are tagged deterministically in `execute()`.
- **Change.** Make shell classification command-aware. For compiler-like shell commands (`cc`, `gcc`, `g++`, `clang`, `make`, `cargo build`, etc.), prefer `compile_error` for diagnostics even when they contain `No such file or directory`. Keep non-compiler missing-file shell failures structural where possible.
- **Metric.** Classification unit tests for compiler header errors, missing source paths, and non-compiler missing-file commands; no regression in E05/E06 recovery tests.
- **Upside.** Hardens E05 against the dangerous misclassification direction: semantic shell error -> structural no-thinking error.
- **Risk.** Low if scoped to compiler command families. Global check reordering is simpler but semantically noisier.
- **Code.** `askme.py:512` (`classify_error`), shell execute call site passing enough command context if needed.
- **Effort.** S.

### E11 — Task-local replan before full replan

- **Hypothesis.** Full replan costs ~73s on local (planner thinking budget, ARCHITECTURE.md:185). Most failures are task-local: one task's plan was wrong, the others are fine. A scoped "re-plan this task only" is dramatically cheaper.
- **Evidence (2026-04-26).** All 3 `fix_missing_include` trials replan once at 69–112s. The replan produces essentially the same 3-task plan. Task-local replan would save ~60–90s per trial.
- **Change.** On task failure, call a mini-planner with `(failed_task, errors, completed_tasks)` that returns only a replacement task description. Reserve full `run()` replan for when task-local replan itself fails.
- **Metric.** Replan count; total test time on failure-heavy medium/hard tests.
- **Upside.** Medium — saves ~60–90s per replan on local, but lower leverage than E05/E06.
- **Risk.** Medium. Must avoid infinite task-local loop — cap at 1 task-local attempt before escalating to full replan.
- **Code.** `askme.py:651` (replan loop), `askme.py:384` (`get_plan`).
- **Effort.** M.

## Verification

### E07 — Deterministic verification before LLM validator

- **Hypothesis.** For goals matching `compile|build|test|run`, checking an exit code or file existence is cheaper and more reliable than the LLM validator. The LLM validator should only fire when deterministic checks are inconclusive.
- **Change.** In `_validate_completion`, run deterministic checks first: if the goal mentions a built artifact, verify it exists + is executable; if it mentions "run X", verify exit 0 was recorded in `completed_step_groups`. Only fall through to the LLM call when checks can't answer.
- **Metric.** LLM validator call count; false-positive/false-negative rate on integration fixtures.
- **Upside.** Saves ~0.5-2s per validation on happy path; more reliable.
- **Risk.** Low — keeps LLM validator as fallback.
- **Code.** `askme.py:471` (`_validate_completion`), `askme.py:447` (`_should_validate`).
- **Effort.** S.

## Performance / runtime

### E08 — `--checkpoint-every-n-tokens` on E4B — ARCHIVED

Moved to [Archived / rejected](#archived--rejected).

### E09 — Q8_0 model trial

- **Hypothesis.** `gemma4-setup.md:22` notes Q8_0 (8 GB) is viable and "higher quality." Parse retries likely drop with better token probabilities. On a 16 GB M1 with q4_0 KV the memory headroom exists.
- **Evidence (2026-04-26).** Local E4B generates bad edit JSON ~60% of first attempts (vs near-zero on OpenRouter 26B). Q8_0 could reduce this underlying failure rate. However, the E05/E06 rerun shows parse-retry thinking inflation is the largest remaining scaffold-addressable bottleneck — run E03 first.
- **Change.** Download Q8_0 GGUF, launch with same flags, run easy + medium integration under E01's harness.
- **Metric.** Parse-retry count, total test time, especially on `fix_missing_include` (currently 609s median).
- **Upside.** Reduces root-cause edit failure rate. After E05/E06, repeated edit failures are cheaper but still cause replans; model quality remains a separate lever.
- **Risk.** Low. Model is a swap; reverts trivially. Decode throughput may drop — measure both axes.
- **Code.** `gemma4-setup.md` (model path), no `askme.py` change.
- **Effort.** S.

### E12 — Split planner vs executor retry budgets

- **Hypothesis.** `MAX_LLM_RETRIES=2` is shared by both. Planner benefits from thinking escalation; executor benefits from more aggressive contract-switching (E03) over thinking. One knob isn't right for both.
- **Change.** Split into `MAX_PLANNER_RETRIES` (default 2, thinking-escalating) and `MAX_EXECUTOR_RETRIES` (default 2, contract-escalating per E03). Callers pick the ladder.
- **Metric.** Per-caller retry distribution; total test time.
- **Upside.** Low-medium, but makes the system easier to tune.
- **Risk.** Low. Gated on E03 landing first — otherwise no meaningful contract ladder to run on.
- **Code.** `askme.py:202` (`MAX_LLM_RETRIES`), `askme.py:205` (`ask_llm`).
- **Effort.** S.

## Planning

### E13 — Planner critique pass on redundancy-risk plans

- **Hypothesis.** Redundant tasks are a real cost. PERFORMANCE.md:57 documents a **3-task** plan on `fix_python_syntax` where task 2 was already satisfied by task 1 — 370s wasted. A ≥4-task trigger would miss this case entirely. The right trigger is a redundancy-risk signal, not task count.
- **Change.** After `get_plan`, run a one-shot critique (think=medium, max_tokens=256) that can drop tasks when **any** of the following is true:
  1. Two or more tasks mention the same filename or symbol.
  2. A task's verbs overlap with a verb already in `completed_tasks` for the same target.
  3. Plan has ≥4 tasks (fallback for sprawl).
  Skip the critique when plan is 1-2 tasks with no overlapping targets.
- **Metric.** Redundant-task rate (measured by executor emitting `done` on step 1); total time on medium tests.
- **Upside.** Targets a documented pathology. Only fires on risk signals.
- **Risk.** Medium — adds an LLM call. Must measure that savings exceed critique cost.
- **Code.** `askme.py:384` (`get_plan`), `askme.py:651` (replan loop — call site).
- **Effort.** M.

### E14 — Typed planner output with `success_criteria`

- **Hypothesis.** If each task ships with a compact success-criteria string, the executor knows when to emit `done` without having to infer it. Currently the model infers completion from step history, which is brittle.
- **Change.** Planner outputs `{tasks: [{desc, success_criteria}]}` instead of plain strings. Executor sees `success_criteria` in slim state.
- **Metric.** Step count per task; `done`-emission reliability.
- **Upside.** Medium — directly targets the "couldn't emit done" pathology (PERFORMANCE.md:57).
- **Risk.** Medium. Adds ~30-50 tokens per task to the planner's output budget, which is already tight (768 tokens shared with thinking). **Gated on E02 landing** to free budget.
- **Code.** `askme.py:143` (`SYSTEM_PLAN`), `askme.py:407` (`get_step`), `askme.py:422` (slim state).
- **Effort.** M.

## Archived / rejected

### E08 — `--checkpoint-every-n-tokens` on E4B (2026-04-26)

**Archived: subsumed by Phase 6.** The `--swa-full --cache-reuse 256` fix ([#22288](https://github.com/ggml-org/llama.cpp/pull/22288)) solved the prompt re-processing problem that checkpointing was meant to work around. Phase 6 deterministic benchmark confirmed no downside vs Phase 5 and 4.5% faster prompt eval. The checkpoint flags (`--checkpoint-every-n-tokens 1024 --ctx-checkpoints 256`) were a Qwen workaround for the same underlying issue (#21468/#21831); `gemma4-setup.md:559` explicitly notes this path is "effectively subsumed."

## References

- [PERFORMANCE.md](PERFORMANCE.md) — benchmark history; completed experiments land here.
- [ARCHITECTURE.md](ARCHITECTURE.md) — design decisions + current constraints.
- [gemma4-setup.md](gemma4-setup.md) — server config; runtime experiments (E08, E09) land here.
- [CLAUDE.md](CLAUDE.md) — agent authoring guidance.
