import importlib.util
import json
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
    agent_class = adapter.build_askme_agent_class(FakeBaseAgent, source)
    cm = FakeContainerManager()
    strict_env = adapter.strict_openrouter_env(
        "qwen/qwen3.6-27b",
        "siliconflow",
        "secret-key",
    )
    if env:
        strict_env.update(env)
    return agent_class(cm, strict_env, FakeLogger()), cm, source


def test_strict_openrouter_environment_disables_fallbacks_and_agent_network():
    env = adapter.strict_openrouter_env("model/id", "siliconflow", "secret")

    assert env["LLM_BACKEND"] == "openrouter"
    assert env["OPENROUTER_ALLOW_FALLBACKS"] == "0"
    assert env["OPENROUTER_REQUIRE_PARAMETERS"] == "1"
    assert env["ALLOW_NETWORK"] == "0"
    assert env["ALLOW_SYSTEM_INSTALLS"] == "0"
    assert env["AGENT_RUN_LOG"] == "/agent-logs/askme-run.jsonl"
    adapter.validate_strict_env(env)

    env["OPENROUTER_ALLOW_FALLBACKS"] = "1"
    with pytest.raises(ValueError, match="OPENROUTER_ALLOW_FALLBACKS"):
        adapter.validate_strict_env(env)

    env = adapter.strict_openrouter_env("model/id", "siliconflow", "secret")
    env["AGENT_REASONING_POLICY"] = "off"
    with pytest.raises(ValueError, match="AGENT_REASONING_POLICY"):
        adapter.validate_strict_env(env)


@pytest.mark.parametrize("field", ["model", "provider", "api_key"])
def test_strict_openrouter_environment_requires_route_fields(field):
    values = {"model": "model/id", "provider": "siliconflow", "api_key": "secret"}
    values[field] = ""
    with pytest.raises(ValueError):
        adapter.strict_openrouter_env(**values)


def test_strict_openrouter_environment_rejects_automatic_provider_route():
    with pytest.raises(ValueError, match="must be pinned"):
        adapter.strict_openrouter_env("model/id", "auto", "secret")


def test_agent_copies_pinned_source_and_passes_full_prompt_by_file(tmp_path):
    agent, cm, source = _agent(tmp_path)
    prompt = "Implement the feature safely; do not expand $(shell).\n" + "x" * 4096

    assert agent.pre_run_setup(object(), object(), tmp_path / "infer.log") is True
    assert agent.prepare_run(object(), prompt, tmp_path / "infer.log") is True
    command = agent.get_run_command("this argument must not be interpolated")

    assert cm.files[adapter.ASKME_PATH] == source.read_bytes()
    assert cm.files[adapter.PROMPT_PATH].decode() == prompt
    manifest = json.loads(cm.files[adapter.ADAPTER_MANIFEST_PATH])
    assert manifest["prompt_chars"] == len(prompt)
    assert manifest["goal_context_chars"] == len(prompt)
    assert manifest["askme_sha256"] == adapter.sha256_file(source)
    assert manifest["allow_provider_fallbacks"] is False
    assert manifest["allow_agent_network_actions"] is False
    assert manifest["limits"] == {"max_replans": 3, "max_tasks": 10, "max_steps": 10}
    assert "python3 -c 'import requests'" in agent.install_script
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
    agent_class = adapter.build_askme_agent_class(FakeBaseAgent, source)
    calls = []

    def original(agent_name, **kwargs):
        calls.append((agent_name, kwargs))
        return "original-agent"

    run_infer_module = SimpleNamespace(get_agent=original)
    cm = FakeContainerManager()
    env = adapter.strict_openrouter_env("model/id", "siliconflow", "secret")

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
        cache_dir=tmp_path / "cache",
    )
    monkeypatch.setattr(adapter, "_git_revision", lambda _path: "featurebench-sha")
    monkeypatch.setattr(adapter, "_git_is_dirty", lambda _path: False)

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


def test_run_canary_rejects_wrong_or_dirty_featurebench_checkout(tmp_path, monkeypatch):
    source = _source(tmp_path)
    featurebench_root = tmp_path / "FeatureBench"
    featurebench_root.mkdir()
    dataset_path = tmp_path / "FeatureBench-dataset"
    dataset_path.mkdir()
    settings = adapter.CanarySettings(
        featurebench_root=featurebench_root,
        featurebench_revision="expected-sha",
        askme_path=source,
        dataset_path=dataset_path,
        dataset_revision="dataset-sha",
        output_dir=tmp_path / "runs",
        task_id="repo.feature.lv1",
        model="model/id",
    )

    monkeypatch.setattr(adapter, "_git_revision", lambda _path: "wrong-sha")
    with pytest.raises(ValueError, match="revision mismatch"):
        adapter.run_canary(settings, "secret-key", SimpleNamespace())

    monkeypatch.setattr(adapter, "_git_revision", lambda _path: "expected-sha")
    monkeypatch.setattr(adapter, "_git_is_dirty", lambda _path: True)
    with pytest.raises(ValueError, match="FeatureBench checkout must be clean"):
        adapter.run_canary(settings, "secret-key", SimpleNamespace())
