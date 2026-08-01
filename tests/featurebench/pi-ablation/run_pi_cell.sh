#!/bin/sh
# pi harness-ablation cell runner (exploratory; NOT a registered canary).
# Usage: run_pi_cell.sh gemma|qwen [smoke]
#
# Runs the pi coding agent on the frozen FeatureBench task inside the pinned
# task image, with strict SiliconFlow routing enforced by a host-side proxy
# (openrouter_pin_proxy.py). The real OPENROUTER_API_KEY never enters the
# container. "smoke" mode uses a trivial prompt and skips the evaluator.
#
# Deviations from the AskMe canary, by design (see README.md in this dir):
# free-form tools (read/write/edit/bash) with NO command guard, no step cap
# (wall-clock cap only), maxTokens 8192, reasoning disabled.
set -eu

CELL="${1:?usage: run_pi_cell.sh gemma|qwen [smoke]}"
MODE="${2:-full}"

ASKME_ROOT="/Users/macmone/code/llama.cpp/agent"
ABLATION_DIR="$ASKME_ROOT/tests/featurebench/pi-ablation"
cd "$ASKME_ROOT"

case "$CELL" in
  gemma) MODEL="google/gemma-4-31b-it";  EXPECTED_SERVED_MODEL="google/gemma-4-31b-it-20260402"; CTX=131072 ;;
  qwen)  MODEL="qwen/qwen3.6-27b";       EXPECTED_SERVED_MODEL="qwen/qwen3.6-27b-20260422";      CTX=262144 ;;
  *) echo "unknown cell: $CELL" >&2; exit 64 ;;
esac

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# ---- Pins (same task cell as the v4 canaries) -------------------------------
TASK_ID="mwaskom__seaborn.7001ebe7.test_algorithms.1f0181c2.lv1"
DATASET_REVISION="e99d6efdfe511ea832c1b5735c536129561ec96a"
IMAGE="docker.io/libercoders/featurebench-specs_seaborn-instance_52738fbb"
IMAGE_DIGEST="sha256:fe27a1a5205fa0939453e79d0f40c74cb402bad8ea1d42fff7de5e2106e77ce1"
PINNED_IMAGE="$IMAGE@$IMAGE_DIGEST"
PROMPT_SHA256="9992981bba2e840bd1cadae55b30040f1ea7a7966e6d5f89ff8f2b194a65ade0"
FEATUREBENCH_ROOT="${FEATUREBENCH_ROOT:-/tmp/FeatureBench}"
FEATUREBENCH_COMMIT="445dcbaec0b2e136061b0acb54e753c0a9f1888e"
PI_VERSION="0.83.0"
NODE_VERSION="v22.23.2"
INNER_TIMEOUT=3540
MAX_TOKENS=8192
PROXY_PORT=8787

RUNS_BASE="${ASKME_FEATUREBENCH_RUNS:-/tmp/askme-featurebench-runs}"
CACHE_DIR="$RUNS_BASE/cache"
RUN_ROOT="$RUNS_BASE/pi-ablation-$CELL-$MODE-$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="$RUN_ROOT/proxy"
CTR="pi-abl-$CELL-$$"
mkdir -p "$RUN_ROOT" "$CACHE_DIR" "$LOG_DIR"

FB_PYTHON="$FEATUREBENCH_ROOT/.venv/bin/python"
FB="$FEATUREBENCH_ROOT/.venv/bin/fb"

echo "=== $(ts) pi-ablation cell=$CELL mode=$MODE model=$MODEL"
echo "=== run_root=$RUN_ROOT"

# ---- [1] Preflight ----------------------------------------------------------
echo "=== $(ts) [1] preflight"
docker info >/dev/null
test "$(git -C "$FEATUREBENCH_ROOT" rev-parse HEAD)" = "$FEATUREBENCH_COMMIT"
test -x "$FB_PYTHON" && test -x "$FB"
OPENROUTER_API_KEY="$(sed -n 's/^OPENROUTER_API_KEY=//p' "$ASKME_ROOT/.env" \
  | head -n 1 | tr -d '\r' | sed -e 's/^"//' -e 's/"$//')"
: "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY missing from .env}"
RUN_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"

if [ ! -s "$CACHE_DIR/node-$NODE_VERSION-linux-x64.tar.gz" ]; then
  echo "=== $(ts) [1] downloading node $NODE_VERSION"
  curl -fsSL -o "$CACHE_DIR/node-$NODE_VERSION-linux-x64.tar.gz" \
    "https://nodejs.org/dist/$NODE_VERSION/node-$NODE_VERSION-linux-x64.tar.gz"
fi

# ---- [2] Prompt from the pinned dataset (hash-verified) ---------------------
echo "=== $(ts) [2] prompt"
if [ "$MODE" = "smoke" ]; then
  printf 'Reply with exactly the word DONE. Do not use any tools.\n' \
    > "$RUN_ROOT/prompt.txt"
else
  DATASET_PATH="$($FB_PYTHON - "$DATASET_REVISION" <<'PY'
import sys
from pathlib import Path
from huggingface_hub import snapshot_download
path = Path(snapshot_download(repo_id="LiberCoders/FeatureBench",
    repo_type="dataset", revision=sys.argv[1])).resolve()
if path.name != sys.argv[1]:
    raise SystemExit(f"unexpected dataset snapshot: {path}")
print(path)
PY
)"
  export DATASET_PATH
  $FB_PYTHON - "$DATASET_PATH" "$TASK_ID" "$PROMPT_SHA256" "$RUN_ROOT/prompt.txt" <<'PY'
import hashlib, sys
from datasets import load_dataset
dataset_path, task_id, want_sha, out = sys.argv[1:]
rows = [r for r in load_dataset(dataset_path, split="fast") if r["instance_id"] == task_id]
assert len(rows) == 1, f"expected one pinned task, found {len(rows)}"
prompt = rows[0]["problem_statement"]
got = hashlib.sha256(prompt.encode()).hexdigest()
assert got == want_sha, f"prompt hash mismatch: {got}"
open(out, "w", encoding="utf-8").write(prompt)
print(f"prompt verified: {len(prompt)} chars")
PY
fi

# ---- [3] Start pin proxy on the host ----------------------------------------
echo "=== $(ts) [3] proxy"
OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
PI_PROXY_SECRET="$RUN_SECRET" \
PI_PROXY_LOG_DIR="$LOG_DIR" \
PI_PROXY_ALLOWED_MODELS="$MODEL" \
PI_PROXY_PORT="$PROXY_PORT" \
  python3 "$ABLATION_DIR/openrouter_pin_proxy.py" \
  > "$RUN_ROOT/proxy-stdout.log" 2>&1 &
PROXY_PID=$!
cleanup() {
  kill "$PROXY_PID" 2>/dev/null || true
  docker rm -f "$CTR" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
sleep 1
curl -fsS "http://127.0.0.1:$PROXY_PORT/health" >/dev/null

# ---- [4] Container with node + pi -------------------------------------------
echo "=== $(ts) [4] container setup"
docker run -d --platform linux/amd64 --name "$CTR" "$PINNED_IMAGE" \
  tail -f /dev/null >/dev/null
docker exec "$CTR" bash -lc 'test -z "$(git -C /testbed status --porcelain)"'
TESTBED_HEAD="$(docker exec "$CTR" git -C /testbed rev-parse HEAD)"
echo "testbed HEAD: $TESTBED_HEAD"

docker cp "$CACHE_DIR/node-$NODE_VERSION-linux-x64.tar.gz" "$CTR:/tmp/node.tar.gz"
docker exec "$CTR" bash -lc \
  'tar -xzf /tmp/node.tar.gz -C /usr/local --strip-components=1 && node --version'
docker exec "$CTR" bash -lc \
  "npm install -g --no-fund --no-audit @earendil-works/pi-coding-agent@$PI_VERSION >/dev/null 2>&1 && pi --version"

docker exec "$CTR" mkdir -p /agent-logs/pi-agent /agent-logs/pi-sessions
python3 - "$MODEL" "$CTX" "$MAX_TOKENS" "$PROXY_PORT" > "$RUN_ROOT/models.json" <<'PY'
import json, sys
model, ctx, max_tokens, port = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
print(json.dumps({"providers": {"openrouter-pinned": {
    "baseUrl": f"http://host.docker.internal:{port}/v1",
    "api": "openai-completions",
    "apiKey": "$PI_PROXY_SECRET",
    "compat": {"supportsDeveloperRole": False, "supportsReasoningEffort": False},
    "models": [{"id": model, "name": f"{model} (pinned via proxy)",
                "reasoning": False, "input": ["text"],
                "contextWindow": ctx, "maxTokens": max_tokens,
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}],
}}}, indent=2))
PY
docker cp "$RUN_ROOT/models.json" "$CTR:/agent-logs/pi-agent/models.json"
docker cp "$RUN_ROOT/prompt.txt" "$CTR:/agent-logs/prompt.txt"

docker exec -e PI_PROXY_SECRET="$RUN_SECRET" "$CTR" bash -lc \
  "python3 -c \"import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:$PROXY_PORT/health', timeout=10).read())\""

# ---- [5] The single pi attempt ----------------------------------------------
echo "=== $(ts) [5] pi attempt (wall cap ${INNER_TIMEOUT}s)"
set +e
docker exec -w /testbed \
  -e PI_PROXY_SECRET="$RUN_SECRET" \
  -e PI_CODING_AGENT_DIR=/agent-logs/pi-agent \
  -e PI_CODING_AGENT_SESSION_DIR=/agent-logs/pi-sessions \
  "$CTR" bash -lc \
  "timeout --signal=TERM --kill-after=15s $INNER_TIMEOUT \
     pi --provider openrouter-pinned --model '$MODEL' --mode json \
        -p \"\$(cat /agent-logs/prompt.txt)\" \
     > /agent-logs/pi-events.ndjson 2> /agent-logs/pi-stderr.log"
PI_EXIT=$?
set -e
printf '%s\n' "$PI_EXIT" > "$RUN_ROOT/pi-exit-code.txt"
echo "pi exit: $PI_EXIT"

# ---- [6] Patch + artifacts --------------------------------------------------
echo "=== $(ts) [6] patch and artifacts"
docker exec "$CTR" git -C /testbed add -A .
docker exec "$CTR" git -C /testbed diff --cached --binary > "$RUN_ROOT/patch.diff"
docker exec "$CTR" git -C /testbed status --porcelain > "$RUN_ROOT/testbed-status.txt"
docker cp "$CTR:/agent-logs" "$RUN_ROOT/agent-logs"
wc -c "$RUN_ROOT/patch.diff"

# ---- [7] Route audit --------------------------------------------------------
# Streaming chunks echo the requested group ID, so the served-model check uses
# OpenRouter's per-generation metadata (authoritative dated model, provider,
# native token counts, billed cost) for every forwarded call.
echo "=== $(ts) [7] route audit"
OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
python3 - "$LOG_DIR" "$EXPECTED_SERVED_MODEL" "$RUN_ROOT/route-audit.json" <<'PY'
import json, os, re, sys, time, urllib.request
from pathlib import Path
log_dir, expected, out = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
key = os.environ["OPENROUTER_API_KEY"]
calls, gen_ids = 0, []
finish, statuses, denied, dropped = {}, {}, 0, set()
streamed = {"prompt": 0, "completion": 0, "cost": 0.0}
for entry in (json.loads(l) for l in open(log_dir / "route-log.jsonl")):
    if entry.get("event") == "denied_model":
        denied += 1
    if entry.get("event") != "forward":
        continue
    calls += 1
    st = entry.get("status", "none")
    statuses[str(st)] = statuses.get(str(st), 0) + 1
    dropped.update(entry.get("dropped_params", []))
    blob = (log_dir / entry["response_file"]).read_bytes().decode("utf-8", "replace")
    m = re.search(r'"id":"(gen-[^"]+)"', blob)
    if m:
        gen_ids.append(m.group(1))
    for line in blob.splitlines():
        line = line.strip()
        if line.startswith("data: ") and line != "data: [DONE]":
            try: f = json.loads(line[6:])
            except ValueError: continue
            u = f.get("usage")
            if u:
                streamed["prompt"] += u.get("prompt_tokens", 0)
                streamed["completion"] += u.get("completion_tokens", 0)
                streamed["cost"] += (u.get("cost") or 0)
            for ch in f.get("choices", []):
                if ch.get("finish_reason"):
                    finish[ch["finish_reason"]] = finish.get(ch["finish_reason"], 0) + 1

served, providers = set(), set()
gen_usage = {"prompt": 0, "completion": 0, "reasoning": 0, "cost": 0.0}
unresolved = []
for gid in gen_ids:
    data = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(
                f"https://openrouter.ai/api/v1/generation?id={gid}",
                headers={"Authorization": f"Bearer {key}"})
            data = json.load(urllib.request.urlopen(req, timeout=30)).get("data")
            if data: break
        except Exception:
            pass
        time.sleep(2 + attempt)
    if not data:
        unresolved.append(gid)
        continue
    served.add(data.get("model"))
    providers.add(data.get("provider_name"))
    gen_usage["prompt"] += data.get("tokens_prompt") or 0
    gen_usage["completion"] += data.get("tokens_completion") or 0
    gen_usage["reasoning"] += data.get("native_tokens_reasoning") or 0
    gen_usage["cost"] += data.get("total_cost") or 0

audit = {
    "calls": calls,
    "statuses": statuses,
    "denied_model_requests": denied,
    "dropped_params": sorted(dropped),
    "generations_resolved": len(gen_ids) - len(unresolved),
    "generations_unresolved": unresolved,
    "served_models": sorted(served),
    "served_providers": sorted(providers),
    "expected_served_model": expected,
    "route_pinned": (
        calls > 0
        and not unresolved
        and len(gen_ids) == calls
        and served == {expected}
        and providers == {"SiliconFlow"}
        and set(statuses) == {"200"}
        and denied == 0
    ),
    "generation_usage": gen_usage,
    "streamed_usage": streamed,
    "finish_reasons": finish,
}
Path(out).write_text(json.dumps(audit, indent=2) + "\n")
print(json.dumps(audit, indent=2))
if not audit["route_pinned"]:
    raise SystemExit("ROUTE PIN VIOLATION (or no successful pinned call)")
PY

# Credential-leak check: real key must appear in zero retained artifacts.
if grep -r -q -- "$OPENROUTER_API_KEY" "$RUN_ROOT" 2>/dev/null; then
  echo "CREDENTIAL LEAK DETECTED in $RUN_ROOT" >&2
  exit 70
fi
echo "credential-leak check: clean"

if [ "$MODE" = "smoke" ]; then
  echo "=== $(ts) smoke done cell=$CELL (evaluator skipped)"
  printf 'Retained run directory: %s\n' "$RUN_ROOT"
  exit 0
fi

# ---- [8] Prediction + official evaluation -----------------------------------
echo "=== $(ts) [8] official evaluation"
EVAL_DIR="$RUN_ROOT/eval"
mkdir -p "$EVAL_DIR"
python3 - "$RUN_ROOT" "$TASK_ID" "$MODEL" "$PI_VERSION" "$PI_EXIT" \
  "$EVAL_DIR/output.jsonl" <<'PY'
import json, sys
from pathlib import Path
run_root, task_id, model, pi_version, pi_exit, out = sys.argv[1:]
patch = Path(run_root, "patch.diff").read_text(encoding="utf-8", errors="replace")
events = Path(run_root, "agent-logs/pi-events.ndjson")
agent_end = False
if events.exists():
    for line in events.read_text(errors="replace").splitlines():
        try:
            if json.loads(line).get("type") == "agent_end":
                agent_end = True
        except ValueError:
            pass
record = {
    "instance_id": task_id,
    "model_patch": patch,
    "agent": f"pi-coding-agent-{pi_version}",
    "model": model,
    "n_attempt": 1,
    "success": pi_exit == "0" and agent_end,
    "task_metadata": {"experiment": "pi-harness-ablation-v1", "not_a_canary": True},
    "error": None,
}
Path(out).write_text(json.dumps(record) + "\n")
print(f"prediction: success={record['success']} patch_bytes={len(patch)}")
PY

(
  cd "$EVAL_DIR"
  "$FB" eval \
    --predictions-path "$EVAL_DIR/output.jsonl" \
    --dataset "$DATASET_PATH" \
    --split fast \
    --task-id "$TASK_ID" \
    --n-concurrent 1 \
    --include-failed
)

REPORT="$EVAL_DIR/eval_outputs/$TASK_ID/attempt-1/report.json"
$FB_PYTHON - "$REPORT" "$TASK_ID" "$RUN_ROOT/joint-outcome.json" \
  "$RUN_ROOT/pi-exit-code.txt" <<'PY'
import json, sys
from pathlib import Path
acc = json.load(open(sys.argv[1]))[sys.argv[2]]
if "error" in acc or "traceback" in acc:
    raise SystemExit(f"official evaluator returned an error: {acc}")
if acc.get("featurebench_eval_completed") is not True:
    raise SystemExit(f"official evaluator did not complete: {acc}")
joint = {
    "agent_completion": Path(sys.argv[4]).read_text().strip() == "0",
    "feature_acceptance": bool(acc.get("resolved")),
    "patch_applied": bool(acc.get("patch_successfully_applied")),
}
Path(sys.argv[3]).write_text(json.dumps(joint, indent=2, sort_keys=True) + "\n")
print(json.dumps(joint, indent=2, sort_keys=True))
PY

echo "=== $(ts) done cell=$CELL"
printf 'Retained run directory: %s\n' "$RUN_ROOT"
