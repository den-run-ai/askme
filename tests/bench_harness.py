#!/usr/bin/env python3
"""E01: Multi-trial integration test harness on top of AGENT_RUN_LOG.

Runs each integration test N times with separate JSONL log files,
then reports median + range per test. No changes to askme.py.

Usage:
    # 3 trials of easy tests on local backend (default)
    python3 tests/bench_harness.py

    # 5 trials of medium tests on openrouter
    python3 tests/bench_harness.py --suite medium --trials 5 --backend openrouter

    # Single specific test
    python3 tests/bench_harness.py --test test_shell_and_write --trials 3

    # List available tests
    python3 tests/bench_harness.py --list
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

AGENT_DIR = Path(__file__).parent.parent

SUITES = {
    "easy": {
        "local": "TestIntegration",
        "openrouter": "TestOpenRouterEasy",
    },
    "medium": {
        "local": "TestIntegrationMedium",
        "openrouter": "TestOpenRouterMedium",
    },
    "hard": {
        "local": "TestIntegrationHard",
        "openrouter": "TestOpenRouterHard",
    },
}


def discover_tests(suite, backend):
    """Use pytest --collect-only to find test names in a suite."""
    class_name = SUITES[suite][backend]
    # "and not" clauses prevent substring matches (TestIntegration matching TestIntegrationMedium)
    k_expr = class_name
    if class_name == "TestIntegration":
        k_expr = "TestIntegration and not Medium and not Hard"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_agent_integration.py",
         "--collect-only", "-q", "-k", k_expr],
        capture_output=True, text=True, cwd=str(AGENT_DIR),
    )
    tests = []
    for line in result.stdout.splitlines():
        if "::" in line and not line.startswith("="):
            # e.g. "tests/test_agent_integration.py::TestIntegration::test_shell_and_write"
            parts = line.strip().split("::")
            if len(parts) >= 3:
                tests.append(parts[-1])
    return tests


def run_single_test(test_name, suite, backend, log_path, model=None, provider=None,
                    allow_fallbacks=False, require_parameters=True,
                    reasoning_effort=None):
    """Run one pytest test with AGENT_RUN_LOG set. Returns (passed, wall_seconds)."""
    class_name = SUITES[suite][backend]
    if class_name == "TestIntegration":
        k_expr = f"TestIntegration and not Medium and not Hard and {test_name}"
    else:
        k_expr = f"{class_name} and {test_name}"
    env = {**os.environ, "AGENT_RUN_LOG": str(log_path)}
    if backend == "openrouter":
        if model:
            env["OPENROUTER_MODEL"] = model
        if provider is not None:
            env["OPENROUTER_PROVIDER"] = provider
        env["OPENROUTER_ALLOW_FALLBACKS"] = "1" if allow_fallbacks else "0"
        env["OPENROUTER_REQUIRE_PARAMETERS"] = "1" if require_parameters else "0"
        # Explicit either way so a cell never inherits a stray effort from the
        # caller's environment.
        env["OPENROUTER_REASONING_EFFORT"] = reasoning_effort or ""
    t0 = time.time()
    result = subprocess.run(
        [sys.executable, "-m", "pytest",
         "tests/test_agent_integration.py", "-s", "-v", "-k", k_expr],
        capture_output=True, text=True, cwd=str(AGENT_DIR), env=env,
        timeout=1200,
    )
    wall = time.time() - t0
    passed = result.returncode == 0
    return passed, wall, result.stdout, result.stderr


def parse_log(log_path):
    """Parse JSONL log into summary metrics."""
    events = []
    try:
        with open(log_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    except FileNotFoundError:
        return None

    if not events:
        return None

    run_end = next((e for e in events if e["event"] == "run_end"), None)
    run_start = next((e for e in events if e["event"] == "run_start"), {})
    plans = [e for e in events if e["event"] == "plan"]
    plan_errors = [e for e in events if e["event"] == "plan_error"]
    steps = [e for e in events if e["event"] == "step"]
    tokens_events = [e for e in events if e["event"] == "tokens"]
    task_completes = [e for e in events if e["event"] == "task_complete"]
    task_fails = [e for e in events if e["event"] == "task_failed"]
    validations = [e for e in events if e["event"] == "validation"]
    local_replans = [e for e in events if e["event"] == "task_local_replan"]

    ok_steps = [s for s in steps if s.get("ok")]
    fail_steps = [s for s in steps if not s.get("ok")]
    thinking_retries = [t for t in tokens_events if t.get("attempt", 0) > 0]
    thinking_calls = [t for t in tokens_events if t.get("thinking")]

    total_prompt_tokens = sum(t.get("prompt", 0) for t in tokens_events)
    total_completion_tokens = sum(t.get("completion", 0) for t in tokens_events)
    total_openrouter_cost = sum(float(t.get("openrouter_cost") or 0) for t in tokens_events)
    served_models = sorted({t.get("model") for t in tokens_events if t.get("model")})
    served_providers = sorted({t.get("provider") for t in tokens_events if t.get("provider")})

    return {
        "status": run_end["status"] if run_end else "unknown",
        "wall_s": run_end.get("wall_s", 0) if run_end else 0,
        "replans": len(plans) - 1 if plans else 0,
        "plan_errors": len(plan_errors),
        "tasks": len(task_completes) + len(task_fails),
        "tasks_ok": len(task_completes),
        "tasks_failed": len(task_fails),
        "steps": len(steps),
        "steps_ok": len(ok_steps),
        "steps_failed": len(fail_steps),
        "thinking_retries": len(thinking_retries),
        "thinking_calls": len(thinking_calls),
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "total_tokens": total_prompt_tokens + total_completion_tokens,
        "openrouter_cost": total_openrouter_cost,
        "requested_model": run_start.get("model", ""),
        "requested_provider": run_start.get("provider", ""),
        "served_models": served_models,
        "served_providers": served_providers,
        "llm_calls": len(tokens_events),
        "validation": validations[0].get("valid") if validations else None,
        "local_replans": len(local_replans),
        "local_replans_ok": len([lr for lr in local_replans if lr.get("ok")]),
        "local_replan_wall_s": sum(lr.get("llm_wall_s", 0) for lr in local_replans),
    }


def fmt_range(values):
    """Format list of numbers as 'median (min–max)'."""
    if not values:
        return "—"
    med = statistics.median(values)
    if len(values) == 1:
        return f"{med:.1f}"
    return f"{med:.1f} ({min(values):.1f}–{max(values):.1f})"


def fmt_int_range(values):
    """Format list of ints as 'median (min–max)'."""
    if not values:
        return "—"
    med = statistics.median(values)
    if len(values) == 1:
        return f"{int(med)}"
    return f"{int(med)} ({min(values)}–{max(values)})"


def git_state():
    """Return the source revision used for a benchmark run."""
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(AGENT_DIR),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(AGENT_DIR),
            capture_output=True, text=True, check=True,
        ).stdout.strip())
        return revision, dirty
    except (OSError, subprocess.CalledProcessError):
        return "unknown", None


def print_report(test_name, trial_results):
    """Print summary table for one test across trials."""
    metrics = [r["metrics"] for r in trial_results if r["metrics"]]
    pytest_passes = sum(1 for r in trial_results if r["passed"])
    agent_completes = sum(
        1 for r in trial_results
        if r["metrics"] and r["metrics"].get("status") == "complete"
    )
    total = len(trial_results)

    print(f"\n{'─' * 60}")
    print(f"  {test_name}  [pytest {pytest_passes}/{total}, agent complete {agent_completes}/{total}]")
    print(f"{'─' * 60}")

    if not metrics:
        print("  No JSONL metrics collected.")
        return

    rows = [
        ("Wall time (s)", fmt_range([m["wall_s"] for m in metrics])),
        ("Agent complete", f"{agent_completes}/{total}"),
        ("Replans (full)", fmt_int_range([m["replans"] for m in metrics])),
        ("Local replans", fmt_int_range([m["local_replans"] for m in metrics])),
        ("Local replans ok", fmt_int_range([m["local_replans_ok"] for m in metrics])),
        ("Local replan (s)", fmt_range([m["local_replan_wall_s"] for m in metrics])),
        ("Steps", fmt_int_range([m["steps"] for m in metrics])),
        ("Steps failed", fmt_int_range([m["steps_failed"] for m in metrics])),
        ("Thinking retries", fmt_int_range([m["thinking_retries"] for m in metrics])),
        ("LLM calls", fmt_int_range([m["llm_calls"] for m in metrics])),
        ("Prompt tokens", fmt_int_range([m["prompt_tokens"] for m in metrics])),
        ("Completion tokens", fmt_int_range([m["completion_tokens"] for m in metrics])),
        ("OpenRouter cost", f"${sum(m['openrouter_cost'] for m in metrics):.4f} credits"),
    ]
    for label, value in rows:
        print(f"  {label:<22} {value}")

    for i, r in enumerate(trial_results):
        status = "PYTEST_PASS" if r["passed"] else "PYTEST_FAIL"
        wall = r["metrics"]["wall_s"] if r["metrics"] else r["wall_s"]
        print(f"  trial {i+1}: {status}  {wall:.1f}s", end="")
        if r["metrics"]:
            m = r["metrics"]
            agent = m.get("status", "unknown")
            lr = f", lr={m['local_replans_ok']}/{m['local_replans']}" if m['local_replans'] else ""
            print(f"  (agent={agent}, steps={m['steps']}, replans={m['replans']}{lr}, "
                  f"retries={m['thinking_retries']})", end="")
        print()


def main():
    parser = argparse.ArgumentParser(description="E01: Multi-trial integration harness")
    parser.add_argument("--suite", choices=["easy", "medium", "hard"], default="easy")
    parser.add_argument("--backend", choices=["local", "openrouter"], default="local")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--test", help="Run a single test by name")
    parser.add_argument("--model", help="OpenRouter model ID (honors OPENROUTER_MODEL by default)")
    parser.add_argument("--provider", help="OpenRouter provider slug; use 'auto' for automatic routing")
    parser.add_argument(
        "--reasoning-effort", choices=["low", "medium", "high"],
        help="Baseline reasoning effort for always-on reasoners like "
             "openai/gpt-oss-20b (honors OPENROUTER_REASONING_EFFORT by default)")
    parser.add_argument(
        "--allow-provider-fallbacks", action="store_true",
        help="Allow OpenRouter to leave the requested provider (disabled by default)",
    )
    parser.add_argument(
        "--no-require-provider-parameters", dest="require_provider_parameters",
        action="store_false", default=True,
        help="Allow a provider that does not advertise all request parameters",
    )
    parser.add_argument("--list", action="store_true", help="List available tests")
    parser.add_argument("--log-dir", help="Directory for JSONL logs (default: auto tmpdir)")
    args = parser.parse_args()

    if args.backend != "openrouter" and (
            args.model or args.provider or args.allow_provider_fallbacks
            or args.reasoning_effort or not args.require_provider_parameters):
        parser.error("OpenRouter routing options require --backend openrouter")

    model = None
    provider = None
    reasoning_effort = None
    if args.backend == "openrouter":
        model = args.model or os.environ.get(
            "OPENROUTER_MODEL", "google/gemma-4-26b-a4b-it")
        provider_arg = args.provider
        if provider_arg is None:
            provider = os.environ.get("OPENROUTER_PROVIDER", "Parasail")
        else:
            provider = "" if provider_arg.lower() == "auto" else provider_arg
        reasoning_effort = args.reasoning_effort or os.environ.get(
            "OPENROUTER_REASONING_EFFORT") or None

    if args.list:
        for suite in SUITES:
            tests = discover_tests(suite, args.backend)
            print(f"\n{suite} ({args.backend}):")
            for t in tests:
                print(f"  {t}")
        return

    if args.test:
        tests = [args.test]
    else:
        tests = discover_tests(args.suite, args.backend)

    if not tests:
        print(f"No tests found for suite={args.suite} backend={args.backend}")
        sys.exit(1)

    git_commit, git_dirty = git_state()
    log_dir = Path(args.log_dir) if args.log_dir else Path(tempfile.mkdtemp(prefix="bench_"))
    log_dir.mkdir(parents=True, exist_ok=True)
    print(f"Suite: {args.suite} | Backend: {args.backend} | Trials: {args.trials}")
    if args.backend == "openrouter":
        print(f"Model: {model} | Provider: {provider or 'auto'} | "
              f"Reasoning effort: {reasoning_effort or 'model default'}")
        if provider:
            print(f"Provider fallbacks: {'enabled' if args.allow_provider_fallbacks else 'disabled'}")
            print(f"Require parameters: {'yes' if args.require_provider_parameters else 'no'}")
        else:
            print("Provider routing: automatic (fallback setting does not apply)")
    print(f"Tests: {tests}")
    print(f"Logs:  {log_dir}")

    all_results = {}
    t_total = time.time()

    for test_name in tests:
        all_results[test_name] = []
        for trial in range(args.trials):
            log_path = log_dir / f"{test_name}_trial{trial+1}.jsonl"
            print(f"\n[{time.strftime('%H:%M:%S')}] "
                  f"{test_name} trial {trial+1}/{args.trials} ...", end="", flush=True)
            try:
                passed, wall, stdout, stderr = run_single_test(
                    test_name, args.suite, args.backend, log_path, model, provider,
                    args.allow_provider_fallbacks, args.require_provider_parameters,
                    reasoning_effort)
            except subprocess.TimeoutExpired:
                passed, wall = False, 1200.0
                print(f" TIMEOUT", flush=True)
                metrics = parse_log(log_path)
                all_results[test_name].append({
                    "passed": False, "wall_s": wall, "metrics": metrics,
                    "timed_out": True})
                continue

            metrics = parse_log(log_path)
            status_str = "PASS" if passed else "FAIL"
            wall_display = metrics["wall_s"] if metrics else wall
            print(f" {status_str} ({wall_display:.1f}s)", flush=True)

            if not passed and not metrics:
                print(f"  stderr tail: {stderr[-300:]}" if stderr else "  (no stderr)")

            all_results[test_name].append({
                "passed": passed, "wall_s": wall, "metrics": metrics})

    # Summary
    total_wall = time.time() - t_total
    print(f"\n{'═' * 60}")
    print(f"  SUMMARY — {args.suite}/{args.backend}, {args.trials} trials")
    print(f"  Total wall time: {total_wall:.0f}s")
    print(f"{'═' * 60}")

    for test_name in tests:
        print_report(test_name, all_results[test_name])

    # Save combined summary as JSON
    summary_path = log_dir / "summary.json"
    summary = {
        "suite": args.suite, "backend": args.backend, "trials": args.trials,
        "model": model, "provider": provider,
        "reasoning_effort": reasoning_effort,
        "allow_provider_fallbacks": args.allow_provider_fallbacks if provider else None,
        "require_provider_parameters": args.require_provider_parameters if provider else None,
        "git_commit": git_commit, "git_dirty": git_dirty,
        "total_wall_s": round(total_wall, 1),
        "tests": {},
    }
    for test_name, results in all_results.items():
        metrics_list = [r["metrics"] for r in results if r["metrics"]]
        pytest_passed = sum(1 for r in results if r["passed"])
        summary["tests"][test_name] = {
            "passed": pytest_passed,  # Back-compat alias for pytest_passed.
            "pytest_passed": pytest_passed,
            "agent_complete": sum(1 for m in metrics_list if m.get("status") == "complete"),
            "total": len(results),
            "agent_status": [m["status"] for m in metrics_list],
            "wall_s": [m["wall_s"] for m in metrics_list],
            "replans": [m["replans"] for m in metrics_list],
            "local_replans": [m["local_replans"] for m in metrics_list],
            "local_replans_ok": [m["local_replans_ok"] for m in metrics_list],
            "steps": [m["steps"] for m in metrics_list],
            "thinking_retries": [m["thinking_retries"] for m in metrics_list],
            "llm_calls": [m["llm_calls"] for m in metrics_list],
            "prompt_tokens": [m["prompt_tokens"] for m in metrics_list],
            "completion_tokens": [m["completion_tokens"] for m in metrics_list],
            "total_tokens": [m["total_tokens"] for m in metrics_list],
            "openrouter_cost": [m["openrouter_cost"] for m in metrics_list],
            "served_models": [m["served_models"] for m in metrics_list],
            "served_providers": [m["served_providers"] for m in metrics_list],
        }
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary JSON: {summary_path}")


if __name__ == "__main__":
    main()
