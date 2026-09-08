# One current-code external repair, registered 2026-09-08

This is an explicitly selected, single-attempt historical bug repair in the actual
[Requests repository](https://github.com/psf/requests/tree/7a13c041dbef42f9f3feb14110f02626f6892e9a),
addressing [upstream issue 6628](https://github.com/psf/requests/issues/6628).
Selection preceded inference: choose a real, small Python serialization bug with
a known gold patch, cheap network-free acceptance, and no extra runtime dependency.
The historical fix may be in model training data. This is a reproducible research
canary, not a novelty test, benchmark score, or estimate of reliability.

The [protocol](protocol.json) freezes the current merged AskMe runtime, upstream
revision, official Qwen3-4B Q4_K_M weights and digest, llama.cpp b9618 source revision,
8K context, q8_0 KV cache, seed, all controller limits, one trial, and a 720-second
outer wall limit. The explicit capability profile has 512 ordinary/1024 write
output tokens, 384 planner/512 validator/96 task-replanner tokens. AskMe retains its
registered runtime's retry policy (up to three HTTP attempts per LLM call), 120s
ordinary/180s replan request timeouts, and temperature 0.1. The wrapper records and
adds seed 20260908 at the supported HTTP injection seam. Retry attempts and partial
responses remain evidence; none starts another task trial. No budget increase or
model substitution is allowed after observing an outcome.

The `macos-26` standard runner has M1/3 vCPU/7 GB. A 2.497 GB model and 8K context
are selected to fit this smaller machine. Actual architecture, memory, OS, compiler,
server binary digest and model digest are retained. This does **not** reproduce the
documented Gemma E4B/M1/16 GB setup and cannot update its performance baseline.
GitHub's [standard runner documentation](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
and [larger runner limits](https://docs.github.com/en/actions/reference/runners/larger-runners)
describe the available hardware; larger runners require an organization plan.

Before generation, the runner writes registration and requalifies three fresh
workspaces: original source fails; a harmless non-empty comment patch fails; and
the minimized upstream gold patch passes. Failed qualification prevents inference.
Acceptance uses stdlib `json`, every supported pickle protocol, multiple documents
including Unicode/empty input, error metadata/type/string preservation, a real
`Response.json()` error, successful JSON parsing, and ordinary exception behavior.
The optional simplejson backend is outside this protocol.

The model gets only [prompt.md](prompt.md) and the real upstream files with a fresh
Git baseline. Upstream history, gold patch, controls, held-out checks and their
outputs are not copied into its workspace or sent as recovery context. Acceptance
runs after the attempt in another fresh checkout with the captured candidate patch
applied. All changed files, including tests/untracked files, are retained; production
changes outside `src/requests/exceptions.py` fail the registered scope check.
AskMe is not a sandbox: this separation prevents ordinary evaluator leakage, not
host-level access by an adversarial agent. Network/install restrictions are prompt
policies. The standard runner contains no API keys and is discarded after the job.

**The v1 primary run is already claimed by GitHub run 34175311656, attempt 1.**
The durable [claim](https://github.com/den-run-ai/askme/blob/1cd6a9a0384202791f611f3f3c0fb040863591e8/claim.json)
was committed before that run's first outcome-bearing model request. Never reset
or reuse it. The updated workflow checks out this immutable claim from the explicit
canonical repository and checks run ID, attempt, workflow revision, protocol name,
harness revision and protocol-file digest before building or downloading a model.
A fresh dispatch or label event receives a different ID and is rejected even though
its attempt number is 1. The worker has no API write credentials or claim-reset path.

The already-running original workflow predates this guard. Its frozen runner hash
and evidence remain the record of what executed. Historical workflow revisions
cannot retroactively enforce a newly added check against a maintainer deliberately
dispatching old code; doing that is outside the authorized experiment. V1 is retired
after its sole claimed outcome. Any subsequent inference requires a new versioned
protocol and claim, while retaining this outcome. Ordinary PR updates, pushes and
schedules do not start the trial. The local exclusive marker additionally prevents
overwriting an attempt's records.

The complete artifact retains registration, protocol/prompt/evaluator/runner,
gold and no-op patches, control outputs, raw HTTP requests and response bodies,
all AskMe JSONL telemetry, stdout/stderr, structured terminal result if one exists,
candidate patch, complete workspace, patch-application output, independent acceptance,
actual wall time, observed tokens, API cost (zero), and server/build/hardware logs.
An outer timeout can leave no terminal event; report it as a timeout, never fabricate
a completion. Report observed token totals as lower bounds if the attempt is killed
or transport telemetry is incomplete. Process groups are cleanup, not containment;
detached descendants remain part of AskMe's documented execution limitation.

The revised recorder captures the initial workspace commit before model execution
and diffs against that immutable commit, so a model-created Git commit cannot hide
its repair. It also retains the baseline identity. This correction does not rewrite
the original run's runner or records. If that run committed changes, independently
reconstruct and score the retained workspace against its original upstream tree,
and publish the correction as an explicit scoring amendment without another model run.

Publish the attempt even if it fails. Wording is predeclared:

- Passing applying in-scope patch: “resolved this one predeclared Requests bug,”
  with harness termination reported separately.
- Applying edits without acceptance: “made applying edits but did not resolve.”
- No valid patch, regression, timeout or other failure: “did not resolve,” naming
  the observed failure mode.

Passing this task is not the announcement gate. Retaining a complete, honest
current-code external attempt is the evidence gate in AskMe issue #85.
