# AskMe — Minimal Local Agent for Constrained LLMs

Designed for: Gemma 4 E4B (dense PLE, 4.5B effective / 8B including embeddings), 16K context, 16GB M1 Mac.

For usage, quickstart, and test commands see [README.md](../README.md). For model/server/runtime configuration see [gemma4-setup.md](gemma4-setup.md). For benchmark history and test-run matrices see [PERFORMANCE.md](PERFORMANCE.md).

## Architecture: Plan → Execute → Replan Loop

```
┌──────────────┐
│  user prompt  │  ← CLI argument
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  get_plan()   │  ← LLM proposes task list from prompt + state
└──────┬───────┘
       │ {"tasks": ["task1", "task2", ...]}
       ▼
┌──────────────┐
│  for task:    │  ← iterate tasks sequentially
│  get_step()   │  ← LLM proposes one action per call
│  execute()    │  ← runs shell / write / edit / read
│  update state │  ← append step result to state
└──────┬───────┘
       │
       ├── all tasks done? → conditional final validation → typed outcome
       └── task failed? → replan (up to MAX_REPLANS)
```

A deterministic `preflight_probe()` runs once before the first plan: platform, arch, available/missing tools, package managers, working-dir listing. The structured result plus execution policy is fed into planner state.

## Core Files

`askme.py` (CLI, LLM client, controller loop, recording) and `actions.py`
(action registry, handlers, typed results/receipts) — no seed files, no
framework. `askme.py` re-exports the action layer's public names, and
`execute()` stays its compatibility facade.

**Key functions:**
- `preflight_probe(working_dir)` — environment probe (platform, arch, tools, package managers, dir listing)
- `get_policy()` — execution policy (`allow_system_installs`, `allow_network`)
- `_repair_json(text)` — semantics-preserving JSON repair: safe object extraction, trailing-delimiter cleanup, and insertion-only container completion; partial keys/values are never deleted or defaulted; returns dict or None
- `LLMSettings` / `LLMClient` — immutable client-local LLM configuration (one `from_env` derivation; the module-level backend/model globals remain the patchable compatibility mirrors) and the client boundary bundling those settings with an injectable HTTP `post`, sleeper, and log/event sinks, so two clients with different backends/models can share one process without global leakage (issue #37)
- `ask_llm(messages, max_tokens, think, reasoning_policy, reasoning_trigger)` — compatibility facade that snapshots the module configuration into a fresh `LLMClient` per call: calls a backend, logs the requested/effective explicit-reasoning decision for every attempt, strips `<think>`/`<|channel>` blocks and code fences, extracts JSON, attempts repair, then retries on parse failure (up to 2). E03: the final auto-retry uses a strict contract with no thinking
- `get_plan(user_prompt, state, client=None)` — task list. Under the default `gated` policy, explicit reasoning is off for the first plan and enabled for replans
- `get_step(task, state, goal, reasoning_policy, goal_context_chars, write_pressure, client=None)` — next action within a task. Goal-aware completion uses the per-run frozen goal-context view rather than the generic field cap; `write_pressure` appends a commit-demanding note on stalled write-shaped tasks
- `replan_task(failed_task, errors, completed_tasks, state, user_prompt, client=None)` — mini-planner for E11: generates a replacement task description for a single failed task. Cheap no-thinking call (`max_tokens=96`, no retries). Returns a `TaskReplanResult` named tuple — the accepted `task` or a typed `reject_reason` (issue #40 removed the module-global side channel). Includes policy/missing_tools in state and rejects exact duplicates, near duplicates, and passive downgrades
- `classify_error(output, cmd)` — categorizes as `timeout`, `missing_tool`, `permission_denied`, `missing_file`, `compile_error`, or `unknown`. Command-aware: compiler-family commands prefer `compile_error` over `missing_file` for ambiguous diagnostics (E16)
- `summarize_errors(errors)` — groups, deduplicates, caps at 3 per type for planner
- `actions.parse_action_envelope(obj)` / `actions.ActionEnvelope` — one pure action boundary shared by reply decode, injected-client controller intake, and defensive dispatch. The parser owns allowed/required fields, string and integer types/bounds, reserved controller metadata, and read-continuation dependencies; it returns either a detached immutable normalized action or a precise `ActionProtocolError` without raising or mutating the raw object. Optional `reasoning:null` and optional tree/control `arg:null` are the only legacy null normalizations
- `execute(action, working_dir)` — compatibility facade over `actions.ActionExecutor`, which dispatches shell/write/edit/read/search/tree through that same parsed registry (`ACTION_SPECS`: name, category, allowed/required fields, handler) with command-aware timeouts, bounded observation windows, and atomic file writes. Unknown actions fail with typed `unknown_action`; malformed actions fail before side effects; `done`/`fail` are controller decisions and dispatching one is a typed `control_action` error
- `StepRecorder` / `actions.StepReceipt` — the single record-and-count path: every executed step, deterministic repair/retry receipt, guard skip, and corrective observation flows through one recorder, with explicit projections for model history, structured results, and JSONL
- `PlanResponse` / `TaskReplanResponse` / `ValidationResponse` / `_action_envelope_error` — response-specific schemas (issue #68): every LLM call site names its expected type (`expect="plan"|"action"|"task_replan"|"validation"`), the client rejects a wrong-type envelope with its normal parse-retry policy, and the call site converts accepted data into a typed record before controller accounting. Executable replies become `DecodedAction(ActionEnvelope, ActionTransport)`, keeping provider truncation facts outside model-controlled fields. Empty, cross-type, and unknown-action envelopes at the executor seam are typed schema rejections that never consume a selected or executed step
- `StepPolicy` / `HeuristicStepPolicy` / `LifecycleStepPolicy` — the pluggable step/completion-pressure arm (issue #31), selected per run by `step_policy` (`AGENT_STEP_POLICY`). The heuristic baseline owns keyword write pressure, the observation tail reserve, and rewrite damping; the lifecycle arm replaces those counters with explicit inspect → modify → verify → finish invariants (a successful mutation needs verification; only a successful shell check clears it; unverified same-target rewrites and `done` are steered to verification). The base class carries the shared duplicate/stuck loop protection (`guard_duplicate`), which never converts repetition into completion
- `WriteObligations` / `ValidationState` / `CompletionPolicy` — the remaining per-run components (issue #69): incomplete-write invariants (truncation classification, zero-byte obligation records, append-safety and recovery order, the `done` refusal, resume anchors, clearing), the typed owner of validation state (projecting through the existing structured-state keys), and the terminal policy (validation gating, verdict handling, the evidence-gated recheck, and the typed `RunOutcome` for completion and exhaustion). `_RunController` sequences planning, attempts, dispatch, recording, and finalization; the individual algorithms live on these components and the step-policy arm
- `_validate_completion(...)` — post-completion LLM check; gated by `_should_validate()`; returns a typed `ValidationResponse` or None when no verdict is available
- `RunOutcome` — the typed terminal record (issue #68): status, final-validation disposition, replans, wall time, and step counters; the `run_end` JSONL event and the structured result's `status`/`outcome` fields are projections of the same record
- `GuardThresholds` / `_config_hash` — resolved outcome-affecting configuration (issue #68): validation mode, the #41 compile-repair arm, the #31 step-policy arm, guard thresholds, per-call request timeouts/retry limits, and capability budgets freeze at run start into one payload whose sha256 `config_hash` is logged at `run_start` and returned in the config metadata (never credentials). The default compatibility path binds that snapshot to `ask_llm` for the whole run; direct facade calls still snapshot per call
- `run_result(user_prompt, working_dir=None, config=None, dependencies=None)` — the public structured-run API (issue #40): resolves workspace ownership (`RunWorkspace`, with an explicit `created` flag and intentional `cleanup()`), composes `_RunController` from an immutable `RunConfig` (per-run LLM settings, execution policy, reasoning policy, budgets; `None` fields resolve from the module compatibility surface) and injectable `RunDependencies` (LLM client, action executor, clock, log/event sinks), and returns `status`/`state`/`log` plus credential-free `config` metadata and the `workspace` record. A pinned `llm` config gives the run its own `LLMClient` — and backend-shaped step budgets — so differently configured runs coexist in one process
- `_run_loop(...)` — compatibility seam over `run_result()` and `_RunController`, the structured core loop with frozen task, step, replan, goal-context, and explicit-reasoning controls. Run-scoped controller data (the structured state dict, rewrite damping, wall clock, the one recorder) lives on `RunState`; attempt-scoped executor state (write pressure, duplicate/observation counters, thinking escalation) lives on `TaskAttemptState`; `done`/`fail` and one shared completion-blocker gate (`_completion_blocker`) remain controller concerns (issue #31)
- `run(user_prompt, working_dir=None)` — backward-compatible public wrapper over `run_result()` returning a boolean

**State** is an in-memory dict (no files). Planner and executor see different views.

Planner (`get_plan`) — curated full state (raw write payloads never included):
```json
{
  "completed_tasks": ["task1"],
  "errors": ["[missing_tool] shell go run: /bin/sh: go: command not found"],
  "environment": {"platform": "darwin", "arch": "arm64", "available_tools": ["python3", "gcc"], "missing_tools": ["go"], "package_managers": ["brew"], "dir_listing": ["main.go"]},
  "policy": {"allow_system_installs": false, "allow_network": true},
  "recent_steps": [{"action": "shell", "arg": "go run main.go", "ok": false, "output": "/bin/sh: go: command not found"}]
}
```

`recent_steps` is a digest of the last 6 executed steps (`_step_digest` — action/arg/ok/output only). Full file contents from `write` steps stay in run state but are never sent to the planner, keeping replan state bounded on write-heavy runs.

Executor (`get_step`) — slim state, token-optimized:
```json
{
  "task": "compile hello.c",
  "task_index": "2/3",
  "step": "2/5",
  "completed_tasks": ["create hello.c"],
  "last_steps": [
    {"action": "write", "arg": "hello.c", "ok": true, "output": "Wrote hello.c"}
  ],
  "missing_tools": ["go"],
  "policy": {"allow_system_installs": false, "allow_network": true}
}
```

State design:
- `last_steps` is a sliding window of `MAX_STEP_HISTORY=3`; mutating-action output truncated to 100 chars, observation actions (`read`/`search`/`tree`) to `OBSERVE_STATE_CHARS=1500`
- `completed_tasks` (last 3, truncated to 80 chars each) gives executor cross-task awareness
- Last step from previous task carries over so executor knows what just happened
- Write/read `arg` fields use basename to save tokens
- Write output is `"Wrote main.c"` (basename) not full path — critical for the LLM to recognize the file was created
- `reasoning_policy` and `goal_context_chars` are frozen in run state for logging and downstream calls, but are deliberately removed from planner state so the experiment label cannot become model-visible task evidence
- The initial planner receives the full request. Executor and task-local replan receive the same frozen `goal_context_chars` prefix in both policy arms; other executor fields remain capped independently

**Two system prompts:**
- `SYSTEM_PLAN` — planner, outputs `{"tasks": [...]}`. Gets full user prompt + full state.
- `SYSTEM_STEP` — executor, outputs `{"action": "...", "arg": "...", "content": "...", "reasoning": "..."}`. Gets original goal + current task + slim state.

## Explicit-Reasoning Policy

`AGENT_REASONING_POLICY` controls requests made through the backend's explicit
reasoning channel; it does not claim that a model performs no internal
reasoning. Normal runtime defaults to `gated`. Evaluation runs freeze either
`gated` or `off` for the entire process.

| Request event | `gated` | `off` |
|---|---|---|
| Initial plan or ordinary executor step | Disabled | Disabled |
| First automatic JSON-contract retry | Medium | Disabled |
| Full planner replan | Medium; high on its first retry | Disabled |
| Executor after semantic/unknown execution errors | Medium; high on its first retry | Disabled |
| Executor after structural errors in `_NO_THINK_ERRORS` | Disabled | Disabled |
| Executor after two duplicate-action skips | Medium; high on its first retry | Disabled |
| Task-local replan | Disabled | Disabled |
| LLM final validator, when enabled | High | Disabled |

On OpenRouter, `OPENROUTER_REASONING_EFFORT` adds an orthogonal baseline for
always-on reasoners (harmony-format gpt-oss models expose `low`/`medium`/`high`
but no off switch): the requested effort is the maximum of the baseline and the
effective level from the table above, so `gated` escalation can raise but never
lower it, and `off` pins every request to exactly the baseline. Unset (the
default) preserves the reasoning-disabled request contract for hybrid models.

Every HTTP attempt emits a `reasoning_decision` JSONL event with the policy,
named trigger, requested level, effective level, baseline effort, and attempt
number. The `off` policy nulls the effective level even when a caller requests
an explicit level.

Internally the provider call is split into client seams (issue #37, first
extraction): a pure per-attempt reasoning decision, a backend-specific request
builder, a one-shot transport step that only classifies its outcome
(`transport`/`http_retryable`/`http_fatal`/`non_json`), and a pure reply
decoder owning reasoning/fence stripping, sentinel content, JSON
extraction/semantics-preserving repair, and typed action parsing. `ask_llm(...)` remains the
compatibility facade and still owns retry/backoff policy, the parse-retry
budget escalation, and the typed errors callers see; its signature, defaults,
and error contract are unchanged. Per-run immutable configuration and
injectable clients (the rest of #37, with #40) come after the remaining seams.
The frozen evaluation contract lives in
[`tests/workflows/PROTOCOL.md`](../tests/workflows/PROTOCOL.md).

Outcome-bearing native evaluations do not import `askme` into the evaluator.
The runner first copies a seed into a fresh workspace, then launches `askme.py`
through its CLI in a cold subprocess with frozen policy and budgets. A separate
outer wall timeout bounds the whole child. The runner retains the structured
agent result, bounded process streams, and isolated JSONL events before running
visible regressions and the held-out sidecar evaluator. Offline no-op,
reference, and independent-alternative qualification may use in-process
callbacks because those paths make no model call.

## Action Model

| Action | Purpose | Notes |
|---|---|---|
| `shell` | Run command with timeout | Command-aware timeouts (`SHELL_TIMEOUT`, `SHELL_TIMEOUT_LONG` for install/build, `SHELL_TIMEOUT_MAX` hard cap) |
| `write` | Create or replace a whole file | Atomic (temp file + rename). An explicitly present empty string creates a zero-byte file. Dict/list content remains supported and auto-serializes to JSON. `append` must be a JSON boolean; `"append": true` appends one chunk. Content may instead travel between sentinel lines after the action JSON — the two forms are mutually exclusive |
| `edit` | Exact single-match string replacement | For localized changes. Fails on zero or multiple matches. Atomic write |
| `read` | Lossless paged read of a UTF-8 file | Initial requests use `offset`/`limit` (1-based lines). Every successful read carries exact page text in `content`; a successful page with unread source returns an action-ready `continuation` with a 0-based Unicode-code-point `cursor`, normalized `limit`, source `sha256`, and informational next-line `offset`. The next action must echo the cursor, limit, and hash; cursors at or beyond `total_chars` are invalid because generated continuations always point to unread content. Line terminators are preserved, `READ_CHARS` counts code points (not UTF-8 bytes or grapheme clusters), and a changed source rejects the stale cursor. `total_lines`/`total_chars`/`total_bytes`/`sha256` hash-link each page and its run-log record |
| `search` | Bounded literal search | `arg` = literal pattern, optional `path` (default `.`). Skips VCS/dependency/hidden/binary files; caps matches and chars; suggests narrowing when capped |
| `tree` | Bounded repository listing | `arg` = directory (default `.`). Depth-, entry-, and char-capped; directories marked with `/` |
| `done` | Mark current task complete | Terminal |
| `fail` | Mark current task failed | Triggers replan |

`edit` exists because full-file `write` content frequently exceeds the local model's 256-token executor budget on multi-line files. Edit payloads fit in ~40-80 tokens; the 26B model on OpenRouter also spontaneously prefers `edit` for fixes. For new large files, chunked `append` writes cover what `edit` cannot.

Observation actions (`read`/`search`/`tree`) carry their own budgets (issue #7): results are bounded by `READ_CHARS`/`SEARCH_MAX_CHARS`/`TREE_MAX_CHARS` and kept in executor step history up to `OBSERVE_STATE_CHARS` (vs 100 chars for mutating actions). Read pages are losslessly resumable. Search and tree remain intentionally lossy discovery summaries: bounded match/file/snippet/entry/depth/character omissions plus unreadable files and traversal errors are exposed in `truncation_reasons` — carried through the action result, the executor step history (inside the bracketed header), and the JSONL `step` record — only complete records are packed, total output including the header fits the history budget, and the model is directed to narrow the query/path or use `read`. On the model-output side, `ask_llm` records `finish_reason` in every `tokens` JSONL event; when a truncated `write`/`edit` payload fails to parse, the retry gets a payload-sized budget (`STEP_WRITE_TOKENS`) instead of more reasoning, and an unrecoverable parse failure surfaces as a typed `[malformed_action]` or `[response_truncated]` error (the latter when the final attempt hit the token budget) that the replanner sees.

Interface revision 3 (issue #15) adds a sentinel-framed content transport: `write` content may follow the action JSON between `<<<CONTENT` / `CONTENT>>>` lines instead of riding inside a JSON string. This removes escaping overhead and makes truncation recoverable — when the closing sentinel is missing and `finish_reason=length`, the decoder retains the partial-content fact in trusted `ActionTransport` (the compatibility mapping still exposes `content_truncated`), the run loop writes the complete lines that arrived, and the step output carries a resume anchor — line count plus the last written line, kept under the observation-class history budget because the stateless executor navigates by it — steering the model to finish the file with chunked `append` (a re-emitted identical truncated write is answered with the same continuation hint, not "already done"; a truncation that yields no complete line is retried as a non-append write so a stale existing file is never appended to). Sentinel extraction happens before reasoning-tag removal so literal payload tags are preserved. Sentinel content is write-only and mutually exclusive with JSON `content`; an unclosed block is executable only when the provider explicitly reports `finish_reason=length`. The model cannot set `content_truncated` or underscore-prefixed controller fields. The closing sentinel must be flush-left and is matched from the end of the response, so content lines that merely resemble it stay content; without a later real terminator such a line leaves the block unclosed and therefore rejected at `finish_reason=stop`. A file whose genuinely-final flush-left line is the sentinel itself must use JSON `content` or chunked `append` instead. The executor also runs under a write-forcing policy on write-shaped tasks (`_WRITE_TASK_RE`) — the heuristic step-policy arm: after `WRITE_PRESSURE_OBSERVATIONS` executed observation steps with no committing action, the step prompt requires a `write`/`edit`/`shell` next; observation actions cannot consume the last `OBSERVE_TAIL_RESERVE` steps of an attempt (one warning skip, then auto-fail as `stuck_loop`); and replan state gains a first-class `no_write_executed` flag — scoped to the failed task's steps, so an earlier task's write cannot mask a later stall — so replans address the stall instead of restating the task. The lifecycle arm (`AGENT_STEP_POLICY=lifecycle`, issue #31) keeps the prompt pressure but replaces tail blocking and rewrite counters with its verification invariants.

Interface revision 4 responds to the rewrite loop observed in the 2026-08-01 v6 canary under the bundled revision-3 changes and a changed serving stack: Gemma rewrote one file 18 times without running the delivered tests or emitting `done`. After `REWRITE_PRESSURE_WRITES` consecutive successful full writes of the same target with no intervening successful `shell`/`edit`, the step prompt requires a shell action, a targeted edit, or `done`; from `REWRITE_SKIP_WRITES` on, further full rewrites of that target are skipped (`rewrite_loop`) with a corrective hint. Observations do not reset the streak; a successful shell or targeted fix does. Any truncated write, including an empty or append attempt, clears the streak so the instructed clean restart cannot be blocked; ordinary appends are exempt from counting but do not clear an armed streak. Replan state distinguishes `unvalidated_write` (a complete mutation with no later successful shell) from `incomplete_write` (any target still has a truncated write not followed by a complete write/append to that same target). `incomplete_write_target` preserves the canonical actionable target across leaf- or working-directory-symlink retargets, and `incomplete_write_append_allowed` records whether append recovery is safe; both fields remain structured on every executor turn, including task-local retries. That exact target is the narrow exception to the usual relative-path rule. Empty-overwrite retries retain every observed referent guard, restrictive overwrite recovery is surfaced before a blocked append, and appending through an older physical alias cannot mutate stale content. Edits and shells cannot reconstruct a missing suffix; unresolved incomplete state blocks `done`, and step exhaustion is terminal — the former deterministic reconciliation of an exhausted run to `complete` from a trailing successful shell is removed (issue #68). `no_write_executed`, `unvalidated_write`, and task-local `failed_steps` use the current task's slice even when it is empty, so zero-dispatch failures do not leak prior-task progress; incomplete artifacts remain visible run-wide across replacement tasks, including zero-byte pending truncations. Empty task lists are rejected as malformed plans, and a failed final validation cannot be bypassed by a recovery without new successful write, edit, or shell evidence. The same revision folds in the three post-merge Codex P2 fixes from PR #16: `commit_executed` counts only successful mutations, write intent is classified from the failed task rather than the whole request, and `_WRITE_TASK_RE` drops passive-prone `include` plus exempts tasks led by an observation verb (`_is_write_shaped`). A future explicit inspect → modify → verify → finish controller is tracked separately in issue #31; revision 4 does not prove that a successful shell was targeted verification.

Interface revision 5 fixes the ranged-read reconstruction defect tracked by issue #30. Revision 2 selected a line window, clipped its rendered body at 1,200 characters, then advanced continuation from the unclipped line endpoint; this could skip unseen suffixes or return no continuation for one oversized line. Revision 5 pages the exact decoded UTF-8 source with a source-hash-bound Unicode-code-point cursor, preserves line terminators, returns page text separately from the display header, and makes the duplicate-read identity include initial line limits or continuation cursor/limit/hash fields. It also makes bounded `search`/`tree` omissions explicit rather than returning definitive-looking incomplete discovery results. Historical v4/v6 canaries retain their original interface attribution; this repair does not retroactively strengthen those outcomes.

The run loop also accounts for selected vs executed actions: every action the executor emits increments `selected_steps`, only dispatched ones increment `executed_steps`, and each guard-suppressed one increments `skipped_steps` and logs a `step_skipped` JSONL event with a typed reason (`duplicate_read`, `stuck_read`, `stuck_append`, …). The 2026-07-31 Qwen canary selected 14 reads of which only 2 executed — that gap is now first-class in `run_end` metrics instead of being reconstructed from logs.

## Failure and Replanning

On task failure:
1. Error is classified into a typed category — deterministic tags cover edit scaffold/target failures and malformed, incomplete, or stale read continuations; heuristic `classify_error()` handles shell output and other exception paths
2. Error-class-specific retry policy (E05): under `gated`, structural failures (`edit_failed`, `missing_file`, `timeout`, `missing_tool`, `permission_denied`, `invalid_read_cursor`, `invalid_read_limit`, `invalid_read_offset`, `invalid_timeout`, `read_cursor_hash_required`, `stale_read_cursor`) skip explicit-reasoning escalation while semantic failures (`compile_error`, `unknown`) request it; `off` suppresses both paths
3. Recovery hints (E06): typed failures inject a short deterministic correction into step output (for example, read exact continuation fields or restart after a stale cursor) so the model knows what to do next without thinking tokens
4. Repeated identical failed edits and repeated duplicate reads are treated as stuck and force replan instead of burning `MAX_STEPS`
5. **Task-local replan (E11):** before a full replan, `replan_task()` calls a cheap mini-planner that generates a replacement task description for just the failed task. Capped at `MAX_TASK_LOCAL_REPLANS=1` — if the replacement also fails, fall through to full replan. Original errors are saved and merged back so the full planner sees both failure contexts. Exact duplicates, near duplicates, and passive downgrades are rejected; rejection reason is logged for JSONL analysis
6. Errors are collected in state, grouped and capped at 3 per type (`summarize_errors`)
7. The full loop restarts with a new plan (`get_plan` with `think=True`); errors reset after each replan
8. `completed_tasks` carries forward — the new plan can build on what's already done
9. Up to `MAX_REPLANS` replan attempts; after that the run exits as `exhausted`

If `ask_llm` exhausts its retries, `LLMTransportError` is raised: planner catches it as a failed plan attempt, executor catches it as a task failure (triggering replan).

## Final Validation

After all tasks complete, `_validate_completion()` runs an LLM check to verify the goal was actually achieved. Catches cases where individual tasks succeed but the overall goal is incomplete (e.g., files created but not compiled, tests written but not run).

- **Gating** (`_should_validate()`): runs when complexity/risk signals are present — replan occurred, failed steps in history, ≥3 tasks, ≥5 total steps, or prompt matches action keywords (compile, build, test, run, fix). Trivial runs skip validation.
- **Evidence collected**: goal + per-task step summaries (action + basename + output snippet, ≤5 per task) + `sorted(os.listdir(working_dir))[:50]`.
- **LLM call**: `ask_llm(..., max_tokens=768, think=True, think_level="high", max_retries=0, expect="validation")` — requests one high explicit-reasoning attempt under `gated`; `off` suppresses the reasoning request. There are no retries. The reply parses into a typed `ValidationResponse` (`valid` plus optional `reason`/`missing`).
- **No verdict is never a pass (issue #68)**: transport errors, parse errors, or off-schema replies on the first wanted check complete the run as `complete_unverified` with a `valid: null` validation event — completion stands, but it is never reported as "Validation passed". Once a validator has explicitly returned `valid: false`, an unavailable recheck cannot erase that known failure.
- **Evidence-gated recheck**: a failed validation triggers a replan with `[validation_failed]`. Completion remains blocked until the recovery produces a new successful `write`/`edit`/`shell` step; then one second validation may run. A recovery with no new successful evidence, an unavailable recheck, or a still-failing second check replans or exhausts rather than silently succeeding.

Env var `AGENT_FINAL_VALIDATE` controls behavior: `auto` (default, gated), `always`, or `0` (disabled).

## Safety Limits

| Constant | Value | Purpose |
|---|---|---|
| `MAX_REPLANS` | 3 | Max plan attempts before giving up |
| `MAX_TASK_LOCAL_REPLANS` | 1 | Max task-local replan attempts before full replan (E11) |
| `MAX_TASKS` | 10 | Max tasks per plan |
| `MAX_STEPS` | 10 | Max actions per task |
| `MAX_RESULT` | 300 | Chars kept from command output |
| `MAX_STEP_HISTORY` | 3 | Sliding window of recent steps sent to executor |
| `MAX_LLM_RETRIES` | 2 | Retries per LLM call on JSON parse failure |
| `LLM_TIMEOUT` | 120s | Default planner, executor, task-replan, and validator request deadline |
| `LLM_TIMEOUT_REPLAN` | 180s | Full-planner replan request deadline |
| `MAX_INPUT` | 300 | Max chars per non-goal field sent to executor |
| `GOAL_CONTEXT_CHARS` | 300 | Default executor/task-replan goal view; independently configurable and frozen per run |
| `SHELL_TIMEOUT` | 30s | Default per-command timeout |
| `SHELL_TIMEOUT_LONG` | 120s | Timeout for install/build commands |
| `SHELL_TIMEOUT_MAX` | 300s | Hard cap for model-specified timeout hint |
| `PLANNER_MAX_TOKENS` | 768 | Task list budget; shared with thinking on replans |
| `TASK_REPLAN_MAX_TOKENS` | 96 | Task-local replacement budget |
| `VALIDATION_MAX_TOKENS` | 768 | Final completion-validator budget |
| Reasoning token floors | 1024/1536/2048 | Effective HTTP `max_tokens` floors for low/medium/high effort; frozen and recorded per run |
| `ALLOW_SYSTEM_INSTALLS` | false | Prompt-visible install policy; not host-level enforcement |
| `ALLOW_NETWORK` | true | Reserved for future use |
| Step output | 100 chars | Max output stored per mutating-action step in history |
| Observation step output | 1500 chars | `OBSERVE_STATE_CHARS` — history budget for `read`/`search`/`tree` results |
| Read page | 60 lines / 1200 Unicode code points | `READ_LINES` / `READ_CHARS`; `READ_LIMIT_MAX=200` caps model-specified limit, while exact cursor continuation handles an oversized page or line |
| Search bound | 15 matches / 1500 chars / 500 files | `SEARCH_MAX_MATCHES` / `SEARCH_MAX_CHARS` / `SEARCH_MAX_FILES` |
| Tree bound | 60 entries / 1500 chars / depth 3 | `TREE_MAX_ENTRIES` / `TREE_MAX_CHARS` / `TREE_MAX_DEPTH` |
| Write-payload retry budget | 512 (local) / 8192 (OpenRouter) | `STEP_WRITE_TOKENS` — bumped when a truncated write/edit payload fails to parse |
| Step tokens (local) | 256 | `STEP_TOKENS` — max completion tokens (local) |
| Step tokens (OpenRouter) | 4096 | `STEP_TOKENS` — sized for implementation writes (issue #15); an 8KB file is ~3000 tokens |
| `WRITE_PRESSURE_OBSERVATIONS` | 3 | Executed observation steps before the executor prompt demands a committing action (write-shaped tasks) |
| `OBSERVE_TAIL_RESERVE` | 3 | Final steps per task attempt reserved for committing actions on write-shaped tasks |
| Thinking tokens (local) | 512 (medium) / 768 (high) | Must be bumped when thinking is enabled |
| Thinking tokens (OpenRouter) | 1024 (low) / 1536 (medium) / 2048 (high) | Reasoning tokens share budget with Parasail; same floors apply to the `OPENROUTER_REASONING_EFFORT` baseline |

## Design Decisions

| Constraint | Solution |
|---|---|
| Limited speed | Short prompts (~200 tok executor, ~500 tok planner), JSON-only output |
| 16K context | Slim executor state (sliding window), no history accumulation |
| Token efficiency | Executor gets slim state (~150-200 tok) vs full state; planner gets full context |
| Code fences | Strip ` ```json ``` ` wrappers from output |
| Reliability | JSON-only output, retries, safety limits, truncated results |
| No seed files | In-memory state only; no state.json/plan.json |
| Failure recovery | Replan loop carries completed tasks forward; errors reset per replan |
| Observability | `log()` helper adds `[HH:MM:SS]` timestamps + `(Xs)` durations |
| Error truncation | `r.stdout[:300] + r.stderr[-300:]` — tail-truncates to keep actual error messages |
| Input caps | `MAX_INPUT=300` caps non-goal executor fields; `GOAL_CONTEXT_CHARS` independently caps and freezes the goal view across a run |
| Parse-error-as-failure | If `get_step()` raises after exhausting retries, task always fails and replans — no false auto-completion |
| Cross-task state | Last step from previous task carries over; `completed_tasks` included in slim state |
| Basename outputs | Write output says `"Wrote main.c"` not full path — LLM needs a clear signal |
| Basename args | Slim step history uses basename for write/read `arg` fields |
| Dict/list content | Write actions auto-serialize dict/list content to JSON — models sometimes output objects instead of escaped strings |
| Multi-backend | `LLM_BACKEND=openrouter` switches to OpenRouter API with configurable model and provider |
| Thinking-on-retry | Zero explicit-reasoning cost on the happy path under `gated`; escalation is keyed by error class (E05). Structural failures skip it while semantic/unknown failures request medium. The `off` policy suppresses every explicit request, including caller-specified levels |
| JSON repair (E03) | `_repair_json` may extract a complete object, remove only a trailing delimiter, or insert missing container closers. It never deletes a partial semantic field. A malformed header with `finish_reason=length` is not repaired into an executable action; valid partial sentinel content is the explicit exception |
| Strict final retry (E03) | Final auto-retry (attempt 2) disables explicit reasoning and appends a strict JSON-only instruction. Caller-specified levels (for example `_validate_completion`) are respected under `gated` and suppressed under `off`. Upstream has no grammar+reasoning coexistence solution |
| Recovery hints (E06) | Short hint appended to step output after typed failures. Model can override — hints are nudges, not commands. `edit_failed`/`missing_file` explain how to recover context; the four read-continuation contract errors deterministically direct the executor to echo the exact fields or restart after a source change |
| Task-local replan (E11) | On task failure, a mini-planner (`SYSTEM_TASK_REPLAN`) generates a replacement task before burning a full replan. Capped at 1 attempt; no-thinking, `max_tokens=96`, `max_retries=0`; exact/near duplicates and passive downgrades rejected with `reject_reason`. Happy path: zero overhead. Observed failure-path cost: ~1.5–5s vs ~70–110s full replan |
| Failed edit/read stuck guard | Consecutive `edit_failed` attempts with the same file and find string auto-fail the task and trigger replan. Repeated duplicate reads are skipped once with a typed observation carrying the exact continuation cursor/limit/hash, then auto-fail as `stuck_loop`. Read identity includes `offset` + `limit` for initial ranges and `cursor` + `limit` + `sha256` for continuation pages, so legitimate navigation is not suppressed. Same-chunk append repeats auto-fail as `stuck_loop` |
| App-dev action surface (issues #7, #30) | Losslessly resumable `read` pages with exact content, source-bound cursors, and hash-linked totals; bounded, explicitly incomplete `search`/`tree` discovery; atomic `write`/`edit` with chunked `append`; per-action observation budgets; `finish_reason` logged per LLM attempt; typed `malformed_action`/`response_truncated` parse failures; and selected/executed/skipped step accounting. Deterministic coverage reconstructs long-line, wide multiline, CRLF, and multibyte sources through EOF and checks bounded discovery caps, snippet clipping, unreadable files, and traversal errors. Protocol revisions 2 and 5 are recorded in `tests/workflows/PROTOCOL.md` |
| Sentinel content transport (issue #15) | The 2026-08-01 v4 canary lost Gemma's repeated implementation writes at the 1536-token cap because whole files rode inside JSON strings — all-or-nothing truncation. Revision 3 moves `write` content between sentinel lines after the action JSON: no escaping, and a truncated block keeps its complete lines and continues through the existing chunked `append` machinery |
| Backend-aware budgets (issue #15) | 256/512-token step caps are a wall-clock constraint at ~7 tok/s locally but unnecessary on OpenRouter. `STEP_TOKENS` is 4096 and `STEP_WRITE_TOKENS` 8192 on OpenRouter; local values are unchanged, but the local-neutrality bar was waived and no local-regression claim is made |
| Write-forcing policy (issue #15) | The 2026-08-01 Qwen canary executed 27 observation steps and never selected a write. On write-shaped tasks: pressure note in the executor prompt after 3 observations with no commit, observation blocked in the last 3 steps of an attempt (skip once, then auto-fail), and `no_write_executed` surfaced in full and task-local replan state |
| Validate-after-write policy (revision 4) | Repeated same-target full writes trigger shell/edit/done pressure and then `rewrite_loop` damping across task-local and full-replan boundaries; `no_write_executed`, `unvalidated_write`, and `failed_steps` are task-scoped, while incomplete artifacts remain visible run-wide across replacement tasks |
| Curated replan state | Planner sees `completed_tasks`/`errors`/`environment`/`policy` plus a `recent_steps` digest; raw write contents stay out of planner prompts. Task-local replanner additionally sees a `failed_steps` digest |
| Planner thinking | `gated` requests it only for second/later planning attempts or stateful direct replans; `off` suppresses the effective level. Benchmarked: requesting it on the first plan caused JSON truncation on the local 768-token budget and no quality gain on either backend |
| Duplicate action guard | Per-action-type loop detection that never proves completion (issue #68). `write(same content)` → skip+continue. `shell(same+ok)` → skip once with "Already ran" observation, then auto-fail as stuck if repeated — never auto-done. `shell(same+fail)` → auto-fail (stuck). `read(same+ok)` → skip once with "Already read" observation, then auto-fail if repeated. First skip injects corrective observation only; 2+ consecutive write skips activate thinking |
| Write content comparison | Duplicate guard on `write` must compare content, not just arg — matching only on `(action, arg)` would kill the write → compile fail → write fix pattern |
| `_content` in step dict | Stored for duplicate detection; excluded from slim state via underscore-prefix convention |
| Multi-turn rejected | Accumulated prior turns bloat context (500-800 tokens across 3 turns) vs curated slim state (~150-200). Revisit with a stronger local model and more context headroom |
| Null content (OpenRouter) | Parasail reasoning can return `content: null` when reasoning exhausts `max_tokens` — fall back to `reasoning_content` / `reasoning` fields, then require result is a dict (non-dict like `null`/`[]` triggers retry) |
| Effort + max_tokens (OpenRouter) | `reasoning.effort` and `reasoning.max_tokens` are mutually exclusive; API returns 400. Use `effort` only; floor outer `max_tokens` at 1024/1536/2048 per effort |
| `<\|think\|>` prefix (local) | Prepended to system prompt to enable thinking; `max_tokens` must be bumped 256→512→768 as thinking tokens share the budget |

## Current Constraints

Active limitations that still shape the design.

- **Feature-scale structured writes can exceed the ordinary action budget.** In one frozen FeatureBench fast canary, the 512-token non-reasoning cap bound implementation writes: after four reads and three planning attempts, the agent emitted zero writes and an empty patch. This is one-task evidence, not a reliability, model-family, or model-size result; it motivates chunked writes, localized edits, or adaptive action budgets rather than proving that a larger cap alone is sufficient. See the [published result](../tests/featurebench/results/2026-07-13-gemma-4-31b-canary.json). Chunked `append` writes, ranged reads, and per-action budgets now exist (issue #7, protocol revision 2). Revision 3 bundled sentinel transport, larger OpenRouter budgets, and write pressure after the v4 canaries and an exploratory pi run. In one v6 attempt per model on a changed serving stack, both cells left applied-but-unresolved patches (Gemma 11/13 target tests; Qwen 7/13); neither ran the delivered tests or terminated cleanly. These observations show that the harness was consequential on this task, but they do not isolate transport, establish a pi ceiling, or support local-performance or general readiness claims.
- **Commit-without-validate rewrite loop (revision 3, observed 2026-08-01).** Under the bundled revision-3 changes on CoreWeave, Gemma 4 31B rewrote the same implementation file 18 times without running the delivered tests or emitting `done`. The externally evaluated patch was applied but unresolved at 11/13 target tests. Interface revision 4 mechanically guards this trajectory with shell/edit/done pressure, rewrite damping, and distinct `unvalidated_write`/`incomplete_write` replan state. This is implemented behavior, not evidence of improved task outcomes; outcome-bearing requalification requires a registered matched-provider v7 protocol.
- **Write content truncation (local Gemma 4 E4B).** The 256-token executor budget can't fit multi-line file content with escapes. The `edit` action is the primary workaround for localized changes; for new large files, chunked `append` writes assemble the file in budget-sized pieces, and a truncated `write`/`edit` payload that fails to parse retries with a `STEP_WRITE_TOKENS` budget.
- **JSON parse failures on already-solved tasks (local).** When the planner emits a task that's already complete, the local model sometimes generates verbose reasoning text instead of `{"action":"done"}`, exhausting token budgets across retries. Mitigation: executor sees `completed_tasks` in slim state and emits `done` on step 1 in the normal case; conditional validation may catch some that slip through.
- **Path truncation in long temp paths (local).** The local model reproduces long absolute paths in shell commands and sometimes truncates them. `SYSTEM_STEP` recommends relative paths; the 26B model on OpenRouter handles this correctly.
- **Action looping (Gemma 4 26B via OpenRouter).** The 26B model occasionally repeats the same successful write action 2-3 times before emitting `done`. Handled by the duplicate guard at the framework level.
- **`--cache-reuse` requires `--swa-full` for Gemma 4 iSWA.** Fixed upstream via [#22288](https://github.com/ggml-org/llama.cpp/pull/22288) (build `a702f395`+). Current default: `--swa-full --cache-reuse 256`. Not compatible with `--mmproj`. See [gemma4-setup.md](gemma4-setup.md).
- **Replan thinking latency.** ~73s per replan on local (thinking shares the 768-token planner budget). Justified by better error analysis on recovery plans; not justified on first plans.
- **Parse-retry thinking latency.** E03 mitigates: `_repair_json` salvages truncated JSON without retrying; final auto-retry (attempt 2) uses a strict contract with no explicit reasoning instead of escalating to high. Caller-specified levels remain active under `gated` and are suppressed under `off`. Upstream has no grammar+reasoning coexistence solution ([#12276](https://github.com/ggml-org/llama.cpp/issues/12276)) and `--json-schema` is broken for Gemma 4 ([#22396](https://github.com/ggml-org/llama.cpp/issues/22396)).
- **Shell error classification is heuristic.** `classify_error(output, cmd)` uses substring matching for shell output. E16 hardened this: compiler-family commands (`cc`, `gcc`, `g++`, `clang`, `make`, `cargo build`, etc.) now prefer `compile_error` for ambiguous diagnostics like `No such file or directory`. Edit-origin errors are not affected because they are tagged deterministically in `execute()`.
- **The reasoning-off request contract assumes hybrid models.** Ordinary OpenRouter calls send `reasoning.enabled=false`, which always-on reasoners (harmony-format gpt-oss) cannot honor — they reason at the provider-default effort regardless, spending unbudgeted completion tokens outside the harness's gating. `OPENROUTER_REASONING_EFFORT` pins such models to a declared baseline instead (E21). This declares the requested effort; it does not verify the served effort — check the `thinking`/`completion` fields in `tokens` events for drift.
- **Done-emission loops on QAT weights (local, observed 2026-08-03).** On the promoted E4B QAT Q4_0 model the dominant local failure class shifted: the model completes all steps correctly, then re-emits duplicate successful actions (reads and writes, not just edits) instead of `done` until the step budget exhausts — 3 of 5 E23 bench pytest failures, always with a correct deliverable on disk. Per the issue #68 design, repetition is never acceptance and these runs correctly end `exhausted`; the sanctioned lever to evaluate against this class is the lifecycle step policy (`AGENT_STEP_POLICY=lifecycle`), which steers repetition toward verification — the E23 bench ran the default heuristic arm. See the [PERFORMANCE.md E23 entry](PERFORMANCE.md#e23-qat-baseline--2026-08-03-local-build-9618-official-e4b-qat-q4_0).
- **Content drift on whole-file rewrites (local QAT, observed 2026-08-03).** Asked to fix a one-character syntax error, QAT rewrote the file via `write` and changed unrelated content (`"hello"` → `"Hello"`) in 3/3 trials — the program runs, semantics drifted, the deterministic postcondition fails while the run reports `complete`. Motivates a prompt nudge toward `edit` for localized fixes and the goal-output arm of E07 validation.

## Multi-Backend Support

`askme.py` supports two backends, selected by `LLM_BACKEND`:
- **Local** (default): llama-server on `localhost:8080`, model configured at server launch.
- **OpenRouter**: any OpenAI-compatible model via OpenRouter API. `OPENROUTER_PROVIDER` sets the provider preference (Parasail by default); `OPENROUTER_ALLOW_FALLBACKS=0` makes that selection strict, and `OPENROUTER_REQUIRE_PARAMETERS=1` rejects endpoints that do not advertise every requested parameter.

Thinking mechanisms differ:
- **OpenRouter**: `reasoning.enabled=true` with `reasoning.effort` ("low"/"medium"/"high" — gated escalation, raised to the optional `OPENROUTER_REASONING_EFFORT` baseline for always-on reasoners such as gpt-oss-20b). Reasoning tokens share the outer `max_tokens` on Parasail; `content` can be `null` if reasoning exhausts the budget.
- **Local**: `<|think|>` prepended to the system prompt; `<|channel>...<channel|>` blocks stripped from output. Note: since 2026-08-03 the recommended server launch uses `--reasoning off` (template auto-detection otherwise drains action budgets into `reasoning_content` — see gemma4-setup.md); whether the `<|think|>` escalation path still elicits thinking under that flag is unverified.

Env var reference lives in [configuration.md](configuration.md).

## Scope

**Can handle:**
- File creation/editing sequences
- Build, test, fix cycles
- Simple multi-step shell workflows
- Any task decomposable into 3-10 sequential steps

**Cannot handle:**
- Tasks requiring >16K context (large file analysis)
- Parallel/branching workflows
- Interactive programs
- Tasks needing >~30 LLM calls (too slow)

## References

- [README.md](../README.md) — usage, quickstart, tests
- [configuration.md](configuration.md) — env var reference and automation CLI
- [gemma4-setup.md](gemma4-setup.md) — server config, KV cache, model-specific notes
- [PERFORMANCE.md](PERFORMANCE.md) — benchmark history and test-run matrices
- [CLAUDE.md](../CLAUDE.md) — agent authoring guidance for this repo
- [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) — `--reasoning on`, `--reasoning-budget`, `--reasoning-format`
- [OpenRouter reasoning tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)
- [#21468](https://github.com/ggml-org/llama.cpp/issues/21468) — `--cache-reuse` broken for Gemma 4 iSWA
