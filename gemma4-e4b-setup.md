# Gemma 4 E4B Setup Plan — Mac M1 16GB

## Overview

Gemma 4 E4B is now the **primary local model**, replacing Qwen 3.5 9B.
4.5B effective params (8B with embeddings), 128K context, multimodal (text/image/audio/video), Apache 2.0.

## Status: COMPLETED (2026-04-06)

- Model downloaded via curl: `models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf` (~5.3GB)
- llama.cpp rebuilt with latest Gemma 4 support
- askme.py updated to use `gemma-4-e4b` model name
- All docs updated

## Download (already done)

```bash
mkdir -p /Users/macmone/code/llama.cpp/models/gemma4-e4b

huggingface-cli download ggml-org/gemma-4-E4B-it-GGUF \
  gemma-4-e4b-it-Q4_K_M.gguf \
  --local-dir /Users/macmone/code/llama.cpp/models/gemma4-e4b
```

~5.34 GB download.

### Step 3 (Optional): Download multimodal projector

Only needed if you want vision/audio capabilities:

```bash
huggingface-cli download ggml-org/gemma-4-E4B-it-GGUF \
  mmproj-gemma-4-e4b-it-f16.gguf \
  --local-dir /Users/macmone/code/llama.cpp/models/gemma4-e4b
```

### Step 4: Rebuild llama.cpp (recommended)

The codebase has Gemma 4 support (`LLM_ARCH_GEMMA4` + ISWA attention + Gemma 4 chat parser). Rebuild to ensure you have the latest:

```bash
cd /Users/macmone/code/llama.cpp
git pull
cmake -B build -DLLAMA_CURL=ON
cmake --build build --config Release -j$(sysctl -n hw.ncpu)
```

### Step 5: Test interactive chat

```bash
cd /Users/macmone/code/llama.cpp

./build/bin/llama-cli \
  -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf \
  -ngl 99 --ctx-size 16384 \
  --temp 0.6 --top-p 0.95 --top-k 20 --min-p 0.0 \
  -cnv
```

Expect ~50 tok/s on Metal GPU.

### Step 6: Start OpenAI-compatible server

```bash
cd /Users/macmone/code/llama.cpp

./build/bin/llama-server \
  -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf \
  -ngl 99 --ctx-size 16384 \
  --port 8080
```

Web UI at http://localhost:8080. OpenAI-compatible API at http://localhost:8080/v1.

### Step 7: Start server with multimodal support (if projector downloaded)

```bash
./build/bin/llama-server \
  -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf \
  --mmproj models/gemma4-e4b/mmproj-gemma-4-e4b-it-f16.gguf \
  -ngl 99 --ctx-size 16384 \
  --port 8080
```

### Step 8: Verify with OpenHands

No config changes needed — OpenHands already points to `http://localhost:8080/v1`. Just restart the server with Gemma 4 and it works.

## Quick Reference Commands

```bash
# Interactive chat
./build/bin/llama-cli \
  -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf \
  -ngl 99 --ctx-size 16384 -cnv

# Server (text-only)
./build/bin/llama-server \
  -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf \
  -ngl 99 --ctx-size 16384 --port 8080

# Server (multimodal)
./build/bin/llama-server \
  -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf \
  --mmproj models/gemma4-e4b/mmproj-gemma-4-e4b-it-f16.gguf \
  -ngl 99 --ctx-size 16384 --port 8080
```

## Expected Model Inventory After Setup

| Model | File | Size | GPU? | Speed |
|---|---|---|---|---|
| **Gemma 4 E4B Q4_K_M** | `models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf` | 5.34 GB | Full Metal | **~50 tok/s** |
| Qwen 3.5 9B Q4_K_M | `models/qwen35-9b/Qwen3.5-9B-Q4_K_M.gguf` | 5.3 GB | Full Metal | ~3 tok/s |

## Notes

- Gemma 4 E4B uses ISWA (Interleaved Sliding Window Attention) — 3 sliding-window layers + 1 global attention layer, repeating
- Per-Layer Embeddings (PLE) make the file ~8B-sized despite 4.5B effective params
- llama.cpp has a dedicated Gemma 4 chat/tool-call parser (`COMMON_CHAT_FORMAT_PEG_GEMMA4`)
- No thinking mode by default (unlike Qwen 3.5) — responses are direct, no `<think>` overhead
- `--flash-attn` flag: test it, should work on Metal but not explicitly confirmed for Gemma 4 ISWA
- Q8_0 (8 GB) is viable if you want higher quality and delete Qwen 3.5 9B later
