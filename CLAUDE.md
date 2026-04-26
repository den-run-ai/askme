# CLAUDE.md

Guidance for Claude Code and other AI agents working in `agent/`.

IMPORTANT: Review [../AGENTS.md](../AGENTS.md) before beginning any work. It governs contributions to upstream llama.cpp. `agent/` is a private-fork project and falls under the private-fork exemption the policy calls out, but the upstream rules apply in full if `agent/` changes are ever proposed to ggml-org/llama.cpp.

## Orientation

NanAgent is a minimal, self-contained agent in a single file (`askme.py`). It takes a user prompt, plans tasks, executes them via shell/write/edit/read actions, and replans on failure. No frameworks, only `requests` as a dependency. Each run uses an isolated `/tmp/nanagent_*` directory, so agent-created files never pollute the repo.

Where to look:
- [README.md](README.md) — usage, quickstart, test commands
- [ARCHITECTURE.md](ARCHITECTURE.md) — loop design, state shapes, action model, current constraints
- [gemma4-setup.md](gemma4-setup.md) — llama-server config, KV cache, model notes
- [PERFORMANCE.md](PERFORMANCE.md) — benchmark history and test-run matrices

## Commands

```bash
# All commands below assume you're in the agent/ directory.

# Run the agent (local, requires llama-server on :8080)
python3 askme.py "your request here"

# Run via OpenRouter
LLM_BACKEND=openrouter python3 askme.py "your request here"

# Allow software installs (disabled by default)
ALLOW_SYSTEM_INSTALLS=1 python3 askme.py "your request here"

# Override final validation (auto=default, always, 0=disabled)
AGENT_FINAL_VALIDATE=always python3 askme.py "your request here"

# Unit tests (mocked, no LLM needed)
python3 -m pytest tests/ -v -k "not Integration and not ServerConfig and not (OpenRouter and not ThinkingRetry and not PlannerReasoning) and not PlannerReasoningOpenRouter"

# Single test
python3 -m pytest tests/ -v -k "test_simple_success"

# Integration — local (requires llama-server on :8080)
python3 -m pytest tests/test_agent_integration.py -s -v -k "TestIntegration and not Medium and not Hard"
python3 -m pytest tests/test_agent_integration.py -s -v -k "IntegrationMedium"
python3 -m pytest tests/test_agent_integration.py -s -v -k "IntegrationHard"

# Integration — OpenRouter (requires OPENROUTER_API_KEY in .env)
python3 -m pytest tests/test_agent_integration.py -s -v -k "TestOpenRouterEasy"
python3 -m pytest tests/test_agent_integration.py -s -v -k "TestOpenRouterMedium"
python3 -m pytest tests/test_agent_integration.py -s -v -k "TestOpenRouterHard"

# Multi-trial benchmark harness (median + range across N trials)
python3 tests/bench_harness.py                                    # 3 trials, easy, local
python3 tests/bench_harness.py --suite medium --backend openrouter # 3 trials, medium, openrouter
python3 tests/bench_harness.py --list                             # show available tests
```

For the `llama-server` launch command, see [README.md](README.md) or [gemma4-setup.md](gemma4-setup.md).

## Test Layout

Test modules are split by concern:
- `test_agent_core.py` — execute / ask_llm
- `test_agent_loop.py` — run loop / state
- `test_agent_recovery.py` — guards / error classification
- `test_agent_planning.py` — planner / preflight / policy / timeouts
- `test_agent_integration.py` — end-to-end (local + OpenRouter)

Unit tests mock `ask_llm` or `requests.post` — no LLM needed. Integration tests use `int_run()` from `_test_support.py` and auto-skip if the backend isn't available (no llama-server on `:8080`, or `OPENROUTER_API_KEY` unset).

## Working in This Repo

- `askme.py` is a single file. Prefer extending it over splitting it unless the file grows unreasonably.
- Token minimization matters — the local model is ~7 tok/s. Before adding to a system prompt or state payload, consider the per-call cost.
- State split is load-bearing: planner sees full state, executor sees slim state. See [ARCHITECTURE.md](ARCHITECTURE.md#core-file).
- When a test run exposes a new failure mode, record the observation in [PERFORMANCE.md](PERFORMANCE.md) with a date. If it shapes current design, add a bullet to Current Constraints in [ARCHITECTURE.md](ARCHITECTURE.md#current-constraints).
