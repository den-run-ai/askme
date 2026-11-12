# AskMe native

The complete AskMe harness as typed Python that also compiles to a standalone
executable. One source tree runs on CPython and PyPy, type-checks under mypy,
compiles with mypyc, and builds with [Pystachy](https://github.com/den-run-ai/pystachy)
into a binary that needs no Python at run time.

`askme.py` remains the reference implementation and the supported entry point.
This directory is an opt-in second implementation of the same contract:
planner, executor, task-local and full replans, all eight actions, both step
policies, write obligations, completion and validation, the OpenRouter and
local request shapes, and the command line.

## Try it without a model

A scenario file holds a prompt's recorded model replies:

```sh
workdir=$(mktemp -d)
python3 native/askme_native.py --working-dir "$workdir" \
  --replay native/scenarios/hello.json 'Create hello.txt containing hello'
cat "$workdir/hello.txt"
```

Build the same files into an executable with an installed Pystachy, then run
it the same way:

```sh
pystachy build native/askme_native.py -o askme
./askme --working-dir "$workdir" \
  --replay native/scenarios/hello.json 'Create hello.txt containing hello'
```

`pystachy run native/askme_native.py ...` compiles and runs in one step.

## Run against a model

The options and environment variables are those of `askme.py`
([configuration](../docs/configuration.md)):

```sh
export LLM_BACKEND=openrouter OPENROUTER_API_KEY=...
./askme --working-dir /path/to/project --result-json result.json \
  "Fix the failing tests"
```

The executable needs `/bin/sh` for shell actions and `timeout` (`gtimeout` on
macOS) for their deadlines. It sends model requests itself, over connections
it keeps open; for `https://` it loads the system's OpenSSL 3 (`libssl.so.3`)
when the first request leaves. Behind a proxy, or where that library is
missing, it runs `curl` for each request instead. AskMe is **not a
sandbox**: model-generated commands run with your permissions, exactly as in
`askme.py`. See the [security guide](../docs/SECURITY.md).

## How the two implementations stay equal

`tests/test_native_port.py` runs every scenario in `native/scenarios/` through
`askme.py` and through this port, then compares the complete transcript:

| Compared | Source |
|---|---|
| Every model request: endpoint, timeout, header names, JSON body | request log |
| Every JSONL event, in order | `AGENT_RUN_LOG` |
| Every console line | stdout |
| The structured result, including key order | `--result-json` |
| The final workspace and the exit code | file system |

Only wall times and the workspace location are normalized. The scenarios cover
all eight actions, read continuations, duplicate and stuck-loop guards, write
pressure, rewrite damping, the lifecycle policy, truncated-write recovery,
compile repair, task-local and full replans, validator verdicts, transport and
decode retries, OpenRouter request shaping, and exhaustion. A second test
drives both action layers through 55 actions, including rejected envelopes and
file-system errors, and requires equal results with no normalization.

The same comparison runs against the Pystachy JIT and a native executable
when a compiler is available:

```sh
ASKME_NATIVE_PYSTACHY=/path/to/pystachy uv run --locked pytest tests/test_native_port.py -q
```

`ASKME_NATIVE_MYPYC` and `ASKME_NATIVE_PYPY` add a mypyc build and a PyPy
interpreter the same way. A runtime without its variable skips, and a skipped
case is not evidence that the port works there.

`tests/native_scenarios.py` is the source of the scenario files; run
`python3 tests/native_scenarios.py` after editing it.

## Differences from `askme.py`

A compiled program has no `subprocess`, `requests`, `hashlib` or `pathlib`.
`native_host.py` supplies those operations from `os`, `open()` and
`os.system()`, and every runtime uses the same adapters.

| Area | `askme.py` | Native port |
|---|---|---|
| HTTP | `requests`, a new connection per request | One HTTP/1.1 client (`native_http.py`) on every runtime, over a connection kept per endpoint; see [HTTP transport](#http-transport) |
| Shell actions | `subprocess` in a new session | `/bin/sh` under `timeout`, which signals the command's process group; without `timeout` a plain watchdog signals the command's shell only |
| SIGPIPE in commands | Default, restored by `subprocess` | Default, restored through GNU `env` or `perl`; with neither, `producer \| head` reports "Broken pipe" |
| Non-UTF-8 command output | Raises | Replaced with U+FFFD |
| Child locale | CPython exports `LC_CTYPE=C.UTF-8` under the C locale | A compiled binary passes the environment through unchanged |
| `.env` | Loaded from the script's directory | Only with `--env-file PATH` |
| Console timestamps | Local time | UTC |
| Configuration errors | Traceback, exit 1 | `askme: error: ...`, exit 2 |
| Malformed provider bodies | Some raise (`IndexError`, `AttributeError`) | Typed transport or parse failures |
| Integers beyond 64 bits in a reply | Accepted, then range-checked | A decode failure under Pystachy |
| Python API | `run_result()`, `RunDependencies`, patchable module globals | None: the command line is the interface |

Three options exist only here: `--replay FILE` answers model requests from
recorded replies, `--request-log FILE` appends every request (header names
only, never values), and `--env-file FILE` loads settings that are not already
set. A replay entry may mark a write as a trusted partial payload; that is the
port's equivalent of the `ActionTransport` metadata an injected client
supplies, and it is how the truncated-write recovery is exercised.

## HTTP transport

`native_http.py` implements the one request AskMe makes: a POST with a JSON
body, answered with a `Content-Length`, chunked or close-delimited reply. The
byte stream beneath it is the only part that differs between runtimes:

| Runtime | Stream | TLS |
|---|---|---|
| Pystachy executable or JIT | libc sockets through Pystachy's FFI (`native_ffi_net.py`) | OpenSSL 3 through the FFI (`native_ffi_tls.py`), loaded at the first `https://` request |
| CPython, PyPy, mypyc | `socket` (`native_std_net.py`) | `ssl` |

Both verify the certificate chain and the host name, and neither has a switch
that turns verification off. Trust comes from the system store, from
`SSL_CERT_FILE`/`SSL_CERT_DIR`, and from the bundle `CURL_CA_BUNDLE` or
`REQUESTS_CA_BUNDLE` names. The timeout bounds the connection attempt and each
read, as with `requests`; a request that timed out is never sent again by the
transport.

A reply ends its connection when it says `Connection: close`, has no length,
or is cut short. Before a kept connection is reused it is checked for a close;
a request that still meets one, with no byte of a reply received, is sent once
more on a new connection.

`ASKME_NATIVE_HTTP` selects the transport:

| Value | Behaviour |
|---|---|
| `auto` (default) | In-process; `curl` when a proxy variable applies to the host (`HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY`, minus `NO_PROXY`) or the stream libraries cannot load |
| `inprocess` | In-process only, directly, with an error where the libraries cannot load |
| `curl` | One `curl --max-time` process per request; headers pass in a private file, never in arguments |

The FFI stream assumes 64-bit Linux with glibc. Elsewhere a Pystachy build
finds no `libc.so.6` and stays on `curl`. `ASKME_NATIVE_PRELOAD=1` loads the
libraries at startup instead of at the first request.

## Measurements

With model replies recorded, the harness alone is timed. Medians from one
shared 4-core x86-64 Linux virtual machine on 2026-11-12, each runtime first
reproducing CPython's transcript
([method and full results](https://github.com/den-run-ai/pystachy/blob/32e8337ad275c8247ecbdf7b0463675e6ebdd3f4/bench/askme/results/2026-11-12-runtimes.md)):

| Runtime | `--help` | 17 scenarios | 2,001-request run | Peak memory, short run |
|---|---:|---:|---:|---:|
| Pystachy executable | 2.3 ms | 0.20 s | 1.7 to 2.0 s | 5 MB |
| Pystachy JIT, cached | 54 ms | 1.09 s | 1.7 to 2.4 s | 13 MB |
| mypyc + CPython 3.13 | 43 ms | 0.94 s | 1.3 to 1.7 s | 19 MB |
| CPython 3.13 | 45 ms | 0.98 s | 1.9 to 2.1 s | 17 MB |
| PyPy 7.3 | 106 ms | 2.70 s | 2.7 to 3.4 s | 123 MB |
| `askme.py` on CPython 3.13 | 158 ms | 2.85 s | 2.4 to 2.5 s | 29 MB |

The executable wins where startup dominates. On long runs it is at CPython's
level and mypyc is fastest. Over real HTTP the port's `curl` process costs
about 7 ms per request on every runtime, against about 1 ms for `requests`,
so `askme.py` finishes first after about 26 requests. No model was called:
with a hosted model, where a request takes hundreds of milliseconds, these
differences are a few percent of a run. Re-run before citing a number after
the code, a runtime or the machine changes.

## Layout

| File | Mirrors | Contents |
|---|---|---|
| `askme_native.py` | `askme.py` | Command line, environment configuration, replay transport |
| `native_loop.py` | `loop.py` | Prompts, planner/executor/replanner calls, run controller |
| `native_policies.py` | `policies.py` | Step policies, write obligations, completion and validation |
| `native_llm.py` | `llm.py` | Settings, request building, reply decoding, retry policy |
| `native_actions.py` | `actions.py` | Action registry, envelope parser, handlers, receipts |
| `native_state.py` | `state.py` | Run state, the step recorder, attempt and step records |
| `native_host.py` | — | Paths, files, SHA-256, processes, the `curl` fallback |
| `native_http.py` | — | The HTTP/1.1 client and its kept connections |
| `native_std_net.py` | — | Streams from `socket` and `ssl`, for the interpreters |
| `native_ffi_net.py`, `native_ffi_tls.py` | — | Streams from libc and OpenSSL through Pystachy's FFI; imported by a Pystachy build only |
| `native_json.py` | — | The JSON value type and helpers |

Run state and step records stay plain JSON values, as in `askme.py`, typed by
one recursive alias. The compatibility facades of `askme.py` have no
counterpart: nothing here is monkeypatched.

A change to the harness must land in both implementations. The parity tests
fail when they diverge on a recorded scenario; add a scenario for behaviour
that none covers.

## Limits

- Validated on Linux. The adapters are POSIX code and should run on macOS,
  but the parity suite does not run there. Windows process handling is not
  ported.
- The in-process client speaks HTTP/1.1 only, sends one request at a time
  and, like the `curl` path, does not follow redirects; a proxy is `curl`'s to
  handle. A compiled build's TLS needs OpenSSL 3 by its Linux name,
  `libssl.so.3`.
- Live model behaviour, provider compatibility and task success rates are
  unchanged from `askme.py` and are not re-qualified here. The scenarios
  replay recorded replies; they measure the harness, not a model.
- Pystachy is an experimental compiler. Its 64-bit integers and bounded JSON
  nesting apply to the compiled program.
