# PEG tool-call parser probe records — 2026-08-29

Diagnostic probe of llama.cpp's `peg-gemma4` tool-call parser using AskMe's
tool definitions and a **synthetic prior tool conversation**. This is not an
AskMe evaluation, benchmark, or reliability estimate.

## Contents

| File | What it is |
|---|---|
| `peg_probe.py` | Frozen producer for the retained run-2 records; its inline claims and `ok` flag have the limitations below |
| `analyze.py` | Authoritative bounded classifier: `uv run --locked --no-dev python tests/bench_records/2026-08-29-peg-probe/analyze.py` |
| `peg_probe_results_run1.jsonl` / `peg_probe_results_run2.jsonl` | 32 records each, including call count, first tool name, parse status, argument keys, usage, and finish reason |
| `peg_probe_run1.txt` / `peg_probe_run2.txt` | Frozen console transcripts; `.txt` because `*.log` is gitignored |
| [`../../peg_probe_v2.py`](../../peg_probe_v2.py) | Separately versioned, offline-tested replacement collector core; no historical results were produced with it |

## Correction — 2026-09-07

The producer and raw records remain byte-identical to PR #97 at `b45bc87`.
The following corrections supersede their inline prose and the earlier
"parser-clean" summaries:

- **Conversation shape is synthetic.** The probe imports the real `_ACTION_TOOLS`
  (all eight definitions) and `SYSTEM_STEP`, and uses `tool_choice: "auto"`
  and temperature 0.1. It then adds an assistant `tool_calls` message and a
  `role: tool` result to exercise the upstream reporter's suggested condition.
  AskMe's actual `get_step()` sends only the system message and a current
  user/state digest; it does not replay those prior tool messages. E25 exercised
  real agent trajectories, but not this synthetic request shape.
- **The producer's `ok` is insufficient.** It parses only the first returned
  call, without requiring exactly one call, the arm's expected tool, or an
  object matching AskMe's action schema. The analyzer now checks retained call
  count, expected tool name, parse status, and object-shape metadata. It reports
  detectable mismatches as `action_contract_failure`, even when raw `ok` is true.
- **Full schema validation cannot be reconstructed.** Successful argument values
  were not retained in full: keys and content excerpts cannot establish path,
  field-type, or other action-contract validity. Successful rows are therefore
  `parse_ok_schema_unverified`, not accepted AskMe actions. Future qualification
  needs a separately versioned producer retaining complete responses and
  validating the action schema; do not reuse the frozen inline `ok` verdict.
- **A token cap does not imply `incomplete_write`.** At the audited runtime,
  unparseable native arguments are rejected, potentially retried within the
  configured budget, and never committed. JSON-parseable arguments carrying
  `finish_reason=length` are accepted with default `ActionTransport` metadata;
  they are not marked as truncated writes. File completeness is unknown.
  See [#94](https://github.com/den-run-ai/askme/issues/94).

`uncapped_parse_failure` means argument parsing failed without a reported token
cap; it would require investigation rather than prove an upstream parser defect.
`budget_truncation` distinguishes a capped parse failure. Neither category
establishes AskMe's task outcome or recovery behavior. Missing input files now
fail analysis rather than silently reducing the evidence denominator.

## Two runs

Run 1 preceded cosmetic Ruff formatting of the producer. The prompts were
unchanged; run 2 is retained alongside it and was produced by the committed
producer. Both JSONL files and both transcripts are present: **64 trials**.
This is an archival provenance statement, not proof that rerunning against a
changed model, server, or imported AskMe revision reproduces the same results.

Run 2 overlapped a local deterministic test run. Its wall times must not be
compared with run 1 or used as performance data. The retained parse and token
observations remain reportable, without a causal timing comparison.

## Cell

- Server: `b9618-c34b92235`, `models/gemma4-e4b-qat/gemma-4-E4B_q4_0-it.gguf`.
- Documented QAT flags: `--reasoning off`, `-np 1`, 16384 context, q4_0 KV,
  `--swa-full --cache-reuse 256`; MTP off.
- Synthetic prior tool round described above; eight trials per arm, four arms,
  two runs.

## Retained observations

| Arm | n | Parse OK, schema unverified | Budget truncation | Uncapped parse failures | Hit cap | Content |
|---|---|---|---|---|---|---|
| `A_short_args` | 16 | 16 | 0 | 0 | 0/16 | short args |
| `B_long_write_512` | 16 | 14 | 2 | 0 | 4/16 | 1426–1644 chars, 56–68 lines |
| `B_long_write_2048` | 16 | 16 | 0 | 0 | 0/16 | 3904–5507 chars, 119–163 lines |
| `C_delimiter_payload` | 16 | 16 | 0 | 0 | 0/16 | 272–344 chars |
| **Total** | **64** | **62** | **2** | **0** | **4/64** | — |

All 64 rows record one expected tool call. The 62 parseable rows record object
keys, but remain schema-unverified. The two parse failures are capped at exactly
512 completion tokens with unterminated `content` strings. Of four capped
trials, the other two remained JSON-parseable. No HTTP 5xx is recorded; the
original report also states no `unparsed peg-gemma4` server-log messages.
These observations do not establish a zero AskMe decoder-rejection rate or
clear upstream #25986.

## Replacement collector — offline-tested 2026-09-08

The versioned `peg_probe_v2.py` core addresses the old first-call acceptance and
incomplete-retention defects without editing `peg_probe.py` or reconstructing
missing historical bytes. It has **no live CLI or default HTTP transport**,
server URL, credential lookup, or action dispatcher. A caller must supply an
explicit exchange callback over complete request/response-body bytes.

Each attempt creates a new directory and refuses an existing destination
before transport. It records exact request bytes and the full returned body,
including non-JSON/HTTP failures, with SHA-256 hashes. It never records HTTP
headers or transport exception text. The canonical native decoder and action
schema validate exactly one expected tool and all retained argument values;
multiple calls, wrong tools, non-object arguments and invalid fields cannot
pass `action_contract_valid`. Full accepted content and delimiter counts remain
available, including Unicode and content beyond the old prefix/suffix excerpts.

That flag is schema validity, not action execution or artifact acceptance.
Schema-valid arguments with `finish_reason=length` remain valid under the
current runtime contract; malformed arguments fail without partial salvage.
A delimiter count of zero does not exercise the delimiter condition. The
collector cannot establish either artifact completeness or upstream defect
clearance by itself. Captured bodies still need sensitivity review and a secret
scan before publication; absence of credential handling is not a privacy proof.

A new live campaign still requires a separately registered protocol: exact
runtime/model/server hashes, actual two-message and synthetic-history arms,
budgets, controls, trial count and a decision rule. The injected transport must
enforce the registered endpoint and request/response/time bounds. This PR adds
only the collector and synthetic regression tests, not that campaign or a
qualified live runner. The original 62 schema-unverified rows remain unchanged.

## Limitations and next qualification

1. The upstream trailing-output condition was not provoked, so tolerance of
   output after a complete call remains untested.
2. **Arm C did not emit its requested delimiter in 16/16 trials.** The missing
   escape mechanism is untested, not cleared.
3. One model/quant, one build, one synthetic conversation depth, temperature
   0.1, no MTP. No claim transfers to AskMe's two-message shape, other models,
   or another server build.
4. No task-outcome decision rule was preregistered. Do not cite a reliability
   interval from the old broad "parser-clean" counter; successful schema values
   and the proposed triggering conditions were not fully tested.
5. Records came from a working tree with documentation edits. Runtime source
   was not changed during either run; the producer imported tools and prompt
   text from that tree.
6. Any follow-up must use a newly versioned producer with complete response
   retention, exact single-call/expected-tool/schema checks, and separate actual
   AskMe and synthetic-history arms. Pin imported runtime and model/server
   revisions, and rerun after build or GGUF changes. Keep these records intact.
