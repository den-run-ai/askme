"""Core unit tests: execute(), ask_llm(), thinking retry, null-arg normalization, transport hardening."""
import json
import requests as req_lib
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from askme import (execute, ask_llm, run, _run_loop, LLMTransportError,
                   LLM_TIMEOUT, _repair_json, _STRICT_JSON_SUFFIX,
                   _validate_action_contract, _parse_reasoning_effort,
                   READ_CHARS)
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
        """Reads return bounded windows with truncation metadata (issue #7)."""
        Path(work_dir, "big.txt").write_text("x" * 5000)
        result = execute({"action": "read", "arg": f"{work_dir}/big.txt"}, work_dir)
        assert result["ok"] is True
        assert result["truncated"] is True
        assert result["output"].startswith("[big.txt: lines 1-1 of 1")
        assert len(result["output"]) <= READ_CHARS + 120  # window + header


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
        assert first_call_body["reasoning"] == {"enabled": False}

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
        """First attempt should explicitly disable model-default reasoning."""
        mock_post.return_value = mock_response({"action": "done"})
        ask_llm([{"role": "user", "content": "test"}])
        call_body = mock_post.call_args_list[0][1]["json"]
        assert call_body["reasoning"] == {"enabled": False}

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_think_true_enables_from_first_attempt(self, mock_post):
        """When think=True, reasoning should be enabled from attempt 0."""
        mock_post.return_value = mock_response({"action": "done"})
        ask_llm([{"role": "user", "content": "test"}], think=True)
        call_body = mock_post.call_args_list[0][1]["json"]
        assert "reasoning" in call_body
        assert call_body["reasoning"]["effort"] == "medium"

    @patch("askme.OPENROUTER_ALLOW_FALLBACKS", False)
    @patch("askme.OPENROUTER_REQUIRE_PARAMETERS", True)
    @patch("askme.OPENROUTER_PROVIDER", "siliconflow")
    @patch("askme.OPENROUTER_API_KEY", "test-key")
    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_strict_provider_and_route_metadata(self, mock_post):
        """Benchmark routing should be pinned and request actual endpoint metadata."""
        mock_post.return_value = mock_response({"action": "done"})
        ask_llm([{"role": "user", "content": "test"}])
        call = mock_post.call_args_list[0]
        assert call[1]["json"]["provider"] == {
            "order": ["siliconflow"], "allow_fallbacks": False,
            "require_parameters": True,
        }
        assert call[1]["headers"]["X-OpenRouter-Metadata"] == "enabled"

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


# --- Baseline reasoning-effort tests (always-on reasoners, e.g. gpt-oss-20b) ---

class TestReasoningEffortBaseline:
    """OPENROUTER_REASONING_EFFORT pins a floor effort for models whose
    reasoning cannot be disabled (harmony-format low/medium/high)."""

    @patch("askme.OPENROUTER_REASONING_EFFORT", "low")
    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_baseline_sent_on_first_attempt(self, mock_post):
        """Every call carries the baseline effort instead of enabled=False."""
        mock_post.return_value = mock_response({"action": "done"})
        ask_llm([{"role": "user", "content": "test"}], max_tokens=256)
        body = mock_post.call_args_list[0][1]["json"]
        assert body["reasoning"] == {"enabled": True, "effort": "low"}
        assert body["max_tokens"] == 1024  # low-effort floor over the 256 budget

    @patch("askme.OPENROUTER_REASONING_EFFORT", "low")
    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_gated_retry_escalates_above_baseline(self, mock_post):
        """The JSON-contract retry still raises effort past the baseline."""
        mock_post.side_effect = [
            mock_response_raw("not json"),
            mock_response({"action": "done"}),
        ]
        result = ask_llm([{"role": "user", "content": "test"}])
        assert result == {"action": "done"}
        first = mock_post.call_args_list[0][1]["json"]
        second = mock_post.call_args_list[1][1]["json"]
        assert first["reasoning"]["effort"] == "low"
        assert second["reasoning"]["effort"] == "medium"
        assert second["max_tokens"] >= 1536

    @patch("askme.OPENROUTER_REASONING_EFFORT", "high")
    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_escalation_never_lowers_baseline(self, mock_post):
        """A medium gated request must not drop a high baseline."""
        mock_post.side_effect = [
            mock_response_raw("not json"),
            mock_response({"action": "done"}),
        ]
        ask_llm([{"role": "user", "content": "test"}])
        first = mock_post.call_args_list[0][1]["json"]
        second = mock_post.call_args_list[1][1]["json"]
        assert first["reasoning"]["effort"] == "high"
        assert second["reasoning"]["effort"] == "high"
        assert second["max_tokens"] >= 2048

    @patch("askme.OPENROUTER_REASONING_EFFORT", "low")
    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_policy_off_pins_baseline_without_escalation(self, mock_post):
        """`off` suppresses harness escalation but the model still reasons —
        the request pins it to exactly the baseline on every attempt."""
        mock_post.side_effect = [
            mock_response_raw("not json"),
            mock_response({"action": "done"}),
        ]
        ask_llm([{"role": "user", "content": "test"}], reasoning_policy="off")
        for call in mock_post.call_args_list:
            assert call[1]["json"]["reasoning"] == {"enabled": True, "effort": "low"}

    @patch("askme.OPENROUTER_REASONING_EFFORT", "low")
    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_strict_final_retry_keeps_baseline(self, mock_post):
        """E03 strict retry disables *extra* reasoning; an always-on model
        still gets the baseline effort alongside the strict contract."""
        mock_post.side_effect = [
            mock_response_raw("bad"),
            mock_response_raw("still bad"),
            mock_response({"action": "done"}),
        ]
        result = ask_llm([{"role": "user", "content": "test"}])
        assert result == {"action": "done"}
        third = mock_post.call_args_list[2][1]["json"]
        assert third["reasoning"] == {"enabled": True, "effort": "low"}
        assert third["messages"][-1]["content"] == _STRICT_JSON_SUFFIX

    @patch("askme.OPENROUTER_REASONING_EFFORT", "low")
    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "local")
    def test_local_backend_ignores_baseline(self, mock_post):
        """The knob is OpenRouter-only; the local request stays untouched."""
        mock_post.return_value = mock_response({"action": "done"})
        ask_llm([
            {"role": "system", "content": "You are a helper."},
            {"role": "user", "content": "test"},
        ], max_tokens=256)
        body = mock_post.call_args_list[0][1]["json"]
        assert "reasoning" not in body
        assert not body["messages"][0]["content"].startswith("<|think|>")
        assert body["max_tokens"] == 256

    def test_parse_reasoning_effort_normalizes_and_validates(self):
        assert _parse_reasoning_effort(None) == ""
        assert _parse_reasoning_effort("") == ""
        assert _parse_reasoning_effort(" LOW ") == "low"
        assert _parse_reasoning_effort("high") == "high"
        with pytest.raises(ValueError):
            _parse_reasoning_effort("banana")


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

        def mock_get_step(task, state, goal="", step_num=0, max_steps=10,
                          think=False, **kwargs):
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


# --- E03: JSON repair tests ---

class TestJsonRepair:
    """E03: _repair_json salvages mechanically broken JSON from truncation."""

    def test_trailing_comma(self):
        result = _repair_json('{"action": "done", "arg": "f.txt",}')
        assert result == {"action": "done", "arg": "f.txt"}

    def test_unclosed_brace(self):
        result = _repair_json('{"action": "shell", "arg": "echo hi"')
        assert result == {"action": "shell", "arg": "echo hi"}

    def test_truncated_key(self):
        result = _repair_json('{"action": "done", "reas')
        assert result == {"action": "done"}

    def test_truncated_value(self):
        result = _repair_json('{"action": "edit", "arg": "main.c", "find": "old tex')
        assert result is not None
        assert result["action"] == "edit"
        assert result["arg"] == "main.c"

    def test_valid_json_passthrough(self):
        result = _repair_json('{"action": "done"}')
        assert result == {"action": "done"}

    def test_empty_string(self):
        assert _repair_json("") is None

    def test_no_brace(self):
        assert _repair_json("just some text") is None

    def test_unfixable_garbage(self):
        assert _repair_json('{{{broken') is None

    def test_non_dict_rejected(self):
        assert _repair_json("[1, 2, 3]") is None

    def test_extra_close_brace(self):
        assert _repair_json('{"a": 1}}') is None

    def test_trailing_prose(self):
        raw = '{"action": "shell", "arg": "python3 greet.py"}` - This suggests the initial run *'
        result = _repair_json(raw)
        assert result == {"action": "shell", "arg": "python3 greet.py"}

    def test_trailing_prose_with_backtick(self):
        raw = '{"action": "done"}\n\nSome commentary about what happened.'
        result = _repair_json(raw)
        assert result == {"action": "done"}


class TestActionContractValidation:
    """E03 follow-up: incomplete action JSON must retry, not execute."""

    def test_write_contract(self):
        assert _validate_action_contract(
            {"action": "write", "arg": "cli.py", "content": "print('hi')"}
        )
        assert _validate_action_contract(
            {"action": "write", "arg": "config.json", "content": {"ok": True}}
        )
        assert _validate_action_contract(
            {"action": "write", "arg": "items.json", "content": [1, 2]}
        )
        assert not _validate_action_contract({"action": "write", "arg": "cli.py"})
        assert not _validate_action_contract(
            {"action": "write", "arg": "cli.py", "content": ""}
        )
        assert not _validate_action_contract(
            {"action": "write", "arg": "", "content": "x"}
        )

    def test_edit_contract(self):
        assert _validate_action_contract(
            {"action": "edit", "arg": "f.py", "find": "old", "replace": ""}
        )
        assert not _validate_action_contract(
            {"action": "edit", "arg": "f.py", "find": "old"}
        )
        assert not _validate_action_contract(
            {"action": "edit", "arg": "f.py", "find": "", "replace": "new"}
        )
        assert not _validate_action_contract(
            {"action": "edit", "arg": "", "find": "old", "replace": "new"}
        )

    def test_shell_and_read_contract(self):
        assert _validate_action_contract({"action": "shell", "arg": "echo hi"})
        assert _validate_action_contract({"action": "read", "arg": "file.txt"})
        assert not _validate_action_contract({"action": "shell", "arg": ""})
        assert not _validate_action_contract({"action": "read"})

    def test_non_action_dicts_pass_through(self):
        assert _validate_action_contract({"tasks": ["one"]})
        assert _validate_action_contract({"valid": False, "reason": "missing"})

    @patch("askme.requests.post")
    def test_valid_but_incomplete_action_retries(self, mock_post):
        mock_post.side_effect = [
            mock_response({"action": "write", "arg": "cli.py"}),
            mock_response({"action": "done"}),
        ]
        result = ask_llm([{"role": "user", "content": "test"}])
        assert result == {"action": "done"}
        assert mock_post.call_count == 2

    @patch("askme.requests.post")
    def test_repaired_but_incomplete_action_retries(self, mock_post):
        mock_post.side_effect = [
            mock_response_raw('{"action":"write","arg":"cli.py","cont'),
            mock_response({"action": "done"}),
        ]
        result = ask_llm([{"role": "user", "content": "test"}])
        assert result == {"action": "done"}
        assert mock_post.call_count == 2


# --- E03: Tiered retry contract tests ---

class TestTieredRetryContract:
    """E03: Final auto-retry uses strict contract, no thinking."""

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_final_retry_no_auto_thinking(self, mock_post):
        """Attempt 2 (final) should NOT escalate to high thinking for auto retries."""
        mock_post.side_effect = [
            mock_response_raw("bad1"),
            mock_response_raw("bad2"),
            mock_response({"action": "done"}),
        ]
        result = ask_llm([{"role": "user", "content": "test"}])
        assert result == {"action": "done"}
        # Attempt 0: reasoning explicitly disabled
        assert mock_post.call_args_list[0][1]["json"]["reasoning"] == {"enabled": False}
        # Attempt 1: medium reasoning
        assert mock_post.call_args_list[1][1]["json"]["reasoning"]["effort"] == "medium"
        # Attempt 2: reasoning disabled (strict contract instead)
        assert mock_post.call_args_list[2][1]["json"]["reasoning"] == {"enabled": False}

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_final_retry_injects_strict_suffix(self, mock_post):
        """Attempt 2 should append strict JSON-only instruction."""
        mock_post.side_effect = [
            mock_response_raw("bad1"),
            mock_response_raw("bad2"),
            mock_response({"action": "done"}),
        ]
        ask_llm([
            {"role": "system", "content": "You are a helper."},
            {"role": "user", "content": "test"},
        ])
        third_msgs = mock_post.call_args_list[2][1]["json"]["messages"]
        assert third_msgs[-1]["content"] == _STRICT_JSON_SUFFIX

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_explicit_think_level_preserved_on_final_retry(self, mock_post):
        """Explicit think_level should override E03 strict behavior."""
        mock_post.side_effect = [
            mock_response_raw("bad1"),
            mock_response_raw("bad2"),
            mock_response({"action": "done"}),
        ]
        ask_llm([{"role": "user", "content": "test"}], think_level="high")
        # All three attempts should have high thinking
        for i, call in enumerate(mock_post.call_args_list):
            body = call[1]["json"]
            assert body["reasoning"]["effort"] == "high", f"attempt {i} lost think_level"
            # No strict suffix when think_level is explicit
            msgs = body["messages"]
            assert msgs[-1]["content"] != _STRICT_JSON_SUFFIX

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "local")
    def test_final_retry_no_think_prefix_local(self, mock_post):
        """Local backend: attempt 2 should not prepend <|think|>."""
        mock_post.side_effect = [
            mock_response_raw("bad1"),
            mock_response_raw("bad2"),
            mock_response({"action": "done"}),
        ]
        ask_llm([
            {"role": "system", "content": "You are a helper."},
            {"role": "user", "content": "test"},
        ])
        third_body = mock_post.call_args_list[2][1]["json"]
        sys_content = third_body["messages"][0]["content"]
        assert not sys_content.startswith("<|think|>"), "Final retry should not use thinking"

    @patch("askme.requests.post")
    def test_repair_avoids_retry(self, mock_post):
        """If _repair_json succeeds, no additional LLM call is made."""
        mock_post.return_value = mock_response_raw('{"action": "done", "arg": "f.txt",}')
        result = ask_llm([{"role": "user", "content": "test"}])
        assert result["action"] == "done"
        assert mock_post.call_count == 1

    @patch("askme.requests.post")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_think_true_final_retry_drops_thinking(self, mock_post):
        """think=True: attempt 0=medium, attempt 1=high, attempt 2=none (strict)."""
        mock_post.side_effect = [
            mock_response_raw("bad"),
            mock_response_raw("bad"),
            mock_response({"action": "done"}),
        ]
        ask_llm([{"role": "user", "content": "test"}], think=True)
        assert mock_post.call_args_list[0][1]["json"]["reasoning"]["effort"] == "medium"
        assert mock_post.call_args_list[1][1]["json"]["reasoning"]["effort"] == "high"
        assert mock_post.call_args_list[2][1]["json"]["reasoning"] == {"enabled": False}
