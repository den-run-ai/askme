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
| —   | E23 | Local revision-3 baseline: QAT Q4_0, `--reasoning off`  | 1    | P0       | S      | done     |
| —   | E21 | gpt-oss-20b low/med/high effort as CI/prototyping model | 1    | P1       | S      | running  |
| —   | E08 | `--checkpoint-every-n-tokens` trial on E4B              | 1    | P1       | S      | archived |
| 2   | E05 | Error-class-specific retry policy                       | 2    | P1       | M      | done     |
| 3   | E06 | Typed recovery templates by `error_type`                | 2    | P1       | M      | done     |
| 4   | E16 | Compiler-aware shell error classification               | 2    | P1       | S      | done     |
| 5   | E03 | Tiered retry contract + JSON repair                     | 2    | P1       | S      | done     |
| 6   | E02 | Shrink `SYSTEM_PLAN` / `SYSTEM_STEP` 25-40%             | 2    | P1       | S      | planned  |
| 7   | E07 | Deterministic verification before LLM validator         | 2    | P1       | S      | partial  |
| 8   | E11 | Task-local replan before full replan                    | 3    | P1       | M      | done     |
| 9   | E17 | Expected-failure task completion semantics              | 3    | P1       | S      | removed  |
| 10  | E18 | Deterministic compile-repair templates                  | 3    | P1       | M      | done     |
| 11  | E04 | Deterministic `search` action (ripgrep)                 | 3    | P1       | M      | done     |
| 12  | E20 | Auto-done after consecutive duplicate-edit skip         | 3    | P1       | S      | removed  |
| 13  | E09 | Model-swap trials: 12B QAT (negative), Q8_0 remaining   | 3    | P2       | S      | partial  |
| 14  | E12 | Split planner vs executor retry budgets                 | 4    | P2       | S      | planned  |
| 15  | E15 | Command-family timeout ladder                           | 4    | P2       | S      | planned  |
| 16  | E19 | Capped low-reasoning task-local replan A/B              | 4    | P2       | S      | planned  |
| —   | E22 | C-header compile-repair ablation (issue #41)            | 4    | P1       | S      | planned  |
| 17  | E13 | Planner critique pass on redundancy-risk plans          | 4    | P2       | M      | planned  |
| 18  | E14 | Typed planner output with `success_criteria`            | 4    | P2       | M      | planned  |
| —   | E24 | MTP A/B on E4B (gated on upstream Metal fixes)          | 4    | P2       | S      | planned  |
| —   | E25 | Native tool-call action transport arm (issue #68)       | 4    | P1       | M      | wired    |
| 19  | E10 | Batched actions (2-3 atomic actions per LLM call)       | 5    | P3       | L      | planned  |

### Wave ordering rationale

Updated 2026-08-03 after the upstream/status audit and the E23 QAT bench (see [gemma4-setup.md](gemma4-setup.md)). Prior updates: 2026-05-03 (experience.md qualitative runs), 2026-04-26 (E05/E06 rerun analysis).

2026-08-03 changes:

- **E23 executed (P0, Wave 1) — new local reference established.** The local binary had silently changed on 2026-06-12 (`a702f395` → `c34b92235` b9618, adding the #23468 cache fix and MTP support) and the installed GGUF (2026-04-06) predated Google's 2026-07-15 weight refresh, so no valid local baseline existed for the revision-3 scaffold. E23 benched the official post-refresh QAT Q4_0 across all three suites — QAT is now the primary model. `--reasoning off` is verified permanent: even the post-refresh template auto-detects as thinking-capable and drains ~192-token action budgets into `reasoning_content`.
- **E02 repriced down.** Its hypothesis assumed #21468 kept every system-prompt token re-processing on every call. Cache reuse has been solid since #23468 (in b9618): warm calls reuse the prefix, so prompt-shrink saves mostly cold-call and completion-side tokens plus planner-budget headroom (the E14 gate). Still worth doing, but the "linear speedup across every call" claim is dead.
- **E03 approach confirmed by upstream inaction.** #22396 (`--json-schema` broken for Gemma 4) was stale-closed 2026-07-05 without a fix, with a re-regression reported in May. Client-side JSON repair remains the durable approach. Retest grammar-based output only after a rebuild past the master PEG overhaul (#24869 et al.).
- **E09 narrowed.** QAT Q4_0 was candidate 1 and is consumed by E23; remaining candidates are Gemma 4 12B Unified QAT (~6.98 GB, the largest dense candidate that fits) and Q8_0. No small-MoE Gemma 4 exists; 26B-A4B remains off the 16 GB shortlist.
- **The E23 QAT stack shows a shifted failure mix, but does not make recovery obsolete.** E06 had no eligible failures and easy+medium had no JSON thinking retries, while E05 still handled `missing_tool` in all three `replan_fix_wrong_command` trials and deterministic C repair fired 11 times across the medium/hard records. The dominant observed local failures were done-emission loops (correct deliverable, no `done`, duplicate skips to exhaustion) plus content drift on whole-file rewrites. Dated evidence is recorded on the E20/E07 dispositions and in ARCHITECTURE.md Current Constraints. Per the issue #68 design, repetition is never acceptance and exhaustion is terminal; mechanism removal remains gated on the planned ablations.
- **E24 added (Wave 4, gated).** MTP self-speculation measured −13% (n-max=1) / −2.7% (n-max=3) on an M1 smoke test — currently a loss, mechanistically explained by llama.cpp #25250 (Metal small-batch mul_mat gap at exactly the draft-verification batch sizes) and #24768 (no adaptive n-max). Gated on either landing.

Updated 2026-05-03 based on experience.md qualitative runs (7 live sessions against local E4B, 2026-04-26/27). Prior update: 2026-04-26 E05/E06 rerun analysis.

- **E05/E06/E16 completed; E03's follow-up is closed.** E05/E06 validated the targeted edit-recovery mechanism. E16 hardened shell error classification. E03 added JSON repair and the tiered retry contract; the historical field-dropping gap from experience.md Run 4 is closed by strict action parsing and the semantics-preserving repair rules completed in PR #82. Repair no longer drops/defaults semantic fields; malformed required fields retry or fail closed.
- **E07 elevated.** Experience.md's strongest finding is that the status field lies in both directions (Run 4: `complete` but broken; Run 6: `exhausted` but correct). E07's deterministic verification directly addresses both. Recommended to run immediately after the E03 follow-up fix.
- **E20 added to Wave 3.** Experience.md Run 6 (783s, 42K tokens) exposed a new failure class: successful edit followed by 8 duplicate-edit loops the scaffold can't break. E20 is a surgical S-effort fix (auto-done after 2+ consecutive duplicate-skipped edits on the same target). Should run before E17/E18 because it addresses a broader pattern (any edit-heavy task, not just expected-failure or compile-repair).
- **E09 stays after E20.** E09 could reduce the underlying edit failure rate from the model side, but E20 and the E03 follow-up target larger observed scaffold bottlenecks first.
- **E11 completed.** Task-local replan confirmed as a cheap filter. Experience.md Runs 1, 5, 7 validated it in live use: 2-5s rescue cost vs ~70s for full replan. E17/E18 target remaining semantic failures. E04 remains in Wave 3.
- **E13 repriced.** Experience.md Runs 4-7 all showed task conflation (model does all work in task 1). E13's planner critique is the structural fix, but at M-effort and Wave 4 priority it's behind the cheaper surgical fixes (E20, E03 follow-up, E07).
- **Wave 4 is effort-ascending then dependency** — E12, E15, and E19 are S-effort standalones. E19 is explicitly gated behind the cheap/no-thinking E11 result. E13 needs redundancy-baseline data from prior waves. E14 is gated on E02 freeing planner-budget headroom.
- **Wave 5 (E10) stays deferred** — redesign-scale; do not start until Waves 2–4 close and the harness can detect reliability regressions.
- **Recommended execution order within waves:** E03 follow-up → E02 → E07 → E20 → E17 → E18 → E09. The E03 follow-up and E07 address the status-field-lying problem (most dangerous for delegated use). E20 addresses the edit-loop exhaustion (most expensive single failure).

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
- **Status.** Done (2026-04-26). Harness discovers tests via `pytest --collect-only`, runs N trials as subprocesses with per-trial `AGENT_RUN_LOG`, parses JSONL, reports median+range for wall time, pytest pass, agent completion status, full/local replans, steps, thinking retries, LLM calls, and tokens. Saves `summary.json` for programmatic comparison. Documented in README.md and CLAUDE.md.

### E23 — Local revision-3 baseline (QAT Q4_0, `--reasoning off`)

- **Context.** Added and executed 2026-08-03. Three things had invalidated all prior local numbers: (1) the llama.cpp binary changed 2026-06-12 (`a702f395` → b9618 `c34b92235`, adding the #23468 cache-reliability fix and MTP support) with no benchmark run since; (2) the installed GGUF (2026-04-06) predated Google's 2026-07-15 weight refresh; (3) b9618's `--reasoning auto` detects the GGUF template as thinking-capable and drains ~192-token action budgets into `reasoning_content`, producing empty/truncated JSON on top of every scaffold metric. Meanwhile the revision-3 scaffold had only OpenRouter/FeatureBench validation.
- **Change.** Pulled the official post-refresh QAT Q4_0 (E09 candidate 1), launched b9618 with the gemma4-setup.md flags including `--reasoning off`, MTP off, and ran all three suites under the E01 harness, 3 trials each. Probed `--reasoning auto` behavior on the fresh template first.
- **Result (2026-08-03).** Done — **QAT Q4_0 promoted to primary local model.** Pytest 22/27, agent-complete 25/27 vs the Apr/May Q4_K_M baseline's 27/27, but at 1.6–39× lower wall time on the agentic workloads: hard 9/9 at −38–66%, `fix_missing_include` 609s → 15.7s, `multi_step_build` replans eliminated, thinking retries 6–7 → 0–3 on `build_with_dependency`. All 5 pytest failures trace to two behavioral quirks: done-emission loops (evidence on the E20 disposition) and content drift on whole-file rewrites (evidence on the E07 disposition). `--reasoning auto` probe: the post-refresh template still triggers thinking — `--reasoning off` is permanent. Full tables: [PERFORMANCE.md E23 entry](PERFORMANCE.md#e23-qat-baseline--2026-08-03-local-build-9618-official-e4b-qat-q4_0). Bench ran the default heuristic step policy; a lifecycle-arm A/B on the done-emission class is the natural follow-up.
- **Code.** `gemma4-setup.md` (model path + flags), no `askme.py` change.
- **Effort.** S.
- **Status.** Done (2026-08-03).

## Prompts / output format

### E02 — Shrink `SYSTEM_PLAN` / `SYSTEM_STEP` 25-40%

- **Hypothesis.** While `--cache-reuse` is broken for Gemma 4 iSWA ([#21468](https://github.com/ggml-org/llama.cpp/issues/21468)), every token in the system prompt is re-processed on every `ask_llm` call. Shrinking them yields a linear speedup across every call in every test.
- **Repriced (2026-08-03).** The premise is stale: #21468 was fixed in April and cache reuse became fully reliable with #23468 (build ~9484; local b9618 includes it). Warm calls now reuse the system-prompt prefix, so the expected yield drops to cold/invalidated-call savings plus planner-token-budget headroom (which E14 is gated on). Keep, but expect single-digit-% wall-time impact, not 10-20%.
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
- **Change (implemented).** Three changes in `ask_llm`:
  1. `_repair_json(text)` attempts mechanical JSON repair (close missing braces, strip trailing commas, strip truncated key-value pairs) before burning a retry. Returns `dict | None`.
  2. Thinking escalation for auto-retries changed: attempt 0 = none, attempt 1 = medium, attempt 2 = none + strict contract. Explicit `think_level` from callers (e.g. validation) is always respected.
  3. On final auto-retry (attempt 2), a strict JSON-only instruction is appended to messages.
- **Result (2026-04-27).** Done. 197/197 unit tests pass (16 new: 10 `TestJsonRepair` + 6 `TestTieredRetryContract`). No upstream fix available — `--json-schema` is broken for Gemma 4 ([#22396](https://github.com/ggml-org/llama.cpp/issues/22396)), grammar+reasoning coexistence has no upstream solution ([#12276](https://github.com/ggml-org/llama.cpp/issues/12276)). Client-side repair is the correct approach.
- **Follow-up gap (2026-05-03, experience.md Run 4).** `_repair_json` can structurally close truncated JSON but strip a required field (`content` for `write` actions). Run 4 wrote `cli.py` with empty content because repair closed the brace around a truncated `content` key. Fix: `_repair_json` should return `None` (let retry escalate) when a required field for the action type is missing. Required fields: `write` → `content`, `edit` → `find`+`replace`, `shell` → `arg`.
- **Follow-up closed (2026-08-03, PR #82).** Repair is now semantics-preserving: `_repair_json` returns `None` rather than dropping or defaulting an action field, required fields come from `ACTION_SPECS`, and a malformed required field retries or fails closed. Matches the 2026-05-03 wave note above; this entry previously still described the gap as open.
- **Code.** `askme.py:205` (`_repair_json`), `askme.py:228` (`_STRICT_JSON_SUFFIX`), `askme.py:231` (`ask_llm` — retry ladder + repair).
- **Effort.** S.
- **Status.** Done (2026-04-27). Pending integration benchmark via E01 harness. Required-field follow-up closed by PR #82 (2026-08-03).
- **Upstream status (2026-08-03).** [#22396](https://github.com/ggml-org/llama.cpp/issues/22396) was stale-closed 2026-07-05 without a fix (a re-regression was reported 2026-05-20 on builds 9244/9253 and got no response). Client-side repair confirmed as the durable approach. Master's grammar/PEG overhaul (#24869/#24839/#24835, post-b9618) is worth a `--json-schema` retest after any rebuild.

## Tools / action model

### E04 — Deterministic `search` action

- **Hypothesis.** Every medium/hard test burns shell calls on `grep`/`find`/`ls` patterns the LLM generates. These are fragile (path truncation, quoting) and token-heavy. A first-class `search` action backed by `rg` is deterministic and cheap.
- **Change.** Add `search` action: `{action: "search", arg: "pattern", path: ".", type?: "py"}`. Wraps `rg --json` and returns bounded match list.
- **Metric.** Number of shell calls used for search in integration tests; shell-generated search failures in logs.
- **Upside.** Reduces LLM call count on file-nav-heavy tasks; improves reliability.
- **Risk.** Adds an action the planner must learn. Mitigate by adding a one-line hint in `SYSTEM_STEP` once E02 lands.
- **Code.** `askme.py:550` (`execute`), `askme.py:189` (`SYSTEM_STEP` — actions list).
- **Effort.** M.
- **Status.** Done (2026-08-01, issue #7). Implemented as a dependency-free pure-Python bounded literal search (no `rg` requirement) plus a bounded `tree` action, with skip rules for VCS/dependency/hidden/binary files and caps (`SEARCH_MAX_MATCHES`/`SEARCH_MAX_CHARS`/`SEARCH_MAX_FILES`, `TREE_MAX_*`). `SYSTEM_STEP` documents both and nudges search/tree over shell grep/find/ls. Deterministic coverage in `tests/test_agent_actions.py`. Integration-side metric (shell-call reduction) still pending an E01 harness run.

### E10 — Batched actions per LLM call **[P3 — redesign-scale]**

- **Hypothesis.** While #21468 is live, per-call overhead dominates. Many step chains are deterministic (write → chmod → run, or read → edit). Emitting 2-3 atomic actions per LLM call halves or thirds per-chain latency.
- **Change.** Allow executor to emit `{actions: [a1, a2, a3]}` in addition to single `{action: ...}`. Execute sequentially; stop on first failure; feed combined result back as one step entry.
- **Metric.** Total LLM calls per integration test; total test time.
- **Upside.** High if it works.
- **Risk.** High. The whole step loop, duplicate guard, step-history shape, and recovery logic are built around one action per step (`askme.py:686` step loop, `askme.py:715` duplicate guard, `askme.py:788` step log). Batched failure attribution breaks error classification. This is a redesign, not a drop-in — do not run before most P1s are done and the harness can measure whether it regresses reliability on medium/hard tests.
- **Code.** `askme.py:686` (step loop), `askme.py:715` (duplicate guard), `askme.py:189` (`SYSTEM_STEP`).
- **Effort.** L.

### E25 — Native tool-call action transport arm (issue #68)

- **Context.** Added 2026-08-04. The ground shifted under the JSON envelope
  path: the post-refresh QAT GGUF ships Google's canonical Gemma 4 chat
  template (2026-07-09, native `<|tool_call>` syntax, changelog "Fixed
  tool-calling loops, turn closures, and thinking content-ordering"), and the
  pinned b9618 build has a dedicated `COMMON_CHAT_FORMAT_PEG_GEMMA4` parser —
  so native tool calling does not depend on the `--json-schema` machinery that
  #22396 left broken. Native tool syntax carries string arguments unescaped
  between `<|"|>` delimiters, removing the JSON-escaping burden that motivated
  the sentinel content transport.
- **Qualification smoke (2026-08-04, local, one-shot probes — not
  outcome-bearing).** Against the documented server flags (`--reasoning off`,
  jinja default-on): 9/9 tool-call responses were structured `tool_calls` with
  valid JSON arguments; multi-line C content with escaped quotes round-tripped
  exactly; multi-step chains continued correctly after `role:tool` results
  (write → compile → run); `done()` was emitted cleanly under explicit
  guidance; `finish_reason=length` returned a structured *partial* tool call
  (name intact, recoverable content prefix); tool definitions cost ~307 prompt
  tokens, fully prefix-cached under `--cache-reuse`. Three sharp edges:
  `tool_choice: "required"` corrupted native delimiters into argument text
  (the arm pins `"auto"`); a satisfied task with no urged next step twice
  produced a 1-token empty reply (handled by normal parse-retry policy); and
  llama.cpp flags a `<|tool_response>` token-metadata bug in the GGUF at load.
  Decode ran ~8.6–11.4 tok/s vs the 13.61 baseline — grammar overhead is
  plausible but unpriced.
- **Change (wired 2026-08-04).** `LLM_ACTION_TRANSPORT`/`--action-transport`
  selects `json` (default, unchanged) or `tools` per run, hash-logged in
  `run_start`/result metadata. The tools arm derives its definitions from
  `ACTION_SPECS`, sends them only on `expect="action"` calls with
  `tool_choice: "auto"`, uses a tools-variant executor prompt, and decodes the
  single tool call into the same envelope validation, retry policy,
  write-budget escalation, and typed classification as the JSON path. Planner,
  task-replan, and validation responses remain JSON on both arms.
- **Metric / decision rule.** Paired local bench (E01 harness, both arms,
  same suites, ≥3 trials, identical budgets/policies) against the E23
  reference: pytest pass + agent-complete rate, wall time, parse retries,
  done-emission-loop incidence, content-drift incidents, decode tok/s. If the
  tools arm is non-inferior on pass rate and not materially slower, flip the
  default and schedule the JSON executor salvage machinery for removal per the
  #68 partial-writes item — after truncated-tool-call recovery obligations are
  requalified. Hosted cells additionally require per-provider tools-support
  qualification before any llm.yml adoption.
- **Risk.** Low while opt-in: the default arm is untouched and both arms are
  deterministic-tested. The known model-side metadata bug and the empty-reply
  edge are recorded above and must be re-checked on any GGUF or build change.
- **Code.** `askme.py` (`ACTION_TRANSPORTS`, `_action_tools`,
  `SYSTEM_STEP_TOOLS`, `_decode_tool_call_reply`, request shaping),
  `tests/test_agent_tool_transport.py`.
- **Effort.** M (wiring done; paired bench pending).
- **Status.** Wired (2026-08-04); default flip gated on the paired bench.

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
- **Follow-up caveat.** The result is robust for deterministic edit scaffold errors. Shell-origin heuristic classification is hardened by E16 (compiler-aware `classify_error`).
- **Risk.** Low for edit-origin failures. Shell-origin classification hardened by E16.
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
- **Change.** Made `classify_error(output, cmd)` command-aware. Compiler-family commands (`cc`, `gcc`, `g++`, `clang`, `make`, `cargo build`, etc.) now prefer `compile_error` for diagnostics even when they contain `No such file or directory`. Non-compiler missing-file shell failures remain structural.
- **Metric.** Classification unit tests for compiler header errors, missing source paths, and non-compiler missing-file commands; no regression in E05/E06 recovery tests.
- **Result (2026-04-26).** Done. Shell execute call site passes `cmd=action["arg"]` to `classify_error()`. Compiler detection uses `_COMPILER_CMD_RE` pattern.
- **Upside.** Hardens E05 against the dangerous misclassification direction: semantic shell error -> structural no-thinking error.
- **Risk.** Low — scoped to compiler command families.
- **Code.** `askme.py:582` (`_COMPILER_CMD_RE`, `classify_error`), `askme.py:652` (shell execute passing `cmd`).
- **Effort.** S.
- **Status.** Done (2026-04-26).

### E11 — Task-local replan before full replan

- **Hypothesis.** Full replan costs ~73s on local (planner thinking budget, ARCHITECTURE.md:185). Most failures are task-local: one task's plan was wrong, the others are fine. A scoped "re-plan this task only" is dramatically cheaper.
- **Evidence (2026-04-26).** All 3 `fix_missing_include` trials replan once at 69–112s. The replan produces essentially the same 3-task plan. Task-local replan would save ~60–90s per trial.
- **Change (implemented).** On task failure, `replan_task()` calls a mini-planner (`SYSTEM_TASK_REPLAN`) with `(failed_task, errors, completed_tasks, policy, missing_tools)` that returns a replacement task description. The call is deliberately cheap: `think=False`, a profile-owned budget (96 tokens in both built-ins), and `max_retries=0`. Inner retry loop (Option A) wraps the per-task body: first failure → local replan → retry with replacement. If replacement also fails or replan returns None, fall through to existing full replan. Original errors are saved and merged back so the full replan sees both failure contexts. `MAX_TASK_LOCAL_REPLANS = 1` prevents infinite loops. Exact duplicates, near duplicates, and passive downgrades are rejected; rejection reasons are logged in `task_local_replan.reject_reason`. Per-attempt execution state (task_done, task_steps, use_think, dup_skip_count) is reset.
- **Metric.** Replan count; total test time on failure-heavy medium/hard tests; `task_local_replan` JSONL events.
- **Upside.** Medium — saves ~60–90s per replan on local, but lower leverage than E05/E06.
- **Risk.** Low. Additive change — any failure falls through to existing behavior. Cap at 1 local attempt.
- **Code.** `askme.py:517` (`SYSTEM_TASK_REPLAN`, `replan_task()`), `askme.py:808` (inner retry loop in `_run_loop()`).
- **Effort.** M.
- **Result (2026-04-26).** Full medium bench after E11: pytest 8/9, agent complete 6/9. `fix_missing_include` exposed the important asymmetry: pytest 3/3 but agent complete 1/3. Mini-replan cost was solved (8/8 calls in 1.46-5.09s vs 64-122s pre-fix), but generated replacements helped only 1/6 times; bad replacements fell through or were rejected. After adding near-duplicate/passive rejection, targeted `fix_missing_include` rerun improved to pytest 3/3, agent complete 3/3, median 203s (range 59-413s) vs prior median 466s (range 234-660s). The mechanism is best understood as a cheap filter before full replan, not a primary semantic recovery worker.
- **Hard bench confirmation (2026-05-03).** 9/9 hard PASS. Local replans were significantly more effective on hard tests than medium: `build_with_dependency` used local replans in every trial (5/5, all ok, 3.6-7.3s each), absorbing failures that would have cost ~70s+ as full replans. `fix_wrong_command` used them in 2/3 trials (all ok). Zero full replans needed on the two faster hard tests. On complex multi-step tasks, no-thinking local replans are an effective primary recovery mechanism, not just a cheap filter.
- **Status.** Done (2026-04-26). 218/218 non-integration tests pass, 4 skipped. Integration artifacts: `/tmp/bench_logs/` and `/tmp/bench_logs_missing_include_rerun/`.

### E20 — Auto-done after consecutive duplicate-edit skip

- **Hypothesis.** When an `edit` succeeds and the model re-emits the same `(file, find_string)` edit, the duplicate-edit-skip guard catches it but only injects a soft observation. The model can reason past this indefinitely, burning all remaining steps on no-op edits. Auto-completing the task after 2+ consecutive duplicate-skipped edits on the same target would break the loop.
- **Evidence (2026-05-03, experience.md Run 6).** Edit succeeded at step 2 (find `return 0` → replace `return 1`). Steps 3-10: 8 duplicate-edit calls on the same `(buggy.py, "return 0")` target, all skipped, thinking=medium from step 5 onward (32-58s per call). Model never pivoted to `shell` or `done`. MAX_STEPS hit → `exhausted`. On-disk deliverable was correct — the framework reported failure on a task that was already done.
- **Change.** Track `last_successful_edit: {file, find_string}` in per-task execution state. When 2+ consecutive duplicate-skipped edits match the same `(file, find_string)`, force `done` for the task. Inject the successful edit evidence into `completed_tasks` for subsequent tasks.
- **Metric.** Run 6-style edit-loop step count; `exhausted` rate on edit-heavy tasks; false-done rate (auto-done when edit was actually wrong).
- **Upside.** High. Directly fixes the most expensive single-run failure (783s, 42K tokens) observed in experience.md. Simple, local, no model changes.
- **Risk.** Low. Only fires after a confirmed successful edit followed by duplicates of the same edit. If the edit was wrong, the model would emit a *different* find_string, not the same one.
- **Code.** `askme.py` duplicate-edit guard area (~askme.py:937), per-task state reset.
- **Effort.** S.
- **Disposition (2026-08-03).** Landed as the edit re-emission auto-done and
  then removed by the issue #68 completion-semantics cleanup: repetition is
  never task acceptance. The duplicate-edit skip and its corrective
  observation remain; completion requires the model's own `done`, and a
  repeated no-op is reported as `stuck`, not success.
- **Evidence (2026-08-03, E23 QAT bench — post-removal frequency data).** On the promoted QAT Q4_0 weights this failure class is now the dominant local one, and it extends beyond edits: 2 of 5 E23 pytest failures were "all steps succeeded, deliverable correct on disk, `done` never emitted, duplicate *read/write* skips until exhausted" (`create_and_read_file` trials 1 and 3), and a third occurrence of the same loop pattern (`create_missing_file_then_use` trial 1, 226.6s) recovered within budget and passed. Under the #68 design these runs correctly stay `exhausted`; the sanctioned counter-lever to evaluate is the lifecycle step policy (`AGENT_STEP_POLICY=lifecycle`), which steers repetition toward verification instead of acceptance — the E23 bench ran the default heuristic arm, so a lifecycle A/B on this failure class is the natural follow-up.

### E17 — Expected-failure task completion semantics

- **Hypothesis.** `fix_missing_include` often starts with a task like "compile to observe the initial failure." The executor treats the failed compile as task failure, even though the task's success criterion is observing the expected error. This burns local replans and sometimes sends recovery down the wrong branch.
- **Evidence (2026-04-26).** In the targeted rerun, trial 1 spent 324s on the "observe initial failure" task before full replan did the useful work. The kept mini-replan changed "observe" to "fix" but still fell to task failure. A deterministic expected-failure completion rule would finish that task immediately after the compile error is observed and move to the edit task.
- **Evidence (2026-05-03, experience.md Run 6).** The debugging prompt ("create buggy code, run to observe failure, edit to fix, run to verify") is a natural expected-failure pattern. The "run to observe initial failure" step would benefit from this mechanism. However, Run 6's primary bottleneck was the edit-loop (see E20), not expected-failure semantics — E17 would save one task's steps, E20 would save the 8 wasted duplicate-edit steps.
- **Change.** Detect task descriptions containing `observe|confirm|initial failure|will fail|read the error` and mark a failed shell step as task-complete when it produces a compile/error diagnostic. Preserve the error evidence for the next task.
- **Metric.** `fix_missing_include` step count, task-local replan count, full replan count, agent_complete rate.
- **Upside.** High for tests/prompts that explicitly ask to observe an expected failure.
- **Risk.** Medium. Must not mark unexpected failures complete. Limit to tasks whose wording clearly expects failure.
- **Code.** `askme.py` task loop around failed shell handling and `task_steps` evidence.
- **Effort.** S.
- **Disposition (2026-08-03).** Landed as the task-text expected-failure regex
  completion and then removed by the issue #68 cleanup: a task-text regex must
  not convert a failing command into completion. The failure evidence stays
  visible to the model as a typed error, and only an explicit `done` claims an
  observe-the-failure task with that evidence.

### E18 — Deterministic compile-repair templates

- **Hypothesis.** Some compile errors map to safe, deterministic source edits. The local model repeatedly struggles to add `#include <stdio.h>` after `printf` diagnostics. A narrow template can fix that class faster than either mini-replan or full replan.
- **Evidence (2026-04-26).** `fix_missing_include` remains the slowest medium test. Even after E11 guards, mini-replan helped 0/2 times in the targeted rerun; full replan did the real recovery.
- **Change.** Add a guarded recovery template for C compile diagnostics: if compile output references `printf`/`stdio.h` or implicit declaration of `printf`, and the target `.c` file lacks `#include <stdio.h>`, insert it at the top before asking the model again.
- **Metric.** `fix_missing_include` median wall time, edit failure count, full replan count, agent_complete rate.
- **Upside.** High for a known benchmark bottleneck; may generalize to a small set of C/Python repair patterns.
- **Risk.** Medium. Keep templates narrow and observable; log template application explicitly.
- **Code.** `askme.py` execute/error-recovery path.
- **Effort.** M.
- **Disposition (2026-08-03).** Landed long ago as the narrow stdio/string
  include rule. The issue #41 boundary work now makes the rule propose a
  normal write action dispatched through the action executor and the one
  recorder — no direct mutation, no fabricated receipt. Whether the rule is
  retained at all remains E22's preregistered decision.

### E22 — C-header compile-repair ablation (issue #41)

- **Hypothesis.** The landed E18-style `#include` repair path either measurably
  raises benchmark-shaped acceptance (retain) or it does not (remove the
  rule). Issue #41 requires the decision be preregistered, not argued post
  hoc.
- **Change.** The #36/#41 boundary conversion landed on 2026-08-03: the rule
  now proposes a normal action dispatched through the executor, so the
  remaining decision is retain-vs-remove only. `AGENT_COMPILE_REPAIR=0` is
  the off arm; default `1` preserves current behavior. Offline arm coverage
  is in `tests/test_agent_recovery.py` (`TestCompileRepairTemplates`).
- **Protocol.** [ablation-compile-repair.md](ablation-compile-repair.md) —
  draft until the owner pins revision/model/route and approves OpenRouter
  spend; no outcome-bearing calls before that registration.
- **Metric.** Held-out acceptance on the C-header task family per arm, plus
  steps, replans, and wall time; gold and harmless controls requalified first.
- **Risk.** Low. The switch is one guarded early return; both arms are
  offline-tested at the same revision.
- **Code.** `askme.py` `_compile_repair_action` gate; no controller branches.
- **Effort.** S (offline prep done; live arms gated on approved spend).

## Verification

### E07 — Deterministic verification before LLM validator

- **Hypothesis.** For goals matching `compile|build|test|run`, checking an exit code or file existence is cheaper and more reliable than the LLM validator. The LLM validator should only fire when deterministic checks are inconclusive.
- **Evidence (2026-05-03, experience.md Runs 4 + 6).** The status field lies in both directions. Run 4: `complete` but `cli.py` is empty (0 bytes) — LLM validation caught it, recovery faked the fix, single-shot prevented re-validation. Run 6: `exhausted` but `buggy.py` is correctly fixed — model looped on duplicate edits, MAX_STEPS hit, framework declared failure on a correct deliverable. Deterministic checks (file exists + non-empty, expected output in shell history) would catch both: Run 4's empty file would fail the check; Run 6's correct output would pass it regardless of step exhaustion.
- **Change.** In `_validate_completion`, run deterministic checks first: if the goal mentions a built artifact, verify it exists + is executable; if it mentions "run X", verify exit 0 was recorded in `completed_step_groups`. Only fall through to the LLM call when checks can't answer. Additionally: allow re-validation after recovery replan when the recovery's task list contains writable/runnable steps (closes the Run 4 single-shot gap).
- **Metric.** LLM validator call count; false-positive/false-negative rate on integration fixtures; status-field accuracy on experience.md-style runs.
- **Upside.** Directly addresses the two most dangerous findings from experience.md. Saves ~0.5-2s per validation on happy path; more reliable.
- **Risk.** Low — keeps LLM validator as fallback.
- **Code.** `askme.py:471` (`_validate_completion`), `askme.py:447` (`_should_validate`).
- **Effort.** S.
- **Disposition (2026-08-03).** Partially landed: `_deterministic_check` runs
  before the LLM validator (empty-artifact and incomplete-write rejection,
  confident-pass short-circuit) and evidence-gated re-validation closed the
  Run 4 single-shot gap. The Run 6-motivated arm — reconciling an
  `exhausted` run to `complete` from a trailing successful shell — was
  landed and then removed by the issue #68 cleanup: exhaustion is terminal,
  and a correct-but-exhausted deliverable is for independent held-out
  acceptance to surface, not a broad in-harness heuristic.
- **Evidence (2026-08-03, E23 QAT bench).** Both lying directions recur on QAT weights. `create_and_read_file` exhausted twice with a correct deliverable on disk (per #68, terminal — held-out acceptance's job to surface). More dangerous is the false *pass*: `fix_python_syntax_error` completed 3/3 while the deliverable's runtime output drifted (`hello` → `Hello` on a whole-file rewrite) — the LLM validator passed a semantically changed program. A goal-output deterministic check (expected output substring from the goal text vs actual shell output) is the still-open E07 arm this motivates.

## Performance / runtime

### E08 — `--checkpoint-every-n-tokens` on E4B — ARCHIVED

Moved to [Archived / rejected](#archived--rejected).

### E09 — Model-swap trials (12B QAT, Q8_0)

- **Hypothesis.** Better weights reduce the root-cause bad-JSON/bad-edit rate; model quality is the lever the scaffold can't reach.
- **Evidence (2026-08-03, E23).** Candidate 1 (official E4B QAT Q4_0) is done and promoted to primary: `fix_missing_include` 609s → 15.7s, hard 9/9 at −38–66% wall, thinking retries near-zero. The pre-QAT evidence line ("bad edit JSON ~60% of first attempts") no longer describes the primary model — remaining candidates are quality plays, benchmarked against the E23 reference.
- **Candidates.**
  1. **Gemma 4 12B Unified QAT** — **DONE, NEGATIVE UNDER THE E4B-FITTED CONTRACT (2026-08-03); GENERIC-PROFILE QUALIFICATION OPEN.** With the then-default local 256-step/512-write-token limits it was 3.6–35× slower than E4B QAT with worse reliability (up to 6–8 JSON retries on `multi_step_build`, easy 6/9, medium partial with 2/3 exhaustion on `fix_python_syntax_error`). The run's requested-model provenance was mislabeled; token events reported the 12B served-model identity, but no artifact hash was retained. This rules out that contract, not the model across capability profiles. See [PERFORMANCE.md E09 12B entry](PERFORMANCE.md#e09-12b-qat-trial--2026-08-03-local-build-9618-gemma-4-12b-unified-qat-q4_0--negative-under-the-e4b-fitted-contract). Scaffold and provenance caveats are recorded there.
  2. **E4B Q8_0** (~8 GB) — remaining candidate, repriced **down**: the 12B result shows raw model quality did not convert to agent reliability under its tested contract on this hardware. Same-model higher precision is a different bet (fewer bad tokens, same style), but expectations are now modest.
- **Change.** For each candidate: download, launch with the E23-validated flags (`--reasoning off`, MTP off), register and pin the requested model/capability profile/served identity, then run easy + medium under E01's harness and compare against the E23 reference.
- **Metric.** Parse-retry count, edit-failure rate, done-emission-loop rate, content-drift incidents, agent_complete rate, total test time; decode tok/s as a guard metric (especially for 12B).
- **Risk.** Low. Model swaps revert trivially. For 12B: decode-speed regression may outweigh quality gains for the agent loop — measure both axes.
- **Code.** `gemma4-setup.md` (model path), no `askme.py` change.
- **Effort.** S per candidate.

### E21 — gpt-oss-20b low/medium/high effort as the OpenRouter CI/prototyping model

- **Context.** OpenRouter-backed CI (`.github/workflows/llm.yml`) and fast
  prototyping currently default to `google/gemma-4-26b-a4b-it` — a larger
  hosted Gemma-family comparison for the local dense-PLE E4B. `openai/gpt-oss-20b` is a comparable-class
  MoE (21B total, ~3.6B active) that was explicitly post-trained for agentic
  tool use and CoT, with a selectable reasoning dial (harmony `low`/`medium`/
  `high`) instead of Gemma's hybrid on/off reasoning.
- **Hypothesis.** At `low` effort, gpt-oss-20b matches or beats the Gemma 4
  26B-A4B pass rate on the easy/medium/Berkeley cells at lower cost and similar
  wall time; `medium`/`high` buys back hard-suite failures at a measurable
  token/wall premium. If `low` holds, CI smoke and prototyping get a cheaper,
  effort-tunable default; if not, the effort ladder still gives a controlled
  reasoning-dose axis Gemma cannot express.
- **Evidence (2026-08-02, unauthenticated OpenRouter catalog probe).**
  gpt-oss-20b is live and routable: 131K context, `reasoning`/`reasoning_effort`
  advertised, $0.03/M prompt + $0.13/M completion vs Gemma 4 26B-A4B's
  $0.07/$0.34 (~2.4× cheaper per token; the gap narrows as effort raises
  completion-token share). 12 endpoints; the repo-default Parasail serves it
  (88.1% 30-min uptime), CoreWeave/DeepInfra at 100% uptime and the lowest
  prices. Caveat: gpt-oss cannot disable reasoning, so the harness's
  `reasoning.enabled=false` contract silently degrades to provider-default
  effort — a baseline knob is required for a controlled comparison.
- **Change.** `OPENROUTER_REASONING_EFFORT` baseline in `askme.py` (merged as
  `max(baseline, gated level)`; `off` policy pins to the baseline; budget
  floors 1024/1536/2048), `--reasoning-effort` on `tests/bench_harness.py`,
  `requested=expected@effort` cells in the llm.yml Berkeley job (e.g.
  `openai/gpt-oss-20b=openai/gpt-oss-20b@low`), effort-qualified cells in
  `tests/ci_llm_gate.py`.
- **Metric.** Per E01 harness: pytest pass + agent-complete rate, wall time,
  completion tokens, `openrouter_cost`, thinking retries — cells
  `gemma-4-26b-a4b-it` (control) vs `gpt-oss-20b@{low,medium,high}` on the
  easy suite plus the two Berkeley protocol cells, ≥3 trials.
- **Upside.** ~2.4× cheaper CI cells and a reasoning-dose axis for experiments
  like E19; a hosted default that stays in the same active-parameter class as
  the local model.
- **Risk.** Low-medium. Reasoning tokens bill as completion tokens even at
  `low` — cost parity must be measured, not assumed from unit prices. JSON
  envelope discipline under harmony reasoning is unproven in this harness;
  the existing `reasoning`-field fallback and E03 repair should absorb leaks,
  and the strict-retry contract intentionally keeps the baseline effort.
- **Code.** `askme.py` (`OPENROUTER_REASONING_EFFORT`, `_merge_effort`),
  `tests/bench_harness.py`, `tests/ci_llm_gate.py`,
  `.github/workflows/llm.yml`.
- **Effort.** S.
- **Status.** Running (2026-08-02). Wiring + unit coverage landed; live cells
  pending an `OPENROUTER_API_KEY` run:
  `python3 tests/bench_harness.py --backend openrouter --suite easy --model openai/gpt-oss-20b --expected-served-model openai/gpt-oss-20b --reasoning-effort low`
  (repeat per effort, plus the control without `--reasoning-effort`), or
  dispatch llm.yml with
  `models: google/gemma-4-26b-a4b-it=google/gemma-4-26b-a4b-it-20260403,openai/gpt-oss-20b=openai/gpt-oss-20b@low,openai/gpt-oss-20b=openai/gpt-oss-20b@medium,openai/gpt-oss-20b=openai/gpt-oss-20b@high`.
  Decision rule: adopt for CI smoke only if every `@low` cell passes at cost
  ≤ the Gemma control and wall time ≤ 1.5× control; otherwise keep Gemma as
  default and retain the effort axis for experiments.

### E12 — Split planner vs executor retry budgets

- **Hypothesis.** `MAX_LLM_RETRIES=2` is shared by both. Planner benefits from thinking escalation; executor benefits from more aggressive contract-switching (E03) over thinking. One knob isn't right for both.
- **Change.** Split into `MAX_PLANNER_RETRIES` (default 2, thinking-escalating) and `MAX_EXECUTOR_RETRIES` (default 2, contract-escalating per E03). Callers pick the ladder.
- **Metric.** Per-caller retry distribution; total test time.
- **Upside.** Low-medium, but makes the system easier to tune.
- **Risk.** Low. Gated on E03 landing first — otherwise no meaningful contract ladder to run on.
- **Code.** `askme.py:202` (`MAX_LLM_RETRIES`), `askme.py:205` (`ask_llm`).
- **Effort.** S.

### E19 — Capped low-reasoning task-local replan A/B

- **Hypothesis.** Medium/high reasoning made task-local replans too expensive (64-122s pre-fix), but a genuinely capped low-reasoning variant might improve replacement quality without giving back the cost win.
- **Evidence (2026-04-26).** No-thinking E11 replans are cheap (1.46-5.09s) but only helped directly 1/6 times in the full medium bench and 0/2 times in the `fix_missing_include` rerun. The value is currently cheap rejection/fallback, not primary recovery.
- **Evidence updated (2026-05-03, hard bench).** Hard tests tell a different story: no-thinking local replans succeeded 5/5 on `build_with_dependency` and 2/2 on `fix_wrong_command`. This weakens the premise that replacement quality is too low — complex multi-step tasks benefit more from task-level rewording than medium single-fix tasks. The case for adding reasoning overhead is weaker given the hard bench data.
- **Change.** Add an env-gated A/B mode for `replan_task()` only: low reasoning, `max_retries=0`, hard `max_tokens` cap around 128-192, no escalation. Compare against default no-thinking mode on `fix_missing_include` and `fix_python_syntax_error`.
- **Metric.** Replacement helped rate (`task_local_replan` followed by `task_complete`), local replan wall time, agent_complete rate, full replan count.
- **Upside.** Could improve mini-replan quality while preserving cheap failure behavior.
- **Risk.** Medium. Any local thinking path can inflate wall time. Abort if p95 local-replan wall exceeds ~10s.
- **Code.** `askme.py:replan_task()`, `tests/bench_harness.py` local-replan correlation metrics.
- **Effort.** S.

### E24 — MTP speculative decoding A/B on E4B **[gated on upstream Metal fixes]**

- **Context.** Added 2026-08-03. Native Gemma 4 MTP landed upstream ([#23398](https://github.com/ggml-org/llama.cpp/pull/23398), [#24282](https://github.com/ggml-org/llama.cpp/pull/24282), both in b9618); the official E4B drafter (98.7 MB) is downloaded. A 2026-08-03 smoke test measured **−13% decode at `--spec-draft-n-max 1` and −2.7% at n-max 3** vs 13.61 tok/s baseline — MTP is currently a small loss on M1.
- **Hypothesis.** The loss is upstream-mechanical, not architectural: draft verification runs at batch sizes 4–16, exactly where Metal's mul_mat path has ~2x headroom ([#25250](https://github.com/ggml-org/llama.cpp/issues/25250)), and there is no adaptive n-max ([#24768](https://github.com/ggml-org/llama.cpp/issues/24768)). When either lands, MTP should flip positive for the agent's long JSON generations.
- **Change.** After the gate lands: A/B `--spec-type draft-mtp --spec-draft-n-max {1,3}` vs no-MTP on easy + medium under the E01 harness, against the E23 reference. Measure end-to-end task success and wall time, not just decode tok/s. Verify JSON quality — [#25072](https://github.com/ggml-org/llama.cpp/issues/25072) reports format corruption specifically under MTP.
- **Metric.** Wall time, agent_complete rate, parse-retry count, decode tok/s.
- **Upside.** Potentially the largest local decode lever if the Metal small-batch gap closes (~2x headroom documented upstream).
- **Risk.** Low — server-flag A/B, trivially revertible. Format-corruption risk (#25072) is why agent-level metrics gate adoption, not raw tok/s.
- **Code.** `gemma4-setup.md` (server flags), no `askme.py` change.
- **Effort.** S.
- **Status.** Planned, **gated** on #25250 or #24768.

## Planning

### E13 — Planner critique pass on redundancy-risk plans

- **Hypothesis.** Redundant tasks are a real cost. PERFORMANCE.md:57 documents a **3-task** plan on `fix_python_syntax` where task 2 was already satisfied by task 1 — 370s wasted. A ≥4-task trigger would miss this case entirely. The right trigger is a redundancy-risk signal, not task count.
- **Evidence (2026-05-03, experience.md Runs 4-7).** Task conflation is the dominant pattern across all hard runs: the model treats task 1 as license to do all work, then runs out of steps. Runs 4, 5, and 7 all hit this — Task 1 ("Create file A") wrote files A, B, and sometimes C within its step budget. The framework rescues via E11 or full replan, but at 60-400s latency cost. A planner critique that flags multi-file plans where tasks mention different files (but the model may conflate them) could preemptively split or merge tasks.
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

- experience.md — qualitative findings from 7 live runs as subagent (2026-04-26/27). Not in the current tree: the file predates the 2026-07-11 history squash and is retained only at the repository root on the `archive/main-pre-pr1-squash-20260711` branch (commit `7449777`). The findings cited inline above (Runs 4 and 6 especially) are summarized where referenced.
- [PERFORMANCE.md](PERFORMANCE.md) — benchmark history; completed experiments land here.
- [ARCHITECTURE.md](ARCHITECTURE.md) — design decisions + current constraints.
- [gemma4-setup.md](gemma4-setup.md) — server config; runtime experiments (E08, E09) land here.
- [CLAUDE.md](../CLAUDE.md) — agent authoring guidance.
