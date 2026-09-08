"""Offline contract/deadline evidence for the standalone serving qualification."""

import json
from pathlib import Path

import pytest
import serving_qualification as gate


def protocol():
    return gate.resolve_protocol(
        {
            "protocol": "synthetic-serving-test-v1",
            "api": "http://127.0.0.1:8080/v1/chat/completions",
            "model": "synthetic-model",
        }
    )


def exchange(*chunks, wall_s=5, timed_out=False, model="synthetic-model"):
    if chunks and model is not None:
        chunks = ({"model": model, **chunks[0]}, *chunks[1:])
    return {
        "name": "probe",
        "wall_s": wall_s,
        "timed_out": timed_out,
        "events": [
            {"event": "chunk", "body": chunk, "wall_s": i + 1} for i, chunk in enumerate(chunks)
        ],
    }


def response(name, body):
    return {"name": name, "events": [{"event": "response", "body": body}]}


@pytest.mark.parametrize(
    "url",
    [
        "https://openrouter.ai/api/v1/chat/completions",
        "http://secret@localhost:8080/v1/chat/completions",
        "http://example.com/v1/chat/completions",
        "http://localhost:8080/v1/chat/completions?key=secret",
    ],
)
def test_only_credential_free_loopback_is_allowed(url):
    with pytest.raises(ValueError, match="credential-free"):
        gate.resolve_protocol({**protocol(), "api": url})


def test_reduced_decode_matrix_cannot_certify_a_deployment():
    with pytest.raises(ValueError, match="fixed"):
        gate.resolve_protocol({**protocol(), "cases": gate.DEFAULT_CASES[:1]})


def test_fragmented_tool_arguments_reconstruct_exact_payload():
    import askme

    name = askme._ACTION_TOOLS[0]["function"]["name"]
    # Reconstruction is independent of which actual schema the runtime declares.
    result = exchange(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "function": {
                                    "name": name,
                                    "arguments": '{"arg":"hello.txt","content":"hi',
                                },
                            }
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {
                                    "arguments": '\\n"}',
                                },
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
    )
    parsed = gate.summarize_stream(result)
    call = parsed["message"]["tool_calls"][0]
    assert json.loads(call["function"]["arguments"]) == {"arg": "hello.txt", "content": "hi\n"}
    assert call["function"]["name"] == name
    assert parsed["first_token_s"] == 1
    assert parsed["terminal"]


def test_prefill_progress_and_empty_role_are_not_first_output_tokens():
    parsed = gate.summarize_stream(
        exchange(
            {"prompt_progress": {"processed": 128}},
            {"choices": [{"delta": {"role": "assistant", "content": ""}}]},
            {"content": "hi", "tokens": [1]},
            {"content": "", "stop": True, "tokens_predicted": 128},
        )
    )
    assert parsed["first_token_s"] == 3
    assert parsed["completion_tokens"] == 128


def test_incomplete_sse_after_deadline_is_never_success():
    case = gate.DEFAULT_CASES[0]
    result = exchange({"content": "partial", "tokens": [1]}, wall_s=120.1, timed_out=True)
    verdict = gate.assess_case(case, result, protocol())
    assert not verdict["qualified"]
    assert {"incomplete_response", "total_limit", "forced_decode_incomplete"} <= set(
        verdict["errors"]
    )


def test_full_forced_decode_passes_but_early_eos_fails():
    case = gate.DEFAULT_CASES[0]
    result = exchange(
        {"content": "hi"},
        {
            "stop": True,
            "tokens_predicted": 128,
            "tokens_evaluated": 600,
            "timings": {"cache_n": 0},
        },
    )
    assert gate.assess_case(case, result, protocol())["qualified"]
    result["events"][-1]["body"]["tokens_predicted"] = 3
    assert not gate.assess_case(case, result, protocol())["qualified"]


@pytest.mark.parametrize(
    "models,qualified",
    [
        ([], False),
        (["wrong-model"], False),
        (["synthetic-model"], True),
        (["wrong-model", "synthetic-model"], False),
        (["synthetic-model", "wrong-model"], False),
        (["synthetic-model", "synthetic-model"], True),
        ([None, "synthetic-model", None], True),
        ([[], "synthetic-model"], False),
        ([{}, "synthetic-model"], False),
        ([0, "synthetic-model"], False),
        (["", "synthetic-model"], False),
    ],
)
def test_every_observed_model_identity_must_match_declared_model(models, qualified):
    chunks = [
        {"content": "hi"},
        {"stop": True, "tokens_predicted": 128, "tokens_evaluated": 600, "timings": {"cache_n": 0}},
    ]
    chunks.extend({"model": model} for model in models)
    verdict = gate.assess_case(gate.DEFAULT_CASES[0], exchange(*chunks, model=None), protocol())
    assert verdict["qualified"] is qualified
    assert ("served_model_mismatch" in verdict["errors"]) is (not qualified)


@pytest.mark.parametrize("cache_n,prompt_tokens", [(1, 600), (0, 599)])
def test_warm_or_wrong_length_prefill_cannot_pass_cold_case(cache_n, prompt_tokens):
    result = exchange(
        {"content": "hi"},
        {
            "stop": True,
            "tokens_predicted": 128,
            "tokens_evaluated": prompt_tokens,
            "timings": {"cache_n": cache_n},
        },
    )
    assert not gate.assess_case(gate.DEFAULT_CASES[0], result, protocol())["qualified"]


@pytest.mark.parametrize("server_accepted", [True, False])
def test_complete_matrix_requires_server_acceptance_before_cancellation(tmp_path, server_accepted):
    names = []

    def call(out, name, url, **kwargs):
        names.append(name)
        if url.endswith("/v1/models"):
            return response(name, {"data": [{"id": "synthetic-model"}]})
        if url.endswith("/tokenize"):
            return response(name, {"tokens": list(range(2500))})
        if url.endswith("/apply-template"):
            return response(name, {"prompt": "synthetic rendered template"})
        if name == "cancel":
            event = (
                {"event": "headers", "status": 200}
                if server_accepted
                else {"event": "request_started"}
            )
            return {"timed_out": True, "events": [event]}
        if url.endswith("/completion"):
            body = kwargs["body"]
            assert body["cache_prompt"] is False
            return exchange(
                {"content": "hi"},
                {
                    "stop": True,
                    "tokens_predicted": body["n_predict"],
                    "tokens_evaluated": len(body["prompt"]),
                    "timings": {"cache_n": 0},
                },
            )
        if name == "planner":
            return exchange(
                {
                    "choices": [
                        {
                            "delta": {"content": '{"tasks":["Create hello.txt"]}'},
                            "finish_reason": "stop",
                        }
                    ]
                }
            )
        if name == "native_action":
            body = kwargs["body"]
            assert len(body["tools"]) >= 6
            assert body["tool_choice"] == "auto"
            return exchange(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {
                                            "name": "write",
                                            "arguments": json.dumps(
                                                {"arg": "hello.txt", "content": "hi\n"}
                                            ),
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            )
        return response(name, {"status": "ok"})

    result = gate.run(
        protocol(),
        tmp_path / "qualification",
        call=call,
        idle=lambda *args, **kwargs: {"idle": True, "wall_s": 0.1},
    )
    assert len(result["results"]) == len(gate.DEFAULT_CASES)
    assert all(item["qualified"] for item in result["results"])
    assert result["qualified"] is server_accepted
    assert names.count("cancel") == 1


def test_busy_slot_after_failed_probe_stops_further_inference(tmp_path):
    names = []
    barriers = iter([{"idle": True}, {"idle": False}])

    def call(out, name, url, **kwargs):
        names.append(name)
        if url.endswith("/v1/models"):
            return response(name, {"data": [{"id": "synthetic-model"}]})
        if url.endswith("/tokenize"):
            return response(name, {"tokens": list(range(2500))})
        if url.endswith("/completion"):
            return exchange({"content": "partial"}, timed_out=True, wall_s=120.1)
        return response(name, {"status": "ok"})

    result = gate.run(
        protocol(),
        tmp_path / "qualification",
        call=call,
        idle=lambda *args, **kwargs: next(barriers),
    )
    assert not result["qualified"]
    assert len(result["results"]) == 1
    assert "no further inference" in result["reason"]
    assert "prefill2000_decode128" not in names
    assert "cancel" not in names


def test_malformed_plan_is_retained_as_failure():
    case = gate.DEFAULT_CASES[-2]
    result = exchange(
        {"choices": [{"delta": {"content": '{"tasks":[null]}'}, "finish_reason": "stop"}]}
    )
    verdict = gate.assess_case(case, result, protocol())
    assert not verdict["qualified"]
    assert any("contract_error" in error for error in verdict["errors"])


@pytest.mark.parametrize("finish_reason,qualified", [("length", False), ("stop", True)])
def test_cutoff_plan_cannot_be_repaired_as_a_natural_stop(finish_reason, qualified):
    result = exchange(
        {
            "choices": [
                {
                    "delta": {"content": '{"tasks":["Create hello.txt"]'},
                    "finish_reason": finish_reason,
                }
            ]
        }
    )
    verdict = gate.assess_case(gate.DEFAULT_CASES[-2], result, protocol())
    assert verdict["finish_reason"] == finish_reason
    assert verdict["qualified"] is qualified
    if not qualified:
        assert any("contract_error" in error for error in verdict["errors"])


def test_oai_usage_only_chunk_supplies_prompt_tokens_without_changing_content():
    parsed = gate.summarize_stream(
        exchange(
            {
                "choices": [
                    {
                        "delta": {"content": '{"tasks":["Create hello.txt"]}'},
                        "finish_reason": "stop",
                    }
                ]
            },
            {"choices": [], "usage": {"prompt_tokens": 617, "completion_tokens": 12}},
            {"choices": [], "usage": None},
        )
    )
    assert parsed["prompt_tokens"] == 617
    assert parsed["completion_tokens"] == 12
    assert parsed["message"]["content"] == '{"tasks":["Create hello.txt"]}'
    assert parsed["finish_reason"] == "stop"


def test_busy_server_blocks_all_generation_after_registration(tmp_path):
    output = tmp_path / "qualification"
    calls = []

    def call(out, name, url, **kwargs):
        assert (out / "registration.json").exists()
        assert (out / "started.json").exists()
        calls.append(url)
        if url.endswith("/v1/models"):
            return response(name, {"data": [{"id": "synthetic-model"}]})
        return response(name, {"status": "ok"})

    result = gate.run(protocol(), output, call=call, idle=lambda *args, **kwargs: {"idle": False})
    assert not result["qualified"]
    assert "busy" in result["reason"]
    assert len(calls) == 3
    assert not any("completion" in url for url in calls)
    saved = json.loads((output / "qualification.json").read_text())
    assert saved == result
    with pytest.raises(FileExistsError):
        gate.run(protocol(), output, call=call)


@pytest.mark.parametrize("payload", [[], {}, [{"id": 0}], [{"is_processing": True}], None])
def test_unknown_or_busy_slot_status_is_not_idle(payload):
    assert not gate.slots_idle(payload)


def test_idle_barrier_uses_remaining_deadline_and_exits_when_recovered(tmp_path):
    now = [0.0]
    limits = []

    def call(out, name, url, **kwargs):
        limits.append(kwargs["timeout"])
        now[0] += 2
        return response(name, [{"is_processing": len(limits) == 1}])

    result = gate.wait_idle(
        tmp_path,
        "idle",
        "http://localhost:8080",
        10,
        call=call,
        clock=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    assert result["idle"]
    assert limits == pytest.approx([10, 7.8])
    assert result["wall_s"] == pytest.approx(4.2)


def test_hard_deadline_kills_http_worker_and_keeps_partial_events(tmp_path, monkeypatch):
    """A stuck/trickling HTTP exchange cannot outrun the parent wall budget."""

    class Process:
        returncode = -9

        def __init__(self, argv, **kwargs):
            self.calls = 0
            self.killed = False

        def communicate(self, input=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                request = json.loads(input)
                Path(request["events"]).write_text(
                    json.dumps(
                        {
                            "event": "chunk",
                            "body": {"content": "partial"},
                            "wall_s": 0.1,
                        }
                    )
                    + "\n"
                )
                raise gate.subprocess.TimeoutExpired("worker", timeout)
            assert self.killed
            assert timeout == 2
            return b"", b""

        def kill(self):
            self.killed = True

    monkeypatch.setattr(gate.subprocess, "Popen", Process)
    result = gate.isolated_http(tmp_path, "probe", "http://localhost:8080/completion", timeout=0.2)
    assert result["timed_out"]
    assert result["events"][0]["body"]["content"] == "partial"
    assert json.loads((tmp_path / "probe.result.json").read_text()) == result
