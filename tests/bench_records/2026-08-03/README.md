# Bench records — 2026-08-03 (E23 QAT baseline + E09 12B trial)

Raw `bench_harness.py` artifacts (per-trial `AGENT_RUN_LOG` JSONL, failing-trial
pytest output, `summary.json`) for the two model trials analyzed in
[PERFORMANCE.md](../../../docs/PERFORMANCE.md). Retained per the evaluation and
evidence discipline in AGENTS.md; the dated PERFORMANCE.md entries are the
narrative, these are the records they must stay arithmetically consistent with.

## Shared configuration

- Hardware: Mac M1 16 GB. llama.cpp build **9618** (`c34b92235`), Metal.
- Server flags (both trials): `-ngl 99 --ctx-size 16384 --flash-attn on
  --cache-type-k q4_0 --cache-type-v q4_0 --swa-full --cache-reuse 256
  --reasoning off -np 1 --port 8080`. MTP off.
- Harness: `tests/bench_harness.py`, 3 trials per test, local backend.

## Cells

| Dir | Model | AskMe revision | Invocation |
|---|---|---|---|
| `qat_easy` / `qat_medium` / `qat_hard` | `google/gemma-4-E4B-it-qat-q4_0-gguf` (`gemma-4-E4B_q4_0-it.gguf`, 5.15 GB) | `187a2c1` (pre-rebase `agent/docs-reorg`) | `python3 tests/bench_harness.py --suite {easy,medium,hard} --trials 3` |
| `12b_easy` / `12b_medium` | `google/gemma-4-12B-it-qat-q4_0-gguf` (`gemma-4-12b-it-qat-q4_0.gguf`, 6.98 GB) | `b86e534`-based branch (post-rebase main: issue #68 semantics, revision-4 pressure) | `ASKME_RUN_LIVE_LLM_TESTS=1 uv run --locked python tests/bench_harness.py --suite {easy,medium} --trials 3` |

## Known limitations (recorded, not corrected post hoc)

1. **No matched Q4_K_M control.** The E23 comparison baselines are the Apr/May
   PERFORMANCE.md entries: build `a702f395`, older AskMe revision, older test
   assertions. The 1.6–39× deltas are therefore **stack-level** (weights +
   build + `--reasoning off` + scaffold), not a weights-isolated causal claim.
   A matched control (Q4_K_M on build 9618, same flags/revision) was not run —
   the legacy weights were deleted for disk space before the need was flagged;
   re-download (`ggml-org/gemma-4-E4B-it-GGUF`) if isolation ever matters.
2. **Cross-revision model comparison.** The 12B cell ran on a newer AskMe
   revision than the QAT cells (rebase landed between them). The 3.6–35×
   deltas and retry counts far exceed plausible scaffold-delta effects, but a
   same-revision E4B rerun is the clean version of this comparison.
3. **12B run stopped externally** during the medium suite (manual interrupt):
   `12b_easy` is complete (9 trials + summary), `12b_medium` holds 4 complete
   trial JSONLs and one incomplete, no `summary.json`. The pre-registered
   decision rule (within ~2× E4B QAT wall time, both E23 failure classes
   cleared) had already failed at the easy tier.
4. The decision rules for both trials were stated in conversation/PR before
   the runs, not in a registered protocol file; this README is the
   after-the-fact registration. Future model trials should register here first.
