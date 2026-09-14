#!/usr/bin/env python3
"""Host-only, fail-closed OpenRouter budget/route proxy for the four-cell canary.

Run with --manifest protocol.json --ledger /host/artifacts/proxy --port 8787.
OPENROUTER_API_KEY stays on the host; clients use PI_PROXY_SECRET + ':' + cell id
as their dummy bearer. Only metadata is retained. Reservations are durable before
network I/O. Dollar reservations are NEVER refunded, including disconnects and
unpriced errors. Unused output-token allowance is released only after a valid
route/cost audit supplies authoritative completed-token counts.
Provider price ceilings, text-only input, a conservative UTF-8-byte prompt bound,
and a capped completion jointly bound each reservation. An audit failure stops
that cell; a restart reloads the same reservations and immutable configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from http.client import HTTPException, HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote

MILLION = Decimal(1_000_000)
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
METADATA_BODY_BYTES = 1_000_000
METADATA_POLL_SECONDS = 60
METADATA_MAX_ATTEMPTS = 12
METADATA_RETRY_STATUSES = {404, 408, 425, 429, 500, 502, 503, 504, 524, 529}
METADATA_STAGES = {
    "complete",
    "http_status",
    "body_limit",
    "json_parse",
    "data_shape",
    "transport",
    "deadline",
    "poll_limit",
}


class MetadataAuditError(ValueError):
    """Fixed diagnostics only: never carry a response body or exception text."""

    def __init__(self, stage: str, http_status: int | None = None, *, retryable=False):
        super().__init__("generation metadata audit failed")
        self.stage = stage if stage in METADATA_STAGES else "transport"
        self.http_status = http_status
        self.retryable = retryable


def safe_fetch_diagnostics(records: object) -> list[dict]:
    """Copy only the tiny fixed telemetry schema into a retained ledger."""
    result = []
    if not isinstance(records, list):
        return result
    for record in records[: METADATA_MAX_ATTEMPTS + 1]:
        if not isinstance(record, dict) or record.get("stage") not in METADATA_STAGES:
            continue
        attempt, elapsed, status = (
            record.get("attempt"),
            record.get("elapsed_ms"),
            record.get("http_status"),
        )
        if type(attempt) is not int or not 0 <= attempt <= METADATA_MAX_ATTEMPTS:
            continue
        if type(elapsed) is not int or elapsed < 0:
            continue
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            continue
        result.append(
            {
                "attempt": attempt,
                "stage": record["stage"],
                "http_status": status,
                "elapsed_ms": elapsed,
            }
        )
    return result


class Rejected(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def money(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid monetary amount") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("monetary amount must be finite and nonnegative")
    return result


@dataclass(frozen=True)
class Cell:
    id: str
    model: str
    provider: str
    input_price_per_million: str
    output_price_per_million: str
    served_model: str = ""
    provider_display: str = ""
    quantization: str = ""


@dataclass(frozen=True)
class Config:
    cells: tuple[Cell, ...]
    global_budget_usd: str = "8"
    cell_budget_usd: str = "2"
    max_calls_per_cell: int = 60
    max_generated_tokens_per_cell: int = 131072
    max_tokens: int = 8192
    max_body_bytes: int = 1_000_000
    temperature: float = 0
    reasoning: dict | None = None

    @classmethod
    def from_manifest(cls, manifest: dict) -> Config:
        cell_keys = set(Cell.__dataclass_fields__)
        cells = tuple(
            Cell(
                **{
                    **{k: v for k, v in c.items() if k in cell_keys},
                    "provider_display": c.get("provider_display", c.get("served_provider", "")),
                }
            )
            for c in manifest["cells"]
        )
        settings = dict(manifest.get("proxy", {}))
        settings.pop("budget_policy", None)  # Human-readable protocol explanation.
        result = cls(cells=cells, **settings)
        result.validate()
        return result

    def validate(self) -> None:
        if not 0 < money(self.global_budget_usd) <= 8:
            raise ValueError("global budget must be in (0, 8]")
        if not 0 < money(self.cell_budget_usd) <= 2:
            raise ValueError("cell budget must be in (0, 2]")
        for value, maximum in (
            (self.max_calls_per_cell, 60),
            (self.max_generated_tokens_per_cell, 131072),
            (self.max_tokens, 8192),
            (self.max_body_bytes, 1_000_000),
        ):
            if type(value) is not int or not 0 < value <= maximum:
                raise ValueError("invalid hard limit")
        if (
            not self.cells
            or len(self.cells) > 4
            or len({c.id for c in self.cells}) != len(self.cells)
        ):
            raise ValueError("one to four unique cells required")
        for cell in self.cells:
            if not all((cell.id, cell.model, cell.provider)) or ":" in cell.id:
                raise ValueError("invalid cell identity")
            if (
                money(cell.input_price_per_million) <= 0
                or money(cell.output_price_per_million) <= 0
            ):
                raise ValueError("positive text-token price ceilings required")
        if self.temperature != 0:
            raise ValueError("comparison temperature must be zero")
        if self.reasoning is not None and not isinstance(self.reasoning, dict):
            raise ValueError("reasoning must be an object")


class Telemetry:
    """Deduplicate SSE terminal/usage frames without retaining model text."""

    def __init__(self, streaming: bool):
        self.streaming = streaming
        self.pending = b""
        self.ids: set[str] = set()
        self.models: set[str] = set()
        self.providers: set[str] = set()
        self.finish: dict[str, str] = {}
        self.usage: dict[str, int | str] = {}
        self.done = False
        self.invalid = False

    def event(self, event: dict) -> None:
        for key, values in (("id", self.ids), ("model", self.models), ("provider", self.providers)):
            value = event.get(key)
            if isinstance(value, str):
                values.add(value)
        for choice in event.get("choices", []):
            if not isinstance(choice, dict):
                self.invalid = True
                continue
            reason = choice.get("finish_reason")
            if reason is not None:
                index = str(choice.get("index", 0))
                if index in self.finish and self.finish[index] != reason:
                    self.invalid = True
                self.finish[index] = str(reason)
        usage = event.get("usage")
        if isinstance(usage, dict):
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = usage.get(key)
                if type(value) is int and value >= 0:
                    self.usage[key] = max(int(self.usage.get(key, 0)), value)
            cost = usage.get("cost")
            if cost is not None:
                try:
                    self.usage["cost"] = str(max(money(self.usage.get("cost", 0)), money(cost)))
                except ValueError:
                    self.invalid = True
        if event.get("error"):
            self.invalid = True

    def line(self, raw: bytes) -> None:
        if not raw.startswith(b"data:"):
            return
        raw = raw[5:].strip()
        if raw == b"[DONE]":
            self.done = True
            return
        try:
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise ValueError("event must be an object")
            self.event(event)
        except (ValueError, TypeError):
            self.invalid = True

    def feed(self, chunk: bytes) -> None:
        self.pending += chunk
        if self.streaming:
            while b"\n" in self.pending:
                line, self.pending = self.pending.split(b"\n", 1)
                self.line(line.rstrip(b"\r"))

    def complete(self) -> dict:
        if self.streaming:
            if self.pending:
                self.line(self.pending)
        else:
            try:
                event = json.loads(self.pending)
                if not isinstance(event, dict):
                    raise ValueError("response must be an object")
                self.event(event)
                self.done = True
            except (ValueError, TypeError):
                self.invalid = True
        return {
            "generation_ids": sorted(self.ids),
            "served_models": sorted(self.models),
            "response_providers": sorted(self.providers),
            "finish_reasons": self.finish,
            "usage": self.usage,
            "stream_complete": self.done,
            "telemetry_valid": not self.invalid,
        }


@dataclass
class Response:
    status: int
    content_type: str
    chunks: Iterable[bytes]
    close: Callable[[], None]


class OpenRouter:
    def __init__(self, key: str):
        self.key = key
        self.last_generation_diagnostics: list[dict] = []

    def completion(self, payload: bytes) -> Response:
        conn = HTTPSConnection("openrouter.ai", timeout=900)
        try:
            conn.request(
                "POST",
                "/api/v1/chat/completions",
                body=payload,
                headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            )
            response = conn.getresponse()
        except Exception:
            conn.close()
            raise

        def chunks():
            while chunk := response.read1(8192):
                yield chunk

        return Response(
            response.status,
            response.getheader("Content-Type", "application/json"),
            chunks(),
            conn.close,
        )

    def generation(self, generation_id: str) -> dict:
        # Metadata indexing can lag successful inference. Poll read-only GETs
        # over a real sixty-second budget, rather than exhausting three quick
        # 404s in 1.5 seconds. No completion is retried or reservation released.
        started = time.monotonic()
        deadline = started + METADATA_POLL_SECONDS
        self.last_generation_diagnostics = []
        last_status = None
        attempt = 0

        def record(stage, status):
            self.last_generation_diagnostics.append(
                {
                    "attempt": attempt,
                    "stage": stage,
                    "http_status": status,
                    "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
                }
            )

        while attempt < METADATA_MAX_ATTEMPTS:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            attempt += 1
            try:
                data = self._generation_once(generation_id, min(10, remaining))
            except MetadataAuditError as exc:
                last_status = exc.http_status
                record(exc.stage, exc.http_status)
                if not exc.retryable:
                    raise
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(2 ** min(attempt, 3), remaining))
            else:
                record("complete", 200)
                return data
        stage = "deadline" if time.monotonic() >= deadline else "poll_limit"
        record(stage, last_status)
        raise MetadataAuditError(stage, last_status)

    def _generation_once(self, generation_id: str, timeout: float) -> dict:
        conn = HTTPSConnection("openrouter.ai", timeout=timeout)
        deadline = time.monotonic() + timeout
        status = None

        def remaining_timeout():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            if conn.sock is not None:
                conn.sock.settimeout(remaining)

        try:
            conn.request(
                "GET",
                "/api/v1/generation?id=" + quote(generation_id, safe=""),
                headers={
                    "Authorization": f"Bearer {self.key}",
                    "Accept": "application/json",
                    "User-Agent": "askme-pi-comparison/2 (metadata-audit)",
                },
            )
            remaining_timeout()
            response = conn.getresponse()
            status = response.status
            if status != 200:
                raise MetadataAuditError(
                    "http_status",
                    status,
                    retryable=status in METADATA_RETRY_STATUSES,
                )
            body = bytearray()
            while True:
                remaining_timeout()
                chunk = response.read1(min(65536, METADATA_BODY_BYTES + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > METADATA_BODY_BYTES:
                    raise MetadataAuditError("body_limit", status)
            try:
                envelope = json.loads(body)
            except (ValueError, UnicodeError):
                raise MetadataAuditError("json_parse", status) from None
            if not isinstance(envelope, dict) or not isinstance(envelope.get("data"), dict):
                raise MetadataAuditError("data_shape", status)
            return envelope["data"]
        except (OSError, HTTPException):
            raise MetadataAuditError("transport", status, retryable=True) from None
        finally:
            conn.close()


class BudgetProxy:
    def __init__(self, config: Config, secret: str, directory: Path, upstream):
        config.validate()
        if len(secret) < 16:
            raise ValueError("dummy proxy secret must have at least 16 characters")
        self.config = config
        self.secret = secret
        self.upstream = upstream
        self.directory = directory
        self.lock = threading.Lock()
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "ledger.json"
        fingerprint = hashlib.sha256(
            json.dumps(asdict(config), sort_keys=True).encode()
        ).hexdigest()
        if self.path.exists():
            self.ledger = json.loads(self.path.read_text())
            if self.ledger["config_sha256"] != fingerprint:
                raise ValueError("cannot reuse ledger with changed protocol")
            # A crash after reservation leaves the charge uncertain: never reset it.
            for call in self.ledger["calls"]:
                if call.get("state") == "reserved":
                    call["state"] = "interrupted"
                    self.ledger["cells"][call["cell"]]["halted"] = "interrupted reservation"
        else:
            self.ledger = {
                "config_sha256": fingerprint,
                "reserved_usd": "0",
                "actual_usd": "0",
                "halted": "",
                "cells": {
                    c.id: {
                        "calls": 0,
                        "reserved_usd": "0",
                        "actual_usd": "0",
                        "reserved_generated_tokens": 0,
                        "actual_generated_tokens": 0,
                        "released_generated_tokens": 0,
                        "halted": "",
                    }
                    for c in config.cells
                },
                "calls": [],
            }
        self.save()

    def save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w") as handle:
            handle.write(self.sanitized_json(self.ledger))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def sanitized_json(self, value: dict) -> str:
        encoded = json.dumps(value, indent=2, sort_keys=True)
        for secret in (self.secret, getattr(self.upstream, "key", "")):
            if secret:
                # Redact the JSON-escaped representation, including unusual keys.
                encoded = encoded.replace(json.dumps(secret)[1:-1], "[REDACTED]")
        return encoded

    def authenticate(self, authorization: str) -> Cell:
        for cell in self.config.cells:
            if hmac.compare_digest(authorization, f"Bearer {self.secret}:{cell.id}"):
                return cell
        raise Rejected(401, "invalid proxy credential")

    def prepare(self, cell: Cell, raw: bytes) -> tuple[bytes, dict]:
        if len(raw) > self.config.max_body_bytes:
            raise Rejected(413, "request body exceeds cap")
        try:
            body = json.loads(raw)
        except ValueError as exc:
            raise Rejected(400, "invalid JSON") from exc
        if not isinstance(body, dict) or body.get("model") != cell.model:
            raise Rejected(403, "model does not match cell")
        keep = {
            "model",
            "messages",
            "stream",
            "tools",
            "tool_choice",
            "max_tokens",
            "max_completion_tokens",
            "temperature",
            "provider",
            "reasoning",
            "usage",
            "stream_options",
            "store",
            "response_format",
            "stop",
            "parallel_tool_calls",
        }
        if set(body) - keep:
            raise Rejected(400, "unsupported request parameters")
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            raise Rejected(400, "messages must be a nonempty list")
        for message in messages:
            if not isinstance(message, dict):
                raise Rejected(400, "invalid message")
            content = message.get("content")
            if isinstance(content, list):
                if not all(
                    isinstance(part, dict)
                    and part.get("type") == "text"
                    and isinstance(part.get("text"), str)
                    for part in content
                ):
                    raise Rejected(400, "only text content is allowed")
            elif content is not None and not isinstance(content, str):
                raise Rejected(400, "only text content is allowed")
        if type(body.get("stream", False)) is not bool:
            raise Rejected(400, "stream must be boolean")
        limits = [body[k] for k in ("max_tokens", "max_completion_tokens") if k in body]
        if any(type(v) is not int or v <= 0 for v in limits):
            raise Rejected(400, "invalid completion token limit")
        max_tokens = min([self.config.max_tokens, *limits])
        dropped = [k for k in ("max_completion_tokens", "store", "stream_options") if k in body]
        for key in dropped:
            body.pop(key)
        body["max_tokens"] = max_tokens
        body["temperature"] = self.config.temperature
        body["provider"] = {
            "order": [cell.provider],
            "only": [cell.provider],
            "allow_fallbacks": False,
            "require_parameters": True,
            "max_price": {
                "prompt": float(money(cell.input_price_per_million)),
                "completion": float(money(cell.output_price_per_million)),
            },
        }
        if cell.quantization:
            body["provider"]["quantizations"] = [cell.quantization]
        body["reasoning"] = self.config.reasoning or {"effort": "none"}
        body["usage"] = {"include": True}
        # Byte-level text tokens cannot outnumber UTF-8 bytes. Additional slack
        # covers chat templates, tool framing, and provider-added special tokens.
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        tools = body.get("tools", [])
        if not isinstance(tools, list):
            raise Rejected(400, "tools must be a list")
        prompt_bound = len(payload) + 4096 + 1024 * (len(messages) + len(tools))
        charge = (
            prompt_bound * money(cell.input_price_per_million)
            + max_tokens * money(cell.output_price_per_million)
        ) / MILLION
        return payload, {
            "request_sha256": hashlib.sha256(payload).hexdigest(),
            "request_bytes": len(payload),
            "prompt_token_upper_bound": prompt_bound,
            "max_tokens": max_tokens,
            "reserved_usd": str(charge),
            "dropped_parameters": sorted(dropped),
            "forced_parameters": ["provider", "reasoning", "temperature", "usage"],
            "stream": body.get("stream", False),
        }

    def reserve(self, cell: Cell, metadata: dict) -> dict:
        state = self.ledger["cells"][cell.id]
        charge = money(metadata["reserved_usd"])
        if self.ledger["halted"] or state["halted"]:
            raise Rejected(429, "cell or experiment halted after uncertain/invalid response")
        if state["calls"] >= self.config.max_calls_per_cell:
            raise Rejected(429, "cell call cap reached")
        if (
            state["reserved_generated_tokens"] + metadata["max_tokens"]
            > self.config.max_generated_tokens_per_cell
        ):
            raise Rejected(429, "cell generated-token cap reached")
        if money(state["reserved_usd"]) + charge > money(self.config.cell_budget_usd):
            raise Rejected(429, "cell dollar budget reached")
        if money(self.ledger["reserved_usd"]) + charge > money(self.config.global_budget_usd):
            raise Rejected(429, "global dollar budget reached")
        self.ledger["reserved_usd"] = str(money(self.ledger["reserved_usd"]) + charge)
        state["reserved_usd"] = str(money(state["reserved_usd"]) + charge)
        state["reserved_generated_tokens"] += metadata["max_tokens"]
        state["calls"] += 1
        call = {
            "n": len(self.ledger["calls"]) + 1,
            "cell": cell.id,
            "state": "reserved",
            **metadata,
        }
        self.ledger["calls"].append(call)
        self.save()  # Any failure here prevents forwarding.
        return call

    def complete(self, authorization: str, raw: bytes, start, emit) -> dict:
        cell = self.authenticate(authorization)
        payload, metadata = self.prepare(cell, raw)
        with self.lock:  # Serialize the entire reservation/forward/audit lifecycle.
            call = self.reserve(cell, metadata)
            telemetry = Telemetry(metadata["stream"])
            response = None
            try:
                response = self.upstream.completion(payload)
                call["http_status"] = response.status
                start(response.status, response.content_type)
                size = 0
                for chunk in response.chunks:
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ValueError("response exceeds cap")
                    telemetry.feed(chunk)
                    emit(chunk)
                call.update(telemetry.complete())
                if (
                    response.status != 200
                    or not call["telemetry_valid"]
                    or not call["stream_complete"]
                ):
                    raise ValueError("invalid completion response")
                if len(call["generation_ids"]) != 1 or not call["finish_reasons"]:
                    raise ValueError("missing unambiguous generation/finish metadata")
                try:
                    audit = self.upstream.generation(call["generation_ids"][0])
                finally:
                    call["generation_fetch_diagnostics"] = safe_fetch_diagnostics(
                        getattr(self.upstream, "last_generation_diagnostics", [])
                    )
                allowed_models = {cell.model, cell.served_model} - {""}
                expected_provider = cell.provider_display or cell.provider
                provider = audit.get("provider_name")
                valid = (
                    isinstance(provider, str)
                    and provider.casefold() == expected_provider.casefold()
                    and audit.get("model") == (cell.served_model or cell.model)
                    and set(call["served_models"]) <= allowed_models
                    and all(
                        p.casefold() == expected_provider.casefold()
                        for p in call["response_providers"]
                    )
                )
                # Retain only known primitive metadata, never provider payloads/errors.
                call["generation_audit"] = {
                    k: v
                    for k, v in audit.items()
                    if k
                    in {
                        "provider_name",
                        "model",
                        "total_cost",
                        "tokens_prompt",
                        "tokens_completion",
                        "native_tokens_reasoning",
                        "native_tokens_completion",
                        "native_tokens_prompt",
                        "finish_reason",
                    }
                    and (v is None or type(v) in (str, int, float))
                }
                cost = money(audit["total_cost"])
                call["actual_usd"] = str(cost)
                state = self.ledger["cells"][cell.id]
                state["actual_usd"] = str(money(state["actual_usd"]) + cost)
                self.ledger["actual_usd"] = str(money(self.ledger["actual_usd"]) + cost)
                for field, limit in (
                    ("tokens_prompt", call["prompt_token_upper_bound"]),
                    ("tokens_completion", call["max_tokens"]),
                    ("native_tokens_prompt", call["prompt_token_upper_bound"]),
                    ("native_tokens_completion", call["max_tokens"]),
                    ("native_tokens_reasoning", call["max_tokens"]),
                ):
                    if type(audit.get(field)) is int and audit[field] > limit:
                        self.ledger["halted"] = "provider token count exceeded reserved bound"
                        valid = False
                if cost > money(call["reserved_usd"]):
                    self.ledger["halted"] = "provider charge exceeded reserved ceiling"
                    valid = False
                call["route_valid"] = valid
                if not valid:
                    raise ValueError("generation route or charge differs from frozen ceiling")
                # Settle only authoritative generated usage after every route
                # and dollar-ceiling check passed. Include separately reported
                # reasoning conservatively even if a provider also counted it
                # in completion usage. Unknown counts keep the full allowance.
                counts = [audit.get("native_tokens_completion"), audit.get("tokens_completion")]
                counts = [v for v in counts if type(v) is int and v >= 0]
                reasoning_tokens = audit.get("native_tokens_reasoning", 0)
                call["generation_usage_settled"] = False
                if (
                    self.config.reasoning is not None
                    and self.config.reasoning.get("enabled") is False
                    and type(reasoning_tokens) is int
                    and reasoning_tokens > 0
                ):
                    call["reasoning_control_mismatch"] = (
                        "provider reported reasoning tokens with reasoning.enabled=false"
                    )
                if counts and type(reasoning_tokens) is int and reasoning_tokens >= 0:
                    observed = max([*counts, int(call["usage"].get("completion_tokens", 0))])
                    observed += reasoning_tokens
                    if observed <= call["max_tokens"]:
                        released = call["max_tokens"] - observed
                        state["reserved_generated_tokens"] -= released
                        state["actual_generated_tokens"] += observed
                        state["released_generated_tokens"] += released
                        call["settled_generated_tokens"] = observed
                        call["released_generated_tokens"] = released
                        call["generation_usage_settled"] = True
                call["state"] = "complete"
            except Exception as exc:
                call.update(telemetry.complete())
                call["state"] = "uncertain_or_invalid"
                call["error_type"] = type(exc).__name__  # No exception text: may contain secrets.
                self.ledger["cells"][cell.id]["halted"] = "uncertain or invalid response"
            finally:
                try:
                    if response is not None:
                        response.close()
                finally:
                    self.save()
            return call


def handler_for(proxy: BudgetProxy):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ComparisonBudgetProxy/1"

        def log_message(self, *_args):
            pass

        def reply(self, status: int, value: dict):
            body = proxy.sanitized_json(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self.reply(200, {"ready": True})
                return
            try:
                proxy.authenticate(self.headers.get("Authorization", ""))
            except Rejected as exc:
                self.reply(exc.status, {"error": exc.reason})
                return
            if self.path != "/ledger":
                self.reply(404, {"error": "not found"})
                return
            with proxy.lock:
                self.reply(200, proxy.ledger)

        def do_POST(self):
            started = False

            def start(status, content_type):
                nonlocal started
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                started = True

            def emit(chunk):
                self.wfile.write(chunk)
                self.wfile.flush()

            try:
                if self.path not in ("/chat/completions", "/v1/chat/completions"):
                    raise Rejected(404, "only chat completions are proxied")
                authorization = self.headers.get("Authorization", "")
                proxy.authenticate(authorization)
                if self.headers.get("Transfer-Encoding"):
                    raise Rejected(400, "chunked request bodies are unsupported")
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError as exc:
                    raise Rejected(400, "invalid content length") from exc
                if not 0 < length <= proxy.config.max_body_bytes:
                    raise Rejected(413, "request body exceeds cap or is empty")
                self.connection.settimeout(60)
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise Rejected(400, "incomplete request body")
                call = proxy.complete(authorization, raw, start, emit)
                if not started:
                    self.reply(502, {"error": "upstream failed", "call": call["n"]})
            except Rejected as exc:
                if not started:
                    self.reply(exc.status, {"error": exc.reason})
            except (OSError, ValueError):
                if not started:
                    self.reply(502, {"error": "proxy transport failed"})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    config = Config.from_manifest(json.loads(args.manifest.read_text()))
    proxy = BudgetProxy(
        config,
        os.environ["PI_PROXY_SECRET"],
        args.ledger,
        OpenRouter(os.environ["OPENROUTER_API_KEY"]),
    )
    server = ThreadingHTTPServer((args.host, args.port), handler_for(proxy))
    print(f"comparison proxy listening on port {args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
