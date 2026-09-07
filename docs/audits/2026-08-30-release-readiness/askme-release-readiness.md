# AskMe release readiness — the harness is hiding its own results

`den-run-ai/askme` · `main` @ `fcd5bc0` · audited 2026-08-30 · 18 issues, 6 PRs open

A state-of-the-repo audit ahead of the public write-up. The code is in better shape than the
documentation claims, the documentation claims things the code no longer does, and the single
most interesting result in the project is currently unreproducible from `main`.

| verified here | value | note |
|---|---|---|
| deterministic suite | 1208 passed / 24 skipped | re-run on Linux py3.11; matches PR #97's claim exactly |
| branch coverage | 96.61% | gate is 90%; covers `askme.py` + `actions.py` only |
| runtime diff, last-green → red CI | 0 lines | `eee3fb7…fcd5bc0` touches docs and bench records only |
| local model | dense, not MoE | Gemma 4 E4B QAT Q4_0 · 5.15 GB · dense PLE |

---

## 01 — Correction first: there is no small Gemma 4 MoE

The request asked to confirm something about "small gemma4 moe" reproducing the local M1
setup. That object doesn't exist, and the misconception is written into the talk materials —
which is almost certainly where it came from.

> `docs/gemma4-setup.md:29` — "No small-MoE Gemma 4 exists — **26B-A4B remains the family's
> only MoE** (Q4 ≥13.6 GB before KV cache) and stays off the 16 GB shortlist; no verified
> acceptable llama.cpp run on 16 GB Apple Silicon exists."

The local M1 primary is **Gemma 4 E4B QAT Q4_0** — *dense* PLE, 4.5B effective / 8B including
embeddings, 5.15 GB, iSWA, full Metal (`docs/gemma4-setup.md:21`). The only Gemma 4 MoE is
`google/gemma-4-26b-a4b-it`, which is the *hosted* OpenRouter cell (`llm.yml:33`) and has never
run locally because it can't.

This matters more than a naming slip, because the local-vs-hosted split is the spine of the
talk. And it has already leaked into the public artifacts:

**[Blocker] SPEAKER_NOTES.md calls the local model a mixture-of-experts, three times.**
Lines 13, 24 and 62 describe "Gemma 4 MoE that fits in 16GB of MacBook RAM",
"mixture-of-experts that fits in sixteen gigabytes", and "the small MoE Gemma runs this same
loop". Line 32 adds "the planner sees full state", which `CLAUDE.md` and `askme.py:1774-1786`
both contradict — the planner gets a curated digest. The talk README links this file as the
canonical delivery script. On a project whose entire pitch is evidence discipline, one
checkable architecture error on the front-line document discounts everything else.

---

## 02 — The finding: correct work, recorded as failure

Issue #95, the four consecutive red CI runs, and the E23 done-emission quirk are all one
defect. That unification is new, and it is the strongest thing to write about.

On the seeded webapp-repair cell, local Gemma 4 E4B lands the fix and verifies it in-run
**20/20**. Held-out acceptance passes on every run checked. The harness records **9/20** as
complete and **11/20** as `exhausted`. The work is done; the agent just can't say so. During
this audit the same shape was reproduced on hosted models in CI (section 03, run 140).

### The mechanism, traced in code

A run is only ever recorded finished if the model emits an explicit `done` for every task.
There is no run status "failed" — the loop produces `complete`, `complete_unverified`, or
`exhausted` (`askme.py:4162-4187`, `:2927`, `:4751-4752`), and `exhausted()` deliberately
refuses any reconciliation to success (`askme.py:3840-3856`; a #68 decision, and the right one).

The failure shape: repair lands → test passes → the replanner emits a verify-only task → the
duplicate/stuck guard suppresses that command *because it already succeeded* → the task can't
be satisfied → `task_failed` → replan → budget exhaustion.

**The missing mechanism.** There is *no path* by which recorded evidence of a prior successful
check can satisfy a verification task. The only receipt-backed auto-completion in the codebase
is the narrow #41 C-header repair (`_task_satisfied_by_deterministic_repair`, `askme.py:2459`,
gated at `:3465`). Suppressed steps go only into the model's sliding window
(`StepRecorder.note`, `askme.py:2551-2554`) and never into `all_steps` — so they are invisible
to validation evidence (`_has_new_validation_evidence`), to write visibility
(`askme.py:1730-1746`), and to the final validator (`:3746-3750`, `:3824-3834`). The agent is
left holding only actions guaranteed to be suppressed.

**[Secondary] The guard asymmetry is backwards.** A repeated harmless `read` ends the attempt
(`askme.py:3171-3179`), while a repeated `write` — the genuinely risky one — only escalates
thinking (`askme.py:3109-3112`). That inversion is exactly what produced run 135's
`[stuck_loop] read …` red. Separately, `dup_skip_count` is a single counter shared across
unrelated guard types and only resets on a dispatched action (`askme.py:2592, 4573`), so one
earlier rewrite-loop skip can make the very *first* duplicate shell or read fatal, with no
corrective turn.

**[Correction] Strike #95's "done emitted zero times" line before anyone cites it.** It's an
instrumentation artifact. `_handle_done` (`askme.py:4560-4568`) calls neither
`recorder.executed()` nor `recorder.skip()` and emits no event, so an *accepted* `done` leaves
no JSONL receipt. Residual (selected − executed − skipped) equals completed tasks in all 9
complete runs, and 8 of the 11 exhausted runs also emitted accepted dones.

**Coverage, read carefully.** 96.61% aggregate hides that the uncovered lines cluster in the
completion, obligation and validation machinery — `askme.py` 1483→1488, 1494-1500, 1551,
1576-1583, 1599, 2172-2208, 2248-2272, 3607→3632. The code deciding "is this run complete" is
among the least-exercised in the repo.

---

## 03 — Your CI question: what macOS CI and OpenRouter can actually confirm

Both credentials work. The scheduled reds are not a regression, and no GitHub-hosted runner
can reproduce the laptop — but a useful subset is reachable today.

### The four red LLM runs are not a code regression

Last green was run 130 on `eee3fb7`. Runs 132–135 all failed on `fcd5bc0`. `askme.py` and
`actions.py` are **byte-identical** between them — the only changes are `docs/PERFORMANCE.md`
and retained bench records from the E25 addendum.

The gate is `trials=1` per cell, zero retries anywhere in `llm.yml`, and a pass rule requiring
every trial to be both a pytest pass and an agent completion (`tests/ci_llm_gate.py:336`).
It's also strict on schedule but advisory on push (`llm.yml:212-214`) — identical code is
advisory-green on a main push and hard-red on Monday's cron.

But "flaky gate" isn't the whole story. The failures are **cell-correlated, not random**: both
protocol failures in run 135 were the same cell — `medium/test_fix_python_syntax_error` — on
*both* models, while the harder cell passed on both. And the smoke failure was
`[stuck_loop] read …` → `exhausted`. That is #95's mechanism, sampled by a maximally
sensitive gate.

### Run 140: the reds become rates

Dispatched `llm.yml` on `main` with `trials=5`, both pinned dated snapshots, provider auto.
Run `33295808202`, 31 minutes, $0.11 on the protocol cells. The exit code is red, as the
all-trials rule guarantees; the table is the result.

| Cell | Model | Pytest | Agent complete | Median wall | Cost |
|---|---|---:|---:|---:|---:|
| hard / replan_build_with_dependency | gemma-4-26b-a4b-it | 0/5 | 2/5 | 127.7 s | $0.031 |
| medium / fix_python_syntax_error | gemma-4-26b-a4b-it | 1/5 | 1/5 | 95.8 s | $0.011 |
| hard / replan_build_with_dependency | qwen3.6-27b | 5/5 | 5/5 | 34.8 s | $0.015 |
| medium / fix_python_syntax_error | qwen3.6-27b | 3/5 | 3/5 | 105.2 s | $0.056 |

**What run 140 establishes.**

- **#95 reproduces on hosted models, in CI, with the artifact verified correct.** In the smoke
  job, `test_multi_step_build` wrote `main.c`, compiled it, ran it and printed `AGENT_OK` — the
  log shows it, and the test's `assert_file` passed. Then task 3 of 3, "Run ./main", was served
  `[1] skip (duplicate successful shell)`, then `[2] auto-fail (same successful shell repeated)`,
  then task failed, replan, `exhausted`. The status assert is the one that failed. That is the
  section-02 mechanism, verbatim, on a hosted 26B MoE.
- **The medium cell's claim-failure rate is in the local regime.** Gemma exhausted 4/5, Qwen
  2/5, against local E4B's 11/20. On the Qwen cell every exhausted trial carried local replans
  and thinking retries with `Steps failed 0` — budget spent after the work, on suppression,
  not on errors.
- **The inverse failure appears too.** Gemma on the hard cell: 0/5 pytest, but 2/5 *claimed*
  complete. False completion and false exhaustion, same harness, same run.

Two caveats travel with these numbers. These CI cells are "Berkeley-derived" and, per
`llm.yml`'s own comment (lines 118–127), explicitly *not* a replay of the talk's frozen
four-model matrix — they bound what the 7/8 figure can be read as; they don't overturn it. And
provider routing was `auto`: each Gemma cell was served by seven or eight different providers
across its five trials. Under CLAUDE.md's matched-provider rule that makes run 140 a variance
measurement, not a causal comparison between models. Full record:
`research/06-run-140-trials5.md`.

### No hosted macOS runner reproduces the M1 reference

| Runner | RAM | Silicon | Verdict for the E4B reference |
|---|---:|---|---|
| `macos-26` | 7 GB | M1, 3 vCPU | **No.** 5.15 GB of weights leaves under ~1.5 GB for KV cache, OS and harness. Free on public repos. |
| `macos-26-xlarge` | 14 GB | M2 Pro, 5 vCPU | Holds the model, but 2 GB under the reference machine and a different chip generation. Billed per-minute — and per PR #96, **org-owned repos only**. This repo is user-owned. |
| `macos-26-large` | 30 GB | Intel, 12 vCPU | **No.** No Metal backend, so `-ngl 99` is meaningless — it would measure CPU inference. |

PR #96 already contains all of this, honestly documented, and its `llama-reference` lane takes
a runner label so a self-hosted arm64 box is a one-line swap. Two caveats it doesn't fully
resolve: the lane **has never executed once** (it's `workflow_dispatch`-only and was skipped in
both PR runs), and it installs llama.cpp from Homebrew — current upstream master — while the
laptop is pinned to hand-built `b9618` / `c34b92235`, ~600+ commits behind, with a rebuild
deliberately gated on ggml-org/llama.cpp#26470. A green reference lane proves "the local
backend still works against current upstream," not "this reproduces the laptop."

### What the two passing macOS runs did establish

Runs `33293229569` and `33293545613` proved two real things. **arm64 determinism:** the full
deterministic suite green on macOS for Python 3.10 and 3.14 — exercising the platform-sensitive
paths that matter here: `/tmp` and `/var` resolving through symlinks into `/private`, and a
case-insensitive default filesystem, both bearing on workspace-path and target-identity
normalization. **llama.cpp contract:** Homebrew llama.cpp installed, served Qwen3-0.6B under
Metal, and AskMe's real `LLMClient` decoded a native tool call end to end. Neither says
anything about Gemma 4, Metal performance, or the 16 GB reference.

---

## 04 — Release blockers

Ranked by what a reader arriving from the video would actually hit. Refactor debt is not on
this list.

1. **The blog's best evidence isn't on `main`.** PR #89 is unmerged, so
   `docs/showcase-tasks.md`, `tests/test_webapp_showcase.py` and the seeded T1c cell that
   #95's 20/20-vs-9/20 headline is measured on **do not exist on the default branch**. The E89
   and E31 bench records live only on `origin/eval/e89-web-local`. Anyone who clones `main`
   after the post can reproduce none of it. This is the single biggest unblock.
2. **blog.md's closing section describes a transport that no longer exists.** Lines 149–173
   present the revision-3 *sentinel-framed write transport* as the landed action-interface
   fix, and PR #21 as "open … v7 requalification pending." Commit `5e59dd6` deleted the sentinel
   transport on 2026-08-04; `askme.py:218` pins `ACTION_TRANSPORT = "tools"`; PR #21 merged
   2026-08-02; no v7 record exists (`tests/featurebench/results/` stops at v6). The blog's
   headline external evidence was produced by an interface the shipped code doesn't have. Same
   paragraph mirrored in `talks/…/README.md:59-78`, which the top-level README links.
3. **"Six fixed JSON actions; one action per turn" is wrong — and it's in the recording.** The
   transport is native tool calls with `tool_choice="auto"` (`askme.py:925-932`), and
   `_action_tools()` exposes all eight `ACTION_SPECS` entries — six handlers plus `done`/`fail`.
   The claim appears in `blog.md:28`, `slides.md:480` and `:679`, `SPEAKER_NOTES.md:33`, and
   therefore in `slides.pdf` and the published video. The recording can't be fixed; the text
   can, with a note.
4. **CLAUDE.md asserts truncation recovery the code cannot perform.** Issue #94 is confirmed
   in code, independently. Since `5e59dd6` no live decode path can set
   `content_truncated=True`: the tool-call decoder raises and discards on a truncated payload
   (`askme.py:1117`), and the success path hardcodes a default `ActionTransport()`
   (`askme.py:1125`). The only remaining producer is `askme.py:4498-4502`, whose own comment
   says it serves "patched/injected clients." `content_truncated` is even in
   `_RESERVED_ACTION_FIELDS`, so a model that tried to declare truncation is schema-rejected.
   Safety is intact — truncated writes are discarded, never committed. But CLAUDE.md's
   "Truncation and observation integrity" invariant is written as unconditional live behavior,
   and ~38–50 test assertions drive that machinery through the injection seam only.
   `docs/ARCHITECTURE.md:200-203` already carries the correct caveat; CLAUDE.md doesn't. Two
   green suites currently encode contradictory contracts.
5. **The #41 C-header repair is still on by default** (`askme.py:418`), its ablation still an
   unregistered draft. It fired 11 times on the E23 bench. Any post citing the 9/9 C result or
   the `fix_missing_include` 609s→15.7s speedup without the deterministic-template attribution
   is overclaiming — PERFORMANCE.md already had to be corrected on this point in PR #87.
6. **A stale number is still selling the primary local model.** `docs/gemma4-setup.md:21`
   advertises the promoted QAT model with "E23 full bench: hard 9/9 at −38–66% wall."
   `docs/PERFORMANCE.md:80-83` retracted that on 2026-08-04 — neither E25 arm reproduced it
   (json 7/9, tools 6/9) and "the E23 hard reference should not be cited as current until
   re-benched." The retraction propagated to PERFORMANCE.md and the 08-04 records README and
   **nowhere else**: it survives in `docs/EXPERIMENTS.md:66, :115, :416`.
7. **The front door doesn't work.** README Quick Start (`README.md:37-48`) has no `git clone`,
   no `cd`, and no expected output, and nothing states the project is source-only despite
   `pyproject.toml:21 package = false`. Every documented command fails on a stock machine:
   `pyproject.toml:22` pins `required-version = "==0.12.1"` exactly, so `uv run --locked …`
   errors on any other uv (hit in this container; had to build a venv by hand). No
   full-history secret scan has ever been run (`docs/TODO.md:70` unchecked), and the repo
   already appears public.

---

## 05 — Pull requests

| PR | Disposition | Reasoning |
|---|---|---|
| #96 macOS lanes | **Merge now** | Clean, credential-free, green, coverage-neutral. Real Apple Silicon parity plus an end-to-end llama.cpp tool-call check; honestly documents that no hosted runner fits the reference. Also the *only* way the reference lane becomes dispatchable — `workflow_dispatch` reads workflows from the default branch. |
| #89 showcase tasks | **Merge, then port records** | Touches no runtime files; only README and PERFORMANCE conflict. The release's best demo asset and the exact cell #95's headline rests on. Land it, then cherry-pick the E89/E31 records from `eval/e89-web-local`. |
| #97 upstream audit | **One fix, then merge** | Docs + evidence only, zero runtime change, CI green. The follow-up commit `b45bc87` arrived and its numbers reproduce from the committed `analyze.py`. But the probe README has a duplicated "## Two runs" section whose stale copy restates the very conflation the commit corrects. |
| #47 dependabot | **Merge now** | actions/checkout 7.0.0 → 7.0.1 security bump. |
| #67 README tweak | Rework | Em-dash sweep is wanted, but the diff drops "report" and "through `llama.cpp`" and is dirty against main. |
| #14 pi ablation | **Do not close** | Draft since Aug 1 — but `main` quantitatively cites its pi-ablation numbers as "the preregistered comparison ceiling" in `tests/featurebench/README.md:84-87`, `blog.md:157`, the talk README, DECK_SPEC, and inside frozen v6 result JSONs. The published evidence chain currently terminates at an unmerged draft branch. Land it as labelled archival evidence or remove the citations. |

---

## 06 — Issues, and where the code is ahead of them

Recurring theme: issue text describes intended work, and the code has repeatedly overtaken
it. Several trackers now misrepresent their own subject.

| Issue | Status | What's actually true |
|---|---|---|
| #85 v0.1.0 announcement | **Release gate** | This issue *is* the gate. 13 of its 15 must-haves are unlanded: no tag, no release, `llm.yml` doesn't watch `actions.py` in its push paths, README has no clone/cd and never says source-only, no secret scan, #41 on by default. |
| #95 exhausted-despite-repair | **Live** | Real defect, mechanism traced, reproduced on hosted models in run 140. Strike the "done emitted zero times" line — instrumentation artifact. |
| #94 dead incomplete_write | **Live** | Confirmed in code on every mechanism link. Its own cited grounding files aren't on main either. |
| #27 talk claims | Partly done | Corrections landed in blog.md / README / DECK_SPEC, but **SPEAKER_NOTES.md was never touched** and still carries all seven flagged claims. slides.md still carries three. |
| #31 lifecycle policy | Your call | Implemented (`askme.py:3344-3469`), tested, off by default. Its A/B returned p=1.000 *with the lifecycle guards firing zero times* — the cell never tested the mechanism. Six doc sites still call it an unrun follow-up. |
| #41 / #68 / #69 | Code ahead | #41's boundary conversion landed — the direct `Path.write_text()` and fabricated receipt the body complains about are gone. #68's eight high-confidence boxes all landed but are unchecked. #69's extraction landed. CLAUDE.md's "gated on #62/#63/#64" line is stale. |
| #62 / #63 / #64 / #66 | Unexecuted | No manifest, no runner, unstarted. #63 A.1 and #64 study-A bullet 3 both specify a sentinel-transport baseline arm that was *deleted* — re-running them as written would mean re-implementing removed code. The FeatureBench adapter they depend on can't run on main (ships `askme.py` without `actions.py`). |
| #76–#84 backlog | Not blocking | The one with real weight is **#77's process-group leak**: agent-spawned descendants survive the harness's subprocess kill via inherited pipes, producing two dated E25 build walls of 5791.9s and 16607.2s against a 1200s cap. That corrupts bench evidence. Also #84's `sleep 60` test still burns 30.15s of the 36.67s suite. |

---

## 07 — The claim ledger

| | Claim | Why |
|---|---|---|
| **Cite** | Local dense Gemma 4 E4B lands and in-run verifies the seeded repair 20/20; the harness records it complete 9/20. | Records on `eval/e89-web-local` — merge to main first (blocker 1). |
| **Cite** | Run 140: on the hosted medium repair cell, gemma-4-26b-a4b-it exhausted 4/5 and qwen3.6-27b 2/5; in the same run the smoke build wrote, compiled and ran a correct binary and was recorded `exhausted`. | Once ported to `tests/bench_records/`. Say "variance under auto provider routing," never "Gemma vs Qwen." |
| **Cite** | A preregistered A/B returned a genuine null: heuristic 4/10 vs lifecycle 5/10, Fisher two-sided p=1.000 — and the preregistered *mediators* revealed the lifecycle guards fired zero times, so the cell never tested its own mechanism. | The pilot's apparent effect and its ~70% wall-time penalty both evaporated at n=10. Best methodology story in the repo. |
| **Cite** | The inverse-failure pair on one task family: hosted gpt-oss-20b reported complete without touching the protected test (held-out acceptance caught `/health` still down); local E4B did the repair and couldn't claim it. | False completion and false exhaustion, same harness, both caught by independent acceptance rather than self-report. Run 140 shows both again. |
| **Cite** | Mid-experiment self-correction: the PEG probe's pass rule conflated a grammar defect with hitting the token cap, fixed in a separate `analyze.py` so the committed script provably produced the committed records. 0/64 parser failures. | Recorded as a bounded negative with four named limits — including that arm C never provoked its own condition, so the missing-escape defect is "untested, not cleared." |
| **Caveat** | The 7/8-accepted hosted matrix from the talk. | True as a frozen 2026-07-10 one-trial-per-cell record at `04033b47` — but the related CI repair cell now fails on both models across 5 trials against byte-identical agent code. Present as a dated record, never as current or reproducible. |
| **Caveat** | FeatureBench v6: Gemma 11/13 F2P, Qwen 7/13. | Produced under revision 3's sentinel transport, removed at revision 6. CLAUDE.md's own rule says re-run before citing. One honest sentence, or a re-run. |
| **Caveat** | "1208 tests passing, 96.6% branch coverage." | All 24 skips are live-backend tests whose skip string can't distinguish "not opted in" from "backend down," and coverage measures only `askme.py`/`actions.py` — not the evaluator and bench harness that produce the public records. |
| **Never** | That resume-anchor / `incomplete_write` recovery is a current capability. | Issue #94. Frame as evolution: the sentinel transport solved escaping *and* truncation salvage; native tool calls made escaping obsolete and cost the salvage half. |
| **Never** | That CI reproduces the M1 Gemma 4 setup. | No hosted GitHub runner is both Apple Silicon and ≥16 GB, and the reference lane has never executed. |
| **Never** | The 9/9 C-header result or the 609s→15.7s speedup without attribution. | A hard-coded two-header deterministic template is on by default and fired 11 times on that bench. |
| **Never** | Any wall-clock number from the E25 hard suite. | Infrastructure-corrupted by the #77 process-group leak — 975.4s is the only honest tools build wall. |

**The thesis to lead with.** Not "small models are ready." Lead with: *we built a harness
careful enough to refuse to lie about success, and discovered it was lying about failure
instead.* The agent does the work, verifies it, passes held-out acceptance — and reports
exhaustion 55% of the time locally, 40–80% hosted. Every guard behaved exactly as designed; the
gap is that nothing could say "the criterion is already satisfied, here is the receipt." That's
a better piece than a capability brag, it's fully backed by retained records, and it makes the
evidence discipline the point rather than the disclaimer.

---

## 08 — What to do, in order

1. **Fix the two falsehoods a reader can check in 60 seconds.** SPEAKER_NOTES.md MoE → dense
   PLE (lines 13, 24, 62) and "planner sees full state" → curated digest (line 32). Same PR:
   split CLAUDE.md's truncation invariant into its live observation half and its inert
   write-recovery half. Half a day, zero risk.
2. **Merge #96 and #47.** Both clean and green. #96 also unlocks dispatching the macOS lanes.
3. **Land #89, then port the E89/E31 records from `eval/e89-web-local`.** The biggest single
   unblock — without it the headline result is unreproducible from main.
4. **Port run 140 into a dated PERFORMANCE.md entry before it expires.** The GitHub artifact
   (`berkeley-protocol-logs`, 35 files, id `9727713336`) is retained 14 days, to 2026-09-13.
   It's the first multi-trial hosted record of the claim-failure rate and belongs under
   `tests/bench_records/` with its provider list, or the number can't be cited.
5. **Add the caveat sentences to blog.md.** Sentinel transport removed at revision 6; PR #21
   merged; no v7 record. Free, and required by the repo's own re-run rule.
6. **Decide #41 explicitly and write the decision into the announcement.** Either flip
   `AGENT_COMPILE_REPAIR` off for v0.1.0 or keep it on with a documented "benchmark-shaped,
   ablation pending" note.
7. **Fix the front door.** clone/cd/expected output in Quick Start; state source-only; relax or
   document the exact uv pin; add `actions.py` to `llm.yml` push paths with a contract-test
   assertion; run Gitleaks over full history.
8. **Link the recording.** Nothing in the repo points at the YouTube video
   (https://www.youtube.com/watch?v=N1XoiJGyNpM). Top-level README, talk README, and blog.md
   all need it.
9. **Then, post-release engineering.** #77's process-group fix as a standalone PR (it corrupts
   bench evidence); #95's observability fix plus the guard change with a real user-visible
   regression test; #94 resolved one way or the other; then #80 → #81.

**Don't promise this.** #62's task panel, #63's paired runner and #64's 300–350 paid cells do
not exist. Presenting them as prerequisites would over-commit. If one evaluation is worth
funding, make it a narrowed #66: v7 protocol, requalified controls, two AskMe cells against the
v6 record.

---

*Audited 2026-08-30 against `main` at `fcd5bc0`, with PR branches
`agent/upstream-audit-2026-08-29` (`b107b4b`), `claude/macos-ci-apple-silicon-r6wv2y`
(`27dbfba`) and `eval/e89-web-local`. Test suite, coverage, the green-to-red runtime diff, the
model-identity correction, the #94 mechanism and the SPEAKER_NOTES claims were re-verified
directly; issue and PR triage came from 19 independent agents, each grounded in file, sha or
run id (raw reports in `research/`). No paid local model runs were made. One OpenRouter
dispatch was triggered: `llm.yml` run 140, `33295808202`, trials=5, about $0.12 in total.*
