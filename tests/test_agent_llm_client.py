"""LLM client seam tests (issue #37): reasoning decision, request build,
one-shot transport, and pure reply decode — each exercised independently of
the ask_llm retry loop, whose policy behavior stays pinned by the existing
TestAskLlm/TestThinkingRetry/TestLLMTransport/TestTieredRetryContract suites."""

import dataclasses
import json
from unittest.mock import patch

import pytest
import requests as req_lib
from _test_support import mock_http_response, mock_response, mock_response_raw

import askme
from askme import (
    LLMClient,
    LLMSettings,
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


class TestLlmHttpAttempt:
    def test_json_null_and_missing_body_are_distinct(self):
        assert mock_http_response(json_body=None).json() is None
        with pytest.raises(ValueError, match="not json"):
            mock_http_response().json()
        with pytest.raises(AttributeError):
            setattr(mock_http_response(), "unexpected", True)

    def test_success_passes_through_request_shape(self):
        calls = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            calls.update(url=url, json=json, headers=headers, timeout=timeout)
            return mock_http_response(json_body={"choices": []})

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
        response = mock_http_response(status_code=status)
        rj, failure = _llm_http_attempt({}, {}, 1, post=lambda *a, **k: response)
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
            {},
            {},
            1,
            post=lambda *a, **k: mock_http_response(status_code=404, text=long_body),
        )
        assert failure["kind"] == "http_fatal"
        assert failure["detail"] == "HTTP 404: " + "x" * 200

    def test_non_json_success_body(self):
        _, failure = _llm_http_attempt(
            {},
            {},
            1,
            post=lambda *a, **k: mock_http_response(text="<html>Bad Gateway</html>"),
        )
        assert failure["kind"] == "non_json"
        assert failure["detail"] == "<html>Bad Gateway</html>"
        assert isinstance(failure["error"], ValueError)

    @patch("askme.requests.post")
    def test_default_post_is_module_requests(self, mock_post):
        mock_post.return_value = mock_http_response(json_body={"ok": 1})
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
        with pytest.raises(json.JSONDecodeError, match="Incomplete action:.*requires field 'arg'"):
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

    def test_unclosed_sentinel_without_length_is_rejected(self):
        text = '{"action":"write","arg":"f.py","reasoning":"w"}\n<<<CONTENT\nline1\nline2'
        with pytest.raises(json.JSONDecodeError, match="sentinel content must close"):
            _decode_action_reply(text, "stop")


# --- Client-local settings and injected dependencies (issue #37) ---


class TestLLMSettings:
    def test_from_env_local_defaults(self):
        s = LLMSettings.from_env(env={})
        assert s.backend == "local"
        assert s.api == "http://localhost:8080/v1/chat/completions"
        assert s.model == "gemma-4-e4b"
        assert s.api_key == ""
        assert s.provider == "Parasail"
        assert s.allow_fallbacks is True
        assert s.require_parameters is False
        assert s.reasoning_effort == ""
        assert s.timeout == askme.LLM_TIMEOUT

    def test_from_env_openrouter_derivation(self):
        s = LLMSettings.from_env(
            env={
                "LLM_BACKEND": "openrouter",
                "OPENROUTER_MODEL": "vendor/model-x",
                "OPENROUTER_API_KEY": "k",
                "OPENROUTER_PROVIDER": " SomeProvider ",
                "OPENROUTER_ALLOW_FALLBACKS": "0",
                "OPENROUTER_REQUIRE_PARAMETERS": "1",
                "OPENROUTER_REASONING_EFFORT": "High",
            }
        )
        assert s.backend == "openrouter"
        assert s.api == askme.OPENROUTER_CHAT_API
        assert s.model == "vendor/model-x"
        assert s.api_key == "k"
        assert s.provider == "SomeProvider"
        assert s.allow_fallbacks is False
        assert s.require_parameters is True
        assert s.reasoning_effort == "high"

    def test_from_env_local_custom_endpoint(self):
        s = LLMSettings.from_env(
            env={"LLM_API_URL": "http://gpu-box:9090/v1/chat/completions", "LLM_MODEL": "m"}
        )
        assert s.api == "http://gpu-box:9090/v1/chat/completions"
        assert s.model == "m"

    def test_from_env_rejects_bad_effort(self):
        with pytest.raises(ValueError, match="OPENROUTER_REASONING_EFFORT"):
            LLMSettings.from_env(env={"OPENROUTER_REASONING_EFFORT": "max"})

    def test_settings_are_immutable(self):
        s = LLMSettings.from_env(env={})
        with pytest.raises(dataclasses.FrozenInstanceError):
            s.model = "other"  # type: ignore[misc]

    @patch("askme.MODEL", "patched-model")
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_current_snapshots_patched_globals(self):
        s = LLMSettings.current()
        assert s.model == "patched-model"
        assert s.backend == "openrouter"

    def test_module_mirrors_match_the_one_derivation(self):
        s = askme._DEFAULT_LLM_SETTINGS
        assert askme.LLM_BACKEND == s.backend
        assert askme.API == s.api
        assert askme.MODEL == s.model
        assert askme.OPENROUTER_API_KEY == s.api_key
        assert askme.OPENROUTER_PROVIDER == s.provider
        assert askme.OPENROUTER_ALLOW_FALLBACKS == s.allow_fallbacks
        assert askme.OPENROUTER_REQUIRE_PARAMETERS == s.require_parameters
        assert askme.OPENROUTER_REASONING_EFFORT == s.reasoning_effort

    def test_settings_passthrough_overrides_globals(self):
        custom = LLMSettings(
            backend="openrouter",
            api=askme.OPENROUTER_CHAT_API,
            model="vendor/custom",
            api_key="secret",
            provider="ProvX",
            allow_fallbacks=False,
            require_parameters=True,
            reasoning_effort="",
            timeout=5,
        )
        body, headers, _ = _build_llm_request(
            [{"role": "user", "content": "hi"}], 256, None, strict=False, settings=custom
        )
        assert body["model"] == "vendor/custom"
        assert body["provider"] == {
            "order": ["ProvX"],
            "allow_fallbacks": False,
            "require_parameters": True,
        }
        assert headers["Authorization"] == "Bearer secret"

    def test_http_attempt_api_override(self):
        calls = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            calls["url"] = url
            return mock_http_response(json_body={"choices": []})

        _, failure = _llm_http_attempt({}, {}, 1, post=fake_post, api="http://alt:1/v1")
        assert failure is None
        assert calls["url"] == "http://alt:1/v1"


def _client_settings(**overrides):
    base = {
        "backend": "openrouter",
        "api": askme.OPENROUTER_CHAT_API,
        "model": "vendor/model-a",
        "api_key": "key-a",
        "provider": "ProvA",
        "allow_fallbacks": True,
        "require_parameters": False,
        "reasoning_effort": "",
        "timeout": 7,
    }
    base.update(overrides)
    return LLMSettings(**base)


DONE_REPLY = {"action": "done", "arg": "", "reasoning": "r"}


class TestLLMClientInjection:
    def test_two_clients_share_a_process_without_global_leakage(self):
        calls = []

        def fake_post(url, json=None, headers=None, timeout=None):
            calls.append((url, json["model"], (headers or {}).get("Authorization"), timeout))
            return mock_response(DONE_REPLY, finish_reason="stop")

        before = (askme.LLM_BACKEND, askme.API, askme.MODEL, askme.OPENROUTER_API_KEY)
        events = []
        a = LLMClient(
            settings=_client_settings(),
            post=fake_post,
            event_sink=events.append,
            log_sink=lambda m: None,
        )
        b = LLMClient(
            settings=_client_settings(
                backend="local",
                api="http://localhost:1234/v1/chat/completions",
                model="local-b",
                api_key="",
                provider="",
                timeout=9,
            ),
            post=fake_post,
            event_sink=events.append,
            log_sink=lambda m: None,
        )
        ra = a.ask([{"role": "user", "content": "hi"}], reasoning_trigger="seam_test")
        rb = b.ask([{"role": "user", "content": "hi"}], reasoning_trigger="seam_test")
        assert ra["action"] == "done" and rb["action"] == "done"
        assert calls[0] == (askme.OPENROUTER_CHAT_API, "vendor/model-a", "Bearer key-a", 7)
        assert calls[1] == ("http://localhost:1234/v1/chat/completions", "local-b", None, 9)
        assert (askme.LLM_BACKEND, askme.API, askme.MODEL, askme.OPENROUTER_API_KEY) == before

    def test_injected_sleeper_and_log_sink_cover_retries(self, capsys):
        sleeps, logs = [], []
        attempts = {"n": 0}

        def flaky_post(url, json=None, headers=None, timeout=None):
            attempts["n"] += 1
            if attempts["n"] < 3:
                return mock_http_response(status_code=500)
            return mock_response(DONE_REPLY, finish_reason="stop")

        client = LLMClient(
            settings=_client_settings(),
            post=flaky_post,
            sleep=sleeps.append,
            log_sink=logs.append,
            event_sink=lambda e: None,
        )
        obj = client.ask([{"role": "user", "content": "hi"}], reasoning_trigger="seam_test")
        assert obj["action"] == "done"
        assert sleeps == [1, 3]
        assert sum("HTTP 500" in m for m in logs) == 2
        # The module console logger was not touched by the injected sinks.
        assert "HTTP 500" not in capsys.readouterr().out

    def test_usage_and_reasoning_events_reach_injected_event_sink(self):
        events = []

        def fake_post(url, json=None, headers=None, timeout=None):
            return mock_response(
                DONE_REPLY,
                finish_reason="stop",
                usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            )

        client = LLMClient(
            settings=_client_settings(reasoning_effort="low"),
            post=fake_post,
            log_sink=lambda m: None,
            event_sink=events.append,
        )
        client.ask([{"role": "user", "content": "hi"}], reasoning_trigger="seam_test")
        decision = next(e for e in events if e["event"] == "reasoning_decision")
        assert decision["baseline_effort"] == "low"
        tokens = next(e for e in events if e["event"] == "tokens")
        assert tokens["total"] == 5
        assert tokens["model"] == "vendor/model-a"

    def test_ask_llm_facade_still_snapshots_module_globals(self):
        with (
            patch("askme.LLM_BACKEND", "openrouter"),
            patch("askme.API", "http://facade-test/v1"),
            patch("askme.MODEL", "facade/model"),
            patch("askme.OPENROUTER_API_KEY", "fk"),
            patch("askme.requests.post") as mock_post,
        ):
            mock_post.return_value = mock_response(DONE_REPLY, finish_reason="stop")
            obj = askme.ask_llm([{"role": "user", "content": "hi"}], reasoning_trigger="seam_test")
        assert obj["action"] == "done"
        url = mock_post.call_args[0][0]
        body = mock_post.call_args[1]["json"]
        headers = mock_post.call_args[1]["headers"]
        assert url == "http://facade-test/v1"
        assert body["model"] == "facade/model"
        assert headers["Authorization"] == "Bearer fk"


class TestWriteRetryBudgetPerClient:
    """Codex P2 (PR #61): the truncated-write retry budget must follow the
    client's backend, not the process-wide import-time backend."""

    def test_write_retry_tokens_follow_settings_backend(self):
        assert _client_settings().write_retry_tokens() == 8192
        assert _client_settings(backend="local").write_retry_tokens() == 512
        assert _client_settings(step_write_tokens=1024).write_retry_tokens() == 1024
        assert LLMSettings.from_env(env={}).write_retry_tokens() == 512
        assert LLMSettings.from_env(env={"LLM_BACKEND": "openrouter"}).write_retry_tokens() == 8192

    @patch("askme.STEP_WRITE_TOKENS", 777)
    def test_current_pins_the_patchable_module_budget(self):
        assert LLMSettings.current().write_retry_tokens() == 777

    def _truncated_then_done(self, bodies):
        truncated_write = '{"action": "write", "arg": "a.py", "content": "aaaa'
        replies = [
            mock_response_raw(truncated_write, finish_reason="length"),
            mock_response(DONE_REPLY, finish_reason="stop"),
        ]

        def fake_post(url, json=None, headers=None, timeout=None):
            bodies.append(json)
            return replies.pop(0)

        return fake_post

    @patch("askme.STEP_WRITE_TOKENS", 512)
    @patch("askme.LLM_BACKEND", "local")
    def test_openrouter_client_in_local_process_gets_full_budget(self):
        bodies = []
        client = LLMClient(
            settings=_client_settings(),
            post=self._truncated_then_done(bodies),
            log_sink=lambda m: None,
            event_sink=lambda e: None,
        )
        obj = client.ask(
            [{"role": "user", "content": "hi"}],
            max_tokens=256,
            max_retries=1,
            reasoning_policy="off",
            reasoning_trigger="seam_test",
        )
        assert obj["action"] == "done"
        assert bodies[0]["max_tokens"] == 256
        assert bodies[1]["max_tokens"] == 8192

    @patch("askme.STEP_WRITE_TOKENS", 8192)
    @patch("askme.LLM_BACKEND", "openrouter")
    def test_local_client_in_openrouter_process_keeps_local_bound(self):
        bodies = []
        client = LLMClient(
            settings=_client_settings(
                backend="local",
                api="http://localhost:1234/v1/chat/completions",
                api_key="",
                provider="",
            ),
            post=self._truncated_then_done(bodies),
            log_sink=lambda m: None,
            event_sink=lambda e: None,
        )
        obj = client.ask(
            [{"role": "user", "content": "hi"}],
            max_tokens=256,
            max_retries=1,
            reasoning_policy="off",
            reasoning_trigger="seam_test",
        )
        assert obj["action"] == "done"
        assert bodies[0]["max_tokens"] == 256
        assert bodies[1]["max_tokens"] == 512
