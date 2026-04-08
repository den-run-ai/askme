#!/usr/bin/env python3
"""Benchmark: planner thinking (think=True vs think=False) on first plan.

Phase 1: get_plan()-only evaluation.
  Runs each prompt through get_plan() with think=True and think=False,
  3 runs per mode with counterbalanced order. Temperature patched to 0.
  Results saved to JSON for consistent rubric scoring.

Phase 2 (run manually, only if Phase 1 shows competitive quality):
  Full _run_loop() comparison.

Usage:
  # Phase 1 — plan quality only (requires llama-server on :8080 or OPENROUTER)
  python3 scripts/bench_planner_thinking.py

  # Phase 2 — full end-to-end (only if Phase 1 warrants it)
  python3 scripts/bench_planner_thinking.py --phase2

  # Use OpenRouter backend
  LLM_BACKEND=openrouter python3 scripts/bench_planner_thinking.py

  # Custom run count
  python3 scripts/bench_planner_thinking.py --runs 5
"""
import sys, os, json, time, tempfile, argparse, random
from pathlib import Path

# Ensure agent/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import askme

# ---------------------------------------------------------------------------
# Benchmark config
# ---------------------------------------------------------------------------
BENCH_TEMPERATURE = 0.0   # deterministic for reproducibility
RUNS_PER_MODE = 3         # default; overridable via --runs

# ---------------------------------------------------------------------------
# Prompt suite
# ---------------------------------------------------------------------------
# Each prompt has:
#   id: short identifier
#   prompt: the user request
#   category: "simple" or "planner-sensitive"
#   rubric: list of criteria to check in the plan

PROMPTS = [
    # --- Simple cases (thinking unlikely to help) ---
    {
        "id": "hello_txt",
        "prompt": "Create a file called hello.txt containing 'hello world'.",
        "category": "simple",
        "rubric": ["single_task_sufficient", "relative_paths"],
    },
    {
        "id": "uname_capture",
        "prompt": "Run 'uname -s' and write its output to os.txt.",
        "category": "simple",
        "rubric": ["single_task_sufficient", "relative_paths"],
    },
    # --- Planner-sensitive: dependencies ---
    {
        "id": "c_compile_run",
        "prompt": (
            "Create main.c that prints 'AGENT_OK', "
            "compile with cc -o main main.c, run ./main."
        ),
        "category": "planner-sensitive",
        "rubric": [
            "dependencies_ordered",  # write before compile before run
            "relative_paths",
            "specific_descriptions",  # includes, printf, filename
        ],
    },
    {
        "id": "header_dep",
        "prompt": (
            "Compile and run a C program. "
            "The program main.c should '#include \"msg.h\"' and call "
            "'printf(\"%s\\n\", MSG);'. "
            "You must create msg.h with '#define MSG \"AGENT_OK\"' first."
        ),
        "category": "planner-sensitive",
        "rubric": [
            "dependencies_ordered",  # msg.h before main.c before compile
            "file_content_hints",    # include hints in task descriptions
            "relative_paths",
            "no_redundant_tasks",
        ],
    },
    # --- Planner-sensitive: tool detection ---
    {
        "id": "missing_tool",
        "prompt": "Compile and run a Go program that prints 'hello'.",
        "category": "planner-sensitive",
        "rubric": [
            "respects_missing_tools",  # should fail-fast if go not available
            "respects_policy",
        ],
    },
    # --- Planner-sensitive: multi-file with ordering ---
    {
        "id": "python_module",
        "prompt": (
            "Create a Python module: utils.py with a function greet(name) "
            "that returns f'Hello, {name}!', and main.py that imports greet "
            "from utils and prints greet('World'). Run main.py."
        ),
        "category": "planner-sensitive",
        "rubric": [
            "dependencies_ordered",  # utils.py before main.py before run
            "specific_descriptions",
            "relative_paths",
            "no_redundant_tasks",
        ],
    },
    # --- Planner-sensitive: error recovery context (simulated replan) ---
    {
        "id": "fix_syntax_error",
        "prompt": (
            "Run python3 greet.py — it has a syntax error. "
            "Fix the error in greet.py and run it again successfully."
        ),
        "category": "planner-sensitive",
        "rubric": [
            "dependencies_ordered",  # diagnose before fix before rerun
            "specific_descriptions",
        ],
    },
    # --- Edge case: platform-aware ---
    {
        "id": "platform_aware",
        "prompt": "Check available disk space and write the result to disk_info.txt.",
        "category": "planner-sensitive",
        "rubric": [
            "respects_platform",  # df on macOS/Linux, not dir on Windows
            "relative_paths",
        ],
    },
]

# ---------------------------------------------------------------------------
# Rubric definitions
# ---------------------------------------------------------------------------

RUBRIC_DESCRIPTIONS = {
    "dependencies_ordered": "Tasks are in correct dependency order",
    "relative_paths": "Task descriptions use relative paths (no /tmp/... or absolute)",
    "specific_descriptions": "Descriptions include key details (filenames, includes, content hints)",
    "file_content_hints": "Descriptions mention what files should contain",
    "no_redundant_tasks": "No overlapping or redundant tasks",
    "single_task_sufficient": "Simple enough for 1-2 tasks (not over-decomposed)",
    "respects_missing_tools": "Respects missing_tools in environment state",
    "respects_policy": "Respects execution policy (e.g. no installs if disallowed)",
    "respects_platform": "Uses platform-appropriate commands",
}


def make_state(working_dir):
    """Build a realistic planner state with preflight data."""
    env = askme.preflight_probe(working_dir)
    return {
        "completed_tasks": [],
        "errors": [],
        "environment": env,
        "policy": askme.get_policy(),
    }


def is_planner_call(messages):
    """Detect planner calls by system prompt content, not token count."""
    if messages and messages[0].get("role") == "system":
        return messages[0]["content"] == askme.SYSTEM_PLAN
    return False


def run_get_plan(prompt, state, think_override):
    """Call get_plan with controlled think parameter via monkeypatch.

    Patches ask_llm to override think and temperature.
    Detects planner calls via SYSTEM_PLAN system prompt.
    """
    original_ask_llm = askme.ask_llm
    call_record = {}

    def patched_ask_llm(messages, max_tokens=256, think=False):
        if is_planner_call(messages):
            call_record["think_requested"] = think
            call_record["think_override"] = think_override
            call_record["max_tokens"] = max_tokens
        return original_ask_llm(messages, max_tokens=max_tokens, think=think_override)

    askme.ask_llm = patched_ask_llm
    try:
        t0 = time.time()
        result = askme.get_plan(prompt, dict(state))  # copy state to avoid mutation
        elapsed = time.time() - t0
    finally:
        askme.ask_llm = original_ask_llm

    return {
        "plan": result,
        "tasks": result.get("tasks", []),
        "elapsed_s": round(elapsed, 2),
        "think": think_override,
        "call_record": {
            "think_requested": call_record.get("think_requested"),
            "think_override": call_record.get("think_override"),
            "max_tokens": call_record.get("max_tokens"),
        },
    }


def score_plan(tasks, rubric_keys, prompt_id, state):
    """Auto-score plan against rubric. Returns dict of criterion -> pass/fail/skip."""
    scores = {}
    task_text = " ".join(tasks).lower()

    for key in rubric_keys:
        if key == "dependencies_ordered":
            # Heuristic: tasks should not mention "run" before "create"/"write"
            # and "compile" should come after "create"/"write"
            create_idx = -1
            compile_idx = -1
            run_idx = -1
            for i, t in enumerate(tasks):
                tl = t.lower()
                if any(w in tl for w in ("create", "write")):
                    create_idx = max(create_idx, 0) if create_idx == -1 else create_idx
                if any(w in tl for w in ("compile", "build", "cc ", "gcc")):
                    compile_idx = i
                if any(w in tl for w in ("run", "execute", "./")) and "create" not in tl:
                    run_idx = i
            if create_idx >= 0 and compile_idx >= 0:
                scores[key] = "pass" if create_idx < compile_idx else "FAIL"
            elif create_idx >= 0 and run_idx >= 0:
                scores[key] = "pass" if create_idx < run_idx else "FAIL"
            else:
                scores[key] = "skip"  # can't determine

        elif key == "relative_paths":
            has_abs = any("/" in t and t.strip().startswith("/") for t in tasks)
            # Check for /tmp or absolute paths in task descriptions
            has_tmp = any("/tmp" in t for t in tasks)
            scores[key] = "FAIL" if (has_abs or has_tmp) else "pass"

        elif key == "specific_descriptions":
            # Tasks should mention filenames or key details
            has_filename = any("." in t for t in tasks)  # e.g. main.c, utils.py
            scores[key] = "pass" if has_filename else "FAIL"

        elif key == "file_content_hints":
            has_hints = any(
                w in task_text
                for w in ("#include", "#define", "import", "def ", "printf", "print(")
            )
            scores[key] = "pass" if has_hints else "FAIL"

        elif key == "no_redundant_tasks":
            # Simple: fewer tasks is better, flag if > 5
            scores[key] = "pass" if len(tasks) <= 4 else "FAIL"

        elif key == "single_task_sufficient":
            scores[key] = "pass" if len(tasks) <= 2 else "FAIL"

        elif key == "respects_missing_tools":
            missing = state.get("environment", {}).get("missing_tools", [])
            # If the prompt mentions a tool that's missing, plan should acknowledge
            if "go" in missing and "go" in prompt_id:
                # Plan should either be a fail/prerequisite task or mention missing
                has_fail = any("fail" in t.lower() or "missing" in t.lower()
                               or "prerequisite" in t.lower() or "not available" in t.lower()
                               for t in tasks)
                scores[key] = "pass" if has_fail else "FAIL"
            else:
                scores[key] = "skip"

        elif key == "respects_policy":
            policy = state.get("policy", {})
            if not policy.get("allow_system_installs", True):
                has_install = any("install" in t.lower() for t in tasks)
                scores[key] = "FAIL" if has_install else "pass"
            else:
                scores[key] = "skip"

        elif key == "respects_platform":
            scores[key] = "skip"  # hard to auto-check without running

        else:
            scores[key] = "skip"

    return scores


def build_run_schedule(prompts, runs_per_mode):
    """Build counterbalanced run schedule.

    For each prompt, alternates which mode goes first across runs.
    Returns list of (prompt, think_mode, run_index) tuples in execution order.
    """
    schedule = []
    for p in prompts:
        for run_idx in range(runs_per_mode):
            # Alternate starting mode: even runs start True, odd start False
            if run_idx % 2 == 0:
                order = [True, False]
            else:
                order = [False, True]
            for think_mode in order:
                schedule.append((p, think_mode, run_idx))
    return schedule


def phase1(args):
    """Phase 1: get_plan()-only comparison."""
    runs_per_mode = args.runs
    print(f"=== Phase 1: Planner thinking benchmark ===")
    print(f"Backend: {askme.LLM_BACKEND} ({askme.MODEL})")
    print(f"Runs per mode: {runs_per_mode}")
    print(f"Temperature: {BENCH_TEMPERATURE} (patched)")
    print()

    # Patch temperature for deterministic results
    original_temperature = None  # will patch in ask_llm

    tmp = tempfile.mkdtemp(prefix="bench_plan_")
    state = make_state(tmp)
    env_snapshot = dict(state["environment"])
    policy_snapshot = dict(state["policy"])
    print(f"Working dir: {tmp}")
    print(f"Environment: platform={env_snapshot['platform']} "
          f"arch={env_snapshot['arch']}")
    print(f"Missing tools: {env_snapshot['missing_tools']}")
    print(f"Policy: {policy_snapshot}")
    print()

    # Patch ask_llm to force temperature=0
    _original_ask_llm = askme.ask_llm
    def temp_patched_ask_llm(messages, max_tokens=256, think=False):
        # Temporarily patch the body construction inside ask_llm
        # We intercept at a higher level: patch the module-level call
        return _original_ask_llm(messages, max_tokens=max_tokens, think=think)
    # Direct approach: monkeypatch the body dict construction in ask_llm
    # Since ask_llm builds body internally, we need to patch at request level
    _original_post = askme.requests.post
    def temp_patched_post(url, **kwargs):
        if "json" in kwargs and "temperature" in kwargs["json"]:
            kwargs["json"]["temperature"] = BENCH_TEMPERATURE
        return _original_post(url, **kwargs)
    askme.requests.post = temp_patched_post

    # Build counterbalanced schedule
    schedule = build_run_schedule(PROMPTS, runs_per_mode)

    results = []
    total_calls = len(schedule)
    call_num = 0

    try:
        for prompt_def, think_mode, run_idx in schedule:
            call_num += 1
            prompt_text = prompt_def["prompt"]
            label = f"think={'T' if think_mode else 'F'}"
            print(f"  [{call_num}/{total_calls}] {prompt_def['id']} {label} run={run_idx+1} ...",
                  end="", flush=True)

            try:
                result = run_get_plan(prompt_text, state, think_override=think_mode)
                tasks = result["tasks"]
                scores = score_plan(tasks, prompt_def["rubric"], prompt_def["id"], state)
                pass_count = sum(1 for v in scores.values() if v == "pass")
                fail_count = sum(1 for v in scores.values() if v == "FAIL")

                print(f" {result['elapsed_s']}s | {len(tasks)} tasks | "
                      f"P={pass_count} F={fail_count}")
                for t in tasks:
                    print(f"      - {t}")

                results.append({
                    "prompt_id": prompt_def["id"],
                    "category": prompt_def["category"],
                    "think": think_mode,
                    "run_idx": run_idx,
                    "tasks": tasks,
                    "elapsed_s": result["elapsed_s"],
                    "scores": scores,
                    "pass": pass_count,
                    "fail": fail_count,
                    "raw_plan": result["plan"],
                })

            except Exception as e:
                print(f" ERROR: {e}")
                results.append({
                    "prompt_id": prompt_def["id"],
                    "category": prompt_def["category"],
                    "think": think_mode,
                    "run_idx": run_idx,
                    "error": str(e),
                })
    finally:
        # Restore patched post
        askme.requests.post = _original_post

    # Save raw results with environment snapshot
    output = {
        "meta": {
            "backend": askme.LLM_BACKEND,
            "model": askme.MODEL,
            "temperature": BENCH_TEMPERATURE,
            "runs_per_mode": runs_per_mode,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "environment": env_snapshot,
            "policy": policy_snapshot,
        },
        "results": results,
    }
    out_path = Path(__file__).parent.parent / "benchmarks" / f"planner_thinking_{int(time.time())}.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nResults saved to {out_path}")

    # Summary table — aggregate across runs
    print()
    print("=== Per-prompt summary (averaged across runs) ===")
    print(f"{'Prompt':<20} {'Mode':<12} {'Avg(s)':<8} {'Tasks':<6} {'Pass':<5} {'Fail':<5} {'Runs':<5}")
    print("-" * 65)
    for p in PROMPTS:
        for think_mode in [True, False]:
            group = [r for r in results if r["prompt_id"] == p["id"]
                     and r.get("think") == think_mode and "error" not in r]
            if not group:
                print(f"{p['id']:<20} {'think=' + str(think_mode):<12} NO DATA")
                continue
            avg_time = sum(r["elapsed_s"] for r in group) / len(group)
            avg_tasks = sum(len(r["tasks"]) for r in group) / len(group)
            total_pass = sum(r.get("pass", 0) for r in group)
            total_fail = sum(r.get("fail", 0) for r in group)
            print(f"{p['id']:<20} {'think=' + str(think_mode):<12} "
                  f"{avg_time:<8.1f} {avg_tasks:<6.1f} {total_pass:<5} {total_fail:<5} {len(group):<5}")

    # Aggregate comparison
    think_true = [r for r in results if r.get("think") is True and "error" not in r]
    think_false = [r for r in results if r.get("think") is False and "error" not in r]

    if think_true and think_false:
        print()
        print("=== Aggregate ===")
        for label, group in [("think=True ", think_true), ("think=False", think_false)]:
            avg_time = sum(r["elapsed_s"] for r in group) / len(group)
            total_pass = sum(r.get("pass", 0) for r in group)
            total_fail = sum(r.get("fail", 0) for r in group)
            print(f"  {label}: n={len(group)}  avg={avg_time:.1f}s  pass={total_pass}  fail={total_fail}")

        # Variance check
        print()
        print("=== Run-to-run variance (per prompt, think=True vs False) ===")
        for p in PROMPTS:
            for think_mode in [True, False]:
                group = [r for r in results if r["prompt_id"] == p["id"]
                         and r.get("think") == think_mode and "error" not in r]
                if len(group) < 2:
                    continue
                times = [r["elapsed_s"] for r in group]
                task_counts = [len(r["tasks"]) for r in group]
                task_sets = [tuple(r["tasks"]) for r in group]
                identical = len(set(task_sets)) == 1
                label = f"think={'T' if think_mode else 'F'}"
                print(f"  {p['id']:<20} {label}: "
                      f"time={min(times):.1f}-{max(times):.1f}s  "
                      f"tasks={min(task_counts)}-{max(task_counts)}  "
                      f"identical={'yes' if identical else 'NO'}")

    return results


def phase2(args):
    """Phase 2: full _run_loop() comparison. Only run if Phase 1 warrants it."""
    runs_per_mode = args.runs
    print("=== Phase 2: Full end-to-end benchmark ===")
    print(f"Backend: {askme.LLM_BACKEND} ({askme.MODEL})")
    print(f"Runs per mode: {runs_per_mode}")
    print(f"Temperature: {BENCH_TEMPERATURE} (patched)")
    print()

    # Patch temperature
    _original_post = askme.requests.post
    def temp_patched_post(url, **kwargs):
        if "json" in kwargs and "temperature" in kwargs["json"]:
            kwargs["json"]["temperature"] = BENCH_TEMPERATURE
        return _original_post(url, **kwargs)
    askme.requests.post = temp_patched_post

    # Use a subset of prompts — only the planner-sensitive ones
    e2e_prompts = [p for p in PROMPTS if p["category"] == "planner-sensitive"
                   and p["id"] != "missing_tool"]  # skip expected-fail prompts

    # Build counterbalanced schedule for Phase 2
    schedule = build_run_schedule(e2e_prompts, runs_per_mode)

    # Snapshot environment
    tmp_env = tempfile.mkdtemp(prefix="bench_e2e_env_")
    env_snapshot = dict(askme.preflight_probe(tmp_env))
    policy_snapshot = dict(askme.get_policy())

    results = []
    total_calls = len(schedule)
    call_num = 0

    try:
        for prompt_def, think_mode, run_idx in schedule:
            call_num += 1
            run_tmp = tempfile.mkdtemp(prefix=f"bench_e2e_{prompt_def['id']}_")
            prompt_text = prompt_def["prompt"]
            label = f"think={'T' if think_mode else 'F'}"
            print(f"  [{call_num}/{total_calls}] {prompt_def['id']} {label} run={run_idx+1} ...",
                  end="", flush=True)

            # Monkeypatch: override think for first planner call only
            plan_call_count = [0]
            first_plan_tasks = [None]  # capture first plan text
            _original_ask_llm = askme.ask_llm

            def make_patched(think_first_plan, call_count, plan_capture):
                def patched_ask_llm(messages, max_tokens=256, think=False):
                    # Detect planner calls via SYSTEM_PLAN system prompt
                    if is_planner_call(messages):
                        call_count[0] += 1
                        if call_count[0] == 1:
                            think = think_first_plan
                        # else: replans keep think=True (as passed by get_plan)
                    result = _original_ask_llm(messages, max_tokens=max_tokens, think=think)
                    # Capture first plan
                    if is_planner_call(messages) and call_count[0] == 1:
                        plan_capture[0] = result
                    return result
                return patched_ask_llm

            askme.ask_llm = make_patched(think_mode, plan_call_count, first_plan_tasks)
            try:
                t0 = time.time()
                result = askme._run_loop(
                    prompt_text, run_tmp,
                    max_replans=2, max_tasks=3, max_steps=5
                )
                elapsed = time.time() - t0
                status = result["status"]
                replans = sum(1 for e in result["log"] if e.get("event") == "plan") - 1
                replans = max(0, replans)

                print(f" {elapsed:.1f}s | {status} | replans={replans}")

                results.append({
                    "prompt_id": prompt_def["id"],
                    "think_first_plan": think_mode,
                    "run_idx": run_idx,
                    "status": status,
                    "elapsed_s": round(elapsed, 1),
                    "replans": replans,
                    "log_events": len(result["log"]),
                    "first_plan": first_plan_tasks[0],
                })
            except Exception as e:
                print(f" ERROR: {e}")
                results.append({
                    "prompt_id": prompt_def["id"],
                    "think_first_plan": think_mode,
                    "run_idx": run_idx,
                    "error": str(e),
                })
            finally:
                askme.ask_llm = _original_ask_llm
    finally:
        askme.requests.post = _original_post

    # Save results
    output = {
        "meta": {
            "backend": askme.LLM_BACKEND,
            "model": askme.MODEL,
            "temperature": BENCH_TEMPERATURE,
            "runs_per_mode": runs_per_mode,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "environment": env_snapshot,
            "policy": policy_snapshot,
        },
        "results": results,
    }
    out_path = Path(__file__).parent.parent / "benchmarks" / f"planner_thinking_e2e_{int(time.time())}.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nResults saved to {out_path}")

    # Summary
    print()
    print("=== Phase 2 Summary (averaged across runs) ===")
    print(f"{'Prompt':<20} {'Mode':<14} {'Avg(s)':<8} {'Success':<8} {'Replans':<8} {'Runs':<5}")
    print("-" * 65)
    for p in e2e_prompts:
        for think_mode in [True, False]:
            group = [r for r in results if r["prompt_id"] == p["id"]
                     and r.get("think_first_plan") == think_mode and "error" not in r]
            if not group:
                print(f"{p['id']:<20} {'think=' + str(think_mode):<14} NO DATA")
                continue
            avg_time = sum(r["elapsed_s"] for r in group) / len(group)
            successes = sum(1 for r in group if r["status"] == "complete")
            avg_replans = sum(r["replans"] for r in group) / len(group)
            print(f"{p['id']:<20} {'think=' + str(think_mode):<14} "
                  f"{avg_time:<8.1f} {successes}/{len(group):<5}  {avg_replans:<8.1f} {len(group):<5}")


def main():
    parser = argparse.ArgumentParser(description="Benchmark planner thinking modes")
    parser.add_argument("--phase2", action="store_true",
                        help="Run full _run_loop() comparison (Phase 2)")
    parser.add_argument("--runs", type=int, default=RUNS_PER_MODE,
                        help=f"Runs per mode per prompt (default: {RUNS_PER_MODE})")
    args = parser.parse_args()

    if args.phase2:
        phase2(args)
    else:
        phase1(args)


if __name__ == "__main__":
    main()
