# Protocol — PR #89 web suite (T1a/T1b/T1c) on local Gemma 4 E4B

Registered 2026-08-04, before any outcome-bearing model call.

## Question

Does local Gemma 4 E4B QAT Q4_0 complete the three showcase web-app tasks
from PR #89 (`TestWebLocal`), on the **current tools-only runtime**?
PERFORMANCE.md's Web Showcase entry states "Local Gemma 4 E4B remains
unmeasured on this suite"; this fills that gap.

## Execution revision

- Base: `main` @ `fcd5bc0` (tools-only, after PRs #91/#92/#93)
- PR head: `origin/claude/askme-small-llm-tasks-xb7l2w` @ `56608b1`
- Eval revision: merge commit `4e528a6` on branch `eval/e89-web-local`
  (worktree; docs-only conflicts in README.md + docs/PERFORMANCE.md resolved
  by keeping both sections — `askme.py`/`actions.py`/test code merged clean)
- Runtime code untouched by the merge; PR #89 adds tests/docs/CI only.

## Model + server

- `models/gemma4-e4b-qat/gemma-4-E4B_q4_0-it.gguf`, alias `gemma-4-e4b`
- Persistent llama-server (`~/.local/bin/askme-llama-server.sh`):
  `-ngl 99 --ctx-size 16384 --flash-attn on --cache-type-k q4_0
   --cache-type-v q4_0 --swa-full --cache-reuse 256 --reasoning off -np 1`
- Capability profile: `legacy-e4b-m1-16k-v1` (step_write_tokens=512)
- Expected served model pinned: `gemma-4-e4b`
- Reasoning policy: `gated` (bench_harness default; matches E23/E25 locals)
- Step policy: default heuristic
- Action transport: tools (only transport on main since #92)

## Cells

One cell per task, `tests/bench_harness.py --suite web --backend local`:

| Cell | Test | Budgets (replan/task/step) | goal_context_chars |
|---|---|---|---|
| T1a | `test_webapp_build_status_service` | 1/3/8 | 900 |
| T1b | `test_webapp_notes_round_trip` | 2/5/8 | 900 |
| T1c | `test_webapp_fix_failing_health_check` | 1/3/8 | default 300 |

## Plan

1. Pilot: 1 trial per cell — feasibility/config check, not a result.
2. If the pilot runs cleanly, matrix: 3 trials per cell (9 runs) as the
   recorded measurement.

## Amendment 1 (2026-08-04, after the pilot, before the matrix)

The pilot exposed two things that change the matrix design:

1. **`bench_harness`'s fixed 1200 s per-trial cap decided the outcome instead
   of the agent's budgets.** T1a was still executing inside its final plan at
   1192 s when pytest was killed. For the matrix the cap is raised via a new
   `BENCH_TRIAL_TIMEOUT` env override (eval-only worktree change, documented,
   not proposed upstream) so each run ends on completion, failure, or budget
   exhaustion. Runs that end on the clock are reported as `TIMEOUT`, never as
   an agent outcome.
2. **The 512-token write budget binds before task difficulty does.** Every
   `app.py` write attempt truncated (`finish_reason=length`) and each executor
   step burned a 256 → 512 → retry ladder. A profile arm is therefore added,
   changing one registered axis only:

   - **Arm A** — `legacy-e4b-m1-16k-v1` (step 256 / write 512): the registered
     local contract, as in E23/E25.
   - **Arm B** — `generic-feature-scale-v1` (step 4096 / write 8192): same
     model, same server, same prompts; tests whether the write budget rather
     than the model is what fails these tasks.

   Arm B is exploratory and labeled as such; it is not a claim about the
   shipped local contract.

## Amendment 2 (2026-08-04, owner decision, ~15 min into Arm A)

Arm A was **stopped after ~1 trial-worth of compute and its partial logs
discarded** — owner call: the pilot had already established the truncation
bind on all three tasks, and three more confirmations at 512-token writes
cost ~2 h of the single-slot server for no new information.

Consequences for how this is reported:

- The **pilot (1 trial per cell) is the only Arm A evidence** and is reported
  as such — a single trial, not a reliability estimate. It is not upgraded to
  a "3-trial result" by the existence of Arm B.
- **Arm B (`generic-feature-scale-v1`, 3 trials per cell) becomes the primary
  measurement.** It answers "can local E4B do these tasks when the write
  budget is not the bottleneck?" — but it is measured on a **non-shipping**
  local contract. Any claim about AskMe's shipped local behavior must cite
  the pilot, not Arm B.
- No selective rerun: the pilot's three negative trials stay in the record.

## Recorded per trial, kept distinct

- pytest pass/fail (includes held-out acceptance: `app.py` relaunched on a
  fresh ephemeral port over loopback HTTP)
- agent termination status (`complete` / `exhausted` / `failed`)
- wall time, replans, steps
- failure class where identifiable (done-emission loop, incomplete_write,
  content drift, false completion caught by acceptance, smoke-script hang)

## Decision rule

Descriptive, not pass/fail gated: report per-cell pytest and agent-complete
counts with median + range wall time over 3 trials. Any single trial is a
health check, not a reliability estimate. Negative trials are retained; no
selective rerun. A cell where the agent claims completion but held-out
acceptance rejects is reported as a false completion, not a pass.
