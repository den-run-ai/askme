"""Fail-closed evidence checks for the credential-free external task runner."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import external_repo_trial as trial


@pytest.mark.parametrize(
    "stdout,exit_code,accepted",
    [
        ('{"accepted":true,"checks":21}', 0, True),
        ('{"accepted":true,"checks":0}', 0, False),
        ('{"accepted":true,"checks":21}', 1, False),
        ('{"accepted":false,"checks":21}', 0, False),
        ("", 0, False),
        ("all fine", 0, False),
    ],
)
def test_independent_acceptance_needs_exact_structured_evidence(
    monkeypatch, stdout, exit_code, accepted
):
    monkeypatch.setattr(
        trial.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=exit_code, stdout=stdout, stderr="diagnostic"),
    )
    result = trial.evaluate(Path("unused"))
    assert result["accepted"] is accepted
    assert result["stderr"] == "diagnostic"


def test_evaluator_timeout_preserves_partial_output(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("acceptance", 30, output=b"partial", stderr=b"stalled")

    monkeypatch.setattr(trial.subprocess, "run", timeout)
    assert trial.evaluate(Path("unused")) == {
        "accepted": False,
        "exit_code": None,
        "error": "evaluator_timeout",
        "stdout": "partial",
        "stderr": "stalled",
    }


@pytest.mark.parametrize(
    "changes,accepted,decision",
    [
        (["src/requests/exceptions.py"], True, "resolved_this_one_task"),
        (["src/requests/exceptions.py"], False, "did_not_resolve"),
        (["src/requests/exceptions.py", "src/requests/models.py"], True, "did_not_resolve"),
        ([], True, "did_not_resolve"),
    ],
)
def test_patch_acceptance_and_agent_termination_are_separate(tmp_path, changes, accepted, decision):
    (tmp_path / "agent.jsonl").write_text(
        json.dumps({"event": "run_end", "status": "exhausted"}) + "\n"
    )
    result = trial.summarize(
        tmp_path, {"exit_code": 0}, 3.25, {"accepted": accepted}, True, changes
    )
    assert result["decision"] == decision
    assert result["agent_status"] == "exhausted"
    assert result["trial_wall_s"] == 3.25
    assert json.loads((tmp_path / "summary.json").read_text()) == result


def test_control_gate_requires_gold_and_nonempty_failing_noop():
    records = {
        "baseline": {"accepted": False},
        "noop": {"accepted": False, "patch_nonempty": True},
        "gold": {"accepted": True, "patch_nonempty": True},
    }
    assert trial.controls_valid(records)
    records["noop"]["patch_nonempty"] = False
    assert not trial.controls_valid(records)
    records["noop"]["patch_nonempty"] = True
    records["baseline"]["accepted"] = True
    assert not trial.controls_valid(records)


def test_http_recorder_preserves_whole_body_and_seed(tmp_path, monkeypatch):
    import requests

    observed = []
    long_text = "雪" * 15000

    def post(api, **kwargs):
        observed.append(kwargs)
        return SimpleNamespace(status_code=200, text=long_text)

    monkeypatch.setattr(requests, "post", post)
    path = tmp_path / "http.jsonl"
    trial.record_post(path, 20260908)(
        "http://127.0.0.1:8080/v1/chat/completions", json={"messages": []}, headers={}, timeout=120
    )
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert records[1]["body"] == long_text
    assert observed[0]["json"] == {"messages": [], "seed": 20260908}
    assert records[0]["body"] == observed[0]["json"]


def test_workflow_requires_explicit_same_repository_opt_in():
    workflow = (
        Path(__file__).parent.parent / ".github/workflows/external-local-trial.yml"
    ).read_text()
    assert "types: [labeled]" in workflow
    assert "github.event.label.name == 'external-local-trial'" in workflow
    assert "github.event.pull_request.head.repo.full_name == github.repository" in workflow
    assert "github.run_attempt == 1" in workflow
    assert "schedule:" not in workflow and "push:" not in workflow
    assert "secrets." not in workflow and "pull_request_target" not in workflow
