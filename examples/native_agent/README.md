# A small native AskMe subset

This opt-in example extracts AskMe's single-action loop into typed Python that
also compiles with [Pystachy](https://github.com/den-run-ai/pystachy). It shows
that a useful agent controller can run as a standalone executable without a
Python interpreter. Model inference remains in an external server.

The main AskMe CLI and API are unchanged. This is a separate, deliberately
smaller contract, not a port of the complete harness or an agent benchmark.

## Try the deterministic repair

From the AskMe repository root, on Linux with GNU coreutils:

```sh
workdir=$(mktemp -d)
cp examples/native_agent/fixtures/value.txt "$workdir/value.txt"
python3 examples/native_agent/agent.py "$workdir" 'Fix value.txt' \
  'grep -qx fixed value.txt' examples/native_agent/fixtures/repair.json
cat "$workdir/value.txt"
rm -rf "$workdir"
```

The replay first asks to finish too early, reads the file, fixes it, runs the
configured check, and finishes. It goes through the same native-tool decoder
as live API responses. It prints JSONL receipts and a final status; success
exits 0, failed/exhausted runs exit 1, configuration errors exit 2.

Compile the **same files** with an installed Pystachy:

```sh
pystachy build examples/native_agent/agent.py -o /tmp/askme-mini
workdir=$(mktemp -d)
cp examples/native_agent/fixtures/value.txt "$workdir/value.txt"
/tmp/askme-mini "$workdir" 'Fix value.txt' 'grep -qx fixed value.txt' \
  examples/native_agent/fixtures/repair.json
rm -rf "$workdir"
```

For JIT execution, substitute `pystachy run examples/native_agent/agent.py`
for `python3 examples/native_agent/agent.py` in the first command. The AOT
executable needs no Python or LLVM at execution time.

## Live mode

With a running OpenAI-compatible native-tool server and `curl` installed:

```sh
export NATIVE_AGENT_ENDPOINT=http://127.0.0.1:8080/v1/chat/completions
export NATIVE_AGENT_MODEL=local
/tmp/askme-mini /path/to/disposable/project 'Fix the failing parser test' \
  'make test' - 12
```

Set `NATIVE_AGENT_API_KEY` for a hosted endpoint. Keys go in a private temporary
header file, not process arguments or receipts. No `.env` loader, provider
profiles, streaming, model reasoning controls, or automatic HTTP retries are
included. `tool_choice=auto` follows AskMe's current local transport choice;
the decoder still requires exactly one native tool call. Live model capability
and hosted-provider compatibility have not been qualified for this example.

## Contract

| Tool | Arguments | Behavior |
|---|---|---|
| `read` | `path` | UTF-8 text; a capped observation explicitly says it is truncated |
| `write` | `path`, `content` | Atomic replacement; existing file permissions preserved |
| `edit` | `path`, `old`, `new` | Replace exactly one nonempty literal occurrence |
| `run_test` | none | Run the fixed operator-selected command, never a model-selected command |
| `done` | none | Accepted only after a successful check following the latest mutation |
| `fail` | `reason` | Stop with a failed status |

Unknown fields, wrong types, duplicate JSON keys, multiple calls, malformed
arguments, and content-only responses are rejected before effects. A valid
argument payload remains valid if the provider reports `finish_reason=length`.
Three identical consecutive actions exhaust the run; repetition never proves
completion. The default is 12 response attempts, with a maximum of 100, and
only four recent receipts enter the next request. Receipts retain file paths
but omit write/edit payloads.

The JSON codec uses typed nodes and integer child indices rather than `Any` or
mixed dictionaries. It handles raw UTF-8, escaped Unicode including surrogate
pairs, strings, containers, booleans/null, and numeric lexemes. It is a wire
codec, not a CPython `json` compatibility layer. JSON/files are capped at
1 MiB, nesting at 32, and observations at 4,096 characters. No binary files,
large-file continuations, new parent-directory creation, or concurrent agents.

The initial process adapter is Linux-specific: `sh`, `timeout`, `mktemp`, `mv`,
`chmod`, and `rm` are external GNU/coreutils tools. Tests have a 20-second
timeout plus 2-second kill grace, bounded output files, and capped observations;
HTTP has a 60-second timeout and 1 MiB response cap. The configured command
runs with the launching user's permissions. This example is not a sandbox.
Passing that visible command supports completion of this run, not independent
acceptance of the generated patch.

## Verification and next steps

```sh
uv run --locked pytest tests/test_native_agent.py -q
PYSTACHY_AGENT_BINARY=/tmp/askme-mini \
  uv run --locked pytest tests/test_native_agent.py -q
```

Set `PYSTACHY_AGENT_JIT` to an executable launcher that forwards its arguments
to `pystachy run examples/native_agent/agent.py` to include JIT parity. The
suite compares complete receipts and disposable workspace bytes, including
failed checks, completion denial, invalid responses, exhaustion, and Unicode.
The ordinary suite mocks HTTP; it makes no model calls.

The separate loopback smoke exercises the actual HTTP adapter using recorded
responses, with no model or paid API call:

```sh
python3 examples/native_agent/http_smoke.py -- /tmp/askme-mini
python3 examples/native_agent/http_smoke.py -- python3 examples/native_agent/agent.py
```

`http_smoke.py` is a CPython test driver; only `agent.py` and `wire_json.py`
belong to the compiled agent.

This is a portability and packaging showcase. Model generation and tools will
usually dominate wall time. Any performance study should separately measure
offline replay, launch time, and memory, with matched model/tool timings.
Useful follow-ups are a small native process/HTTP adapter and an additional
typed tool domain; full `requests`/dynamic AskMe compatibility is broader work.
