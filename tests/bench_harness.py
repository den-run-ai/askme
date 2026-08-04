#!/usr/bin/env python3
"""E01: Multi-trial integration test harness on top of AGENT_RUN_LOG.

Runs each integration test N times with separate JSONL log files,
then reports median + range per test. No changes to askme.py.

Usage:
    # 3 trials of easy tests on the explicit legacy E4B contract
    python3 tests/bench_harness.py --model gemma-4-e4b \
      --capability-profile legacy-e4b-m1-16k-v1 \
      --expected-served-model gemma-4-e4b

    # 5 trials of medium tests on openrouter
    python3 tests/bench_harness.py --suite medium --trials 5 --backend openrouter \
      --model google/gemma-4-26b-a4b-it \
      --expected-served-model google/gemma-4-26b-a4b-it-20260403

    # Single specific test
    python3 tests/bench_harness.py --test test_shell_and_write --trials 3 \
      --model gemma-4-e4b \
      --expected-served-model gemma-4-e4b

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
from typing import Any

AGENT_DIR = Path(__file__).parent.parent
PYTEST_DIAGNOSTIC_STREAM_CHARS = 2000

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
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_agent_integration.py",
            "--collect-only",
            "-q",
            "-m",
            "live_llm",
            "-k",
            k_expr,
        ],
        capture_output=True,
        text=True,
        cwd=str(AGENT_DIR),
    )
    tests = []
    for line in result.stdout.splitlines():
        if "::" in line and not line.startswith("="):
            # e.g. "tests/test_agent_integration.py::TestIntegration::test_shell_and_write"
            parts = line.strip().split("::")
            if len(parts) >= 3:
                tests.append(parts[-1])
    return tests


def run_single_test(
    test_name,
    suite,
    backend,
    log_path,
    model=None,
    provider=None,
    allow_fallbacks=False,
    require_parameters=True,
    reasoning_effort=None,
    capability_profile=None,
    reasoning_policy="gated",
):
    """Run one pytest test with AGENT_RUN_LOG set. Returns (passed, wall_seconds)."""
    class_name = SUITES[suite][backend]
    if class_name == "TestIntegration":
        k_expr = f"TestIntegration and not Medium and not Hard and {test_name}"
    else:
        k_expr = f"{class_name} and {test_name}"
    env = {
        **os.environ,
        "AGENT_RUN_LOG": str(log_path),
        "ASKME_RUN_LIVE_LLM_TESTS": "1",
        # The selected harness backend is part of the cell contract. Never
        # inherit an unrelated process-wide backend into a differently
        # labelled cell.
        "LLM_BACKEND": backend,
        "AGENT_REASONING_POLICY": reasoning_policy,
    }
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
    elif model:
        # Local model identity is part of the run contract; without this the
        # retained E09 12B records were mislabeled as the default E4B model.
        env["LLM_MODEL"] = model
    if capability_profile:
        env["LLM_CAPABILITY_PROFILE"] = capability_profile
    t0 = time.time()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_agent_integration.py",
            "-s",
            "-v",
            "-m",
            "live_llm",
            "-k",
            k_expr,
        ],
        capture_output=True,
        text=True,
        cwd=str(AGENT_DIR),
        env=env,
        timeout=1200,
    )
    wall = time.time() - t0
    passed = result.returncode == 0
    return passed, wall, result.stdout, result.stderr


def write_failure_diagnostic(log_dir, test_name, trial, stdout, stderr, *, timed_out=False):
    """Retain bounded pytest output for a failed synthetic integration trial."""

    def stream_tail(label, content):
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        content = content or ""
        omitted = max(0, len(content) - PYTEST_DIAGNOSTIC_STREAM_CHARS)
        note = f" ({omitted} earlier characters omitted)" if omitted else ""
        tail = content[-PYTEST_DIAGNOSTIC_STREAM_CHARS:].rstrip()
        return f"{label}{note}:\n{tail or '(empty)'}\n"

    path = log_dir / f"{test_name}_trial{trial}_pytest.txt"
    heading = "Pytest timeout diagnostics" if timed_out else "Pytest failure diagnostics"
    path.write_text(
        f"{heading} (bounded stream tails)\n\n"
        + stream_tail("stdout", stdout)
        + "\n"
        + stream_tail("stderr", stderr),
        encoding="utf-8",
    )
    return path.name


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

    run_starts = [e for e in events if e["event"] == "run_start"]
    run_ends = [e for e in events if e["event"] == "run_end"]
    if len(run_starts) != 1 or len(run_ends) != 1:
        raise ValueError(
            "benchmark log must contain exactly one run_start and one run_end "
            f"(found {len(run_starts)} and {len(run_ends)})"
        )
    run_start: dict[str, Any] = run_starts[0]
    run_end = run_ends[0]
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
    usage_complete = bool(tokens_events) and all(
        t.get("usage_observed") is True for t in tokens_events
    )

    total_prompt_tokens = sum(t.get("prompt", 0) for t in tokens_events)
    total_completion_tokens = sum(t.get("completion", 0) for t in tokens_events)
    total_openrouter_cost = sum(float(t.get("openrouter_cost") or 0) for t in tokens_events)
    observed_requested_models = [t.get("requested_model") for t in tokens_events]
    token_requested_model_observed = bool(tokens_events) and all(
        isinstance(model, str) and bool(model.strip()) for model in observed_requested_models
    )
    token_requested_models = (
        sorted(set(observed_requested_models)) if token_requested_model_observed else []
    )
    token_requested_model_valid = token_requested_model_observed and all(
        model == run_start.get("model") for model in observed_requested_models
    )
    observed_served_models = [t.get("served_model") for t in tokens_events]
    served_model_observed = bool(tokens_events) and all(
        isinstance(model, str) and bool(model.strip()) for model in observed_served_models
    )
    served_models = sorted(set(observed_served_models)) if served_model_observed else []
    served_model_sources = sorted(
        {t.get("served_model_source") for t in tokens_events if t.get("served_model_source")}
    )
    observed_served_model_sources = {"openrouter_metadata", "response"}
    served_model_provenance_valid = bool(tokens_events) and all(
        t.get("served_model_source") in observed_served_model_sources for t in tokens_events
    )
    observed_served_providers = [t.get("provider") for t in tokens_events]
    served_provider_observed = bool(tokens_events) and all(
        isinstance(provider, str) and bool(provider.strip())
        for provider in observed_served_providers
    )
    served_providers = sorted(set(observed_served_providers)) if served_provider_observed else []

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
        "usage_complete": usage_complete,
        "backend": run_start.get("backend", ""),
        "requested_model": run_start.get("model", ""),
        "requested_provider": run_start.get("provider", ""),
        "reasoning_effort": run_start.get("reasoning_effort", ""),
        "reasoning_policy": run_start.get("reasoning_policy", ""),
        "allow_provider_fallbacks": run_start.get("allow_provider_fallbacks"),
        "require_provider_parameters": run_start.get("require_parameters"),
        "capability_profile": (run_start.get("capability_profile") or {}).get("name", ""),
        "config_hash": run_start.get("config_hash", ""),
        "token_requested_models": token_requested_models,
        "token_requested_model_valid": token_requested_model_valid,
        "served_models": served_models,
        "served_model_observed": served_model_observed,
        "served_model_sources": served_model_sources,
        "served_model_provenance_valid": served_model_provenance_valid,
        "served_providers": served_providers,
        "served_provider_observed": served_provider_observed,
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
            ["git", "rev-parse", "HEAD"],
            cwd=str(AGENT_DIR),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(AGENT_DIR),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
        return revision, dirty
    except (OSError, subprocess.CalledProcessError):
        return "unknown", None


def print_report(test_name, trial_results):
    """Print summary table for one test across trials."""
    metrics = [r["metrics"] for r in trial_results if r["metrics"]]
    pytest_passes = sum(1 for r in trial_results if r["passed"])
    agent_completes = sum(
        1 for r in trial_results if r["metrics"] and r["metrics"].get("status") == "complete"
    )
    total = len(trial_results)
    valid_trials = sum(1 for r in trial_results if r.get("contract_valid", False))

    print(f"\n{'─' * 60}")
    print(
        f"  {test_name}  [pytest {pytest_passes}/{total}, agent complete "
        f"{agent_completes}/{total}, contract valid {valid_trials}/{total}]"
    )
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
        if not r.get("contract_valid", False):
            status = "INVALID_CONTRACT"
        else:
            status = "PYTEST_PASS" if r["passed"] else "PYTEST_FAIL"
        wall = r["metrics"]["wall_s"] if r["metrics"] else r["wall_s"]
        print(f"  trial {i + 1}: {status}  {wall:.1f}s", end="")
        if r["metrics"]:
            m = r["metrics"]
            agent = m.get("status", "unknown")
            lr = f", lr={m['local_replans_ok']}/{m['local_replans']}" if m["local_replans"] else ""
            print(
                f"  (agent={agent}, steps={m['steps']}, replans={m['replans']}{lr}, "
                f"retries={m['thinking_retries']})",
                end="",
            )
        print()


def positive_int(value):
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def main(argv=None):
    parser = argparse.ArgumentParser(description="E01: Multi-trial integration harness")
    parser.add_argument("--suite", choices=["easy", "medium", "hard"], default="easy")
    parser.add_argument("--backend", choices=["local", "openrouter"], default="local")
    parser.add_argument("--trials", type=positive_int, default=3)
    parser.add_argument("--test", help="Run a single test by name")
    parser.add_argument(
        "--model",
        help="Configured model ID (honors LLM_MODEL locally or OPENROUTER_MODEL remotely)",
    )
    parser.add_argument(
        "--capability-profile",
        choices=["generic-feature-scale-v1", "legacy-e4b-m1-16k-v1"],
        help="Immutable model-facing budget/context profile (honors LLM_CAPABILITY_PROFILE)",
    )
    parser.add_argument(
        "--expected-served-model",
        help="Exact model identity required in token events (honors ASKME_EXPECTED_SERVED_MODEL)",
    )
    parser.add_argument(
        "--provider", help="OpenRouter provider slug; use 'auto' for automatic routing"
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=["low", "medium", "high"],
        help="Baseline reasoning effort for always-on reasoners like "
        "openai/gpt-oss-20b (honors OPENROUTER_REASONING_EFFORT by default)",
    )
    parser.add_argument(
        "--reasoning-policy",
        choices=["gated", "off"],
        help="AskMe reasoning-policy arm (default: AGENT_REASONING_POLICY or gated)",
    )
    parser.add_argument(
        "--allow-provider-fallbacks",
        action="store_true",
        help="Allow OpenRouter to leave the requested provider (disabled by default)",
    )
    parser.add_argument(
        "--no-require-provider-parameters",
        dest="require_provider_parameters",
        action="store_false",
        default=True,
        help="Allow a provider that does not advertise all request parameters",
    )
    parser.add_argument("--list", action="store_true", help="List available tests")
    parser.add_argument("--log-dir", help="Directory for JSONL logs (default: auto tmpdir)")
    args = parser.parse_args(argv)

    if args.backend != "openrouter" and (
        args.provider
        or args.allow_provider_fallbacks
        or args.reasoning_effort
        or not args.require_provider_parameters
    ):
        parser.error("OpenRouter routing options require --backend openrouter")

    model = None
    provider = None
    reasoning_effort = None
    if args.backend == "openrouter":
        model = args.model or os.environ.get("OPENROUTER_MODEL", "google/gemma-4-26b-a4b-it")
        provider_arg = args.provider
        if provider_arg is None:
            provider = os.environ.get("OPENROUTER_PROVIDER", "Parasail")
        else:
            provider = "" if provider_arg.lower() == "auto" else provider_arg
        reasoning_effort = (
            args.reasoning_effort or os.environ.get("OPENROUTER_REASONING_EFFORT") or None
        )
    else:
        model = args.model or os.environ.get("LLM_MODEL", "local-model")
    capability_profile = (
        args.capability_profile
        or os.environ.get("LLM_CAPABILITY_PROFILE")
        or "generic-feature-scale-v1"
    )
    reasoning_policy = args.reasoning_policy or os.environ.get("AGENT_REASONING_POLICY") or "gated"
    expected_served_model = (
        args.expected_served_model or os.environ.get("ASKME_EXPECTED_SERVED_MODEL") or ""
    )
    expected_served_model = expected_served_model.strip()

    if args.list:
        for suite in SUITES:
            tests = discover_tests(suite, args.backend)
            print(f"\n{suite} ({args.backend}):")
            for t in tests:
                print(f"  {t}")
        return

    if not expected_served_model:
        parser.error(
            "--expected-served-model (or ASKME_EXPECTED_SERVED_MODEL) is required "
            "for a qualifying benchmark cell"
        )

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
    occupied_logs = [
        log_dir / f"{test_name}_trial{trial}.jsonl"
        for test_name in tests
        for trial in range(1, args.trials + 1)
        if (log_dir / f"{test_name}_trial{trial}.jsonl").exists()
    ]
    if occupied_logs:
        parser.error(
            "refusing to append to existing trial log(s): "
            + ", ".join(path.name for path in occupied_logs)
        )
    print(f"Suite: {args.suite} | Backend: {args.backend} | Trials: {args.trials}")
    print(f"Model: {model}")
    print(f"Capability profile: {capability_profile}")
    print(f"Expected served model: {expected_served_model or 'not pinned'}")
    if args.backend == "openrouter":
        print(
            f"Provider: {provider or 'auto'} | "
            f"Reasoning effort: {reasoning_effort or 'model default'}"
        )
        if provider:
            print(
                f"Provider fallbacks: {'enabled' if args.allow_provider_fallbacks else 'disabled'}"
            )
            print(f"Require parameters: {'yes' if args.require_provider_parameters else 'no'}")
        else:
            print("Provider routing: automatic (fallback setting does not apply)")
    print(f"Tests: {tests}")
    print(f"Logs:  {log_dir}")

    all_results: dict[str, list[dict[str, Any]]] = {}
    t_total = time.time()

    for test_name in tests:
        all_results[test_name] = []
        for trial in range(args.trials):
            log_path = log_dir / f"{test_name}_trial{trial + 1}.jsonl"
            print(
                f"\n[{time.strftime('%H:%M:%S')}] {test_name} trial {trial + 1}/{args.trials} ...",
                end="",
                flush=True,
            )
            timed_out = False
            try:
                passed, wall, stdout, stderr = run_single_test(
                    test_name,
                    args.suite,
                    args.backend,
                    log_path,
                    model,
                    provider,
                    args.allow_provider_fallbacks,
                    args.require_provider_parameters,
                    reasoning_effort,
                    capability_profile,
                    reasoning_policy,
                )
            except subprocess.TimeoutExpired as exc:
                passed, wall = False, 1200.0
                stdout, stderr = exc.stdout, exc.stderr
                timed_out = True
                print(" TIMEOUT", flush=True)

            pytest_diagnostic = None
            if not passed:
                pytest_diagnostic = write_failure_diagnostic(
                    log_dir, test_name, trial + 1, stdout, stderr, timed_out=timed_out
                )
                print(f"  pytest diagnostics: {pytest_diagnostic}")

            log_parse_error = None
            try:
                metrics = parse_log(log_path)
            except (json.JSONDecodeError, ValueError) as exc:
                metrics = None
                if isinstance(exc, json.JSONDecodeError):
                    log_parse_error = "{} at line {} column {}".format(
                        exc.msg, exc.lineno, exc.colno
                    )
                else:
                    log_parse_error = str(exc)
                print(f"  invalid JSONL: {log_parse_error}")

            if not timed_out:
                provider_route_valid = metrics is not None and (
                    args.backend != "openrouter"
                    or not provider
                    or (
                        metrics["served_provider_observed"]
                        and (
                            args.allow_provider_fallbacks
                            or all(
                                served.casefold() == provider.casefold()
                                for served in metrics["served_providers"]
                            )
                        )
                    )
                )
                route_valid = (
                    metrics is not None
                    and metrics["served_model_observed"]
                    and metrics["served_model_provenance_valid"]
                    and metrics["served_models"] == [expected_served_model]
                    and provider_route_valid
                )
                contract_valid = (
                    metrics is not None
                    and metrics["backend"] == args.backend
                    and metrics["requested_model"] == model
                    and metrics["token_requested_model_valid"]
                    and metrics["usage_complete"]
                    and metrics["requested_provider"] == (provider or "")
                    and metrics["reasoning_policy"] == reasoning_policy
                    and (
                        args.backend != "openrouter"
                        or metrics["reasoning_effort"] == (reasoning_effort or "")
                    )
                    and (
                        args.backend != "openrouter"
                        or (
                            metrics["allow_provider_fallbacks"] is args.allow_provider_fallbacks
                            and metrics["require_provider_parameters"]
                            is args.require_provider_parameters
                        )
                    )
                    and metrics["capability_profile"] == capability_profile
                    and isinstance(metrics["config_hash"], str)
                    and len(metrics["config_hash"]) == 16
                    and all(char in "0123456789abcdef" for char in metrics["config_hash"].lower())
                    and route_valid
                )
                status_str = "PASS" if passed else "FAIL"
                if not contract_valid:
                    status_str = "INVALID-CONTRACT"
                wall_display = metrics["wall_s"] if metrics else wall
                print(f" {status_str} ({wall_display:.1f}s)", flush=True)
            else:
                route_valid = False
                contract_valid = False

            result = {
                "passed": passed,
                "route_valid": route_valid,
                "contract_valid": contract_valid,
                "wall_s": wall,
                "metrics": metrics,
                "pytest_diagnostic": pytest_diagnostic,
                "log_parse_error": log_parse_error,
            }
            if timed_out:
                result["timed_out"] = True
            all_results[test_name].append(result)

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
        "suite": args.suite,
        "backend": args.backend,
        "trials": args.trials,
        "model": model,
        "provider": provider,
        "reasoning_effort": reasoning_effort or "",
        "reasoning_policy": reasoning_policy,
        "capability_profile": capability_profile,
        "expected_served_model": expected_served_model,
        "allow_provider_fallbacks": args.allow_provider_fallbacks if provider else None,
        "require_provider_parameters": args.require_provider_parameters if provider else None,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "total_wall_s": round(total_wall, 1),
        "tests": {},
    }
    for test_name, results in all_results.items():
        metrics_list = [r["metrics"] for r in results if r["metrics"]]
        pytest_passed = sum(1 for r in results if r["passed"])
        summary["tests"][test_name] = {
            "passed": pytest_passed,  # Back-compat alias for pytest_passed.
            "pytest_passed": pytest_passed,
            "valid_trials": sum(1 for r in results if r.get("contract_valid", False)),
            "valid_passes": sum(
                1 for r in results if r["passed"] and r.get("contract_valid", False)
            ),
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
            "usage_complete": [m["usage_complete"] for m in metrics_list],
            "served_models": [m["served_models"] for m in metrics_list],
            "served_model_observed": [m["served_model_observed"] for m in metrics_list],
            "served_model_sources": [m["served_model_sources"] for m in metrics_list],
            "served_model_provenance_valid": [
                m["served_model_provenance_valid"] for m in metrics_list
            ],
            "served_provider_observed": [m["served_provider_observed"] for m in metrics_list],
            "recorded_backends": [m["backend"] for m in metrics_list],
            "requested_models": [m["requested_model"] for m in metrics_list],
            "token_requested_models": [m["token_requested_models"] for m in metrics_list],
            "token_requested_model_valid": [m["token_requested_model_valid"] for m in metrics_list],
            "requested_providers": [m["requested_provider"] for m in metrics_list],
            "recorded_reasoning_efforts": [m["reasoning_effort"] for m in metrics_list],
            "recorded_reasoning_policies": [m["reasoning_policy"] for m in metrics_list],
            "recorded_allow_provider_fallbacks": [
                m["allow_provider_fallbacks"] for m in metrics_list
            ],
            "recorded_require_provider_parameters": [
                m["require_provider_parameters"] for m in metrics_list
            ],
            "capability_profiles": [m["capability_profile"] for m in metrics_list],
            "config_hashes": [m["config_hash"] for m in metrics_list],
            "route_valid": [r.get("route_valid", True) for r in results],
            "contract_valid": [r.get("contract_valid", False) for r in results],
            "served_providers": [m["served_providers"] for m in metrics_list],
            "pytest_diagnostics": [r.get("pytest_diagnostic") for r in results],
            "timed_out": [bool(r.get("timed_out")) for r in results],
            "log_parse_errors": [r.get("log_parse_error") for r in results],
        }
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary JSON: {summary_path}")
    return (
        1
        if any(not r.get("contract_valid", False) for rs in all_results.values() for r in rs)
        else 0
    )


if __name__ == "__main__":
    sys.exit(main())
