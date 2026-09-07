# Gemma 4 E4B — Legacy Capability-Profile Reference

Mac M1 16GB. Last updated 2026-08-04. This guide describes the explicit
`legacy-e4b-m1-16k-v1` AskMe profile; it is not the generic runtime default.

**Current local build:** `c34b92235` (build 9618, master as of 2026-06-13; pulled + rebuilt 2026-06-12). Includes all Phase 6 fixes plus, from the `a702f395 → c34b92235` delta: **Gemma 4 MTP speculative decoding** ([#23398](https://github.com/ggml-org/llama.cpp/pull/23398) for 31B/26B-A4B, [#24282](https://github.com/ggml-org/llama.cpp/pull/24282) for E2B/E4B assistants), the **state-save fix [#23468](https://github.com/ggml-org/llama.cpp/pull/23468) that makes Gemma 4 cache reuse fully reliable** (build ~9484), SWA checkpoint improvements (#23981, #24110, #24411), structured-output parser fix (#22302), fast Walsh-Hadamard KV rotation (#22631), Gemma4ForCausalLM conversion (#23682), and the 12B Unified conversion fix (#24118). PERFORMANCE.md local baselines predate this binary — see the build caveat there.
**Fetched master snapshot as of 2026-08-03:** `ee0445c99` — 632 commits ahead of local. Contains a large grammar/PEG overhaul (#24869, #24839, #24835, #24653, #24624, #24329), server prompt-cache work (#24176 checkpoints at every user message, #25070 prompt-cache RAM limit, #25649 state-ownership refactor), and a reasoning-leak template fix (#24674). A rebuild is worth an isolated A/B, but gate on [#26470](https://github.com/ggml-org/llama.cpp/issues/26470) (Metal Gemma-family decode regression, reported on M5/macOS 27 — M1 impact unknown). Do not replace stable b9618 without a side-by-side.
**Phase 1 (build update): COMPLETE** — all tests pass. See [verification results](#phase-1-verification-results-2026-04-07) below.
**Phase 3 (quantized KV cache): COMPLETE** — q4_0 KV is the current recommended default (-4% vs f16 in single-trial test, ~4x less KV memory). See [Phase 3 results](#phase-3-quantized-kv-cache--complete-2026-04-08).
**Phase 4 (EOS fix): COMPLETE** — #21492 merged, rebuilt, 157/157 unit tests pass, 3/3 easy integration pass (10:02). See [Phase 4 results](#phase-4-eos-fix--complete-2026-04-08).
**Phase 5 (build refresh): COMPLETE** — pulled 12 new Gemma 4 commits to `85dde8dc4`, rebuilt, 159/159 unit tests pass, 3/3 easy integration pass (1:36 — fastest ever). See [Phase 5 results](#phase-5-build-refresh--complete-2026-04-16).
**Phase 6 (cache-reuse unblock): COMPLETE** — #21468 closed upstream via #22288 (merged to master). Rebuilt on master `a702f395`. Deterministic multi-turn benchmark (3 trials × 7 requests) shows no downside vs Phase 5: same cache behavior, same decode speed, 4.5% faster prompt eval. **Phase 6 is the new default.** See [caching_analysis.md](caching_analysis.md) and [PERFORMANCE.md](PERFORMANCE.md#phase-6-caching-ab--2026-04-25-build-a702f395-master).
**Build refresh (2026-06-12, undocumented at the time):** pulled + rebuilt to `c34b92235` (b9618), picking up MTP support and the #23468 cache fix. At that date no local benchmark had run on the binary; E23 established the first baseline on 2026-08-03.
**Status refresh (2026-08-03):** upstream issue dispositions, the MTP smoke test, the `--reasoning off` requirement, and Google's 2026-07-15/16 weight refresh are covered in the dated sections below.

## Model

| Model | File | Size | Architecture | GPU | Status |
|-------|------|------|-------------|-----|--------|
| **Gemma 4 E4B** Q4_K_M | `models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf` | ~5.0 GB | Dense PLE, 4.5B effective / 8B incl. embeddings, iSWA | Full Metal | Pre-refresh (2026-04-06) — legacy |
| **Gemma 4 E4B QAT Q4_0** | `models/gemma4-e4b-qat/gemma-4-E4B_q4_0-it.gguf` | 5.15 GB | Same, quantization-aware-trained, post-refresh weights | Full Metal | **Primary** (promoted 2026-08-03) — E23 full bench: hard 9/9 at −38–66% wall, `fix_missing_include` 39× faster, thinking retries near-zero. Two known quirks (done-emission loops; content drift on rewrites) recorded in ARCHITECTURE.md Current Constraints. See [PERFORMANCE.md E23 entry](PERFORMANCE.md#e23-qat-baseline--2026-08-03-local-build-9618-official-e4b-qat-q4_0) |

- **iSWA** (Interleaved Sliding Window Attention) — 3 sliding-window layers + 1 global attention layer, repeating
- Per-Layer Embeddings (PLE) make the file ~8B-sized despite 4.5B effective params
- llama.cpp has a dedicated Gemma 4 chat/tool-call parser (`COMMON_CHAT_FORMAT_PEG_GEMMA4`)
- No thinking mode by default (unlike Qwen 3.5) — responses are direct, no `<think>` overhead. If enabled via the reasoning-budget sampler (#21697), note that `<think>...</think>` is emitted before JSON content and breaks structured parsers on the non-streaming path — see [unsloth #5044](https://github.com/unslothai/unsloth/issues/5044)
- Q8_0 (8 GB) is viable if you want higher quality and delete Qwen 3.5 9B later
- **Weight refresh (2026-07-15/16):** Google re-published all Gemma 4 checkpoints under the same names with tool-calling JSON reliability, truncated-response, and chat-template fixes. The installed Q4_K_M is dated 2026-04-06 — pre-refresh. Re-pull before the next benchmark; prefer the official **QAT Q4_0** ([google/gemma-4-E4B-it-qat-q4_0-gguf](https://huggingface.co/google/gemma-4-E4B-it-qat-q4_0-gguf), ~5.15 GB, quantization-aware-trained rather than post-quantized). The stale template is also what triggers server-side thinking auto-detection — see `--reasoning off` below.
- **Gemma 4 12B Unified** (released 2026-06-03; dense, encoder-free multimodal, 256K ctx; official [QAT Q4_0 GGUF](https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf) ~6.98 GB) — the 2026-08-03 agent-loop trial was negative under the E4B-fitted contract: 3.6–35× slower than E4B QAT with more exhaustion (see PERFORMANCE.md E09 12B entry). That result is contract-conditional; a newly registered, explicitly pinned capability-profile run is required before a model-wide verdict. It remains a higher-capacity dense candidate that fits in 16 GB. No small-MoE Gemma 4 exists — **26B-A4B remains the family's only MoE** (Q4 ≥13.6 GB before KV cache) and stays off the 16 GB shortlist; no verified acceptable llama.cpp run on 16 GB Apple Silicon exists.

### Download

Primary (official QAT Q4_0, post-refresh weights; repo is ungated, CLI is `hf` in current huggingface_hub):

```bash
hf download google/gemma-4-E4B-it-qat-q4_0-gguf \
  gemma-4-E4B_q4_0-it.gguf \
  --local-dir /Users/macmone/code/llama.cpp/models/gemma4-e4b-qat
```

Legacy (pre-refresh Q4_K_M):

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

### Agentic use (AskMe)

```bash
cd /Users/macmone/code/llama.cpp
./build/bin/llama-server \
  -m models/gemma4-e4b-qat/gemma-4-E4B_q4_0-it.gguf \
  -ngl 99 --ctx-size 16384 --flash-attn on \
  --cache-type-k q4_0 --cache-type-v q4_0 \
  --swa-full --cache-reuse 256 \
  --reasoning off \
  -np 1 \
  --alias gemma-4-e4b \
  --port 8080
# Legacy pre-refresh model: -m models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf
```

Pin the matching AskMe identities when invoking the agent or benchmark
harness:

```bash
export LLM_MODEL=gemma-4-e4b
export LLM_CAPABILITY_PROFILE=legacy-e4b-m1-16k-v1
```

Here “legacy” names the original constrained harness contract; it does not
mean the promoted QAT weights are stale. Server `--reasoning off` disables
llama-server template auto-detection, while AskMe's own reasoning policy stays
`gated` unless `AGENT_REASONING_POLICY` is changed separately.

#### Stable flags

| Flag | Purpose | Notes |
|------|---------|-------|
| `-ngl 99` | Full GPU offload | |
| `--ctx-size 16384` | Full context for single slot | |
| `--flash-attn on` | Memory-efficient attention | |
| `--cache-type-k q4_0 --cache-type-v q4_0` | Quantized KV cache (~4x less memory) | Current recommended default on M1 16GB. See [Phase 3 results](#phase-3-quantized-kv-cache--complete-2026-04-08). |
| `-np 1` | Single slot (avoids 4-way context split on M1) | |
| `--swa-full --cache-reuse 256` | Full SWA attention + cross-slot cache reuse | Requires build `a702f395`+ ([#22288](https://github.com/ggml-org/llama.cpp/pull/22288)); fully reliable since [#23468](https://github.com/ggml-org/llama.cpp/pull/23468) (build ~9484, in local b9618). Deterministic benchmark: no decode penalty, 4.5% faster prompt eval vs without. Not compatible with `--mmproj` — if using `-hf`, also pass `--no-mmproj` for text-only use, since a loaded projector disables cache reuse. |
| `--reasoning off` | Disable thinking | **Permanently required (verified 2026-08-03).** Default is `auto`, which detects the chat template as thinking-capable — **including the post-refresh QAT GGUF** (probe: planner-style prompt emitted 371 chars of `reasoning_content` on a 192-token budget). Not a stale-GGUF artifact; keep the flag on every Gemma 4 GGUF. |
| `--port 8080` | Server port | |

#### Known-broken flags (historical — resolved as of b9618)

| Flag | Issue | Status |
|------|-------|--------|
| `--cache-reuse 256` (alone, no `--swa-full`) | KV prefix reuse — was broken for Gemma 4 iSWA ([#21468](https://github.com/ggml-org/llama.cpp/issues/21468)) | **Resolved**: use with `--swa-full` (stable table above). Fixed by [#22288](https://github.com/ggml-org/llama.cpp/pull/22288), made fully reliable by [#23468](https://github.com/ggml-org/llama.cpp/pull/23468) — both in b9618 |
| `CACHE_WORKAROUND=1` | Manual slot save/restore bypass — tested in Phase 2, **counterproductive** (40% slower) | **Removed** from `askme.py` (issue #38) after `--swa-full --cache-reuse 256` became the stable default; Phase 2 measurements preserved as history |

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

Diagnostic use only: these calls require launching the server with
`--slot-save-path`, which the recommended command no longer sets. The AskMe
runtime no longer issues them — the Phase 2 `CACHE_WORKAROUND` experiment that
did was measured counterproductive and removed (issue #38).

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

## MTP Speculative Decoding — Supported, Not Yet a Win on M1 (2026-08-03)

Native Gemma 4 MTP ("assistant" drafter) support is in build 9618: [#23398](https://github.com/ggml-org/llama.cpp/pull/23398) (merged 2026-06-07; 31B + 26B-A4B; flags `--spec-type draft-mtp --spec-draft-n-max N`) and [#24282](https://github.com/ggml-org/llama.cpp/pull/24282) (merged 2026-06-08; E2B/E4B assistants with their extra `masked_embedding.*` tensors). Google publishes official drafter weights; the E4B drafter is downloaded at `models/gemma4-e4b/gemma-4-e4b-assistant.gguf` (98.7 MB, 2026-06-12). Server-only for now — `llama-bench` and `llama-speculative` cannot load assistant models.

Three-prompt, single-pass smoke test on b9618 (4K ctx, q4_0 KV — not an AskMe evaluation):

| Configuration | Decode | vs baseline |
|---|---|---|
| No MTP | 13.61 tok/s | baseline |
| MTP `--spec-draft-n-max 1` | 11.84 tok/s | −13.0% |
| MTP `--spec-draft-n-max 3` | 13.24 tok/s | −2.7% |

**Verdict: keep MTP off for AskMe.** The loss has a clear upstream explanation: draft verification runs at exactly the batch sizes (4–16) where Metal's mul_mat path is unoptimized — [#25250](https://github.com/ggml-org/llama.cpp/issues/25250) documents ~2x headroom there and names speculative decoding as the affected workload — and there is no adaptive draft length yet ([#24768](https://github.com/ggml-org/llama.cpp/issues/24768), feature request for Google's heuristic n-max). Also watch [#25072](https://github.com/ggml-org/llama.cpp/issues/25072) — tool-call format corruption reported specifically under MTP. Revisit (EXPERIMENTS.md E24) when #25250 or #24768 lands. For calibration: Ollama's advertised ~90% MTP gain on Apple Silicon was measured on a 12B NVFP4 model on an M5 Max — not transferable to M1.

## Critical Issue: `--cache-reuse` for Gemma 4 — FIXED UPSTREAM (2026-04-24)

**[Issue #21468](https://github.com/ggml-org/llama.cpp/issues/21468) is CLOSED.** The fix landed as [PR #22288](https://github.com/ggml-org/llama.cpp/pull/22288) ("server: fix swa-full logic"), merged to master on 2026-04-24 as commit `ffdd983fb`. (Historical note: at the time of writing, local build `85dde8dc4` predated the fix; Phase 6 rebuilt to `a702f395` and validated it, and [#23468](https://github.com/ggml-org/llama.cpp/pull/23468) later made reuse fully reliable — current build b9618 has both.)

**What the fix does:** In `tools/server/server-context.cpp`, `server_context::n_swa` is pinned to 0 when `--swa-full` is passed, which short-circuits the SWA-specific checkpoint-restoration gate (`pos_min_thold = pos_next - n_swa`) so cached prefixes are usable. PR author reported warm-request prompt eval dropping from `prompt_n=821, prompt_ms=982` to `prompt_n=5, prompt_ms=71` — ~13x speedup.

**Required flags after rebuild:** `--swa-full --cache-reuse 256`. Not compatible with `--mmproj` (the PR explicitly notes "Cache reuse does not work with mmproj"). Memory cost of `--swa-full` on E4B 16GB is not yet characterized — needs measurement.

**Historical context (before fix):** Gemma 4's shared KV layers / iSWA architecture broke the prefix matching assumptions in `--cache-reuse`. The system prompt (~200 tokens) was re-processed on every `ask_llm()` call. For a medium integration test doing ~15 LLM calls, that's ~3000 wasted prompt tokens. On M1 at ~50 tok/s prompt eval, that's ~60s overhead per test — explaining why `fix_python_syntax` took 520s and `fix_missing_include` took 660s.

**Workaround (Phase 2): OBSOLETE — removed.** The `CACHE_WORKAROUND` slot save/restore code was removed from `askme.py` (issue #38) after `--swa-full --cache-reuse 256` was promoted to the stable default. The Phase 2 measurements below are preserved as historical evidence; recover the code from Git history if a future server rebuild needs the experiment re-run.

## Upstream Gemma 4 Commits

Historical snapshot (2026-04). Local HEAD was `85dde8dc4` at the time; current local HEAD is `c34b92235` (b9618, 2026-06-13), so everything in both tables below — including the "not in local build" table — **is now in the local build**. Key post-snapshot additions (MTP, #23468, checkpoint fixes) are listed in the header at the top of this doc.

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

### On master but not in local build (historical — all in local build since Phase 6 / b9618)

Pulled in the 98-commit delta between `85dde8dc4` (then-local) and fetched master snapshot `13d36cf89` (2026-04-24). Only Gemma-4-relevant or cache/SWA-adjacent commits listed.

| PR | Title | Impact | Commit |
|----|-------|--------|--------|
| [#22288](https://github.com/ggml-org/llama.cpp/pull/22288) | server: fix swa-full logic | **Closes [#21468](https://github.com/ggml-org/llama.cpp/issues/21468) — cache-reuse now works with `--swa-full`.** PR reports ~13x faster warm-request prompt eval. Required flags: `--swa-full --cache-reuse 256` (incompatible with `--mmproj`) | `ffdd983fb` |
| [#22114](https://github.com/ggml-org/llama.cpp/pull/22114) | server: refactor "use checkpoint" logic | Internal refactor — adds `common_context_can_seq_rm()` and `enum common_context_seq_rm_type`, moves "use checkpoints" decision to `llama_context` at startup. No user-visible flag change | `de71b5f81` |
| [#22027](https://github.com/ggml-org/llama.cpp/pull/22027) | model: Gemma4 model type detection | Cosmetic only — fixes `?B` display in `llama-bench` for Gemma 4 31B and 26BA4B variants | `fcc750875` |
| [#22129](https://github.com/ggml-org/llama.cpp/pull/22129) | Tensor-parallel: fix delayed AllReduce on Gemma-4 MoE | TP-only (multi-GPU) — no impact on single-GPU Metal path | `fd6ae4ca1` |

### Other notable server commits in rebuild delta

| PR | Title | Impact | Commit |
|----|-------|--------|--------|
| [#21793](https://github.com/ggml-org/llama.cpp/pull/21793) | server: Anthropic API prefix-caching fix | Cache-adjacent but **not used by AskMe's OpenAI-compatible local path**. Relevant if testing Claude Code or other Anthropic-compatible clients against this server | `c807c6e3b` |
| [#22267](https://github.com/ggml-org/llama.cpp/pull/22267) | server: fix heap-buffer-overflow from negative `n_discard` | Server security/stability fix in the same rebuild delta. Not Gemma-specific, but worth picking up when rebuilding | `c78fb909b` |

### Not yet merged (watch list)

| PR | Title | Status | Why it matters |
|----|-------|--------|----------------|
| [#21749](https://github.com/ggml-org/llama.cpp/pull/21749) | Prompt caching fix for SWA models | **Closed** (superseded by #22288) | Earlier attempt at the same fix. Closed after #22288 merged. No action needed |

### Known Gemma 4 Bugs (upstream)

| Issue | Title | State | Relevance |
|-------|-------|-------|-----------|
| [#21468](https://github.com/ggml-org/llama.cpp/issues/21468) | Cache reuse broken for Gemma 4 | **Closed** via [#22288](https://github.com/ggml-org/llama.cpp/pull/22288), merged 2026-04-24 | In local build since `a702f395` (Phase 6); reliability completed by [#23468](https://github.com/ggml-org/llama.cpp/pull/23468) (2026-06-02, build ~9484, in b9618) |
| [#21915](https://github.com/ggml-org/llama.cpp/issues/21915) | Gibberish on 2nd message with kv quantization | **Stale-closed 2026-07-08, never fixed or diagnosed** | Never repro'd on E4B/Metal/q4_0. Demoted from active monitoring to an occasional second-message sanity check; keep matching q4_0/q4_0 KV. (Related: the MTP PR #23398 found and fixed a missing Hadamard rotation for quantized KV draft states — consistent with the suspected #21038-adjacent regression, but never connected back to this issue.) |
| [#21516](https://github.com/ggml-org/llama.cpp/issues/21516) | Generates `<unused>` tokens in infinite loop (Vulkan) | **Stale-closed 2026-07-31** after widening to CUDA/ROCm/MoltenVK | CUDA-side quietly resolved ~b9608 (no fix PR identified); per-backend successors open: #26239 (HIP), #26206 (Intel Arc), #26417 (Vulkan BF16 MoE). Not applicable (Metal) |
| [#21424](https://github.com/ggml-org/llama.cpp/issues/21424) | Very long generation latency (Vulkan/AMD) | Open | Not applicable (Metal) |
| [#21831](https://github.com/ggml-org/llama.cpp/issues/21831) | Server forces full prompt re-processing (SWA/recurrent memory error) | Open — **now Qwen-only** | **Gemma 4 side fixed** by [#23468](https://github.com/ggml-org/llama.cpp/pull/23468) (2026-06-02); users confirmed Gemma 4 solid from build 9484 through 9518+. Residual breakage is Qwen 3.5/3.6 MoE checkpoint restoration, tracked in [#22746](https://github.com/ggml-org/llama.cpp/issues/22746) (open, active 2026-08) — relevant if local Qwen is ever attempted |
| [#21912](https://github.com/ggml-org/llama.cpp/issues/21912) | Gemma 4 & Qwen 3.5 full prompt reprocessing in agentic workflows | **Closed** (before Apr 16) | Closed as duplicate of #21831/#21468 |
| [#22337](https://github.com/ggml-org/llama.cpp/issues/22337) | E4B/E2B fails as speculative draft model with 31B target | **Stale-closed 2026-07-28, never fixed** | Classic `-md` drafting across PLE/non-PLE architectures still broken; superseded by native MTP (#23398/#24282) |
| [#21321](https://github.com/ggml-org/llama.cpp/issues/21321) | Generates `<unused24>` tokens | **Closed/completed** (2026-04) | Resolved upstream |
| [#22396](https://github.com/ggml-org/llama.cpp/issues/22396) | `--json-schema` broken for Gemma 4 | **Stale-closed 2026-07-05, unfixed** (re-regression reported 2026-05-20 on builds 9244/9253) | AskMe's client-side JSON repair (E03) remains the right approach. Retest after rebuilding past the grammar/PEG overhaul (#24869 et al., not in b9618) |
| [#26470](https://github.com/ggml-org/llama.cpp/issues/26470) | Metal Gemma-family decode regression ~13% (b9730 → b10219) | Open (2026-08-02) | Single reporter on **M5 / 24 GB / macOS 27**; Qwen unaffected; M1 impact unknown. A/B any rebuild in isolation — do not replace stable b9618 untested |
| [#25986](https://github.com/ggml-org/llama.cpp/issues/25986) | PEG template intermittently unparseable on long multi-line tool-call string args | Open (2026-07-22) | Third open PEG parser issue (with #25072, #24658). Relevant only if AskMe adopts native tool calls (deferred issue #15) |
| [#25250](https://github.com/ggml-org/llama.cpp/issues/25250) | Metal small-batch mul_mat compute-bound at bs 4–16 (~2x headroom) | Open, active 2026-08-02 | Why MTP currently loses on M1 — first gate for retrying MTP (E24) |
| [#24768](https://github.com/ggml-org/llama.cpp/issues/24768) | MTP heuristic/adaptive n-max (feature request) | Open (2026-06-18) | Second MTP gate — removes manual draft-length tuning |

## Cross-Ecosystem Status (2026-04-20, updated 2026-08-03)

iSWA/shared-KV cache reuse **was broken across all frameworks** surveyed here as of 2026-04-20. As of 2026-04-24, **llama.cpp is the first of these frameworks to ship a fix** — [#22288](https://github.com/ggml-org/llama.cpp/pull/22288) solves the SWA-full path and closes #21468. The equivalent problem remains open on MLX ([mlx-lm #980](https://github.com/ml-explore/mlx-lm/issues/980), closed-as-wontfix for `RotatingKVCache`) and is not yet resolved on vLLM. On the KV quantization side, llama.cpp has shipped q4_0 + Hadamard rotation (#21513) — a concrete mitigation that several other frameworks don't yet have an equivalent for (subjective comparison, not benchmarked).

**2026-08-03 delta.** llama.cpp is no longer alone on hybrid-attention prefix caching: **mlx-lm** shipped it (PRs #999/#1006; [#980](https://github.com/ml-explore/mlx-lm/issues/980) closed-*completed* 2026-04-14 — the "wontfix" note above is outdated), and **SGLang** has an SWA-aware radix cache in-tree, hardened through July (#32379, #32373). **vLLM** still lacks hybrid prefix-cache *reuse* (coordinator fix #50457 open, int8-KV-on-hybrid corruption issues open) but resolved the 31B KV-sizing hard blocker ([#39133](https://github.com/vllm-project/vllm/issues/39133) closed 2026-07-25 via #40946); E4B head_dim-512 slowness ([#38887](https://github.com/vllm-project/vllm/issues/38887)) remains open while SGLang merged the per-layer-backend equivalent (#32625, 2026-07-30). **Ollama** fixed the MoE empty-response bug (#15428, 0.21.2+), shipped QAT tags, the 12B, and Apple Silicon MLX-MTP (v0.31.x) — but e4b tool parsing ([#15315](https://github.com/ollama/ollama/issues/15315)) is *still open* after four rounds of fixes. **transformers**: #45419 (tool-call double-escape) still open; GGUF-loading PR #45296 still unmerged. **mistral.rs**: #2051/#2058 untouched, new concurrency bugs added. Tool calling is clean nowhere; llama.cpp remains the reference implementation other repos' threads diff against. The tables below retain 2026-05-03 detail — rows contradicted by this paragraph are superseded.

### transformers (reference implementation)

| Item | State | Details |
|------|-------|---------|
| [#45312](https://github.com/huggingface/transformers/pull/45312) | Merged 2026-04-09 | **Shared-KV semantics authoritative.** "Weight matrices of shared layers are NEVER used, KV states should ALWAYS be shared, even during training or `use_cache=False`." Previously `use_cache=False` produced garbage logits (issue #45242). **Cross-framework corroboration of llama.cpp #21739** — both ecosystems landed the same fix independently |
| [#45336](https://github.com/huggingface/transformers/pull/45336) | Merged 2026-04-09 | Companion to #45312. Silently skips `k_proj` / `v_proj` / `k_norm` / `v_norm` on load for layers flagged `is_kv_shared_layer`. Functionally equivalent to llama.cpp #21739 ("make shared-KV tail `attn_k` tensors optional on load") |
| [#45489](https://github.com/huggingface/transformers/pull/45489) | Open | Back-port gemma4's explicit `shared_kv_states` attention signature to gemma3n. Confirms the explicit-dict pattern is the reference design — useful if llama.cpp's shared-KV handling ever needs to be re-examined |
| [#45419](https://github.com/huggingface/transformers/issues/45419) | Open | **Gemma 4 tool-call template silently double-escapes when `arguments` arrives as a JSON string vs a dict.** No error raised — downstream callers see malformed JSON. Relevant to llama.cpp's `COMMON_CHAT_FORMAT_PEG_GEMMA4` parser and Ollama #15315 (nested-JSON arg parsing). Worth a quick sanity check on both shapes via llama-server |
| [#45202](https://github.com/huggingface/transformers/pull/45202) | Merged 2026-04-23 | Sets `_supports_flash_attn_2 = False` for Gemma 4 because global layers use `head_dim=512` (FA2 caps at 256). Same root cause as vLLM #38887 and FA #2427 — ecosystems aligned on "FA3 or fallback." Not relevant to Metal |
| [#45606](https://github.com/huggingface/transformers/pull/45606) | Merged 2026-04-27 | Fixes Gemma 4 audio relative-position hardcoding by inferring from config. Supersedes the concern in #45468 below — audio support should remain treated as unvalidated in llama.cpp until tested |
| [#45636](https://github.com/huggingface/transformers/issues/45636) | Open (Apr 26) | Proposes `sdpa_memeff` backend for Gemma 4 head dimensions that fast backends don't cover. CUDA/PyTorch-side, not Metal — confirms heterogeneous head-dim attention problem remains ecosystem-wide |
| [#45468](https://github.com/huggingface/transformers/issues/45468) | Open | `Gemma4AudioRelPositionalEncoding` uses hardcoded `torch.arange(12, -1, -1)` instead of reading `attention_context_left` / `attention_context_right` from config. llama.cpp audio just landed via #21421 / #21824 — **fix in progress via #45606 above** |
| [#45296](https://github.com/huggingface/transformers/pull/45296) | Open, approved | Adds GGUF loading of Gemma 4 31B dense + 26B-A4B MoE (text-only) in transformers. Inverse direction — HF catching up to llama.cpp's GGUF. No action required, but signals GGUF format stability |
| [#45386](https://github.com/huggingface/transformers/pull/45386) | Merged 2026-04-20 | GGUF early-cast dtype: ~50% peak RAM reduction (118.7 → 59.4 GB), ~42% faster load on Gemma 4 26B q4_k_m. Not Gemma-specific; different memory model from llama.cpp's GGUF loader so no direct action |
| [#45324](https://github.com/huggingface/transformers/pull/45324) | Merged | PLE hardening: per-layer input embeddings now resize properly with vocab expansion. Relates to llama.cpp #21612 (per-layer projections) — likely moot for llama.cpp since GGUFs are frozen at a fixed vocab |
| [#45206](https://github.com/huggingface/transformers/issues/45206) / [#45207](https://github.com/huggingface/transformers/pull/45207) | Closed / Merged | PLE implementation underdocumented → docstrings added to the PLE pipeline. Useful as reference if revisiting llama.cpp's PLE handling |
| [#45200](https://github.com/huggingface/transformers/issues/45200) / [#45222](https://github.com/huggingface/transformers/pull/45222) | Open / Closed | Text-only training requires `mm_token_type_ids` defaulted to zeros. Chat-template/tokenizer behavior — ensure llama.cpp's template path doesn't require the key for text-only use |

**Hints for llama.cpp:**
- **Shared-KV fix is now cross-framework consensus** — transformers #45312/#45336 and llama.cpp #21739 converged on the same semantics (shared KV authoritative, duplicate `k_proj`/`v_proj` tensors ignored) within ~1 week. Strong validation of the current llama.cpp approach
- **Tool-call template fragility** — #45419's double-escape-on-string-args bug is worth a targeted test on llama.cpp's PEG parser, since Ollama #15315 also points at nested-JSON handling. If llama-server mishandles either shape, that's a parser bug upstream from the PEG grammar
- **Audio encoder hardcoded constants** — #45468 flagged hardcoded positional-encoding values in the reference impl; **#45606 merged the fix (Apr 27)**. llama.cpp's audio path (#21421/#21824) may still have the original hardcoded constants; worth a diff check before relying on audio input
- **FA head_dim=512 is settled** — transformers (#45202 merged Apr 23), vLLM, and FA itself now agree: FA2 cannot do 512, FA3+ required, fall back to SDPA/Triton. Metal path remains the least affected serving option

### vLLM

| Item | State | Details |
|------|-------|---------|
| [#38826](https://github.com/vllm-project/vllm/pull/38826) | Merged | Full Gemma 4 support (MoE, multimodal, reasoning, Gemma4ToolParser, Gemma4ThinkingParser) |
| [#38847](https://github.com/vllm-project/vllm/pull/38847) | Merged | Bugfix: Gemma4ToolParser missing `tools` parameter |
| [#38887](https://github.com/vllm-project/vllm/issues/38887) | Open | E4B extremely slow (~9 tok/s on RTX 4090) — heterogeneous head dims (256 SWA / 512 global) force FlashAttention off, Triton fallback is ~10x slower. Root cause: FA2 caps at head_dim=256. Linked PR [#38891](https://github.com/vllm-project/vllm/pull/38891) adds per-layer attention backend selection (open). New "me too" report Apr 20 |
| [#12655](https://github.com/vllm-project/vllm/pull/12655) | **Closed** (Jun 2025) | Hybrid allocator for full+SWA interleaved models — superseded by [#13296](https://github.com/vllm-project/vllm/pull/13296) |
| [#36684](https://github.com/vllm-project/vllm/pull/36684) | Merged | Fix hybrid attention grouping threshold (1.25 → 1.5) for speculative decoding |
| [#38479](https://github.com/vllm-project/vllm/pull/38479) | Merged | TurboQuant: 2-bit KV cache with WHT rotation — future optimization path |
| [#40911](https://github.com/vllm-project/vllm/issues/40911) | Open (Apr 26) | Gemma 4 reasoning-to-tool-call parser leakage — partial `<\|` text prevents tool-call recognition. Reinforces need for local structured-output sanity tests around thinking/tool transitions |
| [#39866](https://github.com/vllm-project/vllm/pull/39866) | Open | Caps SWA admission budget at `sliding_window + chunk_size` for hybrid models. Validates that SWA memory accounting remains a hot area across frameworks |
| [#39468](https://github.com/vllm-project/vllm/issues/39468) | Open (Apr 10) | Tool call JSON `<\|"\|>` delimiter escaping not properly decoded in vLLM 0.19.0. Related to Gemma 4's custom delimiters |
| [#39392](https://github.com/vllm-project/vllm/issues/39392) | Open | Tool-call-parser produces `<pad>` tokens under concurrent requests. **Root cause**: `Gemma4ToolParser` has thread-unsafe shared mutable state |
| [#39043](https://github.com/vllm-project/vllm/issues/39043) | Open | Tool calling problems when used with Claude Code as client |
| [#40290](https://github.com/vllm-project/vllm/issues/40290) | Open (Apr 2026) | Vision tower fp16 overflow produces `<pad>` tokens. New issue |
| [#40080](https://github.com/vllm-project/vllm/issues/40080) | Open (Apr 2026) | Infinite repetition with JSON schema on Gemma 4. New issue |
| [#40677](https://github.com/vllm-project/vllm/issues/40677) | Open (Apr 2026) | Blackwell SM120 unsupported head_size — heterogeneous head dims hit new hardware |
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
| [mlx-lm #1205](https://github.com/ml-explore/mlx-lm/pull/1205) | Open (Apr 26) | Drops `k_proj`/`v_proj`/`k_norm` tensors for shared-KV layers during sanitize. **Independent confirmation of shared-KV semantics** — functionally equivalent to llama.cpp #21739 and transformers #45336 |
| [mlx-lm #1125](https://github.com/ml-explore/mlx-lm/issues/1125) | Open | Tool call failure with gemma-4-26b-a4b-it-4bit — basic tool calls work on mlx-lm 0.25.2 (Apr 16), but multi-turn tool calling still fails. Fix PR [#1142](https://github.com/ml-explore/mlx-lm/pull/1142) open |
| [mlx-lm PR #1160](https://github.com/ml-explore/mlx-lm/pull/1160) | Open (Apr 16) | Adds reasoning → tool state machine transition. Complementary to #1142 |
| [mlx-swift #389](https://github.com/ml-explore/mlx-swift/issues/389) | Open (but **may be resolved on main**) | Gemma 4 architecture — collaborator davidkoski pointed to "current main and latest tag" as having support (Apr 16). Issue still open |

**MLX verdict:** Improving but not yet viable for agentic use — basic tool calls now work (mlx-lm 0.25.2), multi-turn still broken (PRs #1142 + #1160 pending). mlx-swift may have Gemma 4 support on main (unconfirmed). The prefix cache reuse problem (#980) confirms hybrid-attention KV reuse is an architecture-level challenge, not a llama.cpp-specific bug.

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
| [ollama/ollama](https://github.com/ollama/ollama) | Track Gemma 4 parser/tool-call bugs. [#15315](https://github.com/ollama/ollama/issues/15315) — **fixed in Ollama v0.20.2** via [PR #15306](https://github.com/ollama/ollama/pull/15306) (reworked tool calling to match reference impl). **New issues (Apr 2026):** [#15428](https://github.com/ollama/ollama/issues/15428) MoE returns empty on long system prompts (>500 chars) — **potentially relevant to E4B agentic use with large system prompts**; [#15350](https://github.com/ollama/ollama/issues/15350) Flash Attention hangs on dense 31B with >3-4K token prompts. Architecture recognition fixed in v0.20.5+ |
| [EricLBuehler/mistral.rs](https://github.com/EricLBuehler/mistral.rs) | Rust-native engine with day-one Gemma 4 support (text/image/video/audio + tool calling + agentic). Known issues: [#2051](https://github.com/EricLBuehler/mistral.rs/issues/2051) NaN logits (26B) + infinite hang (E4B) on specific prompts; [#2058](https://github.com/EricLBuehler/mistral.rs/issues/2058) E2B inference hangs via MultimodalModelBuilder. Both open, no fix |
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

**Status (historical):** The code stayed in `askme.py`, disabled by default, until it was removed in issue #38 once `--swa-full --cache-reuse 256` became the stable default. Recover it from Git history to re-test after a server rebuild.

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

### Phase 6: Cache-reuse unblock — COMPLETE (2026-04-25)

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
  -np 1 \
  --port 8080
```

**Verification targets:**
- [ ] Server starts without `cache_reuse is not supported by this context` warning
- [ ] Second request shows reduced `prompt_n` (PR reports `821 → 5` in a warm-request test)
- [ ] Easy integration vs Phase 5 baseline (1:36) — expect improvement from ~200 tokens × 15 calls ≈ 60s saved on prompt eval per medium test, proportionally smaller on easy tests
- [ ] Sanity second-message output quality (guard against [#21915](https://github.com/ggml-org/llama.cpp/issues/21915) KV-quant gibberish widening)
- [ ] Measure memory cost of `--swa-full` on E4B 16GB — not characterized yet
- [x] On success, remove Phase 2 `CACHE_WORKAROUND` code from `askme.py` (no longer needed as a fallback) — removed early in issue #38; the promoted `--swa-full --cache-reuse 256` default made the fallback moot

### Phase 7: Monitor remaining upstream items (updated 2026-08-03)

| PR/Issue | Status / what to do |
|----------|----------------------|
| [#26470](https://github.com/ggml-org/llama.cpp/issues/26470) (Metal Gemma decode regression) | **Rebuild gate.** ~13% E4B decode loss b9730→b10219, reported on M5/macOS 27 only. Before adopting any newer build, A/B against b9618 in isolation |
| [#25250](https://github.com/ggml-org/llama.cpp/issues/25250) (Metal small-batch mul_mat) / [#24768](https://github.com/ggml-org/llama.cpp/issues/24768) (adaptive n-max) | **MTP gates.** When either lands, rerun the MTP A/B (E24) — current smoke test shows −13%/−2.7% |
| [#25986](https://github.com/ggml-org/llama.cpp/issues/25986) / [#25072](https://github.com/ggml-org/llama.cpp/issues/25072) (PEG tool-call parsing) | Gates for ever adopting native tool calls in AskMe (deferred issue #15). #25072 is specifically MTP-adjacent |
| [#21915](https://github.com/ggml-org/llama.cpp/issues/21915) (KV-quant gibberish) | **Stale-closed 2026-07-08, never fixed.** Demoted to occasional second-message sanity check on rebuilds. If ever repro'd on E4B/Metal, revert KV to f16 |
| [#21831](https://github.com/ggml-org/llama.cpp/issues/21831) (full prompt re-processing) | **Gemma 4 side fixed** (#23468, in b9618). Residual is Qwen MoE ([#22746](https://github.com/ggml-org/llama.cpp/issues/22746)) — only matters if local Qwen is attempted. Checkpoint workaround no longer needed for Gemma |
| [PR #25352](https://github.com/ggml-org/llama.cpp/pull/25352) (E8 lattice 2-bit KV, CUDA + Metal) | The successor to TurboQuant for KV compression — first candidate with Metal support. If merged, evaluate vs q4_0 KV |
| [SGLang #22277](https://github.com/sgl-project/sglang/issues/22277) (shared KV + quantized KV crash) | **Auto-closed stale 2026-07-06 without a fix** — PR #22615 still open with failing CI. Treat shared-KV + fp8-KV as still broken on SGLang |
| [vLLM #38887](https://github.com/vllm-project/vllm/issues/38887) / [FA #2427](https://github.com/Dao-AILab/flash-attention/issues/2427) (head_dim=512 blocker) | Still open; vLLM fix PR #38891 unmerged. SGLang merged its per-layer-backend equivalent (#32625, 2026-07-30). Not relevant to Metal |
| [vLLM #39133](https://github.com/vllm-project/vllm/issues/39133) (31B KV sizing) | **Resolved 2026-07-25** via #40946 (+ #45040); the hard blocker is gone. Hybrid prefix-cache *reuse* on vLLM still broken (#50457 open) |

### Phase 8: Future optimizations

- **TurboQuant KV** — [#21089](https://github.com/ggml-org/llama.cpp/pull/21089) (3.5-bit TBQ3_0/TBQ4_0 KV types) was **closed unmerged 2026-06-02** (no demonstrated KLD win over existing quants; Hadamard rotation already captures much of the benefit). The live successor is [#25352](https://github.com/ggml-org/llama.cpp/pull/25352) — E8 lattice 2-bit KV cache, **CUDA + Metal**, open as of 2026-08-03.
- **Model swaps** — official E4B QAT Q4_0 (~5.15 GB, post-refresh weights) and Gemma 4 12B Unified QAT (~6.98 GB) are the E09/E23 candidates; see EXPERIMENTS.md.

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

- **Gemma 4 E4B is the sweet spot** — dense PLE with 4.5B effective params (8B including embeddings), ~5.0GB Q4_K_M, full Metal GPU
- **No forced thinking mode** — unlike Qwen 3.5, Gemma 4 doesn't leak `<think>` tags into responses
- **35B MoE OOMs on Metal GPU** regardless of context size or flash attention
- **Use `-np 1` for agents** — default auto-detects 4 slots, splitting context 4 ways
- **Use `--cache-type-k q4_0 --cache-type-v q4_0`** — current recommended default. Best result in single-trial testing: 4% faster than f16 on Metal M1, identical quality, ~4x less KV memory (~0.5GB vs ~2GB at 16K context). q8_0 is 7% slower in single-trial testing — not recommended.
- **`--swa-full --cache-reuse 256` is the default** — fixed upstream via [#22288](https://github.com/ggml-org/llama.cpp/pull/22288) (requires build `a702f395`+). Deterministic benchmark: no decode penalty, 4.5% faster prompt eval. Not compatible with `--mmproj`. Manual slot save/restore (`CACHE_WORKAROUND=1`) was measured counterproductive and removed in issue #38 — see Phase 2.
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
