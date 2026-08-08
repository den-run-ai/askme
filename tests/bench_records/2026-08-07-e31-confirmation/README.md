# E31 confirmation — lifecycle vs heuristic step policy on T1c (2026-08-07/08)

Preregistered follow-up to the pilot in
`../2026-08-04/e89-web-local/` (`shipped-profile/t1c-lifecycle-3trials/`),
which showed lifecycle 2/3 vs heuristic 0/4 and looked promising.
Protocol registered on issue #31 **before** these runs:
https://github.com/den-run-ai/askme/issues/31#issuecomment-5218596152

## Verdict: UNCONFIRMED — the pilot effect was variance

| Arm | agent complete | Fisher two-sided | Clean-subset | Wall median (clean) |
|---|---|---|---|---|
| `heuristic` (control) | 4/10 | — | 4/10 | 246 s (172–315) |
| `lifecycle` | 5/10 | **p = 1.000** | 3/7 | 253 s (156–317) |

Both the full comparison (5/10 vs 4/10) and the contamination-excluded
subset (3/7 vs 4/10) give **p = 1.000**. The preregistered rule was
α = 0.05, so the arm is reported as unconfirmed.

**The pilot's 0/4 control was a small-sample artifact.** This cell is
high-variance around ~40–50%; four consecutive control failures were
ordinary bad luck, and the apparent lifecycle advantage did not survive
n=10. The pilot's "~70% slower" finding also dissolved: clean wall medians
are 246 s vs 253 s.

## Mediators — why the pilot's mechanism story was wrong

Recorded as preregistered, across all 20 runs:

| Mediator | heuristic | lifecycle |
|---|---|---|
| lifecycle-specific skips (`lifecycle_unverified_done`, rewrite steer) | 0 | **0** |
| `done` actions emitted | 0 | 0 |
| verification command suppressed by duplicate/stuck guards | 50 (10/10 runs) | 48 (10/10 runs) |
| repair verified in-run (successful `test_app.py` after the fix) | **10/10** | **10/10** |

1. **The lifecycle arm's own invariants never fire on this task.** Zero
   lifecycle-specific skips in 10 runs. Whatever the arm did or did not do
   here, it was not via its documented phase invariants — so this cell does
   not test #31's actual mechanism, and should not be cited as if it did.
2. **Neither arm ever emits `done`.** Runs that end `complete` do so because
   the last planned task is marked `task_complete`.
3. **Verification-command suppression is universal, not a discriminator.**
   The pilot hypothesis — that a verify-only task is unsatisfiable because
   the duplicate/stuck guards suppress the command that would satisfy it —
   is **not supported as the cause**: suppression occurs in 20/20 runs,
   including all 9 that complete. It is background, not the variable.
4. What separates the outcomes is gradual and stochastic, consistent with a
   race rather than a rule (means per run):

   | | complete (n=9) | exhausted (n=11) |
   |---|---|---|
   | verify commands executed after the repair | 5.6 | 4.8 |
   | verify commands suppressed after the repair | 4.0 | 5.4 |
   | `task_complete` | 2.3 | 1.2 |
   | `task_failed` | 0.6 | 1.5 |

## The finding that does survive

**20/20 runs produced a correct repair.** Every run in both arms landed a
successful `edit` and then ran `test_app.py` to a passing exit; held-out
acceptance passed on every workspace checked (all pilot workspaces and every
surviving workspace here), with the protected `test_app.py` unmodified.

Yet **11/20 were recorded `exhausted`.** The agent solves this task
essentially always and fails to claim it about half the time. That gap is
independent of step policy and is the thing worth fixing.

## Provenance and limitations

- **Contamination:** lifecycle trials 5–7 of the `lifecycle-c` batch ran with
  multi-hour stalls mid-LLM-call (max inter-event gaps 3243 s, 6848 s,
  11059 s; wall 18493 s, 21583 s, 36382 s) as the host slept between
  2026-08-07 09:11 and 2026-08-08. Their outcomes are retained and reported,
  and the sensitivity analysis above excludes them. The control arm has zero
  contaminated runs.
- **Interrupted invocations:** the lifecycle arm ran as three invocations
  (1 + 2 + 7 trials) because two session-managed background runs were killed
  mid-flight; the third was detached with `nohup`. Partial trials killed
  mid-run were deleted, never counted — only runs with a `run_end` record are
  analyzed. Same session, server, revision, and pinned config throughout.
- Single task, single model, single machine. This says nothing about the
  lifecycle arm on other task families, where its invariants might actually
  fire.
- `analyze.py` reproduces every number here from the JSONL.
