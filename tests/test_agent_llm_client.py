"""LLM client seam tests (issue #37): reasoning decision, request build,
one-shot transport, and pure reply decode — each exercised independently of
the ask_llm retry loop, whose policy behavior stays pinned by the existing
TestAskLlm/TestThinkingRetry/TestLLMTransport/TestTieredRetryContract suites."""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests as req_lib

import askme
from askme import (
    _build_llm_request,
    _decode_action_reply,
    _extract_message_text,
    _llm_http_attempt,
    _reasoning_decision,
)

# --- Reasoning decision (pure escalation table) ---


class TestReasoningDecision:
    def test_explicit_level_pins_every_attempt(self):
        for attempt in range(3):
            requested, effective, trigger = _reasoning_decision(
                attempt, False, "high", "gated", "validation"
            )
            assert (requested, effective, trigger) == ("high", "high", "validation")

    def test_think_escalates_then_goes_strict(self):
        assert _reasoning_decision(0, True, None, "gated", "planner") == (
            "adaptive",
            "medium",
            "planner",
        )
        assert _reasoning_decision(1, True, None, "gated", "planner") == (
            "adaptive",
            "high",
            "planner",
        )
        # Final auto-retry drops thinking: more reasoning doesn't fix format errors.
        assert _reasoning_decision(2, True, None, "gated", "planner") == (
            "adaptive",
            None,
            "planner",
        )

    def test_default_gets_one_json_contract_retry(self):
        assert _reasoning_decision(0, False, None, "gated", "unspecified") == (
            None,
            None,
            "unspecified",
        )
        assert _reasoning_decision(1, False, None, "gated", "unspecified") == (
            "medium",
            "medium",
            "json_retry",
        )
        assert _reasoning_decision(2, False, None, "gated", "unspecified") == (
            None,
            None,
            "unspecified",
        )

    def test_off_policy_suppresses_effective_level_only(self):
        requested, effective, trigger = _reasoning_decision(1, False, None, "off", "unspecified")
        assert (requested, effective, trigger) == ("medium", None, "json_retry")
        requested, effective, _ = _reasoning_decision(0, False, "high", "off", "validation")
        assert (requested, effective) == ("high", None)


# --- Request build (golden bodies per backend) ---


class TestBuildLlmRequest:
    @patch("askme.LLM_BACKEND", "local")
    def test_local_default_body_and_headers(self):
        messages = [{"role": "user", "content": "hi"}]
        body, headers, sent_effort = _build_llm_request(messages, 256, None, strict=False)
        assert body == {
            "model": askme.MODEL,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 256,
        }
        assert headers == {"Content-Type": "application/json"}
        assert sent_effort is None

    @patch("askme.LLM_BACKEND", "local")
    def test_local_think_prefixes_system_and_bumps_budget(self):
        messages = [
            {"role": "system", "content": "You are a helper."},
            {"role": "user", "content": "go"},
        ]
        body, _, sent_effort = _build_llm_request(messages, 256, "medium", strict=False)
        assert body["messages"][0]["content"].startswith("<|think|>\n")
        assert body["max_tokens"] == 512
        assert sent_effort == "medium"
        # The caller's list and message dicts must never be mutated.
        assert messages[0]["content"] == "You are a helper."
        body, _, _ = _build_llm_request(messages, 256, "high", strict=False)
        assert body["max_tokens"] == 768

    @patch("askme.LLM_BACKEND", "local")
    def test_local_think_without_system_message_still_bumps(self):
        messages = [{"role": "user", "content": "go"}]
        body, _, _ = _build_llm_request(messages, 256, "medium", strict=False)
        assert body["messages"][0]["content"] == "go"
        assert body["max_tokens"] == 512

    @patch("askme.OPENROUTER_REASONING_EFFORT", "")
    @patch("askme.OPENROUTER_API_KEY", "test-key")
    @patch("askme.OPENROUTER_PROVIDER", "Parasail")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_openrouter_pins_provider_and_disables_default_reasoning(self):
        body, headers, sent_effort = _build_llm_request(
            [{"role": "user", "content": "hi"}], 256, None, strict=False
        )
        assert body["provider"] == {
            "order": ["Parasail"],
            "allow_fallbacks": askme.OPENROUTER_ALLOW_FALLBACKS,
            "require_parameters": askme.OPENROUTER_REQUIRE_PARAMETERS,
        }
        assert body["reasoning"] == {"enabled": False}
        assert body["max_tokens"] == 256
        assert headers["Authorization"] == "Bearer test-key"
        assert headers["X-OpenRouter-Metadata"] == "enabled"
        assert sent_effort in ("", None) or not sent_effort

    @patch("askme.OPENROUTER_REASONING_EFFORT", "")
    @patch("askme.OPENROUTER_API_KEY", "")
    @patch("askme.OPENROUTER_PROVIDER", "")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_openrouter_without_provider_or_key(self):
        body, headers, _ = _build_llm_request(
            [{"role": "user", "content": "hi"}], 256, None, strict=False
        )
        assert "provider" not in body
        assert headers == {"Content-Type": "application/json"}

    @patch("askme.OPENROUTER_REASONING_EFFORT", "")
    @patch("askme.OPENROUTER_API_KEY", "")
    @patch("askme.OPENROUTER_PROVIDER", "Parasail")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_openrouter_effort_floors_token_budget(self):
        body, _, sent_effort = _build_llm_request(
            [{"role": "user", "content": "hi"}], 100, "medium", strict=False
        )
        assert body["reasoning"] == {"enabled": True, "effort": "medium"}
        assert body["max_tokens"] == askme._EFFORT_TOKEN_FLOOR["medium"]
        assert sent_effort == "medium"

    @patch("askme.OPENROUTER_REASONING_EFFORT", "high")
    @patch("askme.OPENROUTER_API_KEY", "")
    @patch("askme.OPENROUTER_PROVIDER", "Parasail")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_openrouter_baseline_effort_never_lowered(self):
        body, _, sent_effort = _build_llm_request(
            [{"role": "user", "content": "hi"}], 100, "medium", strict=False
        )
        assert sent_effort == "high"
        assert body["reasoning"] == {"enabled": True, "effort": "high"}
        assert body["max_tokens"] == askme._EFFORT_TOKEN_FLOOR["high"]

    @patch("askme.LLM_BACKEND", "local")
    def test_strict_appends_contract_after_backend_shaping(self):
        messages = [
            {"role": "system", "content": "You are a helper."},
            {"role": "user", "content": "go"},
        ]
        body, _, _ = _build_llm_request(messages, 256, "medium", strict=True)
        assert body["messages"][0]["content"].startswith("<|think|>\n")
        assert body["messages"][-1] == {
            "role": "user",
            "content": askme._STRICT_JSON_SUFFIX,
        }
        assert len(messages) == 2


# --- One-shot transport (classification only, injected HTTP callable) ---


def _resp(status=200, body=None, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    if body is None:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = body
    return resp


class TestLlmHttpAttempt:
    def test_success_passes_through_request_shape(self):
        calls = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            calls.update(url=url, json=json, headers=headers, timeout=timeout)
            return _resp(body={"choices": []})

        rj, failure = _llm_http_attempt({"b": 1}, {"h": 2}, 42, post=fake_post)
        assert failure is None
        assert rj == {"choices": []}
        assert calls == {"url": askme.API, "json": {"b": 1}, "headers": {"h": 2}, "timeout": 42}

    def test_connection_error_classified_transport(self):
        def fake_post(*a, **k):
            raise req_lib.exceptions.ConnectionError("refused")

        rj, failure = _llm_http_attempt({}, {}, 1, post=fake_post)
        assert rj is None
        assert failure["kind"] == "transport"
        assert failure["detail"] == "ConnectionError: refused"
        assert isinstance(failure["error"], req_lib.exceptions.ConnectionError)
        assert failure["status"] is None

    def test_timeout_classified_transport(self):
        def fake_post(*a, **k):
            raise req_lib.exceptions.Timeout("slow")

        _, failure = _llm_http_attempt({}, {}, 1, post=fake_post)
        assert failure["kind"] == "transport"

    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    def test_retryable_statuses(self, status):
        rj, failure = _llm_http_attempt({}, {}, 1, post=lambda *a, **k: _resp(status=status))
        assert rj is None
        assert failure == {
            "kind": "http_retryable",
            "detail": f"HTTP {status}",
            "error": None,
            "status": status,
        }

    def test_client_error_is_fatal_with_bounded_excerpt(self):
        long_body = "x" * 300
        _, failure = _llm_http_attempt(
            {}, {}, 1, post=lambda *a, **k: _resp(status=404, text=long_body)
        )
        assert failure["kind"] == "http_fatal"
        assert failure["detail"] == "HTTP 404: " + "x" * 200

    def test_non_json_success_body(self):
        _, failure = _llm_http_attempt(
            {}, {}, 1, post=lambda *a, **k: _resp(status=200, text="<html>Bad Gateway</html>")
        )
        assert failure["kind"] == "non_json"
        assert failure["detail"] == "<html>Bad Gateway</html>"
        assert isinstance(failure["error"], ValueError)

    @patch("askme.requests.post")
    def test_default_post_is_module_requests(self, mock_post):
        mock_post.return_value = _resp(body={"ok": 1})
        rj, failure = _llm_http_attempt({"b": 1}, {"h": 2}, 7)
        assert (rj, failure) == ({"ok": 1}, None)
        assert mock_post.call_args[0][0] == askme.API


# --- Message text extraction (reasoning fallback) ---


class TestExtractMessageText:
    def _rj(self, message):
        return {"choices": [{"message": message}]}

    def test_content_wins_when_present(self):
        assert _extract_message_text(self._rj({"content": "hello"})) == "hello"

    def test_empty_content_falls_back_to_reasoning_content(self):
        msg = {"content": "", "reasoning_content": '{"action":"done"}'}
        assert _extract_message_text(self._rj(msg)) == '{"action":"done"}'

    def test_null_content_falls_back_to_reasoning_dict(self):
        msg = {"content": None, "reasoning": {"content": "fallback"}}
        assert _extract_message_text(self._rj(msg)) == "fallback"

    def test_whitespace_content_falls_back_to_reasoning_string(self):
        msg = {"content": "   ", "reasoning": "fallback"}
        assert _extract_message_text(self._rj(msg)) == "fallback"

    def test_all_empty_returns_empty(self):
        assert _extract_message_text(self._rj({"content": None})) == ""


# --- Pure reply decode ---


class TestDecodeActionReply:
    def test_plain_action(self):
        obj, cleaned, repaired = _decode_action_reply('{"action":"done"}', "stop")
        assert obj == {"action": "done"}
        assert cleaned == '{"action":"done"}'
        assert repaired is False

    def test_strips_think_channel_and_fences_together(self):
        text = '<think>hmm</think><|channel>t\n<channel|>```json\n{"action":"done"}\n```'
        obj, _, repaired = _decode_action_reply(text, "stop")
        assert obj == {"action": "done"}
        assert repaired is False

    def test_extracts_object_after_prose(self):
        obj, _, _ = _decode_action_reply('Sure! {"action":"shell","arg":"ls"}', "stop")
        assert obj == {"action": "shell", "arg": "ls"}

    def test_non_dict_json_raises_with_cleaned_text(self):
        with pytest.raises(json.JSONDecodeError) as exc_info:
            _decode_action_reply("[1, 2]", "stop")
        assert exc_info.value.cleaned_text == "[1, 2]"

    def test_repairable_json_flags_repaired(self):
        obj, _, repaired = _decode_action_reply('{"action":"done","reasoning":"x",}', "stop")
        assert obj == {"action": "done", "reasoning": "x"}
        assert repaired is True

    def test_contract_invalid_action_raises_even_when_parseable(self):
        with pytest.raises(json.JSONDecodeError, match="Incomplete action"):
            _decode_action_reply('{"action":"shell"}', "stop")

    def test_garbage_raises_with_cleaned_text(self):
        with pytest.raises(json.JSONDecodeError) as exc_info:
            _decode_action_reply("no json here", "stop")
        assert exc_info.value.cleaned_text == "no json here"

    def test_closed_sentinel_block_attaches_content(self):
        text = (
            '{"action":"write","arg":"f.py","reasoning":"w"}\n<<<CONTENT\nline1\nline2\nCONTENT>>>'
        )
        obj, _, _ = _decode_action_reply(text, "stop")
        assert obj["content"] == "line1\nline2"
        assert "content_truncated" not in obj

    def test_unclosed_sentinel_at_length_keeps_last_complete_line(self):
        text = '{"action":"write","arg":"f.py","reasoning":"w"}\n<<<CONTENT\nline1\nline2\n'
        obj, _, _ = _decode_action_reply(text, "length")
        assert obj["content_truncated"] is True
        # The stripped trailing newline is restored so the run loop's
        # partial-line trim keeps line2.
        assert obj["content"] == "line1\nline2\n"

    def test_unclosed_sentinel_without_length_is_not_truncated(self):
        text = '{"action":"write","arg":"f.py","reasoning":"w"}\n<<<CONTENT\nline1\nline2'
        obj, _, _ = _decode_action_reply(text, "stop")
        assert obj["content"] == "line1\nline2"
        assert "content_truncated" not in obj
