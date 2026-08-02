#!/usr/bin/env python3
"""Deterministic multi-turn cache benchmark for llama-server.

Simulates an AskMe workload: 1 planner call + 6 executor calls, all
sharing the same system prompt within type.  Measures server-side prompt
eval and decode per request to isolate cache-reuse behaviour from model
output variance.

Key design choices:
- Uses /v1/chat/completions (same endpoint the agent uses)
- temperature=0, seed=1, low max_tokens → deterministic, short responses
- Fixed message content across trials → identical token sequences
- Slot erased between trials → each trial starts cold

Requires llama-server on :8080.

Usage:
    python3 tests/bench_cache_multiturn.py <config_label> [--trials 3]

Output: JSON with per-request timings + summary table to stdout.
"""

import argparse
import json
import statistics
import sys
import time

import requests

URL_CHAT = "http://127.0.0.1:8080/v1/chat/completions"
URL_ERASE = "http://127.0.0.1:8080/slots/0?action=erase"

# ── Realistic message payloads (from actual agent runs) ────────────────

SYSTEM_PLAN = (
    "You are a planner. Given a user request and current state, propose a list of tasks.\n"
    "If a previous plan failed, redesign it based on what went wrong.\n"
    "Prefer fewer tasks (1-3). Each task should be a complete goal, not a single command. Max 10 tasks.\n"
    "Keep descriptions short (under 15 words each) but include key details:\n"
    "- File content hints: which includes, defines, or imports are needed\n"
    "- Use relative filenames (e.g. main.c not /full/path/main.c)\n"
    "- Never create a task for work already in completed_tasks\n"
    "POLICY RULES:\n"
    "- Check state.environment.missing_tools\n"
    "- Respect state.environment.platform\n"
    "Output ONLY valid JSON. No markdown, no explanation.\n"
    'Format: {"tasks": ["task1 description", "task2 description"]}'
)

SYSTEM_STEP = (
    "You are a task executor. Output ONLY valid JSON. No markdown, no explanation.\n"
    "Propose ONE action at a time. Use relative paths.\n"
    "CRITICAL RULES:\n"
    '- Emit {"action":"done"} ONLY when the FULL task description is satisfied.\n'
    '- If last_steps shows the same error 2+ times, emit {"action":"fail"}.\n'
    "- completed_tasks are DONE — never redo their work.\n"
    "- To modify an existing file, prefer edit over write.\n"
    "Actions: shell, write, edit, read, done, fail.\n"
    'Format: {"action":"...","arg":"...","content":"...","reasoning":"max 10 words"}'
)

PLAN_STATE = json.dumps(
    {
        "completed_tasks": [],
        "completed_step_groups": [],
        "errors": [],
        "environment": {
            "platform": "darwin",
            "available_tools": ["python3", "gcc", "make"],
            "missing_tools": [],
            "package_managers": ["brew"],
            "dir_listing": ["(empty)"],
        },
        "policy": {"allow_system_installs": False, "allow_network": False},
    }
)

USER_PROMPT = (
    "Create a C program that prints fibonacci numbers up to N, compile and run it with N=10"
)


def make_plan_messages():
    return [
        {"role": "system", "content": SYSTEM_PLAN},
        {"role": "user", "content": f"REQUEST:\n{USER_PROMPT}\n\nSTATE:\n{PLAN_STATE}"},
    ]


def make_step_messages(task, step_num, last_steps=None, completed_tasks=None):
    slim = {
        "task": task,
        "task_index": 0,
        "step": f"{step_num}/8",
        "last_steps": last_steps or [],
        "policy": {"allow_system_installs": False, "allow_network": False},
    }
    if completed_tasks:
        slim["completed_tasks"] = completed_tasks
    return [
        {"role": "system", "content": SYSTEM_STEP},
        {
            "role": "user",
            "content": f"GOAL:\n{USER_PROMPT}\n\nTASK:\n{task}\n\nSTATE:\n{json.dumps(slim)}",
        },
    ]


# 7 requests simulating a realistic agent run:
#   [0] plan
#   [1] step 1: write fib.c (empty history)
#   [2] step 2: compile (has write result)
#   [3] step 3: run (has compile result)
#   [4] step 4: done
#   [5] step 1 of task 2: verify output (new task, carries completed_tasks)
#   [6] step 2 of task 2: done

TASK1 = "Write fib.c with fibonacci function, compile with gcc, run with N=10"
TASK2 = "Verify output is correct fibonacci sequence"

REQUESTS = [
    ("plan", make_plan_messages),
    ("step_1", lambda: make_step_messages(TASK1, 1)),
    (
        "step_2",
        lambda: make_step_messages(
            TASK1,
            2,
            last_steps=[
                {"action": "write", "arg": "fib.c", "ok": True, "output": "Wrote fib.c (42 lines)"},
            ],
        ),
    ),
    (
        "step_3",
        lambda: make_step_messages(
            TASK1,
            3,
            last_steps=[
                {"action": "write", "arg": "fib.c", "ok": True, "output": "Wrote fib.c (42 lines)"},
                {"action": "shell", "arg": "gcc -o fib fib.c", "ok": True, "output": ""},
            ],
        ),
    ),
    (
        "step_4",
        lambda: make_step_messages(
            TASK1,
            4,
            last_steps=[
                {"action": "shell", "arg": "gcc -o fib fib.c", "ok": True, "output": ""},
                {
                    "action": "shell",
                    "arg": "./fib 10",
                    "ok": True,
                    "output": "0 1 1 2 3 5 8 13 21 34",
                },
            ],
        ),
    ),
    ("step_5", lambda: make_step_messages(TASK2, 1, completed_tasks=[TASK1])),
    (
        "step_6",
        lambda: make_step_messages(
            TASK2,
            2,
            last_steps=[
                {
                    "action": "read",
                    "arg": "fib.c",
                    "ok": True,
                    "output": "#include <stdio.h>\\nvoid fib(int n) { ... }",
                }
            ],
            completed_tasks=[TASK1],
        ),
    ),
]


# ── Benchmark runner ───────────────────────────────────────────────────


def post_chat(messages, max_tokens=32):
    """Send chat completion, return (response_json, wall_seconds)."""
    t0 = time.time()
    r = requests.post(
        URL_CHAT,
        json={
            "model": "local",
            "messages": messages,
            "temperature": 0.0,
            "seed": 1,
            "max_tokens": max_tokens,
        },
        timeout=300,
    )
    wall = time.time() - t0
    r.raise_for_status()
    return r.json(), wall


def erase_slot():
    try:
        requests.post(URL_ERASE, timeout=10)
    except Exception:
        pass


def extract_timings(resp):
    """Pull timing info from response. Works with both usage and timings blocks."""
    row = {}
    usage = resp.get("usage", {})
    row["prompt_tokens"] = usage.get("prompt_tokens", 0)
    row["completion_tokens"] = usage.get("completion_tokens", 0)
    timings = resp.get("timings", {})
    if timings:
        row["prompt_n"] = timings.get("prompt_n", 0)
        row["prompt_ms"] = round(timings.get("prompt_ms", 0), 1)
        row["predicted_n"] = timings.get("predicted_n", 0)
        row["predicted_ms"] = round(timings.get("predicted_ms", 0), 1)
        if row["predicted_n"] > 0 and row["predicted_ms"] > 0:
            row["decode_tok_s"] = round(row["predicted_n"] / (row["predicted_ms"] / 1000), 2)
    return row


def run_trial(label, trial_num):
    """Run one full 7-request trial. Returns list of per-request dicts."""
    erase_slot()
    time.sleep(0.5)
    results = []
    for i, (name, msg_fn) in enumerate(REQUESTS):
        msgs = msg_fn()
        resp, wall_s = post_chat(msgs, max_tokens=32)
        timings = extract_timings(resp)
        row = {
            "config": label,
            "trial": trial_num,
            "request": i,
            "name": name,
            "wall_s": round(wall_s, 3),
            **timings,
        }
        results.append(row)
        # Short label for live progress
        prompt_info = (
            f"prompt_n={timings.get('prompt_n', '?')}"
            if "prompt_n" in timings
            else f"prompt_tok={timings.get('prompt_tokens', '?')}"
        )
        print(f"  [{name:8s}] wall={wall_s:.2f}s {prompt_info}", flush=True)
    return results


# ── Reporting ──────────────────────────────────────────────────────────


def summarize(all_results, label, n_trials):
    """Print summary table with median ± range across trials."""
    print(f"\n{'=' * 72}")
    print(f"Summary: {label}  ({n_trials} trials)")
    print(f"{'=' * 72}")

    # Group by request name
    by_name = {}
    for r in all_results:
        by_name.setdefault(r["name"], []).append(r)

    has_timings = "prompt_n" in all_results[0]
    if has_timings:
        hdr = f"{'Request':<10s} {'prompt_n':>10s} {'prompt_ms':>10s} {'pred_n':>8s} {'decode t/s':>10s} {'wall_s':>8s}"
    else:
        hdr = f"{'Request':<10s} {'prompt_tok':>10s} {'compl_tok':>10s} {'wall_s':>8s}"
    print(hdr)
    print("-" * len(hdr))

    summary_rows = []
    for name in [n for n, _ in REQUESTS]:
        rows = by_name[name]
        walls = [r["wall_s"] for r in rows]
        wall_med = statistics.median(walls)

        if has_timings:
            pn = [r.get("prompt_n", 0) for r in rows]
            pm = [r.get("prompt_ms", 0) for r in rows]
            dn = [r.get("predicted_n", 0) for r in rows]
            dt = [r.get("decode_tok_s", 0) for r in rows]
            pn_med = statistics.median(pn)
            pm_med = statistics.median(pm)
            dn_med = statistics.median(dn)
            dt_med = statistics.median(dt)
            line = f"{name:<10s} {pn_med:>10.0f} {pm_med:>10.1f} {dn_med:>8.0f} {dt_med:>10.2f} {wall_med:>8.3f}"
            summary_rows.append(
                {
                    "name": name,
                    "prompt_n": pn_med,
                    "prompt_ms": pm_med,
                    "predicted_n": dn_med,
                    "decode_tok_s": dt_med,
                    "wall_s": wall_med,
                }
            )
        else:
            pt = [r.get("prompt_tokens", 0) for r in rows]
            ct = [r.get("completion_tokens", 0) for r in rows]
            line = f"{name:<10s} {statistics.median(pt):>10.0f} {statistics.median(ct):>10.0f} {wall_med:>8.3f}"
            summary_rows.append(
                {
                    "name": name,
                    "prompt_tokens": statistics.median(pt),
                    "completion_tokens": statistics.median(ct),
                    "wall_s": wall_med,
                }
            )
        print(line)

    total_wall = sum(r["wall_s"] for r in summary_rows)
    print(f"\nTotal wall (median): {total_wall:.2f}s")

    if has_timings and len(summary_rows) > 1:
        # Check executor requests (indices 1+)
        executor_rows = [r for r in summary_rows if r["name"].startswith("step")]
        if executor_rows:
            first_exec = executor_rows[0]["prompt_n"]
            later_exec = [r["prompt_n"] for r in executor_rows[1:]]
            if later_exec:
                avg_later = statistics.mean(later_exec)
                if first_exec > 0:
                    saved_pct = (1 - avg_later / first_exec) * 100
                    print(
                        f"Executor prompt_n: first={first_exec:.0f}, avg_later={avg_later:.0f} → {saved_pct:+.0f}% eval saved"
                    )

    return summary_rows


def main():
    parser = argparse.ArgumentParser(description="Multi-turn cache benchmark")
    parser.add_argument("label", help="Config label (e.g. phase5, phase6)")
    parser.add_argument("--trials", type=int, default=3, help="Number of trials (default: 3)")
    parser.add_argument("--out", help="Output JSON path (default: /tmp/bench_cache_<label>.json)")
    args = parser.parse_args()

    if not args.out:
        args.out = f"/tmp/bench_cache_{args.label}.json"

    # Preflight: check server is up
    try:
        requests.get("http://127.0.0.1:8080/health", timeout=5)
    except Exception:
        print("ERROR: llama-server not reachable on :8080", file=sys.stderr)
        sys.exit(1)

    print(f"Benchmark: {args.label}, {args.trials} trials, 7 requests each")
    print(f"Output: {args.out}\n")

    all_results = []
    for t in range(args.trials):
        print(f"\n--- Trial {t + 1}/{args.trials} ---")
        trial_results = run_trial(args.label, t + 1)
        all_results.extend(trial_results)

    summary = summarize(all_results, args.label, args.trials)

    output = {
        "label": args.label,
        "trials": args.trials,
        "requests_per_trial": len(REQUESTS),
        "results": all_results,
        "summary": summary,
    }
    with open(args.out, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
