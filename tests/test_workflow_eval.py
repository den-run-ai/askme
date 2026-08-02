import json
from pathlib import Path
from unittest.mock import patch

import pytest
from workflow_eval import (
    ManifestError,
    _askme_agent,
    _run_command,
    evaluate_workflow,
    load_manifest,
)

MANIFEST = Path(__file__).parent / "workflows" / "config_precedence" / "manifest.json"

FIXED_IMPLEMENTATION = """#!/usr/bin/env python3
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
"""


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
        public_test.write_text(
            public_test.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8"
        )
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
        "agent_run",
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


def test_timed_out_check_streams_are_json_serializable(tmp_path):
    timeout = __import__("subprocess").TimeoutExpired(
        ["check"], 1, output=b"partial stdout", stderr=b"partial stderr"
    )
    with patch("workflow_eval.subprocess.run", side_effect=timeout):
        result = _run_command(["check"], tmp_path, 1)

    assert result["status"] == "timeout"
    assert result["stdout"] == "partial stdout"
    assert result["stderr"] == "partial stderr"
    assert json.loads(json.dumps(result))["passed"] is False


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("max_replans", 0, "must be a positive integer"),
        ("agent_timeout_seconds", 0, "must be a positive integer"),
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


def test_askme_adapter_runs_cold_child_with_frozen_configuration(tmp_path):
    limits = load_manifest(MANIFEST)["agent_limits"]
    expected = {"status": "complete", "state": {}, "log": []}

    def child_process(command, **kwargs):
        def option(name):
            return command[command.index(name) + 1]

        assert Path(option("--working-dir")) == tmp_path
        assert tmp_path.is_dir()
        assert Path(option("--prompt-file")).read_text(encoding="utf-8") == ("fix configuration")
        assert option("--reasoning-policy") == "off"
        assert option("--max-replans") == str(limits["max_replans"])
        assert option("--max-tasks") == str(limits["max_tasks"])
        assert option("--max-steps") == str(limits["max_steps"])
        assert option("--goal-context-chars") == str(limits["goal_context_chars"])
        assert kwargs["timeout"] == limits["agent_timeout_seconds"]
        assert kwargs["env"]["AGENT_REASONING_POLICY"] == "off"
        assert kwargs["env"]["AGENT_GOAL_CONTEXT_CHARS"] == str(limits["goal_context_chars"])
        assert kwargs["env"]["AGENT_FINAL_VALIDATE"] == limits["final_validate"]
        run_log = Path(kwargs["env"]["AGENT_RUN_LOG"])
        assert str(run_log) != "/tmp/parent-agent-run.jsonl"
        assert run_log.parent != tmp_path
        run_log.write_text(
            '{"event":"run_start","model":"test-model"}\n'
            '{"event":"reasoning_decision","requested_policy":"off",'
            '"effective_level":null}\n',
            encoding="utf-8",
        )
        Path(option("--result-json")).write_text(json.dumps(expected), encoding="utf-8")
        return __import__("subprocess").CompletedProcess(
            command, 0, stdout="child stdout", stderr="child stderr"
        )

    with (
        patch.dict(
            "workflow_eval.os.environ",
            {
                "AGENT_RUN_LOG": "/tmp/parent-agent-run.jsonl",
                "AGENT_REASONING_POLICY": "invalid-parent-value",
                "AGENT_GOAL_CONTEXT_CHARS": "invalid-parent-value",
            },
        ),
        patch("workflow_eval.subprocess.run", side_effect=child_process) as child,
    ):
        result = _askme_agent(
            "fix configuration",
            tmp_path,
            reasoning_policy="off",
            agent_limits=limits,
        )

    assert result["status"] == "complete"
    metadata = result["_workflow_adapter"]
    assert metadata["status"] == "completed"
    assert metadata["exit_code"] == 0
    assert metadata["stdout"] == "child stdout"
    assert metadata["stderr"] == "child stderr"
    assert metadata["result"] == expected
    assert metadata["run_log"]["status"] == "parsed"
    assert metadata["run_log"]["event_count"] == 2
    assert metadata["run_log"]["events"][0]["model"] == "test-model"
    assert child.call_count == 1


def test_askme_adapter_reports_missing_structured_result(tmp_path):
    def missing_result_child(command, **kwargs):
        Path(kwargs["env"]["AGENT_RUN_LOG"]).write_text(
            '{"event":"run_start","reasoning_policy":"gated"}\n',
            encoding="utf-8",
        )
        return __import__("subprocess").CompletedProcess(
            command, 2, stdout="", stderr="child crashed"
        )

    with patch("workflow_eval.subprocess.run", side_effect=missing_result_child):
        adapter_result = _askme_agent(
            "fix configuration",
            tmp_path,
            reasoning_policy="gated",
            agent_limits=load_manifest(MANIFEST)["agent_limits"],
        )

    assert adapter_result["status"] == "error"
    assert "did not write" in adapter_result["_workflow_adapter_error"]
    assert "_workflow_adapter_infrastructure_error" not in adapter_result
    metadata = adapter_result["_workflow_adapter"]
    assert metadata["status"] == "missing_result"
    assert metadata["exit_code"] == 2
    assert metadata["stderr"] == "child crashed"
    assert metadata["run_log"]["status"] == "parsed"

    evaluated = evaluate_workflow(
        MANIFEST,
        lambda _prompt, _workspace: adapter_result,
        workspace=tmp_path / "missing-result-workspace",
    )
    assert evaluated["agent_status"] == "error"
    assert evaluated["infrastructure_valid"] is True
    assert evaluated["run_valid"] is True
    assert evaluated["outcome"] == "incomplete_failure"


def test_askme_adapter_reports_malformed_structured_result(tmp_path):
    def malformed_child(command, **_kwargs):
        result_path = Path(command[command.index("--result-json") + 1])
        result_path.write_text("{not json", encoding="utf-8")
        Path(_kwargs["env"]["AGENT_RUN_LOG"]).write_text(
            '{"event":"run_start"}\n', encoding="utf-8"
        )
        return __import__("subprocess").CompletedProcess(command, 0, stdout="partial", stderr="")

    with patch("workflow_eval.subprocess.run", side_effect=malformed_child):
        result = _askme_agent(
            "fix configuration",
            tmp_path,
            reasoning_policy="gated",
            agent_limits=load_manifest(MANIFEST)["agent_limits"],
        )

    assert result["status"] == "error"
    assert "malformed" in result["_workflow_adapter_error"]
    assert "_workflow_adapter_infrastructure_error" not in result
    assert result["_workflow_adapter"]["status"] == "malformed_result"
    assert result["_workflow_adapter"]["result_text"] == "{not json"


def test_askme_adapter_launch_failure_is_explicit(tmp_path):
    with patch("workflow_eval.subprocess.run", side_effect=OSError("no python")):
        result = _askme_agent(
            "fix configuration",
            tmp_path,
            reasoning_policy="gated",
            agent_limits=load_manifest(MANIFEST)["agent_limits"],
        )

    assert result["status"] == "error"
    assert "could not launch" in result["_workflow_adapter_error"]
    assert "_workflow_adapter_infrastructure_error" in result
    metadata = result["_workflow_adapter"]
    assert metadata["status"] == "launch_error"
    assert metadata["exit_code"] is None


def test_askme_adapter_retains_malformed_run_log_evidence(tmp_path):
    expected = {"status": "complete", "state": {}, "log": []}

    def malformed_log_child(command, **kwargs):
        result_path = Path(command[command.index("--result-json") + 1])
        result_path.write_text(json.dumps(expected), encoding="utf-8")
        Path(kwargs["env"]["AGENT_RUN_LOG"]).write_text(
            '{"event":"run_start"}\nnot-json\n', encoding="utf-8"
        )
        return __import__("subprocess").CompletedProcess(command, 0, stdout="", stderr="")

    with patch("workflow_eval.subprocess.run", side_effect=malformed_log_child):
        result = _askme_agent(
            "fix configuration",
            tmp_path,
            reasoning_policy="gated",
            agent_limits=load_manifest(MANIFEST)["agent_limits"],
        )

    assert result["status"] == "error"
    assert "malformed" in result["_workflow_adapter_error"]
    metadata = result["_workflow_adapter"]
    assert metadata["status"] == "malformed_run_log"
    assert metadata["result"] == expected
    assert metadata["run_log"]["events"] == [{"event": "run_start"}]
    assert metadata["run_log"]["errors"][0]["line"] == 2


def test_askme_adapter_rejects_reasoning_policy_violation(tmp_path):
    expected = {"status": "complete", "state": {}, "log": []}

    def noncompliant_child(command, **kwargs):
        result_path = Path(command[command.index("--result-json") + 1])
        result_path.write_text(json.dumps(expected), encoding="utf-8")
        Path(kwargs["env"]["AGENT_RUN_LOG"]).write_text(
            '{"event":"reasoning_decision","requested_policy":"off","effective_level":"medium"}\n',
            encoding="utf-8",
        )
        return __import__("subprocess").CompletedProcess(command, 0, stdout="", stderr="")

    with patch("workflow_eval.subprocess.run", side_effect=noncompliant_child):
        result = _askme_agent(
            "fix configuration",
            tmp_path,
            reasoning_policy="off",
            agent_limits=load_manifest(MANIFEST)["agent_limits"],
        )

    assert result["status"] == "error"
    assert "enabled 'medium' under off policy" in result["_workflow_adapter_error"]
    assert "_workflow_adapter_infrastructure_error" in result
    assert result["_workflow_adapter"]["status"] == "reasoning_policy_violation"


def test_askme_timeout_is_a_valid_system_outcome_with_partial_evidence(tmp_path):
    limits = load_manifest(MANIFEST)["agent_limits"]

    def timed_out_child(command, **kwargs):
        assert kwargs["timeout"] == limits["agent_timeout_seconds"]
        Path(kwargs["env"]["AGENT_RUN_LOG"]).write_text(
            '{"event":"run_start","reasoning_policy":"off"}\n'
            '{"event":"reasoning_decision","requested_policy":"off",'
            '"effective_level":null}\n',
            encoding="utf-8",
        )
        raise __import__("subprocess").TimeoutExpired(
            command,
            limits["agent_timeout_seconds"],
            output=b"partial stdout",
            stderr=b"partial stderr",
        )

    with patch("workflow_eval.subprocess.run", side_effect=timed_out_child):
        adapter_result = _askme_agent(
            "fix configuration",
            tmp_path,
            reasoning_policy="off",
            agent_limits=limits,
        )

    assert adapter_result["status"] == "error"
    assert "timed out" in adapter_result["_workflow_adapter_error"]
    assert "_workflow_adapter_infrastructure_error" not in adapter_result
    metadata = adapter_result["_workflow_adapter"]
    assert metadata["status"] == "timeout"
    assert metadata["timeout_seconds"] == limits["agent_timeout_seconds"]
    assert metadata["stdout"] == "partial stdout"
    assert metadata["stderr"] == "partial stderr"
    assert metadata["run_log"]["event_count"] == 2

    evaluated = evaluate_workflow(
        MANIFEST,
        lambda _prompt, _workspace: adapter_result,
        workspace=tmp_path / "timeout-workspace",
    )
    assert evaluated["agent_status"] == "error"
    assert evaluated["infrastructure_valid"] is True
    assert evaluated["run_valid"] is True
    assert evaluated["outcome"] == "incomplete_failure"


def test_unusable_provenance_is_retained_and_invalidates_protocol_run(tmp_path):
    evidence = {
        "status": "malformed_run_log",
        "command": ["python", "askme.py"],
        "exit_code": 2,
        "stdout": "",
        "stderr": "bad arguments",
        "result": None,
        "run_log": {"status": "malformed", "events": [], "errors": ["bad line"]},
    }

    def protocol_failure(_prompt, workspace):
        assert workspace.is_dir()
        assert (workspace / "config_cli.py").is_file()
        return {
            "status": "error",
            "_workflow_adapter_error": "malformed run log",
            "_workflow_adapter_infrastructure_error": "malformed run log",
            "_workflow_adapter": evidence,
        }

    workspace = tmp_path / "copied-before-callback"
    assert not workspace.exists()
    result = evaluate_workflow(MANIFEST, protocol_failure, workspace=workspace)

    assert result["agent_status"] == "error"
    assert result["agent_error"] == "malformed run log"
    assert result["agent_run"] == evidence
    assert result["infrastructure_valid"] is False
    assert result["run_valid"] is False
    assert result["outcome"] == "invalid_run"
    assert json.loads(json.dumps(result))["agent_run"]["exit_code"] == 2
