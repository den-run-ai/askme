import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


MODULE_PATH = Path(__file__).parent / "featurebench" / "askme_adapter.py"
SPEC = importlib.util.spec_from_file_location("askme_featurebench_adapter", MODULE_PATH)
adapter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = adapter
SPEC.loader.exec_module(adapter)


class FakeLogger:
    def __init__(self):
        self.errors = []

    def error(self, message):
        self.errors.append(message)


class FakeBaseAgent:
    def __init__(self, container_manager, env_vars=None, logger=None, **kwargs):
        self.cm = container_manager
        self.env_vars = env_vars or {}
        self.logger = logger or FakeLogger()
        self._kwargs = kwargs


class FakeContainerManager:
    def __init__(self):
        self.files = {}
        self.commands = []

    def copy_to_container(self, _container, source, destination):
        self.files[destination] = Path(source).read_bytes()

    def copy_from_container(self, _container, source, destination):
        if source not in self.files:
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.files[source])
        return True

    def exec_command(self, _container, command, log_file=None):
        self.commands.append((command, log_file))
        return 0, ""


def _source(tmp_path):
    path = tmp_path / "askme.py"
    path.write_text("print('pinned askme')\n", encoding="utf-8")
    return path


def _agent(tmp_path, env=None):
    source = _source(tmp_path)
    agent_class = adapter.build_askme_agent_class(
        FakeBaseAgent, source, "secret-key", inner_timeout=3540
    )
    cm = FakeContainerManager()
    strict_env = adapter.strict_openrouter_env("qwen/qwen3.6-27b", "siliconflow")
    if env:
        strict_env.update(env)
    return agent_class(cm, strict_env, FakeLogger()), cm, source


def test_strict_openrouter_environment_disables_fallbacks_and_omits_credential():
    env = adapter.strict_openrouter_env("model/id", "siliconflow")

    assert env["LLM_BACKEND"] == "openrouter"
    assert env["OPENROUTER_ALLOW_FALLBACKS"] == "0"
    assert env["OPENROUTER_REQUIRE_PARAMETERS"] == "1"
    assert env["ALLOW_NETWORK"] == "0"
    assert env["ALLOW_SYSTEM_INSTALLS"] == "0"
    assert env["AGENT_RUN_LOG"] == "/agent-logs/askme-run.jsonl"
    assert "OPENROUTER_API_KEY" not in env
    adapter.validate_strict_env(env)

    env["OPENROUTER_ALLOW_FALLBACKS"] = "1"
    with pytest.raises(ValueError, match="OPENROUTER_ALLOW_FALLBACKS"):
        adapter.validate_strict_env(env)

    env = adapter.strict_openrouter_env("model/id", "siliconflow")
    env["AGENT_REASONING_POLICY"] = "off"
    with pytest.raises(ValueError, match="AGENT_REASONING_POLICY"):
        adapter.validate_strict_env(env)


@pytest.mark.parametrize("field", ["model", "provider"])
def test_strict_openrouter_environment_requires_route_fields(field):
    values = {"model": "model/id", "provider": "siliconflow"}
    values[field] = ""
    with pytest.raises(ValueError):
        adapter.strict_openrouter_env(**values)


def test_strict_openrouter_environment_rejects_automatic_provider_route():
    with pytest.raises(ValueError, match="must be pinned"):
        adapter.strict_openrouter_env("model/id", "auto")


def _launcher_namespace(tmp_path):
    namespace = {"__name__": "askme_launcher_test"}
    exec(compile(adapter.launcher_source(), "<askme-launcher>", "exec"), namespace)
    namespace["POLICY_LOG_PATH"] = str(tmp_path / "policy.jsonl")
    namespace["RESULT_PATH"] = str(tmp_path / "result.json")
    namespace["WORKSPACE"] = (tmp_path / "workspace").resolve()
    namespace["WORKSPACE"].mkdir()
    return namespace


def test_launcher_scrubs_transient_credential_before_agent_actions(tmp_path, monkeypatch):
    namespace = _launcher_namespace(tmp_path)
    source = tmp_path / "pinned_askme.py"
    source.write_text(
        "import os\n"
        "CAPTURED_KEY = os.environ.get('OPENROUTER_API_KEY')\n"
        "def execute(action, working_dir='.'):\n"
        "    return {'ok': True, 'output': 'ok'}\n"
        "def _main():\n"
        "    return 0\n",
        encoding="utf-8",
    )
    credential = tmp_path / "credential"
    credential.write_text("transient-secret\n", encoding="utf-8")
    namespace["ASKME_PATH"] = str(source)
    namespace["CREDENTIAL_PATH"] = str(credential)
    monkeypatch.setenv("OPENROUTER_API_KEY", "parent-placeholder")

    module, secret = namespace["_load_askme"]()

    assert module.CAPTURED_KEY == "transient-secret"
    assert secret == "transient-secret"
    assert "OPENROUTER_API_KEY" not in os.environ
    assert not credential.exists()


def test_launcher_guards_actions_and_records_outcomes(tmp_path):
    namespace = _launcher_namespace(tmp_path)

    def execute(action, _working_dir):
        if action.get("arg") == "timeout-case":
            return {"ok": False, "output": "TIMEOUT", "error_type": "timeout"}
        if action.get("arg") == "exception-case":
            raise RuntimeError("boom")
        return {"ok": True, "output": "ok"}

    module = SimpleNamespace(execute=execute)
    namespace["_install_guard"](module, "transient-secret")
    workspace = str(namespace["WORKSPACE"])

    assert module.execute({"action": "shell", "arg": "pytest -q"}, workspace)["ok"]
    assert module.execute({"action": "shell", "arg": "git diff --check"}, workspace)["ok"]
    assert not module.execute(
        {"action": "shell", "arg": "curl https://example.com"}, workspace
    )["ok"]
    assert not module.execute(
        {"action": "write", "arg": "../escape.py", "content": "pass"}, workspace
    )["ok"]
    assert not module.execute(
        {"action": "write", "arg": "net.py", "content": "import requests"}, workspace
    )["ok"]
    timeout = module.execute({"action": "shell", "arg": "timeout-case"}, workspace)
    assert timeout["error_type"] == "timeout"
    with pytest.raises(RuntimeError, match="boom"):
        module.execute({"action": "shell", "arg": "exception-case"}, workspace)

    events = [json.loads(line) for line in Path(namespace["POLICY_LOG_PATH"]).read_text().splitlines()]
    decisions = [event for event in events if event["event"] == "action_decision"]
    results = [event for event in events if event["event"] == "action_result"]
    assert len(decisions) == 7
    assert len(results) == 7
    assert any(event["decision"] == "deny" and event["reason"] == "url" for event in decisions)
    assert any(event["error_type"] == "timeout" for event in results)
    assert any(event["error_type"] == "exception:RuntimeError" for event in results)
    assert all("transient-secret" not in json.dumps(event) for event in events)


@pytest.mark.parametrize(
    "command",
    [
        "..",
        "cd ..",
        "cd   ..",
        "cd ../sibling",
        r"cd ..\sibling",
        "cd package/../sibling",
        r"cd package\..\sibling",
        "cd './..'",
    ],
)
def test_launcher_denies_parent_traversal_path_segments(tmp_path, command):
    namespace = _launcher_namespace(tmp_path)

    reason = namespace["_guard_reason"](
        {"action": "shell", "arg": command}, str(namespace["WORKSPACE"])
    )

    assert reason == "parent_traversal"


@pytest.mark.parametrize(
    "command",
    [
        "echo ...",
        "printf foo..bar",
        "pytest tests/test_parent_paths.py -q",
        "python -c 'print(1.25)'",
    ],
)
def test_launcher_parent_guard_ignores_nonsegment_double_dots(tmp_path, command):
    namespace = _launcher_namespace(tmp_path)

    reason = namespace["_guard_reason"](
        {"action": "shell", "arg": command}, str(namespace["WORKSPACE"])
    )

    assert reason is None


class FakeCatalogResponse:
    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self._body


def test_endpoint_catalog_preflight_is_retained_and_exact(tmp_path):
    model = "google/gemma-4-31b-it"
    dated_model = "google/gemma-4-31b-it-20260402"
    captured = {}
    payload = {
        "data": {
            "id": model,
            "endpoints": [
                {
                    "name": f"SiliconFlow | {dated_model}",
                    "provider_name": "SiliconFlow",
                    "model_id": model,
                    "tag": "siliconflow/fp8",
                    "quantization": "fp8",
                    "status": 0,
                },
                {
                    "name": f"Other | {dated_model}",
                    "provider_name": "Other",
                    "model_id": model,
                },
            ],
        }
    }

    def request_get(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return FakeCatalogResponse(payload)

    output = tmp_path / adapter.ENDPOINT_CATALOG_PREFLIGHT
    result = adapter.retained_endpoint_catalog_preflight(
        model,
        "siliconflow",
        (dated_model,),
        "secret-key",
        output,
        request_get=request_get,
    )

    assert result["valid"] is True
    assert result["outcome_bearing_model_call"] is False
    assert result["matches"] == [
        {
            "endpoint_name": f"SiliconFlow | {dated_model}",
            "model_id": model,
            "provider_name": "SiliconFlow",
            "quantization": "fp8",
            "served_model": dated_model,
            "status": 0,
            "tag": "siliconflow/fp8",
        }
    ]
    assert captured == {
        "url": f"https://openrouter.ai/api/v1/models/{model}/endpoints",
        "authorization": "Bearer secret-key",
        "timeout": 20,
    }
    assert "secret-key" not in output.read_text()


def test_endpoint_catalog_preflight_blocks_before_inference_on_route_miss(tmp_path):
    model = "google/gemma-4-31b-it"
    output = tmp_path / adapter.ENDPOINT_CATALOG_PREFLIGHT

    with pytest.raises(ValueError, match="found 0"):
        adapter.retained_endpoint_catalog_preflight(
            model,
            "siliconflow",
            ("google/gemma-4-31b-it-20260402",),
            "secret-key",
            output,
            request_get=lambda *_args, **_kwargs: FakeCatalogResponse(
                {"data": {"id": model, "endpoints": []}}
            ),
        )

    record = json.loads(output.read_text())
    assert record["valid"] is False
    assert record["outcome_bearing_model_call"] is False
    assert "secret-key" not in output.read_text()


def test_agent_copies_pinned_source_and_passes_full_prompt_by_file(tmp_path):
    agent, cm, source = _agent(tmp_path)
    prompt = "Implement the feature safely; do not expand $(shell).\n" + "x" * 4096

    assert agent.pre_run_setup(object(), object(), tmp_path / "infer.log") is True
    assert agent.prepare_run(object(), prompt, tmp_path / "infer.log") is True
    command = agent.get_run_command("this argument must not be interpolated")

    assert cm.files[adapter.ASKME_PATH] == source.read_bytes()
    assert b"secret-key" == cm.files[adapter.CREDENTIAL_PATH].strip()
    assert b"OPENROUTER_API_KEY" in cm.files[adapter.LAUNCHER_PATH]
    assert cm.files[adapter.PROMPT_PATH].decode() == prompt
    manifest = json.loads(cm.files[adapter.ADAPTER_MANIFEST_PATH])
    assert manifest["prompt_chars"] == len(prompt)
    assert manifest["goal_context_chars"] == len(prompt)
    assert manifest["askme_sha256"] == adapter.sha256_file(source)
    assert manifest["allow_provider_fallbacks"] is False
    assert manifest["network_policy_requested"] == "deny"
    assert manifest["container_egress_isolated"] is False
    assert manifest["limits"] == {
        "max_planning_attempts": 3,
        "max_tasks_per_plan": 10,
        "max_steps_per_task_attempt": 10,
        "max_task_local_replans": 1,
        "max_task_attempts": 2,
    }
    assert "python3 -c 'import requests'" in agent.install_script
    assert "command -v timeout" in agent.install_script
    assert "timeout --signal=TERM --kill-after=15s 3540s" in command
    assert "python3 /installed-agent/askme-launcher.py" in command
    assert "--prompt-file /installed-agent/task-prompt.txt" in command
    assert "--working-dir /testbed" in command
    assert "--result-json /agent-logs/askme-result.json" in command
    assert "--max-replans 3" in command
    assert "--max-tasks 10" in command
    assert "--max-steps 10" in command
    assert f"--goal-context-chars {len(prompt)}" in command
    assert prompt not in command
    assert "secret-key" not in agent.get_env_setup_script()


def test_agent_rejects_source_changed_after_snapshot_was_pinned(tmp_path):
    agent, _cm, source = _agent(tmp_path)
    source.write_text("print('changed')\n", encoding="utf-8")

    assert agent.pre_run_setup(object(), object(), tmp_path / "infer.log") is False
    assert "changed after adapter initialization" in agent.logger.errors[-1]


def test_post_run_preserves_logs_and_requires_complete_structured_result(tmp_path):
    agent, cm, _source_path = _agent(tmp_path)
    attempt_dir = tmp_path / "run" / "attempt-1"
    attempt_dir.mkdir(parents=True)
    infer_log = attempt_dir / "infer.log"
    infer_log.write_text("FeatureBench log\n", encoding="utf-8")
    cm.files.update(
        {
            adapter.RESULT_PATH: b'{"status":"complete"}\n',
            adapter.RUN_LOG_PATH: b'{"event":"run_end"}\n',
            adapter.POLICY_LOG_PATH: b'{"event":"launcher_start"}\n',
            adapter.STDOUT_LOG_PATH: b"All tasks complete.\n",
            adapter.ADAPTER_MANIFEST_PATH: b'{"schema_version":1}\n',
            adapter.PROMPT_PATH: b"the exact prompt",
        }
    )

    assert agent.post_run_hook(object(), infer_log) is True
    assert (attempt_dir / "askme-result.json").is_file()
    assert (attempt_dir / "askme-run.jsonl").is_file()
    assert (attempt_dir / "askme-stdout.log").is_file()
    assert (attempt_dir / "askme-adapter.json").is_file()
    assert (attempt_dir / "askme-prompt.txt").read_text() == "the exact prompt"

    cm.files[adapter.RESULT_PATH] = b'{"status":"exhausted"}\n'
    assert agent.post_run_hook(object(), infer_log) is False


def test_registration_is_temporary_and_delegates_other_agents(tmp_path):
    source = _source(tmp_path)
    agent_class = adapter.build_askme_agent_class(
        FakeBaseAgent, source, "secret", inner_timeout=3540
    )
    calls = []

    def original(agent_name, **kwargs):
        calls.append((agent_name, kwargs))
        return "original-agent"

    run_infer_module = SimpleNamespace(get_agent=original)
    cm = FakeContainerManager()
    env = adapter.strict_openrouter_env("model/id", "siliconflow")

    with adapter.registered_askme_agent(run_infer_module, agent_class):
        askme_agent = run_infer_module.get_agent(
            "askme", container_manager=cm, env_vars=env, logger=FakeLogger()
        )
        assert askme_agent.name == "askme"
        assert run_infer_module.get_agent("codex", marker=True) == "original-agent"

    assert run_infer_module.get_agent is original
    assert calls == [("codex", {"marker": True})]


def test_run_canary_uses_official_runner_shape_without_persisting_key(tmp_path, monkeypatch):
    source = _source(tmp_path)
    featurebench_root = tmp_path / "FeatureBench"
    featurebench_root.mkdir()
    dataset_path = tmp_path / "FeatureBench-dataset"
    dataset_path.mkdir()
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text("{}\n", encoding="utf-8")
    output_root = tmp_path / "runs"
    records = {}

    class FakeInferConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            records["config"] = kwargs

    run_infer_module = SimpleNamespace(
        get_agent=lambda agent_name, **kwargs: (agent_name, kwargs)
    )
    original_get_agent = run_infer_module.get_agent

    class FakeRunner:
        def __init__(self, config, config_path):
            records["config_path_text"] = Path(config_path).read_text()
            self.config = config
            self.agent_env_vars = {"OPENROUTER_ALLOW_FALLBACKS": "stale"}
            self.output_dir = config.output_dir / "frozen-run"
            self.output_dir.mkdir(parents=True)

        def run(self):
            records.setdefault("events", []).append("runner")
            records["agent"] = run_infer_module.get_agent(
                "askme",
                container_manager=FakeContainerManager(),
                env_vars=self.agent_env_vars,
                logger=FakeLogger(),
                model=self.config.model,
            )
            return 0

    api = SimpleNamespace(
        BaseAgent=FakeBaseAgent,
        InferConfig=FakeInferConfig,
        InferenceRunner=FakeRunner,
        run_infer_module=run_infer_module,
    )
    settings = adapter.CanarySettings(
        featurebench_root=featurebench_root,
        featurebench_revision="featurebench-sha",
        askme_path=source,
        dataset_path=dataset_path,
        dataset_revision="dataset-sha",
        output_dir=output_root,
        task_id="repo.feature.lv1",
        model="qwen/qwen3.6-27b",
        askme_revision="featurebench-sha",
        protocol_path=protocol_path,
        expected_served_models=("qwen/qwen3.6-27b-20260422",),
        cache_dir=tmp_path / "cache",
    )
    monkeypatch.setattr(adapter, "_git_revision", lambda _path: "featurebench-sha")
    monkeypatch.setattr(adapter, "_git_is_dirty", lambda _path: False)
    monkeypatch.setattr(
        adapter,
        "_validate_protocol_settings",
        lambda _settings: {
            "sources": {
                "askme": {
                    "adapter_code_revision": "a" * 40,
                    "code_files": {"askme.py": adapter.sha256_file(source)},
                }
            }
        },
    )
    monkeypatch.setattr(
        adapter,
        "_audit_retained_run",
        lambda *_args: {
            "infrastructure_valid": True,
            "qualification_valid": True,
        },
    )

    def fake_catalog_preflight(model, provider, expected, api_key, output_path):
        records.setdefault("events", []).append("catalog_preflight")
        records["catalog_preflight"] = {
            "model": model,
            "provider": provider,
            "expected": expected,
            "api_key": api_key,
            "output_path": output_path,
        }
        output_path.write_text('{"valid":true}\n', encoding="utf-8")
        return {"valid": True}

    monkeypatch.setattr(
        adapter,
        "retained_endpoint_catalog_preflight",
        fake_catalog_preflight,
    )

    exit_code, run_dir = adapter.run_canary(settings, "secret-key", api)

    assert exit_code == 0
    assert run_dir == output_root / "frozen-run"
    assert records["config"]["agent"] == "askme"
    assert records["config"]["dataset"] == str(dataset_path.resolve())
    assert records["config"]["task_ids"] == ["repo.feature.lv1"]
    assert records["config"]["n_concurrent"] == 1
    assert records["config"]["n_attempts"] == 1
    assert records["config"]["without_interface_descriptions"] is False
    assert records["config"]["white_box"] is False
    assert isinstance(records["agent"], FakeBaseAgent)
    assert records["events"] == ["catalog_preflight", "runner"]
    assert records["catalog_preflight"]["expected"] == (
        "qwen/qwen3.6-27b-20260422",
    )
    assert records["catalog_preflight"]["output_path"] == (
        run_dir / adapter.ENDPOINT_CATALOG_PREFLIGHT
    )
    assert run_infer_module.get_agent is original_get_agent
    assert "secret-key" not in records["config_path_text"]
    provenance_text = (run_dir / "askme-canary.json").read_text()
    assert "secret-key" not in provenance_text
    provenance = json.loads(provenance_text)
    assert provenance["featurebench_revision"] == "featurebench-sha"
    assert provenance["featurebench_git_dirty"] is False
    assert provenance["dataset_revision"] == "dataset-sha"
    assert provenance["dataset_path"] == str(dataset_path.resolve())
    assert provenance["description"] == "AskMe-adapted FeatureBench one-task canary"
    assert provenance["goal_context"] == "full FeatureBench problem_statement"
    assert provenance["endpoint_catalog_preflight"] == {
        "required": True,
        "relative_path": adapter.ENDPOINT_CATALOG_PREFLIGHT,
        "timing": "immediately_before_inference_runner",
        "outcome_bearing_model_call": False,
    }


def test_run_canary_rejects_wrong_or_dirty_featurebench_checkout(tmp_path, monkeypatch):
    source = _source(tmp_path)
    featurebench_root = tmp_path / "FeatureBench"
    featurebench_root.mkdir()
    dataset_path = tmp_path / "FeatureBench-dataset"
    dataset_path.mkdir()
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text("{}\n", encoding="utf-8")
    settings = adapter.CanarySettings(
        featurebench_root=featurebench_root,
        featurebench_revision="expected-sha",
        askme_path=source,
        dataset_path=dataset_path,
        dataset_revision="dataset-sha",
        output_dir=tmp_path / "runs",
        task_id="repo.feature.lv1",
        model="model/id",
        askme_revision="expected-sha",
        protocol_path=protocol_path,
        expected_served_models=("model/id",),
    )

    monkeypatch.setattr(adapter, "_git_revision", lambda _path: "wrong-sha")
    with pytest.raises(ValueError, match="revision mismatch"):
        adapter.run_canary(settings, "secret-key", SimpleNamespace())

    monkeypatch.setattr(adapter, "_git_revision", lambda _path: "expected-sha")
    monkeypatch.setattr(adapter, "_git_is_dirty", lambda _path: True)
    with pytest.raises(ValueError, match="FeatureBench checkout must be clean"):
        adapter.run_canary(settings, "secret-key", SimpleNamespace())
