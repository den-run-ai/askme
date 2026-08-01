# AskMe FeatureBench canary runbook

This runbook qualifies one AskMe adapter path through FeatureBench's official
inference runner and evaluator. It is intentionally one task with one model
attempt. The only allowed result label is **one-task FeatureBench fast adapter
canary**. It is not a FeatureBench score, a reliability estimate, a model
comparison, or evidence about model size.

The frozen values and outcome contract are also recorded in
[`canary-protocol.json`](canary-protocol.json).

> **Protocol-versioning warning:** the post-run guard, exact-route, and endpoint
> catalog hardening now present on this branch is not part of frozen canary v2.
> Exact v2 inspection or replay requires checkout `29ba811`; do not combine the
> current adapter or audit code with v2's registered file hashes. Any future
> outcome-bearing run must use a separately registered protocol version with a
> new adapter revision and updated hashes before the first model response.

## v4 registration (revision-2 action interface)

Protocol v3 was consumed by the 2026-07-31 Qwen 3.6 27B canary. After that
result, the AskMe action interface changed (issue #7, protocol revision 2 in
[`../workflows/PROTOCOL.md`](../workflows/PROTOCOL.md)): ranged reads with
continuation metadata, bounded `search`/`tree`, chunked `append` writes, typed
`malformed_action`/`response_truncated` failures, and selected-vs-executed
step accounting. Two v4 protocols are registered for the rerun of the same
frozen task under that interface, one per model cell:

- [`gemma-31b-canary-protocol-v4.json`](gemma-31b-canary-protocol-v4.json) —
  `google/gemma-4-31b-it`, expected served `google/gemma-4-31b-it-20260402`
- [`qwen-27b-canary-protocol-v4.json`](qwen-27b-canary-protocol-v4.json) —
  `qwen/qwen3.6-27b`, expected served `qwen/qwen3.6-27b-20260422`

Both pin AskMe base `44574fb` (`askme.py` sha256 `bc677b32…`), the unchanged
hardened adapter/audit hashes, and the same task, dataset revision, image
digest, budgets, and timeouts as v2/v3. The gold and harmless controls were
requalified under this interface before any model call; see
[`results/2026-08-01-v4-control-requalification.json`](results/2026-08-01-v4-control-requalification.json).
The clean merge commit containing the v4 protocols, `72b78c2`, was the
execution revision passed as `--askme-revision`. Both registered attempts have
now been consumed; nothing in this section authorizes any further attempt under
these protocols.

### v4 outcomes (2026-08-01)

Both cells ran their single attempt on 2026-08-01 and were fully qualified:
gold control resolved, harmless control applied and unresolved, exactly one
prediction serialized per cell, deterministic audits
`valid_infrastructure_policy_compliant`, and official acceptance categorical
with no evaluator error. Every token event in both cells was served by
SiliconFlow as the exact preregistered dated model.

- **Gemma 4 31B** — agent `exhausted`, empty patch, unresolved. 16 of 33
  responses hit the output-token cap; under the revision-2 interface these
  surfaced as typed `response_truncated` step failures instead of malformed
  JSON. No write or edit was ever executed.
  Record: [`results/2026-08-01-gemma-4-31b-canary-v4.json`](results/2026-08-01-gemma-4-31b-canary-v4.json).
- **Qwen3.6 27B** — agent `exhausted`, empty patch, unresolved, with zero
  parse failures: all 27 executed steps were observation actions (tree/read);
  the model never attempted a write before its budgets ran out.
  Record: [`results/2026-08-01-qwen36-27b-canary-v4.json`](results/2026-08-01-qwen36-27b-canary-v4.json).

Each is one supported negative canary of the revision-2 action interface on
one frozen task — not a FeatureBench score, a reliability estimate, or a
model comparison.

## Completed canary outcome

The frozen run completed on 2026-07-13. Both qualification controls behaved as
required: the untouched gold patch resolved the task under Docker Rosetta, and
the harmless nonempty patch applied without resolving it. The only model
attempt used the exact dated Gemma 4 31B endpoint on SiliconFlow and passed the
adapter's infrastructure, route, policy, and credential-leak audits. The agent
then exhausted three bounded planning attempts after repeated implementation
writes were truncated or returned malformed JSON. It produced an empty patch,
and official acceptance recorded the task as unresolved with no evaluator
error.

The compact, hash-linked record is
[`results/2026-07-13-gemma-4-31b-canary.json`](results/2026-07-13-gemma-4-31b-canary.json).
This is a supported negative canary of the frozen action protocol on one task.
It is not a FeatureBench score, a reliability estimate, a model-family or
model-size result, or a causal estimate of the reasoning policy. No replacement
model attempt was made.

## Frozen sources and cell

| Field | Frozen value |
|---|---|
| AskMe base | `0dd686c42ee0d325195aec3a500eee4c0ec4c8c9` |
| AskMe adapter code | `9e0937676daf989f14229fa39ef96fd9f88abe26` |
| AskMe execution revision | Registered externally in PR #6 before inference; the clean checkout must match it exactly |
| FeatureBench commit | `445dcbaec0b2e136061b0acb54e753c0a9f1888e` |
| Dataset repository | `LiberCoders/FeatureBench` |
| Dataset revision | `e99d6efdfe511ea832c1b5735c536129561ec96a` |
| Split | `fast` |
| Task | `mwaskom__seaborn.7001ebe7.test_algorithms.1f0181c2.lv1` |
| Image | `docker.io/libercoders/featurebench-specs_seaborn-instance_52738fbb` |
| Image digest | `sha256:fe27a1a5205fa0939453e79d0f40c74cb402bad8ea1d42fff7de5e2106e77ce1` |
| Model | `google/gemma-4-31b-it` |
| Provider route | `siliconflow`, no fallback, required parameters enabled |
| AskMe policy | `gated` |
| Attempts | 1 |
| Inner AskMe timeout | 3540 seconds |
| Outer FeatureBench timeout | 3600 seconds |

Run the commands from the AskMe repository root in one shell. Docker must be
running, `git` and `uv` must be available, and `OPENROUTER_API_KEY` must already
be exported in that shell. Do not put the key on a command line, print it, add it
to a result file, or enable shell tracing.

## 1. Preflight and exact checkouts

```sh
set -eu

ASKME_ROOT="$(pwd -P)"
ASKME_BASE="0dd686c42ee0d325195aec3a500eee4c0ec4c8c9"
ASKME_ADAPTER_CODE_COMMIT="9e0937676daf989f14229fa39ef96fd9f88abe26"
: "${ASKME_RUN_COMMIT:?Export the execution commit registered in PR #6}"
FEATUREBENCH_ROOT="${FEATUREBENCH_ROOT:-/tmp/FeatureBench}"
FEATUREBENCH_COMMIT="445dcbaec0b2e136061b0acb54e753c0a9f1888e"
DATASET_REVISION="e99d6efdfe511ea832c1b5735c536129561ec96a"
TASK_ID="mwaskom__seaborn.7001ebe7.test_algorithms.1f0181c2.lv1"
MODEL="google/gemma-4-31b-it"
EXPECTED_SERVED_MODEL="google/gemma-4-31b-it-20260402"
PROVIDER="siliconflow"
IMAGE_NAME="libercoders/featurebench-specs_seaborn-instance_52738fbb"
IMAGE="docker.io/$IMAGE_NAME"
IMAGE_DIGEST="sha256:fe27a1a5205fa0939453e79d0f40c74cb402bad8ea1d42fff7de5e2106e77ce1"
PINNED_IMAGE="$IMAGE@$IMAGE_DIGEST"
FEATUREBENCH_CACHE="${FEATUREBENCH_CACHE:-$HOME/.cache/featurebench}"
RUNS_BASE="${ASKME_FEATUREBENCH_RUNS:-/tmp/askme-featurebench-runs}"
RUN_ROOT="$RUNS_BASE/canary-$(date -u +%Y%m%dT%H%M%SZ)"
PROTOCOL="$ASKME_ROOT/tests/featurebench/canary-protocol.json"
INNER_TIMEOUT=3540
OUTER_TIMEOUT=3600

: "${OPENROUTER_API_KEY:?Export OPENROUTER_API_KEY before running the canary}"
docker info >/dev/null
mkdir -p "$FEATUREBENCH_CACHE" "$RUN_ROOT"

test -z "$(git -C "$ASKME_ROOT" status --porcelain)"
test "$(git -C "$ASKME_ROOT" rev-parse HEAD)" = "$ASKME_RUN_COMMIT"
git -C "$ASKME_ROOT" merge-base --is-ancestor \
  "$ASKME_ADAPTER_CODE_COMMIT" "$ASKME_RUN_COMMIT"
test "$(git -C "$ASKME_ROOT" hash-object askme.py)" = \
  "$(git -C "$ASKME_ROOT" rev-parse "$ASKME_BASE:askme.py")"

if [ ! -d "$FEATUREBENCH_ROOT/.git" ]; then
  git clone --filter=blob:none \
    https://github.com/LiberCoders/FeatureBench.git "$FEATUREBENCH_ROOT"
fi
git -C "$FEATUREBENCH_ROOT" fetch --filter=blob:none origin "$FEATUREBENCH_COMMIT"
git -C "$FEATUREBENCH_ROOT" checkout --detach "$FEATUREBENCH_COMMIT"
test "$(git -C "$FEATUREBENCH_ROOT" rev-parse HEAD)" = "$FEATUREBENCH_COMMIT"
test -z "$(git -C "$FEATUREBENCH_ROOT" status --porcelain)"

(
  cd "$FEATUREBENCH_ROOT"
  uv sync --frozen
)
test -z "$(git -C "$FEATUREBENCH_ROOT" status --porcelain)"
FB_PYTHON="$FEATUREBENCH_ROOT/.venv/bin/python"
FB="$FEATUREBENCH_ROOT/.venv/bin/fb"
test -x "$FB_PYTHON"
test -x "$FB"
```

The clean AskMe-tree check is deliberate: commit the adapter and protocol before
the first model response. Run artifacts stay outside the checkout because the
adapter rechecks cleanliness immediately before inference. The blob check
permits adapter code on top of the frozen base while requiring the executed
`askme.py` to match that base exactly. The protocol pins the adapter/audit file
hashes at the adapter-code commit. Because a commit cannot contain its own hash,
the later clean execution revision is registered in PR #6 before inference and
supplied separately as `ASKME_RUN_COMMIT`.

### Security boundary

The adapter adds an auditable, best-effort command-level guard at AskMe's action
dispatcher. Direct `read`, `write`, and `edit` actions must resolve inside
`/testbed`; shell action
text is rejected for the adapter/log paths, parent traversal, environment
disclosure, network clients, remote Git operations, and package installation.
The launcher removes the OpenRouter credential from its process environment
after loading AskMe, so model-issued child commands do not inherit the key. The
guard decision log is retained for audit.

Because this is a conservative text filter, some safe commands can be denied.
In particular, a shell command beginning with `set ` (including `set -eu`) is
treated as possible environment disclosure. Literal URLs, network-client names,
environment-access expressions, or adapter paths in comments, test data,
heredocs, and source text can also look like prohibited behavior. A standalone
`..` path segment is denied whether it is bare, separated by whitespace, or
uses `/` or `\\` separators. Treat these denials as guard false positives to
audit, not as evidence that the model attempted exfiltration.

This is defense in depth, not a container network sandbox. The container keeps
default outbound connectivity because AskMe must call OpenRouter; container
egress is **not isolated**. The command filter is not a general shell parser or
an adversarial-code sandbox. This canary therefore trusts the pinned
FeatureBench source, dataset row, repository image, and tests. Do not use this
adapter unchanged on an untrusted repository or task.

## 2. Download and verify the pinned dataset snapshot

FeatureBench's evaluator does not expose a dataset-revision flag. Download the
exact Hugging Face revision and pass its local path to every inference and
evaluation command.

```sh
DATASET_PATH="$($FB_PYTHON - "$DATASET_REVISION" <<'PY'
import sys
from pathlib import Path
from huggingface_hub import snapshot_download

revision = sys.argv[1]
path = Path(snapshot_download(
    repo_id="LiberCoders/FeatureBench",
    repo_type="dataset",
    revision=revision,
)).resolve()
if path.name != revision:
    raise SystemExit(f"unexpected dataset snapshot: {path}")
print(path)
PY
)"
export DATASET_PATH

$FB_PYTHON - "$DATASET_PATH" "$TASK_ID" "$IMAGE_NAME" "$PROTOCOL" <<'PY'
import hashlib
import json
import sys
from datasets import load_dataset

dataset_path, task_id, image_name, protocol_path = sys.argv[1:]
protocol = json.load(open(protocol_path, encoding="utf-8"))["sources"]["dataset"]
dataset = load_dataset(dataset_path, split="fast")
matches = [row for row in dataset if row["instance_id"] == task_id]
if len(matches) != 1:
    raise SystemExit(f"expected one pinned task, found {len(matches)}")
if matches[0]["image_name"] != image_name:
    raise SystemExit("pinned task image does not match the protocol")
prompt = matches[0]["problem_statement"]
if len(prompt) != protocol["problem_statement_chars"]:
    raise SystemExit("pinned task prompt length does not match the protocol")
if hashlib.sha256(prompt.encode()).hexdigest() != protocol["problem_statement_sha256"]:
    raise SystemExit("pinned task prompt hash does not match the protocol")
print(f"Pinned dataset verified: {task_id}")
PY
```

## 3. Pull and tag the digest-pinned image

FeatureBench looks up the dataset's unqualified image name. Pull the immutable
digest first, then give that exact local image the name FeatureBench expects.

```sh
docker pull --platform linux/amd64 "$PINNED_IMAGE"
docker tag "$PINNED_IMAGE" "$IMAGE"

PINNED_IMAGE_ID="$(docker image inspect "$PINNED_IMAGE" --format '{{.Id}}')"
TAGGED_IMAGE_ID="$(docker image inspect "$IMAGE" --format '{{.Id}}')"
test "$PINNED_IMAGE_ID" = "$TAGGED_IMAGE_ID"
test "$(docker image inspect "$IMAGE" --format '{{.Architecture}}')" = "amd64"
```

The pinned task image is `linux/amd64`. On an Apple Silicon Mac, Docker Desktop
must support x86_64 emulation, and inference or tests may be slower than on a
native x86_64 host. Check `docker system df` and `df -h /tmp` before starting.
The image, writable container layers, dataset cache, and retained logs can need
substantial free disk space. This runbook does not delete or prune existing
Docker data.

## 4. Run the official gold control

The gold patch must resolve the pinned task before any model call. Running from a
dedicated directory keeps FeatureBench's fixed `runs/gold` output isolated.

```sh
GOLD_DIR="$RUN_ROOT/gold"
mkdir -p "$GOLD_DIR"
(
  cd "$GOLD_DIR"
  "$FB" eval \
    --predictions-path gold \
    --dataset "$DATASET_PATH" \
    --split fast \
    --task-id "$TASK_ID" \
    --n-concurrent 1
)

GOLD_REPORT="$GOLD_DIR/runs/gold/report.json"
$FB_PYTHON - "$GOLD_REPORT" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))["attempt_1"]
if (
    report["total_instances"] != 1
    or report["completed_instances"] != 1
    or report["resolved_instances"] != 1
    or report["error_instances"] != 0
):
    raise SystemExit(f"gold control did not resolve: {report}")
print("Gold control: resolved")
PY
```

## 5. Run the harmless nonempty control

Generate its prediction JSONL directly from the frozen protocol. The patch only
adds `ASKME_FEATUREBENCH_CONTROL.txt`; it must apply but must not resolve the
feature task.

```sh
CONTROL_DIR="$RUN_ROOT/control"
CONTROL_JSONL="$CONTROL_DIR/output.jsonl"
mkdir -p "$CONTROL_DIR"

$FB_PYTHON - "$PROTOCOL" "$CONTROL_JSONL" <<'PY'
import json
import sys
from pathlib import Path

protocol = json.load(open(sys.argv[1], encoding="utf-8"))
prediction = protocol["sequence"][1]["prediction"]
record = {
    key: prediction[key]
    for key in (
        "instance_id", "model_patch", "agent", "model", "n_attempt", "success"
    )
}
record["task_metadata"] = {"control": "harmless_nonempty"}
record["error"] = None
Path(sys.argv[2]).write_text(json.dumps(record) + "\n", encoding="utf-8")
PY

"$FB" eval \
  --predictions-path "$CONTROL_JSONL" \
  --dataset "$DATASET_PATH" \
  --split fast \
  --task-id "$TASK_ID" \
  --n-concurrent 1 \
  --include-failed

CONTROL_REPORT="$CONTROL_DIR/eval_outputs/$TASK_ID/attempt-1/report.json"
$FB_PYTHON - "$CONTROL_REPORT" "$TASK_ID" <<'PY'
import json
import sys

result = json.load(open(sys.argv[1], encoding="utf-8"))[sys.argv[2]]
if "error" in result or "traceback" in result:
    raise SystemExit(f"harmless control evaluator error: {result}")
if result.get("featurebench_eval_completed") is not True:
    raise SystemExit(f"harmless control evaluator did not complete: {result}")
if not result["patch_successfully_applied"] or result["resolved"]:
    raise SystemExit(f"harmless control violated expectations: {result}")
print("Harmless nonempty control: applied and unresolved")
PY
```

## 6. Run the single AskMe adapter attempt

The adapter passes the full pinned problem statement by file, runs AskMe in
`/testbed`, requests `gated` reasoning, and sets strict SiliconFlow routing with
provider fallback disabled. It retains AskMe's structured result, run log,
policy decisions, stdout, exact prompt, adapter manifest, and FeatureBench
prediction.

The budgets mean at most three total planning attempts across the run, at most
ten tasks in each plan, and at most ten steps in each task attempt. AskMe's
frozen `MAX_TASK_LOCAL_REPLANS=1` permits one replacement after an unsuccessful
task attempt, so a planned task can have at most two task attempts. These are
caps, not expected usage.

The adapter derives a 3540-second inner launcher timeout from the frozen
3600-second FeatureBench timeout. That 60-second margin lets the launcher write
a terminal result and preserve artifacts before the outer runner timeout. Do
not rerun or replace the attempt after inference starts, including after a
timeout or infrastructure failure. Retain the partial artifacts and report the
failure category.

Immediately before `InferenceRunner.run()`, the adapter performs an
authenticated, read-only GET of OpenRouter's per-model endpoint catalog. This
is not a chat-completion request and produces no model response. A
credential-free record named `openrouter-endpoint-catalog-preflight.json` is
retained in the run directory. In future canaries, inference is gated on the
catalog exposing exactly one pinned-provider endpoint for every preregistered
dated served-model ID; the post-run token log must then report those exact IDs.
This gate was added after the completed v2 canary and does not rewrite that
frozen run.

```sh
INFERENCE_ROOT="$RUN_ROOT/inference"
mkdir -p "$INFERENCE_ROOT"

if "$FB_PYTHON" "$ASKME_ROOT/tests/featurebench/askme_adapter.py" \
  --featurebench-root "$FEATUREBENCH_ROOT" \
  --featurebench-revision "$FEATUREBENCH_COMMIT" \
  --askme-path "$ASKME_ROOT/askme.py" \
  --askme-revision "$ASKME_RUN_COMMIT" \
  --protocol-path "$PROTOCOL" \
  --dataset-path "$DATASET_PATH" \
  --dataset-revision "$DATASET_REVISION" \
  --output-dir "$INFERENCE_ROOT" \
  --cache-dir "$FEATUREBENCH_CACHE" \
  --task-id "$TASK_ID" \
  --model "$MODEL" \
  --expected-served-model "$EXPECTED_SERVED_MODEL" \
  --provider "$PROVIDER" \
  --split fast \
  --timeout "$OUTER_TIMEOUT"; then
  ADAPTER_EXIT=0
else
  ADAPTER_EXIT=$?
fi
printf '%s\n' "$ADAPTER_EXIT" > "$RUN_ROOT/adapter-exit-code.txt"

ADAPTER_RUN_DIR="$(find "$INFERENCE_ROOT" -mindepth 1 -maxdepth 1 \
  -type d -print | sort | tail -n 1)"
PREDICTIONS="$ADAPTER_RUN_DIR/output.jsonl"
test -s "$PREDICTIONS"
printf 'Adapter exit: %s\nPredictions: %s\n' "$ADAPTER_EXIT" "$PREDICTIONS"
```

FeatureBench's `InferenceRunner.run()` returns zero when its pipeline finishes,
even when the only inference record has `success=false`. Therefore
`ADAPTER_EXIT=0` is not evidence that AskMe completed. The structured AskMe
result and the single `output.jsonl` record determine agent completion. The
adapter returns `2` for an invalid deterministic audit and `3` for valid
infrastructure with a recorded policy denial; other nonzero values retain the
runner/process failure or interruption. None authorizes a replacement attempt.

An infrastructure failure that produces no prediction is not a model result.
Stop and retain the logs rather than manufacturing an empty prediction or
starting a replacement attempt.

## 7. Run the deterministic post-run audit

The adapter automatically writes its audit to
`$ADAPTER_RUN_DIR/askme-canary-audit.json`. Inspect that retained result, then
independently rerun the same audit against the registered execution revision.
`ASKME_RUN_COMMIT` must be the immutable commit registered outside this run;
do not derive it from the current checkout or substitute a self-referential
adapter revision.

```sh
ATTEMPT_DIR="$ADAPTER_RUN_DIR/run_outputs/$TASK_ID/attempt-1"
ASKME_RESULT="$ATTEMPT_DIR/askme-result.json"
AUTO_AUDIT="$ADAPTER_RUN_DIR/askme-canary-audit.json"
POST_RUN_AUDIT="$RUN_ROOT/deterministic-post-run-audit.json"
: "${ASKME_RUN_COMMIT:?Export the externally registered execution commit}"

if test -s "$AUTO_AUDIT" && "$FB_PYTHON" -m json.tool "$AUTO_AUDIT"; then
  AUTO_AUDIT_EXIT=0
else
  AUTO_AUDIT_EXIT=$?
fi

if "$FB_PYTHON" "$ASKME_ROOT/tests/featurebench/canary_audit.py" \
  "$ADAPTER_RUN_DIR" \
  --protocol "$PROTOCOL" \
  --askme-source "$ASKME_ROOT/askme.py" \
  --code-root "$ASKME_ROOT" \
  --expected-served-model "$EXPECTED_SERVED_MODEL" \
  --expected-run-revision "$ASKME_RUN_COMMIT" \
  --output "$POST_RUN_AUDIT"; then
  POST_RUN_AUDIT_EXIT=0
else
  POST_RUN_AUDIT_EXIT=$?
fi

if [ "$AUTO_AUDIT_EXIT" -ne 0 ] || [ "$POST_RUN_AUDIT_EXIT" -ne 0 ]; then
  AUDIT_CLASS="invalid_infrastructure"
elif "$FB_PYTHON" -c \
  'import json, sys; raise SystemExit(not json.load(open(sys.argv[1]))["policy_compliant"])' \
  "$POST_RUN_AUDIT"; then
  AUDIT_CLASS="valid_infrastructure_policy_compliant"
else
  AUDIT_CLASS="valid_infrastructure_policy_denial"
fi
printf 'Automatic audit inspection exit: %s\nPost-run audit exit: %s\nClassification: %s\n' \
  "$AUTO_AUDIT_EXIT" "$POST_RUN_AUDIT_EXIT" "$AUDIT_CLASS"
```

An invalid-infrastructure result or a policy denial disqualifies the adapter
run, but neither authorizes another attempt. Preserve the artifacts and
continue exactly once to the official evaluator in Section 8 so acceptance is
still recorded for the retained prediction.

## 8. Run official acceptance, including failed inference records

`--include-failed` is mandatory. Without it, FeatureBench skips a prediction
whose inference record has `success=false`, which would hide an incomplete AskMe
trajectory instead of evaluating its delivered patch.

```sh
"$FB" eval \
  --predictions-path "$PREDICTIONS" \
  --dataset "$DATASET_PATH" \
  --split fast \
  --task-id "$TASK_ID" \
  --n-concurrent 1 \
  --include-failed

ACCEPTANCE_REPORT="$ADAPTER_RUN_DIR/eval_outputs/$TASK_ID/attempt-1/report.json"
JOINT_OUTCOME="$RUN_ROOT/joint-outcome.json"
test -s "$ASKME_RESULT"
test -s "$ACCEPTANCE_REPORT"

$FB_PYTHON - \
  "$ASKME_RESULT" "$ACCEPTANCE_REPORT" "$TASK_ID" "$JOINT_OUTCOME" <<'PY'
import json
import sys
from pathlib import Path

agent = json.load(open(sys.argv[1], encoding="utf-8"))
acceptance = json.load(open(sys.argv[2], encoding="utf-8"))[sys.argv[3]]
if "error" in acceptance or "traceback" in acceptance:
    raise SystemExit(f"official evaluator returned an error: {acceptance}")
if acceptance.get("featurebench_eval_completed") is not True:
    raise SystemExit(f"official evaluator did not complete: {acceptance}")
joint = {
    "agent_completion": agent.get("status") in {
        "complete", "complete_deterministic_after_exhausted"
    },
    "feature_acceptance": bool(acceptance.get("resolved")),
    "patch_applied": bool(acceptance.get("patch_successfully_applied")),
}
rendered = json.dumps(joint, indent=2, sort_keys=True) + "\n"
Path(sys.argv[4]).write_text(rendered, encoding="utf-8")
print(rendered, end="")
PY

printf 'Retained run directory: %s\n' "$RUN_ROOT"
```

## Reporting boundary

Report AskMe completion and FeatureBench acceptance independently. `complete`
does not mean the feature passed, and an accepted artifact remains accepted even
if AskMe did not report completion. Adapter qualification requires the gold
control to resolve, the harmless patch to apply and remain unresolved, the only
model attempt to be serialized faithfully, and the official evaluator to return
a categorical outcome. Qualification does not require the model patch to pass.

Record the clean AskMe run commit, adapter revision, FeatureBench commit, dataset
revision, image digest, requested and served model/provider, reasoning policy,
wall time, tokens, billed cost, runner exit, deterministic-audit result, joint
outcome, and retained artifact paths. An evaluator report containing `error` or
`traceback` is an infrastructure/evaluator failure, never an unresolved model
result. Do not report an aggregate percentage, compare this cell with published
systems, or call it a benchmark result.
