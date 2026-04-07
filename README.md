# NanAgent

Minimal agent that runs on local LLMs. Takes a prompt, plans tasks, executes them via shell/write/read actions, and replans on failure. Single file, no frameworks, no dependencies beyond `requests`.

Built for Gemma 4 E4B on llama-server, also supports OpenRouter.

## Quick Start

```bash
# 1. Start llama-server
cd /Users/macmone/code/llama.cpp
mkdir -p /tmp/llama-cache
./build/bin/llama-server \
  -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf \
  -ngl 99 --ctx-size 16384 --flash-attn on \
  -np 1 --cache-reuse 256 --slot-save-path /tmp/llama-cache \
  --port 8080

# 2. Run the agent
python3 agent/askme.py "create a hello world program in C and compile it"

# Or via OpenRouter (set OPENROUTER_API_KEY in agent/.env)
LLM_BACKEND=openrouter python3 agent/askme.py "your task here"
```

## How It Works

**Plan → Execute → Replan.** The LLM breaks your prompt into tasks, executes each one step-by-step (shell commands, file writes, file reads), and replans if something fails. Up to 3 replans.

## Tests

```bash
# Unit tests (no LLM needed)
python3 -m pytest agent/test_agent.py -v -k "not Integration and not ServerConfig and not (OpenRouter and not ThinkingRetry)"

# Integration tests (requires llama-server on :8080)
python3 -m pytest agent/test_agent.py -s -v -k "TestIntegration and not Medium and not Hard"
```

## Files

- `askme.py` — the agent (~336 lines)
- `test_agent.py` — unit + integration tests
- [ARCHITECTURE.md](ARCHITECTURE.md) — design details, server config reference
