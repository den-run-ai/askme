"""Paid-call boundary regressions; these tests never contact a model provider."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal

import pytest

from tests.featurebench.pi_comparison_proxy import (
    BudgetProxy,
    Cell,
    Config,
    Rejected,
    Response,
    Telemetry,
)

SECRET = "dummy-only-test-secret"
MODEL = "test/dense-model"
SERVED = "test/dense-model-20260914"


def configuration(**overrides):
    return Config(
        cells=(Cell("askme", MODEL, "provider/fp8", ".30", "3.20", SERVED, "Provider", "fp8"),),
        reasoning={"effort": "low"},
        **overrides,
    )


def request(**overrides):
    return json.dumps(
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": "Implement the feature."}],
            **overrides,
        }
    ).encode()


class FakeUpstream:
    def __init__(self, *, streaming=False, failure=None, audit=None):
        self.requests = []
        self.audit_requests = []
        self.closed = 0
        self.failure = failure
        self.audit = audit or {
            "provider_name": "Provider",
            "model": SERVED,
            "total_cost": 0.001,
            "tokens_prompt": 25,
            "tokens_completion": 10,
            "secret_nonmetadata": "must not be retained",
        }
        self.event = {
            "id": "gen-example",
            "model": SERVED,
            "provider": "Provider",
            "choices": [
                {"index": 0, "finish_reason": "stop", "message": {"content": "private output"}}
            ],
            "usage": {
                "prompt_tokens": 25,
                "completion_tokens": 10,
                "total_tokens": 35,
                "cost": 0.001,
            },
        }
        self.streaming = streaming
        self.before_forward = lambda: None

    def completion(self, payload):
        self.before_forward()
        self.requests.append(json.loads(payload))
        if self.failure:
            raise self.failure
        raw = json.dumps(self.event).encode()
        if self.streaming:
            # Deliberately repeat terminal/usage events and split UTF-8/JSON boundaries.
            raw = b"data: " + raw + b"\n\ndata: " + raw + b"\n\ndata: [DONE]\n\n"
        return Response(
            200,
            "text/event-stream" if self.streaming else "application/json",
            [raw[:17], raw[17:49], raw[49:]],
            self.close,
        )

    def close(self):
        self.closed += 1

    def generation(self, generation_id):
        self.audit_requests.append(generation_id)
        return self.audit


def execute(proxy, raw=None, authorization=None, emit=None):
    return proxy.complete(
        authorization or f"Bearer {SECRET}:askme",
        request() if raw is None else raw,
        lambda *_: None,
        emit or (lambda _: None),
    )


def test_reservation_is_durable_before_forward_and_never_refunded(tmp_path):
    upstream = FakeUpstream()
    proxy = BudgetProxy(configuration(), SECRET, tmp_path, upstream)

    def inspect_reservation():
        durable = json.loads((tmp_path / "ledger.json").read_text())
        assert durable["calls"][0]["state"] == "reserved"
        assert Decimal(durable["reserved_usd"]) > 0

    upstream.before_forward = inspect_reservation
    call = execute(proxy)
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    assert call["state"] == "complete"
    assert call["route_valid"] is True
    assert ledger["reserved_usd"] == call["reserved_usd"]
    assert Decimal(ledger["reserved_usd"]) > Decimal(ledger["actual_usd"]) == Decimal(".001")
    assert upstream.audit_requests == ["gen-example"]
    assert "private output" not in (tmp_path / "ledger.json").read_text()
    assert "secret_nonmetadata" not in (tmp_path / "ledger.json").read_text()


@pytest.mark.parametrize("failure", [ConnectionError("contains secret credential"), TimeoutError()])
def test_uncertain_transport_failure_retains_reservation_and_halts_cell(tmp_path, failure):
    upstream = FakeUpstream(failure=failure)
    proxy = BudgetProxy(configuration(), SECRET, tmp_path, upstream)
    call = execute(proxy)
    assert call["state"] == "uncertain_or_invalid"
    assert proxy.ledger["reserved_usd"] == call["reserved_usd"]
    assert proxy.ledger["actual_usd"] == "0"
    with pytest.raises(Rejected, match="halted"):
        execute(proxy)
    assert len(upstream.requests) == 1
    assert "contains secret" not in (tmp_path / "ledger.json").read_text()


def test_client_disconnect_keeps_charge_and_closes_upstream(tmp_path):
    upstream = FakeUpstream(streaming=True)
    proxy = BudgetProxy(configuration(), SECRET, tmp_path, upstream)

    def disconnect(_chunk):
        raise BrokenPipeError("client dropped")

    call = execute(proxy, request(stream=True), emit=disconnect)
    assert call["error_type"] == "BrokenPipeError"
    assert proxy.ledger["reserved_usd"] == call["reserved_usd"]
    assert upstream.closed == 1
    assert upstream.audit_requests == []


def test_duplicate_stream_finish_and_usage_are_counted_once(tmp_path):
    upstream = FakeUpstream(streaming=True)
    proxy = BudgetProxy(configuration(), SECRET, tmp_path, upstream)
    call = execute(proxy, request(stream=True))
    assert call["state"] == "complete"
    assert call["finish_reasons"] == {"0": "stop"}
    assert call["usage"] == {
        "prompt_tokens": 25,
        "completion_tokens": 10,
        "total_tokens": 35,
        "cost": "0.001",
    }
    assert call["generation_ids"] == ["gen-example"]
    assert proxy.ledger["actual_usd"] == "0.001"


def test_conflicting_stream_finish_is_invalid():
    telemetry = Telemetry(True)
    telemetry.feed(b'data: {"choices":[{"index":0,"finish_reason":"stop"}]}\n\n')
    telemetry.feed(b'data: {"choices":[{"index":0,"finish_reason":"length"}]}\n\n')
    telemetry.feed(b"data: [DONE]\n\n")
    result = telemetry.complete()
    assert result["telemetry_valid"] is False
    assert len(result["finish_reasons"]) == 1


@pytest.mark.parametrize(
    "raw,authorization,status",
    [
        (request(), "Bearer wrong-secret", 401),
        (request(model="other/model"), None, 403),
        (request(plugins=[{"id": "web"}]), None, 400),
        (
            request(
                messages=[{"content": [{"type": "image_url", "image_url": "https://example.test"}]}]
            ),
            None,
            400,
        ),
        (request(max_tokens=0), None, 400),
        (request(max_tokens=True), None, 400),
        (request(stream="true"), None, 400),
        (b"not JSON", None, 400),
    ],
)
def test_rejected_request_does_not_reserve_or_forward(tmp_path, raw, authorization, status):
    upstream = FakeUpstream()
    proxy = BudgetProxy(configuration(), SECRET, tmp_path, upstream)
    with pytest.raises(Rejected) as caught:
        execute(proxy, raw, authorization)
    assert caught.value.status == status
    assert upstream.requests == []
    assert proxy.ledger["reserved_usd"] == "0"
    assert proxy.ledger["cells"]["askme"]["calls"] == 0


def test_request_body_cap_prevents_forward(tmp_path):
    upstream = FakeUpstream()
    proxy = BudgetProxy(configuration(max_body_bytes=8), SECRET, tmp_path, upstream)
    with pytest.raises(Rejected, match="body"):
        execute(proxy)
    assert upstream.requests == []


def test_cell_budget_is_checked_before_forward(tmp_path):
    upstream = FakeUpstream()
    proxy = BudgetProxy(configuration(cell_budget_usd=".05"), SECRET, tmp_path, upstream)
    first = execute(proxy)
    assert first["state"] == "complete"
    assert Decimal(first["reserved_usd"]) < Decimal(".05")
    with pytest.raises(Rejected, match="cell dollar budget"):
        execute(proxy)
    assert len(upstream.requests) == 1
    assert proxy.ledger["cells"]["askme"]["calls"] == 1


def test_global_budget_is_shared_across_cells(tmp_path):
    config = configuration(global_budget_usd=".05")
    config = replace(config, cells=(*config.cells, replace(config.cells[0], id="pi")))
    upstream = FakeUpstream()
    proxy = BudgetProxy(config, SECRET, tmp_path, upstream)
    execute(proxy)
    with pytest.raises(Rejected, match="global dollar budget"):
        execute(proxy, authorization=f"Bearer {SECRET}:pi")
    assert proxy.ledger["cells"]["pi"]["calls"] == 0
    assert len(upstream.requests) == 1


@pytest.mark.parametrize(
    "config,reason",
    [
        (configuration(max_calls_per_cell=1), "call cap"),
        (configuration(max_generated_tokens_per_cell=8192), "generated-token cap"),
    ],
)
def test_other_hard_limits_are_pre_forward(tmp_path, config, reason):
    upstream = FakeUpstream()
    proxy = BudgetProxy(config, SECRET, tmp_path, upstream)
    execute(proxy)
    with pytest.raises(Rejected, match=reason):
        execute(proxy)
    assert len(upstream.requests) == 1


def test_restarts_retain_completed_and_uncertain_reservations(tmp_path):
    upstream = FakeUpstream()
    config = configuration(max_calls_per_cell=1)
    proxy = BudgetProxy(config, SECRET, tmp_path, upstream)
    execute(proxy)
    resumed = BudgetProxy(config, SECRET, tmp_path, upstream)
    with pytest.raises(Rejected, match="call cap"):
        execute(resumed)
    assert len(upstream.requests) == 1
    with pytest.raises(ValueError, match="changed protocol"):
        BudgetProxy(configuration(), SECRET, tmp_path, upstream)


def test_interrupted_reserved_call_halts_after_restart(tmp_path):
    config = configuration()
    proxy = BudgetProxy(config, SECRET, tmp_path, FakeUpstream())
    _, metadata = proxy.prepare(config.cells[0], request())
    reserved = proxy.reserve(config.cells[0], metadata)
    resumed = BudgetProxy(config, SECRET, tmp_path, FakeUpstream())
    assert resumed.ledger["reserved_usd"] == reserved["reserved_usd"]
    with pytest.raises(Rejected, match="halted"):
        execute(resumed)


def test_shared_route_controls_and_lower_caller_token_limit_are_enforced(tmp_path):
    upstream = FakeUpstream()
    proxy = BudgetProxy(configuration(), SECRET, tmp_path, upstream)
    call = execute(
        proxy,
        request(
            max_completion_tokens=1024,
            temperature=2,
            reasoning={"effort": "high"},
            provider={"order": ["unmatched"]},
            store=True,
            stream_options={"include_usage": True},
        ),
    )
    payload = upstream.requests[0]
    assert payload["max_tokens"] == 1024
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["temperature"] == 0
    assert payload["provider"] == {
        "order": ["provider/fp8"],
        "only": ["provider/fp8"],
        "allow_fallbacks": False,
        "require_parameters": True,
        "quantizations": ["fp8"],
        "max_price": {"prompt": 0.3, "completion": 3.2},
    }
    assert call["dropped_parameters"] == ["max_completion_tokens", "store", "stream_options"]
    assert call["prompt_token_upper_bound"] > call["request_bytes"]
    assert SECRET not in json.dumps(payload)


@pytest.mark.parametrize(
    "field,value", [("provider_name", "Wrong"), ("model", "other/model"), ("total_cost", 3)]
)
def test_route_or_charge_audit_mismatch_is_not_a_valid_result(tmp_path, field, value):
    upstream = FakeUpstream()
    upstream.audit[field] = value
    proxy = BudgetProxy(configuration(), SECRET, tmp_path, upstream)
    call = execute(proxy)
    assert call["state"] == "uncertain_or_invalid"
    assert call["route_valid"] is False
    with pytest.raises(Rejected, match="halted"):
        execute(proxy)


def test_missing_generation_cost_fails_closed(tmp_path):
    upstream = FakeUpstream()
    upstream.audit.pop("total_cost")
    proxy = BudgetProxy(configuration(), SECRET, tmp_path, upstream)
    assert execute(proxy)["state"] == "uncertain_or_invalid"
    with pytest.raises(Rejected, match="halted"):
        execute(proxy)


@pytest.mark.parametrize(
    "overrides",
    [
        {"global_budget_usd": "8.01"},
        {"cell_budget_usd": "2.01"},
        {"max_calls_per_cell": 61},
        {"max_generated_tokens_per_cell": 131073},
        {"global_budget_usd": "NaN"},
        {"cell_budget_usd": "Infinity"},
    ],
)
def test_manifest_cannot_raise_authorized_caps(overrides):
    with pytest.raises(ValueError):
        configuration(**overrides).validate()


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class MetadataResponse:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = b'{"data":{}}' if body is None else body
        self.read_calls = 0

    def read1(self, size):
        self.read_calls += 1
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk


def metadata_transport(monkeypatch, responses):
    from tests.featurebench import pi_comparison_proxy as module

    clock = FakeClock()
    requests = []
    connections = []

    class Connection:
        sock = None

        def __init__(self, host, timeout):
            assert host == "openrouter.ai"
            assert 0 < timeout <= 10
            self.response = responses.pop(0) if len(responses) > 1 else responses[0]
            self.closed = False
            connections.append(self)

        def request(self, method, path, headers):
            requests.append({"method": method, "path": path, "headers": headers})

        def getresponse(self):
            return self.response

        def close(self):
            self.closed = True

    monkeypatch.setattr(module, "HTTPSConnection", Connection)
    monkeypatch.setattr(module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(module.time, "sleep", clock.sleep)
    return module, clock, requests, connections


def test_generation_metadata_polls_past_three_immediate_404s_then_succeeds(monkeypatch):
    responses = [MetadataResponse(404) for _ in range(3)]
    responses.append(MetadataResponse(body=json.dumps({"data": {"model": SERVED}}).encode()))
    module, clock, requests, connections = metadata_transport(monkeypatch, responses)
    upstream = module.OpenRouter("not-a-real-key")
    assert upstream.generation("gen-later") == {"model": SERVED}
    assert clock.now == 14
    assert len(requests) == 4
    assert all(r["method"] == "GET" and r["path"].endswith("id=gen-later") for r in requests)
    assert all(
        r["headers"]["User-Agent"] == "askme-pi-comparison/2 (metadata-audit)" for r in requests
    )
    assert all(connection.closed for connection in connections)
    assert [d["http_status"] for d in upstream.last_generation_diagnostics] == [404, 404, 404, 200]
    assert upstream.last_generation_diagnostics[-1] == {
        "attempt": 4,
        "stage": "complete",
        "http_status": 200,
        "elapsed_ms": 14000,
    }


def test_permanent_missing_metadata_stops_at_sixty_seconds_without_completion_retry(monkeypatch):
    module, clock, requests, connections = metadata_transport(monkeypatch, [MetadataResponse(404)])
    upstream = module.OpenRouter("not-a-real-key")
    with pytest.raises(module.MetadataAuditError) as caught:
        upstream.generation("gen-unavailable")
    assert caught.value.stage == "deadline"
    assert clock.now == 60
    assert len(requests) == 9
    assert {request["method"] for request in requests} == {"GET"}
    assert all(connection.closed for connection in connections)
    assert upstream.last_generation_diagnostics[-1]["elapsed_ms"] == 60000
    assert upstream.last_generation_diagnostics[-1]["http_status"] == 404


@pytest.mark.parametrize("status", [401, 403])
def test_metadata_auth_failure_is_permanent_and_never_reads_error_body(monkeypatch, status):
    response = MetadataResponse(status, body=SECRET.encode())
    module, clock, requests, _ = metadata_transport(monkeypatch, [response])
    upstream = module.OpenRouter("not-a-real-key")
    with pytest.raises(module.MetadataAuditError) as caught:
        upstream.generation("gen-protected")
    assert len(requests) == 1
    assert clock.sleeps == []
    assert response.read_calls == 0
    assert caught.value.stage == "http_status"
    assert caught.value.http_status == status
    assert SECRET not in str(caught.value)
    assert upstream.last_generation_diagnostics == [
        {"attempt": 1, "stage": "http_status", "http_status": status, "elapsed_ms": 0}
    ]


@pytest.mark.parametrize(
    "body,stage",
    [
        (b"not-json " + SECRET.encode(), "json_parse"),
        (b'{"data":[]}', "data_shape"),
        (b"[1,2]", "data_shape"),
    ],
)
def test_malformed_metadata_fails_once_with_only_safe_stage(monkeypatch, body, stage):
    module, clock, requests, _ = metadata_transport(monkeypatch, [MetadataResponse(body=body)])
    upstream = module.OpenRouter("not-a-real-key")
    with pytest.raises(module.MetadataAuditError) as caught:
        upstream.generation("gen-malformed")
    assert caught.value.stage == stage
    assert len(requests) == 1
    assert clock.sleeps == []
    assert SECRET not in str(caught.value)
    assert SECRET not in json.dumps(upstream.last_generation_diagnostics)
    assert upstream.last_generation_diagnostics[0]["http_status"] == 200


def test_oversized_metadata_is_not_retained_or_retried(monkeypatch):
    module, clock, requests, _ = metadata_transport(monkeypatch, [MetadataResponse(body=b"x" * 9)])
    monkeypatch.setattr(module, "METADATA_BODY_BYTES", 8)
    upstream = module.OpenRouter("not-a-real-key")
    with pytest.raises(module.MetadataAuditError) as caught:
        upstream.generation("gen-large")
    assert caught.value.stage == "body_limit"
    assert len(requests) == 1
    assert clock.sleeps == []
    assert upstream.last_generation_diagnostics == [
        {"attempt": 1, "stage": "body_limit", "http_status": 200, "elapsed_ms": 0}
    ]


def test_retained_metadata_redacts_both_credentials_even_if_upstream_echoes_them(tmp_path):
    upstream = FakeUpstream()
    upstream.key = "fake-host-only-api-key"
    upstream.audit["provider_name"] = upstream.key
    upstream.event["id"] = "gen-" + SECRET
    proxy = BudgetProxy(configuration(), SECRET, tmp_path, upstream)
    assert execute(proxy)["state"] == "uncertain_or_invalid"
    persisted = (tmp_path / "ledger.json").read_text()
    assert upstream.key not in persisted
    assert SECRET not in persisted
    assert "[REDACTED]" in persisted


def test_provider_token_bound_violation_halts_experiment(tmp_path):
    upstream = FakeUpstream()
    upstream.audit["tokens_completion"] = 8193
    proxy = BudgetProxy(configuration(), SECRET, tmp_path, upstream)
    assert execute(proxy)["state"] == "uncertain_or_invalid"
    assert proxy.ledger["halted"] == "provider token count exceeded reserved bound"


def test_generation_manifest_alias_and_prose_do_not_change_controls():
    from dataclasses import asdict

    value = asdict(configuration())
    cells = [dict(c) for c in value.pop("cells")]
    cells[0]["served_provider"] = cells[0].pop("provider_display")
    value["budget_policy"] = "Reserve before forwarding; no uncertain refunds."
    loaded = Config.from_manifest({"cells": cells, "proxy": value})
    assert loaded == configuration()


def test_confirmed_unused_generation_allowance_is_released_but_dollars_are_retained(tmp_path):
    upstream = FakeUpstream()
    proxy = BudgetProxy(
        configuration(max_generated_tokens_per_cell=16384), SECRET, tmp_path, upstream
    )
    calls = [execute(proxy) for _ in range(4)]
    assert all(call["state"] == "complete" for call in calls)
    state = proxy.ledger["cells"]["askme"]
    assert state["actual_generated_tokens"] == 40
    assert state["reserved_generated_tokens"] == 40
    assert state["released_generated_tokens"] == 4 * (8192 - 10)
    assert all(call["generation_usage_settled"] for call in calls)
    assert Decimal(state["reserved_usd"]) == sum(Decimal(c["reserved_usd"]) for c in calls)
    assert Decimal(state["reserved_usd"]) > Decimal(state["actual_usd"])


def test_uncertain_or_unaudited_generated_usage_keeps_full_allowance(tmp_path):
    upstream = FakeUpstream()
    upstream.audit.pop("tokens_completion")
    proxy = BudgetProxy(configuration(), SECRET, tmp_path, upstream)
    call = execute(proxy)
    assert call["state"] == "complete"
    assert call["generation_usage_settled"] is False
    state = proxy.ledger["cells"]["askme"]
    assert state["reserved_generated_tokens"] == 8192
    assert state["actual_generated_tokens"] == 0
    assert state["released_generated_tokens"] == 0


def test_invalid_route_never_releases_generation_allowance(tmp_path):
    upstream = FakeUpstream()
    upstream.audit["provider_name"] = "other"
    proxy = BudgetProxy(configuration(), SECRET, tmp_path, upstream)
    call = execute(proxy)
    assert call["state"] == "uncertain_or_invalid"
    state = proxy.ledger["cells"]["askme"]
    assert state["reserved_generated_tokens"] == 8192
    assert state["released_generated_tokens"] == 0


def test_generation_route_requires_exact_dated_model(tmp_path):
    upstream = FakeUpstream()
    upstream.audit["model"] = MODEL
    proxy = BudgetProxy(configuration(), SECRET, tmp_path, upstream)
    call = execute(proxy)
    assert call["route_valid"] is False
    assert call["state"] == "uncertain_or_invalid"


def test_separate_reasoning_is_conservatively_accounted_and_mismatch_recorded(tmp_path):
    upstream = FakeUpstream()
    upstream.audit.update(native_tokens_completion=12, native_tokens_reasoning=20)
    config = replace(configuration(), reasoning={"enabled": False})
    proxy = BudgetProxy(config, SECRET, tmp_path, upstream)
    call = execute(proxy)
    assert call["state"] == "complete"
    assert call["settled_generated_tokens"] == 32
    assert call["reasoning_control_mismatch"]
    assert call["generation_audit"]["native_tokens_completion"] == 12
    assert call["generation_audit"]["native_tokens_reasoning"] == 20


def test_persistence_failure_prevents_network_call(tmp_path, monkeypatch):
    upstream = FakeUpstream()
    proxy = BudgetProxy(configuration(), SECRET, tmp_path, upstream)

    def unwritable():
        raise OSError("disk full")

    monkeypatch.setattr(proxy, "save", unwritable)
    with pytest.raises(OSError, match="disk full"):
        execute(proxy)
    assert upstream.requests == []


def test_concurrent_requests_share_one_budget_decision(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    upstream = FakeUpstream()
    proxy = BudgetProxy(configuration(max_calls_per_cell=1), SECRET, tmp_path, upstream)

    def attempt(_):
        try:
            return execute(proxy)["state"]
        except Rejected:
            return "rejected"

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(attempt, range(4)))
    assert outcomes.count("complete") == 1
    assert outcomes.count("rejected") == 3
    assert len(upstream.requests) == 1


def test_metadata_fetch_diagnostics_persist_on_failure_without_releasing_reservations(
    tmp_path, monkeypatch
):
    module, _clock, requests, _ = metadata_transport(monkeypatch, [MetadataResponse(401)])
    upstream = module.OpenRouter("not-a-real-key")
    completion = FakeUpstream()
    monkeypatch.setattr(upstream, "completion", completion.completion)
    proxy = BudgetProxy(configuration(), SECRET, tmp_path, upstream)
    call = execute(proxy)
    assert call["state"] == "uncertain_or_invalid"
    assert call["generation_fetch_diagnostics"] == [
        {"attempt": 1, "stage": "http_status", "http_status": 401, "elapsed_ms": 0}
    ]
    assert len(completion.requests) == 1
    assert len(requests) == 1
    assert proxy.ledger["reserved_usd"] == call["reserved_usd"]
    assert proxy.ledger["cells"]["askme"]["reserved_generated_tokens"] == call["max_tokens"]
    assert proxy.ledger["cells"]["askme"]["released_generated_tokens"] == 0
    saved = json.loads((tmp_path / "ledger.json").read_text())
    assert saved["calls"][0]["generation_fetch_diagnostics"] == call["generation_fetch_diagnostics"]


def test_successful_metadata_poll_diagnostics_persist_without_dollar_refund(tmp_path, monkeypatch):
    completion = FakeUpstream()
    responses = [
        MetadataResponse(404),
        MetadataResponse(body=json.dumps({"data": completion.audit}).encode()),
    ]
    module, _clock, requests, _ = metadata_transport(monkeypatch, responses)
    upstream = module.OpenRouter("not-a-real-key")
    monkeypatch.setattr(upstream, "completion", completion.completion)
    proxy = BudgetProxy(configuration(), SECRET, tmp_path, upstream)
    call = execute(proxy)
    assert call["state"] == "complete"
    assert [d["http_status"] for d in call["generation_fetch_diagnostics"]] == [404, 200]
    assert len(completion.requests) == 1
    assert len(requests) == 2
    assert proxy.ledger["reserved_usd"] == call["reserved_usd"]
    saved = json.loads((tmp_path / "ledger.json").read_text())
    assert saved["calls"][0]["generation_fetch_diagnostics"] == call["generation_fetch_diagnostics"]


def test_metadata_diagnostic_allowlist_never_copies_extra_response_fields():
    from tests.featurebench.pi_comparison_proxy import safe_fetch_diagnostics

    diagnostic = {"stage": "http_status", "http_status": 404, "attempt": 1, "elapsed_ms": 5}
    assert safe_fetch_diagnostics([{**diagnostic, "error_body": SECRET, "headers": SECRET}]) == [
        diagnostic
    ]
    assert safe_fetch_diagnostics([{**diagnostic, "stage": SECRET}]) == []
