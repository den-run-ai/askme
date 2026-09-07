"""Offline tests for benchmark-harness evidence retention."""

import json
import subprocess

import bench_harness
import pytest


def test_local_trial_pins_model_and_capability_profile(tmp_path, monkeypatch):
    captured = {}

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def run(*args, **kwargs):
        captured.update(kwargs)
        return Completed()

    monkeypatch.setattr(bench_harness.subprocess, "run", run)

    passed, _wall, _stdout, _stderr = bench_harness.run_single_test(
        "test_multi_step_build",
        "easy",
        "local",
        tmp_path / "trial.jsonl",
        model="gemma-4-12b-it-qat-q4_0.gguf",
        capability_profile="generic-feature-scale-v1",
    )

    assert passed is True
    assert captured["env"]["LLM_BACKEND"] == "local"
    assert captured["env"]["LLM_MODEL"] == "gemma-4-12b-it-qat-q4_0.gguf"
    assert captured["env"]["LLM_CAPABILITY_PROFILE"] == "generic-feature-scale-v1"


def test_failure_diagnostic_retains_bounded_stream_tails(tmp_path):
    prefix = "discard-me-" * 400
    stdout_tail = "stdout assertion: expected executable main"
    stderr_tail = "stderr traceback tail"

    name = bench_harness.write_failure_diagnostic(
        tmp_path,
        "test_build",
        2,
        prefix + stdout_tail,
        prefix + stderr_tail,
    )

    assert name == "test_build_trial2_pytest.txt"
    diagnostic = (tmp_path / name).read_text(encoding="utf-8")
    assert stdout_tail in diagnostic
    assert stderr_tail in diagnostic
    assert "earlier characters omitted" in diagnostic
    assert prefix not in diagnostic
    assert len(diagnostic) < 2 * bench_harness.PYTEST_DIAGNOSTIC_STREAM_CHARS + 500


def test_failure_diagnostic_marks_empty_streams(tmp_path):
    name = bench_harness.write_failure_diagnostic(tmp_path, "test_empty", 1, "", "")

    diagnostic = (tmp_path / name).read_text(encoding="utf-8")
    assert diagnostic.count("(empty)") == 2


def test_timeout_retains_partial_pytest_output_in_summary(tmp_path, monkeypatch):
    def time_out(*args, **kwargs):
        args[3].write_text('{"event":', encoding="utf-8")
        raise subprocess.TimeoutExpired(
            cmd=["pytest"],
            timeout=1200,
            output=b"partial stdout",
            stderr=b"partial stderr",
        )

    monkeypatch.setattr(bench_harness, "run_single_test", time_out)
    monkeypatch.setattr(bench_harness, "git_state", lambda: ("0" * 40, False))

    bench_harness.main(
        [
            "--suite",
            "hard",
            "--test",
            "test_timeout",
            "--trials",
            "1",
            "--expected-served-model",
            "timeout-model",
            "--log-dir",
            str(tmp_path),
        ]
    )

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    diagnostic_name = "test_timeout_trial1_pytest.txt"
    assert summary["tests"]["test_timeout"]["pytest_diagnostics"] == [diagnostic_name]
    assert summary["tests"]["test_timeout"]["timed_out"] == [True]
    assert "line 1 column 10" in summary["tests"]["test_timeout"]["log_parse_errors"][0]
    diagnostic = (tmp_path / diagnostic_name).read_text(encoding="utf-8")
    assert diagnostic.startswith("Pytest timeout diagnostics")
    assert "stdout:\npartial stdout" in diagnostic
    assert "stderr:\npartial stderr" in diagnostic
    assert "b'partial" not in diagnostic


@pytest.mark.parametrize(
    ("served_model", "pytest_passed", "expected_exit", "expected_valid", "valid_passes"),
    [
        ("different-served-model.gguf", True, 1, False, 0),
        ("expected-served-model.gguf", True, 0, True, 1),
        ("expected-served-model.gguf", False, 0, True, 0),
    ],
)
def test_expected_served_model_controls_trial_validity(
    tmp_path,
    monkeypatch,
    served_model,
    pytest_passed,
    expected_exit,
    expected_valid,
    valid_passes,
):
    def run_trial(*args, **kwargs):
        args[3].write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "event": "run_start",
                            "backend": "local",
                            "model": "requested-alias",
                            "provider": "",
                            "reasoning_effort": "",
                            "reasoning_policy": "gated",
                            "capability_profile": {"name": "generic-feature-scale-v1"},
                            "config_hash": "0123456789abcdef",
                        }
                    ),
                    json.dumps(
                        {
                            "event": "tokens",
                            "model": served_model,
                            "requested_model": "requested-alias",
                            "served_model": served_model,
                            "served_model_source": "response",
                            "usage_observed": True,
                            "prompt": 1,
                            "completion": 1,
                        }
                    ),
                    json.dumps({"event": "run_end", "status": "complete", "wall_s": 1}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return pytest_passed, 1.0, "", ""

    monkeypatch.setattr(bench_harness, "run_single_test", run_trial)
    monkeypatch.setattr(bench_harness, "git_state", lambda: ("0" * 40, False))

    exit_code = bench_harness.main(
        [
            "--suite",
            "easy",
            "--test",
            "test_route",
            "--trials",
            "1",
            "--model",
            "requested-alias",
            "--expected-served-model",
            "expected-served-model.gguf",
            "--log-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == expected_exit
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["tests"]["test_route"]["pytest_passed"] == int(pytest_passed)
    assert summary["tests"]["test_route"]["valid_trials"] == int(expected_valid)
    assert summary["tests"]["test_route"]["valid_passes"] == valid_passes
    assert summary["tests"]["test_route"]["route_valid"] == [expected_valid]
    assert summary["tests"]["test_route"]["contract_valid"] == [expected_valid]
    assert summary["tests"]["test_route"]["config_hashes"] == ["0123456789abcdef"]
    assert summary["tests"]["test_route"]["token_requested_models"] == [["requested-alias"]]
    assert summary["tests"]["test_route"]["served_model_sources"] == [["response"]]


def test_qualifying_cell_requires_expected_served_model(tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        bench_harness.main(
            [
                "--test",
                "test_route",
                "--trials",
                "1",
                "--log-dir",
                str(tmp_path),
            ]
        )
    assert exc_info.value.code == 2


@pytest.mark.parametrize("trials", ["0", "-1"])
def test_trial_count_must_be_positive(tmp_path, trials):
    with pytest.raises(SystemExit) as exc_info:
        bench_harness.main(
            [
                "--test",
                "test_route",
                "--trials",
                trials,
                "--expected-served-model",
                "served-model",
                "--log-dir",
                str(tmp_path),
            ]
        )
    assert exc_info.value.code == 2


def test_existing_trial_log_is_rejected_before_running(tmp_path, monkeypatch):
    existing = tmp_path / "test_route_trial1.jsonl"
    existing.write_text("old evidence\n", encoding="utf-8")
    monkeypatch.setattr(
        bench_harness,
        "run_single_test",
        lambda *_args, **_kwargs: pytest.fail("occupied log must be rejected before execution"),
    )

    with pytest.raises(SystemExit) as exc_info:
        bench_harness.main(
            [
                "--test",
                "test_route",
                "--trials",
                "1",
                "--expected-served-model",
                "served-model",
                "--log-dir",
                str(tmp_path),
            ]
        )
    assert exc_info.value.code == 2


def test_parse_log_rejects_appended_runs(tmp_path):
    path = tmp_path / "combined.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({"event": event})
            for event in ("run_start", "run_end", "run_start", "run_end")
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="exactly one run_start and one run_end"):
        bench_harness.parse_log(path)


def test_missing_served_identity_invalidates_every_token_call(tmp_path, monkeypatch):
    def run_trial(*args, **_kwargs):
        args[3].write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "event": "run_start",
                            "backend": "local",
                            "model": "requested-alias",
                            "provider": "",
                            "reasoning_effort": "",
                            "reasoning_policy": "gated",
                            "capability_profile": {"name": "generic-feature-scale-v1"},
                            "config_hash": "a" * 16,
                        }
                    ),
                    json.dumps(
                        {
                            "event": "tokens",
                            "requested_model": "requested-alias",
                            "served_model": "expected-model",
                            "served_model_source": "response",
                            "usage_observed": True,
                        }
                    ),
                    json.dumps(
                        {
                            "event": "tokens",
                            "model": "requested-alias",
                            "requested_model": "requested-alias",
                            "served_model": None,
                            "served_model_source": "unobserved",
                            "usage_observed": True,
                        }
                    ),
                    json.dumps({"event": "run_end", "status": "complete"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return True, 1.0, "", ""

    monkeypatch.setattr(bench_harness, "run_single_test", run_trial)
    monkeypatch.setattr(bench_harness, "git_state", lambda: ("0" * 40, False))

    assert (
        bench_harness.main(
            [
                "--test",
                "test_route",
                "--trials",
                "1",
                "--model",
                "requested-alias",
                "--expected-served-model",
                "expected-model",
                "--log-dir",
                str(tmp_path),
            ]
        )
        == 1
    )
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["tests"]["test_route"]["route_valid"] == [False]
    assert summary["tests"]["test_route"]["valid_trials"] == 0


@pytest.mark.parametrize(
    ("token_requested_model", "source", "usage_observed", "expected_route_valid"),
    [
        ("wrong-alias", "response", True, True),
        ("requested-alias", "unobserved", True, False),
        ("requested-alias", "response", False, True),
    ],
)
def test_every_token_call_must_match_requested_model_and_observed_source(
    tmp_path,
    monkeypatch,
    token_requested_model,
    source,
    usage_observed,
    expected_route_valid,
):
    def run_trial(*args, **_kwargs):
        args[3].write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "event": "run_start",
                            "backend": "local",
                            "model": "requested-alias",
                            "provider": "",
                            "reasoning_effort": "",
                            "reasoning_policy": "gated",
                            "capability_profile": {"name": "generic-feature-scale-v1"},
                            "config_hash": "b" * 16,
                        }
                    ),
                    json.dumps(
                        {
                            "event": "tokens",
                            "requested_model": token_requested_model,
                            "served_model": "expected-model",
                            "served_model_source": source,
                            "usage_observed": usage_observed,
                        }
                    ),
                    json.dumps({"event": "run_end", "status": "complete"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return True, 1.0, "", ""

    monkeypatch.setattr(bench_harness, "run_single_test", run_trial)
    monkeypatch.setattr(bench_harness, "git_state", lambda: ("0" * 40, False))

    assert (
        bench_harness.main(
            [
                "--test",
                "test_route",
                "--trials",
                "1",
                "--model",
                "requested-alias",
                "--expected-served-model",
                "expected-model",
                "--log-dir",
                str(tmp_path),
            ]
        )
        == 1
    )
    result = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))["tests"][
        "test_route"
    ]
    assert result["route_valid"] == [expected_route_valid]
    assert result["contract_valid"] == [False]
    assert result["token_requested_models"] == [[token_requested_model]]
    assert result["served_model_sources"] == [[source]]
    assert result["usage_complete"] == [usage_observed]


def test_pinned_provider_must_match_every_observed_response(tmp_path, monkeypatch):
    def run_trial(*args, **_kwargs):
        args[3].write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "event": "run_start",
                            "backend": "openrouter",
                            "model": "requested/model",
                            "provider": "siliconflow",
                            "reasoning_effort": "",
                            "reasoning_policy": "gated",
                            "allow_provider_fallbacks": False,
                            "require_provider_parameters": True,
                            "capability_profile": {"name": "generic-feature-scale-v1"},
                            "config_hash": "c" * 16,
                        }
                    ),
                    json.dumps(
                        {
                            "event": "tokens",
                            "requested_model": "requested/model",
                            "served_model": "served/model",
                            "served_model_source": "openrouter_metadata",
                            "provider": "OtherProvider",
                            "usage_observed": True,
                        }
                    ),
                    json.dumps({"event": "run_end", "status": "complete"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return True, 1.0, "", ""

    monkeypatch.setattr(bench_harness, "run_single_test", run_trial)
    monkeypatch.setattr(bench_harness, "git_state", lambda: ("0" * 40, False))

    assert (
        bench_harness.main(
            [
                "--backend",
                "openrouter",
                "--test",
                "test_route",
                "--trials",
                "1",
                "--model",
                "requested/model",
                "--provider",
                "siliconflow",
                "--expected-served-model",
                "served/model",
                "--log-dir",
                str(tmp_path),
            ]
        )
        == 1
    )
    result = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))["tests"][
        "test_route"
    ]
    assert result["route_valid"] == [False]
    assert result["served_provider_observed"] == [True]
    assert result["served_providers"] == [["OtherProvider"]]


def test_truthful_openrouter_record_qualifies_pinned_cell(tmp_path, monkeypatch):
    """A valid pinned-provider trial must qualify from the record askme writes.

    Regression for the Codex P1 on PR #88: the harness read a nonexistent
    ``require_parameters`` run_start key, so every OpenRouter cell failed the
    provider-parameter identity check regardless of the actual configuration.
    """

    def run_trial(*args, **_kwargs):
        args[3].write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "event": "run_start",
                            "backend": "openrouter",
                            "model": "requested/model",
                            "provider": "siliconflow",
                            "reasoning_effort": "",
                            "reasoning_policy": "gated",
                            "allow_provider_fallbacks": False,
                            "require_provider_parameters": True,
                            "capability_profile": {"name": "generic-feature-scale-v1"},
                            "config_hash": "d" * 16,
                        }
                    ),
                    json.dumps(
                        {
                            "event": "tokens",
                            "requested_model": "requested/model",
                            "served_model": "served/model",
                            "served_model_source": "openrouter_metadata",
                            "provider": "SiliconFlow",
                            "usage_observed": True,
                            "prompt": 1,
                            "completion": 1,
                        }
                    ),
                    json.dumps({"event": "run_end", "status": "complete", "wall_s": 1}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return True, 1.0, "", ""

    monkeypatch.setattr(bench_harness, "run_single_test", run_trial)
    monkeypatch.setattr(bench_harness, "git_state", lambda: ("0" * 40, False))

    assert (
        bench_harness.main(
            [
                "--backend",
                "openrouter",
                "--test",
                "test_route",
                "--trials",
                "1",
                "--model",
                "requested/model",
                "--provider",
                "siliconflow",
                "--expected-served-model",
                "served/model",
                "--log-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    result = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))["tests"][
        "test_route"
    ]
    assert result["route_valid"] == [True]
    assert result["contract_valid"] == [True]
    assert result["valid_trials"] == 1
    assert result["valid_passes"] == 1
