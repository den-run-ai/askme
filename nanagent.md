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
  "last_steps": [
    {"action": "shell", "arg": "gcc hello.c", "ok": false, "output": "gcc: error..."}
  ]
}
```
`last_steps` is capped at `MAX_STEP_HISTORY=3` (sliding window) and step output is truncated to 100 chars.

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
| Shell timeout | 30s | Per-command timeout |
| Step output | 100 chars | Max output stored per step in history |

## Gemma 4 vs Qwen 3.5 (Why We Switched)

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

**Unit tests (29) + server config tests (4) = 33 non-integration tests:**
- `TestExecuteShell` — success, failure, timeout, stderr, truncation, cwd, empty output
- `TestExecuteWrite` — file creation, relative paths, nested dirs, bad paths
- `TestExecuteRead` — read file, absolute/relative paths, missing files, truncation
- `TestExecuteDoneFail` — done/fail/unknown actions, missing keys
- `TestAskLlm` — JSON parsing, think-tag stripping, code-fence stripping
- `TestRunLoop` — full loop: simple success, replan on failure, max replans, multi-task, error tracking, empty plans
- `TestServerConfig` — verifies optimized server config: single slot with full context, slot save/restore via API, cache file on disk

**Integration tests (3, require live LLM):**
- `test_create_and_read_file` — write + read hello.txt
- `test_shell_and_write` — run uname, write output to file
- `test_multi_step_build` — create C file, compile, run

Integration tests use `int_run()` — a tight loop (1 replan, 3 tasks, 5 steps per task) with timestamped progress output for real-time monitoring via `tail -f`. `INT_MAX_STEPS=5` gives room for the LLM to emit a "done" action after completing real work (e.g., write+compile+run = 3 actions + 1 "done" = 4 steps needed).

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
