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
        run("say hello")
        out = capsys.readouterr().out
        assert "All tasks complete" in out

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
        run("compile my code")
        out = capsys.readouterr().out
        assert "will replan" in out
        assert "All tasks complete" in out

    @patch("askme.ask_llm")
    def test_max_replans_exhausted(self, mock_llm, capsys):
        """All plans fail, agent stops after MAX_REPLANS."""
        mock_llm.side_effect = [
            {"tasks": ["do thing"]},
            {"action": "fail", "reasoning": "nope"},
        ] * 3  # 3 replan attempts
        run("impossible task")
        out = capsys.readouterr().out
        assert "Exhausted" in out

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
        run("create and read a file")
        out = capsys.readouterr().out
        assert "All tasks complete" in out

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
        run("run some commands")
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "All tasks complete" in out

    @patch("askme.ask_llm")
    def test_empty_plan(self, mock_llm, capsys):
        """Plan with no tasks completes immediately."""
        mock_llm.side_effect = [
            {"tasks": []},
        ]
        run("do nothing")
        out = capsys.readouterr().out
        assert "All tasks complete" in out


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

# Tight limits for integration: 1 replan, 3 tasks, 5 actions per task
# INT_MAX_STEPS must be > number of real actions so the LLM has room to emit "done".
# Example: write + compile + run = 3 real actions, needs step 4 for "done".
INT_MAX_REPLANS = 1
INT_MAX_TASKS = 3
INT_MAX_STEPS = 5


def log(msg):
    """Timestamped print that flushes immediately for real-time monitoring."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def int_run(user_prompt, work_dir):
    """Minimal agent loop with tight limits and live progress output."""
    from askme import get_plan, get_step, execute
    state = {"completed_tasks": [], "errors": [], "working_dir": work_dir}
    history = []

    log(f"PROMPT: {user_prompt}")
    log(f"WORKDIR: {work_dir}")

    for replan in range(INT_MAX_REPLANS + 1):
        log(f"PLAN attempt {replan + 1}/{INT_MAX_REPLANS + 1} ...")
        t0 = time.time()
        plan = get_plan(user_prompt, state)
        tasks = plan.get("tasks", [])[:INT_MAX_TASKS]
        log(f"PLAN got {len(tasks)} tasks in {time.time()-t0:.1f}s: {tasks}")
        history.append({"event": "plan", "replan": replan, "tasks": tasks})

        all_done = True
        for i, task in enumerate(tasks):
            state["current_task"] = task
            state["task_index"] = f"{i + 1}/{len(tasks)}"
            state["last_steps"] = []
            log(f"TASK {i+1}/{len(tasks)}: {task}")

            task_done = False
            for step in range(INT_MAX_STEPS):
                log(f"  STEP {step+1}/{INT_MAX_STEPS} asking LLM ...")
                t0 = time.time()
                action = get_step(task, state, goal=user_prompt)
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

                result = execute(action, work_dir)
                ok_str = "OK" if result["ok"] else "FAIL"
                log(f"  EXEC -> {ok_str}: {result['output'][:80]}")

                state["last_steps"].append({
                    "action": act, "arg": action.get("arg", ""),
                    "ok": result["ok"], "output": result["output"][:200]
                })
                if not result["ok"]:
                    state["errors"].append(f"{act} failed: {result['output'][:100]}")

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
        # Warm the slot with a minimal request first
        requests.post("http://localhost:8080/v1/chat/completions",
            json={"model": "gemma-4-e4b", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
            timeout=30)
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
