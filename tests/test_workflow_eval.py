import json
from pathlib import Path
from unittest.mock import patch

import askme
import pytest

from workflow_eval import (
    ManifestError,
    _askme_agent,
    evaluate_workflow,
    load_manifest,
)


MANIFEST = Path(__file__).parent / "workflows" / "config_precedence" / "manifest.json"

FIXED_IMPLEMENTATION = '''#!/usr/bin/env python3
import argparse
import json
import os
import sys

DEFAULT_TIMEOUT = 30
TIMEOUT_ERROR = "error: timeout must be a positive integer"

def load_config(path):
    if path is None:
        return {}
    with open(path, encoding="utf-8") as source:
        return json.load(source)

def resolve_timeout(cli_timeout, environment, config):
    if cli_timeout is not None:
        raw = cli_timeout
    elif "ASKME_TIMEOUT" in environment:
        raw = environment["ASKME_TIMEOUT"]
    elif "timeout" in config:
        raw = config["timeout"]
    else:
        raw = DEFAULT_TIMEOUT
    try:
        value = int(raw)
    except (ValueError, TypeError):
        raise ValueError(TIMEOUT_ERROR)
    if value <= 0:
        raise ValueError(TIMEOUT_ERROR)
    return value

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    parser.add_argument("--timeout")
    parser.add_argument("--name", default="service")
    args = parser.parse_args(argv)
    try:
        timeout = resolve_timeout(args.timeout, os.environ, load_config(args.config))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        print(TIMEOUT_ERROR, file=sys.stderr)
        return 2
    print(json.dumps({"name": args.name, "timeout": timeout}, sort_keys=True))
    return 0

if __name__ == "__main__":
    sys.exit(main())
'''


def test_noop_is_rejected_but_public_regressions_pass(tmp_path):
    result = evaluate_workflow(
        MANIFEST,
        lambda _prompt, _workspace: {"status": "complete"},
        workspace=tmp_path / "noop-workspace",
    )

    assert result["infrastructure_valid"] is True
    assert result["agent_complete"] is True
    assert result["regression_passed"] is True
    assert result["feedback_passed"] is False
    assert result["acceptance_passed"] is False
    assert result["artifact_accepted"] is False
    assert result["false_completion"] is True
    assert result["outcome"] == "false_completion"


def test_reference_like_fix_passes_public_and_held_out_checks(tmp_path):
    def reference_agent(_prompt, workspace):
        (workspace / "config_cli.py").write_text(FIXED_IMPLEMENTATION, encoding="utf-8")
        return {"status": "complete"}

    result = evaluate_workflow(
        MANIFEST,
        reference_agent,
        workspace=tmp_path / "fixed-workspace",
    )

    assert result["infrastructure_valid"] is True
    assert result["protected_files_intact"] is True
    assert result["regression_passed"] is True
    assert result["feedback_passed"] is True
    assert result["acceptance_passed"] is True
    assert result["artifact_accepted"] is True
    assert result["false_completion"] is False
    assert result["outcome"] == "clean_success"


def test_protected_public_test_tampering_invalidates_run(tmp_path):
    def tampering_agent(_prompt, workspace):
        public_test = workspace / "tests" / "check_regression.py"
        public_test.write_text(public_test.read_text(encoding="utf-8") + "\n# changed\n",
                               encoding="utf-8")
        return {"status": "complete"}

    result = evaluate_workflow(
        MANIFEST,
        tampering_agent,
        workspace=tmp_path / "tampered-workspace",
    )

    assert result["infrastructure_valid"] is True
    assert result["integrity_passed"] is False
    assert result["run_valid"] is False
    assert result["protected_files_intact"] is False
    assert result["protected_files_changed"] == ["tests/check_regression.py"]
    assert result["infrastructure_errors"] == []
    assert "protected files changed" in result["integrity_error"]
    assert result["outcome"] == "invalid_run"


def test_held_out_evaluator_runs_when_agent_is_incomplete(tmp_path):
    result = evaluate_workflow(
        MANIFEST,
        lambda _prompt, _workspace: {"status": "exhausted"},
        workspace=tmp_path / "incomplete-workspace",
    )

    assert result["agent_status"] == "exhausted"
    assert result["agent_complete"] is False
    assert result["checks"]["public_feedback"]["status"] == "completed"
    assert result["checks"]["held_out_acceptance"]["status"] == "completed"
    assert result["acceptance_passed"] is False
    assert result["false_completion"] is False
    assert result["outcome"] == "incomplete_failure"


def test_accepted_artifact_is_retained_when_agent_reports_incomplete(tmp_path):
    def incomplete_fixing_agent(_prompt, workspace):
        (workspace / "config_cli.py").write_text(FIXED_IMPLEMENTATION, encoding="utf-8")
        return {"status": "exhausted"}

    result = evaluate_workflow(
        MANIFEST,
        incomplete_fixing_agent,
        workspace=tmp_path / "accepted-incomplete-workspace",
    )

    assert result["run_valid"] is True
    assert result["artifact_accepted"] is True
    assert result["agent_complete"] is False
    assert result["false_completion"] is False
    assert result["outcome"] == "accepted_incomplete"


def test_agent_callback_error_does_not_become_infrastructure_failure(tmp_path):
    def failing_agent(_prompt, _workspace):
        raise RuntimeError("adapter response was lost")

    result = evaluate_workflow(
        MANIFEST,
        failing_agent,
        workspace=tmp_path / "callback-error-workspace",
    )

    assert result["infrastructure_valid"] is True
    assert result["run_valid"] is True
    assert result["agent_status"] == "error"
    assert result["agent_error"] == "RuntimeError: adapter response was lost"
    assert result["checks"]["public_feedback"]["status"] == "completed"
    assert result["checks"]["held_out_acceptance"]["status"] == "completed"
    assert result["outcome"] == "incomplete_failure"


def test_result_schema_is_json_serializable(tmp_path):
    result = evaluate_workflow(
        MANIFEST,
        lambda _prompt, _workspace: {"status": "exhausted"},
        workspace=tmp_path / "schema-workspace",
        reasoning_policy="off",
    )

    expected = {
        "schema_version",
        "task_id",
        "reasoning_policy",
        "agent_limits",
        "infrastructure_valid",
        "infrastructure_errors",
        "agent_status",
        "agent_complete",
        "agent_error",
        "integrity_passed",
        "integrity_error",
        "run_valid",
        "protected_files_intact",
        "protected_files_changed",
        "regression_passed",
        "feedback_passed",
        "acceptance_passed",
        "artifact_accepted",
        "false_completion",
        "outcome",
        "checks",
        "workspace_path",
    }
    assert set(result) == expected
    assert json.loads(json.dumps(result))["schema_version"] == 1
    assert result["reasoning_policy"] == "off"
    assert result["agent_limits"] == load_manifest(MANIFEST)["agent_limits"]


def test_prompt_over_frozen_goal_cap_is_rejected(tmp_path):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cap = manifest["agent_limits"]["goal_context_chars"]
    manifest["prompt"] = "x" * (cap + 1)
    over_cap = tmp_path / "over-cap.json"
    over_cap.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ManifestError, match="exceeds frozen goal_context_chars"):
        load_manifest(over_cap)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("max_replans", 0, "must be a positive integer"),
        ("final_validate", "sometimes", "must be one of"),
    ],
)
def test_invalid_agent_limits_are_rejected(tmp_path, key, value, message):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["agent_limits"][key] = value
    invalid = tmp_path / f"invalid-{key}.json"
    invalid.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ManifestError, match=message):
        load_manifest(invalid)


def test_askme_adapter_forwards_frozen_run_configuration(tmp_path):
    limits = load_manifest(MANIFEST)["agent_limits"]
    expected = {"status": "complete", "state": {}, "log": []}

    with (
        patch.object(askme, "FINAL_VALIDATE", "always"),
        patch.object(askme, "_run_loop", return_value=expected) as run_loop,
    ):
        result = _askme_agent(
            "fix configuration",
            tmp_path,
            reasoning_policy="off",
            agent_limits=limits,
        )
        assert askme.FINAL_VALIDATE == "always"

    assert result == expected
    run_loop.assert_called_once_with(
        "fix configuration",
        str(tmp_path),
        max_replans=limits["max_replans"],
        max_tasks=limits["max_tasks"],
        max_steps=limits["max_steps"],
        reasoning_policy="off",
        goal_context_chars=limits["goal_context_chars"],
    )
