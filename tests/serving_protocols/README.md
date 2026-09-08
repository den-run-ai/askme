# Physical M1 serving qualification, 2026-09-08

Issue [109](https://github.com/den-run-ai/askme/issues/109) follows the failed
Requests/Qwen task on a 7 GiB virtual Mac. These are new, task-independent
serving cells; the original task v1 and all its records remain frozen.

The two manifests were published at `0c9d3b8` before model requests. The
qualifier, resolved cases and thresholds are frozen in a subsequent commit
before execution, and hashed again in each registration before any HTTP call.
Run the existing physical-Mac Gemma E4B cell first, then the exact Qwen3-4B
weights from the failed CI attempt. Each cell contains seven singleton cases
and one cancellation probe. Keep every outcome, including incomplete cells.
Do not change budgets or selectively rerun failed probes. E4B is dense PLE;
it is not the Gemma 26B-A4B MoE.

The primary follow-up coding route is Qwen if its entire serving gate passes.
Only if Qwen fails serving qualification may qualified E4B be substituted.
At most one new Requests task attempt is planned. Register that task's exact
model, endpoint, configuration, current-main runtime and serving-record hash
before task inference. Requalify baseline, nonempty no-op and gold controls.
If neither deployment qualifies, run no coding task and publish both failed
serving cells. Task and serving results cannot establish general reliability
or a causal harness benefit.

```bash
uv run --locked python tests/serving_qualification.py \
  --protocol tests/serving_protocols/2026-09-08-m1-e4b.json \
  --output /path/to/new-e4b-records
uv run --locked python tests/serving_qualification.py \
  --protocol tests/serving_protocols/2026-09-08-m1-qwen4b.json \
  --output /path/to/new-qwen-records
```

The Qwen manifest records its downloaded artifact path; reproductions use a
new manifest with their own path and identity. Record the server's full
command, `-lv 4` startup/offload log, model SHA-256, linked binary hashes,
hardware, memory pressure and swap before/after. The existing E4B server is
warm and its startup was not controlled by this study. No other inference
should overlap; an idle E4B process remains present during Qwen's cell.
These are checks on an interactive physical host, not isolated performance
benchmarks or a matched hardware experiment. The 120-second serving bound
tests whether 1024 output tokens can fit the planned local request deadline.

The local MoE alternative was not selected: Google's official text QAT
[26B-A4B weights](https://huggingface.co/google/gemma-4-26B-A4B-it-qat-q4_0-gguf/tree/main)
alone are 14.4 GB; this host had about 12 GiB free disk and already used
8.7 GiB swap before setup. The available E4B model is 5.15 GB. OpenRouter
remains an optional future route within the user's $10 allowance; this
registered local study spends no hosted credits.

## Separately registered hosted control

The physical cells later exposed latency limits; a power-state observation
during Qwen's cell found the host on battery with Low Power Mode enabled.
That observation does not identify its causal contribution and does not
change the frozen local matrix or authorize a local task after a failed gate.

The [hosted manifest](2026-09-08-gemma-moe-hosted.json) therefore declares a
separate Gemma 26B-A4B MoE route: DeepInfra fp8 on OpenRouter, exact served
model pin, no fallbacks, required parameter support and reasoning off.
Two task-independent planner/native-tool probes precede any task inference.
The user authorized up to $10; this hosted study caps qualification at $0.05
and its single subsequent Requests task at $0.45, at most $0.50 combined.
Every attempted request reserves a conservative cost bound before dispatch;
unknown charges keep their reservation. Credentials are supplied in memory
and never enter protocols, request records or task files. AskMe still is not
a host isolation boundary.

This control uses a different model, provider, precision and budget profile.
It cannot establish a local performance result or a causal model/harness
comparison. Preserve a failed qualification and do not selectively rerun it.
Register the exact task protocol, runtime, runner and serving-record hashes
before its one task attempt, and requalify all three task controls.
