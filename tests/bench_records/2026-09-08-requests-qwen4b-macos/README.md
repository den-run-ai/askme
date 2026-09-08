# One predeclared Requests task on local Qwen3-4B — 2026-09-08

**Did not resolve: the serving/deadline path failed before coding began.**
The only primary attempt reached the 720-second outer limit after repeated
planner HTTP read timeouts. No HTTP response or executed action was recorded,
the candidate patch is empty, and fresh-checkout acceptance still raises the
original `JSONDecodeError` pickle `TypeError`. This does not establish that
the model could not solve the coding task.

## Registration and execution

- [Primary run 34175311656](https://github.com/den-run-ai/askme/actions/runs/34175311656),
  attempt 1, job `101903426407`. Its green workflow conclusion means records
  were collected successfully; the task outcome is **did_not_resolve**.
- Frozen [protocol](protocol.json), [prompt](prompt.md), [registration](registration.json),
  [original runner](external_repo_trial.py), and [workflow](workflow.yml).
- Harness `28534c62a498ae3dcda1899a9ec2f989a5745f9e`; workflow PR head
  `1f3eff01287af0a7707ae8e007c293efcbfddc2f`. The actual GitHub merge checkout
  was `5de73dd0acdeb229cb07401321144651441fe10d`; frozen task/runner/workflow
  bytes match that PR head. Runtime hashes are in the protocol.
- [Immutable claim](https://github.com/den-run-ai/askme/commit/1cd6a9a0384202791f611f3f3c0fb040863591e8)
  was committed before inference and permits only this run. V1 is retired:
  do not reset the claim, re-dispatch, re-label, or rerun it.
- The historical external task is Requests issue
  [6628](https://github.com/psf/requests/issues/6628), pinned to
  `7a13c041dbef42f9f3feb14110f02626f6892e9a`. The selection rule, contamination
  caveat and decision rules were recorded in the
  [original task README](task-README.md) before the attempt.
- Official `Qwen/Qwen3-4B-GGUF`, revision
  `bc640142c66e1fdd12af0bd68f40445458f3869b`, `Qwen3-4B-Q4_K_M.gguf`;
  immutable bytes/hash are in [model-identity.json](model-identity.json).
- llama.cpp source `c34b92235b2d6a07963f896085f9ca077ff400b4` (b9618 source).
  The shallow-build version string is retained verbatim. Context 8192, q8_0
  KV, reasoning off, temperature 0.1, seed 20260908; exact flags and budgets
  are in [server-command.json](server-command.json) and the protocol.

## Observed outcome

| Dimension | Recorded result |
|---|---|
| Controls before inference | Baseline and nonempty no-op fail; gold passes all 21 checks |
| Primary attempts | 1, including every internal request/retry |
| Planner requests | 5; four recorded read timeouts (120, 120, 120, 180 seconds); fifth interrupted |
| Completed HTTP responses / executed actions | 0 / 0 |
| Patch | Empty; `patch_applies=true` only means the evaluator accepted an empty diff |
| Independent acceptance | Failed on the original pickle round-trip error |
| Agent completion | No terminal record; outer process timed out |
| Measured attempt wall time | 720.235 seconds; configured limit 720 seconds |
| Tokens | Unknown; zero returned usage records is not zero inference |
| Cost | $0 hosted API charge; standard public macOS runner, no billing measurement |

See the unmodified [summary](summary.json), [qualification](qualification.json),
[HTTP records](http.jsonl), [agent events](agent.jsonl), and
[server log](llama-server.log). The server records prompt processing and task
cancellation. The records establish deadline failures, but do not isolate the
underlying cause of the latency.

The machine reported `VirtualMac2,1`, arm64, 3 vCPU, 7 GiB, macOS 26.6.2.
The server detected an `Apple Paravirtual device` Metal device with 4778 MiB
and an `Apple M1 (Virtual)` CPU. Although the command requested 99 GPU layers,
the log does not establish actual layer allocation or CPU fallback. This is
not a reproduction of physical M1/16 GB reference performance. Server health
and the separate tiny CI contract did not qualify this real 4B workload.

## Retention and independent verification

[portable-evidence.tar.gz](portable-evidence.tar.gz) is the original job-log
archive, SHA-256 `db68958d8bc1d2f85819fd384e4825234d3534ce5559a68f9f7245a5905a19d6`.
Its 27 files are also extracted here, byte for byte. Additional provenance,
the frozen workflow/claim/task README, and the [manifest](manifest.json)
identify their source and hashes. Original runner/evaluator copies are
historical evidence; do not edit or execute them as a new model attempt.

The full GitHub artifact additionally contains `workspace.tar.gz` and expires
on 2026-12-07. Its identity/digest and the connector's HTTP 403 download
limitation are disclosed in [provenance.json](provenance.json). The permanent
portable archive retains all requests, errors, logs, controls, original
runner/evaluator, patch and scoring evidence. Because no coding action was
reached, the pinned upstream plus empty patch reproduce the unmodified
candidate files; the full workspace is not needed to recover a hidden repair.

[Independent offline verification](offline-verification.json) checked the archive and registered file hashes,
recounted every request/event, and replayed the frozen controls on Linux
Python 3.12.13: baseline/no-op fail and gold passes 21 checks. The original
macOS attempt used Python 3.12.10. This is an evaluator replay, with no model
calls and no revised score. The original runner's subsequently fixed
model-committed-patch edge case was not reached, so no scoring amendment is
needed. The earlier workflow-validation failure occurred before any job or
model call; it is retained in the task README history.

This is one potentially training-contaminated historical bug under one serving
configuration. It yields no small-model reliability estimate, model-size
effect or causal harness comparison. Follow-up serving qualification is
tracked in [#109](https://github.com/den-run-ai/askme/issues/109); it requires
a new protocol and does not replace this failed primary outcome.
