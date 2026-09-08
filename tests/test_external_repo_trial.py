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
def hosted_protocol():
    return {
        "backend": "openrouter",
        "api_url": "https://openrouter.ai/api/v1/chat/completions",
        "model_alias": "google/gemma-4-26b-a4b-it",
        "openrouter": {
            "provider": "DeepInfra",
            "expected_response_provider": "DeepInfra",
            "expected_response_model": "google/gemma-4-26b-a4b-it-20260403",
            "quantizations": ["fp8"],
        },
        "cost_control": {
            "cap_usd": "0.50",
            "prompt_usd_per_token": "0.00000007",
            "completion_usd_per_token": "0.00000034",
            "prompt_overhead_tokens": 16384,
        },
    }


@pytest.fixture
def hosted_http(monkeypatch, hosted_protocol):
    import requests

    case = SimpleNamespace(
        calls=[],
        response={
            "provider": "DeepInfra",
            "model": "google/gemma-4-26b-a4b-it-20260403",
            "usage": {"cost": 0.000123, "prompt_tokens": 12, "completion_tokens": 8},
        },
        error=None,
        body={
            "model": hosted_protocol["model_alias"],
            "messages": [{"role": "user", "content": "雪" * 25}],
            "max_tokens": 512,
            "provider": {
                "order": ["DeepInfra"],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
        },
    )

    class Session:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, api, **kwargs):
            assert self.trust_env is False
            assert kwargs["allow_redirects"] is False
            case.calls.append((api, kwargs))
            if case.error:
                raise case.error
            return SimpleNamespace(status_code=200, text=json.dumps(case.response))

    monkeypatch.setattr(requests, "Session", Session)
    return case


def test_hosted_requests_pin_route_record_actual_cost_and_keep_credentials_out(
    tmp_path, hosted_protocol, hosted_http
):
    path = tmp_path / "http.jsonl"
    post = trial.record_post(path, 123, hosted_protocol)
    for _ in range(2):
        post(
            hosted_protocol["api_url"],
            json=hosted_http.body,
            headers={"Authorization": "Bearer fake-secret-for-regression"},
            timeout=120,
        )
    body = hosted_http.calls[0][1]["json"]
    assert body["seed"] == 123
    assert body["provider"]["quantizations"] == ["fp8"]
    assert "quantizations" not in hosted_http.body["provider"]
    assert "fake-secret-for-regression" not in path.read_text()
    cost = trial.HostedRequestBudget.summary(path)
    assert cost["api_cost_usd"] == pytest.approx(0.000246)
    assert cost["api_cost_complete"]
    assert cost["api_cost_upper_bound_usd"] == pytest.approx(cost["api_cost_usd"])
    summary = trial.summarize(
        tmp_path, {"exit_code": 0}, 1, {"accepted": False}, False, [], hosted_protocol
    )
    assert summary["api_cost_usd"] == pytest.approx(0.000246)


@pytest.mark.parametrize("failure", ["price", "provider", "model", "missing_cost", "negative_cost"])
def test_hosted_unexpected_or_unpriced_response_stops_further_calls(
    tmp_path, hosted_protocol, hosted_http, failure
):
    if failure == "price":
        hosted_http.response["usage"]["cost"] = 0.02
    elif failure in {"provider", "model"}:
        hosted_http.response[failure] = "unexpected"
    elif failure == "missing_cost":
        del hosted_http.response["usage"]["cost"]
    else:
        hosted_http.response["usage"]["cost"] = -0.1
    path = tmp_path / "http.jsonl"
    post = trial.record_post(path, 123, hosted_protocol)
    for _ in range(2):
        with pytest.raises((RuntimeError, ValueError)):
            post(hosted_protocol["api_url"], json=hosted_http.body, headers={}, timeout=120)
    assert len(hosted_http.calls) == 1
    assert any(e["event"] == "response" for e in map(json.loads, path.read_text().splitlines()))


def test_hosted_unknown_attempt_reserves_its_cost_before_retry(
    tmp_path, hosted_protocol, hosted_http
):
    import requests

    hosted_protocol["cost_control"]["cap_usd"] = "0.002"
    hosted_http.error = requests.Timeout("Synthetic timeout")
    path = tmp_path / "http.jsonl"
    post = trial.record_post(path, 123, hosted_protocol)
    with pytest.raises(requests.Timeout):
        post(hosted_protocol["api_url"], json=hosted_http.body, headers={}, timeout=120)
    with pytest.raises(RuntimeError, match="budget"):
        post(hosted_protocol["api_url"], json=hosted_http.body, headers={}, timeout=120)
    assert len(hosted_http.calls) == 1
    cost = trial.HostedRequestBudget.summary(path)
    assert cost["api_cost_usd"] == 0
    assert not cost["api_cost_complete"]
    assert cost["api_cost_unpriced_requests"] == 1
    assert 0 < cost["api_cost_upper_bound_usd"] <= 0.002


@pytest.mark.parametrize("failure", ["api", "model", "route", "output", "oversize_prompt"])
def test_hosted_guard_refuses_before_http(tmp_path, hosted_protocol, hosted_http, failure):
    api = hosted_protocol["api_url"]
    if failure == "api":
        api += "/redirect"
    elif failure == "model":
        hosted_http.body["model"] = "other-model"
    elif failure == "route":
        hosted_http.body["provider"]["allow_fallbacks"] = True
    elif failure == "output":
        hosted_http.body["max_tokens"] = -1
    else:
        hosted_protocol["cost_control"]["cap_usd"] = "0.0015"
        hosted_http.body["messages"][0]["content"] = "雪" * 3000
    post = trial.record_post(tmp_path / "http.jsonl", 123, hosted_protocol)
    with pytest.raises((RuntimeError, ValueError)):
        post(api, json=hosted_http.body, headers={}, timeout=120)
    assert not hosted_http.calls


def test_hosted_partial_response_retains_unknown_cost_bound(tmp_path, hosted_protocol, hosted_http):
    import requests

    hosted_http.error = requests.Timeout("Synthetic timeout")
    path = tmp_path / "http.jsonl"
    post = trial.record_post(path, 123, hosted_protocol)
    with pytest.raises(requests.Timeout):
        post(hosted_protocol["api_url"], json=hosted_http.body, headers={}, timeout=120)
    with path.open("a") as stream:
        stream.write('{"event":"response","body":"incomplete')
    summary = trial.HostedRequestBudget.summary(path)
    assert not summary["api_cost_complete"]
    assert summary["api_cost_unpriced_requests"] == 1
    assert summary["api_cost_upper_bound_usd"] > 0


def test_hosted_worker_freezes_route_and_removes_credential_from_action_environment(
    tmp_path, monkeypatch, hosted_protocol
):
    import askme

    protocol = json.loads((trial.TASK / "protocol.json").read_text()) | hosted_protocol
    output = tmp_path / "output"
    output.mkdir()
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    for directory in (output, task_dir):
        trial.save(directory / "protocol.json", protocol)
    (output / "prompt.md").write_text("Synthetic task")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-inherited-credential")
    monkeypatch.setenv("OPENROUTER_MODEL", "unexpected-environment-model")
    monkeypatch.setenv("OPENROUTER_PROVIDER", "unexpected-environment-provider")

    def run_result(prompt, workspace, *, config, dependencies):
        assert "OPENROUTER_API_KEY" not in trial.os.environ
        assert config.llm.api_key == "fake-inherited-credential"
        assert config.llm.backend == "openrouter"
        assert config.llm.api == hosted_protocol["api_url"]
        assert config.llm.model == hosted_protocol["model_alias"]
        assert config.llm.provider == "DeepInfra"
        assert config.llm.allow_fallbacks is False
        assert config.llm.require_parameters is True
        return {"status": "synthetic_no_model_calls"}

    monkeypatch.setattr(askme, "run_result", run_result)
    trial.worker(Path(__file__).parent.parent, tmp_path, output, task_dir)
    assert "fake-inherited-credential" not in (output / "agent-result.json").read_text()


def test_hosted_worker_requires_credential_already_in_environment(
    tmp_path, monkeypatch, hosted_protocol
):
    trial.save(tmp_path / "protocol.json", hosted_protocol)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="explicitly inherited"):
        trial.worker(Path(__file__).parent.parent, tmp_path, tmp_path, tmp_path)


@pytest.mark.parametrize("field,value", [("cap_usd", "0.51"), ("prompt_usd_per_token", "NaN")])
def test_hosted_manifest_requires_finite_bounded_prices(hosted_protocol, field, value):
    hosted_protocol["cost_control"][field] = value
    with pytest.raises(ValueError):
        trial.HostedRequestBudget(hosted_protocol)


@pytest.mark.parametrize(
    "backend,declared,expected",
    [
        ("local", None, "standard public macOS runner; no API charge"),
        ("openrouter", None, "existing local hardware; API charges reported separately"),
        ("openrouter", "declared runner cost", "declared runner cost"),
    ],
)
def test_summary_runner_cost_describes_backend_and_preserves_explicit_metadata(
    tmp_path, backend, declared, expected
):
    protocol = {"backend": backend}
    if declared is not None:
        protocol["runner_cost"] = declared
    trial.summarize(tmp_path, {"exit_code": 0}, 1, {"accepted": False}, False, [], protocol)
    published = json.loads((tmp_path / "summary.json").read_text())
    assert published["runner_cost"] == expected


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
        runtime_sha256={path.name: trial.digest(path) for path in harness.glob("*.py")},
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


@pytest.mark.parametrize("changed", ["omit", "extra", "traversal"])
def test_task_protocol_requires_exact_runtime_inventory_before_attempt(custom_trial, changed):
    case = custom_trial
    runtime = case.protocol["runtime_sha256"]
    if changed == "omit":
        del runtime["actions.py"]
    else:
        runtime["../outside.py" if changed == "traversal" else "unregistered.py"] = "0" * 64
    trial.save(case.task_dir / "protocol.json", case.protocol)
    with pytest.raises(ValueError, match="pin every runtime module"):
        trial.prepare(case.source, case.harness, case.output, case.task_dir)
    assert not (case.output / "trial-started.json").exists()


def test_changed_runtime_discovery_blocks_registered_task(custom_trial):
    case = custom_trial
    trial.prepare(case.source, case.harness, case.output, case.task_dir)
    registration = json.loads((case.output / "registration.json").read_text())
    registration["runtime_discovery_sha256"] = "0" * 64
    trial.save(case.output / "registration.json", registration)
    with pytest.raises(ValueError, match="runtime discovery changed"):
        trial.run_trial(case.source, case.harness, case.output, case.task_dir)
    assert not (case.output / "trial-started.json").exists()


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
    protocol["runtime_sha256"] = {path.name: trial.digest(path) for path in harness.glob("*.py")}
    output = tmp_path / "records"
    output.mkdir()
    trial.save(output / "protocol.json", protocol)
    (output / "prompt.md").write_bytes((trial.TASK / "prompt.md").read_bytes())
    trial.save(
        output / "registration.json",
        {
            "protocol": protocol,
            "runner_sha256": trial.digest(trial.__file__),
            "runtime_discovery_sha256": trial.digest(
                trial.runtime_source_paths.__code__.co_filename
            ),
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
