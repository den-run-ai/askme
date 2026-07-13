# AskMe — Minimal Local Agent for Constrained LLMs

Designed for: Gemma 4 E4B (dense PLE, 4.5B effective / 8B including embeddings), 16K context, 16GB M1 Mac.

For usage, quickstart, and test commands see [README.md](README.md). For model/server/runtime configuration see [gemma4-setup.md](gemma4-setup.md). For benchmark history and test-run matrices see [PERFORMANCE.md](PERFORMANCE.md).

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
       ├── all tasks done? → final validation → exit success
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
- `get_step(task, state, goal, reasoning_policy, goal_context_chars)` — next action within a task. Goal-aware completion uses the per-run frozen goal-context view rather than the generic field cap
- `replan_task(failed_task, errors, completed_tasks, state, user_prompt)` — mini-planner for E11: generates a replacement task description for a single failed task. Cheap no-thinking call (`max_tokens=96`, no retries). Returns string or None. Includes policy/missing_tools in state and rejects exact duplicates, near duplicates, and passive downgrades
- `classify_error(output, cmd)` — categorizes as `timeout`, `missing_tool`, `permission_denied`, `missing_file`, `compile_error`, or `unknown`. Command-aware: compiler-family commands prefer `compile_error` over `missing_file` for ambiguous diagnostics (E16)
- `summarize_errors(errors)` — groups, deduplicates, caps at 3 per type for planner
- `execute(action, working_dir)` — runs shell/write/edit/read/done/fail with command-aware timeouts
- `_validate_completion(...)` — post-completion LLM check; gated by `_should_validate()`
- `_run_loop(...)` — structured core loop with frozen task, step, replan, goal-context, and explicit-reasoning controls
- `run(user_prompt, working_dir=None)` — backward-compatible public wrapper returning a boolean

**State** is an in-memory dict (no files). Planner and executor see different views.

Planner (`get_plan`) — full state:
```json
{
  "completed_tasks": ["task1"],
  "errors": ["[missing_tool] shell go run: /bin/sh: go: command not found"],
  "environment": {"platform": "darwin", "arch": "arm64", "available_tools": ["python3", "gcc"], "missing_tools": ["go"], "package_managers": ["brew"], "dir_listing": ["main.go"]},
  "policy": {"allow_system_installs": false, "allow_network": true}
}
```

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
- `last_steps` is a sliding window of `MAX_STEP_HISTORY=3`, step output truncated to 100 chars
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
[`tests/workflows/PROTOCOL.md`](tests/workflows/PROTOCOL.md).

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
| `write` | Create or replace a whole file | For new files. Dict/list content auto-serialized to JSON |
| `edit` | Exact single-match string replacement | For localized changes. Fails on zero or multiple matches |
| `read` | Read file contents | Truncates large files |
| `done` | Mark current task complete | Terminal |
| `fail` | Mark current task failed | Triggers replan |

`edit` exists because full-file `write` content frequently exceeds the local model's 256-token executor budget on multi-line files. Edit payloads fit in ~40-80 tokens; the 26B model on OpenRouter also spontaneously prefers `edit` for fixes.

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
- **Fail-open**: transport errors, parse errors, or unexpected formats → treated as valid. Agent never fails due to validation infrastructure issues.
- **Single-shot**: `validated_once` flag prevents re-validation after a recovery replan. If validation fails, it triggers one replan with `[validation_failed]` error; the recovery plan succeeds or fails on its own merits.

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
| `ALLOW_SYSTEM_INSTALLS` | false | Whether agent may install software |
| `ALLOW_NETWORK` | true | Reserved for future use |
| Step output | 100 chars | Max output stored per step in history |
| Step tokens (local) | 256 | Max completion tokens (local) |
| Step tokens (OpenRouter) | 512 | Max completion tokens (OpenRouter) |
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
| Failed edit/read stuck guard | Consecutive `edit_failed` attempts with the same file and find string auto-fail the task and trigger replan. Repeated duplicate reads are skipped once, then auto-fail as `stuck_loop`. This preserves cheap first recovery while preventing no-thinking loops |
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

- **Feature-scale structured writes can exceed the ordinary action budget.** In one frozen FeatureBench fast canary, the 512-token non-reasoning cap bound implementation writes: after four reads and three planning attempts, the agent emitted zero writes and an empty patch. This is one-task evidence, not a reliability, model-family, or model-size result; it motivates chunked writes, localized edits, or adaptive action budgets rather than proving that a larger cap alone is sufficient. See the [published result](tests/featurebench/results/2026-07-13-gemma-4-31b-canary.json).
- **Write content truncation (local Gemma 4 E4B).** The 256-token executor budget can't fit multi-line file content with escapes. The `edit` action is the primary workaround for localized changes; `write` is practical mainly for new files.
- **JSON parse failures on already-solved tasks (local).** When the planner emits a task that's already complete, the local model sometimes generates verbose reasoning text instead of `{"action":"done"}`, exhausting token budgets across retries. Mitigation: executor sees `completed_tasks` in slim state and emits `done` on step 1 in the normal case; final validation catches any that slip through.
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

Env var reference lives in [README.md](README.md).

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

- [README.md](README.md) — usage, quickstart, tests
- [gemma4-setup.md](gemma4-setup.md) — server config, KV cache, model-specific notes
- [PERFORMANCE.md](PERFORMANCE.md) — benchmark history and test-run matrices
- [CLAUDE.md](CLAUDE.md) — agent authoring guidance for this repo
- [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) — `--reasoning on`, `--reasoning-budget`, `--reasoning-format`
- [OpenRouter reasoning tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)
- [#21468](https://github.com/ggml-org/llama.cpp/issues/21468) — `--cache-reuse` broken for Gemma 4 iSWA
