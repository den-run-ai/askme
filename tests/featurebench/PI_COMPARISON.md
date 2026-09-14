# Berkeley AskMe–pi qualification

This fresh four-cell experiment completes the operational comparison requested
for the Berkeley presentation. It is a one-task instrumentation check, with one
attempt for each AskMe/pi × Gemma 4 31B/Qwen3.6 27B cell. Pi changes the whole
scaffold; this cannot isolate a planner, tool, transport or budget effect.

The [frozen manifest](pi-comparison-sep14.json) declares the task, order, model
routes, limits, controls and decision rule. The known Seaborn task remains
development evidence and is excluded from future untouched confirmation panels.
The archived [PR #14](https://github.com/den-run-ai/askme/pull/14) remains unchanged
and unqualified. Its historical results are not contemporary controls.

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

Execution requires the `pi-comparison` label on a same-repository draft PR and a
separate durable claim on `eval/claims/pi-sep14-v1` binding the exact workflow
commit, protocol hash and Actions run ID. A relabel or Actions rerun cannot reuse
the claim. No replacements are allowed after any outcome-bearing call.

Ordinary CI runs only deterministic contracts:

```sh
uv run --locked pytest tests/test_pi_comparison.py tests/test_pi_comparison_proxy.py -q
```

The task image, model routes and dependency catalog may become unavailable. Such
failures are setup failures; they must not be relabeled as unresolved model
results or trigger an outcome-dependent replacement. Stop after the four cells.
Any expanded comparison needs a new prospective protocol.
