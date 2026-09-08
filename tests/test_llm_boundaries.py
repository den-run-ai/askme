"""Native attempted-action metadata, with historical text-JSON compatibility."""

import json
from unittest.mock import Mock

import pytest
from _test_support import mock_http_response, mock_llm_response, mock_response_raw

import llm


def _native_reply(name, arguments, *, finish_reason="length"):
    return {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
                },
                "finish_reason": finish_reason,
            }
        ]
    }


def _retry(first, *, expect="action"):
    logs = []
    final = (
        mock_llm_response({"action": "done"})
        if expect == "action"
        else mock_response_raw('{"action":"done"}')
    )
    post = Mock(side_effect=[first, final])
    settings = llm.LLMSettings.from_env({"LLM_CAPABILITY_PROFILE": "legacy-e4b-m1-16k-v1"})
    result = llm.LLMClient(settings, post=post, log_sink=logs.append).ask(
        [], max_tokens=256, max_retries=1, reasoning_policy="off", expect=expect
    )
    assert result == {"action": "done"}
    return [call.kwargs["json"]["max_tokens"] for call in post.call_args_list], logs


def test_canonical_json_decoder_keeps_the_old_import_as_an_identity_alias():
    assert llm._decode_json_reply is llm._decode_action_reply
    assert llm._decode_json_reply.__name__ == "_decode_json_reply"
    assert llm._decode_json_reply('{"tasks":["greet"]}', "stop") == (
        {"tasks": ["greet"]},
        '{"tasks":["greet"]}',
        False,
    )
    decoded, cleaned, repaired = llm._decode_action_reply('{"action":"done",}', "stop")
    assert decoded == {"action": "done"}
    assert cleaned == '{"action":"done",}'
    assert repaired is True


@pytest.mark.parametrize(
    ("name", "retry_budget"),
    [
        ("read", 256),
        ("shell", 256),
        ("unknown", 256),
        (None, 256),
        ("", 256),
        ([], 256),
        ({"name": "write"}, 256),
        (" write ", 256),
        ('write", "extra": "', 256),
        ("write", 512),
        ("edit", 512),
    ],
)
def test_native_retry_uses_tool_name_not_action_text_inside_diagnostics(name, retry_budget):
    # This action-looking text is not the native function name. The old regex
    # incorrectly promoted its presence in a diagnostic to a write attempt.
    arguments = '{"action":"write","arg":"synthetic","content":"cut'
    budgets, logs = _retry(mock_http_response(json_body=_native_reply(name, arguments)))
    assert budgets == [256, retry_budget]
    assert any("write/edit payload budget" in message for message in logs) is (retry_budget == 512)


def test_no_tool_reply_cannot_request_write_budget_with_action_shaped_text():
    budgets, _ = _retry(mock_response_raw('{"action":"write","content":"cut'))
    assert budgets == [256, 256]


def test_ambiguous_multiple_tools_do_not_infer_an_action_from_joined_names():
    payload = _native_reply('write", "action":"edit', "{}")
    payload["choices"][0]["message"]["tool_calls"].append(
        {"function": {"name": "read", "arguments": "{}"}}
    )
    budgets, _ = _retry(mock_http_response(json_body=payload))
    assert budgets == [256, 256]


@pytest.mark.parametrize("name", ["write", "edit"])
@pytest.mark.parametrize(
    "arguments",
    [
        '{"arg":"synthetic","content":"cut',
        '\t { "arg" : "synthetic", "content" : "line\\n\\"quoted\\"\\\\cut',
        "[]",
        '{"action":"read"}',
    ],
)
def test_native_write_edit_retry_preserves_payload_budget_and_log(name, arguments):
    budgets, logs = _retry(mock_http_response(json_body=_native_reply(name, arguments)))
    assert budgets == [256, 512]
    assert "  write/edit payload budget -> 512" in logs
    diagnostic = f'{{"action": "{name}", "arguments": {arguments[:400]}}}'
    assert f"  [retry 1] JSON parse failed, raw: {diagnostic[:120]}" in logs


def test_native_retry_does_not_consult_the_compatibility_json_regex(monkeypatch):
    monkeypatch.setattr(
        llm, "_WRITE_ATTEMPT_RE", Mock(search=Mock(side_effect=AssertionError("text regex used")))
    )
    budgets, _ = _retry(mock_http_response(json_body=_native_reply("write", '{"content":"cut')))
    assert budgets == [256, 512]


@pytest.mark.parametrize("name", ["write", "edit"])
def test_missing_required_native_fields_keep_the_existing_non_escalating_contract(name):
    # Envelope/schema rejection is not the argument-decoding retry branch.
    budgets, _ = _retry(mock_http_response(json_body=_native_reply(name, "{}")))
    assert budgets == [256, 256]


@pytest.mark.parametrize("name", ["write", "edit", "unknown"])
def test_native_argument_error_adds_identity_without_changing_diagnostics(name):
    arguments = '{"arg":"synthetic","content":"cut'
    payload = _native_reply(name, arguments)
    with pytest.raises(json.JSONDecodeError) as info:
        llm._decode_tool_call_reply(payload, "length")
    error = info.value
    cleaned = f'{{"action": "{name}", "arguments": {arguments}}}'
    assert error.msg == "tool call arguments are not valid JSON (truncated)"
    assert error.doc == error.cleaned_text == cleaned
    assert error.pos == 0
    assert error.attempted_action == name


def test_native_exhaustion_preserves_typed_error_flags_and_never_salvages_content():
    post = Mock(
        return_value=mock_http_response(json_body=_native_reply("write", '{"content":"cut'))
    )
    client = llm.LLMClient(llm.LLMSettings.from_env({}), post=post)
    with pytest.raises(json.JSONDecodeError) as info:
        client.ask([], expect="action", max_retries=0)
    assert info.value.response_truncated is True
    assert info.value.malformed_action is True
    assert info.value.attempted_action == "write"
    assert post.call_count == 1


@pytest.mark.parametrize("expect", [None, "plan"])
def test_generic_json_path_preserves_its_historical_write_diagnostic_budget(expect):
    first = mock_response_raw('{"action":"write","arg":"synthetic","content":"cut')
    if expect is None:
        budgets, _ = _retry(first, expect=None)
    else:
        post = Mock(side_effect=[first, mock_response_raw('{"tasks":["greet"]}')])
        settings = llm.LLMSettings.from_env({"LLM_CAPABILITY_PROFILE": "legacy-e4b-m1-16k-v1"})
        result = llm.LLMClient(settings, post=post).ask(
            [], expect="plan", max_tokens=256, max_retries=1, reasoning_policy="off"
        )
        assert result == {"tasks": ["greet"]}
        budgets = [call.kwargs["json"]["max_tokens"] for call in post.call_args_list]
    assert budgets == [256, 512]


def test_raw_reply_still_bypasses_decoding_and_retry_metadata():
    payload = _native_reply("write", '{"content":"cut')
    payload["choices"][0]["message"]["content"] = "raw synthetic text"
    post = Mock(return_value=mock_http_response(json_body=payload))
    client = llm.LLMClient(llm.LLMSettings.from_env({}), post=post)
    assert client.ask([], expect="action", raw=True) == "raw synthetic text"
    assert post.call_count == 1
