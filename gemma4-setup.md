# Gemma 4 E4B — Setup, Configuration & Optimization

Mac M1 16GB. Audited 2026-04-07 against `ggml-org/llama.cpp` master.

## Model

| Model | File | Size | Architecture | GPU | Status |
|-------|------|------|-------------|-----|--------|
| **Gemma 4 E4B** Q4_K_M | `models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf` | ~5.0 GB | MoE 12B (4B active), iSWA | Full Metal | **Primary** |

- **iSWA** (Interleaved Sliding Window Attention) — 3 sliding-window layers + 1 global attention layer, repeating
- Per-Layer Embeddings (PLE) make the file ~8B-sized despite 4.5B effective params
- llama.cpp has a dedicated Gemma 4 chat/tool-call parser (`COMMON_CHAT_FORMAT_PEG_GEMMA4`)
- No thinking mode by default (unlike Qwen 3.5) — responses are direct, no `<think>` overhead
- Q8_0 (8 GB) is viable if you want higher quality and delete Qwen 3.5 9B later

### Download

```bash
mkdir -p /Users/macmone/code/llama.cpp/models/gemma4-e4b

huggingface-cli download ggml-org/gemma-4-E4B-it-GGUF \
  gemma-4-e4b-it-Q4_K_M.gguf \
  --local-dir /Users/macmone/code/llama.cpp/models/gemma4-e4b
```

Optional multimodal projector (vision/audio):
```bash
huggingface-cli download ggml-org/gemma-4-E4B-it-GGUF \
  mmproj-gemma-4-e4b-it-f16.gguf \
  --local-dir /Users/macmone/code/llama.cpp/models/gemma4-e4b
```

### Build

```bash
cd /Users/macmone/code/llama.cpp
git pull
cmake -B build -DLLAMA_CURL=ON
cmake --build build --config Release -j$(sysctl -n hw.ncpu)
```

## Server Configuration

### Agentic use (NanAgent)

```bash
cd /Users/macmone/code/llama.cpp
mkdir -p /tmp/llama-cache
./build/bin/llama-server \
  -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf \
  -ngl 99 --ctx-size 16384 --flash-attn on \
  -np 1 --cache-reuse 256 --slot-save-path /tmp/llama-cache \
  --port 8080
```

| Flag | Purpose | Status |
|------|---------|--------|
| `-ngl 99` | Full GPU offload | Working |
| `--ctx-size 16384` | Full context for single slot | Working |
| `--flash-attn on` | Memory-efficient attention | Working |
| `-np 1` | Single slot (avoids 4-way context split on M1) | Working |
| `--cache-reuse 256` | KV prefix reuse across requests | **Broken for Gemma 4** — [#21468](https://github.com/ggml-org/llama.cpp/issues/21468) |
| `--slot-save-path /tmp/llama-cache` | Disk persistence for KV state | Working (verified by `TestServerConfig`) |

### Interactive chat

```bash
./build/bin/llama-cli \
  -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf \
  -ngl 99 --ctx-size 16384 \
  --temp 0.6 --top-p 0.95 --top-k 20 --min-p 0.0 \
  -cnv
```

### Multimodal server (if projector downloaded)

```bash
./build/bin/llama-server \
  -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf \
  --mmproj models/gemma4-e4b/mmproj-gemma-4-e4b-it-f16.gguf \
  -ngl 99 --ctx-size 16384 --port 8080
```

### Save/Restore KV State via API

```bash
# Save slot 0 (e.g. after processing system prompt)
curl http://localhost:8080/slots/0?action=save -X POST \
  -H "Content-Type: application/json" \
  -d '{"filename": "agent-system-prompt"}'

# Restore it later (instant — skips reprocessing)
curl http://localhost:8080/slots/0?action=restore -X POST \
  -H "Content-Type: application/json" \
  -d '{"filename": "agent-system-prompt"}'
```

## Critical Issue: `--cache-reuse` Broken for Gemma 4

**[Issue #21468](https://github.com/ggml-org/llama.cpp/issues/21468)** — Gemma 4's shared KV layers (iSWA architecture) break the prefix matching assumptions in `--cache-reuse`. The flag is **silently ignored**. Every request re-evaluates the full prompt from scratch.

**Impact on NanAgent:** The system prompt (~200 tokens) is re-processed on every `ask_llm()` call. For a medium integration test doing ~15 LLM calls, that's ~3000 wasted prompt tokens. On M1 at ~50 tok/s prompt eval, that's ~60s overhead per test — explaining why `fix_python_syntax` takes 520s and `fix_missing_include` takes 660s.

**Status:** Open, no fix yet. The iSWA sliding window pattern creates non-contiguous KV layouts that the current prefix matcher can't handle.

**Workaround (not yet implemented):** Manual slot save/restore via API could bypass this — save KV state after the first system prompt eval, restore before each subsequent call. The server already supports this (`/slots/0?action=save|restore`), and `TestServerConfig` verifies it works. See [Phase 2](#phase-2-manual-slot-saverestore-workaround-medium-effort) below.

## Upstream Gemma 4 Commits

Local HEAD: `941146b3f` (18 commits behind master as of 2026-04-07).

### Already in local build

| PR | Title | Impact |
|----|-------|--------|
| [#21309](https://github.com/ggml-org/llama.cpp/pull/21309) | Core Gemma 4 support (vision + MoE, no audio) | Foundation |
| [#21326](https://github.com/ggml-org/llama.cpp/pull/21326) | Template parser fixes | Correct chat formatting |
| [#21343](https://github.com/ggml-org/llama.cpp/pull/21343) | Tokenizer fix (newline grouping, BPE bypass) | Correct tokenization |
| [#21418](https://github.com/ggml-org/llama.cpp/pull/21418) | Specialized parser (tool calling, interleaved thinking) | Tool use support |
| [#21390](https://github.com/ggml-org/llama.cpp/pull/21390) | `final_logit_softcapping` read for Gemma 4 (was stuck at 30.0f) | Output quality |
| [#21428](https://github.com/ggml-org/llama.cpp/pull/21428) | GGUF bool array fix (`sliding_window_pattern` loading) | Correct iSWA behavior |

### Missing locally (in 18 commits behind master)

| Commit | Title | Impact |
|--------|-------|--------|
| `4aa962e` / [#21488](https://github.com/ggml-org/llama.cpp/pull/21488) | Byte token handling in BPE detokenizer for Gemma4 | Tokenizer correctness — may fix edge cases in JSON output |
| `e8f5082` / [#21510](https://github.com/ggml-org/llama.cpp/pull/21510) | Fix restore for checkpoints with `pos_min == 0` | **Affects slot save/restore** — needed before implementing manual cache workaround |
| `15f786e` / [#21159](https://github.com/ggml-org/llama.cpp/pull/21159) | Optimized `flash_attn_stream_k_fixup` kernel | CUDA only, no M1 Metal impact |

### Not yet merged (watch list)

| PR | Title | Status | Impact |
|----|-------|--------|--------|
| [#21492](https://github.com/ggml-org/llama.cpp/pull/21492) | Remove `</s>` EOS token for Gemma 4 | Approved, not merged | **May fix premature generation stops** — could reduce JSON truncation before closing brace, which currently triggers thinking retries |
| [#21518](https://github.com/ggml-org/llama.cpp/pull/21518) | Activation rotation for heterogeneous iSWA | Open | Enables Hadamard rotation ([#21038](https://github.com/ggml-org/llama.cpp/pull/21038)) for Gemma 4 — KL divergence drops 0.947 → 0.746 with quantized KV. No benefit at f16 KV (our current setup). |
| [#21394](https://github.com/ggml-org/llama.cpp/issues/21394) | `attn_rot_k` and `v = 0` (rotation disabled for variable head dims) | Open | Head dims vary per layer (256 SWA, 512 global) — blocks #21518 |
| Draft (ggerganov) | Gemma 4 FFN MoE precision to F32 | Draft | Better expert routing quality, some speed cost |

### Known Gemma 4 Bugs (upstream, no fix)

| Issue | Title | Relevance |
|-------|-------|-----------|
| [#21468](https://github.com/ggml-org/llama.cpp/issues/21468) | Cache reuse broken for Gemma 4 | **Critical** — see above |
| [#21321](https://github.com/ggml-org/llama.cpp/issues/21321) | Generates `<unused24>` tokens | Not seen in agent use (low temp 0.1 may suppress) |
| [#21424](https://github.com/ggml-org/llama.cpp/issues/21424) | Very long generation latency (Vulkan/AMD) | Not applicable (Metal) |

## Implementation Plan

### Phase 1: Update build (low risk, immediate)

```bash
cd /Users/macmone/code/llama.cpp
git stash  # preserve local changes
git pull origin master
git stash pop
cmake -B build -DLLAMA_CURL=ON
cmake --build build --config Release -j$(sysctl -n hw.ncpu)
```

Picks up: Gemma4 byte token fix (#21488), checkpoint restore fix (#21510).

**Test:** Run unit tests + easy integration to verify no regressions.

```bash
python3 -m pytest agent/test_agent.py -v -k "not Integration and not ServerConfig and not (OpenRouter and not ThinkingRetry)"
python3 -m pytest agent/test_agent.py -s -v -k "TestIntegration and not Medium and not Hard"
```

### Phase 2: Manual slot save/restore workaround (medium effort)

Bypass broken `--cache-reuse` by explicitly saving/restoring KV state around the system prompt.

**Changes to `askme.py`:**

1. Add a `_warm_cache()` function called once at start of `run()`:
   ```python
   def _warm_cache(base_url):
       """Pre-process system prompt and save KV state for reuse."""
       # Send minimal request with system prompt to populate KV
       requests.post(f"{base_url}/v1/chat/completions", json={
           "model": "gemma-4-e4b",
           "messages": [{"role": "system", "content": SYS_PLAN},
                        {"role": "user", "content": "hi"}],
           "max_tokens": 1
       }, timeout=60)
       # Save the KV state
       requests.post(f"{base_url}/slots/0?action=save",
           json={"filename": "agent-system-prompt"}, timeout=10)
   ```

2. Add `_restore_cache()` call before each `ask_llm()`:
   ```python
   def _restore_cache(base_url):
       """Restore system prompt KV state before each request."""
       requests.post(f"{base_url}/slots/0?action=restore",
           json={"filename": "agent-system-prompt"}, timeout=10)
   ```

3. Gate behind `CACHE_WORKAROUND=1` env var (off by default until validated).

**Risk:** The checkpoint restore fix (#21510) in Phase 1 may be needed first — it fixes restore when `pos_min == 0`, which is exactly our case (system prompt starts at position 0).

**Test plan:**
- Verify `TestServerConfig` still passes (save/restore mechanics)
- Add `TestCacheWorkaround` unit test:
  - Mock `requests.post` to verify save called once at start, restore called before each `ask_llm`
  - Verify save failure is non-fatal (falls back to no caching)
- Run easy + medium integration tests and compare times:
  - Expected: ~30-50% speedup on multi-call tests (system prompt eval saved)
  - Measure: prompt eval tokens in `/metrics` endpoint before/after
- Add timing test:
  ```python
  def test_cache_restore_faster_than_cold():
      """Second request with restore should have lower prompt eval time."""
      # Cold request
      t0 = time.time(); ask_llm([sys_msg, user_msg], 1); cold = time.time() - t0
      # Save state
      requests.post(".../slots/0?action=save", json={"filename": "test"})
      # Restore + request
      requests.post(".../slots/0?action=restore", json={"filename": "test"})
      t0 = time.time(); ask_llm([sys_msg, user_msg], 1); warm = time.time() - t0
      assert warm < cold * 0.7, f"Restore not faster: cold={cold:.1f}s warm={warm:.1f}s"
  ```

### Phase 3: Monitor upstream fixes (no code changes)

Watch these PRs — when merged, pull and rebuild:

| PR | What to do when merged |
|----|----------------------|
| [#21492](https://github.com/ggml-org/llama.cpp/pull/21492) (`</s>` EOS fix) | Pull, rebuild, re-run medium tests — may reduce JSON truncation that triggers thinking retries |
| [#21468](https://github.com/ggml-org/llama.cpp/issues/21468) fix (cache-reuse for iSWA) | Pull, rebuild, remove Phase 2 workaround, verify `--cache-reuse 256` works via timing test |
| [#21518](https://github.com/ggml-org/llama.cpp/pull/21518) (activation rotation for iSWA) | Only matters if switching to quantized KV cache (`--cache-type-k q4_0`). No impact at f16. |

### Phase 4: Future optimizations (blocked on upstream)

- **Quantized KV cache** — Once #21518 lands, test `--cache-type-k q4_0 --cache-type-v q4_0` for ~4x KV memory reduction. Would allow `--ctx-size 32768` or `--ctx-size 65536` on 16GB M1.
- **TurboQuant KV** — [#21089](https://github.com/ggml-org/llama.cpp/pull/21089) adds 3.5-bit KV cache types (TBQ3_0/TBQ4_0). CPU-only for now, no Metal support.
- **MoE F32 precision** — ggerganov's draft PR for Gemma 4 FFN MoE precision. Would improve expert routing quality at cost of some speed.

## Verification Checklist

After Phase 1 (build update):
- [ ] `git log --oneline -1` shows commit after `4aa962e` (Gemma4 byte token fix)
- [ ] Unit tests pass: `pytest agent/test_agent.py -v -k "not Integration and not ServerConfig and not (OpenRouter and not ThinkingRetry)"`
- [ ] Server starts cleanly with same flags
- [ ] `TestServerConfig` passes (4 tests)
- [ ] Easy integration tests pass (3 tests)

After Phase 2 (cache workaround):
- [ ] `TestCacheWorkaround` unit test passes
- [ ] `test_cache_restore_faster_than_cold` integration test shows measurable speedup
- [ ] Easy integration: time parity or improvement vs Phase 1
- [ ] Medium integration: measurable time reduction (target: <400s for `fix_python_syntax`, <500s for `fix_missing_include`)
- [ ] `CACHE_WORKAROUND=0` still works (graceful fallback)

## 16GB M1 Lessons Learned

- **Gemma 4 E4B is the sweet spot** — MoE with 4B active params, ~5.0GB Q4_K_M, full Metal GPU
- **No forced thinking mode** — unlike Qwen 3.5, Gemma 4 doesn't leak `<think>` tags into responses
- **35B MoE OOMs on Metal GPU** regardless of context size or flash attention
- **Use `-np 1` for agents** — default auto-detects 4 slots, splitting context 4 ways
- **`--cache-reuse 256` is currently broken** for Gemma 4 iSWA ([#21468](https://github.com/ggml-org/llama.cpp/issues/21468)) — flag is silently ignored. Manual slot save/restore is the workaround until upstream fixes it.
- **`--flash-attn on`** works correctly on Metal for Gemma 4 iSWA

## References

- [Issue #21468 — Cache reuse broken for Gemma 4](https://github.com/ggml-org/llama.cpp/issues/21468)
- [PR #21488 — Byte token handling for Gemma4](https://github.com/ggml-org/llama.cpp/pull/21488)
- [PR #21510 — Fix restore for checkpoints with pos_min == 0](https://github.com/ggml-org/llama.cpp/pull/21510)
- [PR #21492 — Remove </s> EOS for Gemma4](https://github.com/ggml-org/llama.cpp/pull/21492)
- [PR #21518 — Activation rotation for heterogeneous iSWA](https://github.com/ggml-org/llama.cpp/pull/21518)
- [Issue #21394 — attn_rot disabled for Gemma 4](https://github.com/ggml-org/llama.cpp/issues/21394)
- [PR #21038 — Hadamard rotation for better KV quantization](https://github.com/ggml-org/llama.cpp/pull/21038)
- [PR #21089 — TurboQuant CPU KV cache types](https://github.com/ggml-org/llama.cpp/pull/21089)
- [Discussion #20572 — Persistent KV cache tutorial](https://github.com/ggml-org/llama.cpp/discussions/20572)
- [llama-server slots API](https://github.com/ggml-org/llama.cpp/blob/master/examples/server/README.md)
- [gemma4-iswa.cpp source](https://github.com/ggml-org/llama.cpp/blob/master/src/models/gemma4-iswa.cpp)
