"""Recovery tests: duplicate guard, cache workaround, failure classification,
error summarization, completion semantics, final validation."""
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import askme
from askme import (execute, ask_llm, get_plan, get_step, run, _run_loop,
                   _should_validate, _validate_completion, LLMTransportError)
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

    def test_write_first_skip_feedback_no_thinking(self, tmp_path):
        """First duplicate write skip injects feedback but defers thinking escalation.
        Thinking only activates on 2+ consecutive skips to save ~10s on harmless duplicates."""
        f = str(tmp_path / "data.txt")
        responses = [
            {"tasks": ["write data.txt"]},
            {"action": "write", "arg": f, "content": "hello"},
            {"action": "write", "arg": f, "content": "hello"},  # skip 1 -- feedback only, no thinking
            {"action": "done"},                                   # model sees feedback without thinking
        ]
        with patch("askme.ask_llm", side_effect=responses) as mock_llm:
            ok = run("write data.txt", working_dir=str(tmp_path))
        assert ok is True
        # The done call should see "Already done" feedback but think=False (deferred)
        last_call = mock_llm.call_args_list[-1]
        user_msg = last_call[0][0][1]["content"]
        assert "Already done" in user_msg
        assert last_call[1].get("think") is False

    def test_write_triple_duplicate_still_detected(self, tmp_path):
        """Three consecutive duplicate writes all detected — synthetic entries preserve _content for matching."""
        f = str(tmp_path / "data.txt")
        responses = [
            {"tasks": ["write data.txt"]},
            {"action": "write", "arg": f, "content": "hello"},
            {"action": "write", "arg": f, "content": "hello"},  # skip 1 -- feedback only
            {"action": "write", "arg": f, "content": "hello"},  # skip 2 -- thinking enabled
            {"action": "done"},
        ]
        with patch("askme.ask_llm", side_effect=responses) as mock_llm:
            ok = run("write data.txt", working_dir=str(tmp_path))
        assert ok is True
        # The done call should have think=True
        last_call = mock_llm.call_args_list[-1]
        assert last_call[1].get("think") is True

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

    def test_edit_same_find_replace_skips(self, tmp_path):
        """Same file + same find + same replace + ok -> skip duplicate edit."""
        f = tmp_path / "main.c"
        f.write_text('#include "msg.h"\nint main(){return 0;}')
        responses = [
            {"tasks": ["fix include"]},
            {"action": "edit", "arg": str(f), "find": '#include "msg.h"',
             "replace": '#include <stdio.h>\n#include "msg.h"'},
            {"action": "edit", "arg": str(f), "find": '#include "msg.h"',
             "replace": '#include <stdio.h>\n#include "msg.h"'},  # dup - skip
            {"action": "done"},
        ]
        with patch("askme.ask_llm", side_effect=responses):
            ok = run("fix include", working_dir=str(tmp_path))
        assert ok is True

    def test_edit_triple_duplicate_still_detected(self, tmp_path):
        """Three consecutive duplicate edits all detected — synthetic entries preserve _find/_replace."""
        f = tmp_path / "main.c"
        f.write_text('#include "msg.h"\nint main(){return 0;}')
        responses = [
            {"tasks": ["fix include"]},
            {"action": "edit", "arg": str(f), "find": '#include "msg.h"',
             "replace": '#include <stdio.h>\n#include "msg.h"'},
            {"action": "edit", "arg": str(f), "find": '#include "msg.h"',
             "replace": '#include <stdio.h>\n#include "msg.h"'},  # skip 1
            {"action": "edit", "arg": str(f), "find": '#include "msg.h"',
             "replace": '#include <stdio.h>\n#include "msg.h"'},  # skip 2 -- thinking enabled
            {"action": "done"},
        ]
        with patch("askme.ask_llm", side_effect=responses) as mock_llm:
            ok = run("fix include", working_dir=str(tmp_path))
        assert ok is True
        # The done call (after skip 2) should have think=True
        last_call = mock_llm.call_args_list[-1]
        assert last_call[1].get("think") is True

    def test_edit_different_find_allowed(self, tmp_path):
        """Same file + different find -> allowed (different edit, not a duplicate)."""
        f = tmp_path / "f.txt"
        f.write_text("aaa\nbbb")
        responses = [
            {"tasks": ["fix two lines"]},
            {"action": "edit", "arg": str(f), "find": "aaa", "replace": "AAA"},
            {"action": "edit", "arg": str(f), "find": "bbb", "replace": "BBB"},
            {"action": "done"},
        ]
        with patch("askme.ask_llm", side_effect=responses):
            ok = run("fix two lines", working_dir=str(tmp_path))
        assert ok is True
        assert f.read_text() == "AAA\nBBB"

    def test_edit_recompile_after_edit_not_blocked(self, tmp_path):
        """shell gcc (fail) -> edit fix -> shell gcc (same cmd) should NOT be blocked."""
        src = tmp_path / "main.c"
        src.write_text('int main(){puts("ok");return 0;}')
        responses = [
            {"tasks": ["compile"]},
            {"action": "shell", "arg": f"cc -o main {src}"},
            {"action": "edit", "arg": str(src),
             "find": 'int main()',
             "replace": '#include <stdio.h>\nint main()'},
            {"action": "shell", "arg": f"cc -o main {src}"},
            {"action": "done"},
        ]
        with patch("askme.ask_llm", side_effect=responses):
            run("compile")

    def test_edit_same_failed_find_triggers_auto_fail(self, tmp_path):
        """Consecutive identical failed edits should bail out, not loop MAX_STEPS."""
        src = tmp_path / "main.c"
        src.write_text("int main() { return 0; }")
        responses = [
            {"tasks": ["fix code"]},
            {"action": "edit", "arg": str(src),
             "find": "nonexistent text", "replace": "new text"},
            {"action": "edit", "arg": str(src),
             "find": "nonexistent text", "replace": "new text"},
            {"action": "done"},  # never reached — auto-fail breaks the task
        ] * 3
        with patch("askme.ask_llm", side_effect=responses):
            result = _run_loop("fix code", str(tmp_path))
        # Only 1 edit step should actually execute (the 2nd triggers auto-fail before execute)
        edit_steps = [h for h in result["log"]
                      if h.get("event") == "step"
                      and h.get("action", {}).get("action") == "edit"]
        assert len(edit_steps) == 1, f"Expected 1 edit step (auto-fail on 2nd), got {len(edit_steps)}"

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

    def test_edit_internals_not_in_slim_state(self):
        """_find and _replace fields should not appear in messages sent to LLM."""
        state = {
            "current_task": "test",
            "task_index": "1/1",
            "last_steps": [
                {"action": "edit", "arg": "f.txt", "ok": True, "output": "Edited f.txt",
                 "_find": "old", "_replace": "new"},
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
        assert "_find" not in user_msg, f"_find leaked into LLM message: {user_msg}"
        assert "_replace" not in user_msg, f"_replace leaked into LLM message: {user_msg}"


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
                resp.status_code = 200
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

    def test_edit_no_match_returns_edit_failed(self, work_dir):
        """Edit with no matching find string should return error_type=edit_failed."""
        p = Path(work_dir) / "test.txt"
        p.write_text("hello world")
        result = execute({"action": "edit", "arg": "test.txt",
                          "find": "nonexistent text", "replace": "new"}, work_dir)
        assert result["ok"] is False
        assert result["error_type"] == "edit_failed"

    def test_edit_ambiguous_returns_edit_failed(self, work_dir):
        """Edit with ambiguous match should return error_type=edit_failed."""
        p = Path(work_dir) / "test.txt"
        p.write_text("aaa\naaa\naaa")
        result = execute({"action": "edit", "arg": "test.txt",
                          "find": "aaa", "replace": "bbb"}, work_dir)
        assert result["ok"] is False
        assert result["error_type"] == "edit_failed"

    def test_edit_empty_find_returns_edit_failed(self, work_dir):
        """Edit with empty find string should return error_type=edit_failed."""
        p = Path(work_dir) / "test.txt"
        p.write_text("hello")
        result = execute({"action": "edit", "arg": "test.txt",
                          "find": "", "replace": "new"}, work_dir)
        assert result["ok"] is False
        assert result["error_type"] == "edit_failed"


# --- E05/E06: Error-class retry policy and recovery hints ---

class TestErrorClassRetryPolicy:
    """E05: Structural failures skip thinking; semantic failures escalate."""

    def test_edit_failure_skips_thinking(self, work_dir):
        """After edit_failed, next step should NOT get thinking escalation."""
        p = Path(work_dir) / "test.c"
        p.write_text("#include <stdio.h>\nint main() { return 0; }")
        responses = [
            {"tasks": ["fix the include"]},
            # Step 1: edit with wrong find string → edit_failed
            {"action": "edit", "arg": "test.c",
             "find": "wrong text", "replace": "right text"},
            # Step 2: read (should NOT have thinking)
            {"action": "read", "arg": "test.c"},
            # Step 3: correct edit
            {"action": "edit", "arg": "test.c",
             "find": "#include <stdio.h>", "replace": "#include <stdlib.h>"},
            {"action": "done"},
        ]
        think_values = []
        original_get_step = None
        import askme
        original_get_step = askme.get_step

        def spy_get_step(*args, **kwargs):
            think_values.append(kwargs.get("think", False))
            return original_get_step(*args, **kwargs)

        with patch("askme.ask_llm", side_effect=responses):
            with patch("askme.get_step", side_effect=spy_get_step):
                run("fix the include", working_dir=work_dir)
        # Step 1 starts with think=False (no prior failure)
        # Step 2 after edit_failed should have think=False (E05: structural failure)
        assert len(think_values) >= 2
        assert think_values[1] is False, f"Expected no thinking after edit_failed, got {think_values}"

    def test_compile_error_escalates_thinking(self, work_dir):
        """After compile_error, next step SHOULD get thinking escalation."""
        # Write a file with a syntax error so gcc produces a real compile error
        p = Path(work_dir) / "test.c"
        p.write_text("int main() { return }")  # missing value
        responses = [
            {"tasks": ["compile test.c"]},
            {"action": "shell", "arg": "gcc -o test test.c"},
            # After compile error, model reads and fixes
            {"action": "read", "arg": "test.c"},
            {"action": "edit", "arg": "test.c",
             "find": "return }", "replace": "return 0; }"},
            {"action": "done"},
        ]
        think_values = []
        original_get_step = askme.get_step

        def spy_get_step(*args, **kwargs):
            think_values.append(kwargs.get("think", False))
            return original_get_step(*args, **kwargs)

        with patch("askme.ask_llm", side_effect=responses):
            with patch("askme.get_step", side_effect=spy_get_step):
                run("compile test.c", working_dir=work_dir)
        # After a shell failure with compile_error, thinking should escalate
        assert len(think_values) >= 2, f"Expected at least 2 steps, got {think_values}"
        assert think_values[1] is True, f"Expected thinking after compile_error, got {think_values}"


class TestRecoveryHints:
    """E06: Recovery hints injected into step output after typed failures."""

    def test_edit_failed_hint_in_step_output(self, work_dir):
        """After edit_failed, the step output seen by the next step should contain the hint."""
        p = Path(work_dir) / "test.c"
        p.write_text("#include <stdio.h>\nint main() { return 0; }")
        responses = [
            {"tasks": ["fix the code"]},
            {"action": "edit", "arg": "test.c",
             "find": "nonexistent", "replace": "replacement"},
            {"action": "read", "arg": "test.c"},
            {"action": "done"},
        ]
        captured_states = []
        original_get_step = askme.get_step

        def spy_get_step(task, state, **kwargs):
            captured_states.append(
                [s.get("output", "") for s in state.get("last_steps", [])])
            return original_get_step(task, state, **kwargs)

        with patch("askme.ask_llm", side_effect=responses):
            with patch("askme.get_step", side_effect=spy_get_step):
                run("fix the code", working_dir=work_dir)
        has_hint = any(
            any("Read the file first" in out for out in outputs)
            for outputs in captured_states
        )
        assert has_hint, f"Expected edit_failed hint in step outputs: {captured_states}"

    def test_missing_file_hint_in_step_output(self, work_dir):
        """After missing_file edit, the step output should contain a recovery hint."""
        responses = [
            {"tasks": ["edit the file"]},
            {"action": "edit", "arg": "nonexistent.c",
             "find": "old", "replace": "new"},
            {"action": "fail", "reasoning": "file not found"},
        ] * 3
        import askme
        captured_states = []
        original_get_step = askme.get_step

        def spy_get_step(task, state, **kwargs):
            captured_states.append(
                [s.get("output", "") for s in state.get("last_steps", [])])
            return original_get_step(task, state, **kwargs)

        with patch("askme.ask_llm", side_effect=responses):
            with patch("askme.get_step", side_effect=spy_get_step):
                run("edit the file", working_dir=work_dir)
        # Check that at least one captured state has a hint about listing directory
        has_hint = any(
            any("Check the filename" in out for out in outputs)
            for outputs in captured_states
        )
        assert has_hint, f"Expected missing_file hint in step outputs: {captured_states}"


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


# --- Final validation tests ---

class TestFinalValidation:
    """Verify end-to-end goal validation after all tasks complete."""

    @pytest.fixture(autouse=True)
    def enable_validation(self):
        """Re-enable validation for this test class (conftest disables it globally)."""
        import askme
        askme.FINAL_VALIDATE = "auto"
        yield
        # conftest autouse fixture will restore to "0" after

    def _simple_run(self, prompt, tmp_path, validate_response=None, extra_responses=None,
                    env_vars=None):
        """Helper: run a single-task agent with optional validation mock.
        Returns (result_bool, ask_llm_mock)."""
        responses = [
            {"tasks": ["do the thing"]},
            {"action": "shell", "arg": "echo done"},
            {"action": "done"},
        ]
        if extra_responses:
            responses.extend(extra_responses)

        call_count = [0]
        original_responses = list(responses)

        def mock_ask_llm(messages, **kwargs):
            # If this is a validation call (SYSTEM_VALIDATE in system prompt)
            sys_content = messages[0]["content"] if messages else ""
            if "completion validator" in sys_content:
                if validate_response is None:
                    return {"valid": True}
                if isinstance(validate_response, Exception):
                    raise validate_response
                return validate_response
            # Regular call
            if call_count[0] < len(original_responses):
                resp = original_responses[call_count[0]]
                call_count[0] += 1
                return resp
            return {"action": "done"}

        env_patches = {}
        if env_vars:
            env_patches = env_vars

        import askme
        old_validate = askme.FINAL_VALIDATE
        if "AGENT_FINAL_VALIDATE" in env_patches:
            askme.FINAL_VALIDATE = env_patches["AGENT_FINAL_VALIDATE"]

        try:
            with patch("askme.ask_llm", side_effect=mock_ask_llm) as mock_llm:
                ok = run(prompt, working_dir=str(tmp_path))
            return ok, mock_llm
        finally:
            askme.FINAL_VALIDATE = old_validate

    def test_validate_skipped_trivial(self, tmp_path):
        """Single-task no-failure run with no keyword match → no validation call."""
        responses = [
            {"tasks": ["write greeting"]},
            {"action": "write", "arg": str(tmp_path / "hi.txt"), "content": "hello"},
            {"action": "done"},
        ]
        validate_called = [False]

        def mock_ask_llm(messages, **kwargs):
            if messages and "completion validator" in messages[0].get("content", ""):
                validate_called[0] = True
                return {"valid": True}
            return responses.pop(0)

        with patch("askme.ask_llm", side_effect=mock_ask_llm):
            ok = run("write greeting", working_dir=str(tmp_path))
        assert ok is True
        assert validate_called[0] is False

    def test_validate_runs_on_replan(self, tmp_path):
        """replan > 0 → validation runs."""
        responses = [
            # Plan 1: task fails
            {"tasks": ["try bad"]},
            {"action": "fail", "reasoning": "oops"},
            # Plan 2: task succeeds
            {"tasks": ["try good"]},
            {"action": "shell", "arg": "echo ok"},
            {"action": "done"},
        ]
        validate_called = [False]

        def mock_ask_llm(messages, **kwargs):
            if messages and "completion validator" in messages[0].get("content", ""):
                validate_called[0] = True
                return {"valid": True}
            return responses.pop(0)

        with patch("askme.ask_llm", side_effect=mock_ask_llm):
            result = _run_loop("try stuff", str(tmp_path), max_replans=3)
        assert result["status"] == "complete"
        assert validate_called[0] is True

    def test_validate_passes(self, tmp_path):
        """{"valid":true} → success returned."""
        ok, _ = self._simple_run("compile and run program", tmp_path,
                                 validate_response={"valid": True})
        assert ok is True

    def test_validate_fails_triggers_replan(self, tmp_path):
        """{"valid":false,...} → replan with [validation_failed] error."""
        responses = [
            # Plan 1
            {"tasks": ["build it"]},
            {"action": "shell", "arg": "echo building"},
            {"action": "done"},
            # Plan 2 (after validation failure)
            {"tasks": ["build it properly"]},
            {"action": "shell", "arg": "echo built"},
            {"action": "done"},
        ]
        call_count = [0]
        validate_count = [0]

        def mock_ask_llm(messages, **kwargs):
            if messages and "completion validator" in messages[0].get("content", ""):
                validate_count[0] += 1
                if validate_count[0] == 1:
                    return {"valid": False, "reason": "binary not created",
                            "missing": ["program"]}
                return {"valid": True}
            resp = responses[call_count[0]]
            call_count[0] += 1
            return resp

        result = None
        with patch("askme.ask_llm", side_effect=mock_ask_llm):
            result = _run_loop("build it", str(tmp_path), max_replans=3)
        assert result["status"] == "complete"
        # Validation should have triggered a replan — check errors were set
        # The second plan should have run (call_count > 3)
        assert call_count[0] > 3

    def test_validate_transport_error_returns_success(self, tmp_path):
        """Transport error → fail-open, return success."""
        ok, _ = self._simple_run("compile and run program", tmp_path,
                                 validate_response=LLMTransportError("connection refused"))
        assert ok is True

    def test_validate_parse_error_returns_success(self, tmp_path):
        """Garbage output → fail-open, return success."""
        ok, _ = self._simple_run("compile and run program", tmp_path,
                                 validate_response=json.JSONDecodeError("bad", "", 0))
        assert ok is True

    def test_validate_no_infinite_loop(self, tmp_path):
        """validated_once flag prevents second validation after recovery replan."""
        responses = [
            # Plan 1
            {"tasks": ["build it"]},
            {"action": "shell", "arg": "echo building"},
            {"action": "done"},
            # Plan 2 (after validation failure)
            {"tasks": ["build properly"]},
            {"action": "shell", "arg": "echo ok"},
            {"action": "done"},
        ]
        call_count = [0]
        validate_count = [0]

        def mock_ask_llm(messages, **kwargs):
            if messages and "completion validator" in messages[0].get("content", ""):
                validate_count[0] += 1
                return {"valid": False, "reason": "still wrong", "missing": ["x"]}
            resp = responses[call_count[0]]
            call_count[0] += 1
            return resp

        with patch("askme.ask_llm", side_effect=mock_ask_llm):
            result = _run_loop("build it", str(tmp_path), max_replans=3)
        # Validation should only run once (validated_once flag)
        assert validate_count[0] == 1
        assert result["status"] == "complete"

    def test_validate_always_mode(self, tmp_path):
        """AGENT_FINAL_VALIDATE=always forces validation on trivial run."""
        import askme
        old = askme.FINAL_VALIDATE
        askme.FINAL_VALIDATE = "always"
        try:
            responses = [
                {"tasks": ["write hi"]},
                {"action": "write", "arg": str(tmp_path / "hi.txt"), "content": "hi"},
                {"action": "done"},
            ]
            validate_called = [False]

            def mock_ask_llm(messages, **kwargs):
                if messages and "completion validator" in messages[0].get("content", ""):
                    validate_called[0] = True
                    return {"valid": True}
                return responses.pop(0)

            with patch("askme.ask_llm", side_effect=mock_ask_llm):
                ok = run("write hi", working_dir=str(tmp_path))
            assert ok is True
            assert validate_called[0] is True
        finally:
            askme.FINAL_VALIDATE = old

    def test_validate_disabled(self, tmp_path):
        """AGENT_FINAL_VALIDATE=0 skips validation."""
        import askme
        old = askme.FINAL_VALIDATE
        askme.FINAL_VALIDATE = "0"
        try:
            responses = [
                # replan scenario that would normally trigger validation
                {"tasks": ["try bad"]},
                {"action": "fail", "reasoning": "oops"},
                {"tasks": ["try good"]},
                {"action": "shell", "arg": "echo ok"},
                {"action": "done"},
            ]
            validate_called = [False]

            def mock_ask_llm(messages, **kwargs):
                if messages and "completion validator" in messages[0].get("content", ""):
                    validate_called[0] = True
                    return {"valid": True}
                return responses.pop(0)

            with patch("askme.ask_llm", side_effect=mock_ask_llm):
                result = _run_loop("compile program", str(tmp_path), max_replans=3)
            assert result["status"] == "complete"
            assert validate_called[0] is False
        finally:
            askme.FINAL_VALIDATE = old

    def test_validate_uses_high_thinking(self, tmp_path):
        """Confirms think=True, think_level="high", max_retries=0."""
        captured_kwargs = {}

        responses = [
            {"tasks": ["build program"]},
            {"action": "shell", "arg": "echo building"},
            {"action": "done"},
        ]
        call_count = [0]

        def mock_ask_llm(messages, **kwargs):
            if messages and "completion validator" in messages[0].get("content", ""):
                captured_kwargs.update(kwargs)
                return {"valid": True}
            resp = responses[call_count[0]]
            call_count[0] += 1
            return resp

        # Use "always" to force validation on this simple run
        import askme
        old = askme.FINAL_VALIDATE
        askme.FINAL_VALIDATE = "always"
        try:
            with patch("askme.ask_llm", side_effect=mock_ask_llm):
                run("build program", working_dir=str(tmp_path))
        finally:
            askme.FINAL_VALIDATE = old

        assert captured_kwargs.get("think") is True
        assert captured_kwargs.get("think_level") == "high"
        assert captured_kwargs.get("max_retries") == 0

    def test_validate_prompt_includes_files_and_steps(self, tmp_path):
        """Evidence contains file listing + step summaries."""
        (tmp_path / "output.txt").write_text("result")
        captured_messages = {}

        responses = [
            {"tasks": ["create output"]},
            {"action": "write", "arg": str(tmp_path / "output.txt"), "content": "result"},
            {"action": "done"},
        ]
        call_count = [0]

        def mock_ask_llm(messages, **kwargs):
            if messages and "completion validator" in messages[0].get("content", ""):
                captured_messages["user"] = messages[1]["content"]
                return {"valid": True}
            resp = responses[call_count[0]]
            call_count[0] += 1
            return resp

        import askme
        old = askme.FINAL_VALIDATE
        askme.FINAL_VALIDATE = "always"
        try:
            with patch("askme.ask_llm", side_effect=mock_ask_llm):
                run("create output", working_dir=str(tmp_path))
        finally:
            askme.FINAL_VALIDATE = old

        user_msg = captured_messages["user"]
        # Should contain goal
        assert "create output" in user_msg
        # Should contain file listing
        assert "output.txt" in user_msg
        # Should contain task evidence
        assert "Task 1" in user_msg
        # Should contain step evidence (write action)
        assert "write" in user_msg

