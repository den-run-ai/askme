#!/usr/bin/env python3
"""Manifest-driven evaluator for native AskMe workflow tasks.

The runner copies a task fixture into a fresh workspace, invokes an injected
agent callback, and then runs public regressions and a held-out evaluator. The
callback is injected so qualification can run offline without a model call.
The evaluator is kept outside the copied workspace to prevent accidental edits;
this is separation for evaluation, not an adversarial filesystem sandbox.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence


SCHEMA_VERSION = 1
REASONING_POLICIES = ("gated", "off")
FINAL_VALIDATE_VALUES = ("0", "auto", "always")
AGENT_LIMIT_KEYS = (
    "max_replans",
    "max_tasks",
    "max_steps",
    "goal_context_chars",
)
AgentCallback = Callable[[str, Path], Any]


class ManifestError(ValueError):
    """Raised when a workflow manifest is unsafe or incomplete."""


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _require_string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{key!r} must be a non-empty string")
    return value


def load_manifest(manifest_path: Path | str) -> dict[str, Any]:
    """Load and validate the static parts of a workflow manifest."""
    path = Path(manifest_path).resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot load manifest {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ManifestError("manifest root must be an object")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError(f"schema_version must be {SCHEMA_VERSION}")
    _require_string(data, "id")
    prompt = _require_string(data, "prompt")
    limits = data.get("agent_limits")
    if not isinstance(limits, dict):
        raise ManifestError("agent_limits must be an object")
    missing_limits = [key for key in (*AGENT_LIMIT_KEYS, "final_validate") if key not in limits]
    if missing_limits:
        raise ManifestError("agent_limits is missing: " + ", ".join(missing_limits))
    for key in AGENT_LIMIT_KEYS:
        value = limits[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ManifestError(f"agent_limits.{key} must be a positive integer")
    if limits["final_validate"] not in FINAL_VALIDATE_VALUES:
        allowed = ", ".join(FINAL_VALIDATE_VALUES)
        raise ManifestError(f"agent_limits.final_validate must be one of: {allowed}")
    goal_cap = limits["goal_context_chars"]
    if len(prompt) > goal_cap:
        raise ManifestError(
            f"prompt length {len(prompt)} exceeds frozen goal_context_chars {goal_cap}"
        )
    fixture_name = _require_string(data, "fixture")

    fixture = (path.parent / fixture_name).resolve()
    if not _is_within(fixture, path.parent.resolve()) or not fixture.is_dir():
        raise ManifestError("fixture must be a directory inside the task directory")

    protected = data.get("protected_files")
    if not isinstance(protected, list) or not protected:
        raise ManifestError("protected_files must be a non-empty list")
    for item in protected:
        if not isinstance(item, str) or not item or Path(item).is_absolute():
            raise ManifestError("protected file paths must be non-empty and relative")
        candidate = (fixture / item).resolve()
        if not _is_within(candidate, fixture) or not candidate.is_file():
            raise ManifestError(f"protected file is missing or unsafe: {item}")

    for key in ("public_regression", "public_feedback", "held_out_evaluator"):
        spec = data.get(key)
        if not isinstance(spec, dict):
            raise ManifestError(f"{key} must be an object")
        command = spec.get("command")
        if (not isinstance(command, list) or not command or
                any(not isinstance(token, str) or not token for token in command)):
            raise ManifestError(f"{key}.command must be a non-empty string list")
        timeout = spec.get("timeout_seconds", 30)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ManifestError(f"{key}.timeout_seconds must be positive")

    evaluator_spec = data["held_out_evaluator"]
    evaluator_name = _require_string(evaluator_spec, "path")
    evaluator = (path.parent / evaluator_name).resolve()
    if not evaluator.is_file():
        raise ManifestError("held-out evaluator does not exist")
    if _is_within(evaluator, fixture):
        raise ManifestError("held-out evaluator must be outside the copied fixture")

    data["_manifest_path"] = path
    data["_fixture_path"] = fixture
    data["_evaluator_path"] = evaluator
    return data


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _protected_hashes(workspace: Path, paths: Sequence[str]) -> dict[str, Optional[str]]:
    hashes: dict[str, Optional[str]] = {}
    root = workspace.resolve()
    for relative in paths:
        candidate = workspace / relative
        if candidate.is_symlink():
            hashes[relative] = None
            continue
        resolved = candidate.resolve()
        if not _is_within(resolved, root) or not resolved.is_file():
            hashes[relative] = None
            continue
        hashes[relative] = _sha256(resolved)
    return hashes


def _expand_command(command: Sequence[str], workspace: Path,
                    evaluator: Path) -> list[str]:
    values = {
        "python": sys.executable,
        "workspace": str(workspace),
        "evaluator": str(evaluator),
    }
    try:
        return [token.format_map(values) for token in command]
    except KeyError as exc:
        raise ManifestError(f"unknown command placeholder: {exc.args[0]}") from exc


def _run_command(command: Sequence[str], workspace: Path,
                 timeout_seconds: float) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["ASKME_EVAL_WORKSPACE"] = str(workspace)
    try:
        completed = subprocess.run(
            list(command),
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "status": "completed",
            "command": list(command),
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "passed": completed.returncode == 0,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "command": list(command),
            "exit_code": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "passed": False,
        }
    except OSError as exc:
        return {
            "status": "launch_error",
            "command": list(command),
            "exit_code": None,
            "stdout": "",
            "stderr": str(exc),
            "passed": False,
        }


def _agent_status(agent_result: Any) -> str:
    if isinstance(agent_result, Mapping):
        status = agent_result.get("status")
        return status if isinstance(status, str) and status else "unknown"
    if agent_result is True:
        return "complete"
    if agent_result is False:
        return "exhausted"
    if isinstance(agent_result, str) and agent_result:
        return agent_result
    return "unknown"


def evaluate_workflow(
    manifest_path: Path | str,
    agent_callback: AgentCallback,
    *,
    workspace: Path | str | None = None,
    keep_workspace: bool = False,
    reasoning_policy: str = "gated",
) -> dict[str, Any]:
    """Run one workflow and return artifact and control-loop outcomes separately.

    The held-out evaluator always runs after the callback, including when the
    agent reports an incomplete status or raises an exception.
    """
    if reasoning_policy not in REASONING_POLICIES:
        raise ValueError(
            f"reasoning_policy must be one of: {', '.join(REASONING_POLICIES)}"
        )
    manifest = load_manifest(manifest_path)
    managed_workspace = workspace is None
    if managed_workspace:
        workspace_path = Path(tempfile.mkdtemp(prefix=f"askme-{manifest['id']}-"))
        # copytree requires the destination not to exist.
        workspace_path.rmdir()
    else:
        workspace_path = Path(workspace).resolve()
    if workspace_path.exists():
        raise FileExistsError(f"workspace already exists: {workspace_path}")

    shutil.copytree(manifest["_fixture_path"], workspace_path)
    initial_hashes = _protected_hashes(workspace_path, manifest["protected_files"])
    infrastructure_errors: list[str] = []
    agent_error: Optional[str] = None
    agent_result: Any = None
    try:
        try:
            agent_result = agent_callback(manifest["prompt"], workspace_path)
        except Exception as exc:  # The checks still run to preserve artifact evidence.
            agent_error = f"{type(exc).__name__}: {exc}"

        final_hashes = _protected_hashes(workspace_path, manifest["protected_files"])
        changed = sorted(
            relative for relative in manifest["protected_files"]
            if initial_hashes.get(relative) != final_hashes.get(relative)
        )
        if changed:
            integrity_error = "protected files changed: " + ", ".join(changed)
        else:
            integrity_error = None

        evaluator = manifest["_evaluator_path"]
        public_spec = manifest["public_regression"]
        feedback_spec = manifest["public_feedback"]
        acceptance_spec = manifest["held_out_evaluator"]
        public_result = _run_command(
            _expand_command(public_spec["command"], workspace_path, evaluator),
            workspace_path,
            float(public_spec.get("timeout_seconds", 30)),
        )
        feedback_result = _run_command(
            _expand_command(feedback_spec["command"], workspace_path, evaluator),
            workspace_path,
            float(feedback_spec.get("timeout_seconds", 30)),
        )
        acceptance_result = _run_command(
            _expand_command(acceptance_spec["command"], workspace_path, evaluator),
            workspace_path,
            float(acceptance_spec.get("timeout_seconds", 30)),
        )
        for name, command_result in (
            ("public regression", public_result),
            ("public feedback", feedback_result),
            ("held-out evaluator", acceptance_result),
        ):
            if command_result["status"] == "launch_error":
                infrastructure_errors.append(
                    f"{name} could not launch: {command_result['stderr']}"
                )

        status = "error" if agent_error is not None else _agent_status(agent_result)
        agent_complete = status == "complete"
        infrastructure_valid = not infrastructure_errors
        integrity_passed = not changed
        run_valid = infrastructure_valid and integrity_passed
        regression_passed = bool(public_result["passed"])
        feedback_passed = bool(feedback_result["passed"])
        acceptance_passed = bool(acceptance_result["passed"])
        artifact_accepted = (
            regression_passed and feedback_passed and acceptance_passed
        )
        if not run_valid:
            outcome = "invalid_run"
        elif agent_complete and artifact_accepted:
            outcome = "clean_success"
        elif agent_complete:
            outcome = "false_completion"
        elif artifact_accepted:
            outcome = "accepted_incomplete"
        else:
            outcome = "incomplete_failure"
        retained = not managed_workspace or keep_workspace
        return {
            "schema_version": SCHEMA_VERSION,
            "task_id": manifest["id"],
            "reasoning_policy": reasoning_policy,
            "agent_limits": dict(manifest["agent_limits"]),
            "infrastructure_valid": infrastructure_valid,
            "infrastructure_errors": infrastructure_errors,
            "agent_status": status,
            "agent_complete": agent_complete,
            "agent_error": agent_error,
            "integrity_passed": integrity_passed,
            "integrity_error": integrity_error,
            "run_valid": run_valid,
            "protected_files_intact": integrity_passed,
            "protected_files_changed": changed,
            "regression_passed": regression_passed,
            "feedback_passed": feedback_passed,
            "acceptance_passed": acceptance_passed,
            "artifact_accepted": artifact_accepted,
            "false_completion": outcome == "false_completion",
            "outcome": outcome,
            "checks": {
                "public_regression": public_result,
                "public_feedback": feedback_result,
                "held_out_acceptance": acceptance_result,
            },
            "workspace_path": str(workspace_path) if retained else None,
        }
    finally:
        if managed_workspace and not keep_workspace:
            shutil.rmtree(workspace_path, ignore_errors=True)


def _noop_agent(_prompt: str, _workspace: Path) -> dict[str, str]:
    return {"status": "exhausted"}


def _askme_agent(
    prompt: str,
    workspace: Path,
    *,
    reasoning_policy: str,
    agent_limits: Mapping[str, Any],
) -> Mapping[str, Any]:
    repository_root = Path(__file__).resolve().parent.parent
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))
    import askme

    previous_final_validate = askme.FINAL_VALIDATE
    askme.FINAL_VALIDATE = agent_limits["final_validate"]
    try:
        return askme._run_loop(
            prompt,
            str(workspace),
            max_replans=agent_limits["max_replans"],
            max_tasks=agent_limits["max_tasks"],
            max_steps=agent_limits["max_steps"],
            reasoning_policy=reasoning_policy,
            goal_context_chars=agent_limits["goal_context_chars"],
        )
    finally:
        askme.FINAL_VALIDATE = previous_final_validate


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--agent", choices=("noop", "askme"), default="noop")
    parser.add_argument(
        "--reasoning-policy", choices=REASONING_POLICIES, default="gated",
        help="Explicit-reasoning policy recorded for this run (default: %(default)s)",
    )
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--keep-workspace", action="store_true")
    parser.add_argument("--result-json", type=Path)
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    if args.agent == "askme":
        def callback(prompt: str, workspace: Path) -> Mapping[str, Any]:
            return _askme_agent(
                prompt,
                workspace,
                reasoning_policy=args.reasoning_policy,
                agent_limits=manifest["agent_limits"],
            )
    else:
        callback = _noop_agent
    result = evaluate_workflow(
        args.manifest,
        callback,
        workspace=args.workspace,
        keep_workspace=args.keep_workspace,
        reasoning_policy=args.reasoning_policy,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.result_json:
        args.result_json.write_text(rendered + "\n", encoding="utf-8")
    if not result["run_valid"]:
        return 2
    return 0 if result["artifact_accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
