# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

IMPORTANT: Ensure you've thoroughly reviewed the [AGENTS.md](../AGENTS.md) file before beginning any work.

## What This Is

NanAgent — a minimal, self-contained agent that runs on local or remote LLMs (Gemma 4 via llama-server or OpenRouter). It takes a user prompt, plans tasks, executes them via shell/write/read actions, and replans on failure. Single file, no frameworks, no external dependencies beyond `requests`.

## Commands

```bash
# Run the agent (local, requires llama-server on :8080)
python3 agent/askme.py "your request here"

# Run via OpenRouter (requires OPENROUTER_API_KEY in .env)
LLM_BACKEND=openrouter python3 agent/askme.py "your request here"

# Start llama-server (optimized for agentic use)
cd /Users/macmone/code/llama.cpp
mkdir -p /tmp/llama-cache
./build/bin/llama-server \
  -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf \
  -ngl 99 --ctx-size 16384 --flash-attn on \
  -np 1 --cache-reuse 256 --slot-save-path /tmp/llama-cache \
  --port 8080

# Unit tests (mocked, no LLM needed)
python3 -m pytest agent/test_agent.py -v -k "not Integration and not ServerConfig and not (OpenRouter and not ThinkingRetry)"

# Single test
python3 -m pytest agent/test_agent.py -v -k "test_simple_success"

# Integration tests — local (requires llama-server on :8080)
python3 -m pytest agent/test_agent.py -s -v -k "TestIntegration and not Medium and not Hard"  # easy (~2min)
python3 -m pytest agent/test_agent.py -s -v -k "IntegrationMedium"   # medium: error recovery (~3min)
python3 -m pytest agent/test_agent.py -s -v -k "IntegrationHard"     # hard: replanning (~5min)

# Integration tests — OpenRouter (requires OPENROUTER_API_KEY in .env)
python3 -m pytest agent/test_agent.py -s -v -k "TestOpenRouterEasy"     # easy (~10s)
python3 -m pytest agent/test_agent.py -s -v -k "TestOpenRouterMedium"   # medium (~2min)
python3 -m pytest agent/test_agent.py -s -v -k "TestOpenRouterHard"     # hard (~2min)

# Rebuild llama.cpp (if needed)
cmake -B build -DLLAMA_CURL=ON
cmake --build build --config Release -j$(sysctl -n hw.ncpu)
```

## Architecture

`askme.py` implements a **Plan → Execute → Replan** loop:

1. **Planner** (`get_plan`) — LLM receives the user prompt + full state (completed tasks, errors) and outputs `{"tasks": ["task1", "task2", ...]}`.
2. **Executor** (`get_step`) — For each task, LLM receives a **slim state** (current task + completed tasks + last 3 steps with cross-task carryover) and proposes one action at a time: `shell`, `write`, `read`, `done`, or `fail`.
3. **Replan** — If a task fails, the full loop restarts with a new plan (up to `MAX_REPLANS=3`). Errors reset per replan since the planner already saw them.

Key design constraint: the local LLM (Gemma 4 E4B, 4B active params) is slow (~7 tok/s) with 16K context on M1. Everything is optimized for minimal token usage — short system prompts, JSON-only output, sliding window of step history, truncated outputs.

### `ask_llm` parsing pipeline
LLM responses go through: strip `<think>` tags → strip `<|channel>` blocks → strip markdown code fences → extract JSON object → retry up to 2 times on parse failure. Retries auto-escalate with thinking: attempt 1 = medium effort, attempt 2 = high effort. When `think=True` is passed (after a failed step execution), thinking starts from attempt 0.

### Error handling
- Shell output: `r.stdout[:300] + r.stderr[-300:]` — tail-truncates stderr to keep actual error messages
- Input caps: `MAX_INPUT=300` chars per field sent to executor — prevents path bloat eating context
- Auto-done: if `get_step()` raises after a successful step, treat as implicit task completion

### State split
- **Planner** sees: `completed_tasks`, `errors` (full context for replanning)
- **Executor** sees: `task`, `task_index`, `step` counter, `completed_tasks[-3:]`, `last_steps[-3:]` with cross-task carryover (last step from previous task preserved)
- Write/read args use **basename** in slim state (e.g. `main.c` not `/full/path/main.c`)
- Write output says `"Wrote main.c"` (basename) — critical for LLM to recognize file was created

## Files

- `askme.py` — the agent (self-contained, ~336 lines)
- `test_agent.py` — 56 unit tests (mocked) + 4 server config tests + 9 local integration + 9 OpenRouter integration tests
- `ARCHITECTURE.md` — detailed architecture doc and design decisions
- `.env` — OPENROUTER_API_KEY (not committed)

## Testing Conventions

- Unit tests mock `ask_llm` or `requests.post` — no LLM needed
- `TestCrossTaskState` — verifies completed_tasks and step carryover across tasks
- `TestOutputFormatting` — verifies basename in write output and slim state args
- `TestWriteContentSerialization` — verifies dict/list content auto-serialized to JSON
- `TestDuplicateGuard` — verifies per-action-type duplicate detection and loop prevention
- Integration tests use `int_run()` with tight limits (`INT_MAX_REPLANS=1`, `INT_MAX_TASKS=3`, `INT_MAX_STEPS=5`)
- Local integration tests are skipped automatically if llama-server isn't running on `:8080`
- OpenRouter integration tests are skipped automatically if `OPENROUTER_API_KEY` is not set
- `TestServerConfig` tests verify the server has optimal agentic config (single slot, full context, cache enabled)

## Safety Limits

All limits are constants at the top of `askme.py`: `MAX_REPLANS=3`, `MAX_TASKS=10`, `MAX_STEPS=10`, `MAX_RESULT=300` chars, `MAX_STEP_HISTORY=3`, `MAX_INPUT=300` chars per executor field, shell timeout 30s. Executor `max_tokens`: 256 (local) / 512 (OpenRouter). These exist to prevent runaway loops with a slow local LLM.

## Known Limitations

### "done" Emission (Local Gemma 4 E4B only)
Local 12B model never emits `{"action": "done"}` — always needs auto-done heuristic. The larger 26B model via OpenRouter emits done reliably. This is a model capability gap, not a prompting issue.

### Action Looping (Gemma 4 26B via OpenRouter) — Mitigated
The 26B model occasionally repeats the same action. Now handled by the **duplicate action guard**: write loops (same content) → auto-done, shell loops (same cmd, success) → auto-done, shell loops (same cmd, fail) → auto-fail + replan. Write with different content and reads are allowed through (legitimate retries).

## Models

- **Local**: Gemma 4 E4B (MoE 12B/4B active, Q4_K_M, ~5GB) — `models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf`
- **OpenRouter**: Gemma 4 26B-A4B (MoE 26B/4B active) — `google/gemma-4-26b-a4b-it` via Parasail/bf16
