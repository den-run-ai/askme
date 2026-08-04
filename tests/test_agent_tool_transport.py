"""Native tool-call action transport arm (issue #68).

The "tools" transport sends the registry-derived tool definitions on
``expect="action"`` calls and decodes the structured tool call into the same
envelope validation as the JSON text path. These tests pin the selection
surface, request shaping, decode contract, retry/budget behavior, and the
hash-logged configuration record. The default arm remains "json"; flipping
the default is gated on the paired local bench (see EXPERIMENTS.md E25).
"""

import dataclasses
import json
from unittest.mock import patch

import pytest
from _test_support import mock_http_response

import askme
from actions import ACTION_SPECS, DecodedAction
from askme import (
    ACTION_TRANSPORTS,
    SYSTEM_STEP,
    SYSTEM_STEP_TOOLS,
    LLMClient,
    LLMSettings,
    RunConfig,
    get_capability_profile,
)


def _settings(**overrides):
    base = {
        "backend": "local",
        "api": "http://localhost:9999/v1/chat/completions",
        "model": "test-model",
        "api_key": "",
        "provider": "",
        "allow_fallbacks": True,
        "require_parameters": False,
        "reasoning_effort": "",
        "timeout": 30,
    }
    base.update(overrides)
    return LLMSettings(**base)


def _tool_reply(name, arguments, finish_reason="stop", content=None, extra_calls=()):
    calls = [
        {"id": "call_1", "type": "function", "function": {"name": name, "arguments": arguments}}
    ]
    calls.extend(extra_calls)
    return mock_http_response(
        json_body={
            "choices": [
                {
                    "message": {"content": content, "tool_calls": calls},
                    "finish_reason": finish_reason,
                }
            ]
        }
    )


def _text_reply(text, finish_reason="stop"):
    return mock_http_response(
        json_body={"choices": [{"message": {"content": text}, "finish_reason": finish_reason}]}
    )


def _client(replies, bodies, **settings_overrides):
    replies = list(replies)

    def fake_post(url, json=None, headers=None, timeout=None):
        bodies.append(json)
        return replies.pop(0)

    return LLMClient(
        settings=_settings(**settings_overrides),
        post=fake_post,
        sleep=lambda s: None,
        log_sink=lambda message: None,
        event_sink=lambda event: None,
    )


def _ask_action(client, max_retries=0, max_tokens=256):
    return client.ask(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        max_tokens=max_tokens,
        max_retries=max_retries,
        reasoning_policy="off",
        reasoning_trigger="tool_transport_test",
        expect="action",
    )


class TestTransportSelection:
    def test_default_is_json_and_env_selects_tools(self):
        assert LLMSettings.from_env(env={}).action_transport == "json"
        env = {"LLM_ACTION_TRANSPORT": "tools"}
        assert LLMSettings.from_env(env=env).action_transport == "tools"

    def test_invalid_env_transport_is_rejected(self):
        with pytest.raises(ValueError, match="LLM_ACTION_TRANSPORT"):
            LLMSettings.from_env(env={"LLM_ACTION_TRANSPORT": "grpc"})

    @patch("askme.ACTION_TRANSPORT", "tools")
    def test_current_snapshots_patched_transport(self):
        assert LLMSettings.current().action_transport == "tools"

    def test_resolved_run_settings_reject_invalid_transport(self):
        with pytest.raises(ValueError, match="action_transport"):
            askme._resolve_run_llm_settings(_settings(action_transport="grpc"))

    def test_ask_rejects_invalid_transport(self):
        client = _client([], [], action_transport="grpc")
        with pytest.raises(ValueError, match="action_transport"):
            _ask_action(client)


class TestToolDefinitions:
    def test_tools_mirror_the_action_registry(self):
        tools = askme._action_tools()
        assert [tool["function"]["name"] for tool in tools] == list(ACTION_SPECS)
        for tool in tools:
            function = tool["function"]
            spec = ACTION_SPECS[function["name"]]
            assert tool["type"] == "function"
            assert function["description"]
            properties = function["parameters"]["properties"]
            assert "action" not in properties
            assert set(properties) == {f for f in spec.allowed if f != "action"}
            assert function["parameters"].get("required", []) == list(spec.requires)

    def test_field_types_cover_registry_shapes(self):
        by_name = {tool["function"]["name"]: tool["function"] for tool in askme._ACTION_TOOLS}
        assert by_name["write"]["parameters"]["properties"]["append"]["type"] == "boolean"
        assert by_name["read"]["parameters"]["properties"]["cursor"]["type"] == "integer"
        assert by_name["read"]["parameters"]["properties"]["sha256"]["type"] == "string"
        assert by_name["done"]["parameters"].get("required", []) == []


class TestRequestShaping:
    def test_action_requests_carry_tools_with_auto_choice(self):
        bodies = []
        client = _client([_tool_reply("done", "{}")], bodies, action_transport="tools")
        _ask_action(client)
        assert bodies[0]["tools"] == askme._ACTION_TOOLS
        # "required" corrupted native Gemma 4 delimiters on b9618 (2026-08-04
        # smoke): the arm pins "auto" and leaves empty replies to retry policy.
        assert bodies[0]["tool_choice"] == "auto"

    def test_non_action_requests_never_carry_tools(self):
        bodies = []
        client = _client([_text_reply('{"tasks": ["t"]}')], bodies, action_transport="tools")
        client.ask(
            [{"role": "user", "content": "plan"}],
            max_retries=0,
            reasoning_policy="off",
            expect="plan",
        )
        assert "tools" not in bodies[0]
        assert "tool_choice" not in bodies[0]

    def test_json_transport_requests_never_carry_tools(self):
        bodies = []
        client = _client([_text_reply('{"action": "done"}')], bodies, action_transport="json")
        _ask_action(client)
        assert "tools" not in bodies[0]

    def test_strict_retry_uses_the_tool_contract_suffix(self):
        bodies = []
        client = _client(
            [_text_reply("no call"), _text_reply("still none"), _tool_reply("done", "{}")],
            bodies,
            action_transport="tools",
        )
        _ask_action(client, max_retries=2)
        assert bodies[2]["messages"][-1]["content"] == askme._STRICT_TOOL_SUFFIX

    def test_get_step_selects_the_transport_prompt(self):
        state = {"last_steps": [], "completed_tasks": [], "all_steps": []}
        for transport, expected_prompt in (("tools", SYSTEM_STEP_TOOLS), ("json", SYSTEM_STEP)):
            bodies = []
            reply = (
                _tool_reply("done", "{}")
                if transport == "tools"
                else _text_reply('{"action": "done"}')
            )
            client = _client([reply], bodies, action_transport=transport)
            askme.get_step("do the task", state, client=client, reasoning_policy="off")
            assert bodies[0]["messages"][0]["content"] == expected_prompt


class TestToolDecode:
    def test_valid_tool_call_lands_in_the_envelope_contract(self):
        client = _client(
            [_tool_reply("write", '{"arg": "a.py", "content": "x = 1\\n", "reasoning": "r"}')],
            [],
            action_transport="tools",
        )
        decoded = _ask_action(client)
        assert isinstance(decoded, DecodedAction)
        assert decoded["action"] == "write"
        assert decoded["content"] == "x = 1\n"

    def test_dict_arguments_are_accepted(self):
        client = _client([_tool_reply("shell", {"arg": "ls"})], [], action_transport="tools")
        decoded = _ask_action(client)
        assert decoded["action"] == "shell"
        assert decoded["arg"] == "ls"

    @pytest.mark.parametrize("arguments", ["", None])
    def test_empty_arguments_decode_as_bare_control_action(self, arguments):
        client = _client([_tool_reply("done", arguments)], [], action_transport="tools")
        decoded = _ask_action(client)
        assert decoded["action"] == "done"

    def test_text_reply_without_tool_call_is_malformed(self):
        client = _client([_text_reply("I finished everything")], [], action_transport="tools")
        with pytest.raises(json.JSONDecodeError, match="no tool call") as exc_info:
            _ask_action(client)
        assert exc_info.value.malformed_action is True
        assert "I finished" in exc_info.value.cleaned_text

    def test_multiple_tool_calls_are_rejected(self):
        extra = (
            {"id": "call_2", "type": "function", "function": {"name": "done", "arguments": "{}"}},
        )
        client = _client(
            [_tool_reply("shell", '{"arg": "ls"}', extra_calls=extra)],
            [],
            action_transport="tools",
        )
        with pytest.raises(json.JSONDecodeError, match="multiple tool calls"):
            _ask_action(client)

    def test_missing_function_name_is_malformed(self):
        client = _client([_tool_reply(None, "{}")], [], action_transport="tools")
        with pytest.raises(json.JSONDecodeError, match="missing a function name"):
            _ask_action(client)

    def test_non_object_arguments_are_malformed(self):
        client = _client([_tool_reply("shell", "[1, 2]")], [], action_transport="tools")
        with pytest.raises(json.JSONDecodeError, match="must be a JSON object"):
            _ask_action(client)

    def test_arguments_may_not_smuggle_an_action_field(self):
        client = _client(
            [_tool_reply("shell", '{"action": "done", "arg": "ls"}')],
            [],
            action_transport="tools",
        )
        with pytest.raises(json.JSONDecodeError, match="may not carry an action field"):
            _ask_action(client)

    def test_unknown_tool_name_is_typed_after_retries(self):
        client = _client([_tool_reply("compile", '{"arg": "x"}')], [], action_transport="tools")
        with pytest.raises(json.JSONDecodeError) as exc_info:
            _ask_action(client)
        assert exc_info.value.envelope_error == "unknown_action"

    def test_truncated_write_arguments_escalate_to_the_write_budget(self):
        truncated = '{"arg": "big.py", "content": "line one\\nline tw'
        bodies = []
        client = _client(
            [
                _tool_reply("write", truncated, finish_reason="length"),
                _tool_reply("write", '{"arg": "big.py", "content": "done"}'),
            ],
            bodies,
            action_transport="tools",
        )
        decoded = _ask_action(client, max_retries=1)
        assert decoded["action"] == "write"
        assert bodies[0]["max_tokens"] == 256
        assert bodies[1]["max_tokens"] == _settings().write_retry_tokens()

    def test_exhausted_truncated_arguments_raise_response_truncated(self):
        truncated = '{"arg": "big.py", "content": "line one\\nline tw'
        client = _client(
            [_tool_reply("write", truncated, finish_reason="length")],
            [],
            action_transport="tools",
        )
        with pytest.raises(json.JSONDecodeError, match="truncated") as exc_info:
            _ask_action(client)
        assert exc_info.value.response_truncated is True
        assert exc_info.value.malformed_action is True

    def test_incomplete_known_action_contract_is_typed(self):
        client = _client([_tool_reply("edit", '{"arg": "a.py"}')], [], action_transport="tools")
        with pytest.raises(json.JSONDecodeError) as exc_info:
            _ask_action(client)
        assert getattr(exc_info.value, "action_protocol_error", None) is not None


class TestConfigSurface:
    def _metadata(self, tmp_path, transport):
        config = RunConfig(llm=_settings(action_transport=transport))
        return askme._RunController("greet", str(tmp_path), config=config).config_metadata()

    def test_transport_is_recorded_and_hash_logged(self, tmp_path):
        tools_meta = self._metadata(tmp_path, "tools")
        json_meta = self._metadata(tmp_path, "json")
        assert tools_meta["action_transport"] == "tools"
        assert json_meta["action_transport"] == "json"
        assert tools_meta["config_hash"] != json_meta["config_hash"]

    def test_default_run_metadata_pins_the_json_arm(self, tmp_path):
        metadata = askme._RunController("greet", str(tmp_path)).config_metadata()
        assert metadata["action_transport"] == askme.ACTION_TRANSPORT == "json"

    def test_cli_flag_selects_the_transport_arm(self, tmp_path):
        expected = {"status": "complete", "state": {}, "log": []}
        with patch("askme.run_result", return_value=expected) as run_result:
            exit_code = askme._main(
                ["--working-dir", str(tmp_path), "--action-transport", "tools", "greet"]
            )
        assert exit_code == 0
        env_config = askme.RunConfig.from_env()
        run_result.assert_called_once_with(
            "greet",
            working_dir=str(tmp_path),
            config=dataclasses.replace(
                env_config,
                llm=dataclasses.replace(env_config.llm, action_transport="tools"),
                max_replans=askme.MAX_REPLANS,
                max_tasks=askme.MAX_TASKS,
                max_steps=askme.MAX_STEPS,
            ),
        )

    def test_transports_registry_is_closed(self):
        assert ACTION_TRANSPORTS == ("json", "tools")
        profile = get_capability_profile("generic-feature-scale-v1")
        assert _settings(capability_profile=profile).action_transport == "json"
