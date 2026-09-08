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
from pathlib import Path

TASK = Path(__file__).parent / "external_repo/requests_pickle_v1"
DEFAULT_API_URL = "http://127.0.0.1:8080/v1/chat/completions"


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
    for name, expected in protocol["runtime_sha256"].items():
        if digest(harness / name) != expected:
            raise ValueError(f"Runtime digest mismatch: {name}")
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
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("requests", "urllib3", "certifi", "charset-normalizer", "idna")
        },
    }
    save(output / "registration.json", registration)
    qualify(source, output, protocol, task_dir)
    return protocol


def record_post(path, seed):
    """Record complete requests/replies at the supported HTTP injection seam."""
    import requests

    def emit(value):
        with path.open("a") as log:
            log.write(json.dumps(value) + "\n")

    def post(api, *, json, headers, timeout):
        body = dict(json, seed=seed)
        started = time.monotonic()
        emit({"event": "request", "api": api, "body": body, "timeout": timeout})
        try:
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
        return response

    return post


def worker(harness, workspace, output, task_dir=TASK):
    sys.path.insert(0, str(harness))
    import askme

    protocol = json.loads((output / "protocol.json").read_text())
    if protocol != json.loads((task_dir / "protocol.json").read_text()):
        raise ValueError("Worker task protocol differs from registration")
    settings = replace(
        askme.LLMSettings.from_env(),
        capability_profile=askme.CapabilityProfile(**protocol["capability_profile"]),
    )
    client = askme.LLMClient(settings, post=record_post(output / "http.jsonl", protocol["seed"]))
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
        "runner_cost": protocol.get("runner_cost", "standard public macOS runner; no API charge"),
        "limitations": protocol.get(
            "limitations",
            "One historical, potentially training-contaminated bug; stdlib json only; "
            "7 GB CI host is not the 16 GB Gemma reference. No reliability inference.",
        ),
    }
    if "runner" in protocol:
        summary["runner"] = protocol["runner"]
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
    for name, expected in protocol["runtime_sha256"].items():
        if digest(harness / name) != expected:
            raise ValueError(f"Registered runtime changed: {name}")
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
            LLM_BACKEND="local",
            LLM_MODEL=protocol["model_alias"],
            LLM_API_URL=protocol.get("api_url", DEFAULT_API_URL),
            AGENT_RUN_LOG=str(output / "agent.jsonl"),
            PYTHONPATH=str(workspace / "src"),
            PYTHONDONTWRITEBYTECODE="1",
        )
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
