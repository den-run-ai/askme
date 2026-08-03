"""Typed action-wire boundary regressions for issue #79."""

import json
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pytest

import askme
from actions import (
    READ_LIMIT_MAX,
    READ_POSITION_MAX,
    SHELL_TIMEOUT_MAX,
    ActionEnvelope,
    ActionProtocolError,
    DecodedAction,
    parse_action_envelope,
)
from askme import _decode_action_reply, _repair_json, _run_loop, execute

TYPE_VALUES = {
    "null": None,
    "bool": True,
    "int": 10,
    "float": 1.5,
    "string": "text",
    "list": ["text"],
    "object": {"key": "value"},
}


FIELD_CASES = {
    "action": ({"action": "done"}, {"string"}),
    "arg": ({"action": "shell", "arg": "echo ok"}, {"string"}),
    "timeout": (
        {"action": "shell", "arg": "echo ok", "timeout": 10},
        {"int"},
    ),
    "reasoning": ({"action": "done", "reasoning": "why"}, {"null", "string"}),
    "content": (
        {"action": "write", "arg": "f.txt", "content": "text"},
        {"string", "list", "object"},
    ),
    "append": (
        {"action": "write", "arg": "f.txt", "content": "text", "append": True},
        {"bool"},
    ),
    "find": (
        {"action": "edit", "arg": "f.txt", "find": "old", "replace": "new"},
        {"string"},
    ),
    "replace": (
        {"action": "edit", "arg": "f.txt", "find": "old", "replace": "new"},
        {"string"},
    ),
    "offset": ({"action": "read", "arg": "f.txt", "offset": 10}, {"int"}),
    "limit": ({"action": "read", "arg": "f.txt", "limit": 10}, {"int"}),
    "cursor": (
        {
            "action": "read",
            "arg": "f.txt",
            "cursor": 10,
            "limit": 10,
            "sha256": "abc123",
        },
        {"int"},
    ),
    "sha256": (
        {
            "action": "read",
            "arg": "f.txt",
            "cursor": 10,
            "limit": 10,
            "sha256": "abc123",
        },
        {"string"},
    ),
    "path": (
        {"action": "search", "arg": "needle", "path": "pkg"},
        {"string"},
    ),
}

INVALID_FIELD_VALUES = [
    (field, kind, value)
    for field, (_base, accepted) in FIELD_CASES.items()
    for kind, value in TYPE_VALUES.items()
    if kind not in accepted
]


def _candidate(field, base, kind, value):
    candidate = dict(base)
    if field == "action" and kind == "string":
        value = "done"
    candidate[field] = value
    return candidate


@pytest.mark.parametrize("field", FIELD_CASES)
@pytest.mark.parametrize("kind,value", TYPE_VALUES.items())
def test_every_action_field_has_one_typed_parser(field, kind, value):
    base, accepted = FIELD_CASES[field]
    candidate = _candidate(field, base, kind, value)
    before = json.loads(json.dumps(candidate))

    parsed = parse_action_envelope(candidate)

    assert (isinstance(parsed, ActionEnvelope)) is (kind in accepted)
    assert candidate == before, "the pure parser mutated its model object"
    if isinstance(parsed, ActionProtocolError):
        assert parsed.error_type in {"malformed_action", "unknown_action"}
        assert parsed.message


@pytest.mark.parametrize(
    "field,base,minimum,maximum",
    [
        ("timeout", {"action": "shell", "arg": "echo ok"}, 5, SHELL_TIMEOUT_MAX),
        ("offset", {"action": "read", "arg": "f.txt"}, 1, READ_POSITION_MAX),
        ("limit", {"action": "read", "arg": "f.txt"}, 1, READ_LIMIT_MAX),
        (
            "cursor",
            {"action": "read", "arg": "f.txt", "limit": 10, "sha256": "abc123"},
            0,
            READ_POSITION_MAX,
        ),
    ],
)
def test_integer_fields_enforce_inclusive_bounds_and_exclude_bools(field, base, minimum, maximum):
    for accepted in (minimum, maximum):
        assert isinstance(parse_action_envelope({**base, field: accepted}), ActionEnvelope)
    for rejected in (minimum - 1, maximum + 1, True):
        parsed = parse_action_envelope({**base, field: rejected})
        assert isinstance(parsed, ActionProtocolError)
        assert parsed.field == field


@pytest.mark.parametrize("field,kind,value", INVALID_FIELD_VALUES)
def test_dispatch_is_non_throwing_for_every_rejected_field_type(field, kind, value, tmp_path):
    base, _accepted = FIELD_CASES[field]
    candidate = _candidate(field, base, kind, value)

    result = execute(candidate, str(tmp_path))

    assert result["ok"] is False
    assert result["error_type"]
    assert list(tmp_path.iterdir()) == []


def test_parsed_action_is_immutable_and_detached_from_input():
    raw = {"action": "write", "arg": "f.json", "content": {"enabled": True}}
    parsed = parse_action_envelope(raw)
    assert isinstance(parsed, ActionEnvelope)
    raw["arg"] = "other.json"
    raw["content"]["enabled"] = False
    assert parsed["arg"] == "f.json"
    assert parsed["content"] == {"enabled": True}
    projected_content = parsed["content"]
    projected_content["enabled"] = False
    assert parsed["content"] == {"enabled": True}
    with pytest.raises(TypeError):
        parsed["arg"] = "changed"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        parsed._items = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    "action,field",
    [
        ({"action": "write", "arg": "f", "content": "x", "timeout": 10}, "timeout"),
        ({"action": "shell", "arg": "true", "append": False}, "append"),
        ({"action": "done", "content": "x"}, "content"),
        ({"action": "write", "arg": "f", "content": "x", "_target": "spoof"}, "_target"),
        (
            {"action": "write", "arg": "f", "content": "x", "content_truncated": True},
            "content_truncated",
        ),
    ],
)
def test_cross_action_and_reserved_fields_are_rejected(action, field):
    parsed = parse_action_envelope(action)
    assert isinstance(parsed, ActionProtocolError)
    assert parsed.field == field


def test_explicit_zero_byte_write_succeeds(tmp_path):
    result = execute({"action": "write", "arg": "empty.txt", "content": ""}, tmp_path)
    assert result == {"ok": True, "output": "Wrote empty.txt"}
    assert (tmp_path / "empty.txt").read_bytes() == b""


@pytest.mark.parametrize(
    "action",
    [
        {"action": "write", "arg": "f.txt", "content": "NEW", "append": "false"},
        {"action": "shell", "arg": "echo unsafe", "timeout": "oops"},
        {"action": "write", "arg": {"path": "f.txt"}, "content": "unsafe"},
        {"action": "edit", "arg": "f.txt", "find": "old", "replace": {}},
    ],
)
def test_known_crash_or_wrong_mutation_values_are_side_effect_free(action, tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("old")
    result = execute(action, tmp_path)
    assert result["ok"] is False
    assert target.read_text() == "old"


def test_missing_brace_repair_preserves_append_semantics(tmp_path):
    raw = '{"action":"write","arg":"f","content":"NEW","append":true'
    assert _repair_json(raw) == {
        "action": "write",
        "arg": "f",
        "content": "NEW",
        "append": True,
    }
    decoded, _text, repaired = _decode_action_reply(raw, "stop")
    assert repaired is True
    assert isinstance(decoded, DecodedAction)
    assert decoded["append"] is True
    target = tmp_path / "f"
    target.write_text("OLD")
    result = execute(decoded, tmp_path)
    assert result["ok"] is True
    assert target.read_text() == "OLDNEW"


@pytest.mark.parametrize(
    "raw",
    [
        '{"action":"write","arg":"f","content":"NEW","app',
        '{"action":"write","arg":"f","content":"NEW","append":',
        '{"action":"write","arg":"f","content":"NEW","append":"fal',
        '{"action":"shell","arg":"true","timeout":12',
        '{"action":"shell","arg":"true","time',
        '{"action":"read","arg":"f","offset":12',
        '{"action":"read","arg":"f","limit":12',
    ],
)
def test_repair_never_drops_a_partial_semantic_field(raw):
    assert _repair_json(raw) is None


def test_length_truncated_json_header_is_not_executable():
    raw = '{"action":"write","arg":"f","content":"NEW","append":true'
    with pytest.raises(json.JSONDecodeError):
        _decode_action_reply(raw, "length")


@pytest.mark.parametrize(
    "raw",
    [
        '{"action":"shell","arg":"true"}\n<<<CONTENT\nx\nCONTENT>>>',
        '{"action":"write","arg":"f","content":"header"}\n<<<CONTENT\nblock\nCONTENT>>>',
        '{"action":"write","arg":"f","content":"x","content_truncated":true}',
        '{"action":"write","arg":"f"}\n<<<CONTENT\nunclosed',
    ],
)
def test_sentinel_ambiguity_reserved_metadata_and_unclosed_stop_are_rejected(raw):
    with pytest.raises(json.JSONDecodeError):
        _decode_action_reply(raw, "stop")


def test_sentinel_payload_preserves_literal_reasoning_and_channel_tags():
    payload = "<think>literal</think>\n<|channel>literal<channel|>\nbody"
    raw = (
        '<think>actual reasoning</think>{"action":"write","arg":"f"}\n'
        f"<<<CONTENT\n{payload}\nCONTENT>>>"
    )
    decoded, _text, _repaired = _decode_action_reply(raw, "stop")
    assert isinstance(decoded, DecodedAction)
    assert decoded["content"] == payload


def test_partial_sentinel_transport_is_separate_but_compatibly_projected():
    raw = '{"action":"write","arg":"f"}\n<<<CONTENT\ncomplete\npartial'
    decoded, _text, _repaired = _decode_action_reply(raw, "length")
    assert isinstance(decoded, DecodedAction)
    assert "content_truncated" not in decoded.envelope
    assert decoded.transport.content_truncated is True
    assert decoded["content_truncated"] is True


@patch("askme.replan_task", return_value=askme.TaskReplanResult(None, "unknown"))
@patch("askme.ask_llm")
def test_controller_reuses_parser_and_rejects_invalid_append(mock_llm, _mock_replan, tmp_path):
    mock_llm.side_effect = [
        {"tasks": ["write f.txt"]},
        {"action": "write", "arg": "f.txt", "content": "unsafe", "append": "false"},
    ]
    result = _run_loop("write f.txt", str(tmp_path), max_replans=1, max_tasks=1, max_steps=2)
    assert result["status"] == "exhausted"
    assert not (tmp_path / "f.txt").exists()
    assert any("field 'append' must be a boolean" in error for error in result["state"]["errors"])
