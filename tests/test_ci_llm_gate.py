"""Unit tests for tests/ci_llm_gate.py (offline; no LLM or network needed)."""

import json
from pathlib import Path

import ci_llm_gate
import pytest

# --- fixtures ---


def _summary(
    model="google/gemma-4-26b-a4b-it",
    suite="hard",
    test="test_replan_build_with_dependency",
    total=1,
    pytest_passed=None,
    agent_complete=None,
    wall=(66.5,),
    cost=(0.00066688,),
    provider="",
    served=("SiliconFlow",),
    capability_profile=ci_llm_gate.BERKELEY_CAPABILITY_PROFILE,
    expected_served_model=None,
):
    """Build a dict shaped like a bench_harness summary.json."""
    pytest_passed = total if pytest_passed is None else pytest_passed
    agent_complete = total if agent_complete is None else agent_complete
    expected_served_model = expected_served_model or {
        "google/gemma-4-26b-a4b-it": "google/gemma-4-26b-a4b-it-20260403",
        "qwen/qwen3.6-27b": "qwen/qwen3.6-27b-20260422",
    }.get(model, model)
    statuses = ["complete"] * agent_complete + ["incomplete"] * (total - agent_complete)
    return {
        "suite": suite,
        "backend": "openrouter",
        "trials": total,
        "model": model,
        "reasoning_effort": "",
        "reasoning_policy": "gated",
        "capability_profile": capability_profile,
        "expected_served_model": expected_served_model,
        "provider": provider,
        "allow_provider_fallbacks": False if provider else None,
        "require_provider_parameters": True if provider else None,
        "git_commit": "0" * 40,
        "git_dirty": False,
        "total_wall_s": sum(wall),
        "tests": {
            test: {
                "passed": pytest_passed,
                "pytest_passed": pytest_passed,
                "agent_complete": agent_complete,
                "total": total,
                "valid_trials": total,
                "valid_passes": pytest_passed,
                "agent_status": statuses,
                "wall_s": list(wall),
                "replans": [0] * total,
                "local_replans": [0] * total,
                "local_replans_ok": [0] * total,
                "steps": [4] * total,
                "thinking_retries": [0] * total,
                "llm_calls": [8] * total,
                "prompt_tokens": [3976] * total,
                "completion_tokens": [375] * total,
                "total_tokens": [4351] * total,
                "openrouter_cost": list(cost),
                "usage_complete": [True] * total,
                "served_models": [[expected_served_model]] * total,
                "served_model_sources": [["response"]] * total,
                "served_model_provenance_valid": [True] * total,
                "served_providers": [list(served)] * total,
                "served_provider_observed": [True] * total,
                "recorded_backends": ["openrouter"] * total,
                "requested_models": [model] * total,
                "token_requested_models": [[model]] * total,
                "token_requested_model_valid": [True] * total,
                "requested_providers": [provider] * total,
                "recorded_reasoning_efforts": [""] * total,
                "recorded_reasoning_policies": ["gated"] * total,
                "recorded_allow_provider_fallbacks": [False] * total,
                "recorded_require_provider_parameters": [True] * total,
                "capability_profiles": [capability_profile] * total,
                "config_hashes": ["0123456789abcdef"] * total,
                "route_valid": [True] * total,
                "contract_valid": [True] * total,
            }
        },
    }


def _write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(json.dumps(payload) if isinstance(payload, dict) else payload, encoding="utf-8")
    return str(path)


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


# --- preflight ---


class TestPreflight:
    def test_missing_key_fails_with_actionable_message(self):
        ok, message = ci_llm_gate.check_openrouter_key(env={}, get=lambda *a, **k: _Resp(200))
        assert not ok
        assert "OPENROUTER_API_KEY" in message
        assert "Openrouter" in message  # points at the deployment environment

    def test_blank_key_is_treated_as_missing(self):
        ok, _ = ci_llm_gate.check_openrouter_key(
            env={"OPENROUTER_API_KEY": "   "}, get=lambda *a, **k: _Resp(200)
        )
        assert not ok

    def test_valid_key_passes_and_is_never_echoed(self):
        calls = {}

        def fake_get(url, headers=None, timeout=None):
            calls["url"] = url
            calls["auth"] = headers["Authorization"]
            return _Resp(200)

        ok, message = ci_llm_gate.check_openrouter_key(
            env={"OPENROUTER_API_KEY": "sk-or-v1-test"}, get=fake_get
        )
        assert ok
        assert calls["url"] == ci_llm_gate.OPENROUTER_MODELS_URL
        assert calls["auth"] == "Bearer sk-or-v1-test"
        assert "sk-or-v1-test" not in message

    def test_rejected_key_fails_with_status(self):
        ok, message = ci_llm_gate.check_openrouter_key(
            env={"OPENROUTER_API_KEY": "sk-bad"}, get=lambda *a, **k: _Resp(401)
        )
        assert not ok
        assert "401" in message
        assert "sk-bad" not in message

    def test_network_error_fails_instead_of_skipping(self):
        def broken_get(*args, **kwargs):
            raise OSError("connection reset")

        ok, message = ci_llm_gate.check_openrouter_key(
            env={"OPENROUTER_API_KEY": "sk-x"}, get=broken_get
        )
        assert not ok
        assert "connection reset" in message

    def test_main_preflight_fails_without_key(self, monkeypatch, capsys):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        rc = ci_llm_gate.main(["preflight"])
        assert rc == 1
        assert "PREFLIGHT FAILED" in capsys.readouterr().out


# --- report gate ---


class TestReportGate:
    def test_all_cells_pass(self, tmp_path, capsys):
        paths = [
            _write(tmp_path, "build.json", _summary()),
            _write(
                tmp_path,
                "repair.json",
                _summary(suite="medium", test="test_fix_python_syntax_error"),
            ),
        ]
        rc = ci_llm_gate.main(["report"] + paths + ["--expect-cells", "2"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "LLM GATE: PASS" in out
        assert "hard/test_replan_build_with_dependency" in out
        assert "medium/test_fix_python_syntax_error" in out

    def test_pytest_failure_fails_gate(self, tmp_path, capsys):
        path = _write(tmp_path, "s.json", _summary(pytest_passed=0))
        rc = ci_llm_gate.main(["report", path])
        out = capsys.readouterr().out
        assert rc == 1
        assert "LLM GATE: FAIL" in out
        assert "pytest 0/1" in out

    def test_valid_cell_failure_can_be_advisory(self, tmp_path, capsys):
        path = _write(tmp_path, "s.json", _summary(pytest_passed=0))
        rc = ci_llm_gate.main(["report", path, "--advisory-cell-failures"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "LLM GATE: ADVISORY CELL FAILURE" in out
        assert "pytest 0/1" in out

    def test_agent_incomplete_fails_gate_even_when_pytest_passes(self, tmp_path):
        path = _write(tmp_path, "s.json", _summary(agent_complete=0))
        rc = ci_llm_gate.main(["report", path])
        assert rc == 1

    def test_partial_multi_trial_pass_fails_gate(self, tmp_path):
        path = _write(
            tmp_path,
            "s.json",
            _summary(
                total=2, pytest_passed=1, agent_complete=2, wall=(66.5, 70.1), cost=(0.0006, 0.0007)
            ),
        )
        rc = ci_llm_gate.main(["report", path])
        assert rc == 1

    def test_missing_cells_fail_gate(self, tmp_path, capsys):
        path = _write(tmp_path, "s.json", _summary())
        rc = ci_llm_gate.main(["report", path, "--expect-cells", "2"])
        assert rc == 1
        assert "expected 2 result cell(s), found 1" in capsys.readouterr().out

    def test_advisory_mode_keeps_missing_cells_blocking(self, tmp_path, capsys):
        path = _write(tmp_path, "s.json", _summary(pytest_passed=0))
        rc = ci_llm_gate.main(["report", path, "--expect-cells", "2", "--advisory-cell-failures"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "LLM GATE: FAIL (evidence integrity)" in out
        assert "expected 2 result cell(s), found 1" in out

    def test_unreadable_summary_fails_gate(self, tmp_path):
        rc = ci_llm_gate.main(["report", str(tmp_path / "nope.json")])
        assert rc == 1

    def test_advisory_mode_keeps_unreadable_summary_blocking(self, tmp_path, capsys):
        rc = ci_llm_gate.main(["report", str(tmp_path / "nope.json"), "--advisory-cell-failures"])
        assert rc == 1
        assert "LLM GATE: FAIL (evidence integrity)" in capsys.readouterr().out

    def test_malformed_summary_fails_gate(self, tmp_path):
        path = _write(tmp_path, "bad.json", "{not json")
        rc = ci_llm_gate.main(["report", path])
        assert rc == 1

    def test_advisory_mode_keeps_malformed_summary_blocking(self, tmp_path, capsys):
        path = _write(tmp_path, "bad.json", "{not json")
        rc = ci_llm_gate.main(["report", path, "--advisory-cell-failures"])
        assert rc == 1
        assert "LLM GATE: FAIL (evidence integrity)" in capsys.readouterr().out

    def test_non_object_summary_is_an_integrity_failure(self, tmp_path, capsys):
        path = _write(tmp_path, "list.json", json.dumps([_summary()]))
        rc = ci_llm_gate.main(["report", path, "--advisory-cell-failures"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "LLM GATE: FAIL (evidence integrity)" in out
        assert "summary root is not an object" in out

    def test_summary_without_tests_fails_gate(self, tmp_path):
        path = _write(tmp_path, "empty.json", {"suite": "hard", "tests": {}})
        rc = ci_llm_gate.main(["report", path])
        assert rc == 1

    @pytest.mark.parametrize(
        "field", ["model", "capability_profile", "expected_served_model", "reasoning_policy"]
    )
    def test_summary_contract_fields_are_required_in_advisory_mode(self, tmp_path, capsys, field):
        summary = _summary(pytest_passed=0)
        summary.pop(field)
        path = _write(tmp_path, "missing-{}.json".format(field), summary)
        rc = ci_llm_gate.main(["report", path, "--advisory-cell-failures"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "LLM GATE: FAIL (evidence integrity)" in out
        assert "summary {} is not a non-empty string".format(field) in out

    def test_berkeley_profile_must_be_the_generic_profile(self, tmp_path, capsys):
        summary = _summary(capability_profile="legacy-e4b-m1-v1", pytest_passed=0)
        path = _write(tmp_path, "wrong-profile.json", summary)
        rc = ci_llm_gate.main(["report", path, "--advisory-cell-failures"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "LLM GATE: FAIL (evidence integrity)" in out
        assert "expected 'generic-feature-scale-v1'" in out

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("git_commit", "not-a-sha", "not a 40-character hexadecimal SHA"),
            ("git_dirty", True, "git_dirty is not false"),
            ("git_dirty", None, "git_dirty is not false"),
        ],
    )
    def test_source_provenance_is_required(self, tmp_path, capsys, field, value, message):
        summary = _summary()
        summary[field] = value
        path = _write(tmp_path, "bad-source.json", summary)

        rc = ci_llm_gate.main(["report", path, "--advisory-cell-failures"])
        out = capsys.readouterr().out

        assert rc == 1
        assert "LLM GATE: FAIL (evidence integrity)" in out
        assert message in out

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("valid_trials", 1, "valid_trials does not equal total"),
            ("valid_passes", 1, "valid_passes disagrees with pytest_passed"),
            ("route_valid", [True], "route_valid length does not match total"),
            ("route_valid", [True, False], "route_valid contains an invalid trial"),
            ("contract_valid", [True], "contract_valid length does not match total"),
            (
                "contract_valid",
                [True, False],
                "contract_valid contains an invalid trial",
            ),
            ("usage_complete", [True], "usage_complete length does not match total"),
            (
                "usage_complete",
                [True, False],
                "usage_complete contains an incomplete trial",
            ),
            ("config_hashes", ["hash"], "config_hashes length does not match total"),
            (
                "config_hashes",
                ["hash", ""],
                "config_hashes must contain only 16-character hex digests",
            ),
            (
                "config_hashes",
                ["not-a-hash", "not-a-hash"],
                "config_hashes must contain only 16-character hex digests",
            ),
            (
                "config_hashes",
                ["a" * 16, "b" * 16],
                "config_hashes differ within one result cell",
            ),
            (
                "requested_models",
                ["google/gemma-4-26b-a4b-it"],
                "requested_models length does not match total",
            ),
            (
                "requested_models",
                ["google/gemma-4-26b-a4b-it", "wrong/model"],
                "requested_models do not exactly match summary model",
            ),
            (
                "recorded_backends",
                ["openrouter"],
                "recorded_backends length does not match total",
            ),
            (
                "recorded_backends",
                ["openrouter", "local"],
                "recorded_backends do not exactly match summary backend",
            ),
            (
                "requested_providers",
                [""],
                "requested_providers length does not match total",
            ),
            (
                "requested_providers",
                ["", "other"],
                "requested_providers do not exactly match summary provider",
            ),
            (
                "recorded_reasoning_efforts",
                ["", "low"],
                "recorded_reasoning_efforts do not exactly match summary reasoning_effort",
            ),
            (
                "recorded_reasoning_policies",
                ["gated", "off"],
                "recorded_reasoning_policies do not exactly match summary reasoning_policy",
            ),
            (
                "capability_profiles",
                [ci_llm_gate.BERKELEY_CAPABILITY_PROFILE],
                "capability_profiles length does not match total",
            ),
            (
                "capability_profiles",
                [ci_llm_gate.BERKELEY_CAPABILITY_PROFILE, "legacy-e4b-m1-v1"],
                "capability_profiles do not exactly match summary capability_profile",
            ),
            (
                "served_models",
                [
                    ["google/gemma-4-26b-a4b-it-20260403"],
                    ["wrong/served-model"],
                ],
                "served_models do not exactly match summary expected_served_model",
            ),
            (
                "token_requested_models",
                [["google/gemma-4-26b-a4b-it"], ["wrong/model"]],
                "token_requested_models do not exactly match summary model",
            ),
            (
                "token_requested_model_valid",
                [True, False],
                "token_requested_model_valid contains an invalid trial",
            ),
            (
                "served_model_sources",
                [["response"], ["unobserved"]],
                "served_model_sources contain a non-observed source",
            ),
            (
                "served_model_provenance_valid",
                [True, False],
                "served_model_provenance_valid contains an invalid trial",
            ),
        ],
    )
    def test_trial_contract_failures_stay_blocking_in_advisory_mode(
        self, tmp_path, capsys, field, value, message
    ):
        summary = _summary(
            total=2,
            pytest_passed=0,
            wall=(66.5, 70.1),
            cost=(0.0006, 0.0007),
        )
        result = summary["tests"]["test_replan_build_with_dependency"]
        result[field] = value
        path = _write(tmp_path, "invalid-{}.json".format(field), summary)
        rc = ci_llm_gate.main(["report", path, "--advisory-cell-failures"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "LLM GATE: FAIL (evidence integrity)" in out
        assert message in out

    def test_advisory_mode_keeps_invalid_counts_blocking(self, tmp_path, capsys):
        summary = _summary(total=0, wall=(), cost=())
        path = _write(tmp_path, "invalid-count.json", summary)
        rc = ci_llm_gate.main(["report", path, "--advisory-cell-failures"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "LLM GATE: FAIL (evidence integrity)" in out
        assert "total must be positive" in out

    def test_advisory_mode_keeps_conflicting_pass_aliases_blocking(self, tmp_path, capsys):
        summary = _summary()
        result = summary["tests"]["test_replan_build_with_dependency"]
        result["passed"] = 0
        path = _write(tmp_path, "conflicting-counts.json", summary)
        rc = ci_llm_gate.main(["report", path, "--advisory-cell-failures"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "LLM GATE: FAIL (evidence integrity)" in out
        assert "pytest_passed disagrees with passed" in out

    def test_advisory_mode_keeps_partial_jsonl_blocking(self, tmp_path, capsys):
        summary = _summary(pytest_passed=0, agent_complete=0)
        result = summary["tests"]["test_replan_build_with_dependency"]
        result["log_parse_errors"] = ["Expecting value at line 1 column 10"]
        path = _write(tmp_path, "partial-log.json", summary)
        rc = ci_llm_gate.main(["report", path, "--advisory-cell-failures"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "LLM GATE: FAIL (evidence integrity)" in out
        assert "benchmark JSONL contains a parse error" in out

    def test_advisory_mode_does_not_swallow_reporter_crash(self, tmp_path, monkeypatch):
        path = _write(tmp_path, "s.json", _summary(pytest_passed=0))

        def crash(*args, **kwargs):
            raise RuntimeError("reporter bug")

        monkeypatch.setattr(ci_llm_gate, "render_markdown", crash)
        with pytest.raises(RuntimeError, match="reporter bug"):
            ci_llm_gate.main(["report", path, "--advisory-cell-failures"])

    def test_markdown_out_appends(self, tmp_path):
        path = _write(tmp_path, "s.json", _summary())
        out_file = tmp_path / "step_summary.md"
        assert ci_llm_gate.main(["report", path, "--markdown-out", str(out_file)]) == 0
        assert ci_llm_gate.main(["report", path, "--markdown-out", str(out_file)]) == 0
        text = out_file.read_text(encoding="utf-8")
        assert text.count("## LLM gate") == 2  # append, not overwrite
        assert "| Cell | Model | Provider |" in text

    def test_report_shows_served_providers(self, tmp_path, capsys):
        path = _write(tmp_path, "s.json", _summary(provider="", served=("SiliconFlow",)))
        ci_llm_gate.main(["report", path])
        out = capsys.readouterr().out
        assert "auto → SiliconFlow" in out

    def test_effort_pinned_cells_stay_distinguishable(self, tmp_path, capsys):
        """Two gpt-oss-20b cells differing only by reasoning effort must show
        distinct model labels and both count toward --expect-cells."""
        low = _summary(model="openai/gpt-oss-20b")
        low["reasoning_effort"] = "low"
        low["tests"]["test_replan_build_with_dependency"]["recorded_reasoning_efforts"] = ["low"]
        high = _summary(model="openai/gpt-oss-20b", pytest_passed=0)
        high["reasoning_effort"] = "high"
        high["tests"]["test_replan_build_with_dependency"]["recorded_reasoning_efforts"] = ["high"]
        paths = [_write(tmp_path, "low.json", low), _write(tmp_path, "high.json", high)]
        rc = ci_llm_gate.main(["report"] + paths + ["--expect-cells", "2"])
        out = capsys.readouterr().out
        assert rc == 1  # the high cell fails the pass rule
        assert "openai/gpt-oss-20b@low" in out
        assert "openai/gpt-oss-20b@high" in out
        # the failure line carries the effort-qualified label
        assert "[openai/gpt-oss-20b@high]: pytest 0/1" in out


def test_workflow_pins_each_berkeley_cell_contract():
    workflow = (Path(__file__).parents[1] / ".github/workflows/llm.yml").read_text(encoding="utf-8")
    berkeley_run = workflow.split("- name: Run Berkeley protocol cells", 1)[1].split(
        "- name: Gate on protocol pass rule", 1
    )[0]
    matrix = (
        "google/gemma-4-26b-a4b-it=google/gemma-4-26b-a4b-it-20260403,"
        "qwen/qwen3.6-27b=qwen/qwen3.6-27b-20260422"
    )
    assert workflow.count(matrix) == 2
    assert "requested=expected-served model cells" in workflow
    assert workflow.count("--capability-profile generic-feature-scale-v1") == 2
    assert workflow.count("--reasoning-policy gated") == 2
    assert workflow.count('--expected-served-model "$EXPECTED_SERVED_MODEL"') == 2
    assert "set -u" in berkeley_run
    assert "set -e" not in berkeley_run
