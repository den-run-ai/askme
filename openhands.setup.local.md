# OpenHands Local Setup

## Current State (2026-04-06)
- OpenHands CLI v1.7.0 installed via uv at `/Users/macmone/.local/bin/openhands`
- Settings configured at `~/.openhands/settings.json` (model=openai/gemma-4-e4b, base_url=http://localhost:8080/v1)
- **CLI mode works without Docker** — use `openhands` (interactive TUI)
- **Web GUI (`openhands serve`) requires Docker** and pulls ~5GB of images — avoid on limited network

## Step 1: Start llama.cpp server

```bash
cd /Users/macmone/code/llama.cpp
./build/bin/llama-server \
  -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf \
  -ngl 99 --ctx-size 16384 \
  --flash-attn on \
  --port 8080
```

## Step 2: Run OpenHands

### Option A: CLI mode (no Docker needed)

```bash
# Interactive TUI
LLM_DROP_PARAMS=true openhands

# Or headless with a task (requires one-time TUI save first — see note below)
LLM_DROP_PARAMS=true openhands --headless -t "your task here"
```

> **Note:** `--headless` mode requires settings to have been saved through the TUI at least once.
> Run `openhands`, hit Save in the Settings dialog, then Ctrl+Q. After that, headless works.

### Option B: Web GUI (requires Docker + ~5GB image pull)

```bash
openhands serve
# Open http://localhost:3000
```

### Option C: Build from source with local runtime (no Docker sandbox)

```bash
cd /Users/macmone/code/OpenHands   # clone in progress, see below
export INSTALL_DOCKER=0
export RUNTIME=local
export INSTALL_PLAYWRIGHT=false
make build && make run
# Frontend at http://localhost:3001
```

If clone was interrupted: `cd /Users/macmone/code && git clone --depth 1 https://github.com/OpenHands/OpenHands.git`

Prereqs: python3.12 (installed), node>=22 (installed v23.10), poetry>=1.8 (installing via `uv tool install poetry`)

## Settings

Configure at `~/.openhands/settings.json`:
- **Model**: `openai/gemma-4-e4b`
- **Base URL**: `http://localhost:8080/v1`
- **API Key**: `local-llm`

To reconfigure, run `openhands` and it will show the settings TUI on first launch.

## Key notes

- OpenHands ideally wants ~22k+ context, but 16384 is the practical max for 16GB RAM
- **Always set** `LLM_DROP_PARAMS=true` — llama-server rejects some OpenAI-specific parameters
- Gemma 4 E4B supports multimodal (vision/audio) — `LLM_DISABLE_VISION` no longer needed if mmproj is loaded
- See [plan.md](plan.md) for full build/download instructions and model inventory
