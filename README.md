# AskMe

Minimal agent that runs on local LLMs. Takes a prompt, plans tasks, executes them via shell/write/edit/read actions, and replans on failure. Single file, no frameworks, no dependencies beyond `requests`.

Built for Gemma 4 E4B on llama-server, also supports OpenRouter.

## Quick Start

```bash
# All commands below assume you're in the agent/ directory.
# From the parent llama.cpp dir, prefix paths with agent/ (e.g. python3 agent/askme.py).

# 1. Start llama-server (from llama.cpp root)
cd /Users/macmone/code/llama.cpp
mkdir -p /tmp/llama-cache
./build/bin/llama-server \
  -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf \
  -ngl 99 --ctx-size 16384 --flash-attn on \
  --cache-type-k q4_0 --cache-type-v q4_0 \
  --swa-full --cache-reuse 256 \
  -np 1 --slot-save-path /tmp/llama-cache \
  --port 8080

# 2. Run the agent (from agent/ directory)
python3 askme.py "create a hello world program in C and compile it"

# Or via OpenRouter (set OPENROUTER_API_KEY in .env)
LLM_BACKEND=openrouter python3 askme.py "your task here"

# Evaluation/automation adapter: fixed workspace, prompt file, structured result
mkdir -p /tmp/task-workspace
python3 askme.py --prompt-file task.md --working-dir /tmp/task-workspace \
  --result-json /tmp/run.json --reasoning-policy gated \
  --max-replans 3 --max-tasks 4 --max-steps 10 --goal-context-chars 1200
```

## How It Works

**Preflight → Plan → Execute → Replan.** Before planning, the agent probes the environment (platform, available tools, package managers). The LLM breaks your prompt into tasks, executes each one step-by-step (shell, write, edit, read), and replans if something fails. Up to 3 replans. After all tasks complete, an LLM-based final validation verifies the goal was actually achieved (gated by complexity signals). By default, the agent will **not** install software — it fails fast with a prerequisite message. Set `ALLOW_SYSTEM_INSTALLS=1` to permit installs.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `LLM_BACKEND` | `local` | `local` or `openrouter` |
| `OPENROUTER_API_KEY` | (from `.env`) | API key for OpenRouter |
| `OPENROUTER_MODEL` | `google/gemma-4-26b-a4b-it` | OpenRouter model |
| `OPENROUTER_PROVIDER` | `Parasail` | Preferred OpenRouter provider; empty means automatic routing |
| `OPENROUTER_ALLOW_FALLBACKS` | `1` | Whether OpenRouter may leave the preferred provider |
| `OPENROUTER_REQUIRE_PARAMETERS` | `0` | Require the provider to advertise support for all request parameters |
| `LLM_API_URL` | `http://localhost:8080/v1/chat/completions` | Custom API URL (local only) |
| `LLM_MODEL` | `gemma-4-e4b` | Model name (local only) |
| `ALLOW_SYSTEM_INSTALLS` | `0` | Whether the agent may install software |
| `AGENT_FINAL_VALIDATE` | `auto` | Final validation: `auto`, `always`, or `0` (disabled) |
| `AGENT_REASONING_POLICY` | `gated` | Explicit-reasoning requests: `gated` preserves the recovery policy; `off` suppresses them at every call site |
| `AGENT_GOAL_CONTEXT_CHARS` | `300` | Goal characters retained for executor and task-local replan context; independent of result/history truncation |
| `AGENT_RUN_LOG` | (unset) | Path to append JSONL events (`run_start`, `reasoning_decision`, `plan`, `tokens`, `step`, `task_complete`, `task_failed`, `validation`, `run_end`). Disabled when unset. |

`off` controls explicit reasoning requests sent by the harness; it is not a claim
that the model performs no internal reasoning. Each request attempt logs the
requested policy, trigger, and effective reasoning level when `AGENT_RUN_LOG` is
enabled.

## Tests

```bash
# Unit tests (mocked, no LLM needed)
python3 -m pytest tests/ -v -k "not Integration and not ServerConfig and not (OpenRouter and not ThinkingRetry and not PlannerReasoning) and not PlannerReasoningOpenRouter"

# Integration — local (requires llama-server on :8080)
python3 -m pytest tests/test_agent_integration.py -s -v -k "TestIntegration and not Medium and not Hard"
python3 -m pytest tests/test_agent_integration.py -s -v -k "IntegrationMedium"
python3 -m pytest tests/test_agent_integration.py -s -v -k "IntegrationHard"

# Integration — OpenRouter (requires OPENROUTER_API_KEY in .env)
python3 -m pytest tests/test_agent_integration.py -s -v -k "TestOpenRouterEasy or TestOpenRouterMedium or TestOpenRouterHard"

# Multi-trial benchmark harness (reports median + range across N trials)
python3 tests/bench_harness.py                                    # 3 trials, easy, local
python3 tests/bench_harness.py --suite medium --trials 5          # 5 trials, medium, local
python3 tests/bench_harness.py --backend openrouter --suite hard  # 3 trials, hard, openrouter
python3 tests/bench_harness.py --backend openrouter --suite easy --trials 1 \
  --model qwen/qwen3.6-27b --provider siliconflow              # strict provider pin
python3 tests/bench_harness.py --test test_shell_and_write        # single test
python3 tests/bench_harness.py --list                             # show available tests

# Native semantic-workflow qualification (offline; no model call)
python3 -m pytest \
  tests/test_workflow_eval.py tests/test_workflow_alternatives.py -q
python3 tests/workflow_eval.py \
  tests/workflows/config_precedence/manifest.json --agent noop
```

## Files

- `askme.py` — the agent
- `tests/` — unit and integration tests, split by concern
- `tests/bench_harness.py` — multi-trial benchmark harness (E01)
- `tests/workflow_eval.py` — manifest-driven native workflow evaluator
- `tests/workflows/` — versioned semantic fixtures and [evaluation protocol](tests/workflows/PROTOCOL.md)
- [ARCHITECTURE.md](ARCHITECTURE.md) — loop design, state model, action model, current constraints
- [gemma4-setup.md](gemma4-setup.md) — llama-server config, KV cache, model notes
- [PERFORMANCE.md](PERFORMANCE.md) — benchmark history and test-run matrices
- [CLAUDE.md](CLAUDE.md) — guidance for AI agents working in this directory
