"""Versioned PEG diagnostic collector, separate from the frozen August producer.

This is an offline-tested building block, not a registered live qualification.
It has no default server, credential lookup, HTTP implementation, or live CLI.
A future preregistered driver supplies the complete request and an exchange
callback accepting those exact bytes and returning (HTTP status, response bytes).
The caller owns request-shape/model/server pinning, bounded transport and consent.
Only synthetic callbacks are exercised by this change.

Each attempt uses a new directory. Complete request and response bodies are
retained before interpretation; headers, credentials and exception text are not.
Schema acceptance never implies action execution or an accepted task artifact.
Review and secret-scan captured bodies before publishing them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from actions import ACTION_SPECS
from llm import _decode_tool_call_reply


def _classify(raw: bytes, expected_tool: str) -> dict[str, Any]:
    result: dict[str, Any] = {"action_contract_valid": False}
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        return {**result, "failure": "response_not_json"}
    if not isinstance(payload, dict):
        return {**result, "failure": "response_shape"}
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return {**result, "failure": "response_shape"}
    message = choices[0].get("message")
    result["finish_reason"] = choices[0].get("finish_reason")
    if not isinstance(message, dict) or not isinstance(message.get("tool_calls"), list):
        return {**result, "failure": "tool_call_shape"}
    calls = message["tool_calls"]
    result["n_tool_calls"] = len(calls)
    if len(calls) != 1:
        return {**result, "failure": "tool_call_count"}
    if not isinstance(calls[0], dict) or not isinstance(calls[0].get("function"), dict):
        return {**result, "failure": "tool_call_shape"}
    result["tool_name"] = calls[0]["function"].get("name")
    if result["tool_name"] != expected_tool:
        return {**result, "failure": "unexpected_tool"}
    try:
        decoded, _, _ = _decode_tool_call_reply(payload, result["finish_reason"])
    except json.JSONDecodeError as exc:
        protocol_error = getattr(exc, "action_protocol_error", None)
        if protocol_error is not None:
            return {**result, "failure": "action_schema", "error_type": protocol_error.error_type}
        return {**result, "failure": "invalid_action_arguments"}
    # The expected known name was checked above; canonical decode already
    # validated this envelope. Reuse it rather than revalidating the schema.
    action = decoded.envelope.to_dict()
    content = action.get("content")
    return {
        **result,
        "action_contract_valid": True,
        "action": action,
        "literal_delimiter_count": content.count('<|"|>') if isinstance(content, str) else None,
    }


def capture_trial(
    request: dict[str, Any],
    expected_tool: str,
    *,
    exchange: Callable[[bytes], tuple[int, bytes]],
    output_dir: Path,
) -> dict[str, Any]:
    """Capture one response and validate its full single expected action.

    No retries, action dispatch, original-record updates or outcome scoring.
    The transport callback must return complete response-body bytes; a transport
    exception produces an explicit negative record without sensitive diagnostics.
    A schema-valid length-flagged action is not marked partial by the runtime;
    completeness and task acceptance remain untested, even when this check passes.
    """
    if expected_tool not in ACTION_SPECS:
        raise ValueError("expected_tool must name an AskMe action")
    raw_request = json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8")
    output_dir.mkdir()  # Exclusive: never append to or overwrite historical evidence.
    (output_dir / "request.json").write_bytes(raw_request)
    record: dict[str, Any] = {
        "collector_version": 2,
        "expected_tool": expected_tool,
        "request_sha256": hashlib.sha256(raw_request).hexdigest(),
        "request_bytes": len(raw_request),
        "action_contract_valid": False,
        "artifact_acceptance": "not_evaluated",
    }
    try:
        status, raw_response = exchange(raw_request)
    except Exception as exc:
        record.update(failure="transport_error", error_type=type(exc).__name__)
    else:
        # Persist bytes first: malformed and non-JSON replies are evidence too.
        (output_dir / "response.bin").write_bytes(raw_response)
        record.update(
            http_status=status,
            response_sha256=hashlib.sha256(raw_response).hexdigest(),
            response_bytes=len(raw_response),
        )
        record.update(
            _classify(raw_response, expected_tool) if status == 200 else {"failure": "http_error"}
        )
    (output_dir / "record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record
