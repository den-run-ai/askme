"""Core unit tests: execute(), ask_llm(), thinking retry, null-arg normalization."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from askme import execute, ask_llm, run
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
