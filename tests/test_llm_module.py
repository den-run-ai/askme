"""Physical client-module contracts; legacy askme patch tests stay unchanged."""

import dataclasses
import inspect
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import get_type_hints
from unittest.mock import Mock

import pytest
from _test_support import mock_http_response, mock_llm_response, mock_response_raw

import actions
import askme
import llm


def test_standalone_import_does_not_load_dotenv_or_facade_or_send_http(tmp_path):
    for module in (llm, actions):
        shutil.copy(Path(module.__file__), tmp_path)
    (tmp_path / ".env").write_text("ASKME_IMPORT_SENTINEL=loaded\n")
    env = {key: value for key, value in os.environ.items() if key != "ASKME_IMPORT_SENTINEL"}
    # Importing llm must not interpret CLI/environment policy settings either.
    env["AGENT_REASONING_POLICY"] = "not-a-policy"
    env["OPENROUTER_REASONING_EFFORT"] = "not-an-effort"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, sys, requests\n"
            "def blocked(*args, **kwargs):\n"
            "    raise AssertionError('Unexpected HTTP during import')\n"
            "requests.sessions.Session.send = blocked\n"
            "import llm\n"
            "assert 'askme' not in sys.modules\n"
            "assert 'ASKME_IMPORT_SENTINEL' not in os.environ\n"
            "assert llm.LLMSettings.from_env({}).model == 'local-model'\n"
            "print('independent import')\n",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert result.stdout == "independent import\n"
    assert result.stderr == ""


@pytest.mark.parametrize(
    "name",
    [
        "CapabilityProfile",
        "LLMTransportError",
        "PlanResponse",
        "TaskReplanResponse",
        "ValidationResponse",
        "_repair_json",
        "_decode_action_reply",
        "_decode_tool_call_reply",
        "_action_envelope_error",
        "_ACTION_TOOLS",
    ],
)
def test_facade_reexports_one_canonical_codec_and_record(name):
    assert getattr(askme, name) is getattr(llm, name)
    assert llm.ACTION_SPECS is askme.ACTION_SPECS is actions.ACTION_SPECS


def test_settings_facade_preserves_fields_constructor_and_explicit_env_defaults(monkeypatch):
    assert inspect.signature(askme.LLMSettings) == inspect.signature(llm.LLMSettings)
    assert dataclasses.fields(askme.LLMSettings) == dataclasses.fields(llm.LLMSettings)
    monkeypatch.setattr(askme, "LLM_TIMEOUT", 19)
    monkeypatch.setattr(askme, "LLM_TIMEOUT_REPLAN", 23)
    monkeypatch.setattr(askme, "MAX_LLM_RETRIES", 4)
    monkeypatch.setattr(askme, "OPENROUTER_CHAT_API", "https://router.invalid/chat")
    monkeypatch.setattr(askme, "_OPENROUTER_DEFAULT_MODEL", "custom-default")
    settings = askme.LLMSettings.from_env({"LLM_BACKEND": "openrouter"})
    assert (settings.timeout, settings.replan_timeout, settings.max_retries) == (19, 23, 4)
    assert (settings.api, settings.model) == ("https://router.invalid/chat", "custom-default")
    assert dataclasses.replace(settings, model="other").model == "other"
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.model = "mutable"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(settings, "unregistered_field", "mutable")
    # An explicit environment mapping never inherits the facade's patched defaults.
    standalone = llm.LLMSettings.from_env({"LLM_BACKEND": "openrouter"})
    assert standalone.api == llm.OPENROUTER_CHAT_API
    assert standalone.timeout == 120


def test_direct_clients_use_only_their_settings_and_sinks(monkeypatch, capsys):
    monkeypatch.setattr(askme, "MODEL", "unrelated-facade-model")
    monkeypatch.setattr(askme, "log", Mock(side_effect=AssertionError("facade log used")))
    monkeypatch.setattr(askme, "_run_log", Mock(side_effect=AssertionError("facade event used")))
    calls, events = [], []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return mock_llm_response(
            {"action": "write", "arg": "hello.txt", "content": "hi\n"},
            finish_reason="tool_calls",
            usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        )

    local = llm.LLMSettings.from_env({"LLM_MODEL": "local-synthetic"})
    remote = llm.LLMSettings.from_env(
        {
            "LLM_BACKEND": "openrouter",
            "OPENROUTER_MODEL": "hosted-synthetic",
            "OPENROUTER_PROVIDER": "synthetic-provider",
            "OPENROUTER_API_KEY": "synthetic-key",
            "OPENROUTER_ALLOW_FALLBACKS": "0",
            "OPENROUTER_REQUIRE_PARAMETERS": "1",
        }
    )
    for settings in (local, remote):
        client = llm.LLMClient(settings, post=post, event_sink=events.append)
        action = client.ask([{"role": "user", "content": "synthetic"}], expect="action")
        assert action == {"action": "write", "arg": "hello.txt", "content": "hi\n"}
    assert [call[1]["json"]["model"] for call in calls] == [local.model, remote.model]
    assert "Authorization" not in calls[0][1]["headers"]
    assert calls[1][1]["headers"]["Authorization"] == "Bearer synthetic-key"
    assert calls[1][1]["json"]["provider"] == {
        "order": ["synthetic-provider"],
        "allow_fallbacks": False,
        "require_parameters": True,
    }
    assert [event["requested_model"] for event in events if event["event"] == "tokens"] == [
        local.model,
        remote.model,
    ]
    assert capsys.readouterr().out == ""


def test_direct_client_keeps_write_retry_budget_and_strict_final_contract():
    truncated = {
        "choices": [
            {
                "finish_reason": "length",
                "message": {
                    "tool_calls": [
                        {"function": {"name": "write", "arguments": '{"content":"partial'}}
                    ]
                },
            }
        ]
    }
    post = Mock(
        side_effect=[
            mock_http_response(json_body=truncated),
            mock_http_response(json_body=truncated),
            mock_llm_response({"action": "write", "arg": "hello.txt", "content": "hi\n"}),
        ]
    )
    settings = llm.LLMSettings.from_env({"LLM_CAPABILITY_PROFILE": "legacy-e4b-m1-16k-v1"})
    messages = [{"role": "system", "content": "synthetic"}]
    reply = llm.LLMClient(settings, post=post).ask(
        messages, expect="action", reasoning_policy="off"
    )
    assert reply["content"] == "hi\n"
    bodies = [call.kwargs["json"] for call in post.call_args_list]
    assert [body["max_tokens"] for body in bodies] == [256, 512, 512]
    assert all(body["tools"] is llm._ACTION_TOOLS for body in bodies)
    assert bodies[0]["messages"] == bodies[1]["messages"] == messages
    assert bodies[2]["messages"] == [
        *messages,
        {"role": "user", "content": llm._STRICT_TOOL_SUFFIX},
    ]
    assert messages == [{"role": "system", "content": "synthetic"}]


def test_direct_client_raw_reasoning_fallback_bypasses_json_decode():
    response = mock_http_response(
        json_body={
            "choices": [{"message": {"content": "", "reasoning": {"content": "plain fallback"}}}]
        }
    )
    client = llm.LLMClient(llm.LLMSettings.from_env({}), post=Mock(return_value=response))
    assert client.ask([], raw=True) == "plain fallback"


def test_direct_transport_errors_keep_the_facade_exception_identity():
    post = Mock(return_value=mock_http_response(status_code=401, text="synthetic failure"))
    client = llm.LLMClient(llm.LLMSettings.from_env({}), post=post)
    with pytest.raises(askme.LLMTransportError, match="HTTP 401: synthetic failure"):
        client.ask([])
    assert post.call_count == 1


def test_facade_client_snapshots_settings_and_sinks_but_resolves_post_late(monkeypatch):
    logs, events, sleeps = [], [], []
    monkeypatch.setattr(askme, "MODEL", "constructor-model")
    monkeypatch.setattr(askme, "log", logs.append)
    monkeypatch.setattr(askme, "_run_log", events.append)
    monkeypatch.setattr(askme.time, "sleep", sleeps.append)
    client = askme.LLMClient()
    post = Mock(
        side_effect=[
            mock_http_response(status_code=500),
            mock_response_raw('{"tasks":["synthetic task"]}', finish_reason="stop"),
        ]
    )
    monkeypatch.setattr(askme, "MODEL", "later-model")
    monkeypatch.setattr(askme, "requests", SimpleNamespace(post=post))
    monkeypatch.setattr(askme, "log", Mock(side_effect=AssertionError("late log")))
    monkeypatch.setattr(askme, "_run_log", Mock(side_effect=AssertionError("late event")))
    assert client.ask([], expect="plan") == {"tasks": ["synthetic task"]}
    assert all(call.kwargs["json"]["model"] == "constructor-model" for call in post.call_args_list)
    assert sleeps == [1]
    assert any("HTTP 500" in message for message in logs)
    assert [event["event"] for event in events] == [
        "reasoning_decision",
        "reasoning_decision",
        "tokens",
    ]


def test_facade_schema_keeps_late_planner_limit_without_duplicating_contract(monkeypatch):
    monkeypatch.setattr(askme, "MAX_TASKS", 1)
    reply = {"tasks": ["first task", None]}
    assert askme.RESPONSE_SCHEMAS["plan"](reply, None)
    assert not llm.RESPONSE_SCHEMAS["plan"](reply, None)
    assert not askme.RESPONSE_SCHEMAS["plan"](reply, {"max_tasks": 2})
    client = askme.LLMClient(post=Mock(return_value=mock_response_raw(json.dumps(reply))))
    assert client.ask([], expect="plan") == reply


def test_existing_facade_client_uses_rebound_response_schema_registry(monkeypatch):
    reply = {"synthetic": "accepted"}
    post = Mock(return_value=mock_response_raw(json.dumps(reply)))
    client = askme.LLMClient(post=post)
    monkeypatch.setattr(
        askme,
        "RESPONSE_SCHEMAS",
        {"synthetic": lambda obj, context: obj == reply and context == {"version": 2}},
    )
    assert client.ask([], expect="synthetic", expect_context={"version": 2}) == reply

    monkeypatch.setattr(askme, "RESPONSE_SCHEMAS", {"synthetic": lambda obj, context: False})
    with pytest.raises(json.JSONDecodeError, match="synthetic response schema") as error:
        client.ask([], expect="synthetic", max_retries=0)
    assert error.value.malformed_action is True
    assert post.call_count == 2

    monkeypatch.setattr(askme, "RESPONSE_SCHEMAS", {})
    with pytest.raises(ValueError, match="expect must be one of"):
        client.ask([], expect="synthetic")
    assert post.call_count == 2  # Removed schemas fail before transport.


def test_facade_uses_latest_schema_when_a_response_arrives(monkeypatch):
    monkeypatch.setattr(askme, "RESPONSE_SCHEMAS", {"synthetic": lambda obj, context: True})

    def post(*args, **kwargs):
        monkeypatch.setattr(askme, "RESPONSE_SCHEMAS", {"synthetic": lambda obj, context: False})
        return mock_response_raw('{"synthetic": "now rejected"}')

    client = askme.LLMClient(post=post)
    with pytest.raises(json.JSONDecodeError, match="synthetic response schema"):
        client.ask([], expect="synthetic", max_retries=0)


def test_explicit_standalone_schema_registries_remain_independent(monkeypatch):
    posts = [
        Mock(
            side_effect=[mock_response_raw('{"value":"one"}'), mock_response_raw('{"value":"two"}')]
        ),
        Mock(return_value=mock_response_raw('{"value":"two"}')),
    ]
    settings = llm.LLMSettings.from_env({})
    clients = [
        llm.LLMClient(
            settings,
            post=post,
            response_schemas={
                "synthetic": lambda obj, context, value=value: obj == {"value": value}
            },
        )
        for value, post in zip(("one", "two"), posts)
    ]
    monkeypatch.setattr(askme, "RESPONSE_SCHEMAS", {})
    monkeypatch.setattr(llm, "RESPONSE_SCHEMAS", {})
    assert clients[0].ask([], expect="synthetic") == {"value": "one"}
    assert clients[1].ask([], expect="synthetic") == {"value": "two"}
    with pytest.raises(json.JSONDecodeError, match="synthetic response schema"):
        clients[0].ask([], expect="synthetic", max_retries=0)
    assert [post.call_count for post in posts] == [2, 1]


def test_standalone_schema_configuration_rejects_ambiguous_sources():
    with pytest.raises(ValueError, match="not both"):
        llm.LLMClient(
            llm.LLMSettings.from_env({}),
            response_schemas={},
            response_schemas_provider=lambda: {},
        )


def test_standalone_settings_compose_with_public_run_api(tmp_path):
    settings = llm.LLMSettings.from_env({"LLM_MODEL": "standalone-config"})
    assert get_type_hints(askme.RunConfig)["llm"] == llm.LLMSettings | None
    assert askme.REASONING_POLICIES is llm.REASONING_POLICIES
    post = Mock(
        side_effect=[
            mock_llm_response({"tasks": ["greet"]}),
            mock_llm_response({"action": "done"}),
        ]
    )
    client = llm.LLMClient(settings, post=post)
    result = askme.run_result(
        "greet",
        working_dir=str(tmp_path),
        config=askme.RunConfig(llm=settings, max_replans=1, final_validate="0"),
        dependencies=askme.RunDependencies(
            llm_client=client, log_sink=lambda _msg: None, event_sink=lambda _event: None
        ),
    )
    assert result["status"] == "complete"
    assert result["config"]["model"] == "standalone-config"
    assert post.call_count == 2
