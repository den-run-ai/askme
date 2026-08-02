"""Planning tests: planner reasoning, preflight probe, execution policy,
command-aware timeouts, server config."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from _test_support import mock_response
from conftest import skip_no_llm

from askme import TASK_REPLAN_MAX_TOKENS, LLMTransportError, get_plan, get_step, replan_task, run

# --- Planner reasoning tests ---


class TestPlannerReasoning:
    """Verify planner uses thinking conditionally: off for first plan, on for replans."""

    def test_first_plan_no_thinking(self):
        """First plan (empty state) should pass think=False — thinking wastes token budget."""
        from askme import PLANNER_MAX_TOKENS

        captured = {}

        def capture_llm(messages, max_tokens=256, think=False, **kwargs):
            captured.update(max_tokens=max_tokens, think=think, timeout=kwargs.get("timeout"))
            return {"tasks": ["do something"]}

        with patch("askme.ask_llm", side_effect=capture_llm):
            get_plan("test request", {"completed_tasks": [], "errors": []})
        assert captured["think"] is False, "First plan should have think=False"
        assert captured["max_tokens"] == PLANNER_MAX_TOKENS, (
            f"Expected max_tokens={PLANNER_MAX_TOKENS}, got {captured['max_tokens']}"
        )
        assert captured["timeout"] is None, "First plan should use default timeout"

    def test_replan_has_thinking(self):
        """Replan (state has errors) should pass think=True and extended timeout."""
        from askme import LLM_TIMEOUT_REPLAN, PLANNER_MAX_TOKENS

        captured = {}

        def capture_llm(messages, max_tokens=256, think=False, **kwargs):
            captured.update(max_tokens=max_tokens, think=think, timeout=kwargs.get("timeout"))
            return {"tasks": ["retry something"]}

        state = {"completed_tasks": [], "errors": ["Task 'build' failed: missing header"]}
        with patch("askme.ask_llm", side_effect=capture_llm):
            get_plan("test request", state)
        assert captured["think"] is True
        assert captured["max_tokens"] == PLANNER_MAX_TOKENS
        assert captured["timeout"] == LLM_TIMEOUT_REPLAN

    def test_replan_with_completed_tasks_has_thinking(self):
        """Partial completion replan should still use think=True and extended timeout."""
        from askme import LLM_TIMEOUT_REPLAN, PLANNER_MAX_TOKENS

        captured = {}

        def capture_llm(messages, max_tokens=256, think=False, **kwargs):
            captured.update(max_tokens=max_tokens, think=think, timeout=kwargs.get("timeout"))
            return {"tasks": ["finish remaining"]}

        state = {"completed_tasks": ["create header"], "errors": ["compile failed"]}
        with patch("askme.ask_llm", side_effect=capture_llm):
            get_plan("test request", state)
        assert captured["think"] is True
        assert captured["max_tokens"] == PLANNER_MAX_TOKENS
        assert captured["timeout"] == LLM_TIMEOUT_REPLAN

    def test_system_plan_includes_hints(self):
        """Updated SYSTEM_PLAN should contain specificity guidance."""
        from askme import SYSTEM_PLAN

        assert "includes" in SYSTEM_PLAN
        assert "Relative filenames" in SYSTEM_PLAN
        assert "completed_tasks" in SYSTEM_PLAN

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "local")
    def test_first_plan_local_no_think_tag(self, mock_post):
        """First plan (local) should NOT prepend <|think|> — no thinking on first plan."""
        mock_post.return_value = mock_response({"tasks": ["do thing"]})
        get_plan("test", {"completed_tasks": [], "errors": []})
        call_body = mock_post.call_args_list[0][1]["json"]
        sys_content = call_body["messages"][0]["content"]
        assert not sys_content.startswith("<|think|>"), (
            f"First plan should not have <|think|> prefix, got: {sys_content[:50]}"
        )

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "local")
    def test_replan_local_uses_think_tag(self, mock_post):
        """Replan (local, errors present) should prepend <|think|> to system prompt."""
        mock_post.return_value = mock_response({"tasks": ["retry thing"]})
        get_plan("test", {"completed_tasks": [], "errors": ["compile failed"]})
        call_body = mock_post.call_args_list[0][1]["json"]
        sys_content = call_body["messages"][0]["content"]
        assert sys_content.startswith("<|think|>\n"), (
            f"Replan should have <|think|> prefix, got: {sys_content[:50]}"
        )

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_first_plan_openrouter_no_reasoning(self, mock_post):
        """First plan (OpenRouter) should explicitly disable reasoning."""
        mock_post.return_value = mock_response({"tasks": ["do thing"]})
        get_plan("test", {"completed_tasks": [], "errors": []})
        call_body = mock_post.call_args_list[0][1]["json"]
        assert call_body["reasoning"] == {"enabled": False}

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_replan_openrouter_uses_reasoning(self, mock_post):
        """Replan (OpenRouter, errors present) should include reasoning params."""
        mock_post.return_value = mock_response({"tasks": ["retry thing"]})
        get_plan("test", {"completed_tasks": [], "errors": ["task failed"]})
        call_body = mock_post.call_args_list[0][1]["json"]
        assert "reasoning" in call_body
        assert call_body["reasoning"]["enabled"] is True
        assert call_body["reasoning"]["effort"] == "medium"

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_replan_null_content_with_reasoning(self, mock_post):
        """Replan: reasoning exhausts token budget, content=null -> retry succeeds."""
        resp_null = MagicMock()
        resp_null.status_code = 200
        resp_null.json.return_value = {
            "choices": [{"message": {"content": None, "reasoning": "planning..."}}]
        }
        mock_post.side_effect = [resp_null, mock_response({"tasks": ["recovered task"]})]
        result = get_plan("test", {"completed_tasks": [], "errors": ["previous failure"]})
        assert "tasks" in result
        assert result["tasks"] == ["recovered task"]


# --- Preflight probe tests ---


class TestPreflightProbe:
    """Verify structured environment probing before first plan."""

    def test_probe_returns_platform(self, work_dir):
        from askme import preflight_probe

        env = preflight_probe(work_dir)
        assert "platform" in env
        assert env["platform"] in ("darwin", "linux", "windows")

    def test_probe_returns_arch(self, work_dir):
        from askme import preflight_probe

        env = preflight_probe(work_dir)
        assert "arch" in env
        assert len(env["arch"]) > 0

    def test_probe_returns_tools(self, work_dir):
        from askme import preflight_probe

        env = preflight_probe(work_dir)
        assert "available_tools" in env
        assert "missing_tools" in env
        assert "python3" in env["available_tools"]

    def test_probe_returns_dir_listing(self, work_dir):
        from askme import preflight_probe

        Path(work_dir, "test.txt").write_text("hi")
        env = preflight_probe(work_dir)
        assert "dir_listing" in env
        assert "test.txt" in env["dir_listing"]

    def test_probe_empty_dir(self, work_dir):
        from askme import preflight_probe

        env = preflight_probe(work_dir)
        assert env["dir_listing"] == ["(empty)"]

    def test_probe_returns_package_managers(self, work_dir):
        from askme import preflight_probe

        env = preflight_probe(work_dir)
        assert "package_managers" in env
        assert isinstance(env["package_managers"], list)

    def test_probe_uses_shutil_which(self):
        """Probe should use shutil.which (cross-platform), not subprocess which."""
        import shutil

        from askme import preflight_probe

        with patch.object(shutil, "which", return_value="/usr/bin/python3") as mock_which:
            preflight_probe("/tmp")
        assert mock_which.call_count > 0
        tool_calls = [c[0][0] for c in mock_which.call_args_list]
        assert "python3" in tool_calls

    def test_run_includes_environment_in_state(self, work_dir):
        """run() should call preflight_probe and include env in planner state."""
        captured = {}

        def capture_llm(messages, **kwargs):
            if "tasks" not in str(messages):
                return {"tasks": []}
            user_msg = messages[-1]["content"]
            captured["user_msg"] = user_msg
            return {"tasks": []}

        with patch("askme.ask_llm", side_effect=capture_llm):
            run("test", working_dir=work_dir)
        assert "environment" in captured.get("user_msg", ""), (
            "Planner should receive environment in state"
        )
        assert "platform" in captured["user_msg"]

    def test_run_includes_policy_in_state(self, work_dir):
        """run() should include policy in planner state."""
        captured = {}

        def capture_llm(messages, **kwargs):
            user_msg = messages[-1]["content"]
            captured["user_msg"] = user_msg
            return {"tasks": []}

        with patch("askme.ask_llm", side_effect=capture_llm):
            run("test", working_dir=work_dir)
        assert "policy" in captured.get("user_msg", ""), "Planner should receive policy in state"
        assert "allow_system_installs" in captured["user_msg"]


# --- Execution policy tests ---


class TestExecutionPolicy:
    """Verify capability/permission policy enforcement."""

    def test_policy_defaults(self):
        from askme import get_policy

        policy = get_policy()
        assert policy["allow_system_installs"] is False
        assert policy["allow_network"] is True

    def test_policy_env_override(self):
        import askme

        old = askme.ALLOW_SYSTEM_INSTALLS
        try:
            askme.ALLOW_SYSTEM_INSTALLS = True
            policy = askme.get_policy()
            assert policy["allow_system_installs"] is True
        finally:
            askme.ALLOW_SYSTEM_INSTALLS = old

    def test_executor_sees_policy(self):
        """get_step should include policy in slim state sent to LLM."""
        captured = {}

        def capture_llm(messages, **kwargs):
            captured["messages"] = messages
            return {"action": "done"}

        state = {
            "current_task": "test",
            "task_index": "1/1",
            "last_steps": [],
            "completed_tasks": [],
            "environment": {"missing_tools": ["go"]},
            "policy": {"allow_system_installs": False, "allow_network": True},
        }
        with patch("askme.ask_llm", side_effect=capture_llm):
            get_step("test task", state, goal="test goal")
        user_msg = captured["messages"][1]["content"]
        assert "policy" in user_msg
        assert "allow_system_installs" in user_msg

    def test_executor_sees_missing_tools(self):
        """get_step should include missing_tools in slim state when present."""
        captured = {}

        def capture_llm(messages, **kwargs):
            captured["messages"] = messages
            return {"action": "done"}

        state = {
            "current_task": "test",
            "task_index": "1/1",
            "last_steps": [],
            "completed_tasks": [],
            "environment": {"missing_tools": ["go", "cargo"]},
            "policy": {"allow_system_installs": False},
        }
        with patch("askme.ask_llm", side_effect=capture_llm):
            get_step("test task", state, goal="test goal")
        user_msg = captured["messages"][1]["content"]
        assert "missing_tools" in user_msg
        assert "go" in user_msg

    def test_system_plan_includes_policy_rules(self):
        from askme import SYSTEM_PLAN

        assert "allow_system_installs" in SYSTEM_PLAN
        assert "missing_tools" in SYSTEM_PLAN
        assert "package_managers" in SYSTEM_PLAN

    def test_system_step_includes_policy_rules(self):
        from askme import SYSTEM_STEP

        assert "missing_tools" in SYSTEM_STEP
        assert "allow_system_installs" in SYSTEM_STEP
        assert "do NOT install" in SYSTEM_STEP


# --- Command-aware timeout tests ---


class TestCommandAwareTimeout:
    """Verify longer timeouts for install/build commands."""

    def test_default_timeout(self):
        from askme import SHELL_TIMEOUT, _get_shell_timeout

        assert _get_shell_timeout("echo hello") == SHELL_TIMEOUT

    def test_install_timeout(self):
        from askme import SHELL_TIMEOUT_LONG, _get_shell_timeout

        assert _get_shell_timeout("brew install go") == SHELL_TIMEOUT_LONG
        assert _get_shell_timeout("apt-get install python3") == SHELL_TIMEOUT_LONG
        assert _get_shell_timeout("pip install requests") == SHELL_TIMEOUT_LONG

    def test_build_timeout(self):
        from askme import SHELL_TIMEOUT_LONG, _get_shell_timeout

        assert _get_shell_timeout("cmake --build .") == SHELL_TIMEOUT_LONG
        assert _get_shell_timeout("cargo build") == SHELL_TIMEOUT_LONG

    def test_model_hint_timeout(self):
        from askme import SHELL_TIMEOUT_MAX, _get_shell_timeout

        assert _get_shell_timeout("some cmd", hint=60) == 60
        assert _get_shell_timeout("some cmd", hint=999) == SHELL_TIMEOUT_MAX
        assert _get_shell_timeout("some cmd", hint=1) == 5  # minimum 5s

    def test_timeout_retry_not_blocked_by_duplicate_guard(self, work_dir, capsys):
        """Shell timeout + retry should NOT trigger auto-fail (duplicate guard exception)."""
        timeout_result = {"ok": False, "output": "TIMEOUT", "error_type": "timeout"}
        responses = [
            {"tasks": ["install thing"]},
            {"action": "shell", "arg": "long_cmd"},
            {"action": "shell", "arg": "long_cmd"},
            {"action": "fail", "reasoning": "still timing out"},
        ] * 3
        with patch("askme.ask_llm", side_effect=responses):
            with patch("askme.execute", return_value=timeout_result):
                run("install thing", working_dir=work_dir)
        out = capsys.readouterr().out
        assert "retrying after timeout" in out
        assert "same shell failed twice" not in out

    def test_timeout_retry_bumps_timeout(self):
        """After a timeout, the retry should inject a bumped timeout into the action."""
        from askme import SHELL_TIMEOUT_LONG, SHELL_TIMEOUT_MAX, _get_shell_timeout

        action = {"action": "shell", "arg": "some_slow_cmd"}
        default_timeout = _get_shell_timeout(action["arg"])
        assert default_timeout == 30
        bumped = max(SHELL_TIMEOUT_LONG, default_timeout * 2)
        expected = min(bumped, SHELL_TIMEOUT_MAX)
        assert expected == SHELL_TIMEOUT_LONG

    def test_timeout_escalation_through_run(self, work_dir, capsys):
        """Drive run() through two timeout retries and verify timeout escalates each time."""
        from askme import SHELL_TIMEOUT_LONG, SHELL_TIMEOUT_MAX

        execute_calls = []

        def tracking_execute(action, wd="."):
            execute_calls.append(dict(action))
            return {"ok": False, "output": "TIMEOUT", "error_type": "timeout"}

        responses = [
            {"tasks": ["slow task"]},
            {"action": "shell", "arg": "slow_cmd"},
            {"action": "shell", "arg": "slow_cmd"},
            {"action": "shell", "arg": "slow_cmd"},
            {"action": "fail", "reasoning": "keeps timing out"},
        ] * 3
        with patch("askme.ask_llm", side_effect=responses):
            with patch("askme.execute", side_effect=tracking_execute):
                run("slow task", working_dir=work_dir)
        shell_calls = [c for c in execute_calls if c.get("action") == "shell"]
        assert len(shell_calls) >= 3, f"Expected >=3 shell calls, got {len(shell_calls)}"
        assert "timeout" not in shell_calls[0], (
            f"First call should not have timeout key: {shell_calls[0]}"
        )
        t1 = shell_calls[1].get("timeout")
        assert t1 is not None, f"Second call should have timeout: {shell_calls[1]}"
        assert t1 >= SHELL_TIMEOUT_LONG, (
            f"Second call timeout {t1} should be >= {SHELL_TIMEOUT_LONG}"
        )
        t2 = shell_calls[2].get("timeout")
        assert t2 is not None, f"Third call should have timeout: {shell_calls[2]}"
        assert t2 >= t1, f"Third timeout {t2} should be >= second {t1}"
        assert t2 <= SHELL_TIMEOUT_MAX, f"Third timeout {t2} should be <= {SHELL_TIMEOUT_MAX}"


# --- Server config tests ---


@skip_no_llm
@pytest.mark.live_llm
class TestServerConfig:
    """Verify llama-server is running with optimized agentic configuration.
    These are fast (no LLM inference), just HTTP checks against the server."""

    def test_single_slot_full_context(self):
        """Server should have 1 slot (-np 1) with full 16K context."""
        import requests

        slots = requests.get("http://localhost:8080/slots", timeout=5).json()
        assert len(slots) == 1, f"Expected 1 slot (-np 1), got {len(slots)}"
        assert slots[0]["n_ctx"] == 16384, f"Expected 16384 ctx, got {slots[0]['n_ctx']}"


# --- E11: Task-local replan function tests ---


class TestReplanTask:
    """Unit tests for replan_task() — the mini-planner for single failed tasks."""

    @patch("askme.ask_llm")
    def test_returns_replacement_string(self, mock_llm):
        mock_llm.return_value = {"task": "fix the include path using relative name"}
        state = {"environment": {"missing_tools": []}, "policy": {"allow_system_installs": False}}
        result = replan_task(
            "add missing include",
            ["[compile_error] gcc: missing header"],
            ["create main.c"],
            state,
            "compile hello.c",
        )
        assert result == "fix the include path using relative name"
        assert mock_llm.called

    @patch("askme.ask_llm")
    def test_includes_policy_and_missing_tools(self, mock_llm):
        """replan_task should pass policy and missing_tools to the mini-planner."""
        captured = {}

        def capture(messages, **kwargs):
            captured["user_msg"] = messages[-1]["content"]
            return {"task": "replacement"}

        mock_llm.side_effect = capture
        state = {
            "environment": {"missing_tools": ["go"]},
            "policy": {"allow_system_installs": False},
        }
        replan_task("run go test", ["[missing_tool] go not found"], [], state, "test the project")
        assert "missing_tools" in captured["user_msg"]
        assert "go" in captured["user_msg"]
        assert "allow_system_installs" in captured["user_msg"]

    @patch("askme.ask_llm")
    def test_transport_error_returns_none(self, mock_llm):
        mock_llm.side_effect = LLMTransportError("timeout")
        state = {"environment": {}, "policy": {}}
        result = replan_task("do thing", ["error"], [], state, "goal")
        assert result is None

    @patch("askme.ask_llm")
    def test_empty_response_returns_none(self, mock_llm):
        mock_llm.return_value = {"task": ""}
        state = {"environment": {}, "policy": {}}
        result = replan_task("do thing", ["error"], [], state, "goal")
        assert result is None

    @patch("askme.ask_llm")
    def test_short_response_returns_none(self, mock_llm):
        mock_llm.return_value = {"task": "no"}
        state = {"environment": {}, "policy": {}}
        result = replan_task("do thing", ["error"], [], state, "goal")
        assert result is None

    @patch("askme.ask_llm")
    def test_noop_replacement_returns_none(self, mock_llm):
        """Returning the same task description should be treated as a failed replan."""
        mock_llm.return_value = {"task": "add missing include"}
        state = {"environment": {}, "policy": {}}
        result = replan_task("add missing include", ["error"], [], state, "goal")
        assert result is None

    @patch("askme.ask_llm")
    def test_near_duplicate_replacement_returns_none(self, mock_llm):
        """Small wording changes should not count as useful task-local replans."""
        import askme

        mock_llm.return_value = {"task": "Edit fix_me.c to include <stdio.h> and recompile"}
        state = {"environment": {}, "policy": {}}
        result = replan_task(
            "Add '#include <stdio.h>' to fix_me.c",
            ["[edit_failed] same find failed twice"],
            [],
            state,
            "fix missing include",
        )
        assert result is None
        assert askme._last_task_replan_reject_reason == "near_duplicate"

    @patch("askme.ask_llm")
    def test_uses_cheap_no_thinking_call(self, mock_llm):
        """Task-local replan must stay cheaper than a full planner replan."""
        captured = {}

        def capture(messages, **kwargs):
            captured.update(kwargs)
            return {"task": "replacement task"}

        mock_llm.side_effect = capture
        state = {"environment": {}, "policy": {}}
        replan_task("do thing", ["error"], [], state, "goal")
        assert captured.get("think") is False
        assert captured.get("max_tokens") == TASK_REPLAN_MAX_TOKENS
        assert captured.get("max_retries") == 0
        assert "think_level" not in captured

    @patch("askme.ask_llm")
    def test_rejects_passive_replacement_for_action_task(self, mock_llm):
        """Do not downgrade an edit/fix task into a read-only prep task."""
        import askme

        mock_llm.return_value = {"task": "Read the contents of fix_me.c to prepare for editing"}
        state = {"environment": {}, "policy": {}}
        result = replan_task(
            "Edit fix_me.c to include #include <stdio.h>",
            ["[stuck_loop] read fix_me.c repeatedly"],
            [],
            state,
            "fix the missing include",
        )
        assert result is None
        assert askme._last_task_replan_reject_reason == "passive_downgrade"
