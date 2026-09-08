"""Fail-closed evidence checks for the credential-free external task runner."""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import external_repo_trial as trial
from tests.external_trial_claim import validate_claim


@pytest.fixture
def custom_trial(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    target = source / "src/requests/exceptions.py"
    target.parent.mkdir(parents=True)
    target.write_text("answer = 'broken'\n")
    trial.git(source, "init", "-q")
    trial.git(source, "add", "--all")
    trial.git(
        source,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "upstream baseline",
    )
    harness = Path(__file__).parent.parent
    task_dir = tmp_path / "custom-task"
    shutil.copytree(trial.TASK, task_dir)
    target.write_text("answer = 'fixed'\n")
    (task_dir / "gold.patch").write_text(trial.git(source, "diff"))
    target.write_text("answer = 'broken'\n")
    (task_dir / "acceptance.py").write_text(
        "import json, pathlib, sys\n"
        "content = (pathlib.Path(sys.argv[1]) / 'src/requests/exceptions.py').read_text()\n"
        "accepted = content == \"answer = 'fixed'\\n\"\n"
        "print(json.dumps({'accepted': accepted, 'checks': 21}))\n"
        "sys.exit(0 if accepted else 1)\n"
    )
    protocol = json.loads((task_dir / "protocol.json").read_text())
    protocol.update(
        protocol="requests-physical-local-v2",
        target_revision=trial.git(source, "rev-parse", "HEAD").strip(),
        harness_revision=trial.git(harness, "rev-parse", "HEAD").strip(),
        runtime_sha256={name: trial.digest(harness / name) for name in ("askme.py", "actions.py")},
        api_url="http://127.0.0.1:18090/v1/chat/completions",
        wall_timeout_seconds=93,
        runner="physical-test-machine",
        runner_cost="existing local hardware",
        api_cost_usd=0,
        limitations="One task on physical hardware; no reliability inference.",
    )
    serving_path = tmp_path / "serving.json"
    trial.save(
        serving_path,
        {
            "qualified": True,
            "protocol": "serving-local-v2",
            "model_alias": protocol["model_alias"],
            "api_url": protocol["api_url"],
        },
    )
    protocol["serving_qualification"] = {
        "path": "../serving.json",
        "sha256": trial.digest(serving_path),
        "protocol": "serving-local-v2",
    }
    trial.save(task_dir / "protocol.json", protocol)

    return SimpleNamespace(
        source=source,
        harness=harness,
        output=tmp_path / "records",
        task_dir=task_dir,
        protocol=protocol,
        serving_path=serving_path,
    )


def test_custom_task_uses_qualified_endpoint_budget_and_retains_one_attempt(
    custom_trial, monkeypatch
):
    from actions import CapturedProcess

    case = custom_trial
    trial.prepare(case.source, case.harness, case.output, case.task_dir)
    assert (
        case.output / "serving-qualification.json"
    ).read_bytes() == case.serving_path.read_bytes()

    def worker(args, *, cwd, env, timeout):
        assert args[args.index("--task-dir") + 1] == str(case.task_dir)
        assert env["LLM_API_URL"] == case.protocol["api_url"]
        assert timeout == case.protocol["wall_timeout_seconds"]
        (Path(cwd) / "src/requests/exceptions.py").write_text("answer = 'fixed'\n")
        return SimpleNamespace(returncode=0, stdout="fixed", stderr="")

    monkeypatch.setattr(CapturedProcess, "run", worker)
    result = trial.run_trial(case.source, case.harness, case.output, case.task_dir)
    assert result["decision"] == "resolved_this_one_task"
    assert result["configured_timeout_s"] == 93
    for field in ("runner", "runner_cost", "api_cost_usd", "limitations"):
        assert result[field] == case.protocol[field]
    with pytest.raises(FileExistsError):
        trial.run_trial(case.source, case.harness, case.output, case.task_dir)


@pytest.mark.parametrize("mode", ["prepare", "run", "worker"])
@pytest.mark.parametrize("explicit_task", [False, True])
def test_cli_selects_task_directory(tmp_path, monkeypatch, mode, explicit_task):
    argv = [
        "external_repo_trial.py",
        mode,
        "--source",
        str(tmp_path / "source"),
        "--harness",
        str(tmp_path / "harness"),
        "--output",
        str(tmp_path / "output"),
    ]
    task_dir = tmp_path / "custom-task" if explicit_task else trial.TASK
    if explicit_task:
        argv.extend(["--task-dir", str(task_dir)])
    observed = []
    function = {"prepare": "prepare", "run": "run_trial", "worker": "worker"}[mode]
    monkeypatch.setattr(trial, function, lambda *args: observed.append(args))
    monkeypatch.setattr(trial.sys, "argv", argv)
    trial.main()
    assert len(observed) == 1
    assert observed[0][-1] == task_dir.resolve()


@pytest.mark.parametrize(
    "field,value",
    [
        ("qualified", False),
        ("qualified", "true"),
        ("protocol", "different-serving-protocol"),
        ("model_alias", "different-model"),
        ("api_url", "http://127.0.0.1:8080/v1/chat/completions"),
    ],
)
def test_serving_gate_rejects_unqualified_or_mismatched_records(custom_trial, field, value):
    case = custom_trial
    record = json.loads(case.serving_path.read_text())
    record[field] = value
    trial.save(case.serving_path, record)
    case.protocol["serving_qualification"]["sha256"] = trial.digest(case.serving_path)
    trial.save(case.task_dir / "protocol.json", case.protocol)
    with pytest.raises(ValueError, match="[Ss]erving"):
        trial.prepare(case.source, case.harness, case.output, case.task_dir)
    assert not (case.output / "trial-started.json").exists()


@pytest.mark.parametrize("changed", ["original", "retained", "task", "runtime", "runner"])
def test_registered_evidence_changes_block_before_task_attempt(custom_trial, monkeypatch, changed):
    case = custom_trial
    trial.prepare(case.source, case.harness, case.output, case.task_dir)
    if changed == "original":
        case.serving_path.write_text(case.serving_path.read_text() + "\n")
    elif changed == "retained":
        path = case.output / "serving-qualification.json"
        path.write_text(path.read_text() + "\n")
    elif changed == "task":
        (case.task_dir / "acceptance.py").write_text("raise AssertionError('changed')\n")
    elif changed == "runtime":
        actual_digest = trial.digest
        monkeypatch.setattr(
            trial,
            "digest",
            lambda path: (
                "changed" if Path(path) == case.harness / "askme.py" else actual_digest(path)
            ),
        )
    else:
        registration = json.loads((case.output / "registration.json").read_text())
        registration["runner_sha256"] = "changed"
        trial.save(case.output / "registration.json", registration)
    with pytest.raises(ValueError):
        trial.run_trial(case.source, case.harness, case.output, case.task_dir)
    assert not (case.output / "trial-started.json").exists()


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

    def independent_check(workspace, task_dir):
        assert task_dir == trial.TASK
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
