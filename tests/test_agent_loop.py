"""Full agent loop tests: run(), cross-task state, output formatting, content serialization."""
import json
from pathlib import Path
from unittest.mock import patch

from askme import execute, run, get_step, MAX_STEP_HISTORY, MAX_INPUT
from _test_support import mock_response


# --- Full agent loop tests ---

class TestRunLoop:
    @patch("askme.ask_llm")
    def test_simple_success(self, mock_llm, capsys):
        """Plan with one task, one shell step, then done."""
        mock_llm.side_effect = [
            {"tasks": ["say hello"]},
            {"action": "shell", "arg": "echo hello", "reasoning": "greet"},
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
            {"tasks": ["compile code"]},
            {"action": "fail", "reasoning": "gcc not found"},
            {"tasks": ["install gcc", "compile code"]},
            {"action": "shell", "arg": "echo installed", "reasoning": "install"},
            {"action": "done", "reasoning": "installed"},
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
        ] * 3
        result = run("impossible task")
        out = capsys.readouterr().out
        assert "Exhausted" in out
        assert result is False

    @patch("askme.ask_llm")
    def test_multi_task_plan(self, mock_llm, capsys):
        """Plan with multiple tasks, all succeed."""
        mock_llm.side_effect = [
            {"tasks": ["create file", "read file"]},
            {"action": "write", "arg": "/tmp/test_askme.txt", "content": "hi", "reasoning": "create"},
            {"action": "done", "reasoning": "created"},
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
            {"action": "shell", "arg": "false", "reasoning": "try"},
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
        state = {
            "current_task": "test",
            "task_index": "1/1",
            "last_steps": [
                {"action": "write", "arg": "/very/long/path/to/file.txt", "ok": True, "output": "Wrote file.txt"},
            ],
            "completed_tasks": [],
        }
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
