# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

IMPORTANT: Ensure you've thoroughly reviewed the [AGENTS.md](../AGENTS.md) file before beginning any work.

## What This Is

NanAgent — a minimal, self-contained agent that runs on local or remote LLMs (Gemma 4 via llama-server or OpenRouter). It takes a user prompt, plans tasks, executes them via shell/write/edit/read actions, and replans on failure. Single file, no frameworks, no external dependencies beyond `requests`. Each run gets an isolated temp directory — agent-created files never pollute the repo. Exits with code 1 on failure for script chaining.

## Commands

```bash
# All commands below assume you're in the agent/ directory.
# From the parent llama.cpp dir, prefix paths with agent/ (e.g. python3 agent/askme.py).

# Run the agent (local, requires llama-server on :8080)
python3 askme.py "your request here"

# Run with cache workaround (COUNTERPRODUCTIVE — see Known Limitations)
# CACHE_WORKAROUND=1 python3 askme.py "your request here"

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
  -np 1 --cache-reuse 256 --slot-save-path /tmp/llama-cache \
  --port 8080

# Unit tests (mocked, no LLM needed)
python3 -m pytest tests/ -v -k "not Integration and not ServerConfig and not (OpenRouter and not ThinkingRetry and not PlannerReasoning) and not PlannerReasoningOpenRouter"

# Single test
python3 -m pytest tests/ -v -k "test_simple_success"

# Integration tests — local (requires llama-server on :8080)
python3 -m pytest tests/test_agent_integration.py -s -v -k "TestIntegration and not Medium and not Hard"  # easy (~6min)
python3 -m pytest tests/test_agent_integration.py -s -v -k "IntegrationMedium"   # medium: error recovery (~40min)
python3 -m pytest tests/test_agent_integration.py -s -v -k "IntegrationHard"     # hard: replanning (~17min)

# Integration tests — OpenRouter (requires OPENROUTER_API_KEY in .env)
python3 -m pytest tests/test_agent_integration.py -s -v -k "TestOpenRouterEasy"     # easy (~10s)
python3 -m pytest tests/test_agent_integration.py -s -v -k "TestOpenRouterMedium"   # medium (~2min)
python3 -m pytest tests/test_agent_integration.py -s -v -k "TestOpenRouterHard"     # hard (~2min)

# Rebuild llama.cpp (if needed, from llama.cpp root)
cmake -B build -DLLAMA_CURL=ON
cmake --build build --config Release -j$(sysctl -n hw.ncpu)
```

## Architecture

`askme.py` implements a **Preflight → Plan → Execute → Replan** loop:

0. **Preflight** (`preflight_probe`) — Before the first plan, deterministically probes: platform, arch, working dir listing, available/missing tools (fixed allowlist: python3, go, node, gcc, cc, make, cargo, rustc, java, javac), and package managers (brew, apt-get, dnf, pacman, apk). Feeds structured dict + execution policy into planner state.
1. **Planner** (`get_plan`) — LLM receives the user prompt + full state (completed tasks, typed errors, environment, policy) and outputs `{"tasks": ["task1", "task2", ...]}`. Always uses thinking (`think=True`) with `PLANNER_MAX_TOKENS=768`. Errors are summarized into typed categories (`[missing_tool]`, `[timeout]`, etc.) before reaching the planner.
2. **Executor** (`get_step`) — For each task, LLM receives a **slim state** (current task + completed tasks + last 3 steps with cross-task carryover + missing_tools + policy) and proposes one action at a time: `shell`, `write`, `edit`, `read`, `done`, or `fail`. `edit` does exact single-match string replacement (fails on zero or multiple matches); `write` replaces the entire file. The model is prompted to prefer `edit` for localized changes and `write` for new files. Completion is goal-aware — executor must satisfy the full task description, not just one successful step.
3. **Replan** — If a task fails, the full loop restarts with a new plan (up to `MAX_REPLANS=3`). Errors reset per replan since the planner already saw them.

The core loop lives in `_run_loop()`, which returns a structured dict `{"status": "complete"|"exhausted", "state": state, "log": history}`. `run()` is a thin wrapper that maps to `True`/`False`. Integration tests call `_run_loop()` directly (via `int_run()`) to get the rich result dict while exercising the same production behavior.

### Working directory isolation
`run()` creates a temp directory per invocation (`/tmp/nanagent_*`), printed at start and end. All `execute()` calls use this dir as cwd. Shell commands run there; relative write/read paths resolve there. Callers can pass `working_dir=` to override (used by tests). `run()` returns `True`/`False`; `__main__` exits with code 1 on failure.

Key design constraint: the local LLM (Gemma 4 E4B, 4B active params) is slow (~7 tok/s) with 16K context on M1. Everything is optimized for minimal token usage — short system prompts, JSON-only output, sliding window of step history, truncated outputs.

### `ask_llm` parsing pipeline
LLM responses go through: strip `<think>` tags → strip `<|channel>` blocks → strip markdown code fences → extract JSON object → retry up to 2 times on parse failure. Retries auto-escalate with thinking: attempt 1 = medium effort, attempt 2 = high effort. When `think=True` is passed (after a failed step execution), thinking starts from attempt 0.

### LLM transport hardening
All `requests.post()` calls use `timeout=LLM_TIMEOUT` (120s). Transport errors (connection refused, timeout, non-JSON body, 5xx/429) retry with backoff (1s, 3s). Client errors (400/401/403) fail fast. After all retries exhausted, `LLMTransportError` is raised. Both planner and executor phases catch `LLMTransportError` — planner errors consume a plan attempt, executor errors trigger replan. JSON-body `"error"` key handling is preserved (existing behavior, no backoff).

### Error handling
- Shell output: `r.stdout[:300] + r.stderr[-300:]` — tail-truncates stderr to keep actual error messages
- Input caps: `MAX_INPUT=300` chars per field sent to executor — prevents path bloat eating context
- Parse-error-as-failure: if `get_step()` raises after a successful step, the task fails and replans (no false auto-completion)
- **Typed failure classification**: `classify_error()` categorizes errors as `timeout`, `missing_tool`, `permission_denied`, `missing_file`, `compile_error`, or `unknown`. Error types are stored in step entries and used for summarization.
- **Error summarization**: `summarize_errors()` groups raw errors by type, deduplicates, and caps at 3 per type before passing to planner. Planner sees `[missing_tool] go: command not found` not raw strings.

### Execution policy
- `ALLOW_SYSTEM_INSTALLS` (env var, default `false`) — controls whether the agent may install software. When false, planner and executor are instructed to fail fast with a prerequisite message instead of attempting installation.
- `ALLOW_NETWORK` (env var, default `true`) — reserved for future use.
- Policy is injected into both planner and executor state so both see the constraints.

### Command-aware timeouts
- Default shell timeout: 30s (`SHELL_TIMEOUT`)
- Install/build commands: 120s (`SHELL_TIMEOUT_LONG`) — matches patterns like `install`, `cmake`, `cargo build`, `brew`
- Model-specified hint: `action.timeout` field, capped at 300s (`SHELL_TIMEOUT_MAX`), minimum 5s
- Timeout failures are exempt from the duplicate guard's "same shell failed twice" rule — they get one retry with the longer timeout.

### State split
- **Planner** sees: `completed_tasks`, `errors` (typed/summarized), `environment` (platform, arch, tools, pkg managers, dir listing), `policy`
- **Executor** sees: `task`, `task_index`, `step` counter, `completed_tasks[-3:]`, `last_steps[-3:]` with cross-task carryover, `missing_tools`, `policy`
- Write/read args use **basename** in slim state (e.g. `main.c` not `/full/path/main.c`)
- Write output says `"Wrote main.c"` (basename) — critical for LLM to recognize file was created

## Files

- `askme.py` — the agent (self-contained, ~695 lines)
- `tests/` — test suite (5 modules + support files, 168 tests total)
  - `conftest.py` — pytest fixtures (`work_dir`) and skip markers (`skip_no_llm`, `skip_no_openrouter`)
  - `_test_support.py` — mock helpers, integration test runners (`int_run` delegates to `_run_loop()`, `or_run`), limit constants
  - `test_agent_core.py` — execute(), ask_llm(), thinking retry, null-arg normalization, LLM transport hardening (~495 lines)
  - `test_agent_loop.py` — run() loop, cross-task state, output formatting, content serialization (~260 lines)
  - `test_agent_recovery.py` — duplicate guard, cache workaround, failure classification, error summarization, completion semantics (~430 lines)
  - `test_agent_planning.py` — planner reasoning, preflight probe, execution policy, command-aware timeouts, server config (~380 lines)
  - `test_agent_integration.py` — local + OpenRouter integration tests (easy/medium/hard) + planner reasoning integration (~400 lines)
- `ARCHITECTURE.md` — detailed architecture doc and design decisions
- `gemma4-setup.md` — Gemma 4 setup, server config, upstream PR tracker, optimization plan
- `.env` — OPENROUTER_API_KEY (not committed)

## Testing Conventions

- Unit tests mock `ask_llm` or `requests.post` — no LLM needed
- `TestCrossTaskState` — verifies completed_tasks and step carryover across tasks
- `TestOutputFormatting` — verifies basename in write output and slim state args
- `TestWriteContentSerialization` — verifies dict/list content auto-serialized to JSON
- `TestDuplicateGuard` — verifies per-action-type duplicate detection: write/edit duplicates skip and continue, shell duplicates auto-done/fail
- `TestCacheWorkaround` — verifies slot save/restore lifecycle, non-fatal failure, backend gating
- `TestPlannerReasoning` — verifies planner always uses think=True and PLANNER_MAX_TOKENS, system prompt includes specificity hints, null-content retry
- `TestPreflightProbe` — verifies environment probing returns platform, arch, tools, dir listing, package managers; verifies run() injects into planner state
- `TestExecutionPolicy` — verifies policy defaults, env override, planner/executor prompt rules, executor sees missing_tools and policy
- `TestFailureClassification` — verifies error categorization (timeout, missing_tool, permission_denied, missing_file, compile_error, unknown), error_type in results
- `TestCommandAwareTimeout` — verifies default/install/build/model-hint timeouts, timeout retry not blocked by duplicate guard
- `TestErrorSummarization` — verifies typed grouping, deduplication, per-type caps, planner receives summarized errors
- `TestCompletionSemantics` — verifies old "any successful step → done" rule removed, goal-aware completion rule present
- `TestLLMTransport` — verifies timeout propagation, retry on transport errors (timeout, connection, non-JSON, 502), fail-fast on 401, LLMTransportError in planner/executor phases, JSON error key preserved
- Integration tests use `int_run()` which delegates to `_run_loop()` with tight limits (`INT_MAX_REPLANS=1`, `INT_MAX_TASKS=3`, `INT_MAX_STEPS=5`)
- Local integration tests are skipped automatically if llama-server isn't running on `:8080`
- OpenRouter integration tests are skipped automatically if `OPENROUTER_API_KEY` is not set
- `TestServerConfig` tests verify the server has optimal agentic config (single slot, full context, cache enabled)

## Safety Limits

All limits are constants at the top of `askme.py`: `MAX_REPLANS=3`, `MAX_TASKS=10`, `MAX_STEPS=10`, `MAX_RESULT=300` chars, `MAX_STEP_HISTORY=3`, `MAX_INPUT=300` chars per executor field, `PLANNER_MAX_TOKENS=768`, `LLM_TIMEOUT=120s` (request timeout), `SHELL_TIMEOUT=30s` (default), `SHELL_TIMEOUT_LONG=120s` (install/build), `SHELL_TIMEOUT_MAX=300s` (hard cap). Executor `max_tokens`: 256 (local) / 512 (OpenRouter). Planner always uses thinking (`think=True`). `ALLOW_SYSTEM_INSTALLS=false` by default. Transport errors retry up to `MAX_LLM_RETRIES=2` times with backoff; `LLMTransportError` is raised on exhaustion. These exist to prevent runaway loops with a slow local LLM.

## Known Limitations

### "done" Emission — RESOLVED (2026-04-07)
Previously believed to be a model capability gap. Root cause was an **empty state bug** — executor received empty `completed_tasks` and `last_steps`, giving the model no context to recognize task completion. After fix, the local 12B model emits `{"action": "done"}` reliably. See local integration test results in [ARCHITECTURE.md](ARCHITECTURE.md#local-integration-test-results-2026-04-07).

### Action Looping (Gemma 4 26B via OpenRouter) — Mitigated
The 26B model occasionally repeats the same action. Now handled by the **duplicate action guard**: write loops (same content) → skip duplicate and continue, shell loops (same cmd, success) → auto-done, shell loops (same cmd, fail) → auto-fail + replan. Write with different content and reads are allowed through (legitimate retries).

### `--cache-reuse` Broken for Gemma 4 — No Viable Workaround
[#21468](https://github.com/ggml-org/llama.cpp/issues/21468) — iSWA shared KV layers break prefix matching. Server logs `cache_reuse is not supported by this context`. Manual slot save/restore workaround was implemented (`CACHE_WORKAROUND=1`) but is **counterproductive** — same iSWA bug affects slot restore too, making requests 40% slower. Code remains (off by default) for retesting when upstream fixes land. See [gemma4-setup.md](gemma4-setup.md) Phase 2.

## Models

- **Local**: Gemma 4 E4B (MoE 12B/4B active, Q4_K_M, ~5GB) — `models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf`
- **OpenRouter**: Gemma 4 26B-A4B (MoE 26B/4B active) — `google/gemma-4-26b-a4b-it` via Parasail/bf16
