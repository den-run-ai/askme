# AskMe FeatureBench canary runbook

This runbook qualifies one AskMe adapter path through FeatureBench's official
inference runner and evaluator. It is intentionally one task with one model
attempt. The only allowed result label is **one-task FeatureBench fast adapter
canary**. It is not a FeatureBench score, a reliability estimate, a model
comparison, or evidence about model size.

The frozen values and outcome contract are also recorded in
[`canary-protocol.json`](canary-protocol.json).

## Frozen sources and cell

| Field | Frozen value |
|---|---|
| AskMe base | `0dd686c42ee0d325195aec3a500eee4c0ec4c8c9` |
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
| Inference timeout | 3600 seconds |

Run the commands from the AskMe repository root in one shell. Docker must be
running, `git` and `uv` must be available, and `OPENROUTER_API_KEY` must already
be exported in that shell. Do not put the key on a command line, print it, add it
to a result file, or enable shell tracing.

## 1. Preflight and exact checkouts

```sh
set -eu

ASKME_ROOT="$(pwd -P)"
ASKME_BASE="0dd686c42ee0d325195aec3a500eee4c0ec4c8c9"
FEATUREBENCH_ROOT="${FEATUREBENCH_ROOT:-/tmp/FeatureBench}"
FEATUREBENCH_COMMIT="445dcbaec0b2e136061b0acb54e753c0a9f1888e"
DATASET_REVISION="e99d6efdfe511ea832c1b5735c536129561ec96a"
TASK_ID="mwaskom__seaborn.7001ebe7.test_algorithms.1f0181c2.lv1"
MODEL="google/gemma-4-31b-it"
PROVIDER="siliconflow"
IMAGE_NAME="libercoders/featurebench-specs_seaborn-instance_52738fbb"
IMAGE="docker.io/$IMAGE_NAME"
IMAGE_DIGEST="sha256:fe27a1a5205fa0939453e79d0f40c74cb402bad8ea1d42fff7de5e2106e77ce1"
PINNED_IMAGE="$IMAGE@$IMAGE_DIGEST"
FEATUREBENCH_CACHE="${FEATUREBENCH_CACHE:-$HOME/.cache/featurebench}"
RUNS_BASE="${ASKME_FEATUREBENCH_RUNS:-/tmp/askme-featurebench-runs}"
RUN_ROOT="$RUNS_BASE/canary-$(date -u +%Y%m%dT%H%M%SZ)"

: "${OPENROUTER_API_KEY:?Export OPENROUTER_API_KEY before running the canary}"
docker info >/dev/null
mkdir -p "$FEATUREBENCH_CACHE" "$RUN_ROOT"

test -z "$(git -C "$ASKME_ROOT" status --porcelain)"
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
`askme.py` to match that base exactly.

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

$FB_PYTHON - "$DATASET_PATH" "$TASK_ID" "$IMAGE_NAME" <<'PY'
import sys
from datasets import load_dataset

dataset_path, task_id, image_name = sys.argv[1:]
dataset = load_dataset(dataset_path, split="fast")
matches = [row for row in dataset if row["instance_id"] == task_id]
if len(matches) != 1:
    raise SystemExit(f"expected one pinned task, found {len(matches)}")
if matches[0]["image_name"] != image_name:
    raise SystemExit("pinned task image does not match the protocol")
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
if report["total_instances"] != 1 or report["resolved_instances"] != 1:
    raise SystemExit(f"gold control did not resolve: {report}")
print("Gold control: resolved")
PY
```

## 5. Run the harmless nonempty control

Generate its prediction JSONL directly from the frozen protocol. The patch only
adds `ASKME_FEATUREBENCH_CONTROL.txt`; it must apply but must not resolve the
feature task.

```sh
PROTOCOL="$ASKME_ROOT/tests/featurebench/canary-protocol.json"
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
if not result["patch_successfully_applied"] or result["resolved"]:
    raise SystemExit(f"harmless control violated expectations: {result}")
print("Harmless nonempty control: applied and unresolved")
PY
```

## 6. Run the single AskMe adapter attempt

The adapter passes the full pinned problem statement by file, runs AskMe in
`/testbed`, requests `gated` reasoning, and sets strict SiliconFlow routing with
provider fallback disabled. It retains AskMe's structured result, run log,
stdout, exact prompt, adapter manifest, and FeatureBench prediction.

Do not rerun or replace the attempt after the first model response. The command
may exit nonzero when AskMe reports incomplete; retain that exit and continue to
official evaluation if `output.jsonl` exists.

```sh
INFERENCE_ROOT="$RUN_ROOT/inference"
mkdir -p "$INFERENCE_ROOT"

if "$FB_PYTHON" "$ASKME_ROOT/tests/featurebench/askme_adapter.py" \
  --featurebench-root "$FEATUREBENCH_ROOT" \
  --featurebench-revision "$FEATUREBENCH_COMMIT" \
  --askme-path "$ASKME_ROOT/askme.py" \
  --dataset-path "$DATASET_PATH" \
  --dataset-revision "$DATASET_REVISION" \
  --output-dir "$INFERENCE_ROOT" \
  --cache-dir "$FEATUREBENCH_CACHE" \
  --task-id "$TASK_ID" \
  --model "$MODEL" \
  --provider "$PROVIDER" \
  --split fast \
  --timeout 3600; then
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

An infrastructure failure that produces no prediction is not a model result.
Stop and retain the logs rather than manufacturing an empty prediction.

## 7. Run official acceptance, including failed inference records

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

ASKME_RESULT="$(find "$ADAPTER_RUN_DIR/run_outputs" \
  -name askme-result.json -type f -print | head -n 1)"
ACCEPTANCE_REPORT="$ADAPTER_RUN_DIR/eval_outputs/$TASK_ID/attempt-1/report.json"
test -s "$ASKME_RESULT"
test -s "$ACCEPTANCE_REPORT"

$FB_PYTHON - "$ASKME_RESULT" "$ACCEPTANCE_REPORT" "$TASK_ID" <<'PY'
import json
import sys

agent = json.load(open(sys.argv[1], encoding="utf-8"))
acceptance = json.load(open(sys.argv[2], encoding="utf-8"))[sys.argv[3]]
joint = {
    "agent_completion": agent.get("status") == "complete",
    "feature_acceptance": bool(acceptance.get("resolved")),
    "patch_applied": bool(acceptance.get("patch_successfully_applied")),
}
print(json.dumps(joint, indent=2, sort_keys=True))
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
wall time, tokens, billed cost, joint outcome, and retained artifact paths. Do
not report an aggregate percentage, compare this cell with published systems,
or call it a benchmark result.
