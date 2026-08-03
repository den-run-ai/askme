"""Contract tests for the public structured-run API (issue #40).

Covers run_result() composition, the immutable RunConfig, injected
dependencies, workspace ownership, the CLI --result-json contract, and the
structured task-replan return value.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from _test_support import mock_response

import askme
from actions import ActionExecutor, ActionResult
from askme import (
    PLANNER_MAX_TOKENS,
    LLMClient,
    LLMSettings,
    RunConfig,
    RunDependencies,
    RunWorkspace,
    run,
    run_result,
)

PLAN_DONE = [{"tasks": ["greet"]}, {"action": "done"}]


class ScriptedClient:
    """Duck-typed LLM dependency: scripted decoded replies, no settings."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def ask(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return self.replies.pop(0)


class ScriptedExecutor:
    """Duck-typed action dependency recording every dispatched action."""

    def __init__(self):
        self.dispatched = []

    def dispatch(self, action):
        self.dispatched.append(dict(action))
        return ActionResult(ok=True, output="scripted ok")


def _quiet_deps(**kwargs):
    """Dependencies that keep scripted runs off stdout and the module log."""
    kwargs.setdefault("log_sink", lambda msg: None)
    kwargs.setdefault("event_sink", lambda event: None)
    return RunDependencies(**kwargs)


def _openrouter_settings(**overrides):
    fields = {
        "backend": "openrouter",
        "api": "https://openrouter.ai/api/v1/chat/completions",
        "model": "vendor/model-a",
        "api_key": "sk-or-test-secret",
        "provider": "Parasail",
        "allow_fallbacks": True,
        "require_parameters": False,
        "reasoning_effort": "",
        "timeout": 120,
    }
    fields.update(overrides)
    return LLMSettings(**fields)


def _local_settings(**overrides):
    fields = {
        "backend": "local",
        "api": "http://localhost:8080/v1/chat/completions",
        "model": "local-model-b",
        "api_key": "",
        "provider": "",
        "allow_fallbacks": True,
        "require_parameters": False,
        "reasoning_effort": "",
        "timeout": 120,
    }
    fields.update(overrides)
    return LLMSettings(**fields)


def _scripted_post(transcript, replies):
    """requests.post stand-in recording the target URL and body model."""
    remaining = list(replies)

    def post(url, json=None, headers=None, timeout=None):
        transcript.append(
            {
                "url": url,
                "model": json["model"],
                "max_tokens": json["max_tokens"],
                "authorization": (headers or {}).get("Authorization", ""),
            }
        )
        return mock_response(remaining.pop(0))

    return post


class TestRunResultWorkspace:
    @patch("askme.ask_llm", side_effect=list(PLAN_DONE))
    def test_supplied_workspace_is_recorded_and_never_created(self, mock_llm, tmp_path):
        result = run_result("greet", working_dir=str(tmp_path))
        assert result["status"] == "complete"
        assert result["workspace"] == {"path": str(tmp_path), "created": False}

    @patch("askme.ask_llm", side_effect=list(PLAN_DONE))
    def test_temporary_workspace_is_created_and_recorded(self, mock_llm):
        result = run_result("greet")
        workspace = result["workspace"]
        try:
            assert workspace["created"] is True
            assert Path(workspace["path"]).name.startswith("askme_")
            assert Path(workspace["path"]).is_dir()
        finally:
            RunWorkspace(**workspace).cleanup()
        assert not Path(workspace["path"]).exists()

    def test_cleanup_never_removes_a_supplied_directory(self, tmp_path):
        workspace = RunWorkspace.resolve(str(tmp_path))
        assert workspace == RunWorkspace(path=str(tmp_path), created=False)
        workspace.cleanup()
        assert tmp_path.is_dir()

    @patch("askme.ask_llm", side_effect=list(PLAN_DONE))
    def test_run_wrapper_translates_the_structured_status(self, mock_llm, tmp_path):
        assert run("greet", working_dir=str(tmp_path)) is True

    @patch(
        "askme.ask_llm",
        side_effect=[{"tasks": ["work"]}, {"action": "fail", "reasoning": "nope"}, {"task": ""}],
    )
    def test_failed_run_reports_exhaustion(self, mock_llm, tmp_path):
        result = run_result(
            "work",
            working_dir=str(tmp_path),
            config=RunConfig(max_replans=1, max_tasks=1, max_steps=1),
        )
        assert result["status"] == "exhausted"

    @patch("askme.ask_llm", side_effect=askme.LLMTransportError("backend down"))
    def test_run_wrapper_translates_failure(self, mock_llm, tmp_path):
        assert run("work", working_dir=str(tmp_path)) is False


class TestRunConfigResolution:
    @patch("askme.ask_llm", side_effect=list(PLAN_DONE))
    def test_default_config_reports_module_configuration(self, mock_llm, tmp_path):
        result = run_result("greet", working_dir=str(tmp_path))
        config = result["config"]
        assert config["backend"] == askme.LLM_BACKEND
        assert config["model"] == askme.MODEL
        assert config["policy"] == askme.get_policy()
        assert config["reasoning_policy"] == askme.DEFAULT_REASONING_POLICY
        assert config["limits"] == {
            "max_replans": askme.MAX_REPLANS,
            "max_tasks": askme.MAX_TASKS,
            "max_steps": askme.MAX_STEPS,
            "goal_context_chars": askme.GOAL_CONTEXT_CHARS,
        }

    def test_pinned_policy_reaches_planner_state_without_touching_globals(self, tmp_path):
        client = ScriptedClient(list(PLAN_DONE))
        before = askme.get_policy()
        result = run_result(
            "greet",
            working_dir=str(tmp_path),
            config=RunConfig(allow_system_installs=True, allow_network=False),
            dependencies=_quiet_deps(llm_client=client),
        )
        assert result["state"]["policy"] == {
            "allow_system_installs": True,
            "allow_network": False,
        }
        assert result["config"]["policy"] == {
            "allow_system_installs": True,
            "allow_network": False,
        }
        assert askme.get_policy() == before

    def test_invalid_reasoning_policy_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="reasoning_policy"):
            run_result(
                "greet",
                working_dir=str(tmp_path),
                config=RunConfig(reasoning_policy="bogus"),
            )

    def test_invalid_goal_context_budget_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="goal_context_chars"):
            run_result(
                "greet",
                working_dir=str(tmp_path),
                config=RunConfig(goal_context_chars=0),
            )

    @pytest.mark.parametrize("budget", ["max_replans", "max_tasks", "max_steps"])
    def test_non_positive_run_budgets_are_rejected(self, tmp_path, budget):
        """The public config path matches the CLI's positive-budget contract."""
        with pytest.raises(ValueError, match=budget):
            run_result(
                "greet",
                working_dir=str(tmp_path),
                config=RunConfig(**{budget: 0}),
            )

    def test_invalid_config_never_leaks_a_temporary_workspace(self):
        created = []
        real_mkdtemp = tempfile.mkdtemp

        def spy_mkdtemp(prefix=""):
            path = real_mkdtemp(prefix=prefix)
            created.append(path)
            return path

        with patch("askme.tempfile.mkdtemp", side_effect=spy_mkdtemp):
            with pytest.raises(ValueError, match="goal_context_chars"):
                run_result("greet", config=RunConfig(goal_context_chars=0))
        assert len(created) == 1
        assert not Path(created[0]).exists()

    def test_from_env_derives_llm_policy_and_reasoning(self):
        env = {
            "LLM_BACKEND": "openrouter",
            "OPENROUTER_MODEL": "vendor/env-model",
            "ALLOW_SYSTEM_INSTALLS": "1",
            "ALLOW_NETWORK": "0",
            "AGENT_REASONING_POLICY": "off",
        }
        config = RunConfig.from_env(env)
        assert config.llm == LLMSettings.from_env(env)
        assert config.llm.model == "vendor/env-model"
        assert config.allow_system_installs is True
        assert config.allow_network is False
        assert config.reasoning_policy == "off"
        assert config.max_replans is None

    def test_from_env_rejects_unknown_reasoning_policy(self):
        with pytest.raises(ValueError, match="AGENT_REASONING_POLICY"):
            RunConfig.from_env({"AGENT_REASONING_POLICY": "sometimes"})


class TestInjectedDependencies:
    def test_scripted_llm_and_executor_run_without_patching(self, tmp_path, capsys):
        client = ScriptedClient(
            [
                {"tasks": ["run the greeting"]},
                {"action": "shell", "arg": "echo hi"},
                {"action": "done"},
            ]
        )
        executor = ScriptedExecutor()
        lines = []
        events = []
        deps = RunDependencies(
            llm_client=client,
            action_executor=executor,
            clock=lambda: 1234.5,
            log_sink=lines.append,
            event_sink=events.append,
        )
        result = run_result("run the greeting", working_dir=str(tmp_path), dependencies=deps)

        assert result["status"] == "complete"
        assert executor.dispatched == [
            {
                "action": "shell",
                "arg": "echo hi",
                "content": "",
                "reasoning": "",
                "find": "",
                "replace": "",
            }
        ]
        assert client.calls[0]["max_tokens"] == PLANNER_MAX_TOKENS
        # The injected sinks own all output: nothing reaches stdout.
        assert capsys.readouterr().out == ""
        assert any(line.startswith("Prompt:") for line in lines)
        event_names = [event["event"] for event in events]
        assert event_names[0] == "run_start"
        assert "plan" in event_names
        assert "step" in event_names
        assert event_names[-1] == "run_end"
        # The injected constant clock makes every wall reading deterministic.
        assert events[-1]["wall_s"] == 0.0

    def test_two_pinned_clients_coexist_without_global_state(self, tmp_path):
        before = (askme.LLM_BACKEND, askme.API, askme.MODEL)
        transcript_a = []
        transcript_b = []
        client_a = LLMClient(
            settings=_openrouter_settings(),
            post=_scripted_post(transcript_a, PLAN_DONE),
            log_sink=lambda msg: None,
            event_sink=lambda event: None,
        )
        client_b = LLMClient(
            settings=_local_settings(),
            post=_scripted_post(transcript_b, PLAN_DONE),
            log_sink=lambda msg: None,
            event_sink=lambda event: None,
        )

        result_a = run_result(
            "greet",
            working_dir=str(tmp_path / "a"),
            dependencies=_quiet_deps(llm_client=client_a),
        )
        result_b = run_result(
            "greet",
            working_dir=str(tmp_path / "b"),
            dependencies=_quiet_deps(llm_client=client_b),
        )

        assert result_a["status"] == "complete"
        assert result_b["status"] == "complete"
        assert {entry["url"] for entry in transcript_a} == {_openrouter_settings().api}
        assert {entry["model"] for entry in transcript_a} == {"vendor/model-a"}
        assert {entry["url"] for entry in transcript_b} == {_local_settings().api}
        assert {entry["model"] for entry in transcript_b} == {"local-model-b"}
        assert result_a["config"]["backend"] == "openrouter"
        assert result_a["config"]["model"] == "vendor/model-a"
        assert result_b["config"]["backend"] == "local"
        assert result_b["config"]["model"] == "local-model-b"
        assert (askme.LLM_BACKEND, askme.API, askme.MODEL) == before

    @pytest.mark.parametrize(
        ("settings", "expected_step_tokens"),
        [(_openrouter_settings(), 4096), (_local_settings(), 256)],
        ids=["openrouter", "local"],
    )
    def test_pinned_config_builds_its_own_client_and_budgets(
        self, tmp_path, settings, expected_step_tokens
    ):
        """config.llm alone pins the transport target and the step budget.

        The executor budget follows the pinned backend regardless of which
        backend the module-level STEP_TOKENS was derived for at import."""
        transcript = []
        replies = [{"tasks": ["work"]}, {"action": "done"}]
        with patch("askme.requests.post", side_effect=_scripted_post(transcript, replies)):
            result = run_result(
                "work",
                working_dir=str(tmp_path),
                config=RunConfig(llm=settings),
                dependencies=_quiet_deps(),
            )
        assert result["status"] == "complete"
        assert {entry["url"] for entry in transcript} == {settings.api}
        assert {entry["model"] for entry in transcript} == {settings.model}
        assert transcript[1]["max_tokens"] == expected_step_tokens

    def test_result_and_events_never_carry_the_api_key(self, tmp_path):
        transcript = []
        events = []
        settings = _openrouter_settings()
        client = LLMClient(
            settings=settings,
            post=_scripted_post(transcript, PLAN_DONE),
            log_sink=lambda msg: None,
            event_sink=events.append,
        )
        result = run_result(
            "greet",
            working_dir=str(tmp_path),
            dependencies=_quiet_deps(llm_client=client, event_sink=events.append),
        )
        assert transcript[0]["authorization"] == f"Bearer {settings.api_key}"
        assert settings.api_key not in json.dumps(result, default=str)
        assert settings.api_key not in json.dumps(events, default=str)

    def test_injected_sinks_alone_capture_llm_telemetry(self, tmp_path, capsys):
        """Sinks without a pinned client still own the LLM retry/usage output."""
        lines = []
        events = []
        usage = {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}
        responses = [mock_response(reply, usage=usage) for reply in PLAN_DONE]
        with patch("askme.requests.post", side_effect=responses):
            result = run_result(
                "greet",
                working_dir=str(tmp_path),
                dependencies=RunDependencies(log_sink=lines.append, event_sink=events.append),
            )
        assert result["status"] == "complete"
        assert capsys.readouterr().out == ""
        assert any(line.startswith("  tokens:") for line in lines)
        event_names = {event["event"] for event in events}
        assert {"reasoning_decision", "tokens", "run_start", "run_end"} <= event_names

    def test_injected_executor_must_name_the_run_workspace(self, tmp_path):
        executor = ActionExecutor(str(tmp_path / "elsewhere"))
        with pytest.raises(ValueError, match="action_executor"):
            run_result(
                "greet",
                working_dir=str(tmp_path),
                dependencies=RunDependencies(action_executor=executor),
            )

    def test_injected_executor_matching_the_workspace_is_accepted(self, tmp_path):
        client = ScriptedClient(list(PLAN_DONE))
        executor = ActionExecutor(str(tmp_path))
        result = run_result(
            "greet",
            working_dir=str(tmp_path),
            dependencies=_quiet_deps(llm_client=client, action_executor=executor),
        )
        assert result["status"] == "complete"

    def test_injected_builtin_executor_receives_strict_shell_envelope(self, tmp_path):
        client = ScriptedClient(
            [
                {"tasks": ["run greeting"]},
                {"action": "shell", "arg": "printf hello"},
                {"action": "done"},
            ]
        )
        executor = ActionExecutor(str(tmp_path))
        result = run_result(
            "run greeting",
            working_dir=str(tmp_path),
            dependencies=_quiet_deps(llm_client=client, action_executor=executor),
        )
        assert result["status"] == "complete"
        shell_step = result["state"]["all_steps"][0]
        assert shell_step["action"] == "shell"
        assert shell_step["ok"] is True

    def test_mismatched_executor_never_leaks_a_temporary_workspace(self):
        created = []
        real_mkdtemp = tempfile.mkdtemp

        def spy_mkdtemp(prefix=""):
            path = real_mkdtemp(prefix=prefix)
            created.append(path)
            return path

        executor = ActionExecutor(".")
        with patch("askme.tempfile.mkdtemp", side_effect=spy_mkdtemp):
            with pytest.raises(ValueError, match="action_executor"):
                run_result("greet", dependencies=RunDependencies(action_executor=executor))
        assert len(created) == 1
        assert not Path(created[0]).exists()

    def test_preflight_missing_tools_reach_the_injected_log_sink(self, tmp_path):
        client = ScriptedClient(list(PLAN_DONE))
        lines = []
        probe = {
            "platform": "linux",
            "arch": "x86_64",
            "working_dir": str(tmp_path),
            "available_tools": ["python3"],
            "missing_tools": ["go", "cargo"],
            "package_managers": [],
            "dir_listing": ["(empty)"],
        }
        with patch("askme.preflight_probe", return_value=probe):
            result = run_result(
                "greet",
                working_dir=str(tmp_path),
                dependencies=_quiet_deps(llm_client=client, log_sink=lines.append),
            )
        assert result["status"] == "complete"
        assert "Missing tools: ['go', 'cargo']" in lines

    def test_injected_log_sink_reaches_final_validation(self, tmp_path):
        lines = []
        client = ScriptedClient([{"tasks": ["greet"]}, {"action": "done"}, {"valid": True}])
        with patch.object(askme, "FINAL_VALIDATE", "always"):
            result = run_result(
                "greet",
                working_dir=str(tmp_path),
                dependencies=_quiet_deps(llm_client=client, log_sink=lines.append),
            )
        assert result["status"] == "complete"
        assert not client.replies  # the validator consumed the scripted verdict
        assert client.calls[-1]["reasoning_trigger"] == "final_validator"
        assert "  Validation passed." in lines

    def test_duplicate_failed_read_auto_fails_through_injected_sinks(self, tmp_path):
        """The stuck-read guard runs unchanged under injected dependencies."""
        client = ScriptedClient(
            [
                {"tasks": ["inspect missing.txt"]},
                {"action": "read", "arg": "missing.txt"},
                {"action": "read", "arg": "missing.txt"},
                {"task": ""},  # task-local replan rejected: empty
            ]
        )
        lines = []
        result = run_result(
            "inspect missing.txt",
            working_dir=str(tmp_path),
            config=RunConfig(max_replans=1, max_tasks=1, max_steps=3),
            dependencies=_quiet_deps(llm_client=client, log_sink=lines.append),
        )
        assert result["status"] == "exhausted"
        assert any("auto-fail (same read failed twice)" in line for line in lines)
        assert any(
            "[stuck_loop] read missing.txt failed twice" in e for e in result["state"]["errors"]
        )

    @patch("askme.execute")
    def test_failed_deterministic_retry_reports_through_injected_sinks(
        self, mock_execute, tmp_path
    ):
        """A compile repair whose retry still fails keeps the typed error."""
        src = tmp_path / "main.c"
        src.write_text('int main(){ printf("hi"); return 0; }\n')
        client = ScriptedClient(
            [
                {"tasks": ["compile main.c"]},
                {"action": "shell", "arg": "cc -o main main.c"},
                {"task": ""},  # task-local replan rejected: empty
            ]
        )
        compile_error = {
            "ok": False,
            "output": "main.c:1:13: error: implicit declaration of function 'printf'",
            "error_type": "compile_error",
        }
        mock_execute.side_effect = [
            dict(compile_error),  # model shell
            {"ok": True, "output": "Wrote main.c"},  # dispatched repair write
            dict(compile_error),  # deterministic retry
        ]
        lines = []
        result = run_result(
            "compile main.c",
            working_dir=str(tmp_path),
            config=RunConfig(max_replans=1, max_tasks=1, max_steps=1),
            dependencies=_quiet_deps(llm_client=client, log_sink=lines.append),
        )
        assert result["status"] == "exhausted"
        assert any(line.startswith("  -> FAIL deterministic retry:") for line in lines)
        # Repair action dispatched through the seam (issue #41), and repair
        # plus failed retry both recorded through the one recorder.
        repair_dispatch = mock_execute.call_args_list[1][0][0]
        assert repair_dispatch["action"] == "write"
        assert "#include <stdio.h>" in repair_dispatch["content"]
        assert [s["action"] for s in result["state"]["all_steps"]] == ["shell", "write", "shell"]
        assert any("[compile_error]" in e for e in result["state"]["errors"])

    def test_injected_executor_survives_successful_deterministic_repair(self, tmp_path):
        """Generated repair actions use the same injected dispatch seam."""
        source = 'int main(){ printf("hi"); return 0; }\n'
        (tmp_path / "main.c").write_text(source)
        client = ScriptedClient(
            [
                {"tasks": ["compile main.c"]},
                {"action": "shell", "arg": "cc -o main main.c"},
                {"action": "done"},
            ]
        )
        compile_error = {
            "ok": False,
            "output": "main.c:1:13: error: implicit declaration of function 'printf'",
            "error_type": "compile_error",
        }

        class RepairExecutor:
            def __init__(self):
                self.dispatched = []
                self.results = [
                    ActionResult.from_dict(compile_error),
                    ActionResult(ok=True, output="Wrote main.c"),
                    ActionResult(ok=True, output="compiled"),
                ]

            def dispatch(self, action):
                self.dispatched.append(dict(action))
                return self.results.pop(0)

        executor = RepairExecutor()
        result = run_result(
            "compile main.c",
            working_dir=str(tmp_path),
            config=RunConfig(max_tasks=1, max_steps=2),
            dependencies=_quiet_deps(llm_client=client, action_executor=executor),
        )

        assert result["status"] == "complete"
        assert [action["action"] for action in executor.dispatched] == [
            "shell",
            "write",
            "shell",
        ]
        repair = executor.dispatched[1]
        assert repair["arg"] == "main.c"
        assert repair["content"] == f"#include <stdio.h>\n{source}"
        assert executor.results == []

    def test_invalid_generated_repair_is_rejected_before_injected_executor(self, tmp_path):
        """A malformed repair proposal fails closed at the action boundary."""
        client = ScriptedClient(
            [
                {"tasks": ["compile main.c"]},
                {"action": "shell", "arg": "cc -o main main.c"},
                {"task": ""},  # task-local replan rejected: empty
            ]
        )

        class CompileErrorExecutor:
            def __init__(self):
                self.dispatched = []

            def dispatch(self, action):
                self.dispatched.append(dict(action))
                return ActionResult(
                    ok=False,
                    output="main.c:1:13: error: implicit declaration of function 'printf'",
                    error_type="compile_error",
                )

        executor = CompileErrorExecutor()
        invalid_repair = {"action": "write", "arg": "main.c"}  # missing content
        with patch("askme._compile_repair_action", return_value=invalid_repair):
            result = run_result(
                "compile main.c",
                working_dir=str(tmp_path),
                config=RunConfig(max_replans=1, max_tasks=1, max_steps=1),
                dependencies=_quiet_deps(llm_client=client, action_executor=executor),
            )

        assert result["status"] == "exhausted"
        assert [action["action"] for action in executor.dispatched] == ["shell"]
        assert any("[compile_error]" in error for error in result["state"]["errors"])


class TestTaskReplanContract:
    """The replan seam speaks TaskReplanResult only (issue #69): the legacy
    str/None/tuple coercion shim and its test-only shapes are removed."""

    def test_coercion_shim_is_gone(self):
        assert not hasattr(askme, "_coerce_task_replan")

    def test_reject_reason_reaches_the_run_log_event(self, tmp_path):
        """The typed rejection travels by return value into the JSONL record."""
        events = []
        client = ScriptedClient(
            [
                {"tasks": ["fix widget handling"]},
                {"action": "fail", "reasoning": "cannot"},
                {"task": "fix widget handling"},  # exact duplicate -> rejected
                {"tasks": []},  # full replan fails -> run exhausts
            ]
        )
        result = run_result(
            "fix widget handling",
            working_dir=str(tmp_path),
            config=RunConfig(max_replans=2, max_tasks=1, max_steps=1),
            dependencies=_quiet_deps(llm_client=client, event_sink=events.append),
        )
        assert result["status"] == "exhausted"
        replan_events = [event for event in events if event["event"] == "task_local_replan"]
        assert replan_events and replan_events[0]["ok"] is False
        assert replan_events[0]["reject_reason"] == "exact_duplicate"


class TestCliResultJsonContract:
    def _cli(self, tmp_path, *extra, result_name="result.json"):
        result_file = tmp_path / result_name
        argv = ["--result-json", str(result_file), *extra]
        return result_file, argv

    def test_success_with_supplied_workspace(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        result_file, argv = self._cli(tmp_path, "greet", "--working-dir", str(workspace))
        responses = [mock_response(reply) for reply in PLAN_DONE]
        with patch("askme.requests.post", side_effect=responses):
            exit_code = askme._main(argv)
        assert exit_code == 0
        written = json.loads(result_file.read_text())
        assert written["status"] == "complete"
        assert set(written) >= {"status", "state", "log", "config", "workspace"}
        assert written["workspace"] == {"path": str(workspace), "created": False}
        assert written["config"]["limits"]["max_steps"] == askme.MAX_STEPS
        assert "api_key" not in json.dumps(written)

    def test_success_with_temporary_workspace(self, tmp_path):
        result_file, argv = self._cli(tmp_path, "greet")
        responses = [mock_response(reply) for reply in PLAN_DONE]
        with patch("askme.requests.post", side_effect=responses):
            exit_code = askme._main(argv)
        assert exit_code == 0
        written = json.loads(result_file.read_text())
        workspace = written["workspace"]
        try:
            assert workspace["created"] is True
            assert Path(workspace["path"]).is_dir()
        finally:
            RunWorkspace(**workspace).cleanup()

    def test_failure_exits_one_and_records_exhaustion(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        result_file, argv = self._cli(
            tmp_path,
            "work",
            "--working-dir",
            str(workspace),
            "--max-replans",
            "1",
            "--max-tasks",
            "1",
            "--max-steps",
            "1",
        )
        responses = [
            mock_response({"tasks": ["work"]}),
            mock_response({"action": "fail", "reasoning": "cannot"}),
            mock_response({"task": ""}),  # task-local replan rejected: empty
        ]
        with patch("askme.requests.post", side_effect=responses):
            exit_code = askme._main(argv)
        assert exit_code == 1
        written = json.loads(result_file.read_text())
        assert written["status"] == "exhausted"
        assert written["config"]["limits"] == {
            "max_replans": 1,
            "max_tasks": 1,
            "max_steps": 1,
            "goal_context_chars": askme.GOAL_CONTEXT_CHARS,
        }

    @pytest.mark.parametrize(
        "argv",
        [
            [],  # no prompt at all
            ["", "--working-dir", "."],  # empty prompt
            ["greet", "--prompt-file", "also.txt"],  # both prompt sources
            ["greet", "--working-dir", "/nonexistent/askme-dir"],
            ["greet", "--max-steps", "0"],
            ["greet", "--max-replans", "many"],
        ],
    )
    def test_invalid_arguments_exit_two_without_running(self, argv):
        with patch("askme.run_result") as run_result_mock:
            with pytest.raises(SystemExit) as excinfo:
                askme._main(argv)
        assert excinfo.value.code == 2
        run_result_mock.assert_not_called()
