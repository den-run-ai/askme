# Performance

Benchmark history and test-run matrices for AskMe. Each entry is a point-in-time measurement against a specific build + model + config. Kept for comparison across changes, not as a current-truth reference.

**Staleness policy.** Each section is dated. If an entry is more than ~6 months old and the code that produced it has changed materially, treat it as historical only — re-run before citing numbers. Sections marked `[stale]` have known divergence from current code.

**Assertion caveat (2026-07-10).** Historical integration runs below used the assertions present at the time. Several build/repair tests verified file content without requiring `agent_complete` and independently executing the final artifact. Those tests now require both completion and a deterministic execution postcondition. Treat older pytest pass counts as harness-history signals, not strict end-to-end success rates.

**Build caveat (2026-08-03).** All local (M1) entries below through 2026-05-03 were measured on llama.cpp build `a702f395` (2026-04-25). The machine has been running build 9618 (`c34b92235`, master 2026-06-13) since 2026-06-12 — it adds the #23468 cache-reuse reliability fix and Gemma 4 MTP support. The E23 entry below is the first benchmark on the current stack and the new local reference; treat all older local numbers as pre-b9618 history.

**Source caveat (2026-08-03 records).** Every complete E23/12B summary records
`git_dirty=true`. Its SHA is a base commit, not an exact reproducible AskMe
revision; the worktree diffs were not retained. See the records README for the
exact base SHAs and other provenance limits.

For architecture decisions and current constraints see [ARCHITECTURE.md](ARCHITECTURE.md). For model/server config see [gemma4-setup.md](gemma4-setup.md). For the active experiment backlog that feeds future Phase entries here, see [EXPERIMENTS.md](EXPERIMENTS.md).

## E09 12B QAT Trial — 2026-08-03, Local (build 9618, Gemma 4 12B Unified QAT Q4_0) — NEGATIVE UNDER THE E4B-FITTED CONTRACT

E09 candidate trial of `google/gemma-4-12B-it-qat-q4_0-gguf` (6.98 GB,
dense 12B) with the same server flags as the E23 reference (`--reasoning off`,
q4_0 KV, `--swa-full --cache-reuse 256`, MTP off) and the then-default local
E4B-fitted 256-step/512-write-token contract. The records predate named
capability profiles. Their `run_start.model` is incorrectly
`gemma-4-e4b` and the summaries have no model identity; the per-call token
events report `gemma-4-12b-it-qat-q4_0.gguf` as the served-model identity, but
the retained records contain no physical-artifact hash.
AskMe's recorded reasoning policy was `gated`; the server's `--reasoning off`
is a separate llama-server parser/default setting. **Scaffold caveat:** this
ran on post-rebase main (issue #68 completion semantics, revision-4 write
pressure, uv-locked pytest 9), while the E23 E4B numbers are from the
pre-rebase tree — a cross-scaffold comparison.

The run was **stopped externally partway through medium** (easy complete, medium partial), but the predeclared decision rule — within ~2× of E4B QAT wall time and both failure classes cleared — was already decisively failed at the easy tier.

### Easy (3 trials each, vs E23 E4B QAT reference)

| Test | Pass | Wall (median) | E4B QAT | Think retries/trial | Notes |
|---|---|---|---|---|---|
| `create_and_read_file` | 2/3 | 268.3s (138.1–374.2) | 95.5s | 3–4 | 1 exhausted |
| `shell_and_write` | 3/3 | 66.4s (34.5–83.6) | 15.8s | 0–1 | clean but 4.2× slower — near-pure decode/prompt-eval tax |
| `multi_step_build` | **1/3** | 202.1s (181.1–215.7) | 43.8s | **6–8** | 2 exhausted; heavy parse-retry churn |

Easy totals: 1568s vs 437s (**3.6×**), pytest 6/9 vs 7/9.

### Medium (partial — run stopped during trial set)

- `fix_python_syntax_error`: exhausted 270.3s / exhausted 482.0s / complete 222.8s (E4B QAT: complete 3/3 at 43.8s median).
- `fix_missing_include` trial 1: complete at **545.8s** (E4B QAT: 15.7s — **35×**).

### Verdict

**12B QAT failed this E4B-fitted agent contract on this 16 GB M1.** Under the
256/512-token limits it paid up to 6–8 JSON retries on `multi_step_build` on top of ~2.5× slower
dense decode, compounding to 3.6–35× wall time with more exhaustion. Neither
E23 failure class was cleared (done-emission-style exhaustion recurred;
content drift was untested because the suite stopped first). This is not a
model-wide rejection: a conclusion under the new generic capability profile
requires a newly registered run with requested, profile, and served identities
pinned. **E4B QAT Q4_0 remains the qualified primary model.** Raw records:
`tests/bench_records/2026-08-03/12b_{easy,medium}/`.

## E23 QAT Baseline — 2026-08-03, Local (build 9618, official E4B QAT Q4_0)

First local benchmark on the current stack (E23): build 9618 `c34b92235`, official post-refresh **QAT Q4_0** (`google/gemma-4-E4B-it-qat-q4_0-gguf`, 5.15 GB, downloaded 2026-08-03), flags per gemma4-setup.md incl. `--reasoning off`, MTP off, default (heuristic) step policy. 3 trials per test via `bench_harness.py`. Raw records: `tests/bench_records/2026-08-03/qat_{easy,medium,hard}/` (protocol + limitations in its README).

**Reasoning probe (pre-bench).** Even the fresh post-refresh QAT template is detected as `thinking = 1` under `--reasoning auto`. A trivial JSON prompt stayed clean, but a planner-style prompt emitted 371 chars of `reasoning_content` (152 completion tokens consumed). `--reasoning off` is a **permanent requirement** for AskMe, not a stale-GGUF artifact.

### Easy (3 trials each, vs 2026-04-26 Q4_K_M baseline on `a702f395`)

| Test | Pass | Wall (median) | Apr baseline | Replans | Notes |
|---|---|---|---|---|---|
| `create_and_read_file` | **1/3** | 95.5s (15.0–137.4) | 33.7s, 3/3 | 2 full in each failed trial | Both failures: all steps succeeded, deliverable correct, model never emitted `done` — duplicate-action loops (5–6 skips) until exhaustion |
| `shell_and_write` | 3/3 | 15.8s (15.4–17.9) | 20.1s | 0 | −21%, clean |
| `multi_step_build` | 3/3 | 43.8s (35.6–53.7) | 118.7s | **0** (baseline: 1 every trial) | −63%, zero replans, zero thinking retries |

### Medium (3 trials each, vs 2026-04-26 Q4_K_M baseline)

| Test | Pytest | Agent | Wall (median) | Apr baseline | Replans | Notes |
|---|---|---|---|---|---|---|
| `fix_python_syntax_error` | **0/3** | 3/3 complete | 43.8s (42.9–52.8) | 124.8s, 3/3 | 0 | **Content drift, not agent failure**: fixed the syntax but rewrote `print("hello"` → `print("Hello")` in all 3 trials; program runs, case-sensitive postcondition (`"hello" in stdout`) fails. Root cause: whole-file `write` rewrite instead of minimal `edit` on first pass |
| `fix_missing_include` | 3/3 | 3/3 | **15.7s** (15.68–15.71) | **609.1s** | 0 | **39× faster than the historical local bottleneck.** 2 steps, 4 LLM calls, zero failed edits, zero thinking retries, near-zero variance |
| `create_missing_file_then_use` | 3/3 | 3/3 | 13.3s (13.2–226.6) | 29.0s | 1 in outlier trial | Trial 1 outlier (226.6s) shows the same done-emission loop pattern before recovering |

### Hard (3 trials each, vs 2026-05-03 Q4_K_M baseline)

| Test | Pass | Wall (median) | May baseline | Replans (full) | Local replans | Thinking retries |
|---|---|---|---|---|---|---|
| `replan_build_with_dependency` | 3/3 | **376.9s** (303.3–422.3) | 903.7s | 0 (0–2) | 2 per trial, **6/6 ok** | 2 (0–3) vs 6–7 |
| `replan_fix_wrong_command` | 3/3 | **26.9s** (17.0–36.1) | 79.6s | 0 (0–1) | 0–1 | 0 |
| `replan_multi_step_recovery` | 3/3 | **54.8s** (34.3–116.9) | 88.0s | 0 (0–1) | 0–1 | 0 |

**Hard: 9/9 pytest, 9/9 agent complete** — same pass rate as the May baseline at −38% to −66% wall time. E11 local replans went 6/6 on `build_with_dependency`. Thinking retries collapsed from 6–7 per `build_with_dependency` trial to 0–3.

### Findings

1. **The current stack transforms error-recovery tests — attribution is stack-level, not weights-isolated.** `fix_missing_include` collapses 609s → 15.7s and `multi_step_build` loses its every-trial replan. **Correction (2026-08-04, from the retained records): recovery machinery was not idle.** Deterministic C repair (issue #41) fired once in each of the three medium `fix_missing_include` trials (**3 repairs**) and 2, 2, and 4 times in the three hard `replan_build_with_dependency` trials (**8 repairs**). These records make repair part of the bundled stack; without the draft #41 on-vs-off arm they do not identify its causal contribution to speed or variance. The suite recorded zero `edit_failed` events and zero thinking retries in easy+medium, but E05 was not fully dormant: `missing_tool` triggered its no-think policy in all three `replan_fix_wrong_command` trials. Hard recorded **5 thinking-retry attempts** (2, 3, 0), not 10; 10 is the paired `reasoning_decision` + `tokens` line count. The comparison baselines are Apr/May runs on build `a702f395` with an older AskMe revision, older assertions, and no deterministic C repair, so the deltas bundle QAT weights + build 9618 (including the #23468 cache fix) + server `--reasoning off` + scaffold evolution + deterministic repair. No matched Q4_K_M-on-b9618 control was run (see `tests/bench_records/2026-08-03/README.md`, limitation 1); the draft #41 on-vs-off ablation is required to price the repair arm's contribution.
2. **New dominant failure class: done-emission loops.** 2 of 5 pytest failures are "work done correctly, `done` never emitted, duplicate-skip until exhausted" (`create_and_read_file` trials 1 and 3), and a third occurrence of the same loop pattern in `create_missing_file_then_use` trial 1 recovered within budget and passed (226.6s vs 13.3s median) — so the pattern appeared in 3 runs but caused 2 of the 5 failures. It extends beyond edits to reads/writes. Recorded as dated evidence on the E20 and E07 dispositions; per the issue #68 design (repetition is never acceptance, exhaustion is terminal) these runs correctly stay `exhausted`, and the sanctioned lever to evaluate is the lifecycle step policy (`AGENT_STEP_POLICY=lifecycle`) — this bench ran the default heuristic arm.
3. **New failure class: content drift on rewrite.** QAT prefers whole-file `write` over minimal `edit` for the first fix and takes liberties with content (capitalization). Systematic (3/3). An agent asked to fix an error should preserve program semantics — a genuine model-behavior regression; motivates a prompt nudge toward `edit` for fixes and the goal-output arm of E07.
4. Suite scorecard: easy 7/9, medium 6/9, hard 9/9 pytest (agent-complete 25/27). The Apr/May Q4_K_M baseline was 27/27 — but at 1.6–39× the wall time on the tests that matter.

### Verdict

**QAT Q4_0 promoted to primary model (2026-08-03).** Every one of the 5 pytest failures is one of the two identified behavioral quirks, both recorded in EXPERIMENTS.md dispositions (E20/E07) and ARCHITECTURE.md Current Constraints, while measured wall-time gains on the improved workloads are 1.6–39×. The promotion is a **current-stack decision** — the post-refresh QAT weights are the recommended E4B artifact going forward regardless of how the gain decomposes across weights/build/flags — not a weights-isolated causal claim (limitation 1 in the bench records). Raw records: `tests/bench_records/2026-08-03/` (copied from `/tmp/bench_qat_{easy,medium,hard}_20260803/`).

## MTP + Reasoning-Default Smoke Test — 2026-08-03, Local (build 9618 `c34b92235`)

Three-prompt, single-pass smoke test of Gemma 4 MTP self-speculation: E4B Q4_K_M + official 98.7 MB assistant GGUF (`--spec-type draft-mtp`), 4K ctx, q4_0 KV. Not an AskMe evaluation — it establishes that the drafter loads and runs on b9618, and prices the current speedup.

| Configuration | Decode | vs baseline |
|---|---|---|
| No MTP | 13.61 tok/s | baseline |
| MTP `--spec-draft-n-max 1` | 11.84 tok/s | −13.0% |
| MTP `--spec-draft-n-max 3` | 13.24 tok/s | −2.7% |

MTP is currently a small loss on M1: draft verification runs at exactly the batch sizes (4–16) where the Metal mul_mat path is unoptimized (llama.cpp [#25250](https://github.com/ggml-org/llama.cpp/issues/25250), ~2x headroom), and there is no adaptive n-max yet ([#24768](https://github.com/ggml-org/llama.cpp/issues/24768)). Keep MTP off; revisit via E24 when either lands. (The 13.61 tok/s baseline vs the ~7 tok/s cited elsewhere reflects 4K-ctx smoke-test conditions vs 16K agentic load — not a contradiction.)

**New failure mode — reasoning auto-detection.** b9618's `--reasoning` flag defaults to `auto` and detects the installed 2026-04-06 GGUF's old chat template as thinking-capable. With AskMe-sized token budgets (~192 for actions), the allowance frequently drained into `reasoning_content`, returning empty or truncated final JSON — a server-side confound on top of every local scaffold metric. Mitigation: `--reasoning off` (now in gemma4-setup.md's launch command). Root-fix hope disproven same day: the post-refresh QAT GGUF also triggers `thinking = 1` under `auto` (see the E23 QAT Baseline entry above) — the flag is permanent.

Offline unit suite on the same date: 448 passed, 31 deselected in 35.1s.

## E21 gpt-oss-20b Effort-Cell Wiring — 2026-08-02

Wiring for [EXPERIMENTS.md E21](EXPERIMENTS.md#e21--gpt-oss-20b-lowmediumhigh-effort-as-the-openrouter-ciprototyping-model)
landed: `OPENROUTER_REASONING_EFFORT` baseline in `askme.py`,
`--reasoning-effort` on `bench_harness.py`, `requested=expected@effort` cells in the llm.yml
Berkeley job, and effort-qualified rows in `ci_llm_gate.py`. No live cells have
run yet (no key in the authoring environment) — the numbers below are from the
unauthenticated OpenRouter catalog (`/models`, `/models/openai/gpt-oss-20b/endpoints`,
2026-08-02), recorded so the eventual run can detect pricing/routing drift.

| Model | Context | Prompt $/M | Completion $/M | Notes |
|---|---|---|---|---|
| `google/gemma-4-26b-a4b-it` (control) | 262K | 0.07 | 0.34 | current CI default |
| `openai/gpt-oss-20b` | 131K | 0.03 | 0.13 | MoE 21B total / ~3.6B active; effort low/medium/high; `:free` variant exists |
| `qwen/qwen3.6-27b` (second CI cell) | 262K | 0.30 | 2.00 | for reference |

gpt-oss-20b endpoints: 12 total; repo-default Parasail serves it at
$0.03/$0.15 with 88.1% 30-min uptime; CoreWeave ($0.03/$0.13) and DeepInfra
($0.03/$0.14) at 100% uptime. All advertise the `reasoning` parameter.
SiliconFlow caps completion at 8K tokens — below `STEP_WRITE_TOKENS` (8192)
plus reasoning share, so pin one of the full-window providers for write-heavy
cells.

Observation worth recording: before this change, pointing `OPENROUTER_MODEL`
at gpt-oss-20b silently degraded the harness's reasoning gating — the
`reasoning.enabled=false` request contract cannot disable harmony-format
reasoning, so every "no thinking" call actually ran at provider-default
effort with reasoning tokens billed as completion tokens outside any budget
the harness chose. The baseline knob makes the served effort an explicit,
logged axis instead.

Comparison matrix to run once a key is available (3 trials each):

```bash
python3 tests/bench_harness.py --backend openrouter --suite easy \
  --model google/gemma-4-26b-a4b-it \
  --expected-served-model google/gemma-4-26b-a4b-it-20260403  # control
python3 tests/bench_harness.py --backend openrouter --suite easy \
  --model openai/gpt-oss-20b --expected-served-model openai/gpt-oss-20b \
  --reasoning-effort low                                 # repeat with medium, high
```

or dispatch llm.yml with
`models: google/gemma-4-26b-a4b-it=google/gemma-4-26b-a4b-it-20260403,openai/gpt-oss-20b=openai/gpt-oss-20b@low,openai/gpt-oss-20b=openai/gpt-oss-20b@medium,openai/gpt-oss-20b=openai/gpt-oss-20b@high`
(8 Berkeley cells). Decision rule is predeclared in E21.

## Revision-4 Focused Test Snapshot — 2026-08-02

`python -m pytest tests/test_agent_actions.py -q` completed locally with
**122 passed**. This deterministic, mocked suite covers the revision-4
rewrite-damping and write-lifecycle changes, including empty and nonempty
truncation recovery, stale-file append prevention, task-scoped replanning,
completion gates, and evidence-gated validation rechecks. No live model or
provider was used; this is mechanism coverage, not an outcome benchmark.

## Test Suite Snapshot — 2026-07-13

`pytest -q` completed with **367 passed and 27 skipped**. Backend-dependent
integration tests skip when their server or credential is unavailable. Pytest
counts in dated sections below are historical snapshots, not the current suite
size.

## Running the Current Multi-Trial Harness

Current runs use `tests/bench_harness.py` (median + range across N trials).
They create new evidence under the current contract; they do not reproduce the
dated entries above, whose source/configuration limitations remain attached:

```bash
python3 tests/bench_harness.py --model gemma-4-e4b \
  --capability-profile legacy-e4b-m1-16k-v1 \
  --expected-served-model gemma-4-e4b
python3 tests/bench_harness.py --suite medium --trials 5 \
  --model gemma-4-e4b --capability-profile legacy-e4b-m1-16k-v1 \
  --expected-served-model gemma-4-e4b
python3 tests/bench_harness.py --backend openrouter --suite hard \
  --model google/gemma-4-26b-a4b-it \
  --expected-served-model google/gemma-4-26b-a4b-it-20260403
python3 tests/bench_harness.py --backend openrouter --suite easy --trials 1 \
  --model qwen/qwen3.6-27b --expected-served-model qwen/qwen3.6-27b-20260422 \
  --provider siliconflow                                         # strict provider pin
python3 tests/bench_harness.py --test test_shell_and_write \
  --model gemma-4-e4b \
  --expected-served-model gemma-4-e4b
python3 tests/bench_harness.py --list                             # show available tests
```

## FeatureBench One-Task Canary — 2026-07-13, OpenRouter (Gemma 4 31B)

One frozen FeatureBench fast task qualified the adapter with passing gold and
harmless controls, then produced a supported negative outcome. The agent made
three planning attempts and four reads, but zero writes, edits, or shell
actions; the resulting patch was empty and the official evaluator completed
without an infrastructure error but did not resolve the task.

The binding constraint in this trajectory was the 512-token non-reasoning
structured-action cap: implementation write proposals were truncated at that
limit or returned malformed JSON, and the bounded retries did not recover a
valid write. This observation motivates testing chunked writes, localized
edits, or an adaptive action budget before broader feature-scale evaluation.

This is one canary on one task, not a FeatureBench score, reliability estimate,
model-family comparison, model-size result, or proof that raising the cap alone
would make the task pass. See the
[published result](../tests/featurebench/results/2026-07-13-gemma-4-31b-canary.json)
and [qualified runbook](../tests/featurebench/README.md).

## E01 Harness Baseline — 2026-04-26, Local (Gemma 4 E4B Q4_K_M)

Local E4B baseline with Phase 6 server config (`--swa-full --cache-reuse 256`). 3 trials per test. Build `a702f395`, M1 16 GB.

### Easy (3 trials each)

| Test | Pass | Wall (median) | Steps | Replans | Thinking retries | LLM calls | Prompt tok | Completion tok |
|---|---|---|---|---|---|---|---|---|
| `create_and_read_file` | 3/3 | 33.7s (27.2–445.2) | 2 | 0 (0–1) | 0 (0–1) | 5 (5–6) | 2637 (2610–3338) | 106 (95–1296) |
| `shell_and_write` | 3/3 | 20.1s (19.6–36.1) | 1 | 0 | 0 | 3 | 1530 (1530–1590) | 57 (57–118) |
| `multi_step_build` | 3/3 | 118.7s (102.7–124.5) | 5 (5–6) | 1 | 0 | 11 (11–12) | 6401 (6389–6892) | 541 (532–570) |

**Observations:**
- Trial 1 of `create_and_read_file` was a 445s cold-start outlier (1 replan + 1 thinking retry). Trials 2–3 settled to 27–34s.
- `multi_step_build` required 1 replan on all 3 trials (vs 0 on OpenRouter 26B). Local E4B model struggles with multi-step planning quality.
- `shell_and_write` is the most stable: 1 step, 0 replans, 0 retries across all trials.
- Local is ~5–24× slower than OpenRouter 26B (dominated by ~7 tok/s decode).

### Medium (3 trials each)

| Test | Pass | Wall (median) | Steps | Failed | Replans | Thinking retries | LLM calls | Prompt tok | Completion tok |
|---|---|---|---|---|---|---|---|---|---|
| `fix_python_syntax_error` | 3/3 | 124.8s (118.6–583.9) | 5 (5–8) | 0 | 0 (0–1) | 1 (1–3) | 9 (9–14) | 4335 (4335–7285) | 882 (743–2286) |
| `fix_missing_include` | 3/3 | 609.1s (569.9–679.3) | 7 | 3 (3–4) | 1 | 3 (2–3) | 15 (14–16) | 10436 (9895–11493) | 4072 (3336–5149) |
| `create_missing_file_then_use` | 3/3 | 29.0s (24.5–171.4) | 3 (2–10) | 0 | 0 (0–1) | 0 | 6 (5–13) | 3223 (2700–7663) | 129 (112–899) |

**Observations:**
- `fix_missing_include` is the clear local bottleneck: 609s median, always 1 replan, 3–4 failed steps, 2–3 thinking retries. Top target for E03 (retry/JSON repair) and E05 (error-class retry).
- Trial 1 outlier pattern persists across all tests — first trial of each runs 2–5× slower (cold model state or unlucky planning). Median excludes these outliers.
- Thinking retries dominate cost: `fix_missing_include` 2–3 per trial, `fix_python_syntax_error` 1–3 per trial.
- `fix_python_syntax_error` improved vs historical (median 125s vs 520s on 2026-04-07 build) — likely `edit` action and byte token fix.
- `create_missing_file_then_use` behaves like an easy test when the planner cooperates (25–29s on trials 2–3).
- Local is ~9–16× slower than OpenRouter 26B on medium tests.

### `fix_missing_include` time breakdown (JSONL analysis)

Per-trial breakdown from run logs, showing where time goes on the worst local test:

| Category | Trial 1 | Trial 2 | Trial 3 | Scaffold-fixable? |
|---|---|---|---|---|
| Planning (incl. replan) | 86s | 112s | 112s | E11 (task-local replan) |
| Failed edit attempts | 132s | 64s | 101s | E03 (JSON repair) |
| Thinking-inflated reads | 304s | 142s | 285s | E05/E06 (error-class retry) |
| Successful steps | ~20s | ~11s | ~22s | irreducible |

The dominant waste pattern: model fails an `edit` (bad JSON or wrong `old` string) → scaffold escalates thinking → next `read` takes 140–253s because thinking tokens consume budget. A failed edit doesn't need more thinking — it needs to read the file first. Error-class branching (E05) + recovery templates (E06: "read before edit retry") would skip the expensive thinking escalation. Full replans produce essentially the same 3-task plan — task-local replan (E11) would cost ~10–20s instead of 69–112s.

~60% of `fix_missing_include` wall time is scaffold-addressable. The remaining ~40% is irreducible model quality (E4B generates bad edit JSON ~60% of first attempts vs near-zero on OpenRouter 26B).

## Hard Bench Post-E03/E05/E06/E11/E16 — 2026-05-03, Local (Gemma 4 E4B Q4_K_M)

Hard suite rerun after five recovery experiments landed (E03 JSON repair + tiered retry, E05 error-class retry policy, E06 typed recovery hints, E11 task-local replan, E16 compiler-aware shell classification). 3 trials per test. Build `a702f395`, M1 16 GB, Phase 6 config.

### Hard (3 trials each)

| Test | Pass | Wall (median) | Steps | Failed | Replans (full) | Local replans | Thinking retries | LLM calls | Prompt tok | Completion tok |
|---|---|---|---|---|---|---|---|---|---|---|
| `replan_build_with_dependency` | 3/3 | 903.7s (784.7–994.1) | 15 (13–28) | 2 (1–4) | 1 (0–2) | 1 (1–2), all ok | 7 (6–7) | 32 (30–53) | 20414 (20138–34062) | 6239 (5877–7436) |
| `replan_fix_wrong_command` | 3/3 | 79.6s (63.5–264.3) | 2 (2–3) | 1 | 0 | 1 (0–1), all ok | 0 (0–3) | 8 (7–8) | 4611 (4018–5008) | 571 (410–2062) |
| `replan_multi_step_recovery` | 3/3 | 88.0s (30.0–174.5) | 3 (3–4) | 0 | 0 | 0 | 0 (0–1) | 6 (6–8) | 4291 (3835–5759) | 577 (149–1284) |

**9/9 PASS — 100% pytest, 100% agent complete.**

**Observations:**
- E11 task-local replans confirmed as a cheap recovery mechanism on hard tests: `build_with_dependency` used local replans in every trial (3.6–7.3s each) and they all succeeded. `fix_wrong_command` used them in 2/3 trials.
- Zero full replans needed on the two faster tests — local replan absorbed the recovery.
- `build_with_dependency` remains the expensive test (~15 min median), dominated by thinking retries (6–7 per trial). This is the E02 (prompt shrink) and E03 follow-up territory — parse-retry thinking inflation is the remaining scaffold bottleneck.
- High variance on `fix_wrong_command` trial 2 (264.3s vs 63.5/79.6s) — 3 thinking retries in that trial vs 0 in the others.
- `multi_step_recovery` is consistently fast (30–175s) with no replans needed.

**Comparison to E01 Harness Baseline (same tests, pre-E03/E05/E06/E11/E16):**

No prior local hard baseline exists in PERFORMANCE.md (E01 baseline only ran easy/medium locally; hard was OpenRouter-only). This is the first local hard harness run.

**Comparison to OpenRouter E01 Hard Baseline:**

| Test | Local median | OpenRouter median | Ratio |
|---|---|---|---|
| `replan_build_with_dependency` | 903.7s | 43.0s | 21× |
| `replan_fix_wrong_command` | 79.6s | 19.6s | 4× |
| `replan_multi_step_recovery` | 88.0s | 9.5s | 9× |

Local is 4–21× slower than OpenRouter 26B on hard tests, consistent with the 5–24× range observed on easy/medium. The gap is widest on `build_with_dependency` where thinking retries compound (~7 per trial locally vs 0 on OpenRouter).

Logs: `/tmp/bench_hard_20260503/`.

## E05/E06 Edit Recovery — 2026-04-26, Local (Gemma 4 E4B Q4_K_M)

Two-trial targeted rerun of `fix_missing_include` after E05/E06:

- E05: structural failures (`edit_failed`, `missing_file`, `timeout`, `missing_tool`, `permission_denied`) skip step-level thinking escalation.
- E06: `edit_failed` and `missing_file` inject short recovery hints into step output.
- Guard: consecutive identical failed edits auto-fail the task and trigger replan instead of burning `MAX_STEPS`.

| Metric | Baseline (median of 3) | Trial 1 | Trial 2 |
|---|---|---|---|
| Wall time | 609.1s | 686.6s (+13%) | 552.9s (-9%) |
| Replans | 1 | 0 | 1 |
| Edit recovery path | 140-253s reads after failed edit | 36s | 36s |
| Failed steps | 3-4 | 1 | 3 |
| Status | PASS | PASS | PASS |

**Finding:** E05/E06 validates on the targeted mechanism but is roughly break-even end-to-end in this sample. Failed edit recovery collapsed from 140-253s thinking-inflated reads to ~36s total (`edit_failed` -> no-thinking `read` -> successful `edit`). Trial 2 also showed repeated post-`edit_failed` retries staying cheap at 7-17s per step until the consecutive failed-edit guard forced a replan.

**New bottleneck:** `ask_llm` internal parse-retry escalation now dominates the slow path. Trial 1 spent 303s on the first shell compile step (73s parse-failed attempt, then 230s thinking retry) and later hit a 217s read step from the same retry ladder. Trial 2 showed the same first-step pattern (109s + 183s = 292s). E05 controls step-level `use_think`; it does not prevent `ask_llm` from escalating thinking internally after JSON parse failures. This makes E03 (JSON repair / stricter retry contract before another model call) the highest-leverage remaining scaffold fix.

**Robustness caveat:** The validated path is edit recovery. Edit mismatch/ambiguous/empty-find failures are deterministic scaffold outcomes, but shell failures still go through `classify_error()` substring heuristics. A compiler diagnostic such as `stdio.h: No such file or directory` can be classified as `missing_file` instead of `compile_error`, which would wrongly skip step-level thinking. This does not invalidate the edit-recovery result, but it narrows the E05/E06 robustness claim to deterministic scaffold-origin errors until shell classification is hardened.

**Verdict:** Keep E05/E06. The edit-recovery classification is structural rather than message-heuristic, and the recovery path is consistently faster. Do not claim a general end-to-end wall-time win until E03 removes parse-retry thinking inflation and shell-origin error classification is made compiler-aware.

## E01 Harness Baseline — 2026-04-26, OpenRouter (Gemma 4 26B-A4B)

First multi-trial harness run (E01). 3 trials per test, all suites. Establishes the baseline for Wave 1+ experiments. 27/27 passed.

### Easy (3 trials each)

| Test | Pass | Wall (median) | Steps | LLM calls | Prompt tok | Completion tok |
|---|---|---|---|---|---|---|
| `create_and_read_file` | 3/3 | 2.5s (2.0–4.0) | 2 | 5 | 2686 | 114 (114–116) |
| `shell_and_write` | 3/3 | 3.9s (3.8–5.2) | 2 | 4 | 2268 | 207 |
| `multi_step_build` | 3/3 | 5.0s (4.4–8.9) | 3 (3–4) | 6 (6–9) | 3370 (3370–5463) | 175 (175–533) |

### Medium (3 trials each)

| Test | Pass | Wall (median) | Steps | Failed | Replans | LLM calls | Prompt tok |
|---|---|---|---|---|---|---|---|
| `fix_python_syntax_error` | 3/3 | 14.0s (8.4–15.1) | 6 (6–8) | 1 | 0 | 9 (9–11) | 4655 (4655–5692) |
| `fix_missing_include` | 3/3 | 38.7s (21.9–41.0) | 8 (7–9) | 2 (2–3) | 1 (0–1) | 12 (12–13) | 10050 (9483–10361) |
| `create_missing_file_then_use` | 3/3 | 2.2s (2.0–2.2) | 2 | 0 | 0 | 5 | 2721 |

### Hard (3 trials each)

| Test | Pass | Wall (median) | Steps | Failed | Replans | LLM calls | Prompt tok |
|---|---|---|---|---|---|---|---|
| `replan_build_with_dependency` | 3/3 | 43.0s (8.3–47.1) | 8 (4–16) | 0 (0–1) | 1 (0–1) | 23 (9–28) | 15875 (5677–19181) |
| `replan_fix_wrong_command` | 3/3 | 19.6s (17.7–29.0) | 3 | 1 | 0 | 6 | 4168 |
| `replan_multi_step_recovery` | 3/3 | 9.5s (7.8–9.8) | 2 | 0 | 0 | 5 | 3201 |

**Observations:**
- `fix_missing_include` has the most variance: 21.9–41.0s wall, 0–1 replans, 2–3 failed steps. This is the test most likely to benefit from Wave 2 experiments (E03 retry/repair, E05 error-class policy).
- `replan_build_with_dependency` shows extreme variance: 8.3–47.1s, 4–16 steps. Trial 1 solved it in 4 steps with no replan; trials 2–3 needed replans and 3–4× more steps. Targets E11 (task-local replan) and E13 (planner critique).
- Zero thinking retries across all hard tests. Easy/medium also zero except 1 on `fix_missing_include` trial 2.
- `create_missing_file_then_use` behaves like an easy test (2.0–2.2s, 2 steps, no failures).

## Phase 6 Caching A/B — 2026-04-25, build `a702f395` (master)

Synthetic + real-workload comparison of Phase 5 (no `--swa-full`, no `--cache-reuse`) vs Phase 6 (`--swa-full --cache-reuse 256`). Full analysis in [caching_analysis.md](caching_analysis.md).

**Synthetic A/B (isolated prefix reuse test):**
- Both configs reuse warm prefix identically via `cache_prompt:true` (in-slot LCP). The `--cache-reuse` flag only affects the across-slot/checkpoint path, which a single-slot back-to-back workload doesn't exercise.
- `--swa-full` decode overhead: ~5% at 8k context, within noise at 500 tokens. No upstream perf bug.

**Easy integration rerun (full logging, each config in isolation):**

| Config | test_create_read | test_shell_write | test_multi_step | Total | Result |
|---|---|---|---|---|---|
| Phase 5 | 90.5s | 65.8s | 331.0s (replan) | 487s (8:07) | 3/3 pass |
| Phase 6 | FAIL (timeouts) | 40.9s | 122.6s (replan) | 974s (16:13) | 2/3 pass |

**Key findings:**
- Model behavior variance (runaway 256-token generation, duplicate write loops) dominates wall time — not config.
- Phase 5's 1:36 baseline was a single lucky run; this rerun took 8:07 with the same config.
- When model cooperates, Phase 6 tests 2+3 ran faster (163s vs 397s) thanks to cache reuse.
- Phase 6 test 1 failed due to cold-start decode slowdown (2.5 tok/s vs 8 tok/s normal), which compounded with runaway generation to trigger 120s transport timeouts.

**Deterministic multi-turn benchmark (3 trials × 7 requests, temperature=0, seed=1):**

| Metric | Phase 5 | Phase 6 | Δ |
|---|---|---|---|
| Total wall (median) | 25.03s | 23.90s | -4.5% |
| Decode tok/s (median) | 10.7 | 10.6 | identical |
| Executor prompt_n (first) | 266 | 266 | same |
| Executor prompt_n (avg rest) | 95 | 94 | same |

Both configs use the same in-slot LCP prefix reuse via `cache_prompt:true`. The `--cache-reuse 256` across-slot path is never exercised in a single-slot sequential workload. No decode penalty from `--swa-full`.

**Verdict:** Phase 6 (`--swa-full --cache-reuse 256`) is the new recommended default. No downside vs Phase 5 — same cache behavior, same decode speed, marginally faster prompt eval. Earlier integration regressions were model output variance, not config effects. See [caching_analysis.md](caching_analysis.md) for full data.

## Model Comparison

Empirical behavior across the three models used during development.

| Model | Architecture | "done" emission | Action looping | Speed | Test status |
|---|---|---|---|---|---|
| **Gemma 4 E4B** (local) | Dense PLE, 4.5B effective / 8B incl. embeddings | Works after cross-task state fix | Duplicate write loops; write content truncation mitigated by `edit` | ~7 tok/s | 9/9 integration tests pass (easy 3:20, medium 20:20, hard 16:49) |
| **Gemma 4 26B A4B** (OpenRouter) | MoE, 25.2B total / 3.8B active | Reliable | Occasional write loops (up to 3x before done); prefers `edit` for fixes | ~1-2s/step | 9/9 integration tests pass (easy 38s, medium 87s, hard 176s) |
| Qwen 3.5 9B | Dense | Unreliable (think-tag issues) | N/A | ~3 tok/s | Legacy, no longer tested |

### Gemma 4 vs Qwen 3.5 — why we switched

Qwen 3.5 defaults to thinking; in this llama.cpp integration its `<think>` blocks caused major reliability issues:
- With `reasoning_format: "deepseek"` (default), thinking goes to `reasoning_content` — but if `max_tokens` is exhausted during thinking, `content` stays empty.
- With `reasoning_format: "none"`, thinking leaks into `content` as literal `<think>` text.
- Required extensive workarounds: think-tag stripping, JSON extraction, higher `max_tokens`, retries.

Gemma 4 E4B has opt-in thinking (rather than default-on):
- No `reasoning_format` parameter needed.
- Think-tag stripping kept as safety net but rarely triggers.
- More reliable JSON output with lower `max_tokens`.
- Its 4.5B-effective dense architecture is faster than the 9B model in this local setup.

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

## OpenRouter Integration Test Results — 2026-04-20 (fast-prototyping re-run)

Full regression on `google/gemma-4-26b-a4b-it` via Parasail. All suites green. Local gemma4 skipped — too slow for medium/hard in a prototyping loop.

| Suite | Result | Wall time | vs 2026-04-08 baseline |
|---|---|---|---|
| Unit (mocked, 159 tests incl. `TestRunLogSink`) | 159/159 pass | 31.0s | +3 tests (run-log sink) |
| `TestOpenRouterEasy` | 3/3 pass | 26.7s | 37.6s → **-29%** |
| `TestOpenRouterMedium` | 3/3 pass | 127.5s | 86.9s → +47% |
| `TestOpenRouterHard` | 3/3 pass | 106.7s | 176.0s → **-39%** |
| `TestPlannerReasoningOpenRouter` | 2/2 pass | 68.0s | (new coverage since 04-08) |

Notes:
- Hard suite materially faster; one test (`test_plan_specificity`) finished in 10s — planner emitted a complete plan on attempt 1, replan #2 returned `tasks: []` and the loop short-circuited to success.
- Medium slower than 04-08 (127s vs 87s) but still well within budgets; no replans observed on the happy path. Suspect provider-side latency variance — re-run if the gap sticks.
- OpenRouter planner wall-time averaged 0.6–1.0s per plan, executor steps 0.6–5.0s (matches the 1–2s/step baseline).

### Tracking / observability — new this run

Added `AGENT_RUN_LOG=path.jsonl` sink in `askme.py`. Each run appends one JSON object per event:

- `run_start` — prompt, working dir, backend, model, limits
- `plan` / `plan_error` — task list + planner wall time per replan
- `tokens` — prompt/completion/total + `thinking` level + retry `attempt` (emitted per `ask_llm` call)
- `step` — task index, step index, action, arg (≤120 chars), ok, error_type, wall time
- `task_complete` / `task_failed` — per-task wall time
- `validation` — `valid` + `reason`/`missing` when gated validator fires
- `run_end` — status (`complete`/`exhausted`), total replans used, total wall time, tail of errors

Backfills the gap called out previously — token/timing/replan history now survives to disk for `PERFORMANCE.md` comparisons without scraping pytest stdout. Sink is off by default (zero overhead when unset) and failure-tolerant (never crashes the run).

Verified with: 3 unit tests (`TestRunLogSink`: lifecycle events emitted, disabled when unset, unwritable path is non-fatal) + one live OpenRouter run to confirm ordering and token capture.

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

## FeatureBench v6 Canary Observations (2026-08-01, revision-3 interface, CoreWeave)

The frozen cell was run once per model under the revision-3 action interface
(issue #17; protocols v5 registered, then re-pinned to CoreWeave as v6 before
any model call). Both attempts produced nonempty, cleanly applying patches and
both exhausted planning attempts without `done`; neither ran the delivered
tests. The earlier v4 and exploratory pi runs used a different serving stack,
so their outcomes are context rather than controlled causal baselines.

| Cell | v4 context | v6 result | Exploratory pi reference |
|---|---|---|---|
| Gemma 4 31B | empty patch, 16/33 `length` | applied but unresolved, **11/13 F2P (84.62%)**, 56/56 `stop`, 21.5 min, $0.032 | 11/13 (84.62%), same failing tests |
| Qwen3.6 27B | empty patch, 0 writes/27 steps | applied but unresolved, 7/13 F2P (53.85%), 1 write/33 steps, 103 s, $0.090 | 10/13 (76.92%) |

1. **Gemma commit-without-validate loop.** The attempt had no truncated model
   responses, but rewrote the same implementation file 18 times, ran zero
   tests, emitted no `done`, and exhausted three planning attempts. Revision 4
   directly guards that observed trajectory with verification pressure,
   rewrite damping, and explicit incomplete-write state; it does not establish
   an outcome improvement.
2. **Qwen observation-dominant trajectory.** One write was selected and
   executed; 23/33 executed actions were reads and eight additional reads were
   skipped as duplicates. The resulting patch remained unresolved at 7/13
   target tests. Because revision 3 bundled transport, budgets, and write
   pressure, this single run does not isolate which mechanism changed behavior.
3. **Empty-action envelopes.** Gemma consumed seven steps on `action: ""`
   parse artifacts. This records overhead in this trajectory; comparison with
   another agent's native tool calls is not a controlled protocol comparison.
4. **Audit-tool provider hardcode.** `canary_audit.py` required SiliconFlow
   literally instead of the protocol's pinned provider, initially marking the
   CoreWeave route invalid. The results change fixed the hardcode; retained
   artifacts then audited as `valid_infrastructure_policy_compliant` with zero
   policy violations in both cells.
5. **Transport-error recovery.** One OpenRouter `ReadTimeout` during a host
   network change was absorbed by the typed retry and the run continued. This
   is one observed recovery, not a reliability estimate.
6. **Interpretation limits.** CoreWeave served Gemma bf16 while earlier
   references used SiliconFlow fp8, and the issue-15 local neutrality bar was
   waived by the maintainer. A registered, matched-provider v7 run is required
   before making revision-4 outcome claims.

## FeatureBench v4 Canary Observations (2026-08-01)

Both registered v4 cells ran their single adapter attempt at execution revision
`72b78c2` (protocols in `tests/featurebench/`, records in
`tests/featurebench/results/`). Harness, routing, and audits were fully green in
both cells; both model attempts produced empty patches and were unresolved.

1. **Gemma 4 31B: output-cap truncation persists under the revision-2
   interface.** 16 of 33 responses finished with `length`; the interface
   converted them into typed `response_truncated` step failures (5 events, 0
   `malformed_action`) instead of v2's malformed-JSON cascade. The failure is
   now legible but not prevented: no write or edit was ever executed before the
   three planning attempts were exhausted (10 steps: 6 tree, 2 shell, 2 read).
2. **Qwen3.6 27B: exploration-only stall — a distinct failure mode.** Zero
   typed parse failures (0 `response_truncated`, 0 `malformed_action`); 41/42
   responses finished `stop` and one reached the output cap without producing
   a parse failure (2,157 completion tokens total). All 27 executed steps were
   observation actions (14 tree, 13 read) with 8 more skipped by
   selected-vs-executed accounting.
   The model never attempted a write in any of the three planning attempts, so
   budgets ran out with an empty diff. Unlike the Gemma cell, nothing blocked
   an implementation write except the model's own action selection.
3. **Infrastructure note.** A full host disk made Docker's VM filesystem go
   read-only during a control eval; the failure was caught pre-inference by the
   control gates, so no model attempt was consumed. Restarting Docker Desktop
   compacted the VM disk and recovered ~16GB.
