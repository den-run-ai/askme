import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parent / "featurebench" / "canary_audit.py"
SPEC = importlib.util.spec_from_file_location("askme_featurebench_canary_audit", MODULE_PATH)
canary_audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = canary_audit
SPEC.loader.exec_module(canary_audit)


TASK_ID = "repo.feature.lv1"
MODEL = "google/gemma-4-31b-it"
DATED_MODEL = "google/gemma-4-31b-it-20260402"
API_KEY = "sk-test-canary-exact-byte-value"


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def _protocol(path, prompt, source_hash, code_files):
    value = {
        "sources": {
            "askme": {
                "adapter_code_revision": "adapter-code-sha",
                "base_source_sha256": source_hash,
                "code_files": code_files,
            },
            "featurebench": {"commit": "featurebench-sha"},
            "dataset": {
                "revision": "dataset-sha",
                "instance_id": TASK_ID,
                "problem_statement_chars": len(prompt),
                "problem_statement_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            },
        },
        "agent_cell": {
            "attempts": 1,
            "model": MODEL,
            "expected_served_models": [DATED_MODEL],
            "provider": {
                "order": ["siliconflow"],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
            "reasoning_policy": "gated",
            "limits": {
                "max_planning_attempts_total": 3,
                "max_tasks_per_plan": 10,
                "max_steps_per_task_attempt": 10,
                "max_task_local_replans_per_task": 1,
                "max_task_attempts_per_task": 2,
                "askme_cli_parameters": {
                    "max_replans": 3,
                    "max_tasks": 10,
                    "max_steps": 10,
                },
                "goal_context": "full_problem_statement",
            },
            "timeouts": {
                "inner_askme_seconds": 3540,
                "inner_kill_grace_seconds": 15,
                "outer_featurebench_seconds": 3600,
            },
        },
    }
    _json(path, value)


def _fixture(tmp_path, *, status="complete", served_model=DATED_MODEL):
    root = tmp_path / "run"
    root.mkdir()
    source = tmp_path / "askme.py"
    source.write_text("print('pinned')\n", encoding="utf-8")
    source_bytes = source.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    prompt = "Implement the feature.\nPreserve exact behavior.\n"
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
    code_files = {}
    for relative in sorted(canary_audit.REQUIRED_CODE_FILES):
        code_path = tmp_path / relative
        code_path.parent.mkdir(parents=True, exist_ok=True)
        code_path.write_text(f"# synthetic {relative}\n", encoding="utf-8")
        code_files[relative] = hashlib.sha256(code_path.read_bytes()).hexdigest()
    protocol = tmp_path / "protocol.json"
    _protocol(protocol, prompt, source_hash, code_files)

    attempt = root / "run_outputs" / TASK_ID / "attempt-1"
    attempt.mkdir(parents=True)
    (attempt / "askme-prompt.txt").write_text(prompt, encoding="utf-8")
    _json(
        attempt / "askme-adapter.json",
        {
            "schema_version": 1,
            "agent": "askme",
            "askme_sha256": source_hash,
            "askme_size_bytes": len(source_bytes),
            "prompt_sha256": prompt_hash,
            "prompt_chars": len(prompt),
            "goal_context_chars": len(prompt),
            "model": MODEL,
            "provider": "siliconflow",
            "allow_provider_fallbacks": False,
            "require_provider_parameters": True,
            "reasoning_policy": "gated",
            "network_policy_requested": "deny",
            "container_egress_isolated": False,
            "limits": {
                "max_planning_attempts": 3,
                "max_tasks_per_plan": 10,
                "max_steps_per_task_attempt": 10,
                "max_task_local_replans": 1,
                "max_task_attempts": 2,
            },
            "timeouts": {"inner_seconds": 3540, "kill_grace_seconds": 15},
        },
    )
    _json(attempt / "askme-result.json", {"status": status, "state": {}, "log": []})
    run_end_status = status
    run_events = [
        {
            "event": "run_start",
            "prompt": prompt,
            "working_dir": "/testbed",
            "backend": "openrouter",
            "model": MODEL,
            "provider": "siliconflow",
            "allow_provider_fallbacks": False,
            "require_provider_parameters": True,
            "reasoning_policy": "gated",
            "limits": {
                "max_replans": 3,
                "max_tasks": 10,
                "max_steps": 10,
                "goal_context_chars": len(prompt),
            },
        },
        {
            "event": "reasoning_decision",
            "requested_policy": "gated",
            "requested_trigger": "executor",
            "requested_level": None,
            "effective_level": None,
            "attempt": 0,
        },
        {
            "event": "tokens",
            "prompt": 100,
            "completion": 20,
            "total": 120,
            "model": served_model,
            "provider": "SiliconFlow",
        },
        {
            "event": "step",
            "task_index": 0,
            "step": 0,
            "action": "read",
            "arg": "seaborn/_core.py",
            "ok": True,
        },
        {"event": "run_end", "status": run_end_status},
    ]
    _jsonl(attempt / "askme-run.jsonl", run_events)
    _jsonl(
        attempt / "askme-policy.jsonl",
        [
            {
                "event": "launcher_start",
                "askme_sha256": source_hash,
                "prompt_sha256": prompt_hash,
                "credential_in_child_environment": False,
                "container_egress_isolated": False,
            },
            {
                "event": "action_decision",
                "sequence": 1,
                "action": "read",
                "arg": "seaborn/_core.py",
                "decision": "allow",
            },
            {
                "event": "action_result",
                "sequence": 1,
                "action": "read",
                "decision": "allow",
                "ok": True,
                "error_type": None,
            },
        ],
    )
    (attempt / "askme-stdout.log").write_text("safe retained log\n", encoding="utf-8")
    _jsonl(
        root / "output.jsonl",
        [
            {
                "instance_id": TASK_ID,
                "n_attempt": 1,
                "model_patch": "diff --git a/a b/a\n",
                "agent": "askme",
                "model": MODEL,
                "task_metadata": {"problem_statement": prompt},
                "success": status == "complete",
                "error": None if status == "complete" else "Agent did not complete successfully",
            }
        ],
    )
    _json(
        root / "askme-canary.json",
        {
            "schema_version": 1,
            "task_id": TASK_ID,
            "model": MODEL,
            "provider": "siliconflow",
            "expected_served_models": [DATED_MODEL],
            "allow_provider_fallbacks": False,
            "require_provider_parameters": True,
            "dataset_revision": "dataset-sha",
            "featurebench_revision": "featurebench-sha",
            "network_policy_requested": "deny",
            "container_egress_isolated": False,
            "credential_in_container_environment": False,
            "limits": {
                "max_planning_attempts": 3,
                "max_tasks_per_plan": 10,
                "max_steps_per_task_attempt": 10,
                "max_task_local_replans": 1,
                "max_task_attempts": 2,
            },
            "timeouts": {
                "outer_seconds": 3600,
                "inner_seconds": 3540,
                "kill_grace_seconds": 15,
            },
            "askme_sha256": source_hash,
            "adapter_code_revision": "adapter-code-sha",
            "code_files": code_files,
            "askme_repository_revision": "run-sha",
            "expected_askme_repository_revision": "run-sha",
            "askme_git_dirty": False,
            "featurebench_git_dirty": False,
        },
    )
    return {
        "root": root,
        "attempt": attempt,
        "source": source,
        "protocol": protocol,
        "prompt": prompt,
    }


def _audit(paths, **kwargs):
    return canary_audit.audit_canary(
        paths["root"],
        protocol_path=paths["protocol"],
        askme_source=paths["source"],
        api_key=API_KEY,
        expected_served_models=[DATED_MODEL],
        expected_run_revision="run-sha",
        **kwargs,
    )


def _codes(result):
    return {violation["code"] for violation in result["violations"]}


def _require_endpoint_catalog_preflight(paths, *, write_record=True):
    provenance_path = paths["root"] / "askme-canary.json"
    provenance = json.loads(provenance_path.read_text())
    provenance["endpoint_catalog_preflight"] = {
        "required": True,
        "relative_path": canary_audit.ENDPOINT_CATALOG_PREFLIGHT_NAME,
        "timing": "immediately_before_inference_runner",
        "outcome_bearing_model_call": False,
    }
    _json(provenance_path, provenance)

    preflight_path = paths["root"] / canary_audit.ENDPOINT_CATALOG_PREFLIGHT_NAME
    if write_record:
        _json(
            preflight_path,
            {
                "schema_version": 1,
                "kind": "openrouter_endpoint_catalog_preflight",
                "requested_model": MODEL,
                "expected_provider": "siliconflow",
                "expected_served_models": [DATED_MODEL],
                "outcome_bearing_model_call": False,
                "response_sha256": "a" * 64,
                "valid": True,
                "validation_errors": [],
                "matches": [
                    {
                        "endpoint_name": f"SiliconFlow | {DATED_MODEL}",
                        "model_id": MODEL,
                        "provider_name": "SiliconFlow",
                        "served_model": DATED_MODEL,
                    }
                ],
            },
        )
    return preflight_path


def _make_inner_timeout(paths, *, in_flight=False, reason="launcher received signal 15"):
    _json(
        paths["attempt"] / "askme-result.json",
        {
            "status": "adapter_interrupted",
            "state": {"errors": [reason]},
            "log": [],
            "adapter_terminal_reason": reason,
        },
    )
    prediction_path = paths["root"] / "output.jsonl"
    prediction = json.loads(prediction_path.read_text())
    prediction["success"] = False
    prediction["error"] = "Agent did not complete successfully"
    _jsonl(prediction_path, [prediction])

    run_path = paths["attempt"] / "askme-run.jsonl"
    run_events = [
        event
        for event in (json.loads(line) for line in run_path.read_text().splitlines())
        if event["event"] != "run_end"
        and not (in_flight and event["event"] == "step")
    ]
    _jsonl(run_path, run_events)

    policy_path = paths["attempt"] / "askme-policy.jsonl"
    policy_events = [
        event
        for event in (
            json.loads(line) for line in policy_path.read_text().splitlines()
        )
        if not (in_flight and event["event"] == "action_result")
    ]
    policy_events.append({"event": "launcher_terminal", "reason": reason})
    _jsonl(policy_path, policy_events)


def test_valid_audit_records_route_integrity_and_action_coverage(tmp_path):
    paths = _fixture(tmp_path)

    result = _audit(paths)

    assert result["status"] == "valid"
    assert result["infrastructure_valid"] is True
    assert result["agent_completion"] is True
    assert result["agent_status"] == "complete"
    assert result["prediction_success"] is True
    assert result["route"]["served_models"] == [DATED_MODEL]
    assert result["route"]["served_providers"] == ["SiliconFlow"]
    assert result["counts"] == {
        "predictions": 1,
        "token_events": 1,
        "run_actions": 1,
        "policy_action_decisions": 1,
        "policy_denials": 0,
        "policy_in_flight_actions": 0,
        "api_key_leaks": 0,
    }
    assert result["integrity"]["prompt_sha256"]
    assert result["integrity"]["askme_sha256"]
    assert result["violations"] == []
    assert result["policy_compliant"] is True
    assert result["qualification_valid"] is True


def test_historical_provenance_without_catalog_preflight_remains_valid(tmp_path):
    paths = _fixture(tmp_path)
    provenance = json.loads((paths["root"] / "askme-canary.json").read_text())

    assert "endpoint_catalog_preflight" not in provenance
    assert _audit(paths)["infrastructure_valid"] is True


def test_required_endpoint_catalog_preflight_passes_post_run_audit(tmp_path):
    paths = _fixture(tmp_path)
    preflight_path = _require_endpoint_catalog_preflight(paths)

    result = _audit(paths)

    assert result["infrastructure_valid"] is True
    assert result["artifacts"]["endpoint_catalog_preflight"] == preflight_path.name


def test_required_endpoint_catalog_preflight_must_exist(tmp_path):
    paths = _fixture(tmp_path)
    _require_endpoint_catalog_preflight(paths, write_record=False)

    result = _audit(paths)

    assert result["infrastructure_valid"] is False
    assert "endpoint_catalog_preflight_missing" in _codes(result)


def test_present_endpoint_catalog_preflight_cannot_disable_requirement(tmp_path):
    paths = _fixture(tmp_path)
    _require_endpoint_catalog_preflight(paths)
    provenance_path = paths["root"] / "askme-canary.json"
    provenance = json.loads(provenance_path.read_text())
    provenance["endpoint_catalog_preflight"]["required"] = False
    _json(provenance_path, provenance)

    result = _audit(paths)

    assert result["infrastructure_valid"] is False
    assert "endpoint_catalog_preflight_required" in _codes(result)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("invalid", "endpoint_catalog_preflight_valid"),
        ("outcome_bearing", "endpoint_catalog_preflight_outcome_bearing"),
        ("wrong_model", "endpoint_catalog_preflight_model"),
        ("wrong_provider", "endpoint_catalog_preflight_provider"),
        ("wrong_served_models", "endpoint_catalog_preflight_served_models"),
        ("bad_response_sha", "endpoint_catalog_preflight_response_sha256"),
        ("missing_match", "endpoint_catalog_preflight_matches"),
        ("duplicate_match", "endpoint_catalog_preflight_matches"),
        ("wrong_match_provider", "endpoint_catalog_preflight_matches"),
    ],
)
def test_required_endpoint_catalog_preflight_rejects_tampering(
    tmp_path, mutation, code
):
    paths = _fixture(tmp_path)
    preflight_path = _require_endpoint_catalog_preflight(paths)
    record = json.loads(preflight_path.read_text())
    if mutation == "invalid":
        record["valid"] = False
    elif mutation == "outcome_bearing":
        record["outcome_bearing_model_call"] = True
    elif mutation == "wrong_model":
        record["requested_model"] = "other/model"
    elif mutation == "wrong_provider":
        record["expected_provider"] = "other"
    elif mutation == "wrong_served_models":
        record["expected_served_models"] = [MODEL]
    elif mutation == "bad_response_sha":
        record["response_sha256"] = "short"
    elif mutation == "missing_match":
        record["matches"] = []
    elif mutation == "duplicate_match":
        record["matches"].append(dict(record["matches"][0]))
    else:
        record["matches"][0]["provider_name"] = "Other"
    _json(preflight_path, record)

    result = _audit(paths)

    assert result["infrastructure_valid"] is False
    assert code in _codes(result)


def test_incomplete_agent_is_not_an_infrastructure_failure(tmp_path):
    paths = _fixture(tmp_path, status="exhausted")

    result = _audit(paths)

    assert result["infrastructure_valid"] is True
    assert result["agent_completion"] is False
    assert result["agent_status"] == "exhausted"
    assert result["prediction_success"] is False


def test_planned_inner_timeout_is_a_valid_retained_system_outcome(tmp_path):
    paths = _fixture(tmp_path)
    _make_inner_timeout(paths)

    result = _audit(paths)

    assert result["infrastructure_valid"] is True
    assert result["timed_out"] is True
    assert result["agent_completion"] is False
    assert result["agent_status"] == "adapter_interrupted"
    assert result["prediction_success"] is False
    assert result["policy_compliant"] is True
    assert result["qualification_valid"] is True
    assert result["policy_in_flight_action"] is None


def test_planned_timeout_allows_one_trailing_in_flight_action(tmp_path):
    paths = _fixture(tmp_path)
    _make_inner_timeout(paths, in_flight=True)

    result = _audit(paths)

    assert result["infrastructure_valid"] is True
    assert result["timed_out"] is True
    assert result["agent_completion"] is False
    assert result["counts"]["run_actions"] == 0
    assert result["counts"]["policy_action_decisions"] == 1
    assert result["counts"]["policy_in_flight_actions"] == 1
    assert result["policy_in_flight_action"] == {"sequence": 1, "action": "read"}


def test_non_signal_adapter_interruption_remains_invalid(tmp_path):
    paths = _fixture(tmp_path)
    _make_inner_timeout(paths, reason="launcher error: RuntimeError")

    result = _audit(paths)

    assert result["infrastructure_valid"] is False
    assert result["timed_out"] is False
    assert result["agent_completion"] is False
    assert "terminal_interruption_invalid" in _codes(result)


def test_timeout_still_requires_a_usage_bearing_response(tmp_path):
    paths = _fixture(tmp_path)
    _make_inner_timeout(paths)
    run_path = paths["attempt"] / "askme-run.jsonl"
    events = [
        json.loads(line)
        for line in run_path.read_text().splitlines()
        if json.loads(line)["event"] != "tokens"
    ]
    _jsonl(run_path, events)

    result = _audit(paths)

    assert result["timed_out"] is True
    assert result["infrastructure_valid"] is False
    assert "token_events_missing" in _codes(result)


def test_timeout_rejects_more_than_one_trailing_in_flight_action(tmp_path):
    paths = _fixture(tmp_path)
    _make_inner_timeout(paths, in_flight=True)
    policy_path = paths["attempt"] / "askme-policy.jsonl"
    events = [json.loads(line) for line in policy_path.read_text().splitlines()]
    events.insert(
        -1,
        {
            "event": "action_decision",
            "sequence": 2,
            "action": "shell",
            "arg": "pytest -q",
            "decision": "allow",
            "reason": None,
        },
    )
    _jsonl(policy_path, events)

    result = _audit(paths)

    assert result["timed_out"] is True
    assert result["infrastructure_valid"] is False
    assert "policy_action_count" in _codes(result)


def test_deterministic_completion_status_counts_as_agent_completion(tmp_path):
    paths = _fixture(tmp_path, status="complete_deterministic_after_exhausted")
    prediction_path = paths["root"] / "output.jsonl"
    prediction = json.loads(prediction_path.read_text())
    prediction["success"] = True
    prediction["error"] = None
    _jsonl(prediction_path, [prediction])

    result = _audit(paths)

    assert result["infrastructure_valid"] is True
    assert result["agent_completion"] is True


def test_prediction_completion_flag_must_serialize_agent_status_faithfully(tmp_path):
    paths = _fixture(tmp_path, status="exhausted")
    prediction_path = paths["root"] / "output.jsonl"
    prediction = json.loads(prediction_path.read_text())
    prediction["success"] = True
    _jsonl(prediction_path, [prediction])

    result = _audit(paths)

    assert result["agent_completion"] is False
    assert result["infrastructure_valid"] is False
    assert "prediction_completion_mismatch" in _codes(result)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("instance_id", "other.task", "prediction_instance_id"),
        ("n_attempt", 2, "prediction_n_attempt"),
        ("agent", "other", "prediction_agent"),
        ("model", "other/model", "prediction_model"),
    ],
)
def test_prediction_identity_is_exact(tmp_path, field, value, code):
    paths = _fixture(tmp_path)
    prediction_path = paths["root"] / "output.jsonl"
    prediction = json.loads(prediction_path.read_text())
    prediction[field] = value
    _jsonl(prediction_path, [prediction])

    result = _audit(paths)

    assert result["infrastructure_valid"] is False
    assert code in _codes(result)


def test_exactly_one_prediction_is_required(tmp_path):
    paths = _fixture(tmp_path)
    prediction_path = paths["root"] / "output.jsonl"
    prediction = json.loads(prediction_path.read_text())
    _jsonl(prediction_path, [prediction, prediction])

    result = _audit(paths)

    assert "prediction_count" in _codes(result)


def test_prompt_must_match_prediction_run_start_and_manifest_lengths(tmp_path):
    paths = _fixture(tmp_path)
    (paths["attempt"] / "askme-prompt.txt").write_text("changed prompt\n")

    result = _audit(paths)

    codes = _codes(result)
    assert "prediction_prompt" in codes
    assert "run_start_prompt" in codes
    assert "manifest_prompt_chars" in codes
    assert "manifest_prompt_hash" in codes
    assert "protocol_prompt_chars" in codes
    assert "protocol_prompt_hash" in codes


def test_source_hash_must_match_manifest_provenance_and_launcher(tmp_path):
    paths = _fixture(tmp_path)
    paths["source"].write_text("print('changed')\n", encoding="utf-8")

    result = _audit(paths)

    codes = _codes(result)
    assert "manifest_source_hash" in codes
    assert "provenance_source_hash" in codes
    assert "policy_launcher_askme_sha256" in codes
    assert "protocol_source_hash" in codes


def test_preregistered_canary_code_file_hashes_are_verified(tmp_path):
    paths = _fixture(tmp_path)
    adapter_path = tmp_path / "tests/featurebench/askme_adapter.py"
    adapter_path.write_text("# changed after registration\n", encoding="utf-8")

    result = _audit(paths)

    assert result["infrastructure_valid"] is False
    assert "code_file_hash" in _codes(result)


def test_provenance_must_repeat_preregistered_code_file_hashes(tmp_path):
    paths = _fixture(tmp_path)
    provenance_path = paths["root"] / "askme-canary.json"
    provenance = json.loads(provenance_path.read_text())
    provenance["code_files"]["tests/featurebench/askme_adapter.py"] = "0" * 64
    _json(provenance_path, provenance)

    result = _audit(paths)

    assert "provenance_code_files" in _codes(result)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("model", "other/model", "run_start_model"),
        ("provider", "auto", "run_start_provider"),
        ("allow_provider_fallbacks", True, "run_start_allow_provider_fallbacks"),
        ("require_provider_parameters", False, "run_start_require_provider_parameters"),
        ("reasoning_policy", "off", "run_start_reasoning_policy"),
        ("limits", {"max_replans": 3}, "run_start_limits"),
    ],
)
def test_run_start_must_match_every_frozen_control(tmp_path, field, value, code):
    paths = _fixture(tmp_path)
    run_path = paths["attempt"] / "askme-run.jsonl"
    events = [json.loads(line) for line in run_path.read_text().splitlines()]
    events[0][field] = value
    _jsonl(run_path, events)

    result = _audit(paths)

    assert code in _codes(result)


def test_exactly_one_run_start_is_required(tmp_path):
    paths = _fixture(tmp_path)
    run_path = paths["attempt"] / "askme-run.jsonl"
    events = [json.loads(line) for line in run_path.read_text().splitlines()]
    events.insert(1, dict(events[0]))
    _jsonl(run_path, events)

    result = _audit(paths)

    assert "run_start_count" in _codes(result)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("remove_tokens", "token_events_missing"),
        ("wrong_model", "token_served_model"),
        ("wrong_provider", "token_served_provider"),
        ("wrong_reasoning_policy", "reasoning_policy"),
    ],
)
def test_usage_route_and_reasoning_policy_are_audited(tmp_path, mutation, code):
    paths = _fixture(tmp_path)
    run_path = paths["attempt"] / "askme-run.jsonl"
    events = [json.loads(line) for line in run_path.read_text().splitlines()]
    if mutation == "remove_tokens":
        events = [event for event in events if event["event"] != "tokens"]
    elif mutation == "wrong_model":
        next(event for event in events if event["event"] == "tokens")["model"] = "other/model"
    elif mutation == "wrong_provider":
        next(event for event in events if event["event"] == "tokens")["provider"] = "Other"
    else:
        next(event for event in events if event["event"] == "reasoning_decision")[
            "requested_policy"
        ] = "off"
    _jsonl(run_path, events)

    result = _audit(paths)

    assert code in _codes(result)


def test_every_token_event_must_use_the_approved_route(tmp_path):
    paths = _fixture(tmp_path)
    run_path = paths["attempt"] / "askme-run.jsonl"
    events = [json.loads(line) for line in run_path.read_text().splitlines()]
    token = dict(next(event for event in events if event["event"] == "tokens"))
    token["model"] = "unexpected/second-response"
    events.insert(-2, token)
    _jsonl(run_path, events)

    result = _audit(paths)

    assert result["counts"]["token_events"] == 2
    assert "token_served_model" in _codes(result)


def test_requested_alias_is_not_an_allowed_served_model(tmp_path):
    paths = _fixture(tmp_path, served_model=MODEL)

    result = canary_audit.audit_canary(
        paths["root"],
        protocol_path=paths["protocol"],
        askme_source=paths["source"],
        api_key=API_KEY,
    )

    assert result["infrastructure_valid"] is False
    assert result["route"]["allowed_served_models"] == [DATED_MODEL]
    assert "token_served_model" in _codes(result)


def test_api_served_model_list_cannot_expand_preregistered_route(tmp_path):
    paths = _fixture(tmp_path)

    result = canary_audit.audit_canary(
        paths["root"],
        protocol_path=paths["protocol"],
        askme_source=paths["source"],
        api_key=API_KEY,
        expected_served_models=[DATED_MODEL, "post-hoc/model"],
        expected_run_revision="run-sha",
    )

    assert result["infrastructure_valid"] is False
    assert "expected_served_models_mismatch" in _codes(result)


def test_expected_run_revision_is_external_to_content_addressed_protocol(tmp_path):
    paths = _fixture(tmp_path)

    result = canary_audit.audit_canary(
        paths["root"],
        protocol_path=paths["protocol"],
        askme_source=paths["source"],
        api_key=API_KEY,
        expected_served_models=[DATED_MODEL],
        expected_run_revision="different-run-sha",
    )

    codes = _codes(result)
    assert "provenance_askme_repository_revision" in codes
    assert "provenance_expected_askme_repository_revision" in codes


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("missing", "policy_log_missing"),
        ("no_launcher", "policy_launcher_count"),
        ("missing_launcher_hash", "policy_launcher_askme_sha256"),
        ("missing_action", "policy_action_count"),
        ("missing_result", "policy_result_count"),
        ("wrong_action", "policy_action_mismatch"),
        ("wrong_arg", "policy_arg_mismatch"),
    ],
)
def test_policy_log_must_correlate_every_executed_action(tmp_path, mutation, code):
    paths = _fixture(tmp_path)
    policy_path = paths["attempt"] / "askme-policy.jsonl"
    events = [json.loads(line) for line in policy_path.read_text().splitlines()]
    if mutation == "missing":
        policy_path.unlink()
    elif mutation == "no_launcher":
        _jsonl(policy_path, events[1:])
    elif mutation == "missing_launcher_hash":
        del events[0]["askme_sha256"]
        _jsonl(policy_path, events)
    elif mutation == "missing_action":
        _jsonl(policy_path, events[:1])
    elif mutation == "missing_result":
        _jsonl(policy_path, events[:2])
    elif mutation == "wrong_action":
        events[1]["action"] = "shell"
        _jsonl(policy_path, events)
    else:
        events[1]["arg"] = "other.py"
        _jsonl(policy_path, events)

    result = _audit(paths)

    assert code in _codes(result)


def test_policy_denial_is_observed_behavior_not_infrastructure_failure(tmp_path):
    paths = _fixture(tmp_path)
    run_path = paths["attempt"] / "askme-run.jsonl"
    run_events = [json.loads(line) for line in run_path.read_text().splitlines()]
    step = next(event for event in run_events if event["event"] == "step")
    step["ok"] = False
    step["error_type"] = "policy_violation"
    _jsonl(run_path, run_events)
    policy_path = paths["attempt"] / "askme-policy.jsonl"
    events = [json.loads(line) for line in policy_path.read_text().splitlines()]
    decision = next(event for event in events if event["event"] == "action_decision")
    decision["decision"] = "deny"
    decision["reason"] = "path_outside_workspace"
    action_result = next(event for event in events if event["event"] == "action_result")
    action_result.update(
        {"decision": "deny", "ok": False, "error_type": "policy_violation"}
    )
    _jsonl(policy_path, events)

    result = _audit(paths)

    assert result["infrastructure_valid"] is True
    assert result["agent_completion"] is True
    assert result["policy_compliant"] is False
    assert result["qualification_valid"] is False
    assert result["counts"]["policy_denials"] == 1
    assert result["policy_denials"] == [
        {
            "sequence": 1,
            "action": "read",
            "reason": "path_outside_workspace",
        }
    ]


def test_policy_full_argument_matches_bounded_run_log_prefix(tmp_path):
    paths = _fixture(tmp_path)
    full_arg = "python -c " + "x" * 180
    run_path = paths["attempt"] / "askme-run.jsonl"
    run_events = [json.loads(line) for line in run_path.read_text().splitlines()]
    next(event for event in run_events if event["event"] == "step")["arg"] = full_arg[:120]
    _jsonl(run_path, run_events)
    policy_path = paths["attempt"] / "askme-policy.jsonl"
    policy_events = [json.loads(line) for line in policy_path.read_text().splitlines()]
    next(event for event in policy_events if event["event"] == "action_decision")[
        "arg"
    ] = full_arg
    _jsonl(policy_path, policy_events)

    result = _audit(paths)

    assert result["infrastructure_valid"] is True


def test_exact_api_key_bytes_are_detected_without_disclosure(tmp_path):
    paths = _fixture(tmp_path)
    (paths["attempt"] / "infer.log").write_bytes(b"prefix:" + API_KEY.encode() + b":suffix")

    result = _audit(paths)
    rendered = json.dumps(result)

    assert result["infrastructure_valid"] is False
    assert result["counts"]["api_key_leaks"] == 1
    assert "api_key_leak" in _codes(result)
    assert API_KEY not in rendered


def test_missing_api_key_makes_leak_audit_invalid(tmp_path):
    paths = _fixture(tmp_path)

    result = canary_audit.audit_canary(
        paths["root"],
        protocol_path=paths["protocol"],
        askme_source=paths["source"],
        api_key="",
        expected_served_models=[DATED_MODEL],
    )

    assert result["infrastructure_valid"] is False
    assert "api_key_missing" in _codes(result)


def test_secret_scan_detects_value_split_across_streaming_chunks(tmp_path):
    paths = _fixture(tmp_path)
    leak = paths["attempt"] / "large.log"
    leak.write_bytes(b"x" * (1024 * 1024 - 4) + API_KEY.encode())

    result = _audit(paths)

    assert result["counts"]["api_key_leaks"] == 1


def test_invalid_audit_can_raise_structured_custom_error(tmp_path):
    paths = _fixture(tmp_path)
    (paths["attempt"] / "askme-policy.jsonl").unlink()

    with pytest.raises(canary_audit.CanaryAuditError) as raised:
        _audit(paths, raise_on_invalid=True)

    assert raised.value.audit["infrastructure_valid"] is False
    assert "policy_log_missing" in _codes(raised.value.audit)
    assert API_KEY not in str(raised.value)


def test_malformed_jsonl_is_reported_as_invalid_not_raised(tmp_path):
    paths = _fixture(tmp_path)
    (paths["attempt"] / "askme-run.jsonl").write_text("not-json\n", encoding="utf-8")

    result = _audit(paths)

    assert result["status"] == "invalid"
    assert "run_log_invalid_json" in _codes(result)


def test_cli_requires_run_revision_and_writes_structured_output(
    tmp_path, monkeypatch, capsys
):
    paths = _fixture(tmp_path)
    output = tmp_path / "reports" / "audit.json"
    monkeypatch.setenv("OPENROUTER_API_KEY", API_KEY)

    exit_code = canary_audit.main(
        [
            str(paths["root"]),
            "--protocol",
            str(paths["protocol"]),
            "--askme-source",
            str(paths["source"]),
            "--code-root",
            str(tmp_path),
            "--expected-served-model",
            DATED_MODEL,
            "--expected-run-revision",
            "run-sha",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    saved = json.loads(output.read_text())
    printed = json.loads(capsys.readouterr().out)
    assert saved == printed
    assert saved["infrastructure_valid"] is True
    assert API_KEY not in output.read_text()

    with pytest.raises(SystemExit):
        canary_audit.parse_args([str(paths["root"])])
