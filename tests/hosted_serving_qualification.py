#!/usr/bin/env python3
"""Register two synthetic OpenRouter contracts; never execute model actions.

Each probe has one HTTP attempt and a 60-second requests read timeout. The
supervisor bounds both probes together to 150 seconds, including drip feeds.
Credentials come only from the launching environment and are never recorded.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

from featurebench.canary_audit import runtime_source_paths

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
API = "https://openrouter.ai/api/v1/chat/completions"
CASES = [
    {"name": "planner", "kind": "plan", "approximate_prompt_tokens": 600, "max_tokens": 768},
    {
        "name": "native_action",
        "kind": "action",
        "approximate_prompt_tokens": 2000,
        "max_tokens": 512,
    },
]
SYNTHETIC = "Synthetic observation: only alpha.txt and beta.txt exist in the example directory. "


def save(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_protocol(protocol):
    resolved = dict(protocol)
    if not resolved.get("protocol") or not resolved.get("model_alias"):
        raise ValueError("A serving protocol and requested model are required")
    if resolved.get("backend") != "openrouter" or resolved.get("api_url") != API:
        raise ValueError("This qualifier requires the official OpenRouter endpoint")
    route = resolved["openrouter"]
    for key in ("provider", "expected_response_provider", "expected_response_model"):
        if not isinstance(route.get(key), str) or not route[key]:
            raise ValueError(f"Missing frozen route field: {key}")
    cap = Decimal(str(resolved["cost_control"]["cap_usd"]))
    if not cap.is_finite() or not Decimal(0) < cap <= Decimal("0.05"):
        raise ValueError("Qualification cost cap must be positive and at most USD 0.05")
    if resolved.get("cases", CASES) != CASES:
        raise ValueError("This qualification uses exactly the two fixed synthetic cases")
    resolved.update(cases=CASES, trials_per_case=1, read_timeout_s=60, wall_timeout_s=150)
    resolved.setdefault("seed", 20260908)
    return resolved


def build_request(case, protocol, key):
    import askme

    instruction = (
        "Plan creating hello.txt containing hi and checking its content."
        if case["kind"] == "plan"
        else "Call write with arg hello.txt and content exactly hi followed by newline."
    )
    settings = askme.LLMSettings(
        backend="openrouter",
        api=API,
        model=protocol["model_alias"],
        api_key=key,
        provider=protocol["openrouter"]["provider"],
        allow_fallbacks=False,
        require_parameters=True,
        reasoning_effort="",
        timeout=60,
    )
    messages = [
        {
            "role": "system",
            "content": askme.SYSTEM_PLAN if case["kind"] == "plan" else askme.SYSTEM_STEP,
        },
        {"role": "user", "content": instruction},
    ]
    body, headers, _ = askme._build_llm_request(
        messages,
        case["max_tokens"],
        None,
        False,
        settings=settings,
        expect=case["kind"],
    )
    # Four characters/token is only a fixture-size approximation. Returned usage
    # is the observed count, including provider templates and actual tool schemas.
    remaining_chars = max(0, case["approximate_prompt_tokens"] * 4 - len(json.dumps(body)))
    padding = (SYNTHETIC * (remaining_chars // len(SYNTHETIC) + 1))[:remaining_chars]
    messages[1]["content"] = padding + "\n" + instruction
    if protocol["openrouter"].get("quantizations"):
        body["provider"]["quantizations"] = protocol["openrouter"]["quantizations"]
    return body, headers


def check_contract(case, payload):
    import askme

    choice = payload["choices"][0]
    finish = choice.get("finish_reason", "")
    if case["kind"] == "action":
        action, _, _ = askme._decode_tool_call_reply(payload, finish)
        if action.get("action") != "write" or action.get("arg") != "hello.txt":
            raise ValueError("Expected synthetic write hello.txt")
        if action.get("content") != "hi\n":
            raise ValueError("Synthetic content differs from hi followed by newline")
    else:
        plan, _, _ = askme._decode_action_reply(askme._extract_message_text(payload), finish)
        tasks = plan.get("tasks")
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 3:
            raise ValueError("Expected one to three plan tasks")
        if any(not isinstance(task, str) or not task.strip() for task in tasks):
            raise ValueError("Plan tasks must be nonempty strings")
    return finish


def guard_failed(path):
    if not path.exists():
        return False
    try:
        for line in path.read_text().splitlines():
            if line:
                event = json.loads(line)
                if event.get("guard_error") or event.get("event") == "request_refused":
                    return True
        return False
    except json.JSONDecodeError:
        return True


def run_probes(protocol, output, *, post_factory=None, clock=time.monotonic):
    import external_repo_trial as trial

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise ValueError("OPENROUTER_API_KEY is required in the launching environment")
    factory = trial.record_post if post_factory is None else post_factory
    path = output / "http.jsonl"
    post = factory(path, protocol["seed"], protocol=protocol)
    results = []
    for case in protocol["cases"]:
        body, headers = build_request(case, protocol, key)
        # Bodies contain only synthetic fixtures. Authorization headers are absent.
        save(output / (case["name"] + "-request.json"), body)
        started = clock()
        record = {"name": case["name"], "qualified": False, "post_invocations": 1}
        try:
            response = post(API, json=body, headers=headers, timeout=60)
            response.raise_for_status()
            payload = response.json()
            record.update(
                finish_reason=check_contract(case, payload),
                usage=payload.get("usage"),
                model=payload.get("model"),
                provider=payload.get("provider"),
            )
            record["qualified"] = True
        except Exception as error:
            record["error"] = {"type": type(error).__name__, "detail": str(error)}
        record["wall_s"] = clock() - started
        if record["wall_s"] > 60:
            record.update(qualified=False, timing_error="total_call_limit_exceeded")
        results.append(record)
        save(output / "probe-results.json", results)
        if guard_failed(path):
            break
    return results


def run(protocol, output, *, process_runner=None):
    from external_repo_trial import HostedRequestBudget

    from actions import CapturedProcess

    protocol = resolve_protocol(protocol)
    output.mkdir(parents=True, exist_ok=False)
    save(output / "protocol.json", protocol)
    hashes = {name: digest(path) for name, path in runtime_source_paths(ROOT / "askme.py").items()}
    registration = {
        "protocol_sha256": digest(output / "protocol.json"),
        "protocol": protocol,
        "runner_sha256": digest(Path(__file__)),
        "request_guard_sha256": digest(ROOT / "tests" / "external_repo_trial.py"),
        "runtime_discovery_sha256": digest(Path(runtime_source_paths.__code__.co_filename)),
        "runtime_sha256": hashes,
        "registered_at_unix": time.time(),
        "python": sys.version,
    }
    save(output / "registration.json", registration)
    with (output / "started.json").open("x") as marker:
        json.dump({"started_at_unix": time.time(), "trials_per_case": 1}, marker)
    result = {
        "qualified": False,
        "protocol": protocol["protocol"],
        "model_alias": protocol["model_alias"],
        "api_url": API,
        "registration_sha256": digest(output / "registration.json"),
        "results": [],
        "reason": "incomplete",
        "runtime_sha256": hashes,
    }
    runner = CapturedProcess.run if process_runner is None else process_runner
    try:
        process = runner(
            [sys.executable, str(Path(__file__).resolve()), "--worker", str(output)],
            timeout=150,
            cwd=ROOT,
        )
        result["process"] = {"exit_code": process.returncode, "timed_out": False}
        (output / "stdout.txt").write_text(process.stdout)
        (output / "stderr.txt").write_text(process.stderr)
    except subprocess.TimeoutExpired:
        result["process"] = {"exit_code": None, "timed_out": True}
    except Exception as error:
        result["infrastructure_error"] = f"{type(error).__name__}: {error}"
    finally:
        if (output / "probe-results.json").exists():
            try:
                result["results"] = json.loads((output / "probe-results.json").read_text())
            except (OSError, ValueError) as error:
                result["results_error"] = f"{type(error).__name__}: {error}"
        try:
            result.update(HostedRequestBudget.summary(output / "http.jsonl"))
        except Exception as error:
            result.update(api_cost_complete=False, cost_error=f"{type(error).__name__}: {error}")
        result["qualified"] = (
            result.get("process", {}).get("exit_code") == 0
            and len(result["results"]) == len(CASES)
            and all(item["qualified"] for item in result["results"])
            and result.get("api_cost_complete") is True
            and not guard_failed(output / "http.jsonl")
        )
        result["reason"] = (
            "all_serving_checks_passed" if result["qualified"] else "serving_checks_failed"
        )
        save(output / "qualification.json", result)
    return result


def worker(output):
    registration = json.loads((output / "registration.json").read_text())
    checks = {
        output / "protocol.json": registration["protocol_sha256"],
        Path(__file__): registration["runner_sha256"],
        ROOT / "tests" / "external_repo_trial.py": registration["request_guard_sha256"],
        Path(runtime_source_paths.__code__.co_filename): registration.get(
            "runtime_discovery_sha256"
        ),
    }
    if any(digest(path) != expected for path, expected in checks.items()):
        raise ValueError("Registered code or protocol changed before hosted probes")
    runtime = {name: digest(path) for name, path in runtime_source_paths(ROOT / "askme.py").items()}
    if runtime != registration["runtime_sha256"]:
        raise ValueError("Registered runtime changed before hosted probes")
    run_probes(registration["protocol"], output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
        return 0
    if args.protocol is None or args.output is None:
        parser.error("--protocol and --output are required")
    result = run(json.loads(args.protocol.read_text()), args.output.resolve())
    print(json.dumps(result, indent=2))
    return 0 if result["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
