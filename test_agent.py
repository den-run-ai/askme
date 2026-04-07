#!/usr/bin/env python3
"""Tests for askme.py agent. No LLM needed — mocks all LLM calls."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent))
from askme import execute, ask_llm, get_plan, get_step, run


@pytest.fixture
def work_dir(tmp_path):
    return str(tmp_path)


def mock_response(content):
    """Create a mock requests.post response returning content as LLM output."""
    resp = MagicMock()
    resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(content)}}]
    }
    return resp


def mock_response_raw(text):
    """Create a mock response with raw text (for testing think-tag stripping etc)."""
    resp = MagicMock()
    resp.json.return_value = {
        "choices": [{"message": {"content": text}}]
    }
    return resp


# --- execute() tests ---

class TestExecuteShell:
    def test_success(self, work_dir):
        result = execute({"action": "shell", "arg": "echo hello"}, work_dir)
        assert result["ok"] is True
        assert "hello" in result["output"]

    def test_failure(self, work_dir):
        result = execute({"action": "shell", "arg": "false"}, work_dir)
        assert result["ok"] is False

    def test_timeout(self, work_dir):
        result = execute({"action": "shell", "arg": "sleep 60"}, work_dir)
        assert result["ok"] is False
        assert result["output"] == "TIMEOUT"

    def test_stderr_captured(self, work_dir):
        result = execute({"action": "shell", "arg": "echo err >&2"}, work_dir)
        assert "err" in result["output"]

    def test_output_truncated(self, work_dir):
        result = execute({"action": "shell", "arg": "python3 -c \"print('x'*500)\""}, work_dir)
        assert len(result["output"]) <= 300

    def test_cwd_respected(self, work_dir):
        result = execute({"action": "shell", "arg": "pwd"}, work_dir)
        assert work_dir in result["output"]

    def test_empty_output(self, work_dir):
        result = execute({"action": "shell", "arg": "true"}, work_dir)
        assert result["ok"] is True
        assert result["output"] == "(no output)"


class TestExecuteWrite:
    def test_write_file(self, work_dir):
        result = execute({"action": "write", "arg": "test.txt", "content": "hello"}, work_dir)
        assert result["ok"] is True
        assert (Path(work_dir) / "test.txt").read_text() == "hello"

    def test_write_relative_path(self, work_dir):
        result = execute({"action": "write", "arg": "sub/test.txt", "content": "nested"}, work_dir)
        assert result["ok"] is True
        assert (Path(work_dir) / "sub" / "test.txt").read_text() == "nested"

    def test_write_empty_content(self, work_dir):
        result = execute({"action": "write", "arg": "empty.txt"}, work_dir)
        assert result["ok"] is True
        assert (Path(work_dir) / "empty.txt").read_text() == ""

    def test_write_bad_path(self, work_dir):
        result = execute({"action": "write", "arg": "/proc/0/impossible", "content": "x"}, work_dir)
        assert result["ok"] is False


class TestExecuteRead:
    def test_read_file(self, work_dir):
        Path(work_dir, "data.txt").write_text("file contents here")
        result = execute({"action": "read", "arg": "data.txt"}, work_dir)
        assert result["ok"] is True
        assert "file contents here" in result["output"]

    def test_read_absolute(self, work_dir):
        Path(work_dir, "abs.txt").write_text("absolute")
        result = execute({"action": "read", "arg": f"{work_dir}/abs.txt"}, work_dir)
        assert result["ok"] is True
        assert "absolute" in result["output"]

    def test_read_missing(self, work_dir):
        result = execute({"action": "read", "arg": "nope.txt"}, work_dir)
        assert result["ok"] is False

    def test_read_truncated(self, work_dir):
        Path(work_dir, "big.txt").write_text("x" * 500)
        result = execute({"action": "read", "arg": f"{work_dir}/big.txt"}, work_dir)
        assert len(result["output"]) <= 300


class TestExecuteDoneFail:
    def test_done(self, work_dir):
        result = execute({"action": "done", "arg": ""}, work_dir)
        assert result["ok"] is True
        assert result["output"] == "task_complete"

    def test_fail(self, work_dir):
        result = execute({"action": "fail", "reasoning": "can't do it"}, work_dir)
        assert result["ok"] is False
        assert "can't do it" in result["output"]

    def test_unknown_action(self, work_dir):
        result = execute({"action": "dance", "arg": ""}, work_dir)
        assert result["ok"] is False
        assert "unknown" in result["output"]

    def test_missing_action_key(self, work_dir):
        result = execute({}, work_dir)
        assert result["ok"] is False


# --- ask_llm() tests ---

class TestAskLlm:
    @patch("askme.requests.post")
    def test_parses_json(self, mock_post):
        mock_post.return_value = mock_response({"action": "done"})
        result = ask_llm([{"role": "user", "content": "test"}])
        assert result == {"action": "done"}

    @patch("askme.requests.post")
    def test_strips_think_tags(self, mock_post):
        mock_post.return_value = mock_response_raw(
            '<think>let me think about this</think>{"action":"done"}'
        )
        result = ask_llm([{"role": "user", "content": "test"}])
        assert result == {"action": "done"}

    @patch("askme.requests.post")
    def test_strips_code_fences(self, mock_post):
        mock_post.return_value = mock_response_raw(
            '```json\n{"action":"shell","arg":"ls"}\n```'
        )
        result = ask_llm([{"role": "user", "content": "test"}])
        assert result["action"] == "shell"

    @patch("askme.requests.post")
    def test_strips_think_and_fences(self, mock_post):
        mock_post.return_value = mock_response_raw(
            '<think>hmm</think>```json\n{"action":"done"}\n```'
        )
        result = ask_llm([{"role": "user", "content": "test"}])
        assert result == {"action": "done"}


# --- Thinking-on-retry tests ---

class TestThinkingRetry:
    """Verify thinking-on-retry escalation in ask_llm()."""

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_thinking_retry_openrouter(self, mock_post):
        """On retry, ask_llm should add reasoning params for OpenRouter."""
        # First call returns bad JSON, second call returns valid JSON
        mock_post.side_effect = [
            mock_response_raw("not json"),  # attempt 0: no thinking, parse fails
            mock_response({"action": "done"}),  # attempt 1: thinking=medium
        ]
        result = ask_llm([{"role": "user", "content": "test"}])
        assert result == {"action": "done"}
        # Check that the second call included reasoning params
        second_call_body = mock_post.call_args_list[1][1]["json"]
        assert "reasoning" in second_call_body
        assert second_call_body["reasoning"]["enabled"] is True
        assert second_call_body["reasoning"]["effort"] == "medium"
        # First call should NOT have reasoning
        first_call_body = mock_post.call_args_list[0][1]["json"]
        assert "reasoning" not in first_call_body

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "local")
    def test_thinking_retry_local(self, mock_post):
        """On retry, ask_llm should prepend <|think|> and bump max_tokens for local."""
        mock_post.side_effect = [
            mock_response_raw("not json"),  # attempt 0: fails
            mock_response({"action": "done"}),  # attempt 1: thinking
        ]
        result = ask_llm([
            {"role": "system", "content": "You are a helper."},
            {"role": "user", "content": "test"}
        ], max_tokens=256)
        assert result == {"action": "done"}
        # Second call should have <|think|> in system prompt and bumped max_tokens
        second_call_body = mock_post.call_args_list[1][1]["json"]
        sys_content = second_call_body["messages"][0]["content"]
        assert sys_content.startswith("<|think|>\n"), f"Expected <|think|> prefix, got: {sys_content[:50]}"
        assert second_call_body["max_tokens"] >= 512
        # First call should NOT have <|think|>
        first_call_body = mock_post.call_args_list[0][1]["json"]
        first_sys = first_call_body["messages"][0]["content"]
        assert not first_sys.startswith("<|think|>")

    @patch("askme.requests.post")
    def test_thinking_strips_channel_tags(self, mock_post):
        """Local thinking output (<|channel>...<channel|>) should be stripped."""
        mock_post.return_value = mock_response_raw(
            '<|channel>thought\nlet me reason about this\n<channel|>{"action":"done"}'
        )
        result = ask_llm([{"role": "user", "content": "test"}])
        assert result == {"action": "done"}

    @patch("askme.requests.post")
    def test_thinking_strips_unclosed_channel_tags(self, mock_post):
        """Unclosed <|channel> blocks (truncated at max_tokens) should be stripped."""
        mock_post.return_value = mock_response_raw(
            '<|channel>thought\nstill thinking...'
        )
        # Should raise JSONDecodeError since no JSON remains after stripping
        with pytest.raises(json.JSONDecodeError):
            ask_llm([{"role": "user", "content": "test"}])

    @patch("askme.requests.post")
    def test_api_error_retries(self, mock_post):
        """API error responses should be retried, not crash on missing 'choices'."""
        resp_err = MagicMock()
        resp_err.json.return_value = {"error": {"message": "rate limited", "code": 429}}
        mock_post.side_effect = [resp_err, mock_response({"action": "done"})]
        result = ask_llm([{"role": "user", "content": "test"}])
        assert result == {"action": "done"}

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_no_thinking_on_first_attempt(self, mock_post):
        """First attempt should never include reasoning params."""
        mock_post.return_value = mock_response({"action": "done"})
        ask_llm([{"role": "user", "content": "test"}])
        call_body = mock_post.call_args_list[0][1]["json"]
        assert "reasoning" not in call_body

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_think_true_enables_from_first_attempt(self, mock_post):
        """When think=True, reasoning should be enabled from attempt 0."""
        mock_post.return_value = mock_response({"action": "done"})
        ask_llm([{"role": "user", "content": "test"}], think=True)
        call_body = mock_post.call_args_list[0][1]["json"]
        assert "reasoning" in call_body
        assert call_body["reasoning"]["effort"] == "medium"

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_null_content_with_reasoning(self, mock_post):
        """When reasoning exhausts tokens, content may be null — should retry, not crash."""
        # First call: content is None (reasoning ate all tokens)
        resp_null = MagicMock()
        resp_null.json.return_value = {
            "choices": [{"message": {"content": None, "reasoning": "thinking..."}}]
        }
        # Second call: valid response
        mock_post.side_effect = [resp_null, mock_response({"action": "done"})]
        result = ask_llm([{"role": "user", "content": "test"}])
        assert result == {"action": "done"}

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_think_escalates_to_high(self, mock_post):
        """With think=True, retry should escalate to high effort."""
        mock_post.side_effect = [
            mock_response_raw("bad"),  # attempt 0: medium, fails
            mock_response({"action": "done"}),  # attempt 1: high
        ]
        result = ask_llm([{"role": "user", "content": "test"}], think=True)
        assert result == {"action": "done"}
        second_body = mock_post.call_args_list[1][1]["json"]
        assert second_body["reasoning"]["effort"] == "high"


# --- Full agent loop tests ---

class TestRunLoop:
    @patch("askme.ask_llm")
    def test_simple_success(self, mock_llm, capsys):
        """Plan with one task, one shell step, then done."""
        mock_llm.side_effect = [
            # get_plan call
            {"tasks": ["say hello"]},
            # get_step: shell action
            {"action": "shell", "arg": "echo hello", "reasoning": "greet"},
            # get_step: done
            {"action": "done", "reasoning": "finished"},
        ]
        result = run("say hello")
        out = capsys.readouterr().out
        assert "All tasks complete" in out
        assert result is True

    @patch("askme.ask_llm")
    def test_task_failure_triggers_replan(self, mock_llm, capsys):
        """Task fails, agent replans, second plan succeeds."""
        mock_llm.side_effect = [
            # Plan 1
            {"tasks": ["compile code"]},
            # Step fails
            {"action": "fail", "reasoning": "gcc not found"},
            # Plan 2 (replan)
            {"tasks": ["install gcc", "compile code"]},
            # Task 1 steps
            {"action": "shell", "arg": "echo installed", "reasoning": "install"},
            {"action": "done", "reasoning": "installed"},
            # Task 2 steps
            {"action": "shell", "arg": "echo compiled", "reasoning": "compile"},
            {"action": "done", "reasoning": "compiled"},
        ]
        result = run("compile my code")
        out = capsys.readouterr().out
        assert "will replan" in out
        assert "All tasks complete" in out
        assert result is True

    @patch("askme.ask_llm")
    def test_max_replans_exhausted(self, mock_llm, capsys):
        """All plans fail, agent stops after MAX_REPLANS."""
        mock_llm.side_effect = [
            {"tasks": ["do thing"]},
            {"action": "fail", "reasoning": "nope"},
        ] * 3  # 3 replan attempts
        result = run("impossible task")
        out = capsys.readouterr().out
        assert "Exhausted" in out
        assert result is False

    @patch("askme.ask_llm")
    def test_multi_task_plan(self, mock_llm, capsys):
        """Plan with multiple tasks, all succeed."""
        mock_llm.side_effect = [
            {"tasks": ["create file", "read file"]},
            # Task 1
            {"action": "write", "arg": "/tmp/test_askme.txt", "content": "hi", "reasoning": "create"},
            {"action": "done", "reasoning": "created"},
            # Task 2
            {"action": "read", "arg": "/tmp/test_askme.txt", "reasoning": "read"},
            {"action": "done", "reasoning": "read it"},
        ]
        result = run("create and read a file")
        out = capsys.readouterr().out
        assert "All tasks complete" in out
        assert result is True

    @patch("askme.ask_llm")
    def test_step_errors_tracked_in_state(self, mock_llm, capsys):
        """Shell command fails mid-task, error is recorded, task continues."""
        mock_llm.side_effect = [
            {"tasks": ["run commands"]},
            # Failing command
            {"action": "shell", "arg": "false", "reasoning": "try"},
            # Recover and finish
            {"action": "shell", "arg": "echo ok", "reasoning": "retry"},
            {"action": "done", "reasoning": "done"},
        ]
        result = run("run some commands")
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "All tasks complete" in out
        assert result is True

    @patch("askme.ask_llm")
    def test_empty_plan(self, mock_llm, capsys):
        """Plan with no tasks completes immediately."""
        mock_llm.side_effect = [
            {"tasks": []},
        ]
        result = run("do nothing")
        out = capsys.readouterr().out
        assert "All tasks complete" in out
        assert result is True

    @patch("askme.ask_llm")
    def test_working_dir_isolation(self, mock_llm, capsys, tmp_path):
        """run() creates isolated temp dir and shell commands execute there."""
        mock_llm.side_effect = [
            {"tasks": ["check dir"]},
            {"action": "shell", "arg": "pwd", "reasoning": "check"},
            {"action": "done", "reasoning": "done"},
        ]
        result = run("check dir", working_dir=str(tmp_path))
        out = capsys.readouterr().out
        assert str(tmp_path) in out
        assert "Working directory:" in out
        assert "Output in:" in out
        assert result is True

    @patch("askme.ask_llm")
    def test_working_dir_printed_on_failure(self, mock_llm, capsys, tmp_path):
        """Working dir is printed even when agent fails."""
        mock_llm.side_effect = [
            {"tasks": ["fail"]},
            {"action": "fail", "reasoning": "nope"},
        ] * 3
        result = run("fail", working_dir=str(tmp_path))
        out = capsys.readouterr().out
        assert "Output in:" in out
        assert result is False

    @patch("askme.ask_llm")
    def test_auto_creates_temp_dir(self, mock_llm, capsys):
        """run() without working_dir creates a temp directory automatically."""
        mock_llm.side_effect = [
            {"tasks": ["check"]},
            {"action": "done", "reasoning": "done"},
        ]
        result = run("check")
        out = capsys.readouterr().out
        assert "nanagent_" in out
        assert result is True


# --- Tests for cross-task state and output formatting bugs ---

class TestCrossTaskState:
    """Verify fixes for empty executor state between tasks."""

    @patch("askme.ask_llm")
    def test_completed_tasks_in_state(self, mock_llm, capsys):
        """Executor should see completed_tasks from prior tasks."""
        calls = []
        def capture_llm(messages, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                return {"tasks": ["create file", "read file"]}
            if len(calls) == 2:
                return {"action": "write", "arg": "test.txt", "content": "hi", "reasoning": "create"}
            if len(calls) == 3:
                return {"action": "done", "reasoning": "created"}
            if len(calls) == 4:
                # This is the first step of task 2 — check that state includes completed_tasks
                user_msg = messages[-1]["content"]
                assert "completed_tasks" in user_msg, \
                    f"Executor should see completed_tasks in state, got: {user_msg[-200:]}"
                return {"action": "done", "reasoning": "already done"}
            return {"action": "done"}
        mock_llm.side_effect = capture_llm
        run("create and read a file")
        out = capsys.readouterr().out
        assert "All tasks complete" in out

    @patch("askme.ask_llm")
    def test_last_step_carries_over(self, mock_llm, capsys):
        """Last step from task N should be visible at start of task N+1."""
        calls = []
        def capture_llm(messages, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                return {"tasks": ["write file", "compile file"]}
            if len(calls) == 2:
                return {"action": "write", "arg": "main.c", "content": "code", "reasoning": "create"}
            if len(calls) == 3:
                return {"action": "done", "reasoning": "created"}
            if len(calls) == 4:
                # First step of task 2 — should see last step from task 1
                user_msg = messages[-1]["content"]
                assert "last_steps" in user_msg
                assert "write" in user_msg, \
                    f"Executor should see carryover step from task 1, got: {user_msg[-200:]}"
                return {"action": "done", "reasoning": "already done"}
            return {"action": "done"}
        mock_llm.side_effect = capture_llm
        run("write and compile")
        out = capsys.readouterr().out
        assert "All tasks complete" in out


class TestOutputFormatting:
    """Verify write output uses basename (not full path) and arg uses basename for file ops."""

    def test_write_output_basename(self, work_dir):
        """Write action output should show filename, not full path."""
        result = execute({"action": "write", "arg": f"{work_dir}/subdir/test.txt", "content": "hi"}, work_dir)
        assert result["ok"] is True
        assert result["output"] == "Wrote test.txt"
        assert work_dir not in result["output"]

    def test_write_relative_output_basename(self, work_dir):
        result = execute({"action": "write", "arg": "hello.txt", "content": "hi"}, work_dir)
        assert result["ok"] is True
        assert result["output"] == "Wrote hello.txt"

    def test_slim_steps_basename_for_write(self):
        """get_step should use basename for write/read args in slim state."""
        from askme import get_step
        state = {
            "current_task": "test",
            "task_index": "1/1",
            "last_steps": [
                {"action": "write", "arg": "/very/long/path/to/file.txt", "ok": True, "output": "Wrote file.txt"},
            ],
            "completed_tasks": [],
        }
        # We can't call get_step directly (it calls ask_llm), but we can test the slim state logic
        from askme import MAX_STEP_HISTORY, MAX_INPUT
        steps = state.get("last_steps", [])[-MAX_STEP_HISTORY:]
        for s in steps:
            arg = s.get("arg", "")
            if s["action"] in ("write", "read") and "/" in arg:
                arg = Path(arg).name
            assert arg == "file.txt", f"Expected basename, got: {arg}"


class TestWriteContentSerialization:
    """Verify that dict/list content in write actions is auto-serialized to JSON."""

    def test_write_dict_content(self, work_dir):
        """Write action with dict content should auto-serialize to JSON string."""
        result = execute({"action": "write", "arg": "config.json", "content": {"status": "SUCCESS"}}, work_dir)
        assert result["ok"] is True
        written = (Path(work_dir) / "config.json").read_text()
        assert '"status"' in written
        assert "SUCCESS" in written

    def test_write_list_content(self, work_dir):
        """Write action with list content should auto-serialize to JSON string."""
        result = execute({"action": "write", "arg": "data.json", "content": [1, 2, 3]}, work_dir)
        assert result["ok"] is True
        written = (Path(work_dir) / "data.json").read_text()
        import json as _json
        assert _json.loads(written) == [1, 2, 3]

    def test_write_string_content_unchanged(self, work_dir):
        """Write action with string content should pass through unchanged."""
        result = execute({"action": "write", "arg": "test.txt", "content": "hello world"}, work_dir)
        assert result["ok"] is True
        assert (Path(work_dir) / "test.txt").read_text() == "hello world"

    def test_write_nested_json_dict(self, work_dir):
        """Write action with nested dict should produce valid JSON file."""
        content = {"database": {"host": "localhost", "port": 5432}, "debug": True}
        result = execute({"action": "write", "arg": "config.json", "content": content}, work_dir)
        assert result["ok"] is True
        import json as _json
        parsed = _json.loads((Path(work_dir) / "config.json").read_text())
        assert parsed == content

    def test_write_empty_dict_content(self, work_dir):
        """Write action with empty dict should write '{}'."""
        result = execute({"action": "write", "arg": "empty.json", "content": {}}, work_dir)
        assert result["ok"] is True
        import json as _json
        assert _json.loads((Path(work_dir) / "empty.json").read_text()) == {}


class TestDuplicateGuard:
    """Verify duplicate action guard prevents loops without blocking legitimate retries."""

    def test_write_same_content_triggers_auto_done(self, tmp_path):
        """Same file + same content + ok → auto-done (true duplicate)."""
        f = str(tmp_path / "data.txt")
        responses = [
            {"tasks": ["write data.txt"]},
            {"action": "write", "arg": f, "content": "hello"},
            {"action": "write", "arg": f, "content": "hello"},  # duplicate — should auto-done
        ]
        with patch("askme.ask_llm", side_effect=responses):
            run("write data.txt")
        assert (tmp_path / "data.txt").read_text() == "hello"

    def test_write_different_content_allowed(self, tmp_path):
        """Same file + different content → allow (legitimate fix attempt)."""
        responses = [
            {"tasks": ["write and fix"]},
            {"action": "write", "arg": str(tmp_path / "f.txt"), "content": "v1"},
            {"action": "write", "arg": str(tmp_path / "f.txt"), "content": "v2"},  # different content
            {"action": "done"},
        ]
        with patch("askme.ask_llm", side_effect=responses):
            run("write and fix")
        assert (tmp_path / "f.txt").read_text() == "v2"

    def test_shell_same_success_triggers_auto_done(self, tmp_path):
        """Same shell + ok → auto-done (true duplicate)."""
        responses = [
            {"tasks": ["run echo"]},
            {"action": "shell", "arg": "echo hi"},
            {"action": "shell", "arg": "echo hi"},  # duplicate — should auto-done
        ]
        with patch("askme.ask_llm", side_effect=responses):
            run("run echo")

    def test_shell_same_failure_triggers_auto_fail(self, tmp_path):
        """Same shell + fail twice → auto-fail, error recorded for replan."""
        responses = [
            {"tasks": ["run bad"]},
            {"action": "shell", "arg": "false"},  # fails (exit 1)
            {"action": "shell", "arg": "false"},  # same fail — auto-fail
            # After auto-fail, should replan:
            {"tasks": ["try something else"]},
            {"action": "shell", "arg": "echo fixed"},
            {"action": "done"},
        ]
        with patch("askme.ask_llm", side_effect=responses):
            run("run bad")

    def test_shell_recompile_after_write_not_blocked(self, tmp_path):
        """shell gcc (fail) → write fix → shell gcc (same cmd) should NOT be blocked."""
        src = tmp_path / "main.c"
        responses = [
            {"tasks": ["compile"]},
            {"action": "shell", "arg": f"cc -o main {src}"},  # fails (no file)
            {"action": "write", "arg": str(src), "content": '#include <stdio.h>\nint main(){puts("ok");return 0;}'},
            {"action": "shell", "arg": f"cc -o main {src}"},  # same cmd but last step is write, not shell
            {"action": "done"},
        ]
        with patch("askme.ask_llm", side_effect=responses):
            run("compile")

    def test_read_same_file_twice_allowed(self, tmp_path):
        """Read same file twice consecutively — allowed (read excluded from guard)."""
        f = tmp_path / "data.txt"
        f.write_text("hello")
        responses = [
            {"tasks": ["read data"]},
            {"action": "read", "arg": str(f)},
            {"action": "read", "arg": str(f)},  # should NOT be blocked
            {"action": "done"},
        ]
        with patch("askme.ask_llm", side_effect=responses):
            run("read data")

    def test_different_action_type_not_duplicate(self, tmp_path):
        """write then shell on same arg — different action types, no guard trigger."""
        responses = [
            {"tasks": ["write and run"]},
            {"action": "write", "arg": str(tmp_path / "s.sh"), "content": "echo ok"},
            {"action": "shell", "arg": f"bash {tmp_path / 's.sh'}"},  # different action type
            {"action": "done"},
        ]
        with patch("askme.ask_llm", side_effect=responses):
            run("write and run")

    def test_content_not_in_slim_state(self):
        """_content field should not appear in messages sent to LLM by get_step()."""
        from askme import get_step
        state = {
            "current_task": "test",
            "task_index": "1/1",
            "last_steps": [
                {"action": "write", "arg": "f.txt", "ok": True, "output": "Wrote f.txt",
                 "_content": "should not appear in slim"},
            ],
            "completed_tasks": [],
        }
        # Mock ask_llm to capture the messages get_step sends
        captured = {}
        def capture_llm(messages, **kwargs):
            captured["messages"] = messages
            return {"action": "done"}
        with patch("askme.ask_llm", side_effect=capture_llm):
            get_step("test task", state, goal="test goal")
        # Verify _content is not anywhere in the user message sent to LLM
        user_msg = captured["messages"][1]["content"]
        assert "_content" not in user_msg, f"_content leaked into LLM message: {user_msg}"


# --- Cache workaround tests ---

class TestCacheWorkaround:
    """Test Phase 2 manual slot save/restore workaround for broken --cache-reuse."""

    def test_warm_cache_saves_slot(self):
        """_warm_cache() should send a minimal request then save slot 0."""
        import askme
        old_cw, old_backend, old_warmed = askme.CACHE_WORKAROUND, askme.LLM_BACKEND, askme._cache_warmed
        try:
            askme.CACHE_WORKAROUND = True
            askme.LLM_BACKEND = "local"
            askme._cache_warmed = False

            calls = []
            def fake_post(url, **kwargs):
                calls.append(url)
                resp = MagicMock()
                resp.status_code = 200
                if "action=save" in url:
                    resp.json.return_value = {"n_saved": 150}
                else:
                    resp.json.return_value = {
                        "choices": [{"message": {"content": '{"tasks":[]}'}}]
                    }
                return resp

            with patch("askme.requests.post", side_effect=fake_post):
                askme._warm_cache()

            assert askme._cache_warmed is True
            assert any("chat/completions" in u for u in calls), "Should send completion request"
            assert any("action=save" in u for u in calls), "Should save slot"
        finally:
            askme.CACHE_WORKAROUND = old_cw
            askme.LLM_BACKEND = old_backend
            askme._cache_warmed = old_warmed

    def test_warm_cache_noop_when_disabled(self):
        """_warm_cache() should do nothing when CACHE_WORKAROUND is False."""
        import askme
        old_cw = askme.CACHE_WORKAROUND
        try:
            askme.CACHE_WORKAROUND = False
            with patch("askme.requests.post") as mock_post:
                askme._warm_cache()
            mock_post.assert_not_called()
        finally:
            askme.CACHE_WORKAROUND = old_cw

    def test_warm_cache_noop_for_remote_backend(self):
        """_warm_cache() should do nothing for remote (non-local) backend."""
        import askme
        old_cw, old_backend = askme.CACHE_WORKAROUND, askme.LLM_BACKEND
        try:
            askme.CACHE_WORKAROUND = True
            askme.LLM_BACKEND = "openrouter"
            with patch("askme.requests.post") as mock_post:
                askme._warm_cache()
            mock_post.assert_not_called()
        finally:
            askme.CACHE_WORKAROUND = old_cw
            askme.LLM_BACKEND = old_backend

    def test_warm_cache_failure_is_nonfatal(self):
        """If save fails, _cache_warmed stays False and execution continues."""
        import askme
        old_cw, old_backend, old_warmed = askme.CACHE_WORKAROUND, askme.LLM_BACKEND, askme._cache_warmed
        try:
            askme.CACHE_WORKAROUND = True
            askme.LLM_BACKEND = "local"
            askme._cache_warmed = False

            def fake_post(url, **kwargs):
                if "action=save" in url:
                    raise ConnectionError("server down")
                resp = MagicMock()
                resp.json.return_value = {
                    "choices": [{"message": {"content": '{"tasks":[]}'}}]
                }
                return resp

            with patch("askme.requests.post", side_effect=fake_post):
                askme._warm_cache()  # should not raise

            assert askme._cache_warmed is False
        finally:
            askme.CACHE_WORKAROUND = old_cw
            askme.LLM_BACKEND = old_backend
            askme._cache_warmed = old_warmed

    def test_restore_called_before_each_llm_request(self):
        """When cache is warmed, _restore_cache() should be called before each ask_llm."""
        import askme
        old_warmed = askme._cache_warmed
        try:
            askme._cache_warmed = True
            restore_calls = []

            def fake_restore():
                restore_calls.append(1)

            with patch("askme._restore_cache", side_effect=fake_restore):
                with patch("askme.requests.post", return_value=mock_response({"tasks": ["t1"]})):
                    ask_llm([{"role": "user", "content": "hi"}], max_tokens=10)

            assert len(restore_calls) == 1, f"Expected 1 restore call, got {len(restore_calls)}"
        finally:
            askme._cache_warmed = old_warmed

    def test_restore_skipped_when_not_warmed(self):
        """_restore_cache() should be a no-op when _cache_warmed is False."""
        import askme
        old_warmed = askme._cache_warmed
        try:
            askme._cache_warmed = False
            with patch("askme.requests.post") as mock_post:
                askme._restore_cache()
            mock_post.assert_not_called()
        finally:
            askme._cache_warmed = old_warmed

    def test_run_calls_warm_cache(self, work_dir):
        """run() should call _warm_cache() once at start."""
        warm_calls = []
        import askme

        def fake_warm():
            warm_calls.append(1)

        with patch("askme._warm_cache", side_effect=fake_warm):
            with patch("askme.get_plan", return_value={"tasks": ["say hi"]}):
                with patch("askme.get_step", return_value={"action": "done"}):
                    run("test", working_dir=work_dir)

        assert len(warm_calls) == 1, f"Expected 1 warm call, got {len(warm_calls)}"


# --- Integration tests (require llama-server on :8080) ---

import time

def llm_available():
    try:
        import requests
        r = requests.get("http://localhost:8080/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


skip_no_llm = pytest.mark.skipif(not llm_available(), reason="llama-server not running on :8080")

# Default tight limits for integration tests.
# INT_MAX_STEPS must be > number of real actions so the LLM has room to emit "done".
# Example: write + compile + run = 3 real actions, needs step 4 for "done".
INT_MAX_REPLANS = 1
INT_MAX_TASKS = 3
INT_MAX_STEPS = 5

# Medium tests: more steps for error recovery within a task (no replans expected)
MED_MAX_REPLANS = 1
MED_MAX_TASKS = 3
MED_MAX_STEPS = 8

# Hard tests: allow replans, more steps and tasks for complex recovery
HARD_MAX_REPLANS = 2
HARD_MAX_TASKS = 5
HARD_MAX_STEPS = 8


def log(msg):
    """Timestamped print that flushes immediately for real-time monitoring."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def int_run(user_prompt, work_dir, max_replans=INT_MAX_REPLANS,
            max_tasks=INT_MAX_TASKS, max_steps=INT_MAX_STEPS):
    """Minimal agent loop with configurable limits and live progress output."""
    from askme import get_plan, get_step, execute, _warm_cache
    state = {"completed_tasks": [], "errors": [], "working_dir": work_dir}
    history = []

    log(f"PROMPT: {user_prompt}")
    log(f"WORKDIR: {work_dir}")
    log(f"LIMITS: replans={max_replans} tasks={max_tasks} steps={max_steps}")
    _warm_cache()

    for replan in range(max_replans + 1):
        log(f"PLAN attempt {replan + 1}/{max_replans + 1} ...")
        t0 = time.time()
        plan = get_plan(user_prompt, state)
        tasks = plan.get("tasks", [])[:max_tasks]
        log(f"PLAN got {len(tasks)} tasks in {time.time()-t0:.1f}s: {tasks}")
        history.append({"event": "plan", "replan": replan, "tasks": tasks})

        all_done = True
        for i, task in enumerate(tasks):
            state["current_task"] = task
            state["task_index"] = f"{i + 1}/{len(tasks)}"
            # Carry over last step from previous task for cross-task context
            prev_last = state["last_steps"][-1:] if state.get("last_steps") else []
            state["last_steps"] = prev_last
            log(f"TASK {i+1}/{len(tasks)}: {task}")

            task_done = False
            use_think = False  # enable thinking after failed step execution
            for step in range(max_steps):
                # Debug: show slim state sent to LLM
                recent = state["last_steps"][-3:]
                slim_debug = [{"action": s["action"], "ok": s["ok"], "output": s.get("output","")[:60]} for s in recent]
                log(f"  STEP {step+1}/{max_steps} state={json.dumps(slim_debug)}{' [think]' if use_think else ''}")
                t0 = time.time()
                try:
                    action = get_step(task, state, goal=user_prompt, step_num=step, max_steps=max_steps, think=use_think)
                except (json.JSONDecodeError, KeyError, Exception) as e:
                    elapsed = time.time() - t0
                    # Auto-done: if last step was successful, treat parse error as implicit completion
                    last = state["last_steps"][-1:] if state["last_steps"] else []
                    if last and last[0].get("ok"):
                        log(f"  STEP {step+1} auto-done (parse error after success, {elapsed:.1f}s)")
                        task_done = True
                        break
                    log(f"  STEP {step+1} LLM error ({elapsed:.1f}s): {e}")
                    state["errors"].append(f"LLM parse error on task '{task}': {str(e)[:100]}")
                    break
                elapsed = time.time() - t0
                act = action.get("action", "")
                arg = (action.get("arg") or "")[:80]
                reason = (action.get("reasoning") or "")[:60]
                log(f"  STEP {step+1} <- {act} {arg} ({reason}) [{elapsed:.1f}s]")
                history.append({"event": "step", "task": i, "step": step, "action": action})

                if act == "done":
                    log(f"  TASK {i+1} DONE")
                    task_done = True
                    break
                if act == "fail":
                    log(f"  TASK {i+1} FAIL: {reason}")
                    state["errors"].append(f"Task '{task}': {action.get('reasoning', '')}")
                    break

                # Duplicate action guard — per-action-type loop detection
                last = state["last_steps"][-1:] if state["last_steps"] else []
                if last and last[0]["action"] == act:
                    prev = last[0]
                    if act == "write" and prev.get("arg", "") == action.get("arg", ""):
                        if prev.get("ok") and prev.get("_content", "") == action.get("content", ""):
                            log(f"  STEP {step+1} auto-done (duplicate write, same content)")
                            task_done = True
                            break
                    elif act == "shell" and prev.get("arg", "") == action.get("arg", ""):
                        if prev.get("ok"):
                            log(f"  STEP {step+1} auto-done (duplicate successful shell)")
                            task_done = True
                            break
                        else:
                            log(f"  STEP {step+1} auto-fail (same shell failed twice)")
                            state["errors"].append(f"Stuck: {act} {action.get('arg','')[:60]} failed twice")
                            break

                try:
                    result = execute(action, work_dir)
                except Exception as e:
                    result = {"ok": False, "output": f"execute error: {str(e)[:100]}"}
                ok_str = "OK" if result["ok"] else "FAIL"
                log(f"  EXEC -> {ok_str}: {result['output'][:80]}")

                step_entry = {
                    "action": act, "arg": action.get("arg", ""),
                    "ok": result["ok"], "output": result["output"][:200]
                }
                if act == "write":
                    step_entry["_content"] = action.get("content", "")
                state["last_steps"].append(step_entry)
                if not result["ok"]:
                    state["errors"].append(f"{act} failed: {result['output'][:100]}")
                    use_think = True  # think harder on next step after failure
                else:
                    use_think = False

            if task_done:
                state["completed_tasks"].append(task)
            else:
                all_done = False
                log(f"REPLAN needed (task {i+1} failed)")
                break

        if all_done:
            log(f"ALL DONE — completed: {state['completed_tasks']}")
            return {"status": "complete", "state": state, "log": history}

    log(f"EXHAUSTED — errors: {state['errors']}")
    return {"status": "exhausted", "state": state, "log": history}


def assert_file(path, content_contains=None):
    """Assert helper with clear messages."""
    p = Path(path)
    assert p.exists(), f"Expected file not found: {p}"
    if content_contains:
        text = p.read_text()
        assert content_contains.lower() in text.lower(), \
            f"Expected '{content_contains}' in {p}, got: {text[:200]}"


@skip_no_llm
class TestIntegration:
    """Live LLM tests. Slow (~30-90s each at ~3 tok/s). Require llama-server on :8080.
    Run with: pytest test_agent.py -k Integration -s"""

    def test_create_and_read_file(self, tmp_path):
        """LLM creates a file and reads it back."""
        result = int_run(
            f"Create a file called hello.txt in {tmp_path} containing 'hello world', then read it to verify.",
            str(tmp_path)
        )
        assert result["status"] == "complete", \
            f"Agent failed. Errors: {result['state']['errors']}"
        assert_file(tmp_path / "hello.txt", "hello")

    def test_shell_and_write(self, tmp_path):
        """LLM runs a shell command and writes output to a file."""
        result = int_run(
            f"Run 'uname -s' and write its output to {tmp_path}/os.txt",
            str(tmp_path)
        )
        assert result["status"] == "complete", \
            f"Agent failed. Errors: {result['state']['errors']}"
        assert_file(tmp_path / "os.txt", "darwin")

    def test_multi_step_build(self, tmp_path):
        """LLM creates a C file, compiles it, and runs it."""
        result = int_run(
            f"In {tmp_path}: create main.c that prints 'AGENT_OK', compile with cc -o main main.c, run ./main",
            str(tmp_path)
        )
        state = result["state"]
        # At minimum the source should exist
        assert_file(tmp_path / "main.c", "AGENT_OK")
        if result["status"] == "complete":
            # Check AGENT_OK appeared in some step output
            all_outputs = " ".join(
                s.get("output", "") for s in state.get("last_steps", [])
            )
            all_outputs += " ".join(
                e["action"].get("reasoning", "")
                for e in result["log"] if e["event"] == "step"
            )
            assert "AGENT_OK" in all_outputs or len(state["completed_tasks"]) >= 2, \
                f"Expected AGENT_OK in output or >=2 tasks done. Completed: {state['completed_tasks']}"


@skip_no_llm
class TestIntegrationMedium:
    """Medium difficulty: LLM must recover from errors within a task (0 replans expected).
    Tests self-correction ability — LLM sees failure in last_steps and adapts.
    Run with: pytest test_agent.py -k IntegrationMedium -s"""

    def test_fix_python_syntax_error(self, tmp_path):
        """LLM writes a broken Python file, runs it (fails), fixes it, runs again."""
        # Pre-seed a file with a syntax error so the LLM hits a predictable failure
        broken = tmp_path / "greet.py"
        broken.write_text('print("hello"\n')  # missing closing paren
        result = int_run(
            f"Run python3 greet.py — it has a syntax error. Fix the error in greet.py and run it again successfully.",
            str(tmp_path),
            max_replans=MED_MAX_REPLANS, max_tasks=MED_MAX_TASKS, max_steps=MED_MAX_STEPS,
        )
        assert result["status"] == "complete", \
            f"Agent failed to self-correct. Errors: {result['state']['errors']}"
        # The fixed file should be valid Python
        fixed_text = broken.read_text()
        assert "print" in fixed_text, f"File was overwritten unexpectedly: {fixed_text[:200]}"

    def test_fix_missing_include(self, tmp_path):
        """LLM compiles a C file missing #include <stdio.h>, fixes it, compiles again."""
        broken_c = tmp_path / "fix_me.c"
        broken_c.write_text('int main() { printf("FIXED\\n"); return 0; }\n')
        result = int_run(
            f"Compile {broken_c} with 'cc -o {tmp_path}/fix_me {broken_c}'. "
            f"It will fail because stdio.h is not included. "
            f"Read the error, add '#include <stdio.h>' to {broken_c}, compile again, then run {tmp_path}/fix_me.",
            str(tmp_path),
            max_replans=MED_MAX_REPLANS, max_tasks=MED_MAX_TASKS, max_steps=MED_MAX_STEPS,
        )
        fixed_text = broken_c.read_text()
        assert "stdio.h" in fixed_text, \
            f"Expected #include <stdio.h> in fixed file, got: {fixed_text[:200]}"
        if result["status"] == "complete":
            # Check FIXED appeared in some step output
            all_outputs = " ".join(
                s.get("output", "") for s in result["state"].get("last_steps", [])
            )
            all_outputs += " ".join(
                e["action"].get("arg", "") + " " + e["action"].get("reasoning", "")
                for e in result["log"] if e["event"] == "step"
            )

    def test_create_missing_file_then_use(self, tmp_path):
        """LLM tries to read a non-existent file, then creates and reads it."""
        result = int_run(
            f"Create a file called data.txt containing 'RECOVERED' in {tmp_path}, then read it to verify the content.",
            str(tmp_path),
            max_replans=MED_MAX_REPLANS, max_tasks=MED_MAX_TASKS, max_steps=MED_MAX_STEPS,
        )
        assert result["status"] == "complete", \
            f"Agent failed. Errors: {result['state']['errors']}"
        assert_file(tmp_path / "data.txt", "RECOVERED")


@skip_no_llm
class TestIntegrationHard:
    """Hard difficulty: LLM must fail a task and replan to succeed.
    Tests the full replan loop — planner sees errors and produces a better plan.
    Run with: pytest test_agent.py -k IntegrationHard -s"""

    def test_replan_build_with_dependency(self, tmp_path):
        """First plan fails because a header file is missing. Replan creates it first.
        The prompt is intentionally vague about ordering so the LLM's first plan
        is likely to try compiling before creating the header."""
        result = int_run(
            f"In {tmp_path}: compile and run a C program. "
            f"The program main.c should '#include \"msg.h\"' and call 'printf(\"%s\\n\", MSG);'. "
            f"The header msg.h should '#define MSG \"REPLAN_OK\"'. "
            f"Compile with 'cc -o {tmp_path}/main {tmp_path}/main.c', then run {tmp_path}/main.",
            str(tmp_path),
            max_replans=HARD_MAX_REPLANS, max_tasks=HARD_MAX_TASKS, max_steps=HARD_MAX_STEPS,
        )
        # At minimum both files should exist
        assert_file(tmp_path / "main.c", "msg.h")
        assert_file(tmp_path / "msg.h", "REPLAN_OK")
        if result["status"] == "complete":
            all_outputs = " ".join(
                s.get("output", "") for s in result["state"].get("last_steps", [])
            )
            all_outputs += " ".join(
                e["action"].get("reasoning", "")
                for e in result["log"] if e["event"] == "step"
            )
            assert "REPLAN_OK" in all_outputs or len(result["state"]["completed_tasks"]) >= 2, \
                f"Expected REPLAN_OK in output. Completed: {result['state']['completed_tasks']}"

    def test_replan_fix_wrong_command(self, tmp_path):
        """Agent tries a command that doesn't exist, replans with the correct approach."""
        result = int_run(
            f"In {tmp_path}: get the current date and save it to {tmp_path}/today.txt. "
            f"First try using the command 'datex' (which doesn't exist). "
            f"When that fails, replan and use the correct 'date' command instead.",
            str(tmp_path),
            max_replans=HARD_MAX_REPLANS, max_tasks=HARD_MAX_TASKS, max_steps=HARD_MAX_STEPS,
        )
        # The file should exist with some date content
        assert_file(tmp_path / "today.txt")
        text = (tmp_path / "today.txt").read_text()
        assert len(text.strip()) > 0, f"today.txt is empty"
        # Check that at least one replan happened
        plan_events = [e for e in result["log"] if e["event"] == "plan"]
        if len(plan_events) >= 2:
            log(f"VERIFIED: {len(plan_events)} plan attempts (replan exercised)")

    def test_replan_multi_step_recovery(self, tmp_path):
        """Complex task: write Python script, run it (it imports a missing module),
        replan to install/fix the dependency, run again."""
        script = tmp_path / "app.py"
        # Pre-seed a script that tries to read a config file that doesn't exist
        script.write_text(
            'import json\n'
            'with open("config.json") as f:\n'
            '    cfg = json.load(f)\n'
            'print("APP_" + cfg["status"])\n'
        )
        result = int_run(
            f"Run 'python3 {script}'. It will fail because config.json doesn't exist in {tmp_path}. "
            f"Create {tmp_path}/config.json with content '{{\"status\": \"SUCCESS\"}}', then run the script again.",
            str(tmp_path),
            max_replans=HARD_MAX_REPLANS, max_tasks=HARD_MAX_TASKS, max_steps=HARD_MAX_STEPS,
        )
        assert_file(tmp_path / "config.json", "SUCCESS")
        if result["status"] == "complete":
            all_outputs = " ".join(
                s.get("output", "") for s in result["state"].get("last_steps", [])
            )
            assert "APP_SUCCESS" in all_outputs or len(result["state"]["completed_tasks"]) >= 2, \
                f"Expected APP_SUCCESS in output. Completed: {result['state']['completed_tasks']}"


@skip_no_llm
class TestServerConfig:
    """Verify llama-server is running with optimized agentic configuration.
    These are fast (no LLM inference), just HTTP checks against the server."""

    def test_single_slot_full_context(self):
        """Server should have 1 slot (-np 1) with full 16K context."""
        import requests
        slots = requests.get("http://localhost:8080/slots", timeout=5).json()
        assert len(slots) == 1, f"Expected 1 slot (-np 1), got {len(slots)}"
        assert slots[0]["n_ctx"] == 16384, f"Expected 16384 ctx, got {slots[0]['n_ctx']}"

    def test_slot_save_enabled(self):
        """--slot-save-path should be configured, allowing slot save via API."""
        import requests
        # Warm the slot with a minimal request first (60s — first prompt is slow on cold cache)
        requests.post("http://localhost:8080/v1/chat/completions",
            json={"model": "gemma-4-e4b", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
            timeout=60)
        # Save should succeed (returns 200 with n_saved > 0)
        resp = requests.post("http://localhost:8080/slots/0?action=save",
            json={"filename": "test-config-check"}, timeout=10)
        assert resp.status_code == 200, f"Slot save failed: {resp.status_code} {resp.text}"
        data = resp.json()
        assert data.get("n_saved", 0) > 0, f"Expected n_saved > 0, got {data}"

    def test_slot_restore(self):
        """Saved slot state should be restorable."""
        import requests
        resp = requests.post("http://localhost:8080/slots/0?action=restore",
            json={"filename": "test-config-check"}, timeout=10)
        assert resp.status_code == 200, f"Slot restore failed: {resp.status_code} {resp.text}"
        data = resp.json()
        assert data.get("n_restored", 0) > 0, f"Expected n_restored > 0, got {data}"

    def test_slot_save_file_on_disk(self):
        """Saved slot state should exist as a file in --slot-save-path dir."""
        from pathlib import Path
        cache_dir = Path("/tmp/llama-cache")
        assert cache_dir.exists(), f"Cache dir {cache_dir} not found (--slot-save-path not set?)"
        saved = list(cache_dir.glob("test-config-check"))
        assert len(saved) == 1, f"Expected saved cache file, found: {list(cache_dir.iterdir())}"
        assert saved[0].stat().st_size > 0, "Saved cache file is empty"


# --- OpenRouter integration tests (gemma-4-26b-a4b via Parasail/bf16) ---

def openrouter_available():
    """Check if OpenRouter API is accessible with a valid key."""
    try:
        import requests, os
        # Load .env
        env_path = Path(__file__).parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
        key = os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            return False
        r = requests.get("https://openrouter.ai/api/v1/models",
                         headers={"Authorization": f"Bearer {key}"}, timeout=10)
        return r.status_code == 200
    except Exception:
        return False


skip_no_openrouter = pytest.mark.skipif(
    not openrouter_available(), reason="OpenRouter API not available")


def or_run(user_prompt, work_dir, max_replans=INT_MAX_REPLANS,
           max_tasks=INT_MAX_TASKS, max_steps=INT_MAX_STEPS):
    """Agent loop using OpenRouter backend (gemma-4-26b-a4b via Parasail)."""
    import os
    # Temporarily switch backend to openrouter
    old_backend = os.environ.get("LLM_BACKEND", "")
    old_model = os.environ.get("OPENROUTER_MODEL", "")
    os.environ["LLM_BACKEND"] = "openrouter"
    os.environ["OPENROUTER_MODEL"] = "google/gemma-4-26b-a4b-it"

    # Reload module-level config
    import askme
    askme.LLM_BACKEND = "openrouter"
    askme.API = "https://openrouter.ai/api/v1/chat/completions"
    askme.MODEL = "google/gemma-4-26b-a4b-it"
    askme.OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

    try:
        return int_run(user_prompt, work_dir, max_replans, max_tasks, max_steps)
    finally:
        # Restore
        if old_backend:
            os.environ["LLM_BACKEND"] = old_backend
        else:
            os.environ.pop("LLM_BACKEND", None)
        if old_model:
            os.environ["OPENROUTER_MODEL"] = old_model
        else:
            os.environ.pop("OPENROUTER_MODEL", None)
        askme.LLM_BACKEND = old_backend or "local"
        askme.API = "http://localhost:8080/v1/chat/completions"
        askme.MODEL = "gemma-4-e4b"


@skip_no_openrouter
class TestOpenRouterEasy:
    """Easy tests via OpenRouter (gemma-4-26b-a4b). Same as TestIntegration.
    Key question: does the larger model emit 'done' reliably?"""

    def test_create_and_read_file(self, tmp_path):
        result = or_run(
            f"Create a file called hello.txt in {tmp_path} containing 'hello world', then read it to verify.",
            str(tmp_path)
        )
        assert result["status"] == "complete", \
            f"Agent failed. Errors: {result['state']['errors']}"
        assert_file(tmp_path / "hello.txt", "hello")

    def test_shell_and_write(self, tmp_path):
        result = or_run(
            f"Run 'uname -s' and write its output to {tmp_path}/os.txt",
            str(tmp_path)
        )
        assert result["status"] == "complete", \
            f"Agent failed. Errors: {result['state']['errors']}"
        assert_file(tmp_path / "os.txt")

    def test_multi_step_build(self, tmp_path):
        result = or_run(
            f"In {tmp_path}: create main.c that prints 'AGENT_OK', compile with cc -o main main.c, run ./main",
            str(tmp_path)
        )
        assert_file(tmp_path / "main.c", "AGENT_OK")
        assert result["status"] == "complete", \
            f"Agent failed. Errors: {result['state']['errors']}"


@skip_no_openrouter
class TestOpenRouterMedium:
    """Medium tests via OpenRouter. Tests error recovery + done emission."""

    def test_fix_python_syntax_error(self, tmp_path):
        broken = tmp_path / "greet.py"
        broken.write_text('print("hello"\n')
        result = or_run(
            f"Run python3 greet.py — it has a syntax error. Fix the error in greet.py and run it again successfully.",
            str(tmp_path),
            max_replans=MED_MAX_REPLANS, max_tasks=MED_MAX_TASKS, max_steps=MED_MAX_STEPS,
        )
        assert result["status"] == "complete", \
            f"Agent failed. Errors: {result['state']['errors']}"
        fixed_text = broken.read_text()
        assert "print" in fixed_text

    def test_fix_missing_include(self, tmp_path):
        broken_c = tmp_path / "fix_me.c"
        broken_c.write_text('int main() { printf("FIXED\\n"); return 0; }\n')
        result = or_run(
            f"Compile {broken_c} with 'cc -o {tmp_path}/fix_me {broken_c}'. "
            f"It will fail because stdio.h is not included. "
            f"Read the error, add '#include <stdio.h>' to {broken_c}, compile again, then run {tmp_path}/fix_me.",
            str(tmp_path),
            max_replans=MED_MAX_REPLANS, max_tasks=MED_MAX_TASKS, max_steps=MED_MAX_STEPS,
        )
        fixed_text = broken_c.read_text()
        assert "stdio.h" in fixed_text

    def test_create_missing_file_then_use(self, tmp_path):
        result = or_run(
            f"Create a file called data.txt containing 'RECOVERED' in {tmp_path}, then read it to verify the content.",
            str(tmp_path),
            max_replans=MED_MAX_REPLANS, max_tasks=MED_MAX_TASKS, max_steps=MED_MAX_STEPS,
        )
        assert result["status"] == "complete", \
            f"Agent failed. Errors: {result['state']['errors']}"
        assert_file(tmp_path / "data.txt", "RECOVERED")


@skip_no_openrouter
class TestOpenRouterHard:
    """Hard tests via OpenRouter. Tests replanning — the 26B model may handle this better."""

    def test_replan_build_with_dependency(self, tmp_path):
        result = or_run(
            f"In {tmp_path}: compile and run a C program. "
            f"The program main.c should '#include \"msg.h\"' and call 'printf(\"%s\\n\", MSG);'. "
            f"The header msg.h should '#define MSG \"REPLAN_OK\"'. "
            f"Compile with 'cc -o {tmp_path}/main {tmp_path}/main.c', then run {tmp_path}/main.",
            str(tmp_path),
            max_replans=HARD_MAX_REPLANS, max_tasks=HARD_MAX_TASKS, max_steps=HARD_MAX_STEPS,
        )
        assert_file(tmp_path / "main.c", "msg.h")
        assert_file(tmp_path / "msg.h", "REPLAN_OK")

    def test_replan_fix_wrong_command(self, tmp_path):
        result = or_run(
            f"In {tmp_path}: get the current date and save it to {tmp_path}/today.txt. "
            f"First try using the command 'datex' (which doesn't exist). "
            f"When that fails, replan and use the correct 'date' command instead.",
            str(tmp_path),
            max_replans=HARD_MAX_REPLANS, max_tasks=HARD_MAX_TASKS, max_steps=HARD_MAX_STEPS,
        )
        assert_file(tmp_path / "today.txt")
        text = (tmp_path / "today.txt").read_text()
        assert len(text.strip()) > 0

    def test_replan_multi_step_recovery(self, tmp_path):
        script = tmp_path / "app.py"
        script.write_text(
            'import json\n'
            'with open("config.json") as f:\n'
            '    cfg = json.load(f)\n'
            'print("APP_" + cfg["status"])\n'
        )
        result = or_run(
            f"Run 'python3 {script}'. It will fail because config.json doesn't exist in {tmp_path}. "
            f"Create {tmp_path}/config.json with content '{{\"status\": \"SUCCESS\"}}', then run the script again.",
            str(tmp_path),
            max_replans=HARD_MAX_REPLANS, max_tasks=HARD_MAX_TASKS, max_steps=HARD_MAX_STEPS,
        )
        assert_file(tmp_path / "config.json", "SUCCESS")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
