#!/usr/bin/env python3
"""Deterministically audit one retained AskMe FeatureBench canary run.

This module intentionally imports neither Docker nor FeatureBench.  It audits
only the files retained by the inference adapter and keeps infrastructure
validity separate from whether AskMe reported completion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

DEFAULT_PROTOCOL = Path(__file__).with_name("canary-protocol.json")
DEFAULT_ASKME_SOURCE = Path(__file__).resolve().parents[2] / "askme.py"
POLICY_LOG_NAME = "askme-policy.jsonl"
ENDPOINT_CATALOG_PREFLIGHT_NAME = "openrouter-endpoint-catalog-preflight.json"
AGENT_COMPLETE_STATUSES = {"complete", "complete_deterministic_after_exhausted"}
REQUIRED_CODE_FILES = {
    "tests/featurebench/askme_adapter.py",
    "tests/featurebench/canary_audit.py",
}


class CanaryAuditError(RuntimeError):
    """Raised when ``raise_on_invalid`` is requested for an invalid audit."""

    def __init__(self, audit: Mapping[str, Any]):
        self.audit = dict(audit)
        count = len(self.audit.get("violations", []))
        super().__init__(f"FeatureBench canary audit invalid ({count} violation(s))")


class _Audit:
    def __init__(self, run_dir: Path, secret: bytes):
        self.run_dir = run_dir
        self.secret = secret
        self.violations: list[dict[str, str]] = []
        self.artifacts: dict[str, str] = {}

    def fail(self, code: str, message: str) -> None:
        # Defense in depth: no diagnostic may echo the credential being sought.
        if self.secret:
            message = message.replace(self.secret.decode("utf-8", errors="ignore"), "<redacted>")
        self.violations.append({"code": code, "message": message})

    def artifact(self, label: str, path: Path) -> None:
        try:
            rendered = str(path.relative_to(self.run_dir))
        except ValueError:
            rendered = str(path)
        self.artifacts[label] = rendered

    def regular_file(self, path: Path, label: str) -> bool:
        self.artifact(label, path)
        if path.is_symlink():
            self.fail(f"{label}_symlink", f"{label} must not be a symlink")
            return False
        if not path.is_file():
            self.fail(f"{label}_missing", f"missing required {label}")
            return False
        return True

    def read_json(self, path: Path, label: str) -> Optional[Any]:
        if not self.regular_file(path, label):
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            self.fail(f"{label}_invalid", f"could not parse {label}: {error}")
            return None

    def read_jsonl(self, path: Path, label: str) -> Optional[list[Any]]:
        if not self.regular_file(path, label):
            return None
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as error:
            self.fail(f"{label}_invalid", f"could not read {label}: {error}")
            return None
        records: list[Any] = []
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                self.fail(
                    f"{label}_invalid_json",
                    f"{label} line {number} is not valid JSON: {error}",
                )
                continue
            if not isinstance(record, dict):
                self.fail(
                    f"{label}_record_type",
                    f"{label} line {number} must contain a JSON object",
                )
                continue
            records.append(record)
        return records


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _contains_bytes(path: Path, needle: bytes) -> bool:
    """Search without loading a retained file in memory or exposing the needle."""
    overlap = b""
    keep = max(len(needle) - 1, 0)
    with path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                return False
            candidate = overlap + chunk
            if needle in candidate:
                return True
            overlap = candidate[-keep:] if keep else b""


def _as_mapping(audit: _Audit, value: Any, label: str) -> Optional[Mapping[str, Any]]:
    if not isinstance(value, dict):
        audit.fail(f"{label}_type", f"{label} must contain a JSON object")
        return None
    return value


def _expect_equal(audit: _Audit, code: str, label: str, observed: Any, expected: Any) -> None:
    if observed != expected:
        audit.fail(code, f"{label} did not match the frozen value")


def _audit_endpoint_catalog_preflight(
    audit: _Audit,
    provenance: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> None:
    """Audit the retained catalog gate when future provenance requires it."""
    config_raw = provenance.get("endpoint_catalog_preflight")
    if config_raw is None:
        # Frozen v2 predates this gate and remains auditable at its pinned code.
        return
    config = _as_mapping(audit, config_raw, "endpoint_catalog_preflight_config")
    if config is None:
        return
    if config.get("required") is not True:
        audit.fail(
            "endpoint_catalog_preflight_required",
            "present endpoint-catalog provenance must declare the gate required",
        )
        return
    relative_path = config.get("relative_path")
    if relative_path != ENDPOINT_CATALOG_PREFLIGHT_NAME:
        audit.fail(
            "endpoint_catalog_preflight_path",
            "required endpoint-catalog preflight path is not the fixed run-root file",
        )
        return

    raw = audit.read_json(
        audit.run_dir / ENDPOINT_CATALOG_PREFLIGHT_NAME,
        "endpoint_catalog_preflight",
    )
    record = _as_mapping(audit, raw, "endpoint_catalog_preflight") if raw is not None else None
    if record is None:
        return

    _expect_equal(
        audit,
        "endpoint_catalog_preflight_valid",
        "endpoint-catalog preflight validity",
        record.get("valid"),
        True,
    )
    _expect_equal(
        audit,
        "endpoint_catalog_preflight_outcome_bearing",
        "endpoint-catalog outcome-bearing marker",
        record.get("outcome_bearing_model_call"),
        False,
    )
    _expect_equal(
        audit,
        "endpoint_catalog_preflight_model",
        "endpoint-catalog requested model",
        record.get("requested_model"),
        expected["model"],
    )
    _expect_equal(
        audit,
        "endpoint_catalog_preflight_provider",
        "endpoint-catalog expected provider",
        record.get("expected_provider"),
        expected["provider"],
    )
    _expect_equal(
        audit,
        "endpoint_catalog_preflight_served_models",
        "endpoint-catalog expected served models",
        record.get("expected_served_models"),
        expected["protocol_served_models"],
    )

    response_sha256 = record.get("response_sha256")
    if (
        not isinstance(response_sha256, str)
        or len(response_sha256) != 64
        or any(character not in "0123456789abcdef" for character in response_sha256)
    ):
        audit.fail(
            "endpoint_catalog_preflight_response_sha256",
            "endpoint-catalog response SHA-256 must be 64 lowercase hexadecimal characters",
        )

    matches = record.get("matches")
    if not isinstance(matches, list) or not all(isinstance(match, dict) for match in matches):
        audit.fail(
            "endpoint_catalog_preflight_matches",
            "endpoint-catalog matches must be a list of objects",
        )
        return
    expected_models = expected["protocol_served_models"]
    expected_provider = expected["provider"].casefold()
    valid_matches = [
        match
        for match in matches
        if isinstance(match.get("provider_name"), str)
        and match["provider_name"].casefold() == expected_provider
        and match.get("served_model") in expected_models
        and match.get("model_id") == expected["model"]
    ]
    if len(matches) != len(expected_models) or len(valid_matches) != len(matches):
        audit.fail(
            "endpoint_catalog_preflight_matches",
            "endpoint-catalog matches contain an unexpected model or provider",
        )
    for served_model in expected_models:
        count = sum(match.get("served_model") == served_model for match in valid_matches)
        if count != 1:
            audit.fail(
                "endpoint_catalog_preflight_matches",
                "endpoint-catalog must retain exactly one provider match per expected model",
            )


def _protocol_expectations(audit: _Audit, protocol: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    try:
        cell = protocol["agent_cell"]
        sources = protocol["sources"]
        dataset = sources["dataset"]
        provider = cell["provider"]
        limits = cell["limits"]
        provider_order = provider["order"]
        if "askme_cli_parameters" in limits:
            cli_limits = dict(limits["askme_cli_parameters"])
            manifest_limits = {
                "max_planning_attempts": limits["max_planning_attempts_total"],
                "max_tasks_per_plan": limits["max_tasks_per_plan"],
                "max_steps_per_task_attempt": limits["max_steps_per_task_attempt"],
                "max_task_local_replans": limits["max_task_local_replans_per_task"],
                "max_task_attempts": limits["max_task_attempts_per_task"],
            }
            timeouts = cell["timeouts"]
            manifest_timeouts = {
                "inner_seconds": timeouts["inner_askme_seconds"],
                "kill_grace_seconds": timeouts["inner_kill_grace_seconds"],
            }
            provenance_timeouts = {
                "outer_seconds": timeouts["outer_featurebench_seconds"],
                **manifest_timeouts,
            }
        else:
            # Backward-compatible parsing keeps malformed pre-hardening runs
            # auditable, while the frozen hardened protocol uses the branch above.
            cli_limits = {
                "max_replans": limits["max_replans"],
                "max_tasks": limits["max_tasks"],
                "max_steps": limits["max_steps_per_task"],
            }
            manifest_limits = dict(cli_limits)
            manifest_timeouts = None
            provenance_timeouts = None
        expected = {
            "task_id": dataset["instance_id"],
            "dataset_revision": dataset["revision"],
            "prompt_chars": dataset["problem_statement_chars"],
            "prompt_sha256": dataset["problem_statement_sha256"],
            "featurebench_revision": sources["featurebench"]["commit"],
            "attempt": cell["attempts"],
            "model": cell["model"],
            "protocol_served_models": list(cell.get("expected_served_models", [])),
            "provider": provider_order[0],
            "allow_fallbacks": provider["allow_fallbacks"],
            "require_parameters": provider["require_parameters"],
            "reasoning_policy": cell["reasoning_policy"],
            "adapter_code_revision": sources["askme"].get(
                "adapter_code_revision", sources["askme"].get("adapter_revision")
            ),
            "base_source_sha256": sources["askme"]["base_source_sha256"],
            "code_files": dict(sources["askme"]["code_files"]),
            "cli_limits": cli_limits,
            "manifest_limits": manifest_limits,
            "manifest_timeouts": manifest_timeouts,
            "provenance_timeouts": provenance_timeouts,
        }
    except (KeyError, IndexError, TypeError, ValueError) as error:
        audit.fail("protocol_shape", f"protocol is missing a frozen field: {error}")
        return None
    if expected["attempt"] != 1:
        audit.fail("protocol_attempts", "canary protocol must freeze exactly one attempt")
    if not isinstance(expected["provider"], str) or not expected["provider"]:
        audit.fail("protocol_provider", "protocol must freeze one provider")
    if expected["reasoning_policy"] != "gated":
        audit.fail("protocol_reasoning_policy", "canary protocol must freeze gated policy")
    if not isinstance(expected["prompt_chars"], int) or expected["prompt_chars"] < 1:
        audit.fail("protocol_prompt_chars", "protocol prompt character count is invalid")
    if not isinstance(expected["prompt_sha256"], str) or len(expected["prompt_sha256"]) != 64:
        audit.fail("protocol_prompt_hash", "protocol prompt SHA-256 is invalid")
    if not isinstance(expected["adapter_code_revision"], str):
        audit.fail("protocol_adapter_revision", "protocol adapter code revision is missing")
    if (
        not isinstance(expected["base_source_sha256"], str)
        or len(expected["base_source_sha256"]) != 64
    ):
        audit.fail("protocol_source_hash", "protocol AskMe source SHA-256 is invalid")
    if set(expected["code_files"]) != REQUIRED_CODE_FILES or not all(
        isinstance(value, str) and len(value) == 64 for value in expected["code_files"].values()
    ):
        audit.fail(
            "protocol_code_files",
            "protocol must pin SHA-256 hashes for both canary code files",
        )
    if not expected["protocol_served_models"] or not all(
        isinstance(model, str) and bool(model.strip())
        for model in expected["protocol_served_models"]
    ):
        audit.fail(
            "protocol_served_models",
            "protocol expected served model IDs must be non-empty strings",
        )
    elif len(set(expected["protocol_served_models"])) != len(expected["protocol_served_models"]):
        audit.fail(
            "protocol_served_models",
            "protocol expected served model IDs must not contain duplicates",
        )
    return expected


def _audit_prediction(
    audit: _Audit,
    run_dir: Path,
    expected: Mapping[str, Any],
) -> tuple[Optional[Mapping[str, Any]], int]:
    records = audit.read_jsonl(run_dir / "output.jsonl", "prediction_jsonl")
    if records is None:
        return None, 0
    if len(records) != 1:
        audit.fail(
            "prediction_count",
            f"output.jsonl must contain exactly one prediction; found {len(records)}",
        )
        return (records[0] if records else None), len(records)
    prediction = records[0]
    checks = {
        "instance_id": expected["task_id"],
        "n_attempt": expected["attempt"],
        "agent": "askme",
        "model": expected["model"],
    }
    for field, frozen in checks.items():
        _expect_equal(
            audit,
            f"prediction_{field}",
            f"prediction {field}",
            prediction.get(field),
            frozen,
        )
    if not isinstance(prediction.get("success"), bool):
        audit.fail("prediction_success_type", "prediction success must be boolean")
    if not isinstance(prediction.get("model_patch"), str):
        audit.fail("prediction_patch_type", "prediction model_patch must be a string")
    return prediction, len(records)


def _audit_integrity(
    audit: _Audit,
    attempt_dir: Path,
    prediction: Optional[Mapping[str, Any]],
    provenance: Optional[Mapping[str, Any]],
    expected: Mapping[str, Any],
    askme_source: Path,
    code_root: Path,
) -> tuple[
    Optional[str],
    Optional[Mapping[str, Any]],
    Optional[Mapping[str, Any]],
    Optional[list[Any]],
    dict[str, Any],
]:
    prompt_path = attempt_dir / "askme-prompt.txt"
    manifest_path = attempt_dir / "askme-adapter.json"
    result_path = attempt_dir / "askme-result.json"
    run_log_path = attempt_dir / "askme-run.jsonl"

    prompt: Optional[str] = None
    prompt_bytes: Optional[bytes] = None
    if audit.regular_file(prompt_path, "prompt"):
        try:
            prompt_bytes = prompt_path.read_bytes()
            prompt = prompt_bytes.decode("utf-8")
        except (OSError, UnicodeError) as error:
            audit.fail("prompt_invalid", f"preserved prompt is not exact UTF-8: {error}")

    manifest_raw = audit.read_json(manifest_path, "adapter_manifest")
    manifest = (
        _as_mapping(audit, manifest_raw, "adapter_manifest") if manifest_raw is not None else None
    )
    result_raw = audit.read_json(result_path, "agent_result")
    result = _as_mapping(audit, result_raw, "agent_result") if result_raw is not None else None
    run_log = audit.read_jsonl(run_log_path, "run_log")

    source_hash: Optional[str] = None
    source_size: Optional[int] = None
    audit.artifact("askme_source", askme_source)
    if askme_source.is_symlink() or not askme_source.is_file():
        audit.fail("askme_source_missing", "pinned AskMe source is missing or a symlink")
    else:
        try:
            source_hash = _sha256_file(askme_source)
            source_size = askme_source.stat().st_size
        except OSError as error:
            audit.fail("askme_source_unreadable", f"could not hash pinned AskMe source: {error}")

    hashes: dict[str, Any] = {}
    if prompt_bytes is not None:
        hashes["prompt_sha256"] = _sha256_bytes(prompt_bytes)
    if source_hash is not None:
        hashes["askme_sha256"] = source_hash
        _expect_equal(
            audit,
            "protocol_source_hash",
            "pinned AskMe source hash",
            source_hash,
            expected["base_source_sha256"],
        )

    code_hashes: dict[str, str] = {}
    root_resolved = code_root.resolve()
    for relative, frozen_hash in sorted(expected["code_files"].items()):
        path = code_root / relative
        audit.artifact(f"code_file:{relative}", path)
        try:
            resolved = path.resolve()
        except OSError as error:
            audit.fail("code_file_path", f"could not resolve pinned code file: {error}")
            continue
        if resolved != root_resolved and root_resolved not in resolved.parents:
            audit.fail("code_file_path", "pinned code file escaped the repository root")
            continue
        if path.is_symlink() or not path.is_file():
            audit.fail("code_file_missing", f"pinned code file is missing: {relative}")
            continue
        try:
            observed_hash = _sha256_file(path)
        except OSError as error:
            audit.fail("code_file_unreadable", f"could not hash pinned code file: {error}")
            continue
        code_hashes[relative] = observed_hash
        _expect_equal(
            audit,
            "code_file_hash",
            f"pinned code file hash for {relative}",
            observed_hash,
            frozen_hash,
        )
    hashes["code_files"] = code_hashes

    if manifest is not None:
        manifest_checks = {
            "schema_version": 1,
            "agent": "askme",
            "model": expected["model"],
            "provider": expected["provider"],
            "allow_provider_fallbacks": expected["allow_fallbacks"],
            "require_provider_parameters": expected["require_parameters"],
            "reasoning_policy": expected["reasoning_policy"],
            "limits": expected["manifest_limits"],
        }
        if expected["manifest_timeouts"] is not None:
            manifest_checks.update(
                {
                    "timeouts": expected["manifest_timeouts"],
                    "network_policy_requested": "deny",
                    "container_egress_isolated": False,
                }
            )
        for field, frozen in manifest_checks.items():
            _expect_equal(
                audit,
                f"manifest_{field}",
                f"adapter manifest {field}",
                manifest.get(field),
                frozen,
            )
        if prompt is not None:
            _expect_equal(
                audit,
                "manifest_prompt_chars",
                "adapter manifest prompt_chars",
                manifest.get("prompt_chars"),
                len(prompt),
            )
            _expect_equal(
                audit,
                "protocol_prompt_chars",
                "preserved prompt character count",
                len(prompt),
                expected["prompt_chars"],
            )
            _expect_equal(
                audit,
                "manifest_goal_context_chars",
                "adapter manifest goal_context_chars",
                manifest.get("goal_context_chars"),
                len(prompt),
            )
        if source_hash is not None:
            _expect_equal(
                audit,
                "manifest_source_hash",
                "adapter manifest AskMe source hash",
                manifest.get("askme_sha256"),
                source_hash,
            )
        if source_size is not None:
            _expect_equal(
                audit,
                "manifest_source_size",
                "adapter manifest AskMe source size",
                manifest.get("askme_size_bytes"),
                source_size,
            )
        if prompt_bytes is not None:
            _expect_equal(
                audit,
                "manifest_prompt_hash",
                "adapter manifest prompt hash",
                manifest.get("prompt_sha256"),
                hashes["prompt_sha256"],
            )
            _expect_equal(
                audit,
                "protocol_prompt_hash",
                "preserved prompt hash",
                hashes["prompt_sha256"],
                expected["prompt_sha256"],
            )

    if provenance is not None:
        provenance_checks = {
            "task_id": expected["task_id"],
            "model": expected["model"],
            "provider": expected["provider"],
            "allow_provider_fallbacks": expected["allow_fallbacks"],
            "require_provider_parameters": expected["require_parameters"],
            "dataset_revision": expected["dataset_revision"],
            "featurebench_revision": expected["featurebench_revision"],
            "limits": expected["manifest_limits"],
            "askme_git_dirty": False,
            "featurebench_git_dirty": False,
        }
        if expected["provenance_timeouts"] is not None:
            provenance_checks.update(
                {
                    "timeouts": expected["provenance_timeouts"],
                    "network_policy_requested": "deny",
                    "container_egress_isolated": False,
                    "credential_in_container_environment": False,
                }
            )
        _expect_equal(
            audit,
            "provenance_adapter_code_revision",
            "run provenance adapter code revision",
            provenance.get("adapter_code_revision"),
            expected["adapter_code_revision"],
        )
        _expect_equal(
            audit,
            "provenance_code_files",
            "run provenance canary code-file hashes",
            provenance.get("code_files"),
            expected["code_files"],
        )
        for field, frozen in provenance_checks.items():
            _expect_equal(
                audit,
                f"provenance_{field}",
                f"run provenance {field}",
                provenance.get(field),
                frozen,
            )
        if source_hash is not None:
            _expect_equal(
                audit,
                "provenance_source_hash",
                "run provenance AskMe source hash",
                provenance.get("askme_sha256"),
                source_hash,
            )

    if prediction is not None and prompt is not None:
        metadata = prediction.get("task_metadata")
        if not isinstance(metadata, dict):
            audit.fail("prediction_metadata", "prediction task_metadata must be an object")
        else:
            _expect_equal(
                audit,
                "prediction_prompt",
                "prediction problem_statement",
                metadata.get("problem_statement"),
                prompt,
            )

    agent_status: Optional[str] = None
    if result is not None:
        status = result.get("status")
        if not isinstance(status, str) or not status:
            audit.fail("agent_result_status", "agent result needs a non-empty status")
        else:
            agent_status = status

    return agent_status, result, manifest, run_log, hashes


def _audit_run_log(
    audit: _Audit,
    events: Optional[list[Any]],
    prompt: Optional[str],
    expected: Mapping[str, Any],
    allowed_served_models: set[str],
) -> tuple[list[dict[str, Any]], list[Mapping[str, Any]]]:
    if events is None:
        return [], []
    run_starts = [event for event in events if event.get("event") == "run_start"]
    if len(run_starts) != 1:
        audit.fail(
            "run_start_count",
            f"run log must contain exactly one run_start; found {len(run_starts)}",
        )
    if run_starts:
        start = run_starts[0]
        if prompt is not None:
            _expect_equal(
                audit, "run_start_prompt", "run_start prompt", start.get("prompt"), prompt
            )
        start_checks = {
            "backend": "openrouter",
            "model": expected["model"],
            "provider": expected["provider"],
            "allow_provider_fallbacks": expected["allow_fallbacks"],
            "require_provider_parameters": expected["require_parameters"],
            "reasoning_policy": expected["reasoning_policy"],
        }
        for field, frozen in start_checks.items():
            _expect_equal(
                audit,
                f"run_start_{field}",
                f"run_start {field}",
                start.get(field),
                frozen,
            )
        if prompt is not None:
            exact_limits = {
                **expected["cli_limits"],
                "goal_context_chars": len(prompt),
            }
            _expect_equal(
                audit,
                "run_start_limits",
                "run_start limits",
                start.get("limits"),
                exact_limits,
            )

    tokens = [event for event in events if event.get("event") == "tokens"]
    if not tokens:
        audit.fail("token_events_missing", "run log must contain at least one token event")
    for index, event in enumerate(tokens, 1):
        model = event.get("model")
        if model not in allowed_served_models:
            audit.fail(
                "token_served_model",
                f"token event {index} used an unapproved served model",
            )
        provider = event.get("provider")
        if not isinstance(provider, str) or provider.casefold() != expected["provider"].casefold():
            audit.fail(
                "token_served_provider",
                f"token event {index} was not served by the pinned provider "
                f"{expected['provider']!r}",
            )

    decisions = [event for event in events if event.get("event") == "reasoning_decision"]
    if not decisions:
        audit.fail(
            "reasoning_decisions_missing",
            "run log must contain at least one reasoning_decision",
        )
    for index, event in enumerate(decisions, 1):
        if event.get("requested_policy") != "gated":
            audit.fail(
                "reasoning_policy",
                f"reasoning_decision {index} did not request gated policy",
            )

    actions: list[dict[str, Any]] = []
    for event in events:
        if event.get("event") != "step":
            continue
        action = event.get("action")
        arg = event.get("arg", "")
        if not isinstance(action, str) or not isinstance(arg, str):
            audit.fail("run_action_shape", "run-log step action and arg must be strings")
            continue
        if not isinstance(event.get("ok"), bool):
            audit.fail("run_action_shape", "run-log step outcome must be boolean")
        if event.get("error_type") is not None and not isinstance(event.get("error_type"), str):
            audit.fail("run_action_shape", "run-log step error_type must be a string or null")
        actions.append(
            {
                "action": action,
                "arg": arg,
                "ok": event.get("ok"),
                "error_type": event.get("error_type"),
            }
        )
    return actions, tokens


def _classify_terminal_timeout(
    audit: _Audit,
    agent_result: Optional[Mapping[str, Any]],
    policy_events: Sequence[Mapping[str, Any]],
) -> bool:
    """Recognize only the launcher's exact planned inner-timeout terminal shape."""
    if agent_result is None or agent_result.get("status") != "adapter_interrupted":
        return False

    reason = agent_result.get("adapter_terminal_reason")
    expected_reason = "launcher received signal 15"
    state = agent_result.get("state")
    errors = state.get("errors") if isinstance(state, dict) else None
    terminals = [event for event in policy_events if event.get("event") == "launcher_terminal"]
    launcher_ends = [event for event in policy_events if event.get("event") == "launcher_end"]
    exact = (
        reason == expected_reason
        and isinstance(errors, list)
        and expected_reason in errors
        and len(terminals) == 1
        and terminals[0].get("reason") == expected_reason
        and policy_events[-1] is terminals[0]
        and not launcher_ends
    )
    if not exact:
        audit.fail(
            "terminal_interruption_invalid",
            "adapter_interrupted did not match the planned inner-timeout signal shape",
        )
        return False
    return True


def _audit_policy_log(
    audit: _Audit,
    attempt_dir: Path,
    run_actions: Sequence[Mapping[str, Any]],
    hashes: Mapping[str, Any],
    agent_result: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    events = audit.read_jsonl(attempt_dir / POLICY_LOG_NAME, "policy_log")
    if events is None:
        return {
            "decisions": 0,
            "denials": [],
            "compliant": None,
            "timed_out": False,
            "in_flight_action": None,
        }
    timed_out = _classify_terminal_timeout(audit, agent_result, events)
    launches = [event for event in events if event.get("event") == "launcher_start"]
    if len(launches) != 1:
        audit.fail(
            "policy_launcher_count",
            f"policy log must contain exactly one launcher_start; found {len(launches)}",
        )
    elif launches:
        launch = launches[0]
        _expect_equal(
            audit,
            "policy_launcher_credential",
            "policy launcher credential_in_child_environment",
            launch.get("credential_in_child_environment"),
            False,
        )
        _expect_equal(
            audit,
            "policy_launcher_egress",
            "policy launcher container_egress_isolated",
            launch.get("container_egress_isolated"),
            False,
        )
        for field in ("askme_sha256", "prompt_sha256"):
            if field in hashes:
                _expect_equal(
                    audit,
                    f"policy_launcher_{field}",
                    f"policy launcher {field}",
                    launch.get(field),
                    hashes[field],
                )

    decisions = [event for event in events if event.get("event") == "action_decision"]
    action_results = [event for event in events if event.get("event") == "action_result"]
    denials: list[dict[str, Any]] = []
    for index, event in enumerate(decisions, 1):
        if event.get("sequence") != index:
            audit.fail(
                "policy_decision_sequence",
                f"policy action decision {index} has a non-contiguous sequence",
            )
        if not isinstance(event.get("action"), str) or not isinstance(event.get("arg"), str):
            audit.fail(
                "policy_action_shape",
                f"policy action decision {index} needs string action and arg fields",
            )
        decision = event.get("decision")
        if decision not in {"allow", "deny"}:
            audit.fail(
                "policy_decision_value",
                f"policy action decision {index} has an invalid decision value",
            )
        elif decision == "deny":
            denials.append(
                {
                    "sequence": event.get("sequence"),
                    "action": event.get("action"),
                    "reason": event.get("reason"),
                }
            )
    in_flight_action: Optional[dict[str, Any]] = None
    completed_decisions = decisions
    if (
        timed_out
        and len(decisions) == len(run_actions) + 1
        and len(action_results) == len(run_actions)
    ):
        trailing = decisions[-1]
        if trailing.get("decision") != "allow":
            audit.fail(
                "policy_in_flight_decision",
                "terminal in-flight action must be an allowed action",
            )
        else:
            in_flight_action = {
                "sequence": trailing.get("sequence"),
                "action": trailing.get("action"),
            }
        completed_decisions = decisions[:-1]

    if len(completed_decisions) != len(run_actions):
        audit.fail(
            "policy_action_count",
            "policy action decisions do not cover every executed run-log action",
        )
    if len(action_results) != len(completed_decisions):
        audit.fail(
            "policy_result_count",
            "policy action results do not cover every policy decision",
        )
    for index, (decision, action) in enumerate(zip(completed_decisions, run_actions), 1):
        if decision.get("action") != action.get("action"):
            audit.fail(
                "policy_action_mismatch",
                f"policy action decision {index} does not match the run-log action",
            )
        decision_arg = decision.get("arg")
        run_arg = action.get("arg", "")
        # AskMe deliberately bounds run-log arguments to 120 characters; the
        # policy log retains the full redacted argument.  Compare the exact
        # value unless the run-log value is at that documented bound.
        arg_matches = decision_arg == run_arg
        if (
            not arg_matches
            and isinstance(decision_arg, str)
            and len(run_arg) == 120
            and decision_arg.startswith(run_arg)
        ):
            arg_matches = True
        if not arg_matches:
            audit.fail(
                "policy_arg_mismatch",
                f"policy action decision {index} does not match the run-log argument",
            )
    for index, (decision, action_result, action) in enumerate(
        zip(completed_decisions, action_results, run_actions), 1
    ):
        for field in ("sequence", "action", "decision"):
            if action_result.get(field) != decision.get(field):
                audit.fail(
                    "policy_result_mismatch",
                    f"policy action result {index} does not match its decision",
                )
                break
        if action_result.get("ok") != action.get("ok"):
            audit.fail(
                "policy_result_outcome_mismatch",
                f"policy action result {index} does not match the run-log outcome",
            )
        if action_result.get("error_type") != action.get("error_type"):
            audit.fail(
                "policy_result_error_mismatch",
                f"policy action result {index} does not match the run-log error type",
            )
        if decision.get("decision") == "deny" and (
            action_result.get("ok") is not False
            or action_result.get("error_type") != "policy_violation"
            or action.get("ok") is not False
            or action.get("error_type") != "policy_violation"
            or not isinstance(decision.get("reason"), str)
            or not decision.get("reason")
        ):
            audit.fail(
                "policy_denial_shape",
                f"policy denial {index} lacks a matching policy_violation result",
            )
    return {
        "decisions": len(decisions),
        "denials": denials,
        "compliant": not denials,
        "timed_out": timed_out,
        "in_flight_action": in_flight_action,
    }


def _audit_run_end(
    audit: _Audit,
    events: Optional[list[Any]],
    agent_status: Optional[str],
    timed_out: bool = False,
) -> None:
    if events is None:
        return
    ends = [event for event in events if event.get("event") == "run_end"]
    if timed_out:
        if len(ends) > 1:
            audit.fail(
                "run_end_count",
                f"timed-out run log has multiple run_end events; found {len(ends)}",
            )
        return
    if len(ends) != 1:
        audit.fail(
            "run_end_count",
            f"run log must contain exactly one run_end; found {len(ends)}",
        )
        return
    if agent_status is None:
        return
    run_status = ends[0].get("status")
    if agent_status in AGENT_COMPLETE_STATUSES:
        allowed = {"complete", "complete_deterministic_after_exhausted"}
        if run_status not in allowed:
            audit.fail("run_end_status", "run_end conflicts with agent completion")
    elif run_status != agent_status:
        audit.fail("run_end_status", "run_end conflicts with structured agent status")


def _scan_for_secret(audit: _Audit) -> int:
    if not audit.secret:
        audit.fail(
            "api_key_missing",
            "a non-empty API key is required for the exact-byte leak scan",
        )
        return 0
    leaks = 0
    if not audit.run_dir.is_dir():
        return leaks
    for path in sorted(audit.run_dir.rglob("*")):
        if path.is_symlink():
            audit.fail("retained_symlink", "retained run tree contains a symlink")
            continue
        if not path.is_file():
            continue
        try:
            leaked = _contains_bytes(path, audit.secret)
        except OSError as error:
            audit.fail("secret_scan_error", f"could not scan retained file: {error}")
            continue
        if leaked:
            leaks += 1
            try:
                relative = path.relative_to(audit.run_dir)
            except ValueError:
                relative = path
            audit.fail(
                "api_key_leak",
                f"exact API key bytes found in retained file {relative}",
            )
    return leaks


def audit_canary(
    run_dir: Path,
    *,
    api_key: str,
    protocol_path: Path = DEFAULT_PROTOCOL,
    askme_source: Path = DEFAULT_ASKME_SOURCE,
    code_root: Optional[Path] = None,
    expected_served_models: Iterable[str] = (),
    expected_run_revision: Optional[str] = None,
    raise_on_invalid: bool = False,
) -> dict[str, Any]:
    """Audit one retained canary and return a JSON-serializable result.

    ``expected_served_models``, when supplied, must exactly repeat the protocol's
    preregistered dated route IDs. Those dated IDs are the sole allowed served
    models; the requested alias is not an audit fallback.
    ``expected_run_revision`` identifies the later clean registration commit;
    it cannot be embedded as that commit's own Git hash in the protocol.
    ``api_key`` is used only as an exact byte sequence for the leak scan and is
    never included in the returned data or an exception message.
    """
    run_dir = Path(run_dir)
    protocol_path = Path(protocol_path)
    askme_source = Path(askme_source)
    code_root = Path(code_root) if code_root is not None else askme_source.parent
    secret = api_key.encode("utf-8") if isinstance(api_key, str) else b""
    audit = _Audit(run_dir, secret)

    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "invalid",
        "infrastructure_valid": False,
        "agent_completion": None,
        "agent_status": None,
        "prediction_success": None,
        "timed_out": False,
        "policy_compliant": None,
        "policy_denials": [],
        "policy_in_flight_action": None,
        "qualification_valid": False,
        "route": {},
        "counts": {},
        "integrity": {},
        "artifacts": audit.artifacts,
        "violations": audit.violations,
    }

    if run_dir.is_symlink() or not run_dir.is_dir():
        audit.fail("run_dir_missing", "retained run directory is missing or a symlink")
        _scan_for_secret(audit)
        if raise_on_invalid:
            raise CanaryAuditError(result)
        return result

    protocol_raw = audit.read_json(protocol_path, "protocol")
    protocol = _as_mapping(audit, protocol_raw, "protocol") if protocol_raw is not None else None
    expected = _protocol_expectations(audit, protocol) if protocol is not None else None
    if expected is None:
        _scan_for_secret(audit)
        if raise_on_invalid:
            raise CanaryAuditError(result)
        return result

    protocol_served_models = {
        model.strip()
        for model in expected["protocol_served_models"]
        if isinstance(model, str) and model.strip()
    }
    supplied_served_models: set[str] = set()
    for model in expected_served_models:
        if not isinstance(model, str) or not model.strip():
            audit.fail(
                "expected_served_model",
                "explicit expected served model IDs must be non-empty strings",
            )
            continue
        normalized = model.strip()
        supplied_served_models.add(normalized)
    if supplied_served_models and supplied_served_models != protocol_served_models:
        audit.fail(
            "expected_served_models_mismatch",
            "supplied served-model allowlist differs from the preregistered protocol",
        )
    allowed_models = protocol_served_models

    result["route"] = {
        "requested_model": expected["model"],
        "allowed_served_models": sorted(allowed_models),
        "requested_provider": expected["provider"],
    }

    prediction, prediction_count = _audit_prediction(audit, run_dir, expected)
    if prediction is not None and isinstance(prediction.get("success"), bool):
        result["prediction_success"] = prediction["success"]

    provenance_raw = audit.read_json(run_dir / "askme-canary.json", "run_provenance")
    provenance = (
        _as_mapping(audit, provenance_raw, "run_provenance") if provenance_raw is not None else None
    )

    attempt_dir = run_dir / "run_outputs" / expected["task_id"] / f"attempt-{expected['attempt']}"
    audit.artifact("attempt_dir", attempt_dir)
    if attempt_dir.is_symlink() or not attempt_dir.is_dir():
        audit.fail("attempt_dir_missing", "canonical task attempt directory is missing")

    agent_status, agent_result, _manifest, events, hashes = _audit_integrity(
        audit,
        attempt_dir,
        prediction,
        provenance,
        expected,
        askme_source,
        code_root,
    )
    result["agent_status"] = agent_status
    result["agent_completion"] = agent_status in AGENT_COMPLETE_STATUSES if agent_status else None
    result["integrity"] = hashes
    if (
        prediction is not None
        and isinstance(prediction.get("success"), bool)
        and agent_status is not None
        and prediction["success"] != (agent_status in AGENT_COMPLETE_STATUSES)
    ):
        audit.fail(
            "prediction_completion_mismatch",
            "prediction success disagrees with structured AskMe completion",
        )
    if provenance is not None:
        _audit_endpoint_catalog_preflight(audit, provenance, expected)
        recorded_models = provenance.get("expected_served_models")
        if (
            not isinstance(recorded_models, list)
            or not all(isinstance(model, str) for model in recorded_models)
            or set(recorded_models) != protocol_served_models
            or len(recorded_models) != len(protocol_served_models)
        ):
            audit.fail(
                "provenance_expected_served_models",
                "run provenance expected served models differ from the protocol",
            )
        if expected_run_revision is not None:
            if not isinstance(expected_run_revision, str) or not expected_run_revision.strip():
                audit.fail(
                    "expected_run_revision",
                    "expected run revision must be a non-empty string",
                )
            else:
                for field in (
                    "askme_repository_revision",
                    "expected_askme_repository_revision",
                ):
                    _expect_equal(
                        audit,
                        f"provenance_{field}",
                        f"run provenance {field}",
                        provenance.get(field),
                        expected_run_revision.strip(),
                    )

    prompt: Optional[str] = None
    prompt_path = attempt_dir / "askme-prompt.txt"
    if prompt_path.is_file() and not prompt_path.is_symlink():
        try:
            prompt = prompt_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            pass

    run_actions, token_events = _audit_run_log(audit, events, prompt, expected, allowed_models)
    policy = _audit_policy_log(audit, attempt_dir, run_actions, hashes, agent_result)
    result["timed_out"] = policy["timed_out"]
    result["policy_compliant"] = policy["compliant"]
    result["policy_denials"] = policy["denials"]
    result["policy_in_flight_action"] = policy["in_flight_action"]
    _audit_run_end(audit, events, agent_status, timed_out=policy["timed_out"])
    leaks = _scan_for_secret(audit)

    served_models = sorted(
        {
            observed_model
            for event in token_events
            if isinstance((observed_model := event.get("model")), str)
        }
    )
    served_providers = sorted(
        {
            observed_provider
            for event in token_events
            if isinstance((observed_provider := event.get("provider")), str)
        }
    )
    result["route"].update({"served_models": served_models, "served_providers": served_providers})
    result["counts"] = {
        "predictions": prediction_count,
        "token_events": len(token_events),
        "run_actions": len(run_actions),
        "policy_action_decisions": policy["decisions"],
        "policy_denials": len(policy["denials"]),
        "policy_in_flight_actions": int(policy["in_flight_action"] is not None),
        "api_key_leaks": leaks,
    }

    result["infrastructure_valid"] = not audit.violations
    result["status"] = "valid" if result["infrastructure_valid"] else "invalid"
    result["qualification_valid"] = bool(
        result["infrastructure_valid"] and result["policy_compliant"]
    )
    if raise_on_invalid and not result["infrastructure_valid"]:
        raise CanaryAuditError(result)
    return result


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--askme-source", type=Path, default=DEFAULT_ASKME_SOURCE)
    parser.add_argument(
        "--code-root",
        type=Path,
        default=DEFAULT_ASKME_SOURCE.parent,
        help="Repository root containing the preregistered canary code files",
    )
    parser.add_argument(
        "--expected-served-model",
        action="append",
        default=[],
        help="Assert one preregistered dated served-model ID (repeatable)",
    )
    parser.add_argument(
        "--expected-run-revision",
        required=True,
        help="Clean protocol-registration commit used for the outcome-bearing run",
    )
    parser.add_argument(
        "--api-key-env",
        default="OPENROUTER_API_KEY",
        help="Environment variable read for the exact-byte leak scan",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for the complete structured audit JSON",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    result = audit_canary(
        args.run_dir,
        protocol_path=args.protocol,
        askme_source=args.askme_source,
        code_root=args.code_root,
        api_key=os.environ.get(args.api_key_env, ""),
        expected_served_models=args.expected_served_model,
        expected_run_revision=args.expected_run_revision,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["infrastructure_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
