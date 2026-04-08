# NanAgent — Minimal Local Agent for Constrained LLMs

Designed for: Gemma 4 E4B (MoE 12B/4B active), 16K context, 16GB M1 Mac.

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
│  execute()    │  ← runs shell / write / read
│  update state │  ← append step result to state
└──────┬───────┘
       │
       ├── all tasks done? → exit success
       └── task failed? → replan (up to 3 attempts)
```

## Core File

### `agent/askme.py` — self-contained, no seed files needed

```python
python3 agent/askme.py "create hello.c, compile it, run it"
```

**Key functions:**
- `preflight_probe(working_dir)` — deterministic environment probe. Returns structured dict: platform, arch, working_dir, available/missing tools, package managers, dir listing. Called once before first plan.
- `get_policy()` — returns execution policy dict (`allow_system_installs`, `allow_network`). Injected into both planner and executor state.
- `ask_llm(messages, max_tokens, think)` — calls llama-server, strips `<think>` tags (closed and unclosed) as safety net, code fences, extracts JSON from mixed text, retries on parse failure (up to 2 retries).
- `get_plan(user_prompt, state)` — asks LLM for a task list (max_tokens=PLANNER_MAX_TOKENS=768). Thinking is conditional: `think=bool(errors or completed_tasks)` — off for first plan, on for replans. Receives full state including completed tasks, typed/summarized errors, environment, and policy.
- `get_step(task, state, goal="")` — asks LLM for next action within a task (max_tokens=256). Receives a slim state: current task, task index, last 3 steps, missing_tools, and policy. Goal-aware completion — executor must satisfy full task, not just one step.
- `classify_error(output, action)` — categorizes errors as `timeout`, `missing_tool`, `permission_denied`, `missing_file`, `compile_error`, or `unknown`.
- `summarize_errors(errors)` — groups raw errors by type, deduplicates, caps at 3 per type for planner.
- `execute(action, working_dir)` — runs shell/write/edit/read/done/fail actions. `edit` does exact single-match string replacement (fails on zero or multiple matches). Uses command-aware timeouts.
- `run(user_prompt)` — outer loop: preflight → plan → execute tasks → replan on failure. Resets errors after each replan.

**State** is an in-memory dict (no files). Planner and executor see different views:

Planner (`get_plan`) receives full state:
```json
{
  "completed_tasks": ["task1"],
  "errors": ["[missing_tool] shell go run: /bin/sh: go: command not found"],
  "environment": {"platform": "darwin", "arch": "arm64", "available_tools": ["python3", "gcc"], "missing_tools": ["go"], "package_managers": ["brew"], "dir_listing": ["main.go"]},
  "policy": {"allow_system_installs": false, "allow_network": true}
}
```

Executor (`get_step`) receives slim state (token-optimized):
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
Key state design decisions:
- `last_steps` is capped at `MAX_STEP_HISTORY=3` (sliding window), step output truncated to 100 chars
- `completed_tasks` (last 3, truncated to 80 chars each) gives executor cross-task awareness
- Last step from previous task carries over (not wiped) so executor knows what just happened
- Write/read `arg` fields use basename (e.g. `main.c` not `/full/path/main.c`) to save tokens
- Write output says `"Wrote main.c"` (basename) not `"Wrote /full/path/..."` — this is critical for the LLM to recognize the file was created

**Two system prompts:**
- `SYSTEM_PLAN` — planner role, outputs `{"tasks": [...]}`. Gets full user prompt + full state.
- `SYSTEM_STEP` — executor role, outputs `{"action": "...", "arg": "...", "content": "...", "reasoning": "..."}`. Gets original goal + current task description + slim state.

## Safety Limits

| Constant | Value | Purpose |
|---|---|---|
| `MAX_REPLANS` | 3 | Max plan attempts before giving up |
| `MAX_TASKS` | 10 | Max tasks per plan |
| `MAX_STEPS` | 10 | Max actions per task |
| `MAX_RESULT` | 300 | Chars kept from command output |
| `MAX_STEP_HISTORY` | 3 | Sliding window of recent steps sent to executor |
| `MAX_LLM_RETRIES` | 2 | Retries per LLM call on JSON parse failure |
| `MAX_INPUT` | 300 | Max chars per field sent to executor |
| `SHELL_TIMEOUT` | 30s | Default per-command timeout |
| `SHELL_TIMEOUT_LONG` | 120s | Timeout for install/build commands |
| `SHELL_TIMEOUT_MAX` | 300s | Hard cap for model-specified timeout hint |
| `ALLOW_SYSTEM_INSTALLS` | false | Whether agent may install software |
| `ALLOW_NETWORK` | true | Reserved for future use |
| Step output | 100 chars | Max output stored per step in history |
| Step tokens (local) | 256 | Max completion tokens for executor (local LLM) |
| Step tokens (OpenRouter) | 512 | Max completion tokens for executor (remote LLM) |
| Thinking tokens (local, medium) | 512 | Max tokens when thinking enabled (local, medium effort) |
| Thinking tokens (local, high) | 768 | Max tokens when thinking enabled (local, high effort) |
| Thinking tokens (OpenRouter, medium) | 1536 | Max tokens when thinking enabled (OpenRouter, medium) |
| Thinking tokens (OpenRouter, high) | 2048 | Max tokens when thinking enabled (OpenRouter, high) |

## Multi-Backend Support

`askme.py` supports two backends, configured via environment variables:

**Local** (default): llama-server on localhost:8080
```bash
python3 agent/askme.py "your request"
```

**OpenRouter**: any OpenAI-compatible model via OpenRouter API
```bash
LLM_BACKEND=openrouter python3 agent/askme.py "your request"
```

| Env var | Default | Purpose |
|---|---|---|
| `LLM_BACKEND` | `local` | `local` or `openrouter` |
| `OPENROUTER_API_KEY` | (from .env) | API key for OpenRouter |
| `OPENROUTER_MODEL` | `google/gemma-4-26b-a4b-it` | Model to use on OpenRouter |
| `LLM_API_URL` | `http://localhost:8080/v1/chat/completions` | Custom API URL (local only) |
| `LLM_MODEL` | `gemma-4-e4b` | Model name (local only) |

OpenRouter requests include `"provider": {"order": ["Parasail"]}` for reliable bf16 throughput.

## Gemma 4 Model Comparison

| Model | Architecture | "done" emission | Action looping | Speed | Notes |
|---|---|---|---|---|---|
| **Gemma 4 E4B** (local) | MoE 12B/4B active | Works after state fix (auto-done retained as safety net) | Duplicate write loops (same as 26B); write content truncation mitigated by `edit` action | ~7 tok/s | 9/9 integration tests pass (easy 3:20, medium 20:20, hard 16:49) |
| **Gemma 4 26B-A4B** (OpenRouter) | MoE 26B/4B active | Reliably emits done | Occasional write loops (3x before done); spontaneously prefers `edit` for fixes | ~1-2s/step | 9/9 integration tests pass (easy 38s, medium 87s, hard 176s) |
| Qwen 3.5 9B | Dense | Unreliable (think-tag issues) | N/A | ~3 tok/s | Legacy |

### Gemma 4 vs Qwen 3.5 (Why We Switched)

Qwen 3.5 has always-on thinking mode (`<think>` blocks) that caused major reliability issues:
- With `reasoning_format: "deepseek"` (default), thinking goes to `reasoning_content` field — but if `max_tokens` is exhausted during thinking, `content` stays empty
- With `reasoning_format: "none"`, thinking leaks into `content` as literal `<think>` text
- Required extensive workarounds: think-tag stripping, JSON extraction, higher max_tokens, retries

Gemma 4 E4B has opt-in thinking (not always-on), so:
- No `reasoning_format` parameter needed
- Think-tag stripping kept as safety net but rarely triggers
- More reliable JSON output with lower max_tokens
- MoE architecture (4B active) means potentially faster inference than dense 9B

## Design Decisions

| Constraint | Solution |
|---|---|
| Limited speed | Short prompts (~200 tok executor, ~500 tok planner), JSON-only output |
| 16K context | Slim executor state (sliding window), no history accumulation |
| Code fences | Strip ` ```json ``` ` wrappers from output |
| Reliability | JSON-only output, retries, safety limits, truncated results |
| No seed files | No state.json/plan.json needed |
| Failure recovery | Replan loop carries completed tasks forward; errors reset per replan |
| Token efficiency | Executor gets slim state (~150-200 tok) vs full state; planner gets full context |
| Observability | `log()` helper adds `[HH:MM:SS]` timestamps + `(Xs)` durations to all activities |
| Error truncation | `r.stdout[:300] + r.stderr[-300:]` — tail-truncates to keep actual error messages |
| Input caps | `MAX_INPUT=300` chars per field sent to executor — prevents path bloat eating context |
| Parse-error-as-failure | If `get_step()` raises after exhausting retries, task always fails and replans — no false auto-completion |
| Cross-task state | Last step from previous task carries over; `completed_tasks` included in slim state |
| Basename outputs | Write output says `"Wrote main.c"` not `"Wrote /full/path/main.c"` — LLM needs clear signal |
| Basename args | Slim step history uses basename for write/read `arg` fields — saves tokens, avoids confusion |
| Multi-backend | `LLM_BACKEND=openrouter` switches to OpenRouter API with configurable model and provider |
| Thinking-on-retry | Thinking enabled only after failure: zero cost on happy path, chain-of-thought on retry |
| Null content | OpenRouter reasoning can return `content: null` — falls back to `reasoning_content` / `reasoning` fields to recover JSON, then requires result is a dict (non-dict like `null` or `[]` triggers retry) |
| Dict/list content | Write actions auto-serialize dict/list content to JSON — models output objects instead of escaped strings |
| Duplicate guard | Per-action-type loop detection: write(same content)→auto-done, shell(same+ok)→auto-done, shell(same+fail)→auto-fail |

## Known LLM Limitations

### "done" Emission (Local Gemma 4 E4B) — Resolved

**Status:** Fixed as of 2026-04-07. The model now emits `{"action": "done"}` reliably.

**Root cause:** The "never emits done" behavior was actually caused by the **cross-task state bug** (empty `last_steps` at task boundaries), not a model capability gap. When the executor received an empty state, the model had no context about what was accomplished and couldn't determine that the task was complete. Once the state bug was fixed (last step carryover + completed_tasks in slim state), done emission started working on all easy tests.

**Auto-done heuristic removed** (2026-04-07) — previously treated parse errors after a successful step as implicit task completion. This was a false-success path: the prior step might have been partial (e.g. "write main.c" succeeded but task was "write and compile main.c"). Parse errors now always fail the task and trigger replan.

### JSON Parse Failures on Already-Solved Tasks (Local Gemma 4 E4B)

**Observed 2026-04-07 during local medium integration tests.**

When the planner creates a task that was already completed by a previous task (e.g., "Correct the syntax error in greet.py" after task 1 already fixed and verified it), the local model struggles to produce valid JSON. Instead of emitting `{"action": "done"}`, it generates verbose reasoning text that exhausts `max_tokens` without producing a JSON object.

**Example:** `test_fix_python_syntax_error` — Task 1 fixed the syntax error and verified with `python3 greet.py`. Task 2 "Correct the syntax error" got the carryover state showing `hello` output from the successful run. The model:
1. Step 1: read greet.py (48.8s, 246 completion tokens — verbose) → saw correct code
2. Step 2: JSON parse failure (256 tokens exhausted, no JSON) → retry 1 with thinking=medium (512 tokens, still no JSON) → retry 2 with thinking=high (609 tokens) → finally produced valid JSON after 254s total
3. Step 3: Another round of JSON parse failures (256 tokens) → retry with thinking=medium (428 tokens, 116s) → finally emitted done

**Impact:** ~370s wasted on a task that should have been instant. The model knows the task is done but can't express it concisely — it generates explanatory text instead of JSON.

**Potential fix:** Detect when a task description matches completed work and auto-skip, or reduce planner task overlap.

### Path Truncation in Long Temp Paths (Local Gemma 4 E4B)

**Observed 2026-04-07 during local medium integration tests.**

When prompts contain long absolute paths (e.g., pytest temp dirs like `/private/var/folders/k9/.../test_fix_missing_include0/fix_me.c`), the model attempts to reproduce the full path in shell commands but truncates it, causing "no such file or directory" errors instead of the expected semantic errors.

**Example:** `test_fix_missing_include` — The compilation command with two long paths required ~112 completion tokens just for the shell command JSON. The model truncated the source file path, getting a path error instead of the expected missing-header error. This derails the recovery flow since the model sees a path error (needs thinking) instead of a missing include error (straightforward fix).

**Workaround:** The `workdir` is set correctly, so the model should use relative paths (`cc -o fix_me fix_me.c`). The SYSTEM_STEP prompt says "use relative paths when possible" but the local model doesn't always follow this. The 26B model on OpenRouter handles this correctly.

**Impact:** Adds extra thinking retries and steps. May cascade into replan if the model can't recover.

### Write Content Truncation (Local Gemma 4 E4B) — Mitigated by `edit` Action (2026-04-08)

**Observed 2026-04-07 during local hard integration tests.**

When the model needs to write multi-line file content (e.g., C source with `#include` directives, escaped quotes, newlines), the write action JSON frequently exceeds `max_tokens` before closing the JSON object. The model correctly identifies the fix but can't fit the complete `{"action":"write","arg":"main.c","content":"..."}` in the token budget.

**Example:** `test_replan_build_with_dependency` step 5 — model needed to rewrite `main.c` with `#include <stdio.h>` added. Retry 1 (thinking=medium, 512 tokens) produced truncated JSON: `{"action": "write", "arg": "main.c", "content": "#include <stdio.h>\n#include \"msg.h\"\n\nint main() {\n    printf(\"%s` — cut off mid-content. Retry 2 (thinking=high, 768 tokens) produced empty output. Retry 3 (thinking=high, 768 tokens) finally succeeded. Total time for this single step: 303.6s.

**Distinct from path truncation:** Path truncation is about long *args* (file paths). Write content truncation is about long *content* fields. Both exhaust `max_tokens` but from different JSON fields.

**Mitigated (2026-04-08):** The `edit` action eliminates this bottleneck for localized changes. Instead of rewriting the entire file (`{"action":"write","content":"...full file..."}` at ~200+ tokens), the model emits `{"action":"edit","find":"old text","replace":"new text"}` at ~40-80 tokens — well within the 256-token local budget. OpenRouter integration tests confirmed the model spontaneously prefers `edit` for fixes (e.g., `test_fix_missing_include` used `edit` to add `#include <stdio.h>` in 42 tokens / 1.0s, vs previous write attempts that took 303.6s with 3 retries). Write is still used for new file creation where edit doesn't apply.

### Cross-Task State Bug (Fixed 2026-04-07)

**Bug:** Executor state (`last_steps`) was wiped clean at the start of each task. The LLM had zero knowledge of what previous tasks accomplished, causing it to redo completed work (e.g., rewriting main.c when the task was "run ./main").

**Fix:** Three changes:
1. **Carry over last step** — last step from previous task is preserved, not wiped
2. **Pass completed_tasks** — executor sees names of completed tasks in slim state
3. **Basename outputs** — write output says `"Wrote main.c"` (not full path) so the LLM can tell which file was created

**Impact:** test_multi_step_build went from 12+ steps with replan to 6 steps, zero replans, 8s total.

### Action Looping (Gemma 4 26B-A4B via OpenRouter)

The larger 26B model reliably emits `done` but occasionally loops on the same write action 2-3 times before stopping. This appears to be a model reasoning limitation — it sees `ok: true` in `last_steps` but writes again. The "use relative paths" instruction in SYSTEM_STEP reduces this but doesn't eliminate it.

## Thinking-on-Retry (Implemented 2026-04-07)

### Motivation

The remaining hard test failure (`test_replan_build_with_dependency`) is a **code hallucination** — the 26B model generates incorrect C code for a `msg.h` + `main.c` pattern. Replanning doesn't help because:
- At `temperature: 0.1`, the model regenerates the same wrong code each attempt
- The error is in code generation quality, not plan structure
- Strategy-level fixes (replan, more steps) can't fix a reasoning-level problem

Thinking mode gives the model a chain-of-thought scratchpad before generating code, which directly addresses reasoning failures. But enabling thinking on every call wastes tokens and time on the happy path (26B already passes 8/9 hard tests without it).

**Solution: enable thinking only on retry after a failed step.** Zero cost when things work, thinking budget only when the model already proved it needs help.

### How Gemma 4 Thinking Works

**Two different mechanisms depending on backend:**

**OpenRouter API** — `reasoning` parameter:
```json
{
  "model": "google/gemma-4-26b-a4b-it",
  "messages": [...],
  "reasoning": {
    "enabled": true,
    "effort": "medium"
  },
  "max_tokens": 1536
}
```
- **IMPORTANT**: `effort` and `max_tokens` are mutually exclusive — cannot specify both (API returns 400)
- **IMPORTANT**: Despite docs claiming reasoning tokens are separate, with Parasail/bf16 provider reasoning tokens **count against `max_tokens`**. Must bump `max_tokens` to compensate (1536 medium, 2048 high).
- Response `content` may be **`null`** if reasoning exhausts all tokens — code must handle with `content or ""`
- Response includes `reasoning_details` array (can be ignored)
- Effort levels: `minimal`, `low`, `medium`, `high`, `xhigh`

**Local llama-server** — `<|think|>` system prompt token:
```json
{
  "model": "gemma-4-e4b",
  "messages": [
    {"role": "system", "content": "<|think|>\nYou are a task executor..."},
    {"role": "user", "content": "..."}
  ],
  "max_tokens": 512
}
```
- Thinking tokens count **against** `max_tokens` — must increase from 256 to 512+
- Output contains `<|channel>thought\n...\<channel|>` blocks (stripped by existing regex)
- Server-level flags: `--reasoning on`, `--reasoning-budget 2000`, `--reasoning-format deepseek`
- Recommended params: temperature 1.0, top_p 0.95, top_k 64

### Key Difference

| Aspect | OpenRouter (Parasail) | Local llama-server |
|---|---|---|
| Thinking token budget | Shared with max_tokens (must bump: 1536/2048) | Shared with max_tokens (must increase: 512/768) |
| How to enable | `"reasoning": {"enabled": true, "effort": "medium"}` | `<|think|>` in system prompt |
| Output location | `reasoning_details` array, `content` may be `null` | Inline `<|channel>` blocks |
| Speed impact | ~10-12s/step (vs ~1s normal) | Significant (~2x at 7 tok/s) |
| Effort control | `effort: "medium"/"high"` (cannot combine with `max_tokens`) | `--reasoning-budget N` |

### Implementation (Done)

```
ask_llm() attempt 0: normal call (no thinking)
    ↓ JSON parse fails or step execution fails
ask_llm() attempt 1: enable thinking (medium)
    - OpenRouter: reasoning.enabled=true, effort="medium", max_tokens bumped to 1536
    - Local: bump max_tokens 256→512, prepend <|think|> to system prompt
    ↓ strip think tags, parse JSON
ask_llm() attempt 2: thinking escalated (high)
    - OpenRouter: effort="high", max_tokens bumped to 2048
    - Local: max_tokens→768
```

When `think=True` is passed from caller (after failed step execution): medium from attempt 0, high from attempt 1+.

Changes to `ask_llm()`:
- `think` parameter enables thinking from first attempt
- Auto-escalation on retries: no thinking → medium → high
- OpenRouter: `reasoning` dict with `effort` only (not `max_tokens` — they're mutually exclusive)
- OpenRouter: `max_tokens` bumped to 1536/2048 (reasoning tokens shared with Parasail)
- Local: `<|think|>` prepended to system prompt, `max_tokens` bumped to 512/768
- `content or ""` handles `null` content when reasoning exhausts token budget
- `<|channel>...<channel|>` stripping regex added for local thinking output
- API error responses (`"error"` key) are retried instead of crashing on missing `"choices"`

Changes to `run()` / `int_run()`:
- `use_think` flag set to `True` after any failed step execution, `False` after success
- Passed through `get_step()` → `ask_llm()` — thinking activates on the step after failure

### Bugs Found During Implementation

1. **`effort` + `max_tokens` mutually exclusive** — OpenRouter returns 400 if both specified in `reasoning` dict. Fixed: use `effort` only.
2. **`content: null` with reasoning** — Parasail provider's reasoning tokens count against `max_tokens`. At original 512 max_tokens, reasoning used ~484 tokens leaving nothing for content. Fixed: bump to 1536/2048.
3. **API error → KeyError** — When OpenRouter returns `{"error": {...}}` instead of `{"choices": [...]}`, code crashed on `rj["choices"]`. Fixed: check for `"error"` key, log and retry.

### Test Results (2026-04-07)

**Unit tests: 9 tests in `TestThinkingRetry`, all pass:**
1. `test_thinking_retry_openrouter` — reasoning params added on retry
2. `test_thinking_retry_local` — `<|think|>` prepended + max_tokens bumped
3. `test_thinking_strips_channel_tags` — closed `<|channel>` blocks stripped
4. `test_thinking_strips_unclosed_channel_tags` — unclosed blocks stripped
5. `test_api_error_retries` — API error responses retried gracefully
6. `test_no_thinking_on_first_attempt` — no reasoning on first attempt
7. `test_think_true_enables_from_first_attempt` — caller can force thinking
8. `test_null_content_with_reasoning` — `content: null` handled (retries)
9. `test_think_escalates_to_high` — medium → high escalation

**OpenRouter hard tests: 3/3 pass:**
- `test_replan_build_with_dependency` — **PASSES** (code hallucination fixed by thinking)
- `test_replan_fix_wrong_command` — **PASSES** (thinking helped debug, replanned successfully)
- `test_replan_multi_step_recovery` — **PASSES** (was failing — model output dict content `{"status": "SUCCESS"}` instead of escaped string; fixed by auto-serialization in `execute()`)

### Local Integration Test Results (2026-04-07, build `941146b3f`)

**Easy: 3/3 pass (3:20 total):**

| Test | Tasks | Steps | Thinking | Dup Guard | Time |
|------|-------|-------|----------|-----------|------|
| create_and_read_file | 2 | 4 (t1:3, t2:1) | 0 | 0 | 54s |
| shell_and_write | 2 | 4 (t1:3, t2:1) | 0 | 0 | 60s |
| multi_step_build | 3 | 6 (t1:2, t2:3, t3:1) | 0 | 1x write | 87s |

- Done emission works reliably (was broken due to empty state bug, not model limitation)
- Duplicate guard fired once (multi_step_build, same write loop as 26B)
- ~10x slower than OpenRouter 26B (~10s/step vs <1s)

**Medium: 3/3 pass (20:20 total):**

| Test | Replans | Tasks | Steps | Thinking | Dup Guard | Time |
|------|---------|-------|-------|----------|-----------|------|
| fix_python_syntax | 0 | 3 | 9 | 3x (med+high+med) | 0 | ~520s |
| fix_missing_include | 1 | 3+3 | 3+5 | 3x + 0 | 2x (write+shell) | ~660s |
| create_missing_file | 0 | 2 | 4 | 0 | 0 | ~37s |

Key observations:
- **fix_python_syntax:** 370s wasted on task 2 ("Correct the syntax error") which was already fixed by task 1. Model couldn't produce `{"action":"done"}` — generated verbose reasoning text that exhausted all 3 retry token budgets (256→512→768 tokens). Only the final thinking=high retry succeeded.
- **fix_missing_include:** Path truncation was the root cause — model tried to reproduce long temp paths (`/private/var/folders/...`) in shell commands, exhausting max_tokens before closing JSON. After replan, the planner's simpler task descriptions let the model use relative paths (`cc -o fix_me fix_me.c`), which succeeded immediately. Total time dominated by thinking retries on truncated paths (~12 min of ~11 min total).
- **create_missing_file:** Clean — no error recovery needed, same pattern as easy tests.
- Duplicate guard saved fix_missing_include from infinite loops (auto-fail on same shell failing twice → triggered replan).

### Local Integration Test Results (2026-04-07, build `0d049d6a9` — post Phase 1 update)

Build updated from `941146b3f` → `0d049d6a9` (18 commits). Includes Gemma4 byte token fix (#21488) and checkpoint restore fix (#21510).

**Easy: 3/3 pass (2:12 total) — ~35% faster than pre-update:**

| Test | Tasks | Steps | Thinking | Dup Guard | Time |
|------|-------|-------|----------|-----------|------|
| create_and_read_file | 2 | 5 (t1:3, t2:1) | 0 | 0 | ~41s |
| shell_and_write | 2 | 4 (t1:3, t2:1) | 0 | 0 | ~36s |
| multi_step_build | 3 | 6 (t1:2, t2:3, t3:1) | 0 | 1x write | ~55s |

**Medium: 3/3 pass (39:08 total):**

| Test | Replans | Tasks | Steps | Thinking Retries | Time |
|------|---------|-------|-------|------------------|------|
| fix_python_syntax | 0 | 3 | 10 | 5x | ~19min |
| fix_missing_include | 1 | 3+3 | 5+4 | 4x | ~19min |
| create_missing_file | 0 | 2 | 4 | 0 | ~15s |

Key observations:
- **Easy tests ~35% faster** — likely byte token fix (#21488) improving JSON output, fewer parse retries
- **Medium tests slower overall** — fix_python_syntax 19min vs 8.7min, more JSON parse retries. The `</s>` EOS fix (#21492, not yet merged) may help
- **`--cache-reuse` confirmed broken** — server now explicitly logs `cache_reuse is not supported by this context`. Phase 2 workaround (manual slot save/restore) tested but **counterproductive** — same iSWA bug affects slot restore, making requests 40% slower
- **Checkpoint restore verified** — `TestServerConfig::test_slot_restore` passes

**Hard: 3/3 pass (16:49 total):**

| Test | Replans | Tasks | Steps | Thinking | Dup Guard | Time |
|------|---------|-------|-------|----------|-----------|------|
| build_with_dependency | 1 | 3+2 | 8+2+2 | 5x (med+med+high+high+0) | 2x (write auto-done) | ~11min |
| fix_wrong_command | 0 | 3 | 4+3+2 | 2x (med+med) | 0 | ~4.5min |
| multi_step_recovery | 0 | 2 | 2+2 | 0 | 0 | ~1.5min |

Key observations:
- **build_with_dependency:** Most complex test. Plan 1 created files in task 1 (msg.h + main.c without `#include <stdio.h>`), task 2 auto-done via duplicate guard, task 3 hit 3 cascading failures: (1) `cc -o program main.c msg.h` — can't compile header directly, (2) thinking=medium fixed to `cc -o program main.c` — missing stdio.h, (3) model knew the fix (`#include <stdio.h>`) but write action JSON truncated at 512 tokens, retry at 768 also truncated, 3rd retry at 768 finally succeeded (303.6s for step 5 alone). After fixing main.c, compiled and ran successfully but exhausted 8/8 steps → triggered replan. Plan 2 was clean: just compile + run (2 steps each, done emission worked).
- **fix_wrong_command:** Model followed instructions to try `datex` first (failed), then thinking=medium recovered with `date > path/today.txt` in a single command (clever — combined date + redirect). Then oddly retried `datex` again (step 3), triggering thinking=medium which produced `done`. Task 2 ran `date` standalone + wrote file. Task 3 re-ran date + done. No replan needed despite test expecting one — the model recovered within task via thinking.
- **multi_step_recovery:** Cleanest hard test — model read the prompt carefully, created config.json first, then ran app.py. Only 4 steps total, no errors, no thinking retries. The planner correctly identified the dependency order without needing to fail first.
- **Write action JSON truncation — mitigated by `edit` (2026-04-08):** When the model needs to write multi-line C code with includes and escapes, the write action JSON frequently exceeds max_tokens (256 local, even 512/768 with thinking). The model correctly identifies the fix but can't fit it in the token budget. This is distinct from the path truncation bug — it's the *content* that's too long, not the path. Observed in build_with_dependency step 5 (303.6s, 3 retries). The `edit` action now handles this case — localized changes fit in ~40-80 tokens vs ~200+ for a full rewrite.
- **New observation — thinking=medium datex recovery:** The model used shell redirection (`date > /path/today.txt`) after `datex` failed, bypassing the need for a separate write action. This is the first observed case of the model combining operations to reduce steps.

### OpenRouter Integration Test Results (2026-04-08, post `edit` action)

After adding the `edit` action, the 26B model spontaneously prefers `edit` for localized fixes and `write` for new files.

**Easy: 3/3 pass (37.6s total):**

| Test | Tasks | Steps | Time |
|------|-------|-------|------|
| create_and_read_file | 2 | 4 (t1:3, t2:1) | ~10s |
| shell_and_write | 1 | 3 | ~7s |
| multi_step_build | 2 | 3 (t1:2, t2:2) | ~21s |

**Medium: 3/3 pass (86.9s total):**

| Test | Replans | Tasks | Steps | Edit used? | Time |
|------|---------|-------|-------|------------|------|
| fix_python_syntax | 0 | 2 | 7 (t1:5, t2:3) | **Yes** — `edit greet.py` (37 tok, 0.9s) | ~23s |
| fix_missing_include | 0 | 3 | 8 (t1:4, t2:4, t3:2) | **Yes** — `edit fix_me.c` (42 tok, 1.0s) | ~50s |
| create_missing_file | 0 | 2 | 4 (t1:3, t2:1) | No (new file, used write) | ~13s |

Key observations:
- **fix_python_syntax:** Model read the file, used `edit` to fix the missing parenthesis, then ran successfully. No thinking retries needed. Previously this required full-file `write`.
- **fix_missing_include:** Model used `edit` to add `#include <stdio.h>` — 42 tokens vs the ~200+ tokens a `write` would need. This is the exact bottleneck that caused 303.6s retries on local hard tests.

**Hard: 3/3 pass (176.0s total, 0 replans):**

| Test | Replans | Tasks | Steps | Edit used? | Time |
|------|---------|-------|-------|------------|------|
| build_with_dependency | 0 | 3 | 14 (t1:6, t2:8, t3:1) | No (new files, used write) | ~68s |
| fix_wrong_command | 0 | 1 | 3 | No (shell only) | ~33s |
| multi_step_recovery | 0 | 2 | 8 (t1:6, t2:2) | No (new file, used write) | ~75s |

Key observations:
- **All 3 hard tests completed with 0 replans** — planner reasoning + edit action eliminated the need for replanning.
- **build_with_dependency:** Write duplicate loops still present (model writes msg.h 3x before moving on), but the duplicate guard handles them. No write truncation because the planner's task descriptions include content hints (`#define MSG "REPLAN_OK"`).
- **multi_step_recovery:** Model truncated a path in one shell command (dropped `7s` from `b2q3fw8x7s`), recovered by checking pwd and retrying with relative paths.

### References

- [OpenRouter reasoning tokens docs](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens) — `reasoning.enabled`, `reasoning.max_tokens`, `reasoning.effort`
- [OpenRouter API parameters](https://openrouter.ai/docs/api/reference/parameters) — full parameter reference
- [Gemma 4 Setup & Optimization](gemma4-setup.md) — upstream PR tracker, `--cache-reuse` broken for iSWA ([#21468](https://github.com/ggml-org/llama.cpp/issues/21468)), manual save/restore workaround plan
- [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) — `--reasoning on`, `--reasoning-budget`, `--reasoning-format`
- [Unsloth Gemma 4 guide](https://unsloth.ai/docs/models/gemma-4) — `<|think|>` token usage, recommended sampling params
- [Google Gemma 4 blog](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/) — thinking is opt-in (not always-on like Qwen 3.5)

## Duplicate Action Guard (Implemented 2026-04-07)

### Motivation

The remaining action looping issue: the model occasionally repeats the same successful action 2-3 times (write loops) or the same failed action up to 8 times (failure loops) before emitting `done` or `fail`. Thinking-on-retry helps with code quality after failures but doesn't prevent repetition of identical actions.

**Multi-turn conversation was considered and rejected** for this agent. Multi-turn accumulates raw LLM output (full absolute paths, verbose reasoning, file content in write actions) as prior assistant turns. Three turns easily hits 500-800 prompt tokens — vs the current slim state at ~150-200 tokens. This would bloat context for the local 4B model (256 max_tokens, 16K context, ~7 tok/s) where every token matters. The curated slim state gives us precise control over prompt size; multi-turn hands that control to the model's own verbosity. Multi-turn makes sense later with a stronger local model and more context headroom.

**Solution: programmatic duplicate action detection.** Zero token cost, eliminates loops at the framework level regardless of model capability.

### How It Works

Detection is **per-action-type** because different actions have different false-positive risks:

```
After get_step() returns an action, before execute():

last = state["last_steps"][-1:]
if not last:
    → no guard (first step)

prev = last[0]

WRITE: must compare content, not just arg
    Same file + same content + prev ok → auto-done (true duplicate)
    Same file + different content → allow (legitimate fix attempt)

SHELL: compare command string
    Same command + prev ok → auto-done (true duplicate)
    Same command + prev fail → auto-fail (stuck, escalate to replan)

READ: no guard (re-reads are legitimate after modifying a file)

DONE/FAIL: no guard (terminal actions, already handled)
```

**Why content matters for write:** The error-recovery pattern is write → compile (fail) → write same file with fix → compile. Matching only on `(action, arg)` would auto-done the second write, killing the fix. This was the critical bug in the original plan.

**Why auto-fail is safe for shell:** If the model runs the exact same command twice consecutively and both fail, no intervening action changed the environment — the model is stuck. But if there's an intervening write/shell between the two attempts, `last_steps[-1]` is that intervening action, not the first failed shell, so the guard doesn't trigger.

**Why read is excluded:** Re-reading a file after modifying it (read → write fix → read to verify) is a legitimate pattern. The last step before the second read is a write, so `last_steps[-1]` wouldn't match anyway in most cases. But even consecutive reads (read → read same file) are harmless — the model might have missed info on first read. The cost of a false positive (skipping a needed re-read) outweighs the cost of an extra read action.

### State change: store write content for comparison

`last_steps` entries for write actions gain a `_content` field (the written content). This is:
- Stored in the in-memory step dict for duplicate detection
- **Not** included in the slim state sent to the LLM (no token cost)
- Only compared against the immediately previous step

```python
# In the step-append block, after execute():
step_entry = {
    "action": act, "arg": action.get("arg", ""),
    "ok": result["ok"], "output": result["output"][:100]
}
if act == "write":
    step_entry["_content"] = action.get("content", "")
state["last_steps"].append(step_entry)
```

The slim state construction in `get_step()` already selects only `action`, `arg`, `ok`, `output` keys — `_content` is automatically excluded.

### Impact

| Scenario | Before | After |
|---|---|---|
| Write main.c loop (same content) | 5 writes → exhausted steps → replan | 1 write → auto-done |
| Write main.c then fix (different content) | N/A (would have been false positive) | allowed (content differs) |
| Shell datex failure loop | 8 attempts → exhausted steps → replan | 2 attempts → auto-fail → replan |
| Shell gcc after fixing source | N/A (would have been false positive) | allowed (last step is write, not shell) |
| Read same file twice | N/A (would have been false positive) | allowed (read excluded from guard) |
| data.txt write loop (same content) | 3 writes → done on step 4 | 1 write → auto-done |
| Normal execution (no loops) | No change | No change (guard never triggers) |

### Implementation Plan

Changes to `run()` and `int_run()` step loops, after `get_step()` returns but before `execute()`:

```python
# Duplicate action guard — per-action-type loop detection
last = state["last_steps"][-1:] if state["last_steps"] else []
if last and last[0]["action"] == act:
    prev = last[0]
    if act == "write" and prev.get("arg", "") == action.get("arg", ""):
        # Write: compare content — same content = loop, different content = fix
        if prev.get("ok") and prev.get("_content", "") == action.get("content", ""):
            log(f"  [{step + 1}] skip (duplicate write, same content)")
            continue
    elif act == "shell" and prev.get("arg", "") == action.get("arg", ""):
        # Shell: same command — ok=loop, fail=stuck
        if prev.get("ok"):
            log(f"  [{step + 1}] auto-done (duplicate successful shell)")
            task_done = True
            break
        else:
            log(f"  [{step + 1}] auto-fail (same shell failed twice)")
            state["errors"].append(f"Stuck: {act} {action.get('arg','')[:60]} failed twice")
            break
    # read, done, fail: no guard
```

Changes to step-append block (both `run()` and `int_run()`):

```python
step_entry = {
    "action": act, "arg": action.get("arg", ""),
    "ok": result["ok"], "output": result["output"][:100]
}
if act == "write":
    step_entry["_content"] = action.get("content", "")
state["last_steps"].append(step_entry)
```

No changes to `ask_llm()`, `get_step()`, `get_plan()`, or system prompts. The guard is purely a loop-level check, and `_content` is invisible to the LLM.

### Testing Approach

**Unit tests (no LLM needed) — `TestDuplicateGuard`:**

1. `test_write_same_content_triggers_auto_done` — mock LLM returns write main.c with identical content twice, verify auto-done on second and task completes without executing the duplicate write
2. `test_write_different_content_allowed` — mock LLM returns write main.c with content "v1" then write main.c with content "v2" (fix), verify second write executes normally (NOT blocked by guard)
3. `test_shell_same_success_triggers_auto_done` — mock LLM returns same shell command twice (both would succeed), verify auto-done on second
4. `test_shell_same_failure_triggers_auto_fail` — mock LLM returns same shell command twice (both fail), verify auto-fail and error recorded for replan
5. `test_shell_recompile_after_write_not_blocked` — mock: shell gcc (fail) → write main.c (fix) → shell gcc (same command), verify third step executes (last step is write, not shell, so guard doesn't trigger)
6. `test_read_same_file_twice_allowed` — mock LLM returns read data.txt twice consecutively, verify second read executes normally (read excluded from guard)
7. `test_different_action_type_not_duplicate` — write main.c then shell on main.c, verify no guard trigger (action types differ)
8. `test_guard_with_thinking_active` — mock: shell fails → shell same (with use_think=True from failure), verify auto-fail fires even with thinking enabled (guard and thinking are independent mechanisms)
9. `test_content_not_in_slim_state` — verify `_content` field is not included in the slim state sent to LLM (check that get_step's state construction excludes underscore-prefixed keys)

**Integration tests (OpenRouter):**
10. Run existing `TestOpenRouterEasy::test_multi_step_build` — verify zero write loops (was 2-3 before)
11. Run existing `TestOpenRouterHard::test_replan_fix_wrong_command` — verify datex fails after 2 attempts not 8
12. Run existing `TestOpenRouterMedium::test_fix_missing_include` — verify write-fix-recompile pattern still works (regression test for the false-positive bug)

**What success looks like:**
- Write loops: eliminated when content is identical, allowed when content differs
- Shell loops: eliminated on success duplicates, auto-fail on consecutive failure duplicates
- Read: never blocked by guard
- Fix patterns (write → compile fail → write fix → recompile): unaffected
- Medium tests pass: error recovery within tasks works (the false-positive regression test)
- Token usage: unchanged (guard is programmatic, `_content` is not sent to LLM)

## Planner Reasoning (Implemented 2026-04-07)

### Motivation

Local integration tests (2026-04-07) revealed that most expensive executor retries trace back to **poor task descriptions from the planner**:

| Root cause | Test | Wasted time | How planner reasoning would help |
|---|---|---|---|
| Overlapping tasks | fix_python_syntax | 370s (3 retry budgets) | Planner would recognize task 1 already fixes the syntax → skip redundant task 2 |
| Missing specificity | build_with_dependency | 303.6s (write content truncation, mitigated by `edit` action) | Task "Create main.c" → "Create main.c with `#include <stdio.h>` and `#include \"msg.h\"`" — executor gets content hint; `edit` action now handles localized fixes in ~40-80 tokens |
| Meta-tasks | fix_wrong_command | ~70s (confused executor) | `"Replan to use correct 'date' command"` is not actionable — thinking would produce `"Get date with 'date' command and save to today.txt"` |
| No path guidance | fix_missing_include | ~660s (path truncation cascade) | Planner could hint "use relative paths in workdir" per task |

**The cost asymmetry is clear:** planner runs **once per plan** (~512 extra tokens = ~73s at 7 tok/s). Executor retries fire **per failed step** (256→512→768 tokens × N steps = 150-600s). Investing reasoning at the planning stage prevents multiple downstream retries.

### Design

Enable thinking on `get_plan()` conditionally — off for first plan, on for replans. Derived from planner state: `think=bool(errors or completed_tasks)`.

**Why not always-on:** Benchmarked across 8 prompts on both OpenRouter (Gemma 4 26B, 48 calls) and local (Gemma 4 E4B 4B-active, 22 calls, stopped early after decisive signal). Results:
- think=False produced equal or better plan quality on every prompt in both backends
- On local, think=True caused JSON truncation failures (thinking tokens consumed the 768-token budget, truncating task lists — 2/3 header_dep runs failed all retry attempts)
- On OpenRouter, think=True was the only mode with rubric failures (header_dep: 2 FAILs vs 0 for think=False)
- Speed: local think=True averaged 40-55s vs 3-12s for think=False (5-12x); OpenRouter 12.1s vs 1.1s (11x)

See `benchmarks/planner_thinking_*.json` for raw data.

**Why replans still need thinking:** Errors provide raw data but the model still needs to reason about: (1) why the error happened, (2) what to change to avoid repeating the failure, (3) which completed work is reusable vs. tainted.

```
get_plan() — conditional thinking:
    is_replan = bool(state.errors or state.completed_tasks)
    ask_llm(messages, max_tokens=PLANNER_MAX_TOKENS, think=is_replan)
    → First plan (no thinking): straightforward decomposition, full token
      budget available for task list
    → Replan (thinking enabled): <|think|> (local) or reasoning.effort="medium"
      (OpenRouter), used to diagnose root cause, preserve completed work,
      avoid repeating failures
```

### Risks

**Replan thinking still adds latency.** ~73s per replan on local. This is justified by better error analysis on recovery plans.

**Fewer tasks ≠ better tasks.** The SYSTEM_PLAN changes could reduce overlap by producing fewer but vaguer tasks, moving failure from "redundant task" to "underspecified executor." Validate SYSTEM_PLAN against both overlap reduction *and* task quality — integration tests should check that task descriptions contain actionable specifics (content hints, filenames), not just that there are fewer tasks.

### Changes to `get_plan()`

```python
PLANNER_MAX_TOKENS = 768  # full budget for task list on first plan; shared with thinking on replans

def get_plan(user_prompt, state):
    is_replan = bool(plan_state.get("errors") or plan_state.get("completed_tasks"))
    return ask_llm([
        {"role": "system", "content": SYSTEM_PLAN},
        {"role": "user", "content": f"REQUEST:\n{user_prompt}\n\nSTATE:\n{json.dumps(plan_state)}"}
    ], max_tokens=PLANNER_MAX_TOKENS, think=is_replan)
```

### Changes to `SYSTEM_PLAN`

Add specificity guidance (only effective when thinking is enabled — without thinking, the model ignores long instructions):

```python
SYSTEM_PLAN = f"""You are a planner. Given a user request and current state, propose a list of tasks.
If a previous plan failed, redesign it based on what went wrong.
Prefer fewer tasks (1-3). Each task should be a complete goal, not a single command. Max {MAX_TASKS} tasks.
Keep descriptions short (under 15 words each) but include key details:
- File content hints: which includes, defines, or imports are needed
- Use relative filenames (e.g. main.c not /full/path/main.c)
- Never create a task for work already in completed_tasks
Output ONLY valid JSON. No markdown, no explanation.
Format: {{"tasks": ["task1 description", "task2 description"]}}"""
```

### Expected Impact

| Scenario | Before (retry-based) | After (planner reasoning) |
|---|---|---|
| build_with_dependency | 3 tasks, 8+2+2 steps, 1 replan, ~11min | 2-3 tasks, ~5 steps, 0 replans, ~3min |
| fix_python_syntax | 3 tasks (1 redundant), 9 steps, 370s wasted | 2 tasks (no overlap), ~5 steps, ~2min |
| fix_missing_include | 3+3 tasks (replan), path truncation, ~11min | 2 tasks, relative paths, ~2min |
| fix_wrong_command | 3 tasks (1 meta-task), ~4.5min | 2 tasks (actionable), ~2min |
| Easy tests (no errors) | No change (already fast) | No overhead (thinking off for first plan) |

**Tradeoff:** Easy tests have no thinking overhead (conditional thinking skips first plan). Hard tests with replans get ~73s extra per replan (1-2 replans = +73-146s), but this is noise compared to the 300-660s saved by better recovery plans.

### Token Budget

| Call | Current | Proposed |
|---|---|---|
| `get_plan()` max_tokens | 512 | `PLANNER_MAX_TOKENS` = 768 |
| Thinking (first plan) | None | None (off — full budget for task list) |
| Thinking (replan) | None | medium (local: `<|think|>`, ~256 tokens for reasoning) |
| Net new tokens per first plan | 0 | 0 (no thinking overhead) |
| Net new tokens per replan | 0 | ~256 thinking + 256 headroom |

The 768 max_tokens is the full budget for task list on first plans. On replans, thinking tokens share this budget — cost per replan: ~73s at 7 tok/s, justified by better error analysis.

**Null-content risk (replans only):** With reasoning on replans, `content: null` can occur (reasoning exhausts shared token budget). The existing `ask_llm()` retry path handles this (`content or ""` → JSON parse failure → retry with escalated thinking). Covered by `test_replan_null_content_with_reasoning`.

### Testing Approach

**Unit tests (no LLM needed) — `TestPlannerReasoning`:**

1. `test_first_plan_no_thinking` — mock `ask_llm`, verify `think=False` and `max_tokens=768` passed on first plan (empty state)
2. `test_replan_has_thinking` — mock `ask_llm`, verify `think=True` and `max_tokens=768` on replan (state has errors)
3. `test_replan_with_completed_tasks_has_thinking` — verify `think=True` and `max_tokens=768` when state has `completed_tasks` (partial completion → replan)
4. `test_system_plan_includes_hints` — verify `SYSTEM_PLAN` contains "File content hints" and "relative filenames" guidance
5. `test_first_plan_local_no_think_tag` — mock `requests.post`, verify `<|think|>` NOT prepended on first plan (local)
6. `test_replan_local_uses_think_tag` — mock `requests.post`, verify `<|think|>` prepended on replan (local, errors present)
7. `test_first_plan_openrouter_no_reasoning` — mock `requests.post`, verify no `reasoning` params on first plan (OpenRouter)
8. `test_replan_openrouter_uses_reasoning` — mock `requests.post`, verify `reasoning.enabled=true` on replan (OpenRouter, errors present)
9. `test_replan_null_content_with_reasoning` — mock `requests.post` to return `content: null` on first call (reasoning exhausts budget on replan), verify retry succeeds

**Integration tests — `TestPlannerReasoningIntegration` (local, requires llama-server):**

Track `planner_wall_time` separately from total test time in results to keep happy-path overhead visible.

8. `test_build_with_dependency_fewer_replans` — same prompt as hard test but assert `replans <= 1` (improvement over current, tighten to 0 once stable)
9. `test_no_overlapping_tasks` — fix_python_syntax prompt, assert no task in plan duplicates work of a previous task (parse plan output, check for redundant tasks)
10. `test_plan_uses_relative_paths` — prompt with long temp paths, assert task descriptions contain relative filenames not absolute paths

**Integration tests — `TestPlannerReasoningOpenRouter` (requires OPENROUTER_API_KEY):**

11. `test_build_with_dependency_fewer_replans_openrouter` — same as test 8 but on 26B model
12. `test_plan_specificity` — build_with_dependency prompt, assert plan task descriptions mention `stdio.h` or `#include` (planner reasoning should identify the dependency)

**What success looks like:**
- Hard tests: ≤1 replans (currently 1, target 0 once stable), ~50% fewer steps, ~60% less total time
- Medium tests: 0 thinking retries on executor (currently 3-6x per test), similar total time (planner overhead offsets retry savings)
- Easy tests: +~73s overhead (acceptable), planner wall time tracked separately
- Task quality: fewer tasks *and* more specific descriptions (not fewer-but-vaguer)
- No regression on unit tests (thinking is transparent to JSON parsing)

**Status (2026-04-08, OpenRouter post-edit):** Hard test targets met — 0 replans on all 3 hard tests (target was ≤1). Medium tests improved — `edit` action eliminates write truncation retries (fix_python_syntax: 0 thinking retries, fix_missing_include: 0 replans). See "OpenRouter Integration Test Results (2026-04-08)" for detailed data.

## Usage

```bash
# 1. Start llama-server (optimized for agentic use)
cd /Users/macmone/code/llama.cpp
mkdir -p /tmp/llama-cache
./build/bin/llama-server \
  -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf \
  -ngl 99 --ctx-size 16384 --flash-attn on \
  --cache-type-k q4_0 --cache-type-v q4_0 \
  -np 1 --slot-save-path /tmp/llama-cache \
  --port 8080

# 2. Run agent
python3 agent/askme.py "create hello.c with hello world, compile it, run it"
```

## Tests

```bash
# Unit tests (mocked, no LLM needed, ~30s)
python3 -m pytest tests/ -v -k "not Integration and not ServerConfig and not (OpenRouter and not ThinkingRetry and not PlannerReasoning) and not PlannerReasoningOpenRouter"

# Integration tests — local (requires llama-server on :8080)
python3 -m pytest tests/test_agent_integration.py -s -v -k "TestIntegration"

# Integration tests — OpenRouter (requires OPENROUTER_API_KEY in .env)
python3 -m pytest tests/test_agent_integration.py -s -v -k "TestOpenRouterEasy or TestOpenRouterMedium or TestOpenRouterHard"
```

**Unit tests (132 non-integration, 27 skipped integration/openrouter):**
- `TestExecuteShell` — success, failure, timeout, stderr, truncation, cwd, empty output
- `TestExecuteWrite` — file creation, relative paths, nested dirs, bad paths
- `TestExecuteEdit` — single match, no match, multiple match, missing file, relative path, empty find, delete text, absolute path
- `TestExecuteRead` — read file, absolute/relative paths, missing files, truncation
- `TestExecuteDoneFail` — done/fail/unknown actions, missing keys
- `TestAskLlm` — JSON parsing, think-tag stripping, code-fence stripping
- `TestThinkingRetry` — thinking-on-retry: OpenRouter reasoning params, local `<|think|>` + max_tokens bump, `<|channel>` tag stripping, escalation from medium to high, think=True from caller
- `TestRunLoop` — full loop: simple success, replan on failure, max replans, multi-task, error tracking, empty plans
- `TestCrossTaskState` — verifies completed_tasks and last step carryover across tasks
- `TestOutputFormatting` — verifies write/edit output uses basename, slim state uses basename for write/edit/read args
- `TestWriteContentSerialization` — verifies dict/list content auto-serialized to JSON in write actions
- `TestDuplicateGuard` — verifies per-action-type duplicate detection: write/edit duplicates skip and continue, shell duplicates auto-done/fail, legitimate retries allowed, read excluded, edit internals not in slim state
- `TestServerConfig` — verifies optimized server config: single slot with full context, slot save/restore via API, cache file on disk
- `TestPlannerReasoning` — verifies conditional planner thinking (first plan=False, replans=True), local/OpenRouter request shapes for both modes, PLANNER_MAX_TOKENS, SYSTEM_PLAN specificity hints, null-content retry on replan

**Integration tests (23 total, require live LLM) — local and OpenRouter tiers:**

Local integration (12 tests, require llama-server on :8080):

Easy (3 tests, 0 replans expected — `TestIntegration`):
- `test_create_and_read_file` — write + read hello.txt
- `test_shell_and_write` — run uname, write output to file
- `test_multi_step_build` — create C file, compile, run

Medium (3 tests, 0 replans expected, error recovery within task — `TestIntegrationMedium`):
- `test_fix_python_syntax_error` — run broken Python, read error, fix syntax, run again
- `test_fix_missing_include` — compile C without stdio.h, read error, add include, compile+run
- `test_create_missing_file_then_use` — read non-existent file, create it, read again

Hard (3 tests, 1-2 replans expected — `TestIntegrationHard`):
- `test_replan_build_with_dependency` — compile C with missing header, replan to create header first
- `test_replan_fix_wrong_command` — try non-existent `datex`, replan with correct `date`
- `test_replan_multi_step_recovery` — run script needing missing config.json, replan to create it

Planner Reasoning (3 tests, validates thinking improves plans — `TestPlannerReasoningIntegration`):
- `test_build_with_dependency_fewer_replans` — same as hard build_with_dependency, assert ≤1 replans
- `test_no_overlapping_tasks` — fix_python_syntax prompt, assert no redundant tasks in plan
- `test_plan_uses_relative_paths` — assert task descriptions use relative filenames, not absolute paths

OpenRouter integration (11 tests, require OPENROUTER_API_KEY in .env):
Same test structure as local (`TestOpenRouterEasy`, `TestOpenRouterMedium`, `TestOpenRouterHard`) plus planner reasoning tests (`TestPlannerReasoningOpenRouter`). Uses Gemma 4 26B-A4B via Parasail/bf16 provider. These tests validate the larger model's behavior and confirmed that:
- 26B model reliably emits `done` (no auto-done needed)
- Cross-task state fixes eliminated redundant work
- Hard tests: 3/3 pass with 0 replans (thinking-on-retry fixes code hallucination, content serialization fixes dict output, duplicate guard prevents loops)
- Planner reasoning: 0 replans on build_with_dependency, task descriptions include dependency hints (stdio.h, msg.h, #include)
- `edit` action (2026-04-08): model spontaneously prefers `edit` for localized fixes — `test_fix_python_syntax` used edit (37 tok, 0.9s), `test_fix_missing_include` used edit (42 tok, 1.0s). See detailed results in "OpenRouter Integration Test Results (2026-04-08)" section above.

`int_run()` accepts configurable limits (replans, tasks, steps). Default: 1 replan, 3 tasks, 5 steps. Medium: 1 replan, 3 tasks, 8 steps. Hard: 2 replans, 5 tasks, 8 steps. Timestamped progress output for real-time monitoring via `tail -f`.

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

## Server Configuration Reference

### Model Inventory

| Model | File | Size | Architecture | GPU | Status |
|---|---|---|---|---|---|
| **Gemma 4 E4B** Q4_K_M | `models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf` | ~5.0 GB | MoE 12B (4B active) | Full Metal | **Primary** |
| Qwen 3.5 9B Q4_K_M | `models/qwen35-9b/Qwen3.5-9B-Q4_K_M.gguf` | 5.3 GB | Dense 9B | Full Metal | Legacy |
| Qwen 3.5 35B-A3B UD-IQ3_S | `models/qwen35/Qwen3.5-35B-A3B-UD-IQ3_S.gguf` | 12.7 GB | MoE 35B (3B active) | CPU only | Legacy |

### KV Cache & Prompt Caching

Key flags for agentic use (defaults are suboptimal for sequential agent calls):

| Flag | What it does | Default | Recommended |
|---|---|---|---|
| `--cache-prompt` | Prompt caching within a slot | Enabled | Keep |
| `--cache-reuse N` | KV shifting for prefix reuse across requests | 0 (off) | 256 (**broken for Gemma 4** — [#21468](https://github.com/ggml-org/llama.cpp/issues/21468), server explicitly disables. Manual slot save/restore workaround tested but counterproductive — no viable workaround until upstream fix) |
| `--slot-save-path DIR` | Persist KV cache to disk (survives restarts) | Off | `/tmp/llama-cache` |
| `-np N` | Parallel slots (auto-detects 4 on M1, splits context) | Auto | 1 |
| `--ctx-size N` | Context per slot (with -np 1, full context for agent) | 2048 | 16384 |
| `--flash-attn on` | Saves GPU memory significantly | Off | On |

### Save/Restore KV State via API

```bash
# Save slot 0 (e.g. after processing system prompt)
curl http://localhost:8080/slots/0?action=save -X POST \
  -H "Content-Type: application/json" \
  -d '{"filename": "agent-system-prompt"}'

# Restore it later (instant — skips reprocessing)
curl http://localhost:8080/slots/0?action=restore -X POST \
  -H "Content-Type: application/json" \
  -d '{"filename": "agent-system-prompt"}'
```

### 16GB M1 Lessons Learned

- **Gemma 4 E4B is the sweet spot** — MoE with 4B active params, ~5.0GB Q4_K_M, full Metal GPU
- **No forced thinking mode** — unlike Qwen 3.5, Gemma 4 doesn't leak `<think>` tags into responses
- **35B MoE OOMs on Metal GPU** regardless of context size or flash attention
- **Use `-np 1` for agents** — default auto-detects 4 slots, splitting context 4 ways
- **Use `--cache-type-k q4_0 --cache-type-v q4_0`** — current recommended default. Best result in single-trial testing: 4% faster than f16 on Metal M1, identical quality, ~4x less KV memory. q8_0 is 7% slower in single-trial testing — not recommended. See [gemma4-setup.md](gemma4-setup.md) Phase 3.
- **Do NOT use `--cache-reuse 256`** — broken for Gemma 4 iSWA ([#21468](https://github.com/ggml-org/llama.cpp/issues/21468)). Server explicitly disables it. Manual slot save/restore (`CACHE_WORKAROUND=1`) is counterproductive (40% slower). No viable workaround until upstream fix. See [gemma4-setup.md](gemma4-setup.md) Phase 2.

## Redundant Task Auto-Skip (Implemented 2026-04-08)

### Motivation

When the planner creates tasks that overlap with already-completed work, the local model (Gemma 4 E4B) wastes hundreds of seconds trying to express "this is already done" — it generates verbose reasoning that exhausts `max_tokens` without producing valid JSON. Observed in `test_fix_python_syntax_error`: task 2 "Correct the syntax error in greet.py" was redundant after task 1 already fixed it, burning ~370s across 3 JSON parse failures.

### Design

Pairwise keyword heuristic in `_run_loop()`, checked before entering the step loop for each task:

1. **`_task_keywords(text)`** — extracts normalized keyword set: lowercase, min 3 chars, stop-word filtered, synonym-normalized (`fix/correct/repair` → `fix`, `create/write/make` → `create`, etc.)
2. **`_task_is_redundant(task, completed_tasks)`** — compares new task against each completed task individually (never unions). Skip triggers only when the new task's keywords ⊆ a completed task's keywords (one-way). The reverse direction (completed ⊆ new) is unsafe — the new task may add work beyond what was completed. Minimum 3 keywords required to avoid false positives on generic tasks.

### Key invariants

- **Pairwise, not union**: Prevents false positives where unrelated completed tasks cover keywords in aggregate (e.g., `["create main.c", "compile tests"]` would wrongly make `"compile main.c"` look redundant)
- **Min-3-keyword guard**: Tasks with ≤2 keywords (e.g., "fix bug") are never skipped — too generic to match safely
- **Skipped tasks NOT in `completed_tasks`**: Preserves replan recovery — if a false-positive skip occurs, the planner can reintroduce the task on replan
- **History-only event**: Skip is recorded as `{"event": "skip", "task": i, "reason": "redundant", "matched": "..."}` — no state mutation

### Testing

Unit tests in `TestRedundantTaskSkip` (12 tests): exact match, synonym match, subset match, different-action rejection, different-file rejection, union trap, min-keyword guard, generic-word guard, empty-completed guard, state integrity (not in completed_tasks), history event, replan recovery.
