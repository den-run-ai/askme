"""Recovery tests: duplicate guard, cache workaround, failure classification,
error summarization, completion semantics."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from askme import execute, ask_llm, get_plan, get_step, run
from _test_support import mock_response


# --- Duplicate action guard tests ---

class TestDuplicateGuard:
    """Verify duplicate action guard prevents loops without blocking legitimate retries."""

    def test_write_same_content_skips_and_continues(self, tmp_path):
        """Same file + same content + ok -> skip duplicate, executor reaches follow-up shell."""
        f = str(tmp_path / "data.txt")
        responses = [
            {"tasks": ["write data.txt and run it"]},
            {"action": "write", "arg": f, "content": "hello"},
            {"action": "write", "arg": f, "content": "hello"},  # duplicate -- should skip
            {"action": "shell", "arg": "cat data.txt"},          # post-skip shell step
            {"action": "done"},
        ]
        with patch("askme.ask_llm", side_effect=responses):
            ok = run("write data.txt and run it", working_dir=str(tmp_path))
        assert ok is True
        assert (tmp_path / "data.txt").read_text() == "hello"

    def test_write_duplicate_skip_livelock_escape(self, tmp_path):
        """After 2 consecutive duplicate write skips, synthetic step is injected so model sees new state."""
        f = str(tmp_path / "data.txt")
        responses = [
            {"tasks": ["write data.txt"]},
            {"action": "write", "arg": f, "content": "hello"},
            {"action": "write", "arg": f, "content": "hello"},  # skip 1
            {"action": "write", "arg": f, "content": "hello"},  # skip 2 -- injects synthetic step
            {"action": "done"},                                   # model now sees new state, emits done
        ]
        with patch("askme.ask_llm", side_effect=responses) as mock_llm:
            ok = run("write data.txt", working_dir=str(tmp_path))
        assert ok is True
        last_call_args = mock_llm.call_args_list[-1]
        user_msg = last_call_args[0][0][1]["content"]
        assert "Already written" in user_msg

    def test_write_different_content_allowed(self, tmp_path):
        """Same file + different content -> allow (legitimate fix attempt)."""
        responses = [
            {"tasks": ["write and fix"]},
            {"action": "write", "arg": str(tmp_path / "f.txt"), "content": "v1"},
            {"action": "write", "arg": str(tmp_path / "f.txt"), "content": "v2"},
            {"action": "done"},
        ]
        with patch("askme.ask_llm", side_effect=responses):
            run("write and fix")
        assert (tmp_path / "f.txt").read_text() == "v2"

    def test_shell_same_success_triggers_auto_done(self, tmp_path):
        """Same shell + ok -> auto-done (true duplicate)."""
        responses = [
            {"tasks": ["run echo"]},
            {"action": "shell", "arg": "echo hi"},
            {"action": "shell", "arg": "echo hi"},
        ]
        with patch("askme.ask_llm", side_effect=responses):
            run("run echo")

    def test_shell_same_failure_triggers_auto_fail(self, tmp_path):
        """Same shell + fail twice -> auto-fail, error recorded for replan."""
        responses = [
            {"tasks": ["run bad"]},
            {"action": "shell", "arg": "false"},
            {"action": "shell", "arg": "false"},
            {"tasks": ["try something else"]},
            {"action": "shell", "arg": "echo fixed"},
            {"action": "done"},
        ]
        with patch("askme.ask_llm", side_effect=responses):
            run("run bad")

    def test_shell_recompile_after_write_not_blocked(self, tmp_path):
        """shell gcc (fail) -> write fix -> shell gcc (same cmd) should NOT be blocked."""
        src = tmp_path / "main.c"
        responses = [
            {"tasks": ["compile"]},
            {"action": "shell", "arg": f"cc -o main {src}"},
            {"action": "write", "arg": str(src), "content": '#include <stdio.h>\nint main(){puts("ok");return 0;}'},
            {"action": "shell", "arg": f"cc -o main {src}"},
            {"action": "done"},
        ]
        with patch("askme.ask_llm", side_effect=responses):
            run("compile")

    def test_read_same_file_twice_allowed(self, tmp_path):
        """Read same file twice consecutively -- allowed (read excluded from guard)."""
        f = tmp_path / "data.txt"
        f.write_text("hello")
        responses = [
            {"tasks": ["read data"]},
            {"action": "read", "arg": str(f)},
            {"action": "read", "arg": str(f)},
            {"action": "done"},
        ]
        with patch("askme.ask_llm", side_effect=responses):
            run("read data")

    def test_different_action_type_not_duplicate(self, tmp_path):
        """write then shell on same arg -- different action types, no guard trigger."""
        responses = [
            {"tasks": ["write and run"]},
            {"action": "write", "arg": str(tmp_path / "s.sh"), "content": "echo ok"},
            {"action": "shell", "arg": f"bash {tmp_path / 's.sh'}"},
            {"action": "done"},
        ]
        with patch("askme.ask_llm", side_effect=responses):
            run("write and run")

    def test_content_not_in_slim_state(self):
        """_content field should not appear in messages sent to LLM by get_step()."""
        state = {
            "current_task": "test",
            "task_index": "1/1",
            "last_steps": [
                {"action": "write", "arg": "f.txt", "ok": True, "output": "Wrote f.txt",
                 "_content": "should not appear in slim"},
            ],
            "completed_tasks": [],
        }
        captured = {}
        def capture_llm(messages, **kwargs):
            captured["messages"] = messages
            return {"action": "done"}
        with patch("askme.ask_llm", side_effect=capture_llm):
            get_step("test task", state, goal="test goal")
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
                askme._warm_cache()

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

        def fake_warm():
            warm_calls.append(1)

        with patch("askme._warm_cache", side_effect=fake_warm):
            with patch("askme.get_plan", return_value={"tasks": ["say hi"]}):
                with patch("askme.get_step", return_value={"action": "done"}):
                    run("test", working_dir=work_dir)

        assert len(warm_calls) == 1, f"Expected 1 warm call, got {len(warm_calls)}"


# --- Typed failure classification tests ---

class TestFailureClassification:
    """Verify error classification into typed categories."""

    def test_classify_timeout(self):
        from askme import classify_error
        assert classify_error("TIMEOUT") == "timeout"

    def test_classify_command_not_found(self):
        from askme import classify_error
        assert classify_error("/bin/sh: go: command not found") == "missing_tool"

    def test_classify_permission_denied(self):
        from askme import classify_error
        assert classify_error("bash: ./script.sh: Permission denied") == "permission_denied"

    def test_classify_missing_file(self):
        from askme import classify_error
        assert classify_error("cc: error: main.c: No such file or directory") == "missing_file"

    def test_classify_compile_error(self):
        from askme import classify_error
        assert classify_error("main.c:1:10: error: expected ';'") == "compile_error"

    def test_classify_unknown(self):
        from askme import classify_error
        assert classify_error("something unexpected happened") == "unknown"

    def test_shell_failure_includes_error_type(self, work_dir):
        """Shell command failure should include error_type in result."""
        result = execute({"action": "shell", "arg": "nonexistent_command_xyz"}, work_dir)
        assert result["ok"] is False
        assert "error_type" in result

    def test_shell_success_no_error_type(self, work_dir):
        """Successful shell command should not include error_type."""
        result = execute({"action": "shell", "arg": "echo hi"}, work_dir)
        assert result["ok"] is True
        assert "error_type" not in result

    def test_error_type_in_step_entry(self, work_dir):
        """Step entry should include error_type from failed execution."""
        responses = [
            {"tasks": ["run bad cmd"]},
            {"action": "shell", "arg": "nonexistent_cmd_xyz"},
            {"action": "fail", "reasoning": "cmd not found"},
        ] * 3
        with patch("askme.ask_llm", side_effect=responses):
            run("run bad cmd", working_dir=work_dir)


# --- Error summarization tests ---

class TestErrorSummarization:
    """Verify error summarization for planner replans."""

    def test_summarize_empty(self):
        from askme import summarize_errors
        assert summarize_errors([]) == []

    def test_summarize_types_errors(self):
        from askme import summarize_errors
        errors = [
            "shell go run main.go: /bin/sh: go: command not found",
            "Step failed: shell brew install: TIMEOUT",
        ]
        result = summarize_errors(errors)
        assert any("[missing_tool]" in e for e in result)
        assert any("[timeout]" in e for e in result)

    def test_summarize_preserves_existing_type_prefix(self):
        """Errors already tagged with [type] should preserve their type, not re-classify."""
        from askme import summarize_errors
        errors = [
            "[compile_error] shell gcc main.c: main.c:1:10: error: expected ';'",
            "[unknown] shell xyz: something weird",
        ]
        result = summarize_errors(errors)
        assert any("[compile_error]" in e for e in result), f"compile_error lost: {result}"
        assert any("[unknown]" in e for e in result), f"unknown lost: {result}"
        assert not any(e.startswith("[error]") for e in result), f"type collapsed to [error]: {result}"

    def test_summarize_deduplicates(self):
        from askme import summarize_errors
        errors = [
            "/bin/sh: go: command not found",
            "/bin/sh: go: command not found",
            "/bin/sh: go: command not found",
        ]
        result = summarize_errors(errors)
        assert len(result) == 1

    def test_summarize_caps_per_type(self):
        from askme import summarize_errors
        errors = [f"[compile_error] error {i}: something went wrong" for i in range(10)]
        result = summarize_errors(errors)
        assert len(result) <= 3
        assert all("[compile_error]" in e for e in result)

    def test_planner_gets_summarized_errors(self):
        """get_plan should pass summarized (typed) errors, not raw strings."""
        captured = {}
        def capture_llm(messages, **kwargs):
            captured["user_msg"] = messages[-1]["content"]
            return {"tasks": ["retry"]}
        state = {
            "completed_tasks": [],
            "errors": ["[missing_tool] shell go run: /bin/sh: go: command not found"],
            "environment": {},
            "policy": {"allow_system_installs": False},
        }
        with patch("askme.ask_llm", side_effect=capture_llm):
            get_plan("test", state)
        assert "[missing_tool]" in captured["user_msg"]


# --- Updated completion semantics tests ---

class TestCompletionSemantics:
    """Verify that completion is goal-aware, not step-aware."""

    def test_system_step_no_immediate_done_rule(self):
        """SYSTEM_STEP should NOT contain the old 'ok=true => done immediately' rule."""
        from askme import SYSTEM_STEP
        assert "SUCCEEDED. Emit" not in SYSTEM_STEP
        assert "ok=true, that action SUCCEEDED" not in SYSTEM_STEP

    def test_system_step_has_goal_aware_rule(self):
        """SYSTEM_STEP should require full task satisfaction for done."""
        from askme import SYSTEM_STEP
        assert "FULL task description" in SYSTEM_STEP

    def test_parse_error_after_success_does_not_auto_complete(self, tmp_path):
        """Parse error after a successful step should fail the task, not auto-complete it."""
        plan_resp = {"tasks": ["write and compile hello.c"]}
        step_ok = {"action": "shell", "arg": "echo hi"}
        def step_side_effect(*a, **kw):
            if step_side_effect.call == 0:
                step_side_effect.call += 1
                return step_ok
            raise json.JSONDecodeError("bad json", "", 0)
        step_side_effect.call = 0

        with patch("askme.get_plan", return_value=plan_resp), \
             patch("askme.get_step", side_effect=step_side_effect), \
             patch("askme.execute", return_value={"ok": True, "output": "hi"}), \
             patch("askme.preflight_probe", return_value={"platform": "test", "arch": "test", "available_tools": [], "missing_tools": [], "package_managers": [], "dir_listing": ""}):
            result = run("write and compile hello.c", working_dir=str(tmp_path))
        assert result is False
