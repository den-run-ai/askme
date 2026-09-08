"""Offline qualification of the replacement collector; no live probe runs."""

import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import peg_probe_v2 as probe
import pytest


def _response(name="write", arguments=None, *, calls=None, finish_reason="tool_calls"):
    if arguments is None:
        arguments = {"arg": "src/example.py", "content": 'print("complete")\n'}
    return json.dumps(
        {
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {
                        "content": None,
                        "tool_calls": calls
                        if calls is not None
                        else [{"function": {"name": name, "arguments": arguments}}],
                    },
                }
            ]
        },
        ensure_ascii=False,
    ).encode()


@pytest.mark.parametrize(
    "raw",
    [
        _response(calls=[]),
        _response(calls=[{"function": {"name": "write"}}, {"function": {"name": "write"}}]),
        _response(name="read", arguments={"arg": "src/example.py"}),
        _response(arguments="[]"),
        _response(arguments='{"arg":"src/example.py","content":"cut'),
        _response(arguments={"arg": "src/example.py"}),
        _response(arguments={"arg": "src/example.py", "content": 12}),
        _response(arguments={"arg": "src/example.py", "content": "x", "extra": True}),
        _response(arguments={"action": "read", "arg": "src/example.py", "content": "x"}),
        b'{"choices":[{"message":{"tool_calls":"not a list"}}]}',
        b'{"choices":[{"message":{"tool_calls":[null]}}]}',
        b'{"choices":[{"message":{"tool_calls":[{"function":null}]}}]}',
        b'{"choices":[null]}',
        b'{"choices":[]}',
        b"[]",
        b"not JSON",
    ],
)
def test_invalid_or_wrong_call_never_passes_and_full_bytes_are_retained(tmp_path, raw):
    exchange = Mock(return_value=(200, raw))
    destination = tmp_path / "trial"
    result = probe.capture_trial(
        {"model": "synthetic", "messages": []}, "write", exchange=exchange, output_dir=destination
    )
    assert result["action_contract_valid"] is False
    assert (destination / "response.bin").read_bytes() == raw
    assert result["response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert exchange.call_count == 1


@pytest.mark.parametrize("finish_reason", ["tool_calls", "length"])
def test_schema_valid_reply_keeps_complete_content_without_claiming_artifact_acceptance(
    tmp_path, finish_reason
):
    content = ('雪 <|"|> \\ "quoted"\n' * 300) + "EOF_SENTINEL\n"
    raw = _response(arguments={"arg": "a.txt", "content": content}, finish_reason=finish_reason)
    exchange = Mock(return_value=(200, raw))
    destination = tmp_path / "trial"
    request = {"model": "synthetic", "messages": [{"role": "user", "content": "write"}]}
    result = probe.capture_trial(request, "write", exchange=exchange, output_dir=destination)
    assert result["action_contract_valid"] is True
    assert result["action"]["content"] == content
    assert result["literal_delimiter_count"] == 300
    assert result["finish_reason"] == finish_reason
    assert result["artifact_acceptance"] == "not_evaluated"
    assert (destination / "response.bin").read_bytes() == raw
    sent = exchange.call_args.args[0]
    assert (destination / "request.json").read_bytes() == sent
    assert json.loads(sent) == request
    assert json.loads((destination / "record.json").read_bytes()) == result


def test_http_failure_cannot_pass_and_keeps_non_utf8_body(tmp_path):
    raw = b"\xffupstream failure\x00"
    result = probe.capture_trial(
        {}, "read", exchange=lambda _: (503, raw), output_dir=tmp_path / "trial"
    )
    assert result["failure"] == "http_error"
    assert result["action_contract_valid"] is False
    assert (tmp_path / "trial" / "response.bin").read_bytes() == raw


@pytest.mark.parametrize("content", [{}, {"x": '<|"|>'}, ['<|"|>', {"x": 1}]])
def test_valid_structured_write_content_is_retained_with_inapplicable_literal_count(
    tmp_path, content
):
    raw = _response(arguments={"arg": "data.json", "content": content})
    destination = tmp_path / "trial"
    result = probe.capture_trial({}, "write", exchange=lambda _: (200, raw), output_dir=destination)
    assert result["action_contract_valid"] is True
    assert result["action"]["content"] == content
    assert result["literal_delimiter_count"] is None
    assert (destination / "response.bin").read_bytes() == raw
    assert json.loads((destination / "record.json").read_bytes()) == result


def test_existing_destination_refuses_before_transport_and_preserves_originals(tmp_path):
    destination = tmp_path / "original"
    destination.mkdir()
    original = destination / "record.json"
    original.write_bytes(b"historical")
    exchange = Mock()
    with pytest.raises(FileExistsError):
        probe.capture_trial({}, "read", exchange=exchange, output_dir=destination)
    exchange.assert_not_called()
    assert original.read_bytes() == b"historical"


def test_transport_error_is_recorded_without_exception_text(tmp_path):
    exchange = Mock(side_effect=OSError("potentially sensitive transport diagnostic"))
    result = probe.capture_trial({}, "read", exchange=exchange, output_dir=tmp_path / "trial")
    assert result["failure"] == "transport_error"
    assert result["error_type"] == "OSError"
    assert "sensitive" not in (tmp_path / "trial" / "record.json").read_text()
    assert not (tmp_path / "trial" / "response.bin").exists()


def test_unknown_expected_tool_refuses_before_transport_or_files(tmp_path):
    exchange = Mock()
    with pytest.raises(ValueError, match="expected_tool"):
        probe.capture_trial({}, "not-an-action", exchange=exchange, output_dir=tmp_path / "trial")
    exchange.assert_not_called()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("arguments", "failure"),
    [
        ('{"content":"cut', "invalid_action_arguments"),
        ("[]", "invalid_action_arguments"),
        ({"arg": "a.py"}, "action_schema"),
        ({"arg": "a.py", "content": 12}, "action_schema"),
        ({"arg": "a.py", "content": "x", "extra": True}, "action_schema"),
    ],
)
def test_schema_rejection_stays_distinct_from_malformed_argument_json(tmp_path, arguments, failure):
    raw = _response(arguments=arguments)
    result = probe.capture_trial(
        {}, "write", exchange=lambda _: (200, raw), output_dir=tmp_path / "trial"
    )
    assert result["failure"] == failure
    assert result["action_contract_valid"] is False
    if failure == "action_schema":
        assert result["error_type"] == "malformed_action"
    else:
        assert "error_type" not in result


def test_frozen_first_call_verdict_is_not_reused_by_the_new_collector(tmp_path, monkeypatch):
    source = Path(__file__).parent / "bench_records/2026-08-29-peg-probe/peg_probe.py"
    spec = importlib.util.spec_from_file_location("frozen_peg_probe", source)
    assert spec is not None and spec.loader is not None
    frozen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frozen)
    raw = _response(
        calls=[
            {"function": {"name": "read", "arguments": '{"arg":"a.py"}'}},
            {"function": {"name": "read", "arguments": '{"arg":"b.py"}'}},
        ]
    )
    response = Mock(status_code=200, text=raw.decode(), json=lambda: json.loads(raw))
    post = Mock(return_value=response)
    monkeypatch.setattr(frozen.requests, "post", post)
    old = frozen.one_trial("A_short_args", frozen.ARMS["A_short_args"], 1)
    assert old["ok"] is True  # The historical counter's reproduced limitation.
    new = probe.capture_trial(
        {}, "read", exchange=lambda _: (200, raw), output_dir=tmp_path / "new-trial"
    )
    assert new["action_contract_valid"] is False
    assert new["failure"] == "tool_call_count"
    assert post.call_count == 1  # Synthetic HTTP only; never contact the server.
