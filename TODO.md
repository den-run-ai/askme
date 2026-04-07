# TODO

## Reliability

- Add network timeouts and transport-level error handling around LLM requests in `ask_llm()`.
- Decide whether non-200 HTTP responses should be retried, surfaced as task errors, or fail the run immediately.
- Add tests for request timeouts, connection failures, and non-JSON API responses.

## Task Completion Semantics

- Revisit the executor rule that treats any successful `write` or `shell` in `last_steps` as immediate grounds for `done`.
- Revisit the auto-done fallback after JSON parse failure when the previous step was merely successful, not necessarily task-complete.
- Add tests for genuinely multi-step single-task flows to verify the agent does not stop early after one successful action.

## Test Consistency

- Align `int_run()` with `run()` so integration tests exercise the same exception handling behavior.
- Align step-history output truncation between `int_run()` and `run()`.
- Add a regression test proving the production loop and integration harness handle LLM/parsing failures the same way.

## Test Coverage Gaps

- Finish `test_fix_missing_include` so it asserts the binary was rebuilt and run successfully, not just that the source file was edited.
- Review other integration tests for partial assertions that allow success without proving the documented behavior.

## Documentation Drift

- Reconcile all stated test counts across `README.md`, `ARCHITECTURE.md`, `gemma4-setup.md`, and `CLAUDE.md`.
- Reconcile the claim that "all tests pass" with the currently documented subsets actually verified.
- Clarify the difference between broken `--cache-reuse` and working slot save/restore so "cache enabled" is not misleading.
- Working directory assumptions in command examples are now aligned across `README.md` and `CLAUDE.md`; keep them in sync if commands change again.

## Cache Workaround State

- Keep `CACHE_WORKAROUND=1` documented as implemented but currently counterproductive, not as an active optimization path.
- Retest manual slot save/restore only after upstream fix for `#21468` lands.
- If Phase 2 is revisited, document measured timings separately from implementation mechanics so outcome and mechanism are not conflated.

## Planner Follow-Up — DONE (2026-04-07)

- ~~If planner thinking is implemented, add coverage for planner `content: null` / reasoning-exhaustion responses.~~ → `test_planner_null_content_with_reasoning` in `TestPlannerReasoning`
- ~~Track planner wall time separately from total integration time to catch happy-path regressions.~~ → `planner_wall_time` logged in `run()` at plan completion
- ~~Prefer an explicit planner token-budget constant if planner `max_tokens` is increased.~~ → `PLANNER_MAX_TOKENS = 768` constant at top of `askme.py`

## Optional Follow-Up

- Consider a diagnostics note describing which issues are code bugs vs known model limitations vs documentation-only inconsistencies.
