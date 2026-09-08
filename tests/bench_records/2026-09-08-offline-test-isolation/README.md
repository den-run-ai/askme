# Offline-test isolation incident — 2026-09-08

The two earlier full-suite passes (1,412 then 1,415 tests) were real passes,
but **were not hermetic**. A final check of the existing local E4B server found
four unexpected generation requests during each pass, despite no live-test
opt-in. These eight observed requests occurred after the registered physical
serving cells. They did not change those frozen measurements or the separate
hosted task outcome, and incurred no OpenRouter charge. They are not additional
qualification trials, coding attempts, or performance evidence.

[Issue #111](https://github.com/den-run-ai/askme/issues/111) tracks the fix in
PR #110. Two tests already present at main `6a212cf` mocked `get_plan` and
`get_step` but left `replan_task` → `ask_llm` → Requests connected:

- `tests/test_agent_core.py::TestLLMTransport::test_transport_error_in_executor_triggers_replan`
- `tests/test_agent_recovery.py::TestCompletionSemantics::test_parse_error_after_success_does_not_auto_complete`

The first can request one task-local replan; the second can request one in
each of three full replans. On a host without a server, client failure/fallback
can let the tests pass. On this developer host, the requests reached a resident
server. Integration skip markers alone therefore did not protect ordinary
controller tests against a missing mock.

A diagnostic guard replaced `requests.Session.send` with `pytest.fail`, before
transport could occur. The full run then reported **2 failed, 1,413 passed,
30 skipped**, identifying exactly these tests with zero sends. This is the
deterministic failure-before-fix evidence; it does not rely on another model run.

The correction scripts the missing task-replanner replies while preserving
the tests' original completion/transport assertions. An autouse Requests
transport guard makes ordinary tests fail closed. Live transport requires both
the `live_llm` marker and explicit environment opt-in. Mocked provider boundaries
remain usable. This is an in-process test guard, not an OS network sandbox;
intentional ephemeral loopback web-app tests have their own boundaries.

The [server excerpt](local-server-excerpt.log) begins at byte offset 3,317,525
of the existing log, immediately after the frozen qualification excerpt. It
shows the two four-request groups at server-minute timestamps 49363 and 49365,
then idle observations. Earlier passing suites are not retroactively asserted
hermetic. Full raw HTTP bodies were not recorded for these accidental calls;
the retained server log and blocked diagnostic establish the defect without
inventing missing telemetry.

The [earlier candidate validation](../../../docs/releases/2026-09-08-candidate-validation.json)
remains a dated record, superseded for the hermeticity claim by this incident
and the final guarded validation. Original local/hosted experiment files are
unchanged. The task and serving protocols must not be rerun to hide this issue.

## Final guarded validation

The [final guarded gate](../../../docs/releases/2026-09-08-hermetic-validation.json)
passed **1,421 tests with 30 expected skips**, 96.46% branch-aware coverage,
Ruff/format/ty, and the scoped whitespace check. The resident E4B server log
was byte-identical before and after: 1,260 total generation-launch records,
last task 203708, 3,332,418 bytes. No new E4B generation request occurred.
The [blocked diagnostic](blocked-diagnostic.txt) and its [tripwire](no_http.py)
are retained separately from the repaired final run.
