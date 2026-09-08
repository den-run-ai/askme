#!/usr/bin/env python3
"""One predeclared real Requests repair; qualification never makes model calls."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

from featurebench.canary_audit import runtime_source_paths

TASK = Path(__file__).parent / "external_repo/requests_pickle_v1"
DEFAULT_API_URL = "http://127.0.0.1:8080/v1/chat/completions"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def command(args, cwd, **kwargs):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, **kwargs)


def git(workspace, *args):
    return command(["git", *args], workspace).stdout.decode()


def export_source(source, revision, target):
    """Export real pinned files without exposing upstream solutions in Git history."""
    target.mkdir()
    with tempfile.TemporaryDirectory() as index_dir:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(index_dir) / "index"))
        command(["git", "read-tree", revision], source, env=env)
        command(
            ["git", "checkout-index", "--all", f"--prefix={target.resolve()}/"], source, env=env
        )
    git(target, "init", "-q")
    git(target, "add", "--all")
    git(
        target,
        "-c",
        "user.name=AskMe evaluation",
        "-c",
        "user.email=eval@example.invalid",
        "commit",
        "-qm",
        "Pinned upstream task baseline",
    )
    return git(target, "rev-parse", "HEAD").strip()


def patch(workspace, baseline):
    # Include untracked files, test edits, removals, mode changes, and binary data.
    # HEAD is model-controlled: a committed repair must remain in the candidate.
    git(workspace, "add", "--all")
    return git(workspace, "diff", "--cached", "--binary", baseline)


def allowed_changes(paths):
    return all(p == "src/requests/exceptions.py" or p.startswith("tests/") for p in paths)


def evaluate(workspace, task_dir=TASK):
    """Fresh interpreter and external checks; no evaluator file enters the checkout."""
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(task_dir / "acceptance.py"), str(workspace)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as error:

        def decoded(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else value or ""

        return {
            "accepted": False,
            "exit_code": None,
            "error": "evaluator_timeout",
            "stdout": decoded(error.stdout),
            "stderr": decoded(error.stderr),
        }
    try:
        evidence = json.loads(result.stdout)
    except json.JSONDecodeError:
        evidence = None
    valid = evidence == {"accepted": True, "checks": 21}
    return {
        "accepted": result.returncode == 0 and valid,
        "exit_code": result.returncode,
        "error": None if valid else "acceptance_failed_or_invalid_evaluator_output",
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def qualify(source, output, protocol, task_dir=TASK):
    records = {}
    for arm in ("baseline", "noop", "gold"):
        with tempfile.TemporaryDirectory(prefix="askme-control-") as directory:
            workspace = Path(directory) / "repo"
            baseline = export_source(source, protocol["target_revision"], workspace)
            if arm == "gold":
                command(["git", "apply", str(task_dir / "gold.patch")], workspace)
            elif arm == "noop":
                file = workspace / "src/requests/exceptions.py"
                file.write_text(file.read_text() + "\n# Harmless non-empty control.\n")
            control_patch = patch(workspace, baseline)
            records[arm] = {**evaluate(workspace, task_dir), "patch_nonempty": bool(control_patch)}
            (output / f"control-{arm}.patch").write_text(control_patch)
    save(output / "qualification.json", records)
    if not controls_valid(records):
        raise RuntimeError("Control qualification failed; no model calls authorized")
    return records


def controls_valid(records):
    return (
        not records["baseline"]["accepted"]
        and not records["noop"]["accepted"]
        and records["noop"]["patch_nonempty"]
        and records["gold"]["accepted"]
        and records["gold"]["patch_nonempty"]
    )


def serving_qualification(protocol, task_dir):
    """Check only declared serving gates; the retired v1 protocol stays unchanged."""
    if "serving_qualification" not in protocol:
        return None
    gate = protocol["serving_qualification"]
    path = task_dir / gate["path"]
    if digest(path) != gate["sha256"]:
        raise ValueError("Serving qualification digest mismatch")
    record = json.loads(path.read_text())
    if record.get("qualified") is not True:
        raise ValueError("Serving qualification did not pass")
    expected = {
        "protocol": gate["protocol"],
        "model_alias": protocol["model_alias"],
        "api_url": protocol.get("api_url", DEFAULT_API_URL),
    }
    for field, value in expected.items():
        if record.get(field) != value:
            raise ValueError(f"Serving qualification mismatch: {field}")
    return path


def prepare(source, harness, output, task_dir=TASK):
    protocol = json.loads((task_dir / "protocol.json").read_text())
    serving_path = serving_qualification(protocol, task_dir)
    output.mkdir(parents=True, exist_ok=False)
    revision = git(harness, "rev-parse", "HEAD").strip()
    if revision != protocol["harness_revision"]:
        raise ValueError("Harness revision differs from the predeclared revision")
    runtime = {
        name: digest(path) for name, path in runtime_source_paths(harness / "askme.py").items()
    }
    if runtime != protocol["runtime_sha256"]:
        raise ValueError("Runtime digest mismatch: protocol must pin every runtime module")
    for name in ("protocol.json", "prompt.md", "gold.patch", "acceptance.py"):
        shutil.copyfile(task_dir / name, output / name)
    if serving_path is not None:
        shutil.copyfile(serving_path, output / "serving-qualification.json")
    shutil.copyfile(__file__, output / "external_repo_trial.py")
    registration = {
        "protocol": protocol,
        "harness_revision": revision,
        "registered_at_unix": time.time(),
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "source_sha256": {p.name: digest(p) for p in task_dir.iterdir() if p.is_file()},
        "runner_sha256": digest(__file__),
        "runtime_discovery_sha256": digest(runtime_source_paths.__code__.co_filename),
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("requests", "urllib3", "certifi", "charset-normalizer", "idna")
        },
    }
    save(output / "registration.json", registration)
    qualify(source, output, protocol, task_dir)
    return protocol


class HostedRequestBudget:
    """Pin the paid route and reserve conservative token charges before every attempt."""

    def __init__(self, protocol):
        if protocol["api_url"] != OPENROUTER_API_URL:
            raise ValueError("Hosted trials require the exact official OpenRouter API URL")
        self.protocol = protocol
        self.route = protocol["openrouter"]
        for field in ("provider", "expected_response_provider", "expected_response_model"):
            if not isinstance(self.route.get(field), str) or not self.route[field]:
                raise ValueError(f"Missing hosted route field: {field}")
        control = protocol["cost_control"]
        self.cap = self.amount(control["cap_usd"])
        if not 0 < self.cap <= Decimal("0.50"):
            raise ValueError("Hosted trial budget must be positive and at most USD 0.50")
        self.prompt_price = self.amount(control["prompt_usd_per_token"])
        self.completion_price = self.amount(control["completion_usd_per_token"])
        if not self.prompt_price or not self.completion_price:
            raise ValueError("Hosted catalog token prices must be positive")
        self.overhead = control["prompt_overhead_tokens"]
        if type(self.overhead) is not int or self.overhead < 16384:
            raise ValueError("Hosted prompt overhead must reserve at least 16384 tokens")
        self.committed = Decimal(0)
        self.halted = False

    @staticmethod
    def amount(value):
        try:
            amount = Decimal(str(value))
        except InvalidOperation as error:
            raise ValueError("Missing or invalid API cost") from error
        if not amount.is_finite() or amount < 0:
            raise ValueError("API cost must be finite and nonnegative")
        return amount

    def reserve(self, api, body):
        if self.halted:
            raise RuntimeError("Hosted cost/route guard halted further requests")
        if api != OPENROUTER_API_URL or body.get("model") != self.protocol["model_alias"]:
            raise ValueError("Hosted endpoint or requested model differs from protocol")
        expected = {
            "order": [self.route["provider"]],
            "allow_fallbacks": False,
            "require_parameters": True,
        }
        if "quantizations" in self.route:
            expected["quantizations"] = self.route["quantizations"]
        route = body.get("provider") or {}
        if any(route.get(field) != value for field, value in expected.items()):
            raise ValueError("Hosted provider route differs from protocol")
        output_tokens = body.get("max_tokens")
        if type(output_tokens) is not int or output_tokens <= 0 or body.get("stream"):
            raise ValueError("Hosted requests require bounded non-streaming output")
        # UTF-8 bytes overestimate textual token counts. Reserve extra template/tool
        # tokens as well; catalog prices and this overhead are frozen in the protocol.
        prompt_bound = len(json.dumps(body, ensure_ascii=False).encode("utf-8")) + self.overhead
        estimate = prompt_bound * self.prompt_price + output_tokens * self.completion_price
        if self.committed + estimate > self.cap:
            raise RuntimeError("Hosted request would exceed the remaining dollar budget")
        self.committed += estimate
        return estimate

    def settle(self, payload, reserved):
        # Unknown cost, unexpected routing or changed prices block further calls.
        # Transport failures retain their full reservation across client retries.
        self.halted = True
        if not isinstance(payload, dict) or not isinstance(payload.get("usage"), dict):
            raise ValueError("Hosted response has no priced usage record")
        cost = self.amount(payload["usage"].get("cost"))
        self.committed += cost - reserved
        if cost > reserved:
            raise RuntimeError("Actual hosted charge exceeded the registered cost estimate")
        if payload.get("provider") != self.route["expected_response_provider"]:
            raise ValueError("Actual hosted provider differs from protocol")
        if payload.get("model") != self.route["expected_response_model"]:
            raise ValueError("Actual hosted model differs from protocol")
        self.halted = False

    @classmethod
    def summary(cls, path):
        total, upper = Decimal(0), Decimal(0)
        requests_count = priced = 0
        lines = path.read_text().splitlines() if path.exists() else []
        for line in lines:
            try:
                event = json.loads(line)
                if "budget_committed_usd" in event:
                    upper = cls.amount(event["budget_committed_usd"])
                if event.get("event") == "request":
                    requests_count += 1
                if event.get("event") == "response":
                    payload = json.loads(event["body"])
                    usage = payload.get("usage") if isinstance(payload, dict) else None
                    if isinstance(usage, dict):
                        total += cls.amount(usage.get("cost"))
                        priced += 1
            except (ValueError, TypeError):
                continue
        return {
            "api_cost_usd": float(total),
            "api_cost_complete": priced == requests_count,
            "api_cost_unpriced_requests": max(0, requests_count - priced),
            "api_cost_upper_bound_usd": float(max(total, upper)),
            "api_cost_note": "Sum of response usage.cost; unknown attempts retain the registered "
            "token-price estimate. The upper bound depends on unchanged catalog pricing.",
        }


def record_post(path, seed, protocol=None):
    """Record complete requests/replies at the supported HTTP injection seam."""
    import requests

    budget = (
        HostedRequestBudget(protocol)
        if protocol and protocol.get("backend") == "openrouter"
        else None
    )
    decode_response = json.loads

    def emit(value):
        with path.open("a") as log:
            log.write(json.dumps(value) + "\n")

    def post(api, *, json, headers, timeout):
        body = dict(json, seed=seed)
        started = time.monotonic()
        accounting = {}
        if budget:
            if "quantizations" in budget.route:
                body["provider"] = dict(
                    body.get("provider") or {}, quantizations=budget.route["quantizations"]
                )
            try:
                reserved = budget.reserve(api, body)
            except (ValueError, RuntimeError) as error:
                emit({"event": "request_refused", "reason": str(error)})
                raise
            accounting = {
                "estimated_max_cost_usd": str(reserved),
                "budget_committed_usd": str(budget.committed),
            }
        emit({"event": "request", "api": api, "body": body, "timeout": timeout, **accounting})
        try:
            if budget:
                with requests.Session() as session:
                    session.trust_env = False
                    response = session.post(
                        api, json=body, headers=headers, timeout=timeout, allow_redirects=False
                    )
            else:
                response = requests.post(api, json=body, headers=headers, timeout=timeout)
        except Exception as error:
            emit(
                {
                    "event": "transport_error",
                    "type": type(error).__name__,
                    "detail": str(error),
                    "wall_s": time.monotonic() - started,
                }
            )
            raise
        emit(
            {
                "event": "response",
                "status": response.status_code,
                "body": response.text,
                "wall_s": time.monotonic() - started,
            }
        )
        if budget:
            guard_error = None
            try:
                if response.status_code != 200:
                    budget.halted = True
                    raise ValueError("Hosted HTTP response was not successful")
                budget.settle(decode_response(response.text), reserved)
            except (ValueError, RuntimeError) as error:
                budget.halted = True
                guard_error = str(error)
                raise
            finally:
                emit(
                    {
                        "event": "cost_ledger",
                        "budget_committed_usd": str(budget.committed),
                        "guard_error": guard_error,
                    }
                )
        return response

    return post


def worker(harness, workspace, output, task_dir=TASK):
    protocol = json.loads((output / "protocol.json").read_text())
    hosted = protocol.get("backend") == "openrouter"
    api_key = os.environ.pop("OPENROUTER_API_KEY", "") if hosted else ""
    if hosted and not api_key:
        raise ValueError("Hosted worker requires an explicitly inherited API credential")
    sys.path.insert(0, str(harness))
    import askme

    if hosted:
        os.environ.pop("OPENROUTER_API_KEY", None)
    if protocol != json.loads((task_dir / "protocol.json").read_text()):
        raise ValueError("Worker task protocol differs from registration")
    settings = replace(
        askme.LLMSettings.from_env(),
        capability_profile=askme.CapabilityProfile(**protocol["capability_profile"]),
    )
    if hosted:
        settings = replace(
            settings,
            backend="openrouter",
            api=protocol["api_url"],
            model=protocol["model_alias"],
            api_key=api_key,
            provider=protocol["openrouter"]["provider"],
            allow_fallbacks=False,
            require_parameters=True,
            reasoning_effort="",
        )
    client = askme.LLMClient(
        settings, post=record_post(output / "http.jsonl", protocol["seed"], protocol)
    )
    result = askme.run_result(
        (output / "prompt.md").read_text(),
        str(workspace),
        config=askme.RunConfig(llm=settings, **protocol["run_config"]),
        dependencies=askme.RunDependencies(llm_client=client),
    )
    save(output / "agent-result.json", result)


def summarize(output, process, elapsed, acceptance, applying, changes, protocol=None):
    protocol = protocol or {}
    events = []
    if (output / "agent.jsonl").exists():
        for line in (output / "agent.jsonl").read_text().splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                events.append({"event": "invalid_jsonl", "raw": line})
    tokens = [e for e in events if e.get("event") == "tokens"]
    end = next((e for e in reversed(events) if e.get("event") == "run_end"), {})
    resolved = applying and bool(changes) and allowed_changes(changes) and acceptance["accepted"]
    summary = {
        "decision": "resolved_this_one_task" if resolved else "did_not_resolve",
        "patch_applies": applying,
        "changed_files": changes,
        "scope_valid": allowed_changes(changes),
        "acceptance": acceptance,
        "agent_status": end.get("status", "no_terminal_record"),
        "process": process,
        "trial_wall_s": elapsed,
        "configured_timeout_s": protocol.get("wall_timeout_seconds", 720),
        "observed_prompt_tokens": sum(e.get("prompt", 0) for e in tokens),
        "observed_completion_tokens": sum(e.get("completion", 0) for e in tokens),
        "usage_records": len(tokens),
        "usage_complete": False,
        "usage_note": "Observed totals are lower bounds; failed/interrupted HTTP attempts "
        "may not return usage. Raw request/response records are retained.",
        "api_cost_usd": protocol.get("api_cost_usd", 0),
        "runner_cost": protocol.get(
            "runner_cost",
            "existing local hardware; API charges reported separately"
            if protocol.get("backend") == "openrouter"
            else "standard public macOS runner; no API charge",
        ),
        "limitations": protocol.get(
            "limitations",
            "One historical, potentially training-contaminated bug; stdlib json only; "
            "7 GB CI host is not the 16 GB Gemma reference. No reliability inference.",
        ),
    }
    if "runner" in protocol:
        summary["runner"] = protocol["runner"]
    if protocol.get("backend") == "openrouter":
        summary.update(HostedRequestBudget.summary(output / "http.jsonl"))
    save(output / "summary.json", summary)
    print(json.dumps(summary, indent=2))
    return summary


def run_trial(source, harness, output, task_dir=TASK):
    protocol = json.loads((output / "protocol.json").read_text())
    registration = json.loads((output / "registration.json").read_text())
    if protocol != registration["protocol"]:
        raise ValueError("Registered protocol changed")
    if git(harness, "rev-parse", "HEAD").strip() != protocol["harness_revision"]:
        raise ValueError("Registered harness revision changed")
    runtime = {
        name: digest(path) for name, path in runtime_source_paths(harness / "askme.py").items()
    }
    if runtime != protocol["runtime_sha256"]:
        raise ValueError("Registered runtime changed: protocol must pin every runtime module")
    if digest(runtime_source_paths.__code__.co_filename) != registration.get(
        "runtime_discovery_sha256"
    ):
        raise ValueError("Registered runtime discovery changed")
    for name, expected in registration["source_sha256"].items():
        if digest(task_dir / name) != expected:
            raise ValueError(f"Registered evaluator/task changed: {name}")
    if digest(__file__) != registration["runner_sha256"]:
        raise ValueError("Registered trial runner changed")
    if digest(output / "prompt.md") != registration["source_sha256"]["prompt.md"]:
        raise ValueError("Registered prompt changed")
    qualification = json.loads((output / "qualification.json").read_text())
    if not controls_valid(qualification):
        raise ValueError("Controls are not qualified")
    if (
        serving_qualification(protocol, task_dir) is not None
        and digest(output / "serving-qualification.json")
        != protocol["serving_qualification"]["sha256"]
    ):
        raise ValueError("Registered serving qualification changed")
    backend = protocol.get("backend", "local")
    if backend not in {"local", "openrouter"}:
        raise ValueError("Unknown trial backend")
    if backend == "openrouter":
        HostedRequestBudget(protocol)
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise ValueError("Hosted trial requires an explicitly inherited API credential")
    # Exclusive marker prevents accidentally overwriting or retrying an outcome.
    with (output / "trial-started.json").open("x") as marker:
        json.dump({"trial": 1, "started_at_unix": time.time()}, marker)
    sys.path.insert(0, str(harness))
    from actions import CapturedProcess

    with tempfile.TemporaryDirectory(prefix="askme-external-") as directory:
        workspace = Path(directory) / "repo"
        baseline = export_source(source, protocol["target_revision"], workspace)
        save(
            output / "workspace-baseline.json",
            {
                "commit": baseline,
                "tree": git(workspace, "rev-parse", "HEAD^{tree}").strip(),
                "upstream_revision": protocol["target_revision"],
            },
        )
        env = {
            key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG") if key in os.environ
        }
        env.update(
            LLM_BACKEND=backend,
            LLM_MODEL=protocol["model_alias"],
            LLM_API_URL=protocol.get("api_url", DEFAULT_API_URL),
            AGENT_RUN_LOG=str(output / "agent.jsonl"),
            PYTHONPATH=str(workspace / "src"),
            PYTHONDONTWRITEBYTECODE="1",
        )
        if backend == "openrouter":
            env["OPENROUTER_API_KEY"] = os.environ["OPENROUTER_API_KEY"]
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        started = time.monotonic()
        process = {"timed_out": False}
        try:
            completed = CapturedProcess.run(
                [
                    sys.executable,
                    "-I",
                    str(Path(__file__).resolve()),
                    "worker",
                    "--harness",
                    str(harness),
                    "--source",
                    str(workspace),
                    "--output",
                    str(output),
                    "--task-dir",
                    str(task_dir.resolve()),
                ],
                cwd=str(workspace),
                env=env,
                timeout=protocol["wall_timeout_seconds"],
            )
            process["exit_code"] = completed.returncode
            stdout, stderr = completed.stdout, completed.stderr
        except subprocess.TimeoutExpired as error:
            process = {"timed_out": True, "exit_code": None}
            stdout, stderr = error.stdout or b"", error.stderr or b""
        elapsed = time.monotonic() - started
        for name, text in (("stdout.txt", stdout), ("stderr.txt", stderr)):
            (output / name).write_bytes(text.encode() if isinstance(text, str) else text)
        candidate_patch = patch(workspace, baseline)
        (output / "candidate.patch").write_text(candidate_patch)
        changes = git(workspace, "diff", "--cached", "--name-only", baseline).splitlines()
        shutil.make_archive(str(output / "workspace"), "gztar", workspace)
        evaluation = Path(directory) / "evaluation"
        export_source(source, protocol["target_revision"], evaluation)
        applied = subprocess.run(
            ["git", "apply", "--allow-empty", "--binary", "-"],
            input=candidate_patch,
            text=True,
            capture_output=True,
            cwd=evaluation,
        )
        (output / "patch-apply.txt").write_text(applied.stdout + applied.stderr)
        acceptance = (
            evaluate(evaluation, task_dir) if applied.returncode == 0 else {"accepted": False}
        )
        return summarize(
            output, process, elapsed, acceptance, applied.returncode == 0, changes, protocol
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "run", "worker"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--harness", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--task-dir", type=Path, default=TASK, help="Frozen task/protocol directory (default: v1)"
    )
    args = parser.parse_args()
    source, harness, output = (p.resolve() for p in (args.source, args.harness, args.output))
    task_dir = args.task_dir.resolve()
    if args.mode == "prepare":
        prepare(source, harness, output, task_dir)
    elif args.mode == "worker":
        worker(harness, source, output, task_dir)
    else:
        run_trial(source, harness, output, task_dir)


if __name__ == "__main__":
    main()
