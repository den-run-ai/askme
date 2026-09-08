"""Credential-free, network-free hosted qualification contract tests."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import hosted_serving_qualification as gate
import pytest


def protocol():
    return gate.resolve_protocol(
        {
            "protocol": "hosted-synthetic-v1",
            "backend": "openrouter",
            "api_url": gate.API,
            "model_alias": "google/gemma-4-26b-a4b-it",
            "openrouter": {
                "provider": "DeepInfra",
                "expected_response_provider": "DeepInfra",
                "expected_response_model": "google/gemma-4-26b-a4b-it-20260403",
                "quantizations": ["fp8"],
            },
            "cost_control": {
                "cap_usd": "0.05",
                "prompt_usd_per_token": "0.00000007",
                "completion_usd_per_token": "0.00000034",
                "prompt_overhead_tokens": 16384,
            },
        }
    )


def response(kind="plan", valid=True):
    message = {"content": '{"tasks":["Create hello.txt"]}' if valid else '{"tasks":[null]}'}
    if kind == "action":
        message = {
            "tool_calls": [
                {
                    "function": {
                        "name": "write",
                        "arguments": json.dumps(
                            {
                                "arg": "hello.txt",
                                "content": "hi\n" if valid else "wrong",
                            }
                        ),
                    }
                }
            ]
        }
    payload = {
        "provider": "DeepInfra",
        "model": "google/gemma-4-26b-a4b-it-20260403",
        "usage": {"prompt_tokens": 600, "completion_tokens": 30, "cost": 0.0001},
        "choices": [
            {"finish_reason": "tool_calls" if kind == "action" else "stop", "message": message}
        ],
    }
    return SimpleNamespace(json=lambda: payload, raise_for_status=lambda: None)


@pytest.mark.parametrize("cost", ["0.06", "0", "-1", "Infinity", "NaN"])
def test_qualification_cap_cannot_exceed_authorized_five_cents(cost):
    p = protocol()
    p["cost_control"]["cap_usd"] = cost
    with pytest.raises(ValueError, match="cost cap"):
        gate.resolve_protocol(p)


def test_paid_endpoint_cannot_be_substituted():
    with pytest.raises(ValueError, match="official"):
        gate.resolve_protocol({**protocol(), "api_url": "https://example.com/chat"})


def test_requests_match_actual_unforced_askme_contract():
    import askme

    body, headers = gate.build_request(gate.CASES[1], protocol(), "synthetic-test-key")
    assert body["tools"] == askme._ACTION_TOOLS
    assert body["tool_choice"] == "auto"
    assert body["provider"] == {
        "order": ["DeepInfra"],
        "allow_fallbacks": False,
        "require_parameters": True,
        "quantizations": ["fp8"],
    }
    assert body["reasoning"] == {"enabled": False}
    assert body["max_tokens"] == 512
    assert body["messages"][0]["content"] == askme.SYSTEM_STEP
    assert headers["Authorization"] == "Bearer synthetic-test-key"
    assert "synthetic-test-key" not in json.dumps(body)
    planner, _ = gate.build_request(gate.CASES[0], protocol(), "synthetic-test-key")
    assert planner["max_tokens"] == 768
    assert "tools" not in planner
    assert "response_format" not in planner
    assert planner["messages"][0]["content"] == askme.SYSTEM_PLAN


def test_semantic_failure_is_retained_then_second_probe_runs_once(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-test-key")
    calls = []

    def factory(path, seed, *, protocol):
        def post(api, *, json, headers, timeout):
            calls.append(json)
            assert timeout == 60
            assert headers["Authorization"] == "Bearer synthetic-test-key"
            return response("plan", valid=False) if len(calls) == 1 else response("action")

        return post

    results = gate.run_probes(protocol(), tmp_path, post_factory=factory)
    assert len(calls) == 2
    assert not results[0]["qualified"]
    assert results[1]["qualified"]
    assert json.loads((tmp_path / "probe-results.json").read_text()) == results
    assert all("synthetic-test-key" not in path.read_text() for path in tmp_path.iterdir())


def test_budget_guard_failure_blocks_next_http_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-test-key")
    calls = []

    def factory(path, seed, *, protocol):
        def post(api, **kwargs):
            calls.append(api)
            path.write_text(
                json.dumps({"event": "cost_ledger", "guard_error": "budget exhausted"}) + "\n"
            )
            raise RuntimeError("budget exhausted")

        return post

    result = gate.run_probes(protocol(), tmp_path, post_factory=factory)
    assert len(calls) == len(result) == 1
    assert result[0]["error"]["detail"] == "budget exhausted"
    assert not result[0]["qualified"]


def test_well_formed_reply_past_total_call_limit_fails_timing(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-test-key")
    times = iter([0, 61, 62, 63])
    replies = iter([response(), response("action")])
    result = gate.run_probes(
        protocol(),
        tmp_path,
        post_factory=lambda *args, **kwargs: lambda *a, **kw: next(replies),
        clock=lambda: next(times),
    )
    assert not result[0]["qualified"]
    assert result[0]["timing_error"] == "total_call_limit_exceeded"
    assert result[1]["qualified"]


def test_missing_key_never_enters_http_factory(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="launching environment"):
        gate.run_probes(
            protocol(), tmp_path, post_factory=lambda *args, **kwargs: pytest.fail("HTTP created")
        )


def test_outer_deadline_retains_partial_results_and_registration(tmp_path, monkeypatch):
    import external_repo_trial as trial

    monkeypatch.setattr(
        trial,
        "HostedRequestBudget",
        SimpleNamespace(
            summary=lambda path: {
                "api_cost_usd": 0.0001,
                "api_cost_complete": False,
            }
        ),
        raising=False,
    )
    output = tmp_path / "qualification"

    def runner(argv, *, timeout, cwd):
        assert timeout == 150
        assert (output / "registration.json").exists()
        assert (output / "started.json").exists()
        assert "synthetic-test-key" not in " ".join(argv)
        gate.save(output / "probe-results.json", [{"name": "planner", "qualified": True}])
        raise subprocess.TimeoutExpired(argv, timeout)

    result = gate.run(protocol(), output, process_runner=runner)
    assert not result["qualified"]
    assert result["process"]["timed_out"]
    assert len(result["results"]) == 1
    assert json.loads((output / "qualification.json").read_text()) == result
    with pytest.raises(FileExistsError):
        gate.run(protocol(), output, process_runner=runner)


def test_worker_refuses_changed_code_before_http(tmp_path, monkeypatch):
    gate.save(
        tmp_path / "registration.json",
        {
            "protocol_sha256": "wrong",
            "runner_sha256": gate.digest(Path(gate.__file__)),
            "request_guard_sha256": gate.digest(gate.ROOT / "tests" / "external_repo_trial.py"),
            "runtime_sha256": {},
            "protocol": protocol(),
        },
    )
    gate.save(tmp_path / "protocol.json", protocol())
    monkeypatch.setattr(gate, "run_probes", lambda *args: pytest.fail("No HTTP after mismatch"))
    with pytest.raises(ValueError, match="changed"):
        gate.worker(tmp_path)


def test_partial_ledger_cannot_clear_guard(tmp_path):
    path = tmp_path / "http.jsonl"
    path.write_text('{"event":"response",')
    assert gate.guard_failed(path)
