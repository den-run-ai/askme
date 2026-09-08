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


def patch(workspace):
    # Include untracked files, test edits, removals, mode changes, and binary data.
    git(workspace, "add", "--all")
    return git(workspace, "diff", "--cached", "--binary", "HEAD")


def allowed_changes(paths):
    return all(p == "src/requests/exceptions.py" or p.startswith("tests/") for p in paths)


def evaluate(workspace):
    """Fresh interpreter and external checks; no evaluator file enters the checkout."""
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(TASK / "acceptance.py"), str(workspace)],
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


def qualify(source, output, protocol):
    records = {}
    for arm in ("baseline", "noop", "gold"):
        with tempfile.TemporaryDirectory(prefix="askme-control-") as directory:
            workspace = Path(directory) / "repo"
            export_source(source, protocol["target_revision"], workspace)
            if arm == "gold":
                command(["git", "apply", str(TASK / "gold.patch")], workspace)
            elif arm == "noop":
                file = workspace / "src/requests/exceptions.py"
                file.write_text(file.read_text() + "\n# Harmless non-empty control.\n")
            control_patch = patch(workspace)
            records[arm] = {**evaluate(workspace), "patch_nonempty": bool(control_patch)}
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


def prepare(source, harness, output):
    protocol = json.loads((TASK / "protocol.json").read_text())
    output.mkdir(parents=True, exist_ok=False)
    revision = git(harness, "rev-parse", "HEAD").strip()
    if revision != protocol["harness_revision"]:
        raise ValueError("Harness revision differs from the predeclared revision")
    for name, expected in protocol["runtime_sha256"].items():
        if digest(harness / name) != expected:
            raise ValueError(f"Runtime digest mismatch: {name}")
    for name in ("protocol.json", "prompt.md", "gold.patch", "acceptance.py"):
        shutil.copyfile(TASK / name, output / name)
    shutil.copyfile(__file__, output / "external_repo_trial.py")
    registration = {
        "protocol": protocol,
        "harness_revision": revision,
        "registered_at_unix": time.time(),
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "source_sha256": {p.name: digest(p) for p in TASK.iterdir() if p.is_file()},
        "runner_sha256": digest(__file__),
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("requests", "urllib3", "certifi", "charset-normalizer", "idna")
        },
    }
    save(output / "registration.json", registration)
    qualify(source, output, protocol)
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


def worker(harness, workspace, output):
    sys.path.insert(0, str(harness))
    import askme

    protocol = json.loads((output / "protocol.json").read_text())
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


def summarize(output, process, elapsed, acceptance, applying, changes):
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
        "configured_timeout_s": 720,
        "observed_prompt_tokens": sum(e.get("prompt", 0) for e in tokens),
        "observed_completion_tokens": sum(e.get("completion", 0) for e in tokens),
        "usage_records": len(tokens),
        "usage_complete": False,
        "usage_note": "Observed totals are lower bounds; failed/interrupted HTTP attempts "
        "may not return usage. Raw request/response records are retained.",
        "api_cost_usd": 0,
        "runner_cost": "standard public macOS runner; no API charge",
        "limitations": "One historical, potentially training-contaminated bug; stdlib json only; "
        "7 GB CI host is not the 16 GB Gemma reference. No reliability inference.",
    }
    save(output / "summary.json", summary)
    print(json.dumps(summary, indent=2))
    return summary


def run_trial(source, harness, output):
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
        if digest(TASK / name) != expected:
            raise ValueError(f"Registered evaluator/task changed: {name}")
    if digest(__file__) != registration["runner_sha256"]:
        raise ValueError("Registered trial runner changed")
    if digest(output / "prompt.md") != registration["source_sha256"]["prompt.md"]:
        raise ValueError("Registered prompt changed")
    qualification = json.loads((output / "qualification.json").read_text())
    if not controls_valid(qualification):
        raise ValueError("Controls are not qualified")
    # Exclusive marker prevents accidentally overwriting or retrying an outcome.
    with (output / "trial-started.json").open("x") as marker:
        json.dump({"trial": 1, "started_at_unix": time.time()}, marker)
    sys.path.insert(0, str(harness))
    from actions import CapturedProcess

    with tempfile.TemporaryDirectory(prefix="askme-external-") as directory:
        workspace = Path(directory) / "repo"
        export_source(source, protocol["target_revision"], workspace)
        env = {
            key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG") if key in os.environ
        }
        env.update(
            LLM_BACKEND="local",
            LLM_MODEL=protocol["model_alias"],
            LLM_API_URL="http://127.0.0.1:8080/v1/chat/completions",
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
        candidate_patch = patch(workspace)
        (output / "candidate.patch").write_text(candidate_patch)
        changes = git(workspace, "diff", "--cached", "--name-only", "HEAD").splitlines()
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
        acceptance = evaluate(evaluation) if applied.returncode == 0 else {"accepted": False}
        return summarize(output, process, elapsed, acceptance, applied.returncode == 0, changes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "run", "worker"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--harness", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source, harness, output = (p.resolve() for p in (args.source, args.harness, args.output))
    if args.mode == "prepare":
        prepare(source, harness, output)
    elif args.mode == "worker":
        worker(harness, source, output)
    else:
        run_trial(source, harness, output)


if __name__ == "__main__":
    main()
