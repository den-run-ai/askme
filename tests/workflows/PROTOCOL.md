# Native workflow protocol — Phase 1

**Protocol revision:** 4 (2026-08-02). Revision 4 adds the validate-after-write
executor policy (from the v6 canary's commit-without-validate rewrite loop):
verification pressure after repeated same-target full writes, `rewrite_loop`
damping skips that persist across task-local and full-replan boundaries,
`unvalidated_write` and `incomplete_write` replan flags, and
the three post-merge Codex P2 fixes to the revision-3 write-forcing mechanics
(success-only commit counting, failed-task-scoped intent classification,
passive-phrasing
exemptions in the write-task regex). `no_write_executed`,
`unvalidated_write`, and task-local `failed_steps` are task-scoped even when
the failed task dispatched zero actions. Incomplete artifacts are run-scoped,
including zero-byte pending truncations; `incomplete_write_target` carries the
frozen, action-ready canonical recovery path when a symlink is later retargeted,
while `incomplete_write_append_allowed` says whether append recovery is safe;
the exact target is the sole exception to the executor's relative-path rule, and
empty-overwrite retries retain every observed append referent guard. Unresolved
truncated writes block
`done` and deterministic
reconciliation, empty task lists are invalid plans, and a failed final
validation requires new successful mutation/shell evidence before rechecking.
Any outcome-bearing run under revision 4
requires a newly registered **v7** canary protocol; the recorded v6 outcomes
remain revision-3 results. Revision 3 (2026-08-01) changed the AskMe action
interface used by outcome-bearing runs (issue #15, from the v4 canary and pi
ablation findings): sentinel-framed `write` content transport
(`<<<CONTENT`/`CONTENT>>>` after the action JSON — no JSON-string escaping,
truncation keeps complete lines and recovers via a resume anchor plus chunked
`append`), backend-aware budgets (`STEP_TOKENS=4096`/`STEP_WRITE_TOKENS=8192`
on OpenRouter; local 256/512 unchanged), and a write-forcing executor policy
on write-shaped tasks (commitment pressure after 3 executed observation
steps, an observation-free step-tail reserve, and a first-class
`no_write_executed` replan flag scoped to the failed task). Revision 2
(2026-08-01, issue #7) introduced ranged `read` windows with continuation
metadata plus hash-linked totals, bounded `search` and `tree` actions, atomic
`write`/`edit` with chunked `append` transport, observation-action state
budgets with truncation flags, typed `malformed_action`/`response_truncated`
parse failures, selected-vs-executed step accounting, and curated replan
state. Any matrix recorded under an earlier revision must be re-run under
this revision before comparison; per the rule below, action/budget changes
after results require a new protocol version.

**FeatureBench protocol numbering:** the FeatureBench canary series (issue #7)
has consumed protocol versions up to v4 (both v4 cells spent their single
attempt on 2026-08-01 under the revision-2 interface; empty-patch unresolved,
records in `tests/featurebench/results/`). Frozen **v6** protocols reflecting
this revision-3 action interface are registered in `tests/featurebench/` (one
Gemma 4 31B cell, one Qwen 3.6 27B cell), with the gold and harmless controls
requalified before any model call; v6 supersedes the unconsumed v5 protocols
with a provider re-pin (SiliconFlow → CoreWeave) and no other change. Each cell allows one model attempt on the
same canary task; subset expansion requires a patch reaching execution and
held-out acceptance first.

**Status:** infrastructure and task qualification only. No outcome-bearing
model call has been made under this protocol. The four-task paired pilot is not
registered until its remaining fixtures, model route, and randomized schedule
are frozen at an immutable revision.

## Purpose

The native suite measures the delivered artifact and the agent control loop
separately. It does not measure model families, scaling, general reliability,
or whether a model internally "reasons." The policy manipulation controls only
the explicit reasoning channel requested by AskMe.

Each task contains:

- a syntactically valid seed workspace;
- protected visible regression checks that pass on the seed;
- protected visible feedback checks that fail on the seeded semantic defect;
- a held-out behavioral evaluator outside the copied workspace; and
- a fixed prompt and run-budget manifest.

The held-out evaluator is separated from ordinary agent context, but Phase 1
is not an adversarial sandbox. An agent process with unrestricted host access
could search outside its workspace. Container/filesystem isolation is later
work.

## Explicit-reasoning policies

The `gated` arm freezes the behavior that existed before this suite. The `off`
arm runs the same planner, executor, replanners, tools, and validator while
suppressing every explicit reasoning request.

| Request event | `gated` | `off` |
|---|---|---|
| Initial plan or ordinary executor step | disabled | disabled |
| First automatic JSON-contract retry | medium | disabled |
| Full planner replan | medium; high on its first retry | disabled |
| Executor after `compile_error`, `stuck_loop`, or `unknown` | medium; high on its first retry | disabled |
| Executor after structural errors in `_NO_THINK_ERRORS` | disabled | disabled |
| Executor after two duplicate-action skips | medium; high on its first retry | disabled |
| Task-local replan | disabled | disabled |
| LLM final validator, when enabled | high | disabled |

Every HTTP attempt must emit a `reasoning_decision` record containing the
requested policy, trigger, requested level, effective level, and attempt.
Policy compliance is a run-validity check, not an outcome metric.

## Outcome contract

The runner records these dimensions independently:

1. `infrastructure_valid`: fixture and check processes launched correctly;
2. `integrity_passed`: protected visible checks were unchanged;
3. `agent_status` and `agent_complete`: the control loop's own report;
4. `regression_passed` and `feedback_passed`: visible artifact checks; and
5. `acceptance_passed`: held-out behavioral evaluation.

`artifact_accepted` requires all three artifact checks. `run_valid` requires
infrastructure and integrity. The two planned primary outcomes are held-out
artifact acceptance and false completion over all valid scheduled runs. The
full acceptance × completion table is always retained so accepted-but-
incomplete artifacts remain visible.

## Qualification and exclusions

Before model runs, the no-op seed must preserve regressions while failing
visible feedback and held-out acceptance. A same-author reference
implementation and an independently authored alternative must each pass every
artifact check. The alternative author may use the task prompt and protected
public checks, but not the reference implementation or held-out evaluator; its
provenance is retained with the fixture. Each trial starts from a fresh copied
fixture.

Outcome-bearing AskMe runs use a cold-start CLI subprocess with a prompt file,
the copied working directory, a structured result path, and every frozen policy
and inner budget passed explicitly; the parent enforces the manifest's frozen
wall-clock timeout. In-process callbacks remain available only for offline
runner qualification. This prevents Python module state from leaking between
scheduled cells and exercises the same process boundary used by an external
adapter.

Only failures before any model response may be repeated as infrastructure
invalidity. Timeouts, malformed responses, crashes, wrong artifacts, tool/test
failures, and exhaustion after the first response are system outcomes. A task,
policy, evaluator, or budget change after results requires a new protocol
version and rerun of the affected matrix.

## Phase 1 boundary

This revision supplies one qualified semantic task, configuration precedence
and process error behavior. It is sufficient to validate the runner and policy
instrumentation, not to run or interpret the planned 4 × 2 × 3 pilot.
