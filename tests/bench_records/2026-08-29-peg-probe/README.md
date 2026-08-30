# PEG tool-call parser probe records — 2026-08-29

Diagnostic probe of llama.cpp's `peg-gemma4` tool-call parser against the
tool-call shapes AskMe's tools-only executor emits. Summary table, findings, and
the full limits list live in `docs/PERFORMANCE.md` under "PEG Tool-Call Parser
Probe — 2026-08-29".

**This is not a benchmark and not an AskMe evaluation.** No task-outcome,
reliability, or model-quality claim is made or supported by these records. The
probe calls `/v1/chat/completions` directly and only checks whether one
structured tool call with JSON-parseable arguments comes back.

## Contents

| File | What it is |
|---|---|
| `peg_probe.py` | The probe. Re-runnable: `uv run --locked --no-dev python tests/bench_records/2026-08-29-peg-probe/peg_probe.py [n] [arm]` |
| `peg_probe_results_run1.jsonl` | One record per trial (32), including usage, finish reason, payload size, and the full request body on any failure |
| `peg_probe_run1.txt` | Console transcript of run 1 |

A second 32-trial replication (`peg_probe_results_run2.jsonl` /
`peg_probe_run2.txt`) is added in a follow-up commit on this branch — see
"Two runs" below. Transcripts use `.txt` because `*.log` is gitignored
repository-wide.

## Two runs

Run 1 was produced by this script before it was reformatted to satisfy the
repository's Ruff configuration (`tests/` is linted in CI). The fix was
cosmetic only — `dict(...)` rewritten as literals, an unused `re` import
removed, and quote-style normalization — and **every prompt string is
byte-identical between the two versions**, so the two runs are the same
experiment. Run 1 is retained rather than discarded because it is a valid
32-trial dataset; run 2 exists so that the committed script is provably the one
that produced at least one committed record set. Combined they give n=64, which
tightens the 95% upper bound on the per-call failure rate from roughly 9% to
roughly 4.6%.

## Cell

- Server: `b9618-c34b92235`, `models/gemma4-e4b-qat/gemma-4-E4B_q4_0-it.gguf`,
  `gemma4-setup.md` flags including `--reasoning off`, `-np 1`, 16384 ctx,
  q4_0 KV, `--swa-full --cache-reuse 256`. MTP off.
- Request shape faithful to AskMe: real `_ACTION_TOOLS` (all 8 definitions
  imported from `askme`), real `SYSTEM_STEP`, `tool_choice: "auto"`,
  `temperature 0.1`, and a history carrying a completed prior assistant
  `tool_call` plus its `role:tool` result — the conversation depth
  [#25986](https://github.com/ggml-org/llama.cpp/issues/25986)'s reporter says
  is required, and which a plain curl does not have.
- 8 trials per arm, 4 arms.

## Result

32/32 trials returned exactly one structured tool call with JSON-parseable
arguments. Zero HTTP 5xx, zero `unparsed peg-gemma4` lines in the server log for
the duration. #25986 **did not reproduce on this cell**, including at ~3x the
payload size the `legacy-e4b-m1-16k-v1` write cap permits.

## Provenance and limitations

1. **The dominant upstream failure mode was never provoked.** #25986's primary
   defect is that trailing output after a complete tool call voids the entire
   parse. That needs the model to over-generate past `<tool_call|>`, which it did
   not do at `temperature 0.1`. These records bound the observed rate under
   AskMe's real settings; they do not show the parser tolerates trailing output.
2. **Arm `C_delimiter_payload` failed to force its condition.** The model
   declined to emit the literal `<|"|>` delimiter into file content in 8/8 trials
   despite a direct instruction (`content_has_delim` is `false` in every record).
   The "no escape mechanism for the string delimiter" defect is therefore
   **untested, not cleared**.
3. **Single cell.** One model/quant (E4B QAT Q4_0), one build (b9618), one
   conversation depth, temp 0.1, no MTP, no `--jinja` variation. The upstream
   failing cell was 26B-A4B UD-Q4_K_XL and the report notes MTP amplified it.
4. **Not an outcome-bearing registered protocol.** 8 trials/arm is a smoke bound,
   not a reliability estimate. No decision rule was preregistered.
5. `B_long_write_512` trial 8 hit `finish_reason=length`. It is counted clean
   because the wire format round-tripped; AskMe would correctly classify that
   payload as `incomplete_write`. "Parser-clean" never means "artifact complete".
6. Records were produced from the working tree with uncommitted documentation
   edits present. No `askme.py`/`actions.py` source was modified for or during
   the run — the probe only imports `_ACTION_TOOLS` and `SYSTEM_STEP`.
7. Re-run this alongside E27: master carries post-b9618 PEG hardening (#24329,
   #24869, #26780) that a b9618 result cannot speak to.
