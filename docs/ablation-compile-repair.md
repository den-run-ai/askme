# Preregistration draft — C-header compile-repair ablation (issue #41, E22)

**Status: DRAFT — not registered.** This document becomes a registered
protocol only when the owner fills every `PIN:` field below and approves the
OpenRouter budget. Until then, no outcome-bearing model calls may cite it.
Per [CLAUDE.md](../CLAUDE.md), a changed interface after registration
requires a new protocol, not a rewritten result.

## Question

Does the deterministic C-header repair rule (`_compile_repair_action`, which
since 2026-08-03 only proposes a normal write action that the controller
dispatches through the #36 action executor and single recorder) measurably
improve benchmark-shaped task acceptance, relative to the same revision with
the rule disabled?

## Decision rule (fixed before any trial)

- **Remove** the repair rule if the repair arm's held-out acceptance
  advantage over the off arm is ≤ 0 percentage points across the registered
  trial set.
- **Retain** if the advantage is ≥ 20 percentage points. Per the
  [#41 coordination comment](https://github.com/den-run-ai/askme/issues/41#issuecomment-5160211863),
  a retained rule must emit an ordinary action through the #36
  dispatch-and-record path — never a direct write or fabricated receipt;
  that boundary conversion landed on 2026-08-03, so retention keeps the
  landed action-producing rule as-is.
- An advantage strictly between 0 and 20 points is **inconclusive**: report
  it with per-arm variance, keep the flag, and file the follow-up decision on
  #41 rather than stretching this protocol.

Scope limit: this decides only the fate of this repair path. It is not an
official benchmark score, a model-family conclusion, or evidence of general
feature readiness.

## Arms

Both arms run at one pinned revision; the only difference is the environment
switch.

| Arm | Setting | Behavior |
|-----|---------|----------|
| A (repair on) | `AGENT_COMPILE_REPAIR=1` (default) | Current behavior: the guarded `#include` rule proposes a write action dispatched through the action executor, then the deterministic shell retry; both recorded through the single recorder |
| B (repair off) | `AGENT_COMPILE_REPAIR=0` | `_compile_repair_action` returns `None` before any proposal; compile errors surface to the model as ordinary typed failures |

Offline qualification of both arms (no model calls) already exists:
`tests/test_agent_recovery.py::TestCompileRepairTemplates`, including
`test_flag_disables_repair_before_any_proposal` and
`test_flag_off_arm_surfaces_the_compile_error_unrepaired`.

## Execution discipline (binding on every paid trial)

- **Fresh process per trial.** `COMPILE_REPAIR_ENABLED` is resolved from the
  environment at import time, so a long-lived process ignores later `env`
  changes. Each trial launches a new `python3 askme.py` process with the
  arm's environment set explicitly; arms must never share a process.
- **Provenance verification.** Before a trial counts, the runner checks that
  its `run_start` JSONL event's `compile_repair` matches the intended arm.
  A mismatch invalidates the trial, is retained as typed evidence, and the
  trial is re-run in a fresh process — it must not be relabeled.
- **Counterbalanced order.** Trials run in alternating blocks that also
  alternate the starting arm (ABBA BAAB …, or an equivalent predeclared
  schedule) rather than a fixed ABAB sequence, so provider drift and
  time-of-day effects cannot align with one arm.
- **Infrastructure and evaluator failures are predeclared, not judged
  post hoc.** A trial that fails for infrastructure reasons (transport
  errors exhausting retries, provider outage, runner crash, evaluator
  crash) is excluded from both arms' acceptance denominators, retained as
  distinct evidence, and replaced by a fresh trial in the same schedule
  slot. An evaluator failure on a control halts the run for
  requalification. Agent-side outcomes — exhaustion, timeouts inside
  budget, malformed actions, wrong artifacts, false completion — are
  ordinary negative results and stay in the denominators.

## Task family and controls

- **Primary tasks:** a C-header task set — `.c` sources using `printf`/string
  functions without the header, compiled by a task command, with acceptance
  judged held-out and never returned to the agent. Candidate lineages differ
  and must be pinned by exact artifact, not by shape: the local
  `fix_missing_include` integration task lives in
  `tests/test_agent_integration.py`, while the existing FeatureBench canary
  protocols pin a different (Seaborn/Python) task. Registration names the
  exact task IDs, fixture/dataset revision, runner, and acceptance command
  (`PIN: task set` / `PIN: evaluator` below); operators must not substitute a
  differently-shaped workload under the same protocol name.
- **Gold control (both arms, before inference):** the pinned evaluator run
  against a known-correct solution artifact (`predictions: gold` style) must
  resolve, proving the dataset/evaluator infrastructure itself. A repair
  trigger is not a gold control.
- **Trigger check (arm A):** a task where the repair is known to fire,
  requalified offline at the pinned revision — a manipulation check that the
  treatment is active, recorded separately from the gold control.
- **Harmless non-empty control (both arms):** a compiling C task with all
  headers present; the repair must not fire and both arms must pass.
- Controls that fail requalification block registration.

## Registration checklist (owner)

- `PIN: execution revision` — commit hash containing the flag and both
  offline arm tests; runbook verified executable at exactly that revision.
- `PIN: task set` — exact task IDs and fixture/dataset revision (file hashes
  or dataset tag) for the primary tasks and every control.
- `PIN: evaluator` — the exact held-out acceptance command/runner and its
  revision; the same evaluator judges both arms and is never shown to the
  agent.
- `PIN: model` / `PIN: provider route` — identical for both arms;
  `OPENROUTER_ALLOW_FALLBACKS` and reasoning policy/effort frozen and
  reported (for always-on reasoners, `off` pins the declared baseline).
- `PIN: trials` — planned: 10 per arm on the primary task (report exact
  count); matched interleaved execution order (ABAB…) to spread provider
  drift.
- `PIN: budgets` — per-run step/replan/token bounds (defaults unless stated)
  and total spend ceiling.
- `PIN: log retention` — `AGENT_RUN_LOG` JSONL per trial, retained under
  `tests/featurebench/results/` conventions; negative runs, malformed
  actions, exhaustion, timeouts, and evaluator errors preserved as distinct
  evidence.

## Reporting

Per arm: acceptance rate with variance, selected/executed/skipped step
counts, replans, wall time, typed-error mix, and `deterministic_repair` /
`deterministic_retry` receipt counts (arm B must show zero). Every trial's
`run_start` JSONL event records the resolved arm as `compile_repair`; receipt
counts alone cannot identify the arm, because arm A also shows zero whenever
the trigger never fires. Summaries must
stay arithmetically consistent with retained JSONL records. Results land in
[PERFORMANCE.md](PERFORMANCE.md) as a dated entry and close E22 in
[EXPERIMENTS.md](EXPERIMENTS.md); the decision lands on issue #41.
