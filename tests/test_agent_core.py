"""Core unit tests: execute(), ask_llm(), thinking retry, null-arg normalization, transport hardening."""
import json
import requests as req_lib
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from askme import execute, ask_llm, run, _run_loop, LLMTransportError, LLM_TIMEOUT
from _test_support import mock_response, mock_response_raw


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


class TestExecuteEdit:
    def test_edit_single_match(self, work_dir):
        Path(work_dir, "main.c").write_text('#include "msg.h"\nint main(){return 0;}')
        result = execute({"action": "edit", "arg": "main.c",
                          "find": '#include "msg.h"',
                          "replace": '#include <stdio.h>\n#include "msg.h"'}, work_dir)
        assert result["ok"] is True
        assert "Edited" in result["output"]
        text = (Path(work_dir) / "main.c").read_text()
        assert '#include <stdio.h>\n#include "msg.h"' in text
        assert "int main()" in text

    def test_edit_no_match(self, work_dir):
        Path(work_dir, "f.txt").write_text("hello world")
        result = execute({"action": "edit", "arg": "f.txt",
                          "find": "goodbye", "replace": "hi"}, work_dir)
        assert result["ok"] is False
        assert "No match" in result["output"]

    def test_edit_multiple_matches(self, work_dir):
        Path(work_dir, "f.txt").write_text("aaa\naaa\naaa")
        result = execute({"action": "edit", "arg": "f.txt",
                          "find": "aaa", "replace": "bbb"}, work_dir)
        assert result["ok"] is False
        assert "3 times" in result["output"]

    def test_edit_missing_file(self, work_dir):
        result = execute({"action": "edit", "arg": "nope.txt",
                          "find": "x", "replace": "y"}, work_dir)
        assert result["ok"] is False
        assert result.get("error_type") == "missing_file"

    def test_edit_relative_path(self, work_dir):
        (Path(work_dir) / "sub").mkdir()
        (Path(work_dir) / "sub" / "f.txt").write_text("old text")
        result = execute({"action": "edit", "arg": "sub/f.txt",
                          "find": "old text", "replace": "new text"}, work_dir)
        assert result["ok"] is True
        assert (Path(work_dir) / "sub" / "f.txt").read_text() == "new text"

    def test_edit_empty_find(self, work_dir):
        Path(work_dir, "f.txt").write_text("content")
        result = execute({"action": "edit", "arg": "f.txt",
                          "find": "", "replace": "x"}, work_dir)
        assert result["ok"] is False
        assert "non-empty" in result["output"]

    def test_edit_delete_text(self, work_dir):
        """Replace with empty string effectively deletes the matched text."""
        Path(work_dir, "f.txt").write_text("line1\nDELETE_ME\nline3")
        result = execute({"action": "edit", "arg": "f.txt",
                          "find": "DELETE_ME\n", "replace": ""}, work_dir)
        assert result["ok"] is True
        assert (Path(work_dir) / "f.txt").read_text() == "line1\nline3"

    def test_edit_absolute_path(self, work_dir):
        p = Path(work_dir) / "abs.txt"
        p.write_text("before")
        result = execute({"action": "edit", "arg": str(p),
                          "find": "before", "replace": "after"}, work_dir)
        assert result["ok"] is True
        assert p.read_text() == "after"


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
        mock_post.side_effect = [
            mock_response_raw("not json"),
            mock_response({"action": "done"}),
        ]
        result = ask_llm([{"role": "user", "content": "test"}])
        assert result == {"action": "done"}
        second_call_body = mock_post.call_args_list[1][1]["json"]
        assert "reasoning" in second_call_body
        assert second_call_body["reasoning"]["enabled"] is True
        assert second_call_body["reasoning"]["effort"] == "medium"
        first_call_body = mock_post.call_args_list[0][1]["json"]
        assert "reasoning" not in first_call_body

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "local")
    def test_thinking_retry_local(self, mock_post):
        """On retry, ask_llm should prepend <|think|> and bump max_tokens for local."""
        mock_post.side_effect = [
            mock_response_raw("not json"),
            mock_response({"action": "done"}),
        ]
        result = ask_llm([
            {"role": "system", "content": "You are a helper."},
            {"role": "user", "content": "test"}
        ], max_tokens=256)
        assert result == {"action": "done"}
        second_call_body = mock_post.call_args_list[1][1]["json"]
        sys_content = second_call_body["messages"][0]["content"]
        assert sys_content.startswith("<|think|>\n"), f"Expected <|think|> prefix, got: {sys_content[:50]}"
        assert second_call_body["max_tokens"] >= 512
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
        with pytest.raises(json.JSONDecodeError):
            ask_llm([{"role": "user", "content": "test"}])

    @patch("askme.requests.post")
    def test_api_error_retries(self, mock_post):
        """API error responses should be retried, not crash on missing 'choices'."""
        resp_err = MagicMock()
        resp_err.status_code = 200
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
        resp_null = MagicMock()
        resp_null.status_code = 200
        resp_null.json.return_value = {
            "choices": [{"message": {"content": None, "reasoning": "thinking..."}}]
        }
        mock_post.side_effect = [resp_null, mock_response({"action": "done"})]
        result = ask_llm([{"role": "user", "content": "test"}])
        assert result == {"action": "done"}

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_null_content_recovers_json_from_reasoning(self, mock_post):
        """When content is null but reasoning contains valid JSON, recover it directly."""
        resp_null = MagicMock()
        resp_null.status_code = 200
        resp_null.json.return_value = {
            "choices": [{"message": {"content": None, "reasoning": '{"action": "shell", "arg": "echo hi"}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }
        mock_post.return_value = resp_null
        result = ask_llm([{"role": "user", "content": "test"}])
        assert result == {"action": "shell", "arg": "echo hi"}
        assert mock_post.call_count == 1

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_non_dict_json_rejected(self, mock_post):
        """json.loads returning non-dict (e.g. null, array) should retry, not pass through."""
        mock_post.side_effect = [
            mock_response_raw("null"),
            mock_response({"action": "done"}),
        ]
        result = ask_llm([{"role": "user", "content": "test"}])
        assert result == {"action": "done"}
        assert mock_post.call_count == 2

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_think_escalates_to_high(self, mock_post):
        """With think=True, retry should escalate to high effort."""
        mock_post.side_effect = [
            mock_response_raw("bad"),
            mock_response({"action": "done"}),
        ]
        result = ask_llm([{"role": "user", "content": "test"}], think=True)
        assert result == {"action": "done"}
        second_body = mock_post.call_args_list[1][1]["json"]
        assert second_body["reasoning"]["effort"] == "high"


# --- Null arg normalization tests ---

class TestNullArgNormalization:
    """Verify that null/None values in action fields don't crash the agent."""

    def test_done_with_null_arg(self, tmp_path):
        """Model returns {"action":"done","arg":null} — should not crash."""
        responses = [
            {"tasks": ["do something"]},
            {"action": "shell", "arg": "echo hi"},
            {"action": "done", "arg": None, "reasoning": None},
        ]
        with patch("askme.ask_llm", side_effect=responses):
            assert run("do something") is True

    def test_write_with_null_reasoning(self, tmp_path):
        """Write action with null reasoning field — should not crash."""
        responses = [
            {"tasks": ["write file"]},
            {"action": "write", "arg": str(tmp_path / "f.txt"),
             "content": "hello", "reasoning": None},
            {"action": "done"},
        ]
        with patch("askme.ask_llm", side_effect=responses):
            assert run("write file") is True


# --- LLM transport hardening tests ---

class TestLLMTransport:
    """Verify transport-level error handling in ask_llm()."""

    @patch("askme.requests.post")
    def test_timeout_propagated(self, mock_post):
        """requests.post() must be called with timeout=LLM_TIMEOUT."""
        mock_post.return_value = mock_response({"action": "done"})
        ask_llm([{"role": "user", "content": "test"}])
        _, kwargs = mock_post.call_args
        assert kwargs.get("timeout") == LLM_TIMEOUT

    @patch("askme.requests.post")
    def test_timeout_retry(self, mock_post):
        """Timeout errors should retry, then raise LLMTransportError."""
        mock_post.side_effect = req_lib.exceptions.Timeout("timed out")
        with patch("askme.time.sleep"):
            with pytest.raises(LLMTransportError, match="Transport failed"):
                ask_llm([{"role": "user", "content": "test"}])
        # Should have attempted MAX_LLM_RETRIES + 1 times
        assert mock_post.call_count == 3

    @patch("askme.requests.post")
    def test_connection_refused_retry(self, mock_post):
        """ConnectionError should retry, then raise LLMTransportError."""
        mock_post.side_effect = req_lib.exceptions.ConnectionError("refused")
        with patch("askme.time.sleep"):
            with pytest.raises(LLMTransportError, match="Transport failed"):
                ask_llm([{"role": "user", "content": "test"}])
        assert mock_post.call_count == 3

    @patch("askme.requests.post")
    def test_non_json_body_retry(self, mock_post):
        """200 response with non-JSON body should retry, then raise."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("not json")
        resp.text = "<html>Bad Gateway</html>"
        mock_post.return_value = resp
        with patch("askme.time.sleep"):
            with pytest.raises(LLMTransportError, match="Non-JSON"):
                ask_llm([{"role": "user", "content": "test"}])
        assert mock_post.call_count == 3

    @patch("askme.time.sleep")
    @patch("askme.requests.post")
    def test_502_retry_with_backoff(self, mock_post, mock_sleep):
        """502 response should retry with backoff delays."""
        resp_502 = MagicMock()
        resp_502.status_code = 502
        resp_502.text = "Bad Gateway"
        mock_post.side_effect = [resp_502, resp_502, resp_502]
        with pytest.raises(LLMTransportError, match="HTTP 502"):
            ask_llm([{"role": "user", "content": "test"}])
        # Verify backoff: sleep(1) then sleep(3)
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list[0][0][0] == 1
        assert mock_sleep.call_args_list[1][0][0] == 3

    @patch("askme.requests.post")
    def test_401_fail_fast(self, mock_post):
        """401 should raise immediately without retry."""
        resp = MagicMock()
        resp.status_code = 401
        resp.text = "Unauthorized"
        mock_post.return_value = resp
        with pytest.raises(LLMTransportError, match="HTTP 401"):
            ask_llm([{"role": "user", "content": "test"}])
        assert mock_post.call_count == 1  # no retry

    def test_transport_error_in_planner_consumes_attempt(self, tmp_path):
        """LLMTransportError in get_plan() should consume a plan attempt, not crash."""
        plan_calls = {"n": 0}
        original_get_plan = None

        def mock_get_plan(user_prompt, state):
            plan_calls["n"] += 1
            if plan_calls["n"] == 1:
                raise LLMTransportError("connection refused")
            return {"tasks": ["say hello"]}

        with patch("askme.get_plan", side_effect=mock_get_plan), \
             patch("askme.get_step", return_value={"action": "done"}):
            result = _run_loop("test", str(tmp_path), max_replans=2)
        assert result["status"] == "complete"
        assert plan_calls["n"] == 2  # first failed, second succeeded
        # Log should show plan_error event for first attempt
        events = [e["event"] for e in result["log"]]
        assert "plan_error" in events

    def test_transport_error_in_executor_triggers_replan(self, tmp_path):
        """LLMTransportError in get_step() should trigger replan, not crash."""
        step_calls = {"n": 0}

        def mock_get_step(task, state, goal="", step_num=0, max_steps=10, think=False):
            step_calls["n"] += 1
            if step_calls["n"] == 1:
                raise LLMTransportError("timeout")
            return {"action": "done"}

        with patch("askme.get_plan", return_value={"tasks": ["do something"]}), \
             patch("askme.get_step", side_effect=mock_get_step):
            result = _run_loop("test", str(tmp_path), max_replans=2)
        assert result["status"] == "complete"

    @patch("askme.requests.post")
    def test_json_error_key_still_retried(self, mock_post):
        """Existing behavior: JSON body with 'error' key should still retry."""
        resp_err = MagicMock()
        resp_err.status_code = 200
        resp_err.json.return_value = {"error": {"message": "rate limited"}}
        mock_post.side_effect = [resp_err, mock_response({"action": "done"})]
        result = ask_llm([{"role": "user", "content": "test"}])
        assert result == {"action": "done"}
