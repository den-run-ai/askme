# Berkeley AskMe–pi qualification

This four-cell experiment investigates the operational comparison requested
for the Berkeley presentation. It is a one-task instrumentation check, with one
attempt for each AskMe/pi × Gemma 4 31B/Qwen3.6 27B cell. Pi changes the whole
scaffold; this cannot isolate a planner, tool, transport or budget effect.

The [completed v2 receipt](../bench_records/2026-09-14-pi-comparison-v2/README.md)
reports 8/13 held-out target tests for both AskMe cells and 11/13 for both pi
cells. Every patch applied and all 387 preservation tests passed; no cell
resolved the complete task. Gemma pi reached agent completion, both AskMe
episodes exhausted replans, and Qwen pi reached the conservative reservation
cap. These are four single attempts on one known task, not a model ranking.

The 111 requests cost $0.232438 in generation-audited charges. Metadata polling
recorded 1,480.774 seconds across calls; episode wall times include this study's
instrumentation and do not measure native model or harness latency. The receipt
also records a missing read-limit bound in AskMe's advertised tool schema;
the observed failures cannot be attributed solely to model capability.

The [v2 manifest](pi-comparison-sep14-v2.json) declares the task, order, model
routes, limits, controls and decision rule. The known Seaborn task remains
development evidence and is excluded from future untouched confirmation panels.
The archived [PR #14](https://github.com/den-run-ai/askme/pull/14) remains unchanged
and unqualified. Its historical results are not contemporary controls.

The [v1 receipt](../bench_records/2026-09-14-pi-comparison-v1/README.md) retains
all four infrastructure-invalid attempts. Each stopped after its first model
response because generation metadata was unavailable to the proxy; no usable
task score was produced. Response telemetry reported $0.00299138, while the
conservative reservation remained $0.05043320 and settled billing is unknown.
The original [v1 manifest](pi-comparison-sep14.json) remains immutable.

V2 is a separately registered follow-up across all four cells. It repairs
metadata polling with a bounded 60-second window, explicit User-Agent and safe
stage/status diagnostics. It preserves the task, model routes, native harness
settings, cell order and limits. V1's full reservation plus v2's $8 cap totals
$8.05043320 against the user's original $10 budget. No v1 attempt is replaced.

The runner addresses the archived runner defects: it derives the checkout root,
counts one terminal finish reason per response choice, and requires a valid pi
`agent_end` with a successful terminal assistant message. Pi can exit zero and
emit `agent_end` after an HTTP error; that is still incomplete.

Before inference, the workflow checks exact sources, current endpoint identity,
precision and prices, and runs fresh official gold and harmless nonempty
controls. Each cell uses a fresh masked workspace and process. Only the six
AskMe runtime files enter the inference container; repository evidence and Git
history are excluded. The inference container is destroyed before the official
evaluator runs. Both scaffolds share the same tooling image and external ceilings,
while retaining their native prompts, tools and internal budgets.

The host proxy reserves conservative request charges before forwarding, with a
$2 ceiling per cell and $8 total. Unknown or failed charges retain their complete
reservation. The real key never enters an inference container. Containers use
an internal Docker network; only the host proxy provides model access. All calls,
route metadata, cost reservations, trajectories, patches, official acceptance
and failures are retained. The generated npm dependency lock, Node checksum,
derived image ID and source hashes are recorded before the first model request.

V2 execution requires the `pi-comparison-v2` label on a same-repository draft PR and a
separate durable claim on `eval/claims/pi-sep14-v2` binding the exact workflow
commit, protocol hash and Actions run ID. A relabel or Actions rerun cannot reuse
the claim. No replacements are allowed after any outcome-bearing call.

Two initial setup runs made no model requests and spent no credits. The
[infrastructure amendment](pi-comparison-infrastructure-amendment-sep14.json)
retains their control evidence and records the launcher-path and environment
fixes before the first outcome-bearing attempt. Artifact auditing rejects
symlinks and special files before reading any content.

Ordinary CI runs only deterministic contracts:

```sh
uv run --locked pytest tests/test_pi_comparison.py tests/test_pi_comparison_proxy.py -q
```

The task image, model routes and dependency catalog may become unavailable. Such
failures are setup failures; they must not be relabeled as unresolved model
results or trigger an outcome-dependent replacement. Stop after the four cells.
Any expanded comparison needs a new prospective protocol.
