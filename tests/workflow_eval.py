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

SCHEMA_VERSION = 2
REASONING_POLICIES = ("gated", "off")
FINAL_VALIDATE_VALUES = ("0", "auto", "always")
CAPABILITY_PROFILES = ("generic-feature-scale-v1", "legacy-e4b-m1-16k-v1")
AGENT_LIMIT_KEYS = (
    "max_replans",
    "max_tasks",
    "max_steps",
    "goal_context_chars",
    "agent_timeout_seconds",
)
AgentCallback = Callable[[str, Path], Any]
_ADAPTER_METADATA = "_workflow_adapter"
_ADAPTER_ERROR = "_workflow_adapter_error"
_ADAPTER_INFRASTRUCTURE_ERROR = "_workflow_adapter_infrastructure_error"
MAX_CHILD_STREAM_CHARS = 20_000
MAX_RUN_LOG_EVENTS = 2_000
MAX_RUN_LOG_ERROR_CHARS = 1_000


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
    missing_limits = [
        key
        for key in (*AGENT_LIMIT_KEYS, "final_validate", "capability_profile")
        if key not in limits
    ]
    if missing_limits:
        raise ManifestError("agent_limits is missing: " + ", ".join(missing_limits))
    for key in AGENT_LIMIT_KEYS:
        value = limits[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ManifestError(f"agent_limits.{key} must be a positive integer")
    if limits["final_validate"] not in FINAL_VALIDATE_VALUES:
        allowed = ", ".join(FINAL_VALIDATE_VALUES)
        raise ManifestError(f"agent_limits.final_validate must be one of: {allowed}")
    if limits["capability_profile"] not in CAPABILITY_PROFILES:
        allowed = ", ".join(CAPABILITY_PROFILES)
        raise ManifestError(f"agent_limits.capability_profile must be one of: {allowed}")
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
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(token, str) or not token for token in command)
        ):
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


def _expand_command(command: Sequence[str], workspace: Path, evaluator: Path) -> list[str]:
    values = {
        "python": sys.executable,
        "workspace": str(workspace),
        "evaluator": str(evaluator),
    }
    try:
        return [token.format_map(values) for token in command]
    except KeyError as exc:
        raise ManifestError(f"unknown command placeholder: {exc.args[0]}") from exc


def _stream_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _run_command(command: Sequence[str], workspace: Path, timeout_seconds: float) -> dict[str, Any]:
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
            "stdout": _stream_text(exc.stdout),
            "stderr": _stream_text(exc.stderr),
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


def _bounded_child_stream(value: Any) -> tuple[str, bool, int]:
    text = _stream_text(value)
    length = len(text)
    return text[:MAX_CHILD_STREAM_CHARS], length > MAX_CHILD_STREAM_CHARS, length


def _read_agent_run_log(path: Path) -> tuple[dict[str, Any], Optional[str]]:
    evidence: dict[str, Any] = {
        "status": "parsed",
        "events": [],
        "event_count": 0,
        "events_truncated": False,
        "errors": [],
    }
    if not path.is_file():
        error = "AskMe child did not write its isolated JSONL run log"
        evidence["status"] = "missing"
        evidence["errors"].append(error)
        return evidence, error

    try:
        with path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError("event must be a JSON object")
                except (json.JSONDecodeError, ValueError) as exc:
                    evidence["errors"].append(
                        {
                            "line": line_number,
                            "error": str(exc),
                            "text": line[:MAX_RUN_LOG_ERROR_CHARS],
                            "text_truncated": len(line) > MAX_RUN_LOG_ERROR_CHARS,
                        }
                    )
                    continue
                evidence["event_count"] += 1
                if len(evidence["events"]) < MAX_RUN_LOG_EVENTS:
                    evidence["events"].append(event)
                else:
                    evidence["events_truncated"] = True
    except (OSError, UnicodeError) as exc:
        error = f"could not read AskMe JSONL run log: {exc}"
        evidence["status"] = "unreadable"
        evidence["errors"].append(error)
        return evidence, error

    if evidence["errors"]:
        evidence["status"] = "malformed"
        return evidence, "AskMe child JSONL run log contains malformed events"
    if evidence["events_truncated"]:
        evidence["status"] = "truncated"
        error = "AskMe child JSONL run log exceeds the retained event limit"
        evidence["errors"].append(error)
        return evidence, error
    return evidence, None


def _reasoning_policy_error(run_log: Mapping[str, Any], reasoning_policy: str) -> Optional[str]:
    decision_since_response = False
    for index, event in enumerate(run_log.get("events", []), start=1):
        if event.get("event") == "tokens":
            if not decision_since_response:
                return f"tokens event {index} has no preceding reasoning decision"
            decision_since_response = False
            continue
        if event.get("event") != "reasoning_decision":
            continue
        decision_since_response = True
        if (
            not isinstance(event.get("requested_trigger"), str)
            or not event["requested_trigger"]
            or "requested_level" not in event
            or "effective_level" not in event
            or not isinstance(event.get("attempt"), int)
            or isinstance(event.get("attempt"), bool)
            or event["attempt"] < 0
        ):
            return f"reasoning decision {index} is missing level/trigger/attempt provenance"
        if event.get("requested_policy") != reasoning_policy:
            return (
                f"reasoning decision {index} requested policy "
                f"{event.get('requested_policy')!r}; expected {reasoning_policy!r}"
            )
        requested = event["requested_level"]
        if requested not in {None, "low", "medium", "high", "adaptive"}:
            return f"reasoning decision {index} has an invalid requested level"
        if reasoning_policy == "off" and event.get("effective_level") is not None:
            return (
                f"reasoning decision {index} enabled "
                f"{event.get('effective_level')!r} under off policy"
            )
        if reasoning_policy == "gated":
            effective = event.get("effective_level")
            if requested is None and effective is not None:
                return f"reasoning decision {index} enabled an unrequested level"
            if requested in {"low", "medium", "high"} and effective != requested:
                return f"reasoning decision {index} changed the requested gated level"
            if requested == "adaptive" and effective not in {None, "medium", "high"}:
                return f"reasoning decision {index} has an invalid adaptive level"
    return None


def _configuration_provenance_error(
    run_log: Mapping[str, Any],
    agent_limits: Mapping[str, Any],
    reasoning_policy: str,
    expected_prompt: str,
    expected_workspace: Path,
    child_result: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """Verify the profile treatment and hash recorded by a cold AskMe run."""
    expected_profile = str(agent_limits["capability_profile"])
    run_starts = [event for event in run_log.get("events", []) if event.get("event") == "run_start"]
    if len(run_starts) != 1:
        return f"run log contains {len(run_starts)} run_start events; expected exactly one"
    run_start = run_starts[0]
    if run_start.get("prompt") != expected_prompt:
        return "run_start prompt does not match the scheduled manifest prompt"
    recorded_workspace = run_start.get("working_dir")
    if (
        not isinstance(recorded_workspace, str)
        or Path(recorded_workspace).resolve() != expected_workspace
    ):
        return "run_start working_dir does not match the copied workflow workspace"
    start_profile = run_start.get("capability_profile")
    start_profile_name = start_profile.get("name") if isinstance(start_profile, Mapping) else None
    if start_profile_name != expected_profile:
        return f"run_start capability profile {start_profile_name!r}; expected {expected_profile!r}"
    start_hash = run_start.get("config_hash")
    if (
        not isinstance(start_hash, str)
        or len(start_hash) != 16
        or any(character not in "0123456789abcdef" for character in start_hash.lower())
    ):
        return "run_start is missing config_hash"
    start_config = {
        key: value
        for key, value in run_start.items()
        if key not in {"event", "prompt", "ts", "working_dir"}
    }
    hash_payload = dict(start_config)
    hash_payload.pop("config_hash")
    canonical = json.dumps(hash_payload, sort_keys=True, separators=(",", ":"), default=str)
    recomputed_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    if start_hash != recomputed_hash:
        return "run_start config_hash does not match its resolved configuration"

    expected_inner_limits = {
        key: agent_limits[key]
        for key in ("max_replans", "max_tasks", "max_steps", "goal_context_chars")
    }
    if run_start.get("limits") != expected_inner_limits:
        return "run_start limits do not match the frozen manifest limits"
    if run_start.get("final_validate") != agent_limits["final_validate"]:
        return "run_start final_validate does not match the frozen manifest policy"
    if run_start.get("reasoning_policy") != reasoning_policy:
        return "run_start reasoning_policy does not match the scheduled arm"

    if child_result is None:
        return None

    run_ends = [event for event in run_log.get("events", []) if event.get("event") == "run_end"]
    if len(run_ends) != 1:
        return f"run log contains {len(run_ends)} run_end events; expected exactly one"
    if run_ends[0].get("status") != child_result.get("status"):
        return "run_end status does not match structured result"

    result_config = child_result.get("config")
    if not isinstance(result_config, Mapping):
        return "structured result is missing config metadata"
    result_profile = result_config.get("capability_profile")
    result_profile_name = (
        result_profile.get("name") if isinstance(result_profile, Mapping) else None
    )
    if result_profile_name != expected_profile:
        return (
            f"structured result capability profile {result_profile_name!r}; "
            f"expected {expected_profile!r}"
        )
    if dict(result_config) != start_config:
        return "structured result config metadata does not match run_start"
    result_workspace = child_result.get("workspace")
    if (
        not isinstance(result_workspace, Mapping)
        or result_workspace.get("created") is not False
        or not isinstance(result_workspace.get("path"), str)
        or Path(result_workspace["path"]).resolve() != expected_workspace
    ):
        return "structured result workspace does not match the copied workflow workspace"
    return None


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
        raise ValueError(f"reasoning_policy must be one of: {', '.join(REASONING_POLICIES)}")
    manifest = load_manifest(manifest_path)
    managed_workspace = workspace is None
    if managed_workspace:
        workspace_path = Path(tempfile.mkdtemp(prefix=f"askme-{manifest['id']}-"))
        # copytree requires the destination not to exist.
        workspace_path.rmdir()
    else:
        assert workspace is not None
        workspace_path = Path(workspace).resolve()
    if workspace_path.exists():
        raise FileExistsError(f"workspace already exists: {workspace_path}")

    shutil.copytree(manifest["_fixture_path"], workspace_path)
    initial_hashes = _protected_hashes(workspace_path, manifest["protected_files"])
    infrastructure_errors: list[str] = []
    agent_error: Optional[str] = None
    agent_run: Optional[dict[str, Any]] = None
    agent_result: Any = None
    try:
        try:
            agent_result = agent_callback(manifest["prompt"], workspace_path)
        except Exception as exc:  # The checks still run to preserve artifact evidence.
            agent_error = f"{type(exc).__name__}: {exc}"
        if isinstance(agent_result, Mapping):
            adapter_metadata = agent_result.get(_ADAPTER_METADATA)
            if isinstance(adapter_metadata, Mapping):
                agent_run = dict(adapter_metadata)
            adapter_error = agent_result.get(_ADAPTER_ERROR)
            if isinstance(adapter_error, str) and adapter_error:
                agent_error = adapter_error
            adapter_infrastructure_error = agent_result.get(_ADAPTER_INFRASTRUCTURE_ERROR)
            if isinstance(adapter_infrastructure_error, str) and adapter_infrastructure_error:
                infrastructure_errors.append(adapter_infrastructure_error)

        final_hashes = _protected_hashes(workspace_path, manifest["protected_files"])
        changed = sorted(
            relative
            for relative in manifest["protected_files"]
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
                infrastructure_errors.append(f"{name} could not launch: {command_result['stderr']}")

        status = "error" if agent_error is not None else _agent_status(agent_result)
        agent_complete = status == "complete"
        infrastructure_valid = not infrastructure_errors
        integrity_passed = not changed
        run_valid = infrastructure_valid and integrity_passed
        regression_passed = bool(public_result["passed"])
        feedback_passed = bool(feedback_result["passed"])
        acceptance_passed = bool(acceptance_result["passed"])
        artifact_accepted = regression_passed and feedback_passed and acceptance_passed
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
            "agent_run": agent_run,
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


def _noop_agent(_prompt: str, _workspace: Path) -> Mapping[str, Any]:
    return {"status": "exhausted"}


def _askme_agent(
    prompt: str,
    workspace: Path,
    *,
    reasoning_policy: str,
    agent_limits: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Run AskMe in a cold child process and return its structured result."""
    repository_root = Path(__file__).resolve().parent.parent
    askme_script = repository_root / "askme.py"
    workspace = workspace.resolve()

    def process_metadata(
        status: str,
        *,
        command: Sequence[str] = (),
        exit_code: Optional[int] = None,
        stdout: Any = "",
        stderr: Any = "",
        result: Any = None,
    ) -> dict[str, Any]:
        bounded_stdout, stdout_truncated, stdout_chars = _bounded_child_stream(stdout)
        bounded_stderr, stderr_truncated, stderr_chars = _bounded_child_stream(stderr)
        return {
            "status": status,
            "command": list(command),
            "exit_code": exit_code,
            "stdout": bounded_stdout,
            "stdout_chars": stdout_chars,
            "stdout_truncated": stdout_truncated,
            "stderr": bounded_stderr,
            "stderr_chars": stderr_chars,
            "stderr_truncated": stderr_truncated,
            "result": result,
            "run_log": {
                "status": "not_started",
                "events": [],
                "event_count": 0,
                "events_truncated": False,
                "errors": [],
            },
        }

    def failure(
        error: str,
        metadata: Mapping[str, Any],
        *,
        infrastructure_error: Optional[str] = None,
    ) -> Mapping[str, Any]:
        result = {
            "status": "error",
            _ADAPTER_ERROR: error,
            _ADAPTER_METADATA: dict(metadata),
        }
        if infrastructure_error is not None:
            result[_ADAPTER_INFRASTRUCTURE_ERROR] = infrastructure_error
        return result

    if not workspace.is_dir():
        error = f"AskMe workspace does not exist: {workspace}"
        return failure(
            error,
            process_metadata("workspace_error"),
            infrastructure_error=error,
        )

    with tempfile.TemporaryDirectory(prefix="askme-workflow-adapter-") as directory:
        exchange = Path(directory)
        prompt_file = exchange / "prompt.txt"
        result_file = exchange / "result.json"
        run_log_file = exchange / "run.jsonl"
        try:
            prompt_file.write_text(prompt, encoding="utf-8")
        except OSError as exc:
            error = f"could not write AskMe prompt file: {exc}"
            return failure(
                error,
                process_metadata("exchange_error", stderr=str(exc)),
                infrastructure_error=error,
            )

        command = [
            sys.executable,
            str(askme_script),
            "--prompt-file",
            str(prompt_file),
            "--working-dir",
            str(workspace),
            "--result-json",
            str(result_file),
            "--reasoning-policy",
            reasoning_policy,
            "--capability-profile",
            str(agent_limits["capability_profile"]),
            "--max-replans",
            str(agent_limits["max_replans"]),
            "--max-tasks",
            str(agent_limits["max_tasks"]),
            "--max-steps",
            str(agent_limits["max_steps"]),
            "--goal-context-chars",
            str(agent_limits["goal_context_chars"]),
        ]
        environment = os.environ.copy()
        environment["AGENT_REASONING_POLICY"] = reasoning_policy
        environment["LLM_CAPABILITY_PROFILE"] = str(agent_limits["capability_profile"])
        environment["AGENT_GOAL_CONTEXT_CHARS"] = str(agent_limits["goal_context_chars"])
        environment["AGENT_FINAL_VALIDATE"] = str(agent_limits["final_validate"])
        environment["AGENT_RUN_LOG"] = str(run_log_file)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            completed = subprocess.run(
                command,
                cwd=repository_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=agent_limits["agent_timeout_seconds"],
            )
        except subprocess.TimeoutExpired as exc:
            timeout_seconds = agent_limits["agent_timeout_seconds"]
            error = f"AskMe child timed out after {timeout_seconds} seconds"
            metadata = process_metadata(
                "timeout",
                command=command,
                stdout=exc.stdout,
                stderr=exc.stderr,
            )
            metadata["timeout_seconds"] = timeout_seconds
            metadata["run_log"], run_log_error = _read_agent_run_log(run_log_file)
            policy_error = _reasoning_policy_error(metadata["run_log"], reasoning_policy)
            provenance_error = _configuration_provenance_error(
                metadata["run_log"], agent_limits, reasoning_policy, prompt, workspace
            )
            return failure(
                error,
                metadata,
                infrastructure_error=run_log_error or policy_error or provenance_error,
            )
        except OSError as exc:
            error = f"could not launch AskMe child process: {exc}"
            metadata = process_metadata("launch_error", command=command, stderr=str(exc))
            metadata["run_log"], _ = _read_agent_run_log(run_log_file)
            return failure(error, metadata, infrastructure_error=error)

        metadata = process_metadata(
            "completed",
            command=command,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        metadata["run_log"], run_log_error = _read_agent_run_log(run_log_file)
        policy_error = _reasoning_policy_error(metadata["run_log"], reasoning_policy)
        provenance_error = _configuration_provenance_error(
            metadata["run_log"], agent_limits, reasoning_policy, prompt, workspace
        )
        base_evidence_error = run_log_error or policy_error or provenance_error
        if not result_file.is_file():
            metadata["status"] = "missing_result"
            error = (
                "AskMe child did not write its structured result "
                f"(exit code {completed.returncode})"
            )
            return failure(error, metadata, infrastructure_error=base_evidence_error)

        try:
            result_text = result_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            metadata["status"] = "unreadable_result"
            metadata["result_error"] = str(exc)
            return failure(
                f"could not read AskMe structured result: {exc}",
                metadata,
                infrastructure_error=base_evidence_error,
            )
        try:
            child_result = json.loads(result_text)
        except json.JSONDecodeError as exc:
            metadata["status"] = "malformed_result"
            metadata["result_error"] = str(exc)
            bounded_result, result_truncated, result_chars = _bounded_child_stream(result_text)
            metadata["result_text"] = bounded_result
            metadata["result_text_chars"] = result_chars
            metadata["result_text_truncated"] = result_truncated
            return failure(
                f"AskMe structured result is malformed: {exc}",
                metadata,
                infrastructure_error=base_evidence_error,
            )

        if (
            not isinstance(child_result, dict)
            or not isinstance(child_result.get("status"), str)
            or not child_result["status"]
        ):
            metadata["status"] = "malformed_result"
            metadata["result"] = child_result
            error = "AskMe structured result must contain a non-empty string status"
            return failure(error, metadata, infrastructure_error=base_evidence_error)

        metadata["result"] = child_result
        result_provenance_error = _configuration_provenance_error(
            metadata["run_log"],
            agent_limits,
            reasoning_policy,
            prompt,
            workspace,
            child_result,
        )
        full_evidence_error = run_log_error or policy_error or result_provenance_error
        expected_exit = 0 if child_result["status"] == "complete" else 1
        if completed.returncode != expected_exit:
            metadata["status"] = "exit_result_mismatch"
            error = (
                f"AskMe exit code {completed.returncode} conflicts with "
                f"structured status {child_result['status']!r}"
            )
            return failure(error, metadata, infrastructure_error=full_evidence_error)

        if run_log_error is not None:
            metadata["status"] = metadata["run_log"]["status"] + "_run_log"
            return failure(
                run_log_error,
                metadata,
                infrastructure_error=run_log_error,
            )

        if policy_error is not None:
            metadata["status"] = "reasoning_policy_violation"
            return failure(
                policy_error,
                metadata,
                infrastructure_error=policy_error,
            )

        if result_provenance_error is not None:
            metadata["status"] = "configuration_provenance_violation"
            return failure(
                result_provenance_error,
                metadata,
                infrastructure_error=result_provenance_error,
            )

        return {
            "status": child_result["status"],
            _ADAPTER_METADATA: metadata,
        }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--agent", choices=("noop", "askme"), default="noop")
    parser.add_argument(
        "--reasoning-policy",
        choices=REASONING_POLICIES,
        default="gated",
        help="Explicit-reasoning policy recorded for this run (default: %(default)s)",
    )
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--keep-workspace", action="store_true")
    parser.add_argument("--result-json", type=Path)
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    if args.agent == "askme":

        def askme_callback(prompt: str, workspace: Path) -> Mapping[str, Any]:
            return _askme_agent(
                prompt,
                workspace,
                reasoning_policy=args.reasoning_policy,
                agent_limits=manifest["agent_limits"],
            )

        callback: AgentCallback = askme_callback
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
