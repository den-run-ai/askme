# Gemma 4 E4B — Setup, Configuration & Optimization

Mac M1 16GB. Last updated 2026-04-24.

**Current local build:** `85dde8dc4` (master as of 2026-04-16). Includes all prior fixes plus: tokenizer edge case (#21534), ambiguous grammar fix (#21661), reasoning budget sampler (#21697), official template alignment (#21704), shared-KV optional tensors (#21739), parsing edge cases (#21760), audio support (#21421/#21824), NVFP4 (#21971), custom newline split (#21406), multimodal tests (#21806).
**Fetched master snapshot as of 2026-04-24:** `13d36cf89` — 98 commits ahead of local, including the **cache-reuse fix ([#22288](https://github.com/ggml-org/llama.cpp/pull/22288)) that closes [#21468](https://github.com/ggml-org/llama.cpp/issues/21468)**. Public master may have advanced; rebuild target is `13d36cf89` or later.
**Phase 1 (build update): COMPLETE** — all tests pass. See [verification results](#phase-1-verification-results-2026-04-07) below.
**Phase 3 (quantized KV cache): COMPLETE** — q4_0 KV is the current recommended default (-4% vs f16 in single-trial test, ~4x less KV memory). See [Phase 3 results](#phase-3-quantized-kv-cache--complete-2026-04-08).
**Phase 4 (EOS fix): COMPLETE** — #21492 merged, rebuilt, 157/157 unit tests pass, 3/3 easy integration pass (10:02). See [Phase 4 results](#phase-4-eos-fix--complete-2026-04-08).
**Phase 5 (build refresh): COMPLETE** — pulled 12 new Gemma 4 commits to `85dde8dc4`, rebuilt, 159/159 unit tests pass, 3/3 easy integration pass (1:36 — fastest ever). See [Phase 5 results](#phase-5-build-refresh--complete-2026-04-16).
**Phase 6 (cache-reuse unblock): COMPLETE** — #21468 closed upstream via #22288 (merged to master). Rebuilt on master `a702f395`. Deterministic multi-turn benchmark (3 trials × 7 requests) shows no downside vs Phase 5: same cache behavior, same decode speed, 4.5% faster prompt eval. **Phase 6 is the new default.** See [caching_analysis.md](caching_analysis.md) and [PERFORMANCE.md](PERFORMANCE.md#phase-6-caching-ab--2026-04-25-build-a702f395-master).

## Model

| Model | File | Size | Architecture | GPU | Status |
|-------|------|------|-------------|-----|--------|
| **Gemma 4 E4B** Q4_K_M | `models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf` | ~5.0 GB | MoE 12B (4B active), iSWA | Full Metal | **Primary** |

- **iSWA** (Interleaved Sliding Window Attention) — 3 sliding-window layers + 1 global attention layer, repeating
- Per-Layer Embeddings (PLE) make the file ~8B-sized despite 4.5B effective params
- llama.cpp has a dedicated Gemma 4 chat/tool-call parser (`COMMON_CHAT_FORMAT_PEG_GEMMA4`)
- No thinking mode by default (unlike Qwen 3.5) — responses are direct, no `<think>` overhead. If enabled via the reasoning-budget sampler (#21697), note that `<think>...</think>` is emitted before JSON content and breaks structured parsers on the non-streaming path — see [unsloth #5044](https://github.com/unslothai/unsloth/issues/5044)
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
  --cache-type-k q4_0 --cache-type-v q4_0 \
  --swa-full --cache-reuse 256 \
  -np 1 --slot-save-path /tmp/llama-cache \
  --port 8080
```

#### Stable flags

| Flag | Purpose | Notes |
|------|---------|-------|
| `-ngl 99` | Full GPU offload | |
| `--ctx-size 16384` | Full context for single slot | |
| `--flash-attn on` | Memory-efficient attention | |
| `--cache-type-k q4_0 --cache-type-v q4_0` | Quantized KV cache (~4x less memory) | Current recommended default on M1 16GB. See [Phase 3 results](#phase-3-quantized-kv-cache--complete-2026-04-08). |
| `-np 1` | Single slot (avoids 4-way context split on M1) | |
| `--slot-save-path /tmp/llama-cache` | Disk persistence for KV state | Verified by `TestServerConfig` |
| `--swa-full --cache-reuse 256` | Full SWA attention + cross-slot cache reuse | Requires build `a702f395`+ ([#22288](https://github.com/ggml-org/llama.cpp/pull/22288)). Deterministic benchmark: no decode penalty, 4.5% faster prompt eval vs without. Not compatible with `--mmproj`. |
| `--port 8080` | Server port | |

#### Known-broken flags (do not use on current local build `85dde8dc4`)

| Flag | Issue | Status |
|------|-------|--------|
| `--cache-reuse 256` (alone, no `--swa-full`) | KV prefix reuse — broken for Gemma 4 iSWA ([#21468](https://github.com/ggml-org/llama.cpp/issues/21468)). On local build `85dde8dc4`, server logs `cache_reuse is not supported by this context, it will be disabled`. | **Fix merged upstream** via [#22288](https://github.com/ggml-org/llama.cpp/pull/22288) — requires `--swa-full` companion. After rebuild, move to the experimental table below and validate. |
| `CACHE_WORKAROUND=1` | Manual slot save/restore bypass — tested in Phase 2, **counterproductive** (40% slower). Same iSWA bug affects slot restore. | Disabled by default. Code kept for retesting after Phase 6 rebuild. |

#### Experimental / deferred

| Flag | Purpose | Status |
|------|---------|--------|
| `--swa-full --cache-reuse 256` | SWA-full prompt caching | **Promoted to stable default (2026-04-25).** Deterministic multi-turn benchmark (3 trials × 7 requests) showed no downside: same in-slot cache reuse, same decode speed, 4.5% faster prompt eval. See [caching_analysis.md](caching_analysis.md). |
| `--ctx-size 32768` with q4_0 | Double context using KV memory savings | Not yet validated — see Phase 3 checklist |
| `--cache-type-k q8_0 --cache-type-v q8_0` | Middle-ground KV quantization | Tested, 7% slower than f16 — not recommended |

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

# Restore it later (generic llama-server behavior — on Gemma 4 iSWA this is counterproductive, see Phase 2)
curl http://localhost:8080/slots/0?action=restore -X POST \
  -H "Content-Type: application/json" \
  -d '{"filename": "agent-system-prompt"}'
```

## Critical Issue: `--cache-reuse` for Gemma 4 — FIXED UPSTREAM (2026-04-24)

**[Issue #21468](https://github.com/ggml-org/llama.cpp/issues/21468) is CLOSED.** The fix landed as [PR #22288](https://github.com/ggml-org/llama.cpp/pull/22288) ("server: fix swa-full logic"), merged to master on 2026-04-24 as commit `ffdd983fb`. The fetched master snapshot is `13d36cf89`; our local build `85dde8dc4` predates the fix and still exhibits the original behavior.

**What the fix does:** In `tools/server/server-context.cpp`, `server_context::n_swa` is pinned to 0 when `--swa-full` is passed, which short-circuits the SWA-specific checkpoint-restoration gate (`pos_min_thold = pos_next - n_swa`) so cached prefixes are usable. PR author reported warm-request prompt eval dropping from `prompt_n=821, prompt_ms=982` to `prompt_n=5, prompt_ms=71` — ~13x speedup.

**Required flags after rebuild:** `--swa-full --cache-reuse 256`. Not compatible with `--mmproj` (the PR explicitly notes "Cache reuse does not work with mmproj"). Memory cost of `--swa-full` on E4B 16GB is not yet characterized — needs measurement.

**Historical context (before fix):** Gemma 4's shared KV layers / iSWA architecture broke the prefix matching assumptions in `--cache-reuse`. The system prompt (~200 tokens) was re-processed on every `ask_llm()` call. For a medium integration test doing ~15 LLM calls, that's ~3000 wasted prompt tokens. On M1 at ~50 tok/s prompt eval, that's ~60s overhead per test — explaining why `fix_python_syntax` took 520s and `fix_missing_include` took 660s.

**Workaround (Phase 2): OBSOLETE.** `CACHE_WORKAROUND=1` remains off by default in `askme.py`. Once Phase 6 rebuild + test validates the upstream fix, the Phase 2 workaround code can be removed.

## Upstream Gemma 4 Commits

Local HEAD: `85dde8dc4` (master as of 2026-04-16). Fetched master as of 2026-04-24 is `13d36cf89` — 98 commits ahead, see [On master but not in local build](#on-master-but-not-in-local-build) below.

### In local build

| PR | Title | Impact | Since |
|----|-------|--------|-------|
| [#21309](https://github.com/ggml-org/llama.cpp/pull/21309) | Core Gemma 4 support (vision + MoE, no audio) | Foundation | `941146b3f` |
| [#21326](https://github.com/ggml-org/llama.cpp/pull/21326) | Template parser fixes | Correct chat formatting | `941146b3f` |
| [#21343](https://github.com/ggml-org/llama.cpp/pull/21343) | Tokenizer fix (newline grouping, BPE bypass) | Correct tokenization | `941146b3f` |
| [#21418](https://github.com/ggml-org/llama.cpp/pull/21418) | Specialized parser (tool calling, interleaved thinking) | Tool use support | `941146b3f` |
| [#21390](https://github.com/ggml-org/llama.cpp/pull/21390) | `final_logit_softcapping` read for Gemma 4 (was stuck at 30.0f) | Output quality | `941146b3f` |
| [#21428](https://github.com/ggml-org/llama.cpp/pull/21428) | GGUF bool array fix (`sliding_window_pattern` loading) | Correct iSWA behavior | `941146b3f` |
| [#21488](https://github.com/ggml-org/llama.cpp/pull/21488) | Byte token handling in BPE detokenizer for Gemma4 | Tokenizer correctness — fixes edge cases in JSON output | `0d049d6a9` |
| [#21510](https://github.com/ggml-org/llama.cpp/pull/21510) | Fix restore for checkpoints with `pos_min == 0` | **Unblocks Phase 2** — slot save/restore now works correctly | `0d049d6a9` |
| [#21159](https://github.com/ggml-org/llama.cpp/pull/21159) | Optimized `flash_attn_stream_k_fixup` kernel | CUDA only, no M1 Metal impact | `0d049d6a9` |
| [#21513](https://github.com/ggml-org/llama.cpp/pull/21513) | Attention rotation for heterogeneous iSWA | **Enables Phase 3** — Hadamard rotation for Gemma 4's mixed head dims (256 SWA / 512 global). KL divergence 0.947 → 0.746 with quantized KV. | `c5ce4bc22` |
| [#21509](https://github.com/ggml-org/llama.cpp/pull/21509) | Server: fix model params not propagated | Sampling defaults from model metadata now propagate correctly | `c5ce4bc22` |
| [#21492](https://github.com/ggml-org/llama.cpp/pull/21492) | Remove `</s>` EOS token for Gemma 4 | Removes spurious `</s>` from EOG list, adds `<eos>` — **reduces premature generation stops** | `d12cc3d1c` |
| [#21612](https://github.com/ggml-org/llama.cpp/pull/21612) | Per-layer projections in first layer | Reduces graph splits for partial offload (minor perf) | `d12cc3d1c` |
| [#21566](https://github.com/ggml-org/llama.cpp/pull/21566) | CUDA: check for buffer overlap before fusing | Fixes Gemma 4 26B `<unused>` token spam on CUDA — not relevant to Metal | `d12cc3d1c` |
| [#21534](https://github.com/ggml-org/llama.cpp/pull/21534) | Gemma 4 tokenizer tests, fix edge case | Tokenizer correctness — fixes edge case in vocab handling | `85dde8dc4` |
| [#21625](https://github.com/ggml-org/llama.cpp/pull/21625) | Fix multimodal padding token for gemma3n/gemma4 | Correct multimodal padding — no impact on text-only agent use | `85dde8dc4` |
| [#21661](https://github.com/ggml-org/llama.cpp/pull/21661) | Fix ambiguous grammar rule in gemma4 | Parser correctness — resolves ambiguity in grammar-guided output | `85dde8dc4` |
| [#21697](https://github.com/ggml-org/llama.cpp/pull/21697) | Enable reasoning budget sampler for gemma4 | Allows reasoning budget control — relevant if using thinking mode | `85dde8dc4` |
| [#21704](https://github.com/ggml-org/llama.cpp/pull/21704) | Better align to updated official gemma4 template | Template alignment — improves chat/tool-call formatting fidelity | `85dde8dc4` |
| [#21739](https://github.com/ggml-org/llama.cpp/pull/21739) | Make shared-KV tail `attn_k` tensors optional on load | Model loading robustness — shared KV layers load gracefully when tensors absent | `85dde8dc4` |
| [#21760](https://github.com/ggml-org/llama.cpp/pull/21760) | `common/gemma4`: handle parsing edge cases | Fixes 3 template parsing bugs: missing generation prompt before tool calls, trailing `<channel\|>` tokens, duplicate opening `<channel\|>`. Author notes artifacts primarily appear in 26B — E4B impact unverified but safe | `85dde8dc4` |
| [#21421](https://github.com/ggml-org/llama.cpp/pull/21421) | Gemma 4 audio conformer encoder support | Multimodal — enables audio input when projector is loaded | `85dde8dc4` |
| [#21824](https://github.com/ggml-org/llama.cpp/pull/21824) | Use causal attn for gemma 4 audio | Multimodal audio fix | `85dde8dc4` |
| [#21406](https://github.com/ggml-org/llama.cpp/pull/21406) | Custom newline split for Gemma 4 | Replaces `std::regex` newline splitting — fixes stack overflow on large newline-heavy prompts | `85dde8dc4` |
| [#21806](https://github.com/ggml-org/llama.cpp/pull/21806) | mtmd: add gemma 4 test (vision + audio) [no ci] | Test-only multimodal coverage — no runtime impact | `85dde8dc4` |
| [#21971](https://github.com/ggml-org/llama.cpp/pull/21971) | NVFP4 tensors for Gemma4 | CUDA/NVIDIA-only — no Metal impact | `85dde8dc4` |

### On master but not in local build

Pulled in the 98-commit delta between `85dde8dc4` (local) and fetched master snapshot `13d36cf89` (2026-04-24). Only Gemma-4-relevant or cache/SWA-adjacent commits listed.

| PR | Title | Impact | Commit |
|----|-------|--------|--------|
| [#22288](https://github.com/ggml-org/llama.cpp/pull/22288) | server: fix swa-full logic | **Closes [#21468](https://github.com/ggml-org/llama.cpp/issues/21468) — cache-reuse now works with `--swa-full`.** PR reports ~13x faster warm-request prompt eval. Required flags: `--swa-full --cache-reuse 256` (incompatible with `--mmproj`) | `ffdd983fb` |
| [#22114](https://github.com/ggml-org/llama.cpp/pull/22114) | server: refactor "use checkpoint" logic | Internal refactor — adds `common_context_can_seq_rm()` and `enum common_context_seq_rm_type`, moves "use checkpoints" decision to `llama_context` at startup. No user-visible flag change | `de71b5f81` |
| [#22027](https://github.com/ggml-org/llama.cpp/pull/22027) | model: Gemma4 model type detection | Cosmetic only — fixes `?B` display in `llama-bench` for Gemma 4 31B and 26BA4B variants | `fcc750875` |
| [#22129](https://github.com/ggml-org/llama.cpp/pull/22129) | Tensor-parallel: fix delayed AllReduce on Gemma-4 MoE | TP-only (multi-GPU) — no impact on single-GPU Metal path | `fd6ae4ca1` |

### Other notable server commits in rebuild delta

| PR | Title | Impact | Commit |
|----|-------|--------|--------|
| [#21793](https://github.com/ggml-org/llama.cpp/pull/21793) | server: Anthropic API prefix-caching fix | Cache-adjacent but **not used by NanAgent's OpenAI-compatible local path**. Relevant if testing Claude Code or other Anthropic-compatible clients against this server | `c807c6e3b` |
| [#22267](https://github.com/ggml-org/llama.cpp/pull/22267) | server: fix heap-buffer-overflow from negative `n_discard` | Server security/stability fix in the same rebuild delta. Not Gemma-specific, but worth picking up when rebuilding | `c78fb909b` |

### Not yet merged (watch list)

| PR | Title | Status | Why it matters |
|----|-------|--------|----------------|
| [#21749](https://github.com/ggml-org/llama.cpp/pull/21749) | Prompt caching fix for SWA models | **OPEN, effectively superseded by #22288** | Earlier attempt at the same fix. On 2026-04-23 ggerganov asked the submitter to "confirm that #22288 also works." No action needed on our side — #22288 is in master |

### Known Gemma 4 Bugs (upstream)

| Issue | Title | State | Relevance |
|-------|-------|-------|-----------|
| [#21468](https://github.com/ggml-org/llama.cpp/issues/21468) | Cache reuse broken for Gemma 4 | **Closed** via [#22288](https://github.com/ggml-org/llama.cpp/pull/22288), merged 2026-04-24 | Fix requires `--swa-full` companion flag. Merged to master, not yet in local build — see [Phase 6](#phase-6-cache-reuse-unblock--pending-2026-04-24) |
| [#21915](https://github.com/ggml-org/llama.cpp/issues/21915) | Gibberish on 2nd message with kv quantization | Open, **widening** | **Monitor closely** — scope growing: now affects GLM-4.7-Flash on Vulkan (Apr 20), and triggered on *first* message with 130k prompt (Apr 16) — the "2nd message" framing may be a red herring. No E4B/Metal/q4_0 reports yet, but suspected regression near rotation PR #21038 (which our q4_0 default depends on via #21513). Do a sanity second-message check after next rebuild |
| [#21516](https://github.com/ggml-org/llama.cpp/issues/21516) | Generates `<unused>` tokens in infinite loop (Vulkan) | Open | Not applicable (Metal). Separate from CUDA fix #21566 |
| [#21424](https://github.com/ggml-org/llama.cpp/issues/21424) | Very long generation latency (Vulkan/AMD) | Open | Not applicable (Metal) |
| [#21831](https://github.com/ggml-org/llama.cpp/issues/21831) | Server forces full prompt re-processing (SWA/recurrent memory error) | Open | Related to #21468. **Checkpoint workaround confirmed working for Qwen** (Apr 16): `--checkpoint-every-n-tokens 1024 --ctx-checkpoints 256` — context stable up to 262k tokens with no forced reprocessing. Untested on E4B/Metal. Gemma 4 26B actually retains context correctly despite the warning — real breakage is on qwen35moe |
| [#21912](https://github.com/ggml-org/llama.cpp/issues/21912) | Gemma 4 & Qwen 3.5 full prompt reprocessing in agentic workflows | **Closed** (before Apr 16) | Closed as duplicate of #21831/#21468 |
| [#21321](https://github.com/ggml-org/llama.cpp/issues/21321) | Generates `<unused24>` tokens | **Closed/completed** (2026-04) | Resolved upstream |

## Cross-Ecosystem Status (2026-04-20, with 2026-04-24 note)

iSWA/shared-KV cache reuse **was broken across all frameworks** surveyed here as of 2026-04-20. As of 2026-04-24, **llama.cpp is the first of these frameworks to ship a fix** — [#22288](https://github.com/ggml-org/llama.cpp/pull/22288) solves the SWA-full path and closes #21468. The equivalent problem remains open on MLX ([mlx-lm #980](https://github.com/ml-explore/mlx-lm/issues/980), closed-as-wontfix for `RotatingKVCache`) and is not yet resolved on vLLM. On the KV quantization side, llama.cpp has shipped q4_0 + Hadamard rotation (#21513) — a concrete mitigation that several other frameworks don't yet have an equivalent for (subjective comparison, not benchmarked).

### transformers (reference implementation)

| Item | State | Details |
|------|-------|---------|
| [#45312](https://github.com/huggingface/transformers/pull/45312) | Merged 2026-04-09 | **Shared-KV semantics authoritative.** "Weight matrices of shared layers are NEVER used, KV states should ALWAYS be shared, even during training or `use_cache=False`." Previously `use_cache=False` produced garbage logits (issue #45242). **Cross-framework corroboration of llama.cpp #21739** — both ecosystems landed the same fix independently |
| [#45336](https://github.com/huggingface/transformers/pull/45336) | Merged 2026-04-09 | Companion to #45312. Silently skips `k_proj` / `v_proj` / `k_norm` / `v_norm` on load for layers flagged `is_kv_shared_layer`. Functionally equivalent to llama.cpp #21739 ("make shared-KV tail `attn_k` tensors optional on load") |
| [#45489](https://github.com/huggingface/transformers/pull/45489) | Open | Back-port gemma4's explicit `shared_kv_states` attention signature to gemma3n. Confirms the explicit-dict pattern is the reference design — useful if llama.cpp's shared-KV handling ever needs to be re-examined |
| [#45419](https://github.com/huggingface/transformers/issues/45419) | Open | **Gemma 4 tool-call template silently double-escapes when `arguments` arrives as a JSON string vs a dict.** No error raised — downstream callers see malformed JSON. Relevant to llama.cpp's `COMMON_CHAT_FORMAT_PEG_GEMMA4` parser and Ollama #15315 (nested-JSON arg parsing). Worth a quick sanity check on both shapes via llama-server |
| [#45202](https://github.com/huggingface/transformers/pull/45202) | Open | Sets `_supports_flash_attn_2 = False` for Gemma 4 because global layers use `head_dim=512` (FA2 caps at 256). Same root cause as vLLM #38887 and FA #2427 — ecosystems aligned on "FA3 or fallback." Not relevant to Metal |
| [#45468](https://github.com/huggingface/transformers/issues/45468) | Open | `Gemma4AudioRelPositionalEncoding` uses hardcoded `torch.arange(12, -1, -1)` instead of reading `attention_context_left` / `attention_context_right` from config. llama.cpp audio just landed via #21421 / #21824 — **worth verifying the same hardcoding isn't replicated** if/when audio input is tested |
| [#45296](https://github.com/huggingface/transformers/pull/45296) | Open, approved | Adds GGUF loading of Gemma 4 31B dense + 26B-A4B MoE (text-only) in transformers. Inverse direction — HF catching up to llama.cpp's GGUF. No action required, but signals GGUF format stability |
| [#45386](https://github.com/huggingface/transformers/pull/45386) | Merged 2026-04-20 | GGUF early-cast dtype: ~50% peak RAM reduction (118.7 → 59.4 GB), ~42% faster load on Gemma 4 26B q4_k_m. Not Gemma-specific; different memory model from llama.cpp's GGUF loader so no direct action |
| [#45324](https://github.com/huggingface/transformers/pull/45324) | Merged | PLE hardening: per-layer input embeddings now resize properly with vocab expansion. Relates to llama.cpp #21612 (per-layer projections) — likely moot for llama.cpp since GGUFs are frozen at a fixed vocab |
| [#45206](https://github.com/huggingface/transformers/issues/45206) / [#45207](https://github.com/huggingface/transformers/pull/45207) | Closed / Merged | PLE implementation underdocumented → docstrings added to the PLE pipeline. Useful as reference if revisiting llama.cpp's PLE handling |
| [#45200](https://github.com/huggingface/transformers/issues/45200) / [#45222](https://github.com/huggingface/transformers/pull/45222) | Open / Closed | Text-only training requires `mm_token_type_ids` defaulted to zeros. Chat-template/tokenizer behavior — ensure llama.cpp's template path doesn't require the key for text-only use |

**Hints for llama.cpp:**
- **Shared-KV fix is now cross-framework consensus** — transformers #45312/#45336 and llama.cpp #21739 converged on the same semantics (shared KV authoritative, duplicate `k_proj`/`v_proj` tensors ignored) within ~1 week. Strong validation of the current llama.cpp approach
- **Tool-call template fragility** — #45419's double-escape-on-string-args bug is worth a targeted test on llama.cpp's PEG parser, since Ollama #15315 also points at nested-JSON handling. If llama-server mishandles either shape, that's a parser bug upstream from the PEG grammar
- **Audio encoder hardcoded constants** — #45468 flagged hardcoded positional-encoding values in the reference impl. llama.cpp's newly-landed audio path (#21421/#21824) may have inherited the same constants; worth a diff check before relying on audio input
- **FA head_dim=512 is settled** — transformers, vLLM, and FA itself now agree: FA2 cannot do 512, FA3+ required, fall back to SDPA/Triton. Metal path remains the least affected serving option

### vLLM

| Item | State | Details |
|------|-------|---------|
| [#38826](https://github.com/vllm-project/vllm/pull/38826) | Merged | Full Gemma 4 support (MoE, multimodal, reasoning, Gemma4ToolParser, Gemma4ThinkingParser) |
| [#38847](https://github.com/vllm-project/vllm/pull/38847) | Merged | Bugfix: Gemma4ToolParser missing `tools` parameter |
| [#38887](https://github.com/vllm-project/vllm/issues/38887) | Open | E4B extremely slow (~9 tok/s on RTX 4090) — heterogeneous head dims (256 SWA / 512 global) force FlashAttention off, Triton fallback is ~10x slower. Root cause: FA2 caps at head_dim=256. Linked PR [#38891](https://github.com/vllm-project/vllm/pull/38891) adds per-layer attention backend selection (open). New "me too" report Apr 20 |
| [#12655](https://github.com/vllm-project/vllm/pull/12655) | **Closed** (Jun 2025) | Hybrid allocator for full+SWA interleaved models — superseded by [#13296](https://github.com/vllm-project/vllm/pull/13296) |
| [#36684](https://github.com/vllm-project/vllm/pull/36684) | Merged | Fix hybrid attention grouping threshold (1.25 → 1.5) for speculative decoding |
| [#38479](https://github.com/vllm-project/vllm/pull/38479) | Merged | TurboQuant: 2-bit KV cache with WHT rotation — future optimization path |
| [#39392](https://github.com/vllm-project/vllm/issues/39392) | Open | Tool-call-parser produces `<pad>` tokens under concurrent requests |
| [#39043](https://github.com/vllm-project/vllm/issues/39043) | Open | Tool calling problems when used with Claude Code as client |
| [#39133](https://github.com/vllm-project/vllm/issues/39133) | Open, **escalated** | Gemma 4 31B KV cache sizing — confirmed **hard functional blocker**: uniform KV allocation wastes ~83% of memory for 50 sliding-window layers, single requests >~12K tokens hang forever. Evolved from "display bug" to architectural limitation (Apr 18–20) |

### SGLang

| Item | State | Details |
|------|-------|---------|
| [#21952](https://github.com/sgl-project/sglang/pull/21952) | Merged | Full Gemma 4 support. Key fixes in PR history: (1) `layer_scalar` must apply to ALL decoder layers (SWA + global), not just full-attention — llama.cpp already does this correctly (`gemma4-iswa.cpp:226` applies `out_scale` unconditionally in the main layer loop); (2) SWA memory pool index retrieval fix; (3) bidirectional image-token attention |
| [#22277](https://github.com/sgl-project/sglang/issues/22277) | Open (**likely fixed**) | E4B's 18 shared KV layers + fp8 KV cache crash: dtype mismatch (bf16 query × fp8 key). Fix: [PR #22615](https://github.com/sgl-project/sglang/pull/22615) dequantizes fp8 keys before Triton kernel. Confirms shared-KV + quantized-KV is fragile across frameworks — llama.cpp's Hadamard rotation (#21513) likely handles this for q4_0 |

### MLX

| Item | State | Details |
|------|-------|---------|
| [mlx-lm #980](https://github.com/ml-explore/mlx-lm/issues/980) | Closed | `RotatingKVCache` can't support prefix reuse for ANY hybrid-attention model (Gemma 3/4, Qwen 3.5). Multi-turn penalty: ~200s vs ~5s. **Architecturally identical to llama.cpp #21468** |
| [mlx #3393](https://github.com/ml-explore/mlx/issues/3393) | Closed | Quantized Gemma 4 26B MoE produced garbage on base M4 (10 GPU cores) — `gather_mm` Metal kernel dispatch issue. Fixed |
| [mlx-lm #1096](https://github.com/ml-explore/mlx-lm/issues/1096) | Closed | Gemma 4 native tool calls not parsed in OpenAI-compat server. Fixed |
| [mlx-lm #1125](https://github.com/ml-explore/mlx-lm/issues/1125) | Open | Tool call failure with gemma-4-26b-a4b-it-4bit — basic tool calls work on mlx-lm 0.25.2 (Apr 16), but multi-turn tool calling still fails. Fix PR [#1142](https://github.com/ml-explore/mlx-lm/pull/1142) open |
| [mlx-swift #389](https://github.com/ml-explore/mlx-swift/issues/389) | Open (but **may be resolved on main**) | Gemma 4 architecture — collaborator davidkoski pointed to "current main and latest tag" as having support (Apr 16). Issue still open |

**MLX verdict:** Improving but not yet viable for agentic use — basic tool calls now work (mlx-lm 0.25.2), multi-turn still broken. mlx-swift may have Gemma 4 support on main (unconfirmed). The prefix cache reuse problem (#980) confirms hybrid-attention KV reuse is an architecture-level challenge, not a llama.cpp-specific bug.

### FlashAttention

| Item | State | Details |
|------|-------|---------|
| [#2427](https://github.com/Dao-AILab/flash-attention/issues/2427) | Open | FA2 caps at head_dim=256; Gemma 4 global attention uses 512. **Tri Dao confirmed head_dim=512 is FA3-only, not FA2** (Apr 17). Forward pass on Hopper is doable (existing FA3 code portable to FA4), backward on Blackwell harder. Community suggested Split-D technique (FFPA) as workaround for head dims up to 1024+. Practical CUDA path is Triton-based (see vLLM #38891). Not relevant to Metal |

## Ecosystem Watch List (2026-04-20)

Repos to monitor for Gemma 4 fixes, parser bugs, and architecture insights relevant to this agent setup.

### High signal

| Repo | Why |
|------|-----|
| [huggingface/transformers](https://github.com/huggingface/transformers) | Reference implementation for model config, tokenizer/chat template, tool-call formatting, `layer_scalar`, shared-KV semantics, and generation behavior. **Now actively tracked** — see [transformers subsection above](#transformers-reference-implementation) for specific Gemma 4 items |
| [ollama/ollama](https://github.com/ollama/ollama) | Track Gemma 4 parser/tool-call bugs. [#15315](https://github.com/ollama/ollama/issues/15315) still open — a "gemma4 tool call repair" heuristic shipped Apr 7 for Gemma 4's custom `<\|"\|>` delimiters, but issue persists on 0.20.2. **Root cause identified** (Apr 20): nested JSON in tool args breaks the parser. Potentially relevant to llama.cpp's `COMMON_CHAT_FORMAT_PEG_GEMMA4` |
| [EricLBuehler/mistral.rs](https://github.com/EricLBuehler/mistral.rs) | Rust-native engine with day-one Gemma 4 support (text/image/video/audio + tool calling + agentic). Most interesting non-llama.cpp local backend for Mac-friendly architecture ideas |
| [Dao-AILab/flash-attention](https://github.com/Dao-AILab/flash-attention) | Track head_dim=512 / Gemma 4 full-attention support ([#2427](https://github.com/Dao-AILab/flash-attention/issues/2427)). Not directly useful for Metal, but root bottleneck behind vLLM's slow Triton fallback for E4B/31B heterogeneous attention |
| [triton-lang/triton](https://github.com/triton-lang/triton) | Lower-level than vLLM, but relevant because vLLM falls back to Triton attention for Gemma 4. If FlashAttention doesn't solve head_dim=512 quickly, Triton kernels may become the practical GPU serving path |

### Medium signal

| Repo | Why |
|------|-----|
| [sst/opencode](https://github.com/sst/opencode) | Track local OpenAI-compatible provider and tool-call compatibility. The Gemma 4 ecosystem is still finding parser mismatches between model output, server adapter, and agent client |
| [Blaizzy/mlx-vlm](https://github.com/Blaizzy/mlx-vlm) | More relevant than bare mlx for Gemma 4 on Apple Silicon — HF points to mlx-vlm for full multimodal Gemma 4 support and TurboQuant usage. Not first choice for agentic use until tool calling is cleaner |
| [huggingface/transformers.js](https://github.com/huggingface/transformers.js) | Gemma 4 support for browser/WebGPU. Not directly useful for M1 agent, but useful if bugs surface in tokenization, ONNX export, or multimodal formatting |
| [huggingface/trl](https://github.com/huggingface/trl) | Gemma 4 fine-tuning support including multimodal tool-response training. Track only if you care about agent behavior tuning or tool-call datasets |
| [google-deepmind/gemma](https://github.com/google-deepmind/gemma) / [google-gemini/gemma-cookbook](https://github.com/google-gemini/gemma-cookbook) | Official examples, prompt/template clarifications, model behavior notes. Less likely to produce low-level cache fixes |
| [ml-explore/mlx-lm](https://github.com/ml-explore/mlx-lm) / [ml-explore/mlx](https://github.com/ml-explore/mlx) / [ml-explore/mlx-swift](https://github.com/ml-explore/mlx-swift) | Secondary trackers. mlx-lm #1125 improving (basic tool calls work, multi-turn still broken); mlx-swift #389 may have support on main (unconfirmed); mlx-lm #1096 and mlx #3393 closed |

### Low signal (GPU serving only)

| Repo | Why |
|------|-----|
| [NVIDIA/TensorRT-LLM](https://github.com/NVIDIA/TensorRT-LLM) | NVIDIA deployment, FP8/NVFP4, paged KV, MoE serving |
| [huggingface/text-generation-inference](https://github.com/huggingface/text-generation-inference) | Production serving behavior, less useful for Apple Silicon/local agent |
| [InternLM/lmdeploy](https://github.com/InternLM/lmdeploy) | CUDA/TurboMind serving, prefix-cache for routed experts |

### Watch queries

```
repo:huggingface/transformers gemma4 OR "Gemma 4"
repo:ollama/ollama gemma4 tool parser
repo:EricLBuehler/mistral.rs gemma4 OR "Gemma 4"
repo:Dao-AILab/flash-attention "head_dim" "512"
repo:triton-lang/triton gemma4 OR "Gemma 4"
repo:sst/opencode gemma4 OR "Gemma 4" OR "tool_calls"
repo:Blaizzy/mlx-vlm gemma4 OR "Gemma 4"
```

## Implementation Plan

### Phase 1: Update build — COMPLETE (2026-04-07)

Pulled 18 commits from `941146b3f` → `0d049d6a9`. Rebuilt successfully (build `b8695`).

Picks up: Gemma4 byte token fix (#21488), checkpoint restore fix (#21510).

```bash
cd /Users/macmone/code/llama.cpp
git pull origin master
cmake -B build -DLLAMA_CURL=ON
cmake --build build --config Release -j$(sysctl -n hw.ncpu)
```

#### Phase 1 Verification Results (2026-04-07)

| Check | Result |
|-------|--------|
| HEAD includes `4aa962e` (byte token fix) | `0d049d6a9` — yes |
| 59/59 unit tests | Pass (30.6s) |
| Server starts with same flags | Yes — now logs `cache_reuse is not supported by this context` instead of silently ignoring |
| 4/4 `TestServerConfig` | Pass (1.1s) |
| 3/3 easy integration | Pass (2:12) |
| 3/3 medium integration | Pass (39:08) |

**Easy integration (2:12 total):**

| Test | Tasks | Steps | Time |
|------|-------|-------|------|
| create_and_read_file | 2 | 5 (t1:3, t2:1) | ~41s |
| shell_and_write | 2 | 4 (t1:3, t2:1) | ~36s |
| multi_step_build | 3 | 6 (t1:2, t2:3, t3:1) | ~55s |

**Medium integration (39:08 total):**

| Test | Replans | Tasks | Steps | Thinking Retries | Time |
|------|---------|-------|-------|------------------|------|
| fix_python_syntax | 0 | 3 | 10 | 5x (med+high+med+med+med) | ~19min |
| fix_missing_include | 1 | 3+3 | 5+4 | 4x | ~19min |
| create_missing_file | 0 | 2 | 4 | 0 | ~15s |

**Observations vs pre-update baseline:**
- Easy tests: ~2:12 vs ~3:20 — **~35% faster** (likely byte token fix improving JSON output)
- Medium tests: ~39min vs ~20min — **slower** on fix_python_syntax (19min vs 8.7min), similar on fix_missing_include. JSON parse retries dominate — the `</s>` EOS fix (#21492, not yet merged) may help.
- Server now explicitly disables cache_reuse for Gemma 4 instead of silently ignoring
- Checkpoint restore fix (#21510) is confirmed working (`TestServerConfig::test_slot_restore` passes)

### Phase 2: Manual slot save/restore workaround — COUNTERPRODUCTIVE (2026-04-07)

Bypass broken `--cache-reuse` by explicitly saving/restoring KV state around the system prompt. The checkpoint restore fix (#21510) from Phase 1 unblocks this — it fixes restore when `pos_min == 0`, which is exactly our case (system prompt starts at position 0).

**Implementation (in `askme.py`):**

- `CACHE_WORKAROUND` env var — `CACHE_WORKAROUND=1` to enable, off by default
- `_warm_cache()` — called once at start of `run()`: sends minimal request with system prompt to populate KV, then saves slot 0 to disk via `/slots/0?action=save`
- `_restore_cache()` — called inside `ask_llm()` before each `requests.post`: restores saved KV state via `/slots/0?action=restore`
- Non-fatal — save/restore failures log a warning and fall back to normal behavior (no caching)
- Local-only — skipped for OpenRouter backend
- 7 unit tests in `TestCacheWorkaround` (mocked, no server needed)

**Result: COUNTERPRODUCTIVE.** Easy integration: 2:51 with cache vs 2:02 baseline (+49s, 40% slower). Every step after the first is ~5s slower — the restored KV state adds overhead that iSWA can't reconcile efficiently. Direct timing test confirms savings are negligible (0.02s per call) because the server already keeps the last request's KV in the single slot.

**Root cause:** The same iSWA prefix-matching issue that breaks `--cache-reuse` (#21468) also prevents slot restore from saving prompt eval time. Restoring stale KV state for a different prompt forces the server to do expensive comparison/invalidation before processing, which is slower than just evaluating from scratch.

**Status:** Code remains in `askme.py` but `CACHE_WORKAROUND` defaults to off (`0`). Kept for future testing when upstream fixes land.

**Verification:**
- [x] `TestCacheWorkaround` unit tests pass (7/7)
- [x] All 66 unit tests pass (was 59, +7 new)
- [x] Integration timing: `CACHE_WORKAROUND=1` is **40% slower** than baseline (2:51 vs 2:02)
- [x] Direct timing: 0.02s savings per call (negligible, restore overhead dominates)
- [x] `CACHE_WORKAROUND=0` (default) works — cache functions are no-ops

### Phase 3: Quantized KV cache — COMPLETE (2026-04-08)

Pulled 7 commits to `c5ce4bc22` (build `b8702`), picking up iSWA rotation (#21513) and server params fix (#21509). Tested f16 vs q8_0 vs q4_0 KV cache.

**What it does:** Replaces f16 KV cache with q4_0, reducing KV memory by ~4x. The iSWA rotation fix (#21513) ensures Gemma 4's mixed head dims (256 SWA / 512 global) get proper Hadamard rotation, keeping quantized KV quality close to f16 (KL divergence 0.947 → 0.746 with rotation).

**Why it matters on 16GB M1:** f16 KV at 16K context uses ~2GB. With q4_0, that drops to ~0.5GB — freeing headroom for larger context or more breathing room.

**Result: q4_0 is the current recommended default on this M1 16GB setup.** Best result in current local tests — fastest of all three configs, identical quality, ~4x less KV memory.

#### Phase 3 Comparison Results (2026-04-08)

Build `b8702` (`c5ce4bc22`). 132/132 unit tests pass. 3/3 easy integration tests pass on all configs.

| Test | f16 (baseline) | q8_0 | q4_0 |
|------|---------------|------|------|
| create_and_read_file | plan 65s, exec 25s = ~90s | plan 86s, exec 28s = ~114s | plan 45s, exec 22s = ~67s |
| shell_and_write | plan 81s, exec 25s = ~106s | plan 89s, exec 24s = ~113s | plan 81s, exec 14s = ~95s |
| multi_step_build | plan 58s, exec+replan = ~204s | plan 64s, exec+replan = ~202s | plan 65s, exec+replan = ~221s |
| **Total** | **6:40** | **7:09** (+7%) | **6:23** (-4%) |
| Replans | 1 (multi_step) | 1 (multi_step) | 1 (multi_step) |
| Quality | Same duplicate-write pattern | Identical behavior | Identical behavior |

**Observations:**
- q4_0 is 4% faster than f16 — likely because smaller KV cache gives better Metal memory throughput on M1
- q8_0 is 7% slower than f16 — quantize/dequantize overhead without enough memory savings to compensate
- All three configs produce identical behavior patterns (same duplicate-write issue on multi_step_build, same replan count)
- Planner thinking dominates timing (~45-89s per plan call) — exec steps are fast (~3-20s each)
- The duplicate-write loop on multi_step_build is a model behavior issue, not KV cache related

**Benchmark caveats:**
- Results are from a single trial per config — run-to-run variance is expected (planner thinking time alone ranges 45-89s)
- Planner thinking dominates total timing, not pure decode throughput — small KV cache speedups may be masked or amplified by planning variance
- `--cache-reuse` is disabled for Gemma 4, so every request re-evaluates the full prompt from scratch
- The `edit` action was added between older baselines and the current run — historical timing comparisons are not apples-to-apples
- These results apply to this specific M1 16GB / Q4_K_M / 16K context setup and may not generalize to other hardware or quant levels

**Verification checklist:**
- [x] Build includes `4eb19514d` (iSWA rotation fix) — confirmed in `c5ce4bc22`
- [x] 132/132 unit tests pass (30.8s)
- [x] 4/4 `TestServerConfig` pass
- [x] Server starts with `--cache-type-k q8_0 --cache-type-v q8_0`
- [x] 3/3 easy integration tests pass with q8_0 (7:09)
- [x] Server starts with `--cache-type-k q4_0 --cache-type-v q4_0`
- [x] 3/3 easy integration tests pass with q4_0 (6:23)
- [x] Quality comparison: identical step counts, replans, and task completion across all three
- [ ] Expanded context test (`--ctx-size 32768` with q4_0) — deferred, current 16K is sufficient

### Phase 4: EOS fix — COMPLETE (2026-04-08)

Pulled to `d12cc3d1c` picking up EOS fix (#21492), per-layer projections (#21612), CUDA buffer overlap fix (#21566), and other non-Gemma changes.

**What it does:** Removes spurious `</s>` from Gemma 4's end-of-generation token list and adds `<eos>`. This was causing premature generation stops that triggered JSON truncation and thinking retries.

#### Phase 4 Verification Results (2026-04-08)

| Check | Result |
|-------|--------|
| HEAD includes `d9a12c82f` (EOS fix) | `d12cc3d1c` — yes |
| 157/157 unit tests | Pass (30.9s) |
| 3/3 easy integration | Pass (10:02) |

**Easy integration (10:02 total, q4_0 KV):**

| Test | Steps | Replans | Thinking Retries | Time |
|------|-------|---------|------------------|------|
| create_and_read_file | 5 | 1 | 1x (high) | ~286s |
| shell_and_write | 2 | 0 | 0 | ~21s |
| multi_step_build | 5+5 | 1 | 2x (medium) | ~295s |

**Observations vs Phase 3 (q4_0, build b8702):**
- `shell_and_write`: 21s vs 95s — **78% faster**, clean run with no retries
- `create_and_read_file`: 286s vs 67s — slower due to transport timeout during replan (noise, not regression)
- `multi_step_build`: 295s vs 221s — similar, duplicate-write loop persists (model behavior, not EOS related)
- Thinking retries: 3 total vs Phase 1's 9 — **trending down** but single-trial variance is high
- The duplicate-write loop on `multi_step_build` is a model behavior issue unrelated to EOS

**Note:** Single trial — run-to-run variance is significant. The `create_and_read_file` regression is likely a transport timeout outlier, not a real regression from the EOS fix.

### Phase 5: Build refresh — COMPLETE (2026-04-16)

Pulled from `d12cc3d1c` → `85dde8dc4` (~8 days, 12 new Gemma 4 commits). Rebuilt successfully. Picks up: tokenizer edge case (#21534), ambiguous grammar fix (#21661), reasoning budget sampler (#21697), official template alignment (#21704), shared-KV optional tensors (#21739), parsing edge cases (#21760), multimodal padding (#21625), audio support (#21421/#21824), NVFP4 (#21971).

#### Phase 5 Verification Results (2026-04-16)

| Check | Result |
|-------|--------|
| HEAD | `85dde8dc4` — up to date with master |
| 159/159 unit tests | Pass (30.6s) |
| 4/4 `TestServerConfig` | Pass (1.1s) |
| 3/3 easy integration | Pass (1:36) |

**Easy integration (1:36 total, q4_0 KV):**

| Test | Steps | Replans | Thinking Retries | Time |
|------|-------|---------|------------------|------|
| create_and_read_file | 5 (1 dup skip) | 0 | 0 | ~11s |
| shell_and_write | 2 | 0 | 0 | ~6s |
| multi_step_build | 5+2 | 0 | 1x (medium) | ~59s |

**Observations vs Phase 4 (build `d12cc3d1c`, q4_0 KV):**
- **Total: 1:36 vs 10:02 — 84% faster** — biggest single improvement across all phases
- `multi_step_build`: 59s vs 295s — **80% faster**, no replans (was 1), duplicate-write handled cleanly by skip guard
- `shell_and_write`: 6s vs 21s — **71% faster**, already fast, now faster
- `create_and_read_file`: 11s vs 286s — **96% faster** (Phase 4 had a transport timeout outlier, so this is closer to the true baseline)
- Thinking retries: 1 total vs Phase 4's 3, Phase 1's 9 — **consistently trending down**
- Template alignment (#21704), grammar fix (#21661), and parsing edge cases (#21760) likely the key contributors — cleaner LLM output = fewer retries and faster planning
- No duplicate-write loop on `multi_step_build` — the persistent model behavior issue from Phase 3/4 appears resolved

**Verification checklist:**
- [x] `git log --oneline -1` shows `85dde8dc4`
- [x] 159/159 unit tests pass (30.6s)
- [x] Server starts with q4_0 KV flags
- [x] `TestServerConfig` passes (4 tests, 1.1s)
- [x] 3/3 easy integration pass (1:36)
- [x] No gibberish on multi-message sequences (KV-quant #21915 not repro'd)
- **Conclusion:** Significant quality improvement from upstream template/grammar/parser fixes. Easy integration 84% faster than Phase 4. Duplicate-write loop no longer observed. Medium tests deferred — easy tests now fast enough that variance is low.

### Phase 6: Cache-reuse unblock — PENDING (2026-04-24)

[#21468](https://github.com/ggml-org/llama.cpp/issues/21468) was closed upstream by [#22288](https://github.com/ggml-org/llama.cpp/pull/22288) ("server: fix swa-full logic"), merged on 2026-04-24. Fetched master advanced from `85dde8dc4` (our local) to `13d36cf89` — 98 commits. Rebuild required before we can test the fix.

**Planned steps:**

```bash
cd /Users/macmone/code/llama.cpp
git pull origin master                        # 85dde8dc4 → 13d36cf89
cmake -B build -DLLAMA_CURL=ON
cmake --build build --config Release -j$(sysctl -n hw.ncpu)
```

Then launch the server with the current q4_0 flags **plus** `--swa-full --cache-reuse 256`:

```bash
./build/bin/llama-server \
  -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf \
  -ngl 99 --ctx-size 16384 --flash-attn on \
  --cache-type-k q4_0 --cache-type-v q4_0 \
  --swa-full --cache-reuse 256 \
  -np 1 --slot-save-path /tmp/llama-cache \
  --port 8080
```

**Verification targets:**
- [ ] Server starts without `cache_reuse is not supported by this context` warning
- [ ] Second request shows reduced `prompt_n` (PR reports `821 → 5` in a warm-request test)
- [ ] Easy integration vs Phase 5 baseline (1:36) — expect improvement from ~200 tokens × 15 calls ≈ 60s saved on prompt eval per medium test, proportionally smaller on easy tests
- [ ] Sanity second-message output quality (guard against [#21915](https://github.com/ggml-org/llama.cpp/issues/21915) KV-quant gibberish widening)
- [ ] Measure memory cost of `--swa-full` on E4B 16GB — not characterized yet
- [ ] On success, remove Phase 2 `CACHE_WORKAROUND` code from `askme.py` (no longer needed as a fallback)

### Phase 7: Monitor remaining upstream items

| PR/Issue | What to do when fixed |
|----------|----------------------|
| [#21915](https://github.com/ggml-org/llama.cpp/issues/21915) (KV-quant gibberish) | **Widening** — now affects more models (GLM-4.7-Flash) and backends (Vulkan), may trigger on first message with large prompts. Monitor for E4B/Metal repro. If confirmed, temporarily revert KV to f16 |
| [#21831](https://github.com/ggml-org/llama.cpp/issues/21831) (full prompt re-processing) | Still open. Effectively subsumed by #21468's fix for the SWA-full path. **Checkpoint workaround confirmed for Qwen**: `--checkpoint-every-n-tokens 1024 --ctx-checkpoints 256`. Consider testing on E4B/Metal if Phase 6 results are unsatisfactory |
| [SGLang #22277](https://github.com/sgl-project/sglang/issues/22277) (shared KV + quantized KV crash) | Likely fixed via PR #22615 (dequantizes fp8 keys). Confirms shared-KV + quantized-KV is fragile across frameworks |
| [vLLM #38887](https://github.com/vllm-project/vllm/issues/38887) / [FA #2427](https://github.com/Dao-AILab/flash-attention/issues/2427) (head_dim=512 blocker) | FA confirmed FA3-only. Practical path is Triton-based (vLLM #38891). Not relevant to Metal, but tracks when GPU serving catches up |
| [vLLM #39133](https://github.com/vllm-project/vllm/issues/39133) (31B KV sizing) | **Escalated to hard blocker** — uniform KV allocation wastes ~83% memory for SWA layers, requests >12K hang. Architectural limitation, not just display bug |

### Phase 8: Future optimizations

- **TurboQuant KV** — [#21089](https://github.com/ggml-org/llama.cpp/pull/21089) adds 3.5-bit KV cache types (TBQ3_0/TBQ4_0). CPU-only for now, no Metal support.

## Verification Checklist

After Phase 1 (build update) — **ALL PASS (2026-04-07)**:
- [x] `git log --oneline -1` shows `0d049d6a9` (after `4aa962e` Gemma4 byte token fix)
- [x] 59/59 unit tests pass (30.6s)
- [x] Server starts — now explicitly logs `cache_reuse is not supported by this context`
- [x] `TestServerConfig` passes (4 tests, 1.1s)
- [x] 3/3 easy integration tests pass (2:12)
- [x] 3/3 medium integration tests pass (39:08)

After Phase 2 (cache workaround) — **COUNTERPRODUCTIVE (2026-04-07)**:
- [x] `TestCacheWorkaround` unit tests pass (7/7)
- [x] All 66 unit tests pass (was 59, +7 new)
- [x] Integration timing: **40% slower** with cache (2:51 vs 2:02 easy tests)
- [x] Direct timing: 0.02s savings per call — restore overhead dominates
- [x] `CACHE_WORKAROUND=0` works (default — cache functions are no-ops)
- **Conclusion:** iSWA prefix-matching bug affects slot restore too, not just `--cache-reuse`. No workaround possible until upstream fix for #21468.

After Phase 3 (quantized KV cache) — **q4_0 recommended (2026-04-08)**:
- [x] Build `b8702` (`c5ce4bc22`) includes iSWA rotation fix
- [x] 132/132 unit tests pass
- [x] 3/3 easy integration with q8_0: 7:09 (+7% vs f16)
- [x] 3/3 easy integration with q4_0: 6:23 (-4% vs f16, fastest)
- [x] Identical quality across all three KV types
- **Conclusion:** q4_0 KV is the current recommended default — best result in single-trial testing, ~4x less KV memory. Pending multi-trial validation.

After Phase 4 (EOS fix) — **COMPLETE (2026-04-08)**:
- [x] `git log --oneline -1` shows `d12cc3d1c` (includes `d9a12c82f` EOS fix)
- [x] 157/157 unit tests pass (30.9s)
- [x] 3/3 easy integration pass (10:02, q4_0 KV)
- [x] `shell_and_write` 78% faster (21s vs 95s) — clean run, no retries
- [x] Thinking retries trending down (3 vs Phase 1's 9) — high variance, needs more trials
- **Conclusion:** EOS fix merged and working. Duplicate-write loop persists (model behavior). Medium tests deferred — need multi-trial runs to separate signal from noise.

After Phase 5 (build refresh) — **ALL PASS (2026-04-16)**:
- [x] `git log --oneline -1` shows `85dde8dc4` (12 new Gemma 4 commits from `d12cc3d1c`)
- [x] 159/159 unit tests pass (30.6s)
- [x] Server starts with q4_0 KV flags, no errors
- [x] `TestServerConfig` passes (4 tests, 1.1s)
- [x] 3/3 easy integration pass (1:36 — **84% faster** than Phase 4's 10:02)
- [x] No KV-quant gibberish (#21915 not repro'd on E4B/Metal/q4_0)
- [x] Duplicate-write loop on `multi_step_build` no longer observed
- **Conclusion:** Template alignment (#21704), grammar fix (#21661), and parsing edge cases (#21760) dramatically improve LLM output quality. Fastest easy integration ever. No regressions.

Phase 6 (cache-reuse unblock) — **COMPLETE (2026-04-25)**:
- [x] Rebuilt on master `a702f395` (includes `ffdd983fb` #22288)
- [x] Server starts with `--swa-full --cache-reuse 256` + q4_0 KV, no `cache_reuse is not supported` warning
- [x] Cache reuse active — warm requests show LCP similarity matching (sim_best up to 1.000)
- [x] Easy integration: 2/3 pass; test 1 failed due to cold-start decode penalty + runaway generation
- [x] Phase 5 rerun: 3/3 pass in 8:07 (baseline 1:36 was a lucky run)
- [x] **Deterministic multi-turn benchmark: 3 trials × 7 requests, both configs identical cache behavior, Phase 6 4.5% faster, no decode penalty**
- [ ] Second-message sanity check for KV-quant gibberish (#21915) — deferred, not blocking
- [ ] Memory cost of `--swa-full` measured on 16GB M1 — deferred, not blocking
- **Verdict:** Phase 6 (`--swa-full --cache-reuse 256`) promoted to default. No downside vs Phase 5. Earlier integration regressions were model output variance. See [caching_analysis.md](caching_analysis.md).

## 16GB M1 Lessons Learned

- **Gemma 4 E4B is the sweet spot** — MoE with 4B active params, ~5.0GB Q4_K_M, full Metal GPU
- **No forced thinking mode** — unlike Qwen 3.5, Gemma 4 doesn't leak `<think>` tags into responses
- **35B MoE OOMs on Metal GPU** regardless of context size or flash attention
- **Use `-np 1` for agents** — default auto-detects 4 slots, splitting context 4 ways
- **Use `--cache-type-k q4_0 --cache-type-v q4_0`** — current recommended default. Best result in single-trial testing: 4% faster than f16 on Metal M1, identical quality, ~4x less KV memory (~0.5GB vs ~2GB at 16K context). q8_0 is 7% slower in single-trial testing — not recommended.
- **`--swa-full --cache-reuse 256` is the default** — fixed upstream via [#22288](https://github.com/ggml-org/llama.cpp/pull/22288) (requires build `a702f395`+). Deterministic benchmark: no decode penalty, 4.5% faster prompt eval. Not compatible with `--mmproj`. Manual slot save/restore (`CACHE_WORKAROUND=1`) is obsolete — see Phase 2.
- **`--flash-attn on`** works correctly on Metal for Gemma 4 iSWA

## References

- [Issue #21468 — Cache reuse broken for Gemma 4 (closed via #22288)](https://github.com/ggml-org/llama.cpp/issues/21468)
- [PR #22288 — server: fix swa-full logic (merged, closes #21468)](https://github.com/ggml-org/llama.cpp/pull/22288)
- [PR #21793 — server: Anthropic API prefix-caching fix](https://github.com/ggml-org/llama.cpp/pull/21793)
- [PR #22267 — server heap-buffer-overflow fix](https://github.com/ggml-org/llama.cpp/pull/22267)
- [PR #22114 — server: refactor "use checkpoint" logic](https://github.com/ggml-org/llama.cpp/pull/22114)
- [PR #22027 — Gemma4 model type detection (cosmetic)](https://github.com/ggml-org/llama.cpp/pull/22027)
- [PR #22129 — Tensor-parallel fix for Gemma-4 MoE](https://github.com/ggml-org/llama.cpp/pull/22129)
- [PR #21749 — Prompt caching fix for SWA models (open, superseded by #22288)](https://github.com/ggml-org/llama.cpp/pull/21749)
- [PR #21760 — common/gemma4 parsing edge cases (merged)](https://github.com/ggml-org/llama.cpp/pull/21760)
- [Issue #21915 — Gibberish on 2nd message with kv quantization](https://github.com/ggml-org/llama.cpp/issues/21915)
- [PR #21488 — Byte token handling for Gemma4](https://github.com/ggml-org/llama.cpp/pull/21488)
- [PR #21510 — Fix restore for checkpoints with pos_min == 0](https://github.com/ggml-org/llama.cpp/pull/21510)
- [PR #21492 — Remove </s> EOS for Gemma4](https://github.com/ggml-org/llama.cpp/pull/21492)
- [PR #21513 — Attention rotation for heterogeneous iSWA](https://github.com/ggml-org/llama.cpp/pull/21513) (merged, was #21518)
- [PR #21509 — Server: fix model params not propagated](https://github.com/ggml-org/llama.cpp/pull/21509)
- [PR #21612 — Per-layer projections in first layer](https://github.com/ggml-org/llama.cpp/pull/21612)
- [PR #21566 — CUDA: check for buffer overlap before fusing](https://github.com/ggml-org/llama.cpp/pull/21566)
- [PR #21038 — Hadamard rotation for better KV quantization](https://github.com/ggml-org/llama.cpp/pull/21038)
- [PR #21089 — TurboQuant CPU KV cache types](https://github.com/ggml-org/llama.cpp/pull/21089)
- [Discussion #20572 — Persistent KV cache tutorial](https://github.com/ggml-org/llama.cpp/discussions/20572)
- [llama-server slots API](https://github.com/ggml-org/llama.cpp/blob/master/examples/server/README.md)
- [gemma4-iswa.cpp source](https://github.com/ggml-org/llama.cpp/blob/master/src/models/gemma4-iswa.cpp)
- [PR #21406 — Custom newline split for Gemma 4](https://github.com/ggml-org/llama.cpp/pull/21406)
- [Issue #21831 — Server forces full prompt re-processing (SWA/recurrent)](https://github.com/ggml-org/llama.cpp/issues/21831)
- [Issue #21912 — Full prompt reprocessing in agentic workflows](https://github.com/ggml-org/llama.cpp/issues/21912)
- [vLLM #38826 — Full Gemma 4 support](https://github.com/vllm-project/vllm/pull/38826)
- [vLLM #38887 — E4B slow on RTX 4090 (Triton fallback)](https://github.com/vllm-project/vllm/issues/38887)
- [vLLM #12655 — Hybrid allocator for iSWA models (closed, superseded by #13296)](https://github.com/vllm-project/vllm/pull/12655)
- [vLLM #38891 — Per-layer attention backend selection (open)](https://github.com/vllm-project/vllm/pull/38891)
- [vLLM #39133 — Gemma 4 31B KV cache sizing blocker](https://github.com/vllm-project/vllm/issues/39133)
- [SGLang #22615 — Fix fp8 KV dequantization for shared KV layers](https://github.com/sgl-project/sglang/pull/22615)
- [mlx-lm #1142 — Fix Gemma 4 tool call parser (open)](https://github.com/ml-explore/mlx-lm/pull/1142)
- [SGLang #21952 — Gemma 4 support (includes layer_scalar + SWA KV fixes)](https://github.com/sgl-project/sglang/pull/21952)
- [SGLang #22277 — Shared KV + fp8 KV cache crash](https://github.com/sgl-project/sglang/issues/22277)
- [mlx-lm #980 — Prefix cache reuse broken for hybrid-attention models](https://github.com/ml-explore/mlx-lm/issues/980)
- [FlashAttention #2427 — head_dim=512 support needed for Gemma 4](https://github.com/Dao-AILab/flash-attention/issues/2427)
- [Ollama #15315 — gemma4:e4b tool parsing errors (open)](https://github.com/ollama/ollama/issues/15315)
- [mistral.rs — Rust-native engine with Gemma 4 support](https://github.com/EricLBuehler/mistral.rs)
- [OpenCode — local OpenAI-compatible agent client](https://github.com/sst/opencode)
- [mlx-vlm — Gemma 4 multimodal on Apple Silicon](https://github.com/Blaizzy/mlx-vlm)
