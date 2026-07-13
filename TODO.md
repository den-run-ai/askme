# TODO

## Reliability — DONE (2026-04-08)

- ~~Add network timeouts and transport-level error handling around LLM requests in `ask_llm()`.~~ → `LLM_TIMEOUT=120`, `LLMTransportError`, transport retry with backoff in `ask_llm()`. Both planner and executor phases catch transport errors.
- ~~Decide whether non-200 HTTP responses should be retried, surfaced as task errors, or fail the run immediately.~~ → 429/5xx retry with backoff, 4xx fail fast, JSON "error" key preserved (existing behavior). `TestLLMTransport` verifies.
- ~~Add tests for request timeouts, connection failures, and non-JSON API responses.~~ → 9 tests in `TestLLMTransport` covering timeout propagation, retry, fail-fast, planner/executor error handling.

## Task Completion Semantics — DONE (2026-04-07)

- ~~Revisit the executor rule that treats any successful `write` or `shell` in `last_steps` as immediate grounds for `done`.~~ → Replaced with goal-aware completion: executor must satisfy the full task description, not just one successful step. `TestCompletionSemantics` verifies the old rule is gone.
- Revisit the auto-done fallback after JSON parse failure when the previous step was merely successful, not necessarily task-complete.
- Add tests for genuinely multi-step single-task flows to verify the agent does not stop early after one successful action.

## Test Consistency — DONE (2026-04-08)

- ~~Align `int_run()` with `run()` so integration tests exercise the same exception handling behavior.~~ → `int_run()` now delegates to `_run_loop()` (the shared core loop extracted from `run()`). Integration tests exercise identical production behavior: preflight, policy, null normalization, error reset, timeout retry, typed error formatting.
- ~~Align step-history output truncation between `int_run()` and `run()`.~~ → Eliminated by shared `_run_loop()`.
- ~~Add a regression test proving the production loop and integration harness handle LLM/parsing failures the same way.~~ → No separate regression test needed — they use the same code path now.

## Test Coverage Gaps

- Finish `test_fix_missing_include` so it asserts the binary was rebuilt and run successfully, not just that the source file was edited.
- Review other integration tests for partial assertions that allow success without proving the documented behavior.

## Documentation Drift

- ~~Reconcile all stated test counts across `README.md`, `ARCHITECTURE.md`, `gemma4-setup.md`, and `CLAUDE.md`.~~ → `README.md` now carries a dated current-suite snapshot; older counts are explicitly historical phase records (2026-07-13).
- ~~Reconcile the claim that "all tests pass" with the currently documented subsets actually verified.~~ → Current verification reports the exact `pytest -q` pass/skip outcome and labels backend-dependent skips (2026-07-13).
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

## Agentic Execution Improvements — DONE (2026-04-07)

- ~~Add capability/permission policy (`ALLOW_SYSTEM_INSTALLS`, `ALLOW_NETWORK`).~~ → Policy injected into planner and executor state/prompts. `TestExecutionPolicy` verifies.
- ~~Add structured preflight probe (platform, arch, tools, pkg managers, dir listing).~~ → `preflight_probe()` runs before first plan. `TestPreflightProbe` verifies.
- ~~Fix task-completion semantics — remove "any successful step → done" rule.~~ → Executor prompt now requires full task satisfaction. `TestCompletionSemantics` verifies.
- ~~Add typed failure classification.~~ → `classify_error()` returns `timeout`, `missing_tool`, `permission_denied`, `missing_file`, `compile_error`, `unknown`. `TestFailureClassification` verifies.
- ~~Add command-aware timeouts (30s default, 120s install/build, 300s max).~~ → `_get_shell_timeout()` with pattern matching and model hint support. Timeout retries exempt from duplicate guard. `TestCommandAwareTimeout` verifies.
- ~~Add error summarization for replans.~~ → `summarize_errors()` groups by type, deduplicates, caps at 3 per type. `TestErrorSummarization` verifies.

## Agentic Execution Follow-Up

- Validate preflight + policy with live LLM integration tests (both local and OpenRouter).
- Consider making the tool allowlist configurable via env var or config file.
- The auto-done fallback after JSON parse failure (when last step was successful) still uses step-level heuristic, not goal-aware completion. This is a known compromise for small LLMs that struggle with JSON output.
- ~~Update `int_run()` to include preflight and policy so integration tests exercise the same path as `run()`.~~ → Done via `_run_loop()` extraction (2026-04-08).

## Optional Follow-Up

- Consider a diagnostics note describing which issues are code bugs vs known model limitations vs documentation-only inconsistencies.

## Public Release Hygiene

- [x] Add an explicit security boundary and untrusted-input guidance.
- [x] Remove the private-fork and machine-specific path assumptions from primary setup docs.
- [x] Record the FeatureBench structured-action failure in `PERFORMANCE.md` and `ARCHITECTURE.md`.
- [ ] Run a dedicated full-history scanner such as Gitleaks or TruffleHog immediately before changing repository visibility. A basic full-history content regex scan with filename-only output found no common key or private-key patterns on 2026-07-13, but it is not a substitute for a dedicated scanner.
- [ ] Decide whether to enforce network isolation in code; changing `ALLOW_NETWORK` today would be a behavior and protocol change, not documentation hygiene.
- [ ] Confirm every audience-facing repository link resolves after the repository visibility change.
