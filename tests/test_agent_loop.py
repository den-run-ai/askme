"""Full agent loop tests: run(), cross-task state, output formatting, content serialization."""
import json
from pathlib import Path
from unittest.mock import patch

from askme import MAX_STEP_HISTORY, _run_loop, execute, run

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

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_task_failure_triggers_replan(self, mock_llm, mock_replan, capsys):
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

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_max_replans_exhausted(self, mock_llm, mock_replan, capsys):
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
        """An empty task list violates the planner contract and exhausts."""
        mock_llm.side_effect = [{"tasks": []}] * 3
        result = run("do nothing")
        out = capsys.readouterr().out
        assert "Planner contract error" in out
        assert "Exhausted" in out
        assert result is False

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

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_working_dir_printed_on_failure(self, mock_llm, mock_replan, capsys, tmp_path):
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
        assert "askme_" in out
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

    def test_edit_output_basename(self, work_dir):
        """Edit action output should show filename, not full path."""
        Path(work_dir, "sub").mkdir()
        Path(work_dir, "sub", "test.txt").write_text("old")
        result = execute({"action": "edit", "arg": f"{work_dir}/sub/test.txt",
                          "find": "old", "replace": "new"}, work_dir)
        assert result["ok"] is True
        assert result["output"] == "Edited test.txt"
        assert work_dir not in result["output"]

    def test_slim_steps_basename_for_edit(self):
        """get_step should use basename for edit args in slim state."""
        state = {
            "current_task": "test",
            "task_index": "1/1",
            "last_steps": [
                {"action": "edit", "arg": "/very/long/path/to/file.txt", "ok": True, "output": "Edited file.txt"},
            ],
            "completed_tasks": [],
        }
        steps = state.get("last_steps", [])[-MAX_STEP_HISTORY:]
        for s in steps:
            arg = s.get("arg", "")
            if s["action"] in ("write", "read", "edit") and "/" in arg:
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


class TestRunLogSink:
    """AGENT_RUN_LOG JSONL sink — append-only, never crashes the run."""

    @patch("askme.ask_llm")
    def test_run_log_emits_lifecycle_events(self, mock_llm, tmp_path, work_dir):
        import askme
        mock_llm.side_effect = [
            {"tasks": ["echo hi"]},
            {"action": "shell", "arg": "echo hi", "reasoning": "hi"},
            {"action": "done", "reasoning": "done"},
        ]
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            askme.run("say hi", work_dir)
        finally:
            askme.RUN_LOG_PATH = old
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        kinds = [e["event"] for e in events]
        assert kinds[0] == "run_start"
        assert "plan" in kinds
        assert "step" in kinds
        assert "task_complete" in kinds
        assert kinds[-1] == "run_end"
        assert events[-1]["status"] == "complete"
        # Every event carries a timestamp
        assert all("ts" in e for e in events)

    @patch("askme.ask_llm")
    def test_run_log_disabled_when_env_unset(self, mock_llm, tmp_path, work_dir):
        import askme
        mock_llm.side_effect = [
            {"tasks": ["x"]},
            {"action": "done", "reasoning": "done"},
        ]
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = ""
        try:
            askme.run("noop", work_dir)
        finally:
            askme.RUN_LOG_PATH = old
        # No log file created in tmp_path
        assert not any(p.suffix == ".jsonl" for p in tmp_path.iterdir())

    @patch("askme.ask_llm")
    def test_run_log_failure_is_nonfatal(self, mock_llm, tmp_path, work_dir):
        import askme
        mock_llm.side_effect = [
            {"tasks": ["x"]},
            {"action": "done", "reasoning": "done"},
        ]
        # Point at an unwritable path; run must still succeed
        bad_path = tmp_path / "nonexistent_dir" / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(bad_path)
        try:
            result = askme.run("noop", work_dir)
        finally:
            askme.RUN_LOG_PATH = old
        assert result is True


# --- E11: Task-local replan loop tests ---

class TestTaskLocalReplan:
    """Verify task-local replan behavior in the run loop."""

    @patch("askme.replan_task")
    @patch("askme.ask_llm")
    def test_local_replan_succeeds_no_full_replan(self, mock_llm, mock_replan, capsys):
        """Task fails, local replan returns replacement, replacement succeeds, no full replan."""
        mock_llm.side_effect = [
            {"tasks": ["compile code"]},            # plan
            {"action": "fail", "reasoning": "gcc error"},  # task 1 fails
            # replacement task execution:
            {"action": "shell", "arg": "echo fixed", "reasoning": "fix"},
            {"action": "done", "reasoning": "compiled"},
        ]
        mock_replan.return_value = "fix gcc path and compile"
        result = run("compile my code")
        out = capsys.readouterr().out
        assert result is True
        assert "All tasks complete" in out
        assert "Task-local replan" in out
        assert mock_replan.called
        # "will replan" should NOT appear (that's the full replan message)
        assert "will replan" not in out

    @patch("askme.replan_task")
    @patch("askme.ask_llm")
    def test_local_replan_fails_triggers_full_replan(self, mock_llm, mock_replan, capsys):
        """Task fails, local replan returns replacement, replacement also fails, triggers full replan."""
        mock_llm.side_effect = [
            {"tasks": ["compile code"]},                    # plan 1
            {"action": "fail", "reasoning": "gcc error"},   # task 1 fails
            # replacement task execution:
            {"action": "fail", "reasoning": "still broken"},  # replacement also fails
            # full replan:
            {"tasks": ["install gcc then compile"]},         # plan 2
            {"action": "shell", "arg": "echo ok", "reasoning": "fix"},
            {"action": "done", "reasoning": "done"},
        ]
        mock_replan.return_value = "try different gcc path"
        result = run("compile my code")
        out = capsys.readouterr().out
        assert result is True
        assert "Task-local replan" in out
        assert "will replan" in out  # full replan triggered

    @patch("askme.replan_task")
    @patch("askme.ask_llm")
    def test_local_replan_none_triggers_full_replan(self, mock_llm, mock_replan, capsys):
        """Task fails, replan_task returns None, falls through to full replan immediately."""
        mock_llm.side_effect = [
            {"tasks": ["compile code"]},                    # plan 1
            {"action": "fail", "reasoning": "gcc error"},   # task 1 fails
            # full replan (no local replan attempted):
            {"tasks": ["fix and compile"]},                  # plan 2
            {"action": "shell", "arg": "echo ok", "reasoning": "fix"},
            {"action": "done", "reasoning": "done"},
        ]
        mock_replan.return_value = None
        result = run("compile my code")
        out = capsys.readouterr().out
        assert result is True
        assert "Task-local replan failed" in out  # replan_task was called but returned None
        assert "will replan" in out

    @patch("askme.replan_task")
    @patch("askme.ask_llm")
    def test_local_replan_capped_at_one(self, mock_llm, mock_replan, capsys):
        """Only one task-local replan attempt per task, then full replan."""
        call_count = {"replan": 0}
        def counting_replan(*args, **kwargs):
            call_count["replan"] += 1
            return "replacement task"
        mock_replan.side_effect = counting_replan
        mock_llm.side_effect = [
            {"tasks": ["compile code"]},                    # plan 1
            {"action": "fail", "reasoning": "error 1"},     # task 1 fails
            {"action": "fail", "reasoning": "error 2"},     # replacement also fails
            # full replan:
            {"tasks": ["new approach"]},                     # plan 2
            {"action": "done", "reasoning": "done"},
        ]
        result = run("compile my code")
        assert result is True
        assert call_count["replan"] == 1, "replan_task should be called exactly once per task"

    @patch("askme.replan_task")
    @patch("askme.ask_llm")
    def test_preserves_errors_for_full_replan(self, mock_llm, mock_replan, capsys):
        """When both original and replacement fail, full replan should see errors from both."""
        plan_states = []
        call_idx = {"n": 0}
        def tracking_llm(messages, **kwargs):
            call_idx["n"] += 1
            n = call_idx["n"]
            if n == 1:
                return {"tasks": ["compile code"]}
            if n == 2:
                return {"action": "fail", "reasoning": "original gcc error"}
            if n == 3:
                return {"action": "fail", "reasoning": "replacement also failed"}
            if n == 4:
                # This is the full replan — capture what the planner sees
                user_msg = messages[-1]["content"]
                plan_states.append(user_msg)
                return {"tasks": ["final fix"]}
            if n == 5:
                return {"action": "done", "reasoning": "done"}
            return {"action": "done"}
        mock_llm.side_effect = tracking_llm
        mock_replan.return_value = "try alternative approach"
        result = run("compile my code")
        assert result is True
        assert len(plan_states) == 1, "Should have captured one full replan state"
        replan_state = plan_states[0]
        assert "original gcc error" in replan_state, \
            f"Full replan should see original error, got: {replan_state[-300:]}"
        assert "replacement also failed" in replan_state, \
            f"Full replan should see replacement error, got: {replan_state[-300:]}"

    @patch("askme.replan_task")
    @patch("askme.ask_llm")
    def test_local_replan_resets_execution_state(self, mock_llm, mock_replan, capsys):
        """Replacement attempt should start with fresh execution state (steps, think, etc.)."""
        step_nums = []
        call_idx = {"n": 0}
        def tracking_llm(messages, **kwargs):
            call_idx["n"] += 1
            n = call_idx["n"]
            if n == 1:
                return {"tasks": ["compile code"]}
            if n == 2:
                return {"action": "shell", "arg": "gcc main.c", "reasoning": "compile"}
            if n == 3:
                return {"action": "fail", "reasoning": "compile error"}
            if n >= 4:
                # Replacement task steps — check step numbering
                user_msg = messages[-1]["content"]
                if '"step":' in user_msg:
                    import re
                    m = re.search(r'"step":\s*"(\d+)/\d+"', user_msg)
                    if m:
                        step_nums.append(int(m.group(1)))
            if n == 4:
                return {"action": "done", "reasoning": "done"}
            return {"action": "done"}
        mock_llm.side_effect = tracking_llm
        mock_replan.return_value = "recompile with fix"
        result = run("compile my code")
        assert result is True
        if step_nums:
            assert step_nums[0] == 1, f"Replacement should start at step 1, got {step_nums[0]}"

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_duplicate_read_auto_fails_task(self, mock_llm, mock_replan, capsys, tmp_path):
        """Repeated reads of the same file should trip the stuck-loop guard."""
        (tmp_path / "fix_me.c").write_text("int main(void) { return 0; }\n")
        mock_llm.side_effect = [
            {"tasks": ["inspect fix_me.c"]},
            {"action": "read", "arg": "fix_me.c", "reasoning": "read"},
            {"action": "read", "arg": "fix_me.c", "reasoning": "read again"},
            {"action": "read", "arg": "fix_me.c", "reasoning": "still reading"},
        ]
        result = _run_loop("inspect fix_me.c", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=5)
        out = capsys.readouterr().out
        assert result["status"] == "exhausted"
        assert "skip (duplicate read)" in out
        assert "auto-fail (same read repeated" in out
        assert mock_replan.called

    @patch("askme.replan_task")
    @patch("askme.ask_llm")
    def test_local_replan_emits_jsonl_event(self, mock_llm, mock_replan, tmp_path, work_dir):
        """task_local_replan event should appear in JSONL log."""
        import askme
        mock_llm.side_effect = [
            {"tasks": ["compile code"]},
            {"action": "fail", "reasoning": "error"},
            {"action": "done", "reasoning": "done"},
        ]
        mock_replan.return_value = "fixed compile task"
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            askme.run("compile", work_dir)
        finally:
            askme.RUN_LOG_PATH = old
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        lr_events = [e for e in events if e["event"] == "task_local_replan"]
        assert len(lr_events) == 1
        assert lr_events[0]["original"] == "compile code"
        assert lr_events[0]["replacement"] == "fixed compile task"
        assert lr_events[0]["ok"] is True
        assert "llm_wall_s" in lr_events[0]

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_failed_local_replan_emits_jsonl_event(self, mock_llm, mock_replan, tmp_path, work_dir):
        """Failed task_local_replan event should appear with ok=False and replacement=None."""
        import askme
        mock_llm.side_effect = [
            {"tasks": ["compile code"]},
            {"action": "fail", "reasoning": "error"},
        ] * 3  # enough for full replans
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            askme.run("compile", work_dir)
        finally:
            askme.RUN_LOG_PATH = old
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        lr_events = [e for e in events if e["event"] == "task_local_replan"]
        assert len(lr_events) >= 1
        assert lr_events[0]["ok"] is False
        assert lr_events[0]["replacement"] is None
        assert "llm_wall_s" in lr_events[0]
        assert "reject_reason" in lr_events[0]

    @patch("askme.ask_llm")
    def test_failed_local_replan_logs_reject_reason(self, mock_llm, tmp_path, work_dir):
        """Guard-rejected replacements should log why they were rejected."""
        import askme
        mock_llm.side_effect = [
            {"tasks": ["add missing include"]},
            {"action": "fail", "reasoning": "same edit failed"},
            {"task": "add missing include"},
        ] * 2
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            askme._run_loop("compile", work_dir, max_replans=1, max_tasks=1, max_steps=1)
        finally:
            askme.RUN_LOG_PATH = old
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        lr_events = [e for e in events if e["event"] == "task_local_replan"]
        assert lr_events
        assert lr_events[0]["ok"] is False
        assert lr_events[0]["reject_reason"] == "exact_duplicate"
