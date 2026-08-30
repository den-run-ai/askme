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
| `analyze.py` | **Authoritative classifier.** Re-derives outcomes from the retained records: `uv run --locked --no-dev python tests/bench_records/2026-08-29-peg-probe/analyze.py` |
| `peg_probe_results_run1.jsonl` | One record per trial (32), including usage, finish reason, payload size, and the full request body on any failure |
| `peg_probe_results_run2.jsonl` | Same, run 2 (32) |
| `peg_probe_run1.txt` / `peg_probe_run2.txt` | Console transcripts |

Transcripts use `.txt` because `*.log` is gitignored repository-wide.

### Read `analyze.py`, not the raw `ok` flag

The probe's inline `ok` field — and therefore the `FAIL` lines in the
transcripts — means only "one tool call came back and its arguments parsed as
JSON". That is too coarse, because it gives the same verdict to two unrelated
events:

- **`parser_failure`** — malformed output while the model stopped on its own
  (`finish_reason != "length"`). This is the #25986 class and the only outcome
  that bears on it.
- **`budget_truncation`** — arguments are an unterminated prefix purely because
  generation hit `max_tokens` (`finish_reason == "length"`). Expected and
  handled: AskMe classifies this as a truncated write and retries with the
  payload-sized budget (`STEP_WRITE_TOKENS`).

`peg_probe.py` is deliberately left exactly as it was when it produced run 2, so
that record set is provably the output of the committed script. The correction
lives in `analyze.py` instead, which is sound because every record already
carries `finish_reason` — the classification is fully re-derivable from the raw
data, and no re-run was needed.

## Two runs

Run 1 was produced by this script before it was reformatted to satisfy the
repository's Ruff configuration (`tests/` is linted in CI). The fix was
cosmetic only — `dict(...)` rewritten as literals, an unused `re` import
removed, and quote-style normalization — and **every prompt string is
byte-identical between the two versions**, so the two runs are the same
experiment. Run 1 is retained rather than discarded because it is a valid
32-trial dataset; run 2 exists so that the committed script is provably the one
that produced at least one committed record set. Combined they give n=64, which
tightens the 95% upper bound on the per-call parser-failure rate from roughly 9%
to roughly 4.6%.

Run 2 overlapped a local `pytest tests/ -q` run on the same machine. That
inflated its wall times (`B_long_write_512` ~37s in run 1 vs ~50–100s in run 2)
and is why per-trial timings from run 2 must not be compared against run 1 or
used as performance data. It does not affect parse outcomes or token counts,
which are what this probe measures.

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
- 8 trials per arm, 4 arms, 2 runs — **n=64**.

## Result

**0/64 parser failures. #25986 did not reproduce on this cell**, including at
~3.5x the payload size the `legacy-e4b-m1-16k-v1` write cap permits. Zero HTTP
5xx and zero `unparsed peg-gemma4` lines in the server log across both runs.

| Arm | n | clean | budget_truncation | parser_failure | hit cap | content |
|---|---|---|---|---|---|---|
| `A_short_args` | 16 | 16 | 0 | **0** | 0/16 | short args |
| `B_long_write_512` | 16 | 14 | 2 | **0** | 4/16 | 1426–1644 chars, 56–68 lines |
| `B_long_write_2048` | 16 | 16 | 0 | **0** | 0/16 | 3904–5507 chars, 119–163 lines |
| `C_delimiter_payload` | 16 | 16 | 0 | **0** | 0/16 | 272–344 chars |
| **Total** | **64** | **62** | **2** | **0** | 4/64 | — |

The two non-clean trials are both `budget_truncation`: `finish_reason=length`
with `completion_tokens=512` exactly, cut mid-payload so the `content` string
never closes. Not a grammar defect — the expected truncation path.

**Secondary finding, independent of #25986: the legacy 512-token write cap binds
routinely.** 4 of 16 `B_long_write_512` trials (1/8 run 1, 3/8 run 2) hit the cap
on an ordinary "implement a small module" task, and 2 of those landed mid-string.
At 2048 tokens, 0 of 16 hit the cap while producing payloads up to 5,507
characters. This is a real constraint on `legacy-e4b-m1-16k-v1` for write-shaped
work and is unrelated to any parser issue.

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
5. **"Parser-clean" never means "artifact complete."** 4 of 64 trials hit
   `finish_reason=length`; 2 still produced closeable JSON (counted `clean`
   because the wire format round-tripped) and 2 did not (`budget_truncation`).
   AskMe would correctly treat all four as truncated writes. The distinction this
   probe measures is whether the *grammar* held, not whether the file was whole.
6. **The `ok` flag in the raw records is not the verdict** — see "Read
   `analyze.py`, not the raw `ok` flag" above. Run 2's transcript shows two
   `FAIL` lines that are budget truncation, not parser failures.
7. Records were produced from the working tree with uncommitted documentation
   edits present. No `askme.py`/`actions.py` source was modified for or during
   either run — the probe only imports `_ACTION_TOOLS` and `SYSTEM_STEP`.
8. Run 2's wall times are contaminated by a concurrent local test run and must
   not be read as performance data (see "Two runs").
9. Re-run this alongside E27: master carries post-b9618 PEG hardening (#24329,
   #24869, #26780) that a b9618 result cannot speak to.
