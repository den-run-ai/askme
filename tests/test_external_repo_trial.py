"""Fail-closed evidence checks for the credential-free external task runner."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import external_repo_trial as trial
from tests.external_trial_claim import validate_claim


def test_committed_repair_is_replayed_against_original_workspace(tmp_path, monkeypatch):
    from actions import CapturedProcess

    source = tmp_path / "source"
    source.mkdir()
    target = source / "src/requests/exceptions.py"
    target.parent.mkdir(parents=True)
    target.write_text("answer = 'broken'\n")
    trial.git(source, "init", "-q")
    trial.git(source, "add", "--all")
    commit_args = ("-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit")
    trial.git(source, *commit_args, "-qm", "upstream baseline")
    harness = Path(__file__).parent.parent
    protocol = json.loads((trial.TASK / "protocol.json").read_text())
    protocol["target_revision"] = trial.git(source, "rev-parse", "HEAD").strip()
    protocol["harness_revision"] = trial.git(harness, "rev-parse", "HEAD").strip()
    output = tmp_path / "records"
    output.mkdir()
    trial.save(output / "protocol.json", protocol)
    (output / "prompt.md").write_bytes((trial.TASK / "prompt.md").read_bytes())
    trial.save(
        output / "registration.json",
        {
            "protocol": protocol,
            "runner_sha256": trial.digest(trial.__file__),
            "source_sha256": {p.name: trial.digest(p) for p in trial.TASK.iterdir() if p.is_file()},
        },
    )
    trial.save(
        output / "qualification.json",
        {
            "baseline": {"accepted": False},
            "noop": {"accepted": False, "patch_nonempty": True},
            "gold": {"accepted": True, "patch_nonempty": True},
        },
    )

    def committed_worker(*args, cwd, **kwargs):
        workspace = Path(cwd)
        (workspace / "src/requests/exceptions.py").write_text("answer = 'fixed'\n")
        trial.git(workspace, "add", "--all")
        trial.git(workspace, *commit_args, "-qm", "model commits the repair")
        assert trial.git(workspace, "status", "--porcelain") == ""
        return SimpleNamespace(returncode=0, stdout="committed repair", stderr="")

    def independent_check(workspace):
        return {
            "accepted": (workspace / "src/requests/exceptions.py").read_text()
            == "answer = 'fixed'\n"
        }

    monkeypatch.setattr(CapturedProcess, "run", committed_worker)
    monkeypatch.setattr(trial, "evaluate", independent_check)
    result = trial.run_trial(source, harness, output)
    assert result["decision"] == "resolved_this_one_task"
    assert result["changed_files"] == ["src/requests/exceptions.py"]
    assert "+answer = 'fixed'" in (output / "candidate.patch").read_text()
    captured = json.loads((output / "workspace-baseline.json").read_text())
    assert captured["upstream_revision"] == protocol["target_revision"]


@pytest.fixture
def claimed_primary_run():
    protocol_path = trial.TASK / "protocol.json"
    protocol = json.loads(protocol_path.read_text())
    claim = {
        "protocol": protocol["protocol"],
        "harness_revision": protocol["harness_revision"],
        "protocol_sha256": trial.digest(protocol_path),
        "workflow_revision": "1f3eff01287af0a7707ae8e007c293efcbfddc2f",
        "run_id": 34175311656,
        "run_attempt": 1,
    }
    environment = {
        "GITHUB_REPOSITORY": "den-run-ai/askme",
        "GITHUB_RUN_ID": "34175311656",
        "GITHUB_RUN_ATTEMPT": "1",
        "TRIAL_WORKFLOW_REVISION": claim["workflow_revision"],
    }
    return claim, protocol_path, environment


def test_claim_accepts_only_the_registered_primary_run(claimed_primary_run):
    claim, protocol_path, environment = claimed_primary_run
    assert validate_claim(claim, protocol_path, environment) == claim


@pytest.mark.parametrize(
    "field,value",
    [
        ("GITHUB_RUN_ID", "34175311657"),  # Another fresh dispatch also has attempt 1.
        ("GITHUB_RUN_ID", "34175311655"),
        ("GITHUB_RUN_ATTEMPT", "0"),
        ("GITHUB_RUN_ATTEMPT", "2"),
        ("GITHUB_REPOSITORY", "someone/askme"),
        ("TRIAL_WORKFLOW_REVISION", "f" * 40),
    ],
)
def test_claim_rejects_other_runs_and_attempts(claimed_primary_run, field, value):
    claim, protocol_path, environment = claimed_primary_run
    environment[field] = value
    with pytest.raises(ValueError):
        validate_claim(claim, protocol_path, environment)


@pytest.mark.parametrize("field", ["protocol", "harness_revision", "protocol_sha256"])
def test_claim_rejects_changed_protocol(claimed_primary_run, field):
    claim, protocol_path, environment = claimed_primary_run
    claim[field] = "changed"
    with pytest.raises(ValueError):
        validate_claim(claim, protocol_path, environment)


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
    assert "repository: den-run-ai/askme" in workflow
    assert "ref: 1cd6a9a0384202791f611f3f3c0fb040863591e8" in workflow
    assert workflow.index("python3 tests/external_trial_claim.py") < workflow.index("Build pinned")
