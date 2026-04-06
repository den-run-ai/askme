# Local LLM Setup — llama.cpp + Gemma 4 E4B

Local inference setup on Mac M1 (16GB) using llama.cpp with Gemma 4 E4B (MoE, 12B total / 4B active).

## Models

| Model | Type | Size | GPU | Status |
|---|---|---|---|---|
| **Gemma 4 E4B** (Q4_K_M) | MoE 12B/4B | ~5.4 GB | Full Metal | **Primary** |
| Qwen 3.5 9B (Q4_K_M) | Dense | 5.3 GB | Full Metal | Legacy |
| Qwen 3.5 35B-A3B (UD-IQ3_S) | MoE | 12.7 GB | CPU only | Legacy |

## Quick Start

### Interactive chat (Gemma 4 E4B, recommended)
```bash
cd /Users/macmone/code/llama.cpp
./build/bin/llama-cli \
  -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf \
  -ngl 99 --ctx-size 16384 --flash-attn on \
  --temp 0.6 --top-p 0.95 --top-k 20 --min-p 0.0 \
  -cnv
```

### API server (OpenAI-compatible, optimized for agentic use)
```bash
mkdir -p /tmp/llama-cache
./build/bin/llama-server \
  -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf \
  -ngl 99 --ctx-size 16384 --flash-attn on \
  -np 1 --cache-reuse 256 --slot-save-path /tmp/llama-cache \
  --port 8080
```
Web UI: http://localhost:8080

### Test the API
```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma-4-e4b","messages":[{"role":"user","content":"Hello!"}],"max_tokens":200}'
```

## OpenHands Integration

See [openhands.setup.local.md](openhands.setup.local.md) for connecting OpenHands to this local server.

## File Layout

```
llama.cpp/
  build/bin/            # Compiled binaries (llama-cli, llama-server, etc.)
  models/
    gemma4-e4b/         # Gemma 4 E4B (Q4_K_M, ~5.4GB) — PRIMARY
    qwen35/             # 35B MoE (UD-IQ3_S, 12.7GB) — legacy
    qwen35-9b/          # 9B Dense (Q4_K_M, 5.3GB) — legacy
  agent/
    askme.py            # NanAgent — minimal local agent
    plan.md             # Detailed setup steps and troubleshooting
  openhands.setup.local.md  # OpenHands configuration
```

## Rebuild

```bash
cmake -B build -DLLAMA_CURL=ON
cmake --build build --config Release -j$(sysctl -n hw.ncpu)
```

## Performance (M1 16GB, Gemma 4 E4B Q4_K_M)

- **~7 tok/s generation**, **~22 tok/s prompt processing** (full Metal GPU)
- 29 unit tests + 3 integration tests pass (zero replans)
- Clean JSON output, no `<think>` tag leakage

## 16GB M1 Lessons Learned

- **Gemma 4 E4B is the sweet spot** — MoE with 4B active params, ~5.0GB Q4_K_M, full Metal GPU
- **No forced thinking mode** — unlike Qwen 3.5, Gemma 4 doesn't leak `<think>` tags into responses
- **35B MoE OOMs on Metal GPU** regardless of context size or flash attention
- `--flash-attn on` significantly reduces GPU memory usage
- **Use `-np 1` for agents** — default auto-detects 4 slots, splitting context 4 ways
- **Use `--cache-reuse 256`** — enables KV prefix reuse across different requests (off by default)
