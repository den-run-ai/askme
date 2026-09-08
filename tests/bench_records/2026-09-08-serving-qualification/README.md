# Physical M1 serving qualification — 2026-09-08

Both deployments failed the predeclared serving gate. No local coding task
was attempted. Both models returned valid synthetic plans and the requested
native `write` call; latency and cancellation recovery prevented qualification.
These are deployment observations, not coding-capability failures.

## Protocol and retained evidence

The [E4B](../../serving_protocols/2026-09-08-m1-e4b.json) and
[Qwen3-4B](../../serving_protocols/2026-09-08-m1-qwen4b.json) cells each contain
seven singleton probes followed by one cancellation probe. Manifests were
published before inference; the executed qualifier and resolved thresholds
were frozen at [d29b68c](https://github.com/den-run-ai/askme/commit/d29b68c7c6c13b4b0957003757ea523ee7edde3a).
Registrations hash the runner, runtime and configuration before HTTP calls.
There were no selective probe reruns and no hosted API charge.

Native probes request exactly 600 or 2000 input tokens, force 128/512/1024
output tokens with EOS disabled, and disable prompt reuse. Completed probes
confirm `timings.cache_n=0`. Planner and action probes use AskMe's actual
prompts and all action schemas. The gate requires first output within
30 seconds, each complete response within 120 seconds, and a slot-idle
response within 10 seconds after the cancellation client's one-second limit.
All checks must pass before a separately registered local task may run.

The [raw archive](raw-evidence.tar.gz) retains the original `e4b/` and
`qwen4b/` directories, including registrations, exact requests, streamed
responses, partial outputs, timeout records and qualification results.
All 174 archived files were verified byte-for-byte against the original
directories; [member hashes](raw-evidence-manifest.json) make that check
reproducible. The [frozen qualifier](frozen-serving-qualification.py) is also
retained and matches its registered SHA-256.
Readable results are also retained as [E4B](e4b-qualification.json) and
[Qwen](qwen4b-qualification.json). Model hashes, flags, context, KV precision,
host metadata and linked-binary hashes are retained in the registrations and
[host snapshots](host-before.json).

## Results

Times below are seconds, rounded to milliseconds from the retained records.
Each cell gives **first output / total client wall time**. A timeout includes
bounded client cleanup; an incomplete reply has no final throughput result.

| Probe | E4B | Qwen3-4B | Both cells |
|---|---:|---:|---|
| 600 input / 128 output | 15.044 / 42.908 | 14.975 / 38.182 | Pass |
| 2000 input / 128 output | 45.734 / 74.692 | 47.607 / 71.676 | First output too late |
| 600 input / 512 output | 13.475 / 104.107 | 13.043 / 97.399 | Pass |
| 2000 input / 512 output | 43.316 / 120.014 | 42.690 / 120.009 | First output too late; timeout |
| 2000 input / 1024 output | 44.522 / 120.014 | 40.790 / 120.016 | First output too late; timeout |
| Planner JSON | 14.377 / 18.625 | 10.677 / 13.237 | Pass |
| Native action | 51.803 / 58.723 | 46.094 / 52.496 | Correct call, first output too late |

Completed native throughput probes measured E4B prefill at 39.93–44.57
tokens/s and decode at 4.45–5.66 tokens/s; Qwen prefill at 40.10–46.27
tokens/s and decode at 5.37–6.08 tokens/s. These ranges cover three completed
input/output-size combinations per model, not repeated-trial variance.

Both cancellation clients received server acceptance before being interrupted.
Their idle checks timed out after 10.012530 seconds (E4B) and 10.010152 seconds
(Qwen). The later server logs show cancellation-enqueue to slot-release delays
of **41.568229 seconds** for E4B and **36.508148 seconds** for Qwen. Those later
observations do not change either failed ten-second recovery gate. See the
[E4B excerpt](e4b-server-excerpt.log) and [Qwen log](qwen-server.log).
The E4B excerpt starts at byte offset 3,260,796 of the existing server log,
sampled before these probes; earlier user-session logs were not copied.

## What the diagnosis establishes

The physical host is a MacBookAir10,1, Apple M1, eight CPU cores, 16 GiB,
macOS 26.5; llama.cpp is b9618, source
`c34b92235b2d6a07963f896085f9ca077ff400b4`. E4B is the existing Gemma 4 E4B
QAT Q4_0 **dense PLE** deployment, not the Gemma 26B-A4B MoE. Qwen uses the
same official Qwen3-4B Q4_K_M artifact as the failed CI trial. Its verbosity-4
startup log explicitly reports **37/37 layers offloaded to GPU**. CPU-only
fallback therefore does not explain this physical Qwen cell; the original
CI log still does not establish its actual layer allocation.

Cancellation is queued in this pinned llama.cpp version. The
[response reader](https://github.com/ggml-org/llama.cpp/blob/c34b92235b2d6a07963f896085f9ca077ff400b4/tools/server/server-queue.cpp#L430-L445)
logs cancellation and posts a priority task. The
[main queue loop](https://github.com/ggml-org/llama.cpp/blob/c34b92235b2d6a07963f896085f9ca077ff400b4/tools/server/server-queue.cpp#L139-L167)
drains tasks, then synchronously calls the inference update, which invokes
[`llama_decode`](https://github.com/ggml-org/llama.cpp/blob/c34b92235b2d6a07963f896085f9ca077ff400b4/tools/server/server-context.cpp#L3286-L3302).
The [cancel handler](https://github.com/ggml-org/llama.cpp/blob/c34b92235b2d6a07963f896085f9ca077ff400b4/tools/server/server-context.cpp#L2233-L2241)
releases the slot when processed. Slot-status metrics use that same queue.
Thus the cancellation log records enqueueing, not an immediate interruption
or release acknowledgment. A long inference update can delay cancellation
and `/slots` replies. This identifies a mechanism for the observed delay;
it does not isolate why the underlying inference was slow.

The [power observation](power-observation.json), collected during Qwen's cell
after E4B finished, found battery power, 88% charge and battery Low Power Mode
enabled. No earlier power sample exists; settings were not changed. The host
already used 8695.44 MiB of swap before qualification. It remained an
interactive host, and the idle E4B server stayed resident during Qwen's cell.
Power state, resident workloads and memory activity are uncontrolled variables.
These cells do not reproduce an isolated, tuned reference-performance result.

The [original virtual-Mac trial](../2026-09-08-requests-qwen4b-macos/README.md)
logged its first 147 prompt tokens at 9.05 tokens/s and returned no complete
HTTP response. This physical deployment does complete small requests, but
different hardware, workloads, power observations and prompt sizes prevent
attributing that contrast to virtualization, memory, configuration or any
single cause. A future causal comparison needs a new protocol with matched
inputs and sampled power/memory conditions. Neither cell estimates coding
reliability, a model-family effect or a causal harness benefit.

## Offline correction audit

The original qualifier passed a hardcoded `stop` to the planner decoder;
the current helper now forwards the recorded finish reason and exposes OAI
prompt-token usage. A deterministic cutoff regression reproduces why that
distinction matters. The appended [offline audit](offline-audit.json) verifies
the original runner hash, all fourteen case verdicts/error lists, and all
174 original cell files. Both actual planners ended with `stop`, and both
native calls exactly requested `hello.txt` containing `hi\n`. Their observed
prompt counts were 633/2258 (E4B planner/action) and 562/2240 (Qwen).
Original qualification files are unchanged; no score was amended and this
audit made no model calls.
