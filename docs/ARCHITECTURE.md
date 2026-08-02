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
       ├── all tasks done? → conditional fail-open validation → exit success
       └── task failed? → replan (up to MAX_REPLANS)
```

A deterministic `preflight_probe()` runs once before the first plan: platform, arch, available/missing tools, package managers, working-dir listing. The structured result plus execution policy is fed into planner state.

## Core File

`agent/askme.py` is self-contained — no seed files, no framework.

**Key functions:**
- `preflight_probe(working_dir)` — environment probe (platform, arch, tools, package managers, dir listing)
- `get_policy()` — execution policy (`allow_system_installs`, `allow_network`)
- `_repair_json(text)` — mechanical JSON repair for truncation artifacts (trailing commas, unclosed braces, truncated keys); returns dict or None
- `ask_llm(messages, max_tokens, think, reasoning_policy, reasoning_trigger)` — calls a backend, logs the requested/effective explicit-reasoning decision for every attempt, strips `<think>`/`<|channel>` blocks and code fences, extracts JSON, attempts repair, then retries on parse failure (up to 2). E03: the final auto-retry uses a strict contract with no thinking
- `get_plan(user_prompt, state)` — task list. Under the default `gated` policy, explicit reasoning is off for the first plan and enabled for replans
- `get_step(task, state, goal, reasoning_policy, goal_context_chars, write_pressure)` — next action within a task. Goal-aware completion uses the per-run frozen goal-context view rather than the generic field cap; `write_pressure` appends a commit-demanding note on stalled write-shaped tasks
- `replan_task(failed_task, errors, completed_tasks, state, user_prompt)` — mini-planner for E11: generates a replacement task description for a single failed task. Cheap no-thinking call (`max_tokens=96`, no retries). Returns string or None. Includes policy/missing_tools in state and rejects exact duplicates, near duplicates, and passive downgrades
- `classify_error(output, cmd)` — categorizes as `timeout`, `missing_tool`, `permission_denied`, `missing_file`, `compile_error`, or `unknown`. Command-aware: compiler-family commands prefer `compile_error` over `missing_file` for ambiguous diagnostics (E16)
- `summarize_errors(errors)` — groups, deduplicates, caps at 3 per type for planner
- `execute(action, working_dir)` — runs shell/write/edit/read/search/tree/done/fail with command-aware timeouts, bounded observation windows, and atomic file writes
- `_validate_completion(...)` — post-completion LLM check; gated by `_should_validate()`
- `_run_loop(...)` — structured core loop with frozen task, step, replan, goal-context, and explicit-reasoning controls
- `run(user_prompt, working_dir=None)` — backward-compatible public wrapper returning a boolean

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

Every HTTP attempt emits a `reasoning_decision` JSONL event with the policy,
named trigger, requested level, effective level, and attempt number. The `off`
policy nulls the effective level even when a caller requests an explicit level.
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
| `write` | Create or replace a whole file | Atomic (temp file + rename). Dict/list content auto-serialized to JSON. `"append": true` appends one chunk — chunked-write transport for files larger than the executor token budget. Content may also travel between sentinel lines after the action JSON (revision 3) — no escaping, truncation recoverable |
| `edit` | Exact single-match string replacement | For localized changes. Fails on zero or multiple matches. Atomic write |
| `read` | Ranged read of a file window | `offset`/`limit` (1-based lines); returns a header `[file: lines X-Y of N; continue: offset=Z]` plus the bounded window. `truncated`/`continuation` fields drive navigation; `total_lines`/`total_bytes`/`sha256` fields hash-link each read for the run-log audit (not sent to the model) |
| `search` | Bounded literal search | `arg` = literal pattern, optional `path` (default `.`). Skips VCS/dependency/hidden/binary files; caps matches and chars; suggests narrowing when capped |
| `tree` | Bounded repository listing | `arg` = directory (default `.`). Depth-, entry-, and char-capped; directories marked with `/` |
| `done` | Mark current task complete | Terminal |
| `fail` | Mark current task failed | Triggers replan |

`edit` exists because full-file `write` content frequently exceeds the local model's 256-token executor budget on multi-line files. Edit payloads fit in ~40-80 tokens; the 26B model on OpenRouter also spontaneously prefers `edit` for fixes. For new large files, chunked `append` writes cover what `edit` cannot.

Observation actions (`read`/`search`/`tree`) carry their own budgets (issue #7): results are bounded by `READ_CHARS`/`SEARCH_MAX_CHARS`/`TREE_MAX_CHARS`, flagged with `truncated`, and kept in executor step history up to `OBSERVE_STATE_CHARS` (vs 100 chars for mutating actions) — large files stay navigable without flooding executor state. On the model-output side, `ask_llm` records `finish_reason` in every `tokens` JSONL event; when a truncated `write`/`edit` payload fails to parse, the retry gets a payload-sized budget (`STEP_WRITE_TOKENS`) instead of more reasoning, and an unrecoverable parse failure surfaces as a typed `[malformed_action]` or `[response_truncated]` error (the latter when the final attempt hit the token budget) that the replanner sees.

Interface revision 3 (issue #15) adds a sentinel-framed content transport: `write` content may follow the action JSON between `<<<CONTENT` / `CONTENT>>>` lines instead of riding inside a JSON string. This removes escaping overhead and makes truncation recoverable — when the closing sentinel is missing and `finish_reason=length`, `ask_llm` returns the partial content with `content_truncated` (a cutoff on a line boundary keeps its last complete line), the run loop writes the complete lines that arrived, and the step output carries a resume anchor — line count plus the last written line, kept under the observation-class history budget because the stateless executor navigates by it — steering the model to finish the file with chunked `append` (a re-emitted identical truncated write is answered with the same continuation hint, not "already done"; a truncation that yields no complete line is retried as a non-append write so a stale existing file is never appended to). The closing sentinel must be flush-left and is matched from the end of the response, so content lines that merely resemble it stay content; a file whose genuinely-final flush-left line is the sentinel itself must use JSON `content` or chunked `append` instead. The executor also runs under a write-forcing policy on write-shaped tasks (`_WRITE_TASK_RE`): after `WRITE_PRESSURE_OBSERVATIONS` executed observation steps with no committing action, the step prompt requires a `write`/`edit`/`shell` next; observation actions cannot consume the last `OBSERVE_TAIL_RESERVE` steps of an attempt (one warning skip, then auto-fail as `stuck_loop`); and replan state gains a first-class `no_write_executed` flag — scoped to the failed task's steps, so an earlier task's write cannot mask a later stall — so replans address the stall instead of restating the task.

Interface revision 4 responds to the rewrite loop observed in the 2026-08-01 v6 canary under the bundled revision-3 changes and a changed serving stack: Gemma rewrote one file 18 times without running the delivered tests or emitting `done`. After `REWRITE_PRESSURE_WRITES` consecutive successful full writes of the same target with no intervening successful `shell`/`edit`, the step prompt requires a shell action, a targeted edit, or `done`; from `REWRITE_SKIP_WRITES` on, further full rewrites of that target are skipped (`rewrite_loop`) with a corrective hint. Observations do not reset the streak; a successful shell or targeted fix does. Any truncated write, including an empty or append attempt, clears the streak so the instructed clean restart cannot be blocked; ordinary appends are exempt from counting but do not clear an armed streak. Replan state distinguishes `unvalidated_write` (a complete mutation with no later successful shell) from `incomplete_write` (any target still has a truncated write not followed by a complete write/append to that same target). Edits and shells cannot reconstruct a missing suffix; unresolved incomplete state blocks `done` and deterministic exhaustion reconciliation. These write-state flags and task-local `failed_steps` use the current task's slice even when it is empty, so a zero-dispatch failure reports `no_write_executed` without leaking prior-task evidence. Empty task lists are rejected as malformed plans, and a failed final validation cannot be bypassed by a recovery without new successful write, edit, or shell evidence. The same revision folds in the three post-merge Codex P2 fixes from PR #16: `commit_executed` counts only successful mutations, write intent is classified from the failed task rather than the whole request, and `_WRITE_TASK_RE` drops passive-prone `include` plus exempts tasks led by an observation verb (`_is_write_shaped`). A future explicit inspect → modify → verify → finish controller is tracked separately in issue #31; revision 4 does not prove that a successful shell was targeted verification.

The run loop also accounts for selected vs executed actions: every action the executor emits increments `selected_steps`, only dispatched ones increment `executed_steps`, and each guard-suppressed one increments `skipped_steps` and logs a `step_skipped` JSONL event with a typed reason (`duplicate_read`, `stuck_read`, `stuck_append`, …). The 2026-07-31 Qwen canary selected 14 reads of which only 2 executed — that gap is now first-class in `run_end` metrics instead of being reconstructed from logs.

## Failure and Replanning

On task failure:
1. Error is classified into a typed category — deterministic tags for edit scaffold failures (`edit_failed`) and missing edit targets (`missing_file`), heuristic `classify_error()` for shell output and exception paths
2. Error-class-specific retry policy (E05): under `gated`, structural failures (`edit_failed`, `missing_file`, `timeout`, `missing_tool`, `permission_denied`) skip explicit-reasoning escalation while semantic failures (`compile_error`, `unknown`) request it; `off` suppresses both paths
3. Recovery hints (E06): typed failures inject a short hint into step output (e.g., "Read the file first" for `edit_failed`) so the model knows what to do next without thinking tokens
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
- **LLM call**: `ask_llm(..., max_tokens=768, think=True, think_level="high", max_retries=0)` — requests one high explicit-reasoning attempt under `gated`; `off` suppresses the reasoning request. There are no retries. Returns `{"valid": true}` or `{"valid": false, "reason": "...", "missing": [...]}`.
- **Fail-open**: transport errors, parse errors, or unexpected formats on the first optional check → treated as valid. Once a validator has explicitly returned `valid: false`, an unavailable recheck cannot erase that known failure.
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
| `MAX_INPUT` | 300 | Max chars per non-goal field sent to executor |
| `GOAL_CONTEXT_CHARS` | 300 | Default executor/task-replan goal view; independently configurable and frozen per run |
| `SHELL_TIMEOUT` | 30s | Default per-command timeout |
| `SHELL_TIMEOUT_LONG` | 120s | Timeout for install/build commands |
| `SHELL_TIMEOUT_MAX` | 300s | Hard cap for model-specified timeout hint |
| `PLANNER_MAX_TOKENS` | 768 | Task list budget; shared with thinking on replans |
| `ALLOW_SYSTEM_INSTALLS` | false | Prompt-visible install policy; not host-level enforcement |
| `ALLOW_NETWORK` | true | Reserved for future use |
| Step output | 100 chars | Max output stored per mutating-action step in history |
| Observation step output | 1500 chars | `OBSERVE_STATE_CHARS` — history budget for `read`/`search`/`tree` results |
| Read window | 60 lines / 1200 chars | `READ_LINES` / `READ_CHARS`; `READ_LIMIT_MAX=200` caps model-specified limit |
| Search bound | 15 matches / 1500 chars / 500 files | `SEARCH_MAX_MATCHES` / `SEARCH_MAX_CHARS` / `SEARCH_MAX_FILES` |
| Tree bound | 60 entries / 1500 chars / depth 3 | `TREE_MAX_ENTRIES` / `TREE_MAX_CHARS` / `TREE_MAX_DEPTH` |
| Write-payload retry budget | 512 (local) / 8192 (OpenRouter) | `STEP_WRITE_TOKENS` — bumped when a truncated write/edit payload fails to parse |
| Step tokens (local) | 256 | `STEP_TOKENS` — max completion tokens (local) |
| Step tokens (OpenRouter) | 4096 | `STEP_TOKENS` — sized for implementation writes (issue #15); an 8KB file is ~3000 tokens |
| `WRITE_PRESSURE_OBSERVATIONS` | 3 | Executed observation steps before the executor prompt demands a committing action (write-shaped tasks) |
| `OBSERVE_TAIL_RESERVE` | 3 | Final steps per task attempt reserved for committing actions on write-shaped tasks |
| Thinking tokens (local) | 512 (medium) / 768 (high) | Must be bumped when thinking is enabled |
| Thinking tokens (OpenRouter) | 1536 (medium) / 2048 (high) | Reasoning tokens share budget with Parasail |

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
| JSON repair (E03) | `_repair_json` attempts mechanical fixes (close braces, strip trailing commas/truncated fields) before burning a retry. Guarantees valid JSON structure but not complete action fields — downstream KeyError/validation paths still handle missing fields |
| Strict final retry (E03) | Final auto-retry (attempt 2) disables explicit reasoning and appends a strict JSON-only instruction. Caller-specified levels (for example `_validate_completion`) are respected under `gated` and suppressed under `off`. Upstream has no grammar+reasoning coexistence solution |
| Recovery hints (E06) | Short hint appended to step output after typed failures. Model can override — hints are nudges, not commands. Only `edit_failed` and `missing_file` have hints; adding more requires evidence of wasted thinking cycles |
| Task-local replan (E11) | On task failure, a mini-planner (`SYSTEM_TASK_REPLAN`) generates a replacement task before burning a full replan. Capped at 1 attempt; no-thinking, `max_tokens=96`, `max_retries=0`; exact/near duplicates and passive downgrades rejected with `reject_reason`. Happy path: zero overhead. Observed failure-path cost: ~1.5–5s vs ~70–110s full replan |
| Failed edit/read stuck guard | Consecutive `edit_failed` attempts with the same file and find string auto-fail the task and trigger replan. Repeated duplicate reads are skipped once with a typed observation (continuation offset when the window was truncated), then auto-fail as `stuck_loop`. Read duplicate detection is offset-aware — navigating to a new range of the same file is legitimate. Same-chunk append repeats auto-fail as `stuck_loop` |
| App-dev action surface (issue #7) | Ranged `read` windows with continuation metadata and hash-linked totals, bounded `search`/`tree` actions, atomic `write`/`edit` with chunked `append`, per-action observation budgets with `truncated` flags, `finish_reason` logged per LLM attempt, typed `malformed_action`/`response_truncated` parse failures, and selected/executed/skipped step accounting. Deterministic coverage in `tests/test_agent_actions.py` (incl. an end-to-end synthetic-repo case: symbol beyond the first read window, patch > 512 tokens); protocol revision 2 in `tests/workflows/PROTOCOL.md` |
| Sentinel content transport (issue #15) | The 2026-08-01 v4 canary lost Gemma's repeated implementation writes at the 1536-token cap because whole files rode inside JSON strings — all-or-nothing truncation. Revision 3 moves `write` content between sentinel lines after the action JSON: no escaping, and a truncated block keeps its complete lines and continues through the existing chunked `append` machinery |
| Backend-aware budgets (issue #15) | 256/512-token step caps are a wall-clock constraint at ~7 tok/s locally but unnecessary on OpenRouter. `STEP_TOKENS` is 4096 and `STEP_WRITE_TOKENS` 8192 on OpenRouter; local values are unchanged, but the local-neutrality bar was waived and no local-regression claim is made |
| Write-forcing policy (issue #15) | The 2026-08-01 Qwen canary executed 27 observation steps and never selected a write. On write-shaped tasks: pressure note in the executor prompt after 3 observations with no commit, observation blocked in the last 3 steps of an attempt (skip once, then auto-fail), and `no_write_executed` surfaced in full and task-local replan state |
| Validate-after-write policy (revision 4) | Repeated same-target full writes trigger shell/edit/done pressure and then `rewrite_loop` damping; write-state flags and task-local `failed_steps` are scoped to the failed task, distinguishing complete-but-unvalidated from truncated/incomplete artifacts |
| Curated replan state | Planner sees `completed_tasks`/`errors`/`environment`/`policy` plus a `recent_steps` digest; raw write contents stay out of planner prompts. Task-local replanner additionally sees a `failed_steps` digest |
| Planner thinking | `gated` requests it only for replans (`think=bool(errors or completed_tasks)`); `off` suppresses the effective level. Benchmarked: requesting it on the first plan caused JSON truncation on the local 768-token budget and no quality gain on either backend |
| Duplicate action guard | Per-action-type loop detection. `write(same content)` → skip+continue. `shell(same+ok)` → auto-done. `shell(same+fail)` → auto-fail (stuck). `read(same+ok)` → skip once with "Already read" observation, then auto-fail if repeated. First skip injects corrective observation only; 2+ consecutive write skips activate thinking |
| Write content comparison | Duplicate guard on `write` must compare content, not just arg — matching only on `(action, arg)` would kill the write → compile fail → write fix pattern |
| `_content` in step dict | Stored for duplicate detection; excluded from slim state via underscore-prefix convention |
| Multi-turn rejected | Accumulated prior turns bloat context (500-800 tokens across 3 turns) vs curated slim state (~150-200). Revisit with a stronger local model and more context headroom |
| Null content (OpenRouter) | Parasail reasoning can return `content: null` when reasoning exhausts `max_tokens` — fall back to `reasoning_content` / `reasoning` fields, then require result is a dict (non-dict like `null`/`[]` triggers retry) |
| Effort + max_tokens (OpenRouter) | `reasoning.effort` and `reasoning.max_tokens` are mutually exclusive; API returns 400. Use `effort` only; bump outer `max_tokens` to 1536/2048 |
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

## Multi-Backend Support

`askme.py` supports two backends, selected by `LLM_BACKEND`:
- **Local** (default): llama-server on `localhost:8080`, model configured at server launch.
- **OpenRouter**: any OpenAI-compatible model via OpenRouter API. `OPENROUTER_PROVIDER` sets the provider preference (Parasail by default); `OPENROUTER_ALLOW_FALLBACKS=0` makes that selection strict, and `OPENROUTER_REQUIRE_PARAMETERS=1` rejects endpoints that do not advertise every requested parameter.

Thinking mechanisms differ:
- **OpenRouter**: `reasoning.enabled=true` with `reasoning.effort` ("medium"/"high"). Reasoning tokens share the outer `max_tokens` on Parasail; `content` can be `null` if reasoning exhausts the budget.
- **Local**: `<|think|>` prepended to the system prompt; `--reasoning on` at server launch. `<|channel>...<channel|>` blocks stripped from output.

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
