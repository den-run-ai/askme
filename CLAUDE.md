# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

IMPORTANT: Ensure you've thoroughly reviewed the [AGENTS.md](../AGENTS.md) file before beginning any work.

## What This Is

NanAgent — a minimal, self-contained agent that runs on local or remote LLMs (Gemma 4 via llama-server or OpenRouter). It takes a user prompt, plans tasks, executes them via shell/write/edit/read actions, and replans on failure. Single file (`askme.py`), no frameworks, no external dependencies beyond `requests`. Each run gets an isolated temp directory — agent-created files never pollute the repo. Exits with code 1 on failure for script chaining.

## Commands

```bash
# All commands below assume you're in the agent/ directory.

# Run the agent (local, requires llama-server on :8080)
python3 askme.py "your request here"

# Run via OpenRouter (requires OPENROUTER_API_KEY in .env)
LLM_BACKEND=openrouter python3 askme.py "your request here"

# Allow the agent to install software (disabled by default)
ALLOW_SYSTEM_INSTALLS=1 python3 askme.py "your request here"

# Start llama-server (optimized for agentic use)
cd /Users/macmone/code/llama.cpp
mkdir -p /tmp/llama-cache
./build/bin/llama-server \
  -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf \
  -ngl 99 --ctx-size 16384 --flash-attn on \
  --cache-type-k q4_0 --cache-type-v q4_0 \
  -np 1 --slot-save-path /tmp/llama-cache \
  --port 8080

# Unit tests (mocked, no LLM needed)
python3 -m pytest tests/ -v -k "not Integration and not ServerConfig and not (OpenRouter and not ThinkingRetry and not PlannerReasoning) and not PlannerReasoningOpenRouter"

# Single test
python3 -m pytest tests/ -v -k "test_simple_success"

# Integration tests — local (requires llama-server on :8080)
python3 -m pytest tests/test_agent_integration.py -s -v -k "TestIntegration and not Medium and not Hard"  # easy
python3 -m pytest tests/test_agent_integration.py -s -v -k "IntegrationMedium"   # medium: error recovery
python3 -m pytest tests/test_agent_integration.py -s -v -k "IntegrationHard"     # hard: replanning

# Integration tests — OpenRouter (requires OPENROUTER_API_KEY in .env)
python3 -m pytest tests/test_agent_integration.py -s -v -k "TestOpenRouterEasy"
python3 -m pytest tests/test_agent_integration.py -s -v -k "TestOpenRouterMedium"
python3 -m pytest tests/test_agent_integration.py -s -v -k "TestOpenRouterHard"

# KV cache benchmarks (stop llama-server first)
./scripts/bench_kv.sh q4_0          # single trial, one KV mode
./scripts/bench_kv.sh all 3         # 3 trials each for f16, q8_0, q4_0

# Rebuild llama.cpp (if needed, from llama.cpp root)
cmake -B build -DLLAMA_CURL=ON
cmake --build build --config Release -j$(sysctl -n hw.ncpu)
```

## Architecture

`askme.py` implements a **Preflight → Plan → Execute → Replan** loop:

0. **Preflight** (`preflight_probe`) — Deterministically probes platform, arch, working dir listing, available/missing tools, and package managers. Feeds structured dict + execution policy into planner state.
1. **Planner** (`get_plan`) — LLM receives the user prompt + full state (completed tasks, typed/summarized errors, environment, policy) and outputs `{"tasks": [...]}`. Thinking is conditional: **off for first plan, on for replans**. First-plan thinking was benchmarked and found to provide no quality benefit while consuming token budget — on the local model it caused JSON truncation failures.
2. **Executor** (`get_step`) — For each task, LLM receives a **slim state** (current task + completed tasks + last 3 steps with cross-task carryover + missing_tools + policy) and proposes one action at a time: `shell`, `write`, `edit`, `read`, `done`, or `fail`. Completion is goal-aware — executor must satisfy the full task description, not just one successful step.
3. **Replan** — If a task fails, the full loop restarts with a new plan (up to `MAX_REPLANS`). Errors are classified into types (`timeout`, `missing_tool`, `permission_denied`, `missing_file`, `compile_error`, `unknown`), summarized/deduplicated, then fed to the planner.

The core loop lives in `_run_loop()`, which returns `{"status": "complete"|"exhausted", "state": state, "log": history}`. `run()` is a thin wrapper mapping to `True`/`False`. Integration tests call `_run_loop()` directly (via `int_run()`) for the rich result dict.

### Key design decisions

- **Token minimization**: The local LLM (Gemma 4 E4B, 4B active params) is ~7 tok/s on M1. Everything is optimized for minimal token usage — short system prompts, JSON-only output, sliding window of step history, truncated outputs.
- **State split**: Planner sees the full picture (environment, all errors, policy). Executor sees only what it needs (current task, last 3 steps, missing tools, policy). Write/read args use **basename** in slim state — critical for the LLM to recognize file operations.
- **`ask_llm` parsing pipeline**: Strip `<think>` tags → strip `<|channel>` blocks → strip markdown fences → extract JSON → retry up to 2 times with auto-escalating thinking effort.
- **LLM transport**: All requests use `timeout=LLM_TIMEOUT`. Transport errors (connection refused, timeout, non-JSON, 5xx/429) retry with backoff. Client errors (4xx) fail fast. `LLMTransportError` is raised on exhaustion; planner catches it as a plan attempt, executor catches it as a replan trigger.
- **Duplicate action guard**: Handles model looping — write/edit duplicates skip and continue, shell duplicates (same cmd) auto-done/fail. Timeout failures are exempt (get one retry with longer timeout). Thinking escalation is deferred on duplicate skips: first skip gets a corrective observation only, thinking activates on 2+ consecutive skips.
- **Working directory isolation**: `run()` creates `/tmp/nanagent_*` per invocation. All shell/write/read resolve there. Callers can pass `working_dir=` to override (tests do this).

## Testing Conventions

- Unit tests mock `ask_llm` or `requests.post` — no LLM needed
- Integration tests use `int_run()` (from `_test_support.py`) which delegates to `_run_loop()` with tight limits (`INT_MAX_REPLANS=1`, `INT_MAX_TASKS=3`, `INT_MAX_STEPS=5`)
- Local integration tests skip automatically if llama-server isn't on `:8080`
- OpenRouter integration tests skip automatically if `OPENROUTER_API_KEY` is not set
- Test modules are split by concern: `test_agent_core.py` (execute/ask_llm), `test_agent_loop.py` (run loop/state), `test_agent_recovery.py` (guards/classification), `test_agent_planning.py` (planner/preflight/policy/timeouts), `test_agent_integration.py` (end-to-end)

## Known Limitations

### `--cache-reuse` Broken for Gemma 4 — No Viable Workaround
[#21468](https://github.com/ggml-org/llama.cpp/issues/21468) — iSWA shared KV layers break prefix matching. Manual slot save/restore workaround (`CACHE_WORKAROUND=1`) is **counterproductive** — same bug affects slot restore, making requests 40% slower. Code remains off by default for retesting when upstream fixes land.

### Action Looping (Gemma 4 26B via OpenRouter) — Mitigated
The 26B model occasionally repeats the same action. Handled by the duplicate action guard (see Architecture).

## Models

- **Local**: Gemma 4 E4B (MoE 12B/4B active, Q4_K_M, ~5GB) — `models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf`
- **OpenRouter**: Gemma 4 26B-A4B (MoE 26B/4B active) — `google/gemma-4-26b-a4b-it` via Parasail/bf16
