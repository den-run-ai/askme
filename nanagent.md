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
- `ask_llm(messages, max_tokens, think)` — calls llama-server, strips `<think>` tags (closed and unclosed) as safety net, code fences, extracts JSON from mixed text, retries on parse failure (up to 2 retries).
- `get_plan(user_prompt, state)` — asks LLM for a task list (max_tokens=512). Receives full state including completed tasks and errors.
- `get_step(task, state, goal="")` — asks LLM for next action within a task (max_tokens=256). Receives a slim state: only current task, task index, and last 3 steps (sliding window). The original user prompt is passed as a `GOAL:` line for context (e.g., file paths, specifics) but completed task history and errors are excluded.
- `execute(action, working_dir)` — runs shell/write/read/done/fail actions
- `run(user_prompt)` — outer loop: plan → execute tasks → replan on failure. Resets errors after each replan.

**State** is an in-memory dict (no files). Planner and executor see different views:

Planner (`get_plan`) receives full state:
```json
{
  "completed_tasks": ["task1"],
  "errors": ["Step failed: shell gcc: ..."]
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
  ]
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
| Shell timeout | 30s | Per-command timeout |
| Step output | 100 chars | Max output stored per step in history |
| Step tokens (local) | 256 | Max completion tokens for executor (local LLM) |
| Step tokens (OpenRouter) | 512 | Max completion tokens for executor (remote LLM) |

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
| **Gemma 4 E4B** (local) | MoE 12B/4B active | Never emits — needs auto-done | N/A (too slow to test extensively) | ~7 tok/s | Primary local model |
| **Gemma 4 26B-A4B** (OpenRouter) | MoE 26B/4B active | Reliably emits done | Occasional write loops (3x before done) | ~1-2s/step | Recommended for testing |
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
| Auto-done | If `get_step()` raises JSONDecodeError and last step was successful, treat as implicit task completion |
| Cross-task state | Last step from previous task carries over; `completed_tasks` included in slim state |
| Basename outputs | Write output says `"Wrote main.c"` not `"Wrote /full/path/main.c"` — LLM needs clear signal |
| Basename args | Slim step history uses basename for write/read `arg` fields — saves tokens, avoids confusion |
| Multi-backend | `LLM_BACKEND=openrouter` switches to OpenRouter API with configurable model and provider |

## Known LLM Limitations

### "done" Emission (Local Gemma 4 E4B)

Gemma 4 E4B (12B/4B active, local) reliably solves problems but struggles to emit `{"action": "done"}`. After a successful action it either:
- Generates verbose reasoning that exhausts `max_tokens` (256), truncating the JSON
- Re-runs the same command instead of completing
- Produces empty or unparseable responses

**Workarounds applied:**
1. **Stronger prompting** — `SYSTEM_STEP` uses CRITICAL RULES section with explicit done instructions
2. **Auto-done heuristic** — if JSON parse fails after a successful step, treat as implicit completion
3. **Input caps** — `MAX_INPUT=300` chars per field reduces prompt bloat that triggers verbose responses

**Impact:** Auto-done adds ~5 min latency per trigger (3 retries × ~100s each).

### Cross-Task State Bug (Fixed 2026-04-07)

**Bug:** Executor state (`last_steps`) was wiped clean at the start of each task. The LLM had zero knowledge of what previous tasks accomplished, causing it to redo completed work (e.g., rewriting main.c when the task was "run ./main").

**Fix:** Three changes:
1. **Carry over last step** — last step from previous task is preserved, not wiped
2. **Pass completed_tasks** — executor sees names of completed tasks in slim state
3. **Basename outputs** — write output says `"Wrote main.c"` (not full path) so the LLM can tell which file was created

**Impact:** test_multi_step_build went from 12+ steps with replan to 6 steps, zero replans, 8s total.

### Action Looping (Gemma 4 26B-A4B via OpenRouter)

The larger 26B model reliably emits `done` but occasionally loops on the same write action 2-3 times before stopping. This appears to be a model reasoning limitation — it sees `ok: true` in `last_steps` but writes again. The "use relative paths" instruction in SYSTEM_STEP reduces this but doesn't eliminate it.

## Usage

```bash
# 1. Start llama-server (optimized for agentic use)
cd /Users/macmone/code/llama.cpp
mkdir -p /tmp/llama-cache
./build/bin/llama-server \
  -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf \
  -ngl 99 --ctx-size 16384 --flash-attn on \
  -np 1 --cache-reuse 256 --slot-save-path /tmp/llama-cache \
  --port 8080

# 2. Run agent
python3 agent/askme.py "create hello.c with hello world, compile it, run it"
```

## Tests

```bash
# Unit tests (mocked, no LLM needed, ~30s)
python3 -m pytest agent/test_agent.py -v -k "not Integration"

# Integration tests (requires llama-server on :8080, ~5min total with Gemma 4 E4B)
python3 -m pytest agent/test_agent.py -s -v -k "Integration"
```

**Unit tests (34) + server config tests (4) = 38 non-integration tests:**
- `TestExecuteShell` — success, failure, timeout, stderr, truncation, cwd, empty output
- `TestExecuteWrite` — file creation, relative paths, nested dirs, bad paths
- `TestExecuteRead` — read file, absolute/relative paths, missing files, truncation
- `TestExecuteDoneFail` — done/fail/unknown actions, missing keys
- `TestAskLlm` — JSON parsing, think-tag stripping, code-fence stripping
- `TestRunLoop` — full loop: simple success, replan on failure, max replans, multi-task, error tracking, empty plans
- `TestCrossTaskState` — verifies completed_tasks and last step carryover across tasks
- `TestOutputFormatting` — verifies write output uses basename, slim state uses basename for args
- `TestServerConfig` — verifies optimized server config: single slot with full context, slot save/restore via API, cache file on disk

**Integration tests (18 total, require live LLM) — local and OpenRouter tiers:**

Local integration (9 tests, require llama-server on :8080):

Easy (3 tests, 0 replans expected — `TestIntegration`):
- `test_create_and_read_file` — write + read hello.txt
- `test_shell_and_write` — run uname, write output to file
- `test_multi_step_build` — create C file, compile, run

Medium (3 tests, 0 replans expected, error recovery within task — `TestIntegrationMedium`):
- `test_fix_python_syntax_error` — run broken Python, read error, fix syntax, run again
- `test_fix_missing_include` — compile C without stdio.h, read error, add include, compile+run
- `test_create_missing_file_then_use` — read non-existent file, create it, read again

Hard (3 tests, 1-2 replans expected — `TestIntegrationHard`, skipped):
- `test_replan_build_with_dependency` — compile C with missing header, replan to create header first
- `test_replan_fix_wrong_command` — try non-existent `datex`, replan with correct `date`
- `test_replan_multi_step_recovery` — run script needing missing config.json, replan to create it

OpenRouter integration (9 tests, require OPENROUTER_API_KEY in .env):
Same test structure as local (`TestOpenRouterEasy`, `TestOpenRouterMedium`, `TestOpenRouterHard`) but uses Gemma 4 26B-A4B via Parasail/bf16 provider. These tests validate the larger model's behavior and confirmed that:
- 26B model reliably emits `done` (no auto-done needed)
- Cross-task state fixes eliminated redundant work
- Hard tests: 2/3 pass (build_with_dependency fails due to code hallucination)

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
