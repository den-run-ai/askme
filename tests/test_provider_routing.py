"""OpenRouter routing constraints reach HTTP independently of provider order."""

from unittest.mock import Mock

import pytest
from _test_support import mock_llm_response, mock_response_raw

import llm


@pytest.mark.parametrize(
    ("provider", "allow_fallbacks", "require_parameters", "expected_route"),
    [
        ("", True, False, None),
        ("", False, False, {"allow_fallbacks": False, "require_parameters": False}),
        ("", True, True, {"allow_fallbacks": True, "require_parameters": True}),
        ("", False, True, {"allow_fallbacks": False, "require_parameters": True}),
        (
            "synthetic-provider",
            True,
            False,
            {"order": ["synthetic-provider"], "allow_fallbacks": True, "require_parameters": False},
        ),
        (
            "synthetic-provider",
            False,
            False,
            {
                "order": ["synthetic-provider"],
                "allow_fallbacks": False,
                "require_parameters": False,
            },
        ),
        (
            "synthetic-provider",
            True,
            True,
            {"order": ["synthetic-provider"], "allow_fallbacks": True, "require_parameters": True},
        ),
        (
            "synthetic-provider",
            False,
            True,
            {"order": ["synthetic-provider"], "allow_fallbacks": False, "require_parameters": True},
        ),
    ],
)
@pytest.mark.parametrize("expect", [None, "plan", "action"])
def test_openrouter_outgoing_routing_matrix(
    provider, allow_fallbacks, require_parameters, expected_route, expect
):
    settings = llm.LLMSettings.from_env(
        {
            "LLM_BACKEND": "openrouter",
            "OPENROUTER_MODEL": "routing-synthetic",
            "OPENROUTER_PROVIDER": provider,
            "OPENROUTER_ALLOW_FALLBACKS": "1" if allow_fallbacks else "0",
            "OPENROUTER_REQUIRE_PARAMETERS": "1" if require_parameters else "0",
        }
    )
    reply = (
        {"action": "read", "arg": "fixture.txt"} if expect == "action" else {"tasks": ["inspect"]}
    )
    post = Mock(return_value=mock_llm_response(reply))
    messages = [{"role": "user", "content": "Synthetic routing check"}]
    result = llm.LLMClient(settings, post=post).ask(
        messages, max_tokens=128, expect=expect, max_retries=0, reasoning_policy="off"
    )

    assert result == reply
    expected_body = {
        "model": "routing-synthetic",
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 128,
        "reasoning": {"enabled": False},
    }
    if expected_route is not None:
        expected_body["provider"] = expected_route
    if expect == "action":
        expected_body.update(tools=llm._ACTION_TOOLS, tool_choice="auto")
    post.assert_called_once_with(
        llm.OPENROUTER_CHAT_API,
        json=expected_body,
        headers={"Content-Type": "application/json"},
        timeout=settings.timeout,
    )
    assert messages == [{"role": "user", "content": "Synthetic routing check"}]


@pytest.mark.parametrize("expect", ["plan", "action"])
def test_automatic_constraints_survive_parse_retries_and_strict_turn(expect):
    settings = llm.LLMSettings.from_env(
        {
            "LLM_BACKEND": "openrouter",
            "OPENROUTER_PROVIDER": " \t ",
            "OPENROUTER_ALLOW_FALLBACKS": "0",
            "OPENROUTER_REQUIRE_PARAMETERS": "1",
        }
    )
    reply = (
        {"action": "read", "arg": "fixture.txt"} if expect == "action" else {"tasks": ["inspect"]}
    )
    post = Mock(
        side_effect=[
            mock_response_raw("not JSON"),
            mock_response_raw("not JSON"),
            mock_llm_response(reply),
        ]
    )
    messages = [{"role": "user", "content": "Synthetic retry check"}]
    result = llm.LLMClient(settings, post=post, sleep=lambda seconds: None).ask(
        messages, max_tokens=128, expect=expect, max_retries=2, reasoning_policy="off"
    )

    assert result == reply
    bodies = [call.kwargs["json"] for call in post.call_args_list]
    assert len(bodies) == 3
    assert all(
        body.get("provider") == {"allow_fallbacks": False, "require_parameters": True}
        for body in bodies
    )
    assert [body["max_tokens"] for body in bodies] == [128, 128, 128]
    assert bodies[0]["messages"] == bodies[1]["messages"] == messages
    assert bodies[2]["messages"] == [
        *messages,
        {
            "role": "user",
            "content": llm._STRICT_TOOL_SUFFIX if expect == "action" else llm._STRICT_JSON_SUFFIX,
        },
    ]


@pytest.mark.parametrize("expect", ["plan", "action"])
def test_local_requests_do_not_receive_openrouter_constraints(expect):
    settings = llm.LLMSettings.from_env(
        {
            "LLM_BACKEND": "local",
            "OPENROUTER_PROVIDER": "synthetic-provider",
            "OPENROUTER_ALLOW_FALLBACKS": "0",
            "OPENROUTER_REQUIRE_PARAMETERS": "1",
        }
    )
    reply = (
        {"action": "read", "arg": "fixture.txt"} if expect == "action" else {"tasks": ["inspect"]}
    )
    post = Mock(return_value=mock_llm_response(reply))
    result = llm.LLMClient(settings, post=post).ask(
        [], max_tokens=128, expect=expect, max_retries=0, reasoning_policy="off"
    )
    assert result == reply
    assert post.call_count == 1
    assert "provider" not in post.call_args.kwargs["json"]
