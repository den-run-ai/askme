#!/usr/bin/env bash
# bench_kv.sh — Benchmark KV cache modes for NanAgent on llama-server.
#
# Usage:
#   ./scripts/bench_kv.sh q4_0          # single run with q4_0 KV
#   ./scripts/bench_kv.sh f16 3         # 3 trials with f16 KV
#   ./scripts/bench_kv.sh all 3         # 3 trials each for f16, q8_0, q4_0
#
# Requires: llama-server binary built, model file present.
# Outputs: benchmarks/kv_<mode>_<timestamp>.json per trial.
#
# The script manages the server lifecycle: starts, waits for health,
# runs easy integration tests, captures results, and stops the server.
# If a server is already running on the target port, it exits with an error.

set -euo pipefail

LLAMA_ROOT="/Users/macmone/code/llama.cpp"
MODEL="models/gemma4-e4b/gemma-4-e4b-it-Q4_K_M.gguf"
AGENT_DIR="$LLAMA_ROOT/agent"
SERVER="$LLAMA_ROOT/build/bin/llama-server"
PORT=8080
CTX_SIZE=16384
HEALTH_URL="http://localhost:$PORT/health"
HEALTH_TIMEOUT=60
BENCHMARKS_DIR="$AGENT_DIR/benchmarks"

# Test filter for easy integration suite
TEST_FILTER="TestIntegration and not Medium and not Hard"

die() { echo "ERROR: $*" >&2; exit 1; }

# --- Parse args ---

KV_MODE="${1:-}"
TRIALS="${2:-1}"

if [ -z "$KV_MODE" ]; then
    echo "Usage: $0 <kv_mode|all> [trials]"
    echo "  kv_mode: f16, q8_0, q4_0, or 'all'"
    echo "  trials:  number of runs per mode (default: 1)"
    exit 1
fi

if [ "$KV_MODE" = "all" ]; then
    MODES=(f16 q8_0 q4_0)
else
    MODES=("$KV_MODE")
fi

# Validate mode
for m in "${MODES[@]}"; do
    case "$m" in
        f16|q8_0|q4_0) ;;
        *) die "Unknown KV mode: $m (expected f16, q8_0, or q4_0)" ;;
    esac
done

# --- Preflight checks ---

[ -x "$SERVER" ] || die "Server not found: $SERVER (run cmake --build)"
[ -f "$LLAMA_ROOT/$MODEL" ] || die "Model not found: $LLAMA_ROOT/$MODEL"

# Fail if server is already running on the port
if curl -s --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
    die "Server already running on port $PORT. Stop it first."
fi

mkdir -p "$BENCHMARKS_DIR"

# Capture llama.cpp build SHA
BUILD_SHA=$(cd "$LLAMA_ROOT" && git rev-parse --short HEAD)

# --- Server lifecycle ---

SERVER_PID=""

start_server() {
    local kv_mode="$1"
    local kv_flags=""

    if [ "$kv_mode" != "f16" ]; then
        kv_flags="--cache-type-k $kv_mode --cache-type-v $kv_mode"
    fi

    echo "Starting server: kv=$kv_mode ctx=$CTX_SIZE port=$PORT"

    # Capture startup log for KV mode verification
    local server_log="$BENCHMARKS_DIR/.server_${kv_mode}.log"

    $SERVER \
        -m "$LLAMA_ROOT/$MODEL" \
        -ngl 99 --ctx-size "$CTX_SIZE" --flash-attn on \
        $kv_flags \
        -np 1 --slot-save-path /tmp/llama-cache \
        --port "$PORT" \
        >"$server_log" 2>&1 &
    SERVER_PID=$!

    # Wait for health
    local waited=0
    while ! curl -s --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; do
        sleep 1
        waited=$((waited + 1))
        if [ $waited -ge $HEALTH_TIMEOUT ]; then
            kill "$SERVER_PID" 2>/dev/null || true
            die "Server did not become healthy within ${HEALTH_TIMEOUT}s"
        fi
        # Check server hasn't crashed
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            die "Server process exited unexpectedly. Check $server_log"
        fi
    done

    echo "Server healthy after ${waited}s (pid=$SERVER_PID)"

    # Verify KV mode from startup log
    if [ "$kv_mode" != "f16" ]; then
        if grep -q "cache_type_k.*$kv_mode" "$server_log" 2>/dev/null || \
           grep -q "KV.*$kv_mode" "$server_log" 2>/dev/null; then
            echo "KV mode verified in server log: $kv_mode"
        else
            echo "WARNING: Could not verify KV mode in server log (server API does not expose cache type)"
        fi
    fi
}

stop_server() {
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "Stopping server (pid=$SERVER_PID)"
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
        SERVER_PID=""
        # Brief pause for port release
        sleep 1
    fi
}

# Cleanup on exit
trap stop_server EXIT

# --- Run one trial ---

run_trial() {
    local kv_mode="$1"
    local trial_num="$2"
    local timestamp
    timestamp=$(date +%Y%m%dT%H%M%S)
    local outfile="$BENCHMARKS_DIR/kv_${kv_mode}_${timestamp}.json"
    local test_log="$BENCHMARKS_DIR/.test_${kv_mode}_${trial_num}.log"

    echo ""
    echo "=== Trial $trial_num: kv=$kv_mode ==="

    # Run easy integration tests with verbose output
    local start_time
    start_time=$(date +%s)

    cd "$AGENT_DIR"
    python3 -m pytest tests/test_agent_integration.py -s -v \
        -k "$TEST_FILTER" \
        >"$test_log" 2>&1 || true

    local end_time
    end_time=$(date +%s)
    local wall_time=$((end_time - start_time))

    # Parse results from pytest output
    local passed failed
    passed=$(grep -c "PASSED" "$test_log" 2>/dev/null || echo "0")
    failed=$(grep -c "FAILED" "$test_log" 2>/dev/null || echo "0")

    # Parse per-test timing from verbose output (lines like "test_name PASSED")
    # and agent log lines for plan/exec timing
    local test_names=()
    local test_results=()

    while IFS= read -r line; do
        if [[ "$line" =~ ::(test_[a-z_]+)\ (PASSED|FAILED) ]]; then
            test_names+=("${BASH_REMATCH[1]}")
            test_results+=("${BASH_REMATCH[2]}")
        fi
    done < "$test_log"

    # Count replans, retries, malformed JSON from test output
    local replan_count retry_count malformed_count
    replan_count=$(grep -ci "replan\|re-plan\|plan attempt" "$test_log" 2>/dev/null || echo "0")
    retry_count=$(grep -ci "thinking retry\|retry.*think\|escalat" "$test_log" 2>/dev/null || echo "0")
    malformed_count=$(grep -ci "json\|parse error\|JSONDecode" "$test_log" 2>/dev/null || echo "0")

    # Build JSON result
    local tests_json="["
    for i in "${!test_names[@]}"; do
        [ "$i" -gt 0 ] && tests_json+=","
        local status="pass"
        [ "${test_results[$i]}" = "FAILED" ] && status="fail"
        tests_json+="{\"name\":\"${test_names[$i]}\",\"status\":\"$status\"}"
    done
    tests_json+="]"

    cat > "$outfile" <<ENDJSON
{
  "timestamp": "$timestamp",
  "build_sha": "$BUILD_SHA",
  "model": "$MODEL",
  "ctx_size": $CTX_SIZE,
  "kv_mode": "$kv_mode",
  "trial": $trial_num,
  "test_filter": "$TEST_FILTER",
  "tests": $tests_json,
  "passed": $passed,
  "failed": $failed,
  "wall_time_s": $wall_time,
  "replan_count": $replan_count,
  "retry_count": $retry_count,
  "malformed_json_count": $malformed_count
}
ENDJSON

    echo "Result: ${passed} passed, ${failed} failed, ${wall_time}s wall time"
    echo "Written: $outfile"
}

# --- Main ---

for mode in "${MODES[@]}"; do
    start_server "$mode"

    for trial in $(seq 1 "$TRIALS"); do
        run_trial "$mode" "$trial"
    done

    stop_server
done

echo ""
echo "=== Benchmark complete ==="
echo "Results in: $BENCHMARKS_DIR/"
ls -1t "$BENCHMARKS_DIR"/kv_*.json 2>/dev/null | head -20
