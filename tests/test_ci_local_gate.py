"""Deterministic tests for the macOS/llama.cpp CI gate.

Every HTTP and subprocess boundary is injected, so this suite is
network-free, credential-free, and does not need a running llama-server —
the same discipline tests/test_ci_llm_gate.py applies to the paid gate.
"""

import json
from types import SimpleNamespace

import ci_local_gate as gate
import pytest


class FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def fake_sysctl(values, missing_returncode=1):
    """Return a subprocess.run stand-in serving a sysctl name -> value map."""

    def run(argv, **kwargs):
        name = argv[-1]
        if name not in values:
            return FakeProc(stdout="", returncode=missing_returncode)
        return FakeProc(stdout=values[name] + "\n", returncode=0)

    return run


M1_16GB = {
    "hw.memsize": str(16 * 1024**3),
    "hw.ncpu": "8",
    "machdep.cpu.brand_string": "Apple M1",
    "hw.model": "Macmini9,1",
}

RUNNER_7GB = {
    "hw.memsize": str(7 * 1024**3),
    "hw.ncpu": "3",
    "machdep.cpu.brand_string": "Apple M1",
    "hw.model": "VirtualMac2,1",
}


# --- hardware ---


def test_collect_hardware_parses_sysctl_values():
    hardware = gate.collect_hardware(run=fake_sysctl(M1_16GB), env={})
    assert hardware["chip"] == "Apple M1"
    assert hardware["hw_model"] == "Macmini9,1"
    assert hardware["cpus"] == 8
    assert hardware["memory_bytes"] == 16 * 1024**3
    assert hardware["memory_gb"] == pytest.approx(16.0)
    assert hardware["reference_memory_gb"] == 16


def test_collect_hardware_survives_a_missing_sysctl():
    """Linux and any sandbox without sysctl must report zeros, not raise."""
    hardware = gate.collect_hardware(run=fake_sysctl({}), env={})
    assert hardware["memory_bytes"] == 0
    assert hardware["memory_gb"] == 0.0
    assert hardware["chip"] == ""
    assert hardware["cpus"] == 0


def test_collect_hardware_records_the_runner_label():
    hardware = gate.collect_hardware(
        run=fake_sysctl(M1_16GB), env={"ASKME_RUNNER_LABEL": "macos-26-xlarge"}
    )
    assert hardware["runner_label"] == "macos-26-xlarge"


def test_collect_hardware_tolerates_non_numeric_sysctl_output():
    hardware = gate.collect_hardware(
        run=fake_sysctl({"hw.memsize": "not-a-number", "hw.ncpu": "also-not"}), env={}
    )
    assert hardware["memory_bytes"] == 0
    assert hardware["cpus"] == 0


def test_check_hardware_accepts_a_runner_meeting_the_floor():
    hardware = gate.collect_hardware(run=fake_sysctl(M1_16GB), env={})
    ok, message = gate.check_hardware(hardware, min_memory_gb=16, require_apple_silicon=False)
    assert ok
    assert "Apple M1" in message


def test_check_hardware_rejects_an_undersized_runner():
    """The whole point of the reference lane's gate: a 7 GB runner cannot
    hold the 5.15 GB reference model plus a 16K KV cache, and must fail
    before the download rather than being OOM-killed mid-run."""
    hardware = gate.collect_hardware(run=fake_sysctl(RUNNER_7GB), env={})
    ok, message = gate.check_hardware(hardware, min_memory_gb=16)
    assert not ok
    assert "7.00 GB" in message
    assert "16.00 GB" in message
    # The message must point at the actual remedy, not just the number.
    assert "14 GB" in message
    assert "self-hosted" in message


def test_check_hardware_clears_the_xlarge_default_floor():
    """macos-26-xlarge is 14 GB: short of the 16 GB reference machine but
    enough for the reference model, so the default floor of 12 passes."""
    hardware = gate.collect_hardware(run=fake_sysctl({**M1_16GB, "hw.memsize": str(14 * 1024**3)}))
    ok, _ = gate.check_hardware(hardware, min_memory_gb=12)
    assert ok
    ok_at_reference, _ = gate.check_hardware(hardware, min_memory_gb=16)
    assert not ok_at_reference


def test_check_hardware_flags_a_pass_that_is_below_the_reference_machine():
    """A 14 GB runner clears the default floor, but the summary must still
    say it is not the 16 GB reference — otherwise a green xlarge run reads
    back as reference-grade evidence."""
    hardware = gate.collect_hardware(run=fake_sysctl({**M1_16GB, "hw.memsize": str(14 * 1024**3)}))
    ok, message = gate.check_hardware(hardware, min_memory_gb=12)
    assert ok
    assert "below the 16 GB reference deployment" in message
    assert "not comparable" in message


def test_check_hardware_does_not_flag_a_runner_matching_the_reference():
    hardware = gate.collect_hardware(run=fake_sysctl(M1_16GB), env={})
    ok, message = gate.check_hardware(hardware, min_memory_gb=16)
    assert ok
    assert "below the" not in message


def test_check_hardware_requires_apple_silicon_when_asked():
    ok, message = gate.check_hardware(
        {"system": "Linux", "machine": "x86_64", "memory_gb": 64.0},
        require_apple_silicon=True,
    )
    assert not ok
    assert "not macOS" in message

    ok, message = gate.check_hardware(
        {"system": "Darwin", "machine": "x86_64", "memory_gb": 30.0},
        require_apple_silicon=True,
    )
    assert not ok
    assert "not Apple Silicon" in message


def test_check_hardware_skips_the_memory_floor_when_unset():
    hardware = gate.collect_hardware(run=fake_sysctl(RUNNER_7GB), env={})
    ok, _ = gate.check_hardware(hardware, min_memory_gb=0.0)
    assert ok


# --- preflight ---


def _ok_health(url, **kwargs):
    if url.endswith("/health"):
        return SimpleNamespace(status_code=200)
    if url.endswith("/props"):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"model_path": "/models/x.gguf", "chat_template_tool_use": True},
        )
    raise AssertionError("unexpected GET " + url)


def _ok_chat(url, **kwargs):
    return SimpleNamespace(
        status_code=200,
        json=lambda: {
            "model": "ci-local-model",
            "choices": [{"message": {"role": "assistant", "content": "ready"}}],
        },
    )


def test_preflight_passes_against_a_healthy_server():
    ok, message, record = gate.check_llama_server(get=_ok_health, post=_ok_chat)
    assert ok
    assert "assistant" in message
    assert record["health_status"] == 200
    assert record["chat_ok"] is True
    assert record["served_model_id"] == "ci-local-model"


def test_preflight_derives_the_base_url_from_the_chat_endpoint():
    seen = []

    def get(url, **kwargs):
        seen.append(url)
        return _ok_health(url, **kwargs)

    gate.check_llama_server(api="http://127.0.0.1:9999/v1/chat/completions", get=get, post=_ok_chat)
    assert "http://127.0.0.1:9999/health" in seen


def test_preflight_fails_when_the_server_is_unreachable():
    def boom(url, **kwargs):
        raise OSError("connection refused")

    ok, message, record = gate.check_llama_server(get=boom, post=_ok_chat)
    assert not ok
    assert "unreachable" in message
    assert "connection refused" in record["health_error"]


def test_preflight_fails_on_an_unhealthy_status():
    def unhealthy(url, **kwargs):
        return SimpleNamespace(status_code=503)

    ok, message, _ = gate.check_llama_server(get=unhealthy, post=_ok_chat)
    assert not ok
    assert "503" in message


def test_preflight_fails_when_health_is_green_but_chat_rejects_the_body():
    """A server that answers /health but rejects the chat-completions shape
    AskMe sends is broken. conftest's probe only checks /health, so this is
    exactly the gap the gate exists to close."""

    def rejecting(url, **kwargs):
        return SimpleNamespace(status_code=400)

    ok, message, record = gate.check_llama_server(get=_ok_health, post=rejecting)
    assert not ok
    assert "HTTP 400" in message
    assert record["health_status"] == 200
    assert "chat_ok" not in record


def test_preflight_fails_on_a_non_openai_reply_body():
    def wrong_shape(url, **kwargs):
        return SimpleNamespace(status_code=200, json=lambda: {"unexpected": True})

    ok, message, _ = gate.check_llama_server(get=_ok_health, post=wrong_shape)
    assert not ok
    assert "not an OpenAI reply" in message


def test_preflight_tolerates_a_missing_props_endpoint():
    def no_props(url, **kwargs):
        if url.endswith("/props"):
            raise OSError("404")
        return SimpleNamespace(status_code=200)

    ok, _, record = gate.check_llama_server(get=no_props, post=_ok_chat)
    assert ok
    assert "served_model" not in record


# --- probe ---


def _decoded(**fields):
    """A real DecodedAction — mapping-like, but not a dict subclass.

    Built through the production types on purpose: a hand-rolled stand-in
    let an accessor bug (reading a `.type` attribute that DecodedAction
    does not have) pass here while failing against a live server.
    """
    from actions import ActionEnvelope, ActionTransport, DecodedAction

    return DecodedAction(ActionEnvelope(tuple(fields.items())), ActionTransport())


def test_probe_passes_when_an_action_decodes():
    captured = {}

    def client_factory(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(ask=lambda *a, **k: _decoded(action="write", arg="x", content="y"))

    ok, message, record = gate.probe_action_transport(
        api="http://127.0.0.1:8080/v1/chat/completions",
        model="ci-local-model",
        client_factory=client_factory,
    )
    assert ok
    assert record["decoded_action"] == "write"
    assert "write" in message
    # The probe must exercise the real local-backend settings shape.
    assert captured["backend"] == "local"
    assert captured["model"] == "ci-local-model"
    assert captured["api_key"] == ""


def test_probe_decodes_a_real_tool_call_end_to_end(monkeypatch):
    """The default path — no injected factory — through the real
    askme.LLMClient, with only the HTTP boundary mocked. This is the test
    that pins the probe against the actual tools transport: the request it
    sends and the DecodedAction interface it reads back."""
    import json as _json

    import requests
    from _test_support import mock_http_response

    sent = {}

    def fake_post(url, **kwargs):
        sent["url"] = url
        sent["body"] = kwargs.get("json")
        return mock_http_response(
            json_body={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "write",
                                        "arguments": _json.dumps(
                                            {"arg": "hello.txt", "content": "hi"}
                                        ),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
        )

    monkeypatch.setattr(requests, "post", fake_post)
    ok, _, record = gate.probe_action_transport(
        api="http://127.0.0.1:8080/v1/chat/completions", model="ci-local-model"
    )
    assert ok
    assert record["decoded_action"] == "write"
    # The probe must carry the registry-derived tool definitions with
    # tool_choice auto — that is the transport contract it exists to check.
    assert sent["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    assert sent["body"]["model"] == "ci-local-model"
    assert sent["body"]["tool_choice"] == "auto"
    assert {t["function"]["name"] for t in sent["body"]["tools"]} == {
        "done",
        "edit",
        "fail",
        "read",
        "search",
        "shell",
        "tree",
        "write",
    }


def test_probe_requests_the_action_schema_over_the_tools_transport():
    """The value of this probe is that it goes through expect="action" —
    the tools payload and envelope decode — not a plain chat call."""
    seen = {}

    def client_factory(**kwargs):
        def ask(messages, **call_kwargs):
            seen["messages"] = messages
            seen.update(call_kwargs)
            return _decoded(action="shell", arg="ls")

        return SimpleNamespace(ask=ask)

    gate.probe_action_transport(client_factory=client_factory)
    assert seen["expect"] == "action"
    assert seen["messages"][0]["role"] == "system"


def test_probe_reports_a_transport_failure_without_raising():
    def client_factory(**kwargs):
        def ask(*a, **k):
            raise RuntimeError("no tool call after retries")

        return SimpleNamespace(ask=ask)

    ok, message, record = gate.probe_action_transport(client_factory=client_factory)
    assert not ok
    assert "no tool call after retries" in record["error"]
    assert "raised" in message


def test_probe_reports_an_undecodable_reply():
    def client_factory(**kwargs):
        return SimpleNamespace(ask=lambda *a, **k: None)

    ok, message, _ = gate.probe_action_transport(client_factory=client_factory)
    assert not ok
    assert "no action decoded" in message


def test_probe_accepts_a_dict_shaped_action():
    def client_factory(**kwargs):
        return SimpleNamespace(ask=lambda *a, **k: {"action": "read"})

    ok, _, record = gate.probe_action_transport(client_factory=client_factory)
    assert ok
    assert record["decoded_action"] == "read"


def test_probe_reports_an_action_object_without_a_mapping_interface():
    """Anything that is not mapping-like is a decode miss, not a crash."""

    def client_factory(**kwargs):
        return SimpleNamespace(ask=lambda *a, **k: object())

    ok, message, _ = gate.probe_action_transport(client_factory=client_factory)
    assert not ok
    assert "no action decoded" in message


# --- report rendering ---


def test_markdown_carries_the_evidence_boundary():
    """Every summary must say these runs are not performance evidence, so a
    green macOS job is never mistaken for a docs/PERFORMANCE.md baseline."""
    markdown = gate.render_markdown([("hardware", {"ok": True, "message": "Apple M1"})])
    assert "not performance evidence" in markdown
    assert "docs/PERFORMANCE.md" in markdown
    assert "| hardware | pass | Apple M1 |" in markdown


def test_markdown_distinguishes_advisory_from_fail():
    markdown = gate.render_markdown(
        [
            ("gate", {"ok": False, "message": "broken"}),
            ("probe", {"ok": False, "advisory": True, "message": "no call"}),
        ]
    )
    assert "| gate | fail | broken |" in markdown
    assert "| probe | advisory | no call |" in markdown


def test_markdown_escapes_pipes_in_details():
    markdown = gate.render_markdown([("x", {"ok": True, "message": "a|b\nc"})])
    assert "a\\|b c" in markdown


# --- CLI ---


def test_hardware_command_writes_a_json_record(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "collect_hardware", lambda: dict(M1_16GB_HARDWARE))
    out = tmp_path / "hardware.json"
    summary = tmp_path / "summary.md"
    rc = gate.main(
        [
            "hardware",
            "--min-memory-gb",
            "12",
            "--json-out",
            str(out),
            "--markdown-out",
            str(summary),
        ]
    )
    assert rc == 0
    record = json.loads(out.read_text())
    assert record["ok"] is True
    assert record["memory_gb"] == 16.0
    assert "not performance evidence" in summary.read_text()


M1_16GB_HARDWARE = {
    "system": "Darwin",
    "machine": "arm64",
    "chip": "Apple M1",
    "cpus": 8,
    "memory_bytes": 16 * 1024**3,
    "memory_gb": 16.0,
    "runner_label": "self-hosted-tart",
    "reference_memory_gb": 16,
}


def test_hardware_command_exits_nonzero_below_the_floor(tmp_path, monkeypatch):
    small = dict(M1_16GB_HARDWARE, memory_gb=7.0, memory_bytes=7 * 1024**3)
    monkeypatch.setattr(gate, "collect_hardware", lambda: small)
    assert gate.main(["hardware", "--min-memory-gb", "16"]) == 1


def test_preflight_command_exits_nonzero_when_the_server_is_down(tmp_path, monkeypatch):
    monkeypatch.setattr(
        gate, "check_llama_server", lambda **kwargs: (False, "down", {"health_status": None})
    )
    out = tmp_path / "preflight.json"
    assert gate.main(["preflight", "--json-out", str(out)]) == 1
    assert json.loads(out.read_text())["ok"] is False


def test_probe_command_is_advisory_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(
        gate, "probe_action_transport", lambda **kwargs: (False, "no call", {"error": "x"})
    )
    out = tmp_path / "probe.json"
    assert gate.main(["probe", "--json-out", str(out)]) == 0
    record = json.loads(out.read_text())
    assert record["ok"] is False
    assert record["advisory"] is True


def test_probe_command_blocks_under_strict(monkeypatch):
    monkeypatch.setattr(
        gate, "probe_action_transport", lambda **kwargs: (False, "no call", {"error": "x"})
    )
    assert gate.main(["probe", "--strict"]) == 1


def test_probe_command_passes_through_success(monkeypatch):
    monkeypatch.setattr(
        gate, "probe_action_transport", lambda **kwargs: (True, "ok", {"decoded_action": "write"})
    )
    assert gate.main(["probe", "--strict"]) == 0
