# Preregistration draft — C-header compile-repair ablation (issue #41, E22)

**Status: DRAFT — not registered.** This document becomes a registered
protocol only when the owner fills every `PIN:` field below and approves the
OpenRouter budget. Until then, no outcome-bearing model calls may cite it.
Per [CLAUDE.md](../CLAUDE.md), a changed interface after registration
requires a new protocol, not a rewritten result.

## Question

Does the deterministic C-header repair path (`_try_compile_repair`, the
tracked #41 exception that mutates the workspace outside the #36 action
handlers) measurably improve benchmark-shaped task acceptance, relative to
the same revision with the path disabled?

## Decision rule (fixed before any trial)

- **Remove** the special repair path if the repair arm's held-out acceptance
  advantage over the off arm is ≤ 0 percentage points across the registered
  trial set.
- **Retain** if the advantage is ≥ 20 percentage points; per the
  [#41 coordination comment](https://github.com/den-run-ai/askme/issues/41#issuecomment-5160211863),
  retention means the rule is converted to emit an ordinary `edit` action
  through the #36 dispatch-and-record path — never a direct write or
  fabricated receipt.
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
| A (repair on) | `AGENT_COMPILE_REPAIR=1` (default) | Current behavior: guarded `#include` insertion + deterministic shell retry, recorded through the single recorder |
| B (repair off) | `AGENT_COMPILE_REPAIR=0` | `_try_compile_repair` returns `None` before any mutation; compile errors surface to the model as ordinary typed failures |

Offline qualification of both arms (no model calls) already exists:
`tests/test_agent_recovery.py::TestCompileRepairTemplates`, including
`test_flag_disables_repair_before_any_mutation` and
`test_flag_off_arm_surfaces_the_compile_error_unrepaired`.

## Task family and controls

- **Primary tasks:** the C-header task family that motivated the path
  (FeatureBench canary lineage, `fix_missing_include` shape — a `.c` source
  using `printf`/string functions without the header, compiled by a task
  command; acceptance judged by the held-out evaluator, never returned to the
  agent).
- **Gold control (arm A):** a task where the repair is known to trigger;
  requalify at the pinned revision before inference.
- **Harmless non-empty control (both arms):** a compiling C task with all
  headers present; the repair must not fire and both arms must pass.
- Controls that fail requalification block registration.

## Registration checklist (owner)

- `PIN: execution revision` — commit hash containing the flag and both
  offline arm tests; runbook verified executable at exactly that revision.
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
`deterministic_retry` receipt counts (arm B must show zero). Summaries must
stay arithmetically consistent with retained JSONL records. Results land in
[PERFORMANCE.md](PERFORMANCE.md) as a dated entry and close E22 in
[EXPERIMENTS.md](EXPERIMENTS.md); the decision lands on issue #41.
