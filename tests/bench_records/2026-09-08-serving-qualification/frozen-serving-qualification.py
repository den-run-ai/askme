#!/usr/bin/env python3
"""One registered, task-independent llama.cpp serving qualification.

Run: python tests/serving_qualification.py --protocol protocol.json --output NEW_DIR
The protocol names protocol/api/model and optional provenance/expected_model_path.
Every HTTP exchange and failure is retained. No actions or task work are executed.
"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_CASES = [
    {"name": "prefill600_decode128", "kind": "completion", "prompt_tokens": 600, "max_tokens": 128},
    {
        "name": "prefill2000_decode128",
        "kind": "completion",
        "prompt_tokens": 2000,
        "max_tokens": 128,
    },
    {"name": "prefill600_decode512", "kind": "completion", "prompt_tokens": 600, "max_tokens": 512},
    {
        "name": "prefill2000_decode512",
        "kind": "completion",
        "prompt_tokens": 2000,
        "max_tokens": 512,
    },
    {
        "name": "prefill2000_decode1024",
        "kind": "completion",
        "prompt_tokens": 2000,
        "max_tokens": 1024,
    },
    {"name": "planner", "kind": "plan", "prompt_tokens": 600, "max_tokens": 384},
    {"name": "native_action", "kind": "action", "prompt_tokens": 2000, "max_tokens": 512},
]
SYNTHETIC = "Synthetic observation: the sample directory contains alpha.txt and beta.txt. "


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_protocol(protocol):
    """Freeze defaults before calls; only credential-free loopback endpoints apply."""
    resolved = dict(protocol)
    for key in ("protocol", "api", "model"):
        if not isinstance(resolved.get(key), str) or not resolved[key].strip():
            raise ValueError(f"Missing {key}")
    parsed = urlparse(resolved["api"])
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path != "/v1/chat/completions"
    ):
        raise ValueError("Qualification requires a credential-free loopback chat-completions URL")
    # Cases are fixed, so a weaker manifest cannot accidentally certify a deployment.
    if "cases" in resolved and resolved["cases"] != DEFAULT_CASES:
        raise ValueError("This qualification version uses the fixed DEFAULT_CASES")
    resolved["cases"] = DEFAULT_CASES
    resolved.setdefault("first_token_limit_s", 30)
    resolved.setdefault("total_limit_s", 120)
    resolved.setdefault("cancel_after_s", 1)
    resolved.setdefault("idle_limit_s", 10)
    for key in ("first_token_limit_s", "total_limit_s", "cancel_after_s", "idle_limit_s"):
        if not isinstance(resolved[key], (int, float)) or not 0 < resolved[key] <= 180:
            raise ValueError(f"Invalid {key}")
    resolved["trials_per_case"] = 1
    resolved["scope"] = "Task-independent serving gate; no reliability or causal harness claim"
    return resolved


def http_worker(config):
    """A disposable process owns the socket so an outer deadline closes it."""
    started = time.monotonic()
    events = Path(config["events"])

    def emit(value):
        value["wall_s"] = time.monotonic() - started
        with events.open("a") as stream:
            stream.write(json.dumps(value) + "\n")

    emit({"event": "request_started"})
    try:
        with requests.request(
            config["method"],
            config["url"],
            json=config.get("body"),
            stream=config.get("stream", False),
            timeout=(2, config["timeout"]),
        ) as response:
            emit({"event": "headers", "status": response.status_code})
            response.raise_for_status()
            if config.get("stream"):
                for line in response.iter_lines(chunk_size=1):
                    if not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if data == b"[DONE]":
                        emit({"event": "done"})
                    else:
                        emit({"event": "chunk", "body": json.loads(data)})
            else:
                emit({"event": "response", "body": response.json()})
            emit({"event": "finished"})
    except Exception as error:
        emit({"event": "error", "type": type(error).__name__, "detail": str(error)})


def isolated_http(output, name, url, *, body=None, stream=False, timeout=10):
    """Hard deadline includes connection, prefill, trickled bytes and body parsing."""
    request = {
        "method": "GET" if body is None else "POST",
        "url": url,
        "body": body,
        "stream": stream,
        "timeout": timeout,
        "events": str(output / f"{name}.events.jsonl"),
    }
    save(output / f"{name}.request.json", request)
    started = time.monotonic()
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--http-worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    timed_out = False
    try:
        _, stderr = process.communicate(json.dumps(request).encode(), timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        _, stderr = process.communicate(timeout=2)
    events = []
    path = Path(request["events"])
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                events.append({"event": "incomplete_event", "raw": line})
    result = {
        "name": name,
        "wall_s": time.monotonic() - started,
        "timed_out": timed_out,
        "exit_code": process.returncode,
        "stderr": stderr.decode(errors="replace"),
        "events": events,
    }
    save(output / f"{name}.result.json", result)
    return result


def response_body(result):
    for event in result["events"]:
        if event["event"] == "response":
            return event["body"]
    raise ValueError(f"No complete HTTP response for {result['name']}")


def slots_idle(payload):
    return (
        bool(payload)
        and isinstance(payload, list)
        and all(isinstance(slot, dict) and slot.get("is_processing") is False for slot in payload)
    )


def wait_idle(
    output, name, base, limit, *, call=isolated_http, clock=time.monotonic, sleep=time.sleep
):
    started = clock()
    polls = 0
    while clock() - started < limit:
        polls += 1
        remaining = limit - (clock() - started)
        result = call(output, f"{name}-{polls}", base + "/slots", timeout=remaining)
        try:
            idle = slots_idle(response_body(result))
        except ValueError:
            idle = False
        if idle and clock() - started <= limit:
            return {"idle": True, "wall_s": clock() - started, "polls": polls}
        sleep(min(0.2, max(0, limit - (clock() - started))))
    return {"idle": False, "wall_s": clock() - started, "polls": polls}


def summarize_stream(result):
    """Reconstruct native/OAI SSE, retaining tool argument fragments verbatim."""
    text = ""
    tools = {}
    first_token = None
    completion_tokens = None
    timings = None
    terminal = False
    finish_reason = None
    model = None
    prompt_tokens = None
    for event in result["events"]:
        if event["event"] == "done":
            terminal = True
        if event["event"] != "chunk":
            continue
        chunk = event["body"]
        if chunk.get("model"):
            model = chunk["model"]
        choices = chunk.get("choices") or []
        delta = choices[0].get("delta", {}) if choices else {}
        content = chunk.get("content") or delta.get("content") or ""
        fragments = delta.get("tool_calls") or []
        if first_token is None and (content or fragments or chunk.get("tokens")):
            first_token = event["wall_s"]
        text += content
        for fragment in fragments:
            entry = tools.setdefault(
                fragment.get("index", 0),
                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
            )
            if fragment.get("id"):
                entry["id"] = fragment["id"]
            for field in ("name", "arguments"):
                entry["function"][field] += fragment.get("function", {}).get(field) or ""
        if choices and choices[0].get("finish_reason"):
            finish_reason = choices[0]["finish_reason"]
            terminal = True
        if chunk.get("stop") is True:
            terminal = True
            finish_reason = chunk.get("stop_type")
        if chunk.get("timings"):
            timings = chunk["timings"]
            completion_tokens = timings.get("predicted_n", completion_tokens)
        completion_tokens = (chunk.get("usage") or {}).get("completion_tokens", completion_tokens)
        completion_tokens = chunk.get("tokens_predicted", completion_tokens)
        prompt_tokens = chunk.get("tokens_evaluated", prompt_tokens)
    return {
        "first_token_s": first_token,
        "terminal": terminal,
        "finish_reason": finish_reason,
        "completion_tokens": completion_tokens,
        "timings": timings,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "message": {"role": "assistant", "content": text, "tool_calls": list(tools.values())},
    }


def assess_case(case, result, protocol):
    import askme

    parsed = summarize_stream(result)
    errors = []
    if result["timed_out"] or not parsed["terminal"]:
        errors.append("incomplete_response")
    if any(event["event"] == "error" for event in result["events"]):
        errors.append("transport_error")
    if parsed["first_token_s"] is None or parsed["first_token_s"] > protocol["first_token_limit_s"]:
        errors.append("first_token_limit")
    if result["wall_s"] > protocol["total_limit_s"]:
        errors.append("total_limit")
    if parsed["model"] and parsed["model"] != protocol["model"]:
        errors.append("served_model_mismatch")
    if case["kind"] == "completion":
        if parsed["completion_tokens"] != case["max_tokens"]:
            errors.append("forced_decode_incomplete")
        if parsed["prompt_tokens"] != case["prompt_tokens"]:
            errors.append("prompt_token_count_mismatch")
        if (parsed["timings"] or {}).get("cache_n") != 0:
            errors.append("cold_prefill_not_confirmed")
    else:
        try:
            if case["kind"] == "plan":
                plan, _, _ = askme._decode_action_reply(parsed["message"]["content"], "stop")
                if not isinstance(plan.get("tasks"), list) or not 1 <= len(plan["tasks"]) <= 3:
                    raise ValueError("Plan must contain one to three tasks")
                if any(not isinstance(task, str) or not task.strip() for task in plan["tasks"]):
                    raise ValueError("Plan tasks must be nonempty strings")
            else:
                reply = {"choices": [{"message": parsed["message"]}]}
                action, _, _ = askme._decode_tool_call_reply(reply, parsed["finish_reason"])
                if action.get("action") != "write" or action.get("arg") != "hello.txt":
                    raise ValueError("Expected synthetic write hello.txt")
                if action.get("content") != "hi\n":
                    raise ValueError("Expected the exact synthetic file content")
        except (ValueError, TypeError, KeyError) as error:
            errors.append(f"contract_error: {error}")
    return {"name": case["name"], "qualified": not errors, "errors": errors, **parsed}


def run(protocol, output, *, call=isolated_http, idle=wait_idle):
    """Register before the first call, retain failures, stop if cancellation leaves work busy."""
    protocol = resolve_protocol(protocol)
    output.mkdir(parents=True, exist_ok=False)
    save(
        output / "registration.json",
        {
            "protocol": protocol,
            "registered_at_unix": time.time(),
            "runner_sha256": digest(Path(__file__)),
            "runtime_sha256": {name: digest(ROOT / name) for name in ("askme.py", "actions.py")},
            "python": sys.version,
        },
    )
    with (output / "started.json").open("x") as marker:
        json.dump({"started_at_unix": time.time(), "trials_per_case": 1}, marker)
    result = {
        "qualified": False,
        "protocol": protocol["protocol"],
        "model_alias": protocol["model"],
        "api_url": protocol["api"],
        "registration_sha256": digest(output / "registration.json"),
        "results": [],
        "reason": "incomplete",
    }
    base = protocol["api"].removesuffix("/v1/chat/completions")
    try:
        for endpoint in ("health", "props", "v1/models"):
            payload = response_body(call(output, endpoint.replace("/", "-"), base + "/" + endpoint))
            if endpoint == "props" and protocol.get("expected_model_path"):
                if payload.get("model_path") != protocol["expected_model_path"]:
                    raise ValueError("Server model_path differs from declared artifact")
            if endpoint == "v1/models" and protocol["model"] not in {
                model.get("id") for model in payload.get("data", [])
            }:
                raise ValueError("Declared alias absent from served models")
        barrier = idle(output, "initial-idle", base, protocol["idle_limit_s"], call=call)
        if not barrier["idle"]:
            raise ValueError("Server is busy before qualification")
        synthetic = SYNTHETIC * 1000
        save(output / "synthetic.json", {"text": synthetic})
        tokenized = response_body(
            call(
                output,
                "tokenize",
                base + "/tokenize",
                body={
                    "content": synthetic,
                    "add_special": True,
                },
            )
        )
        tokens = tokenized["tokens"]
        if len(tokens) < 2000:
            raise ValueError("Synthetic input did not tokenize to 2000 tokens")
        import askme

        for case in protocol["cases"]:
            if case["kind"] == "completion":
                url = base + "/completion"
                body = {
                    "model": protocol["model"],
                    "prompt": tokens[: case["prompt_tokens"]],
                    "n_predict": case["max_tokens"],
                    "ignore_eos": True,
                    "cache_prompt": False,
                    "stream": True,
                    "temperature": 0.1,
                    "seed": 20260908,
                }
            else:
                url = protocol["api"]
                if case["kind"] == "plan":
                    system = askme.SYSTEM_PLAN
                    instruction = "Plan creating hello.txt containing hi and checking its content."
                else:
                    system = askme.SYSTEM_STEP
                    instruction = (
                        "Call write with arg hello.txt and content exactly hi followed by newline."
                    )
                # Include actual registry schemas; tokenizer records the full rendered prompt.
                body, _, _ = askme._build_llm_request(
                    [
                        {"role": "system", "content": system},
                        {
                            "role": "user",
                            "content": instruction
                            + "\n"
                            + SYNTHETIC * (case["prompt_tokens"] // 20),
                        },
                    ],
                    case["max_tokens"],
                    None,
                    False,
                    settings=askme.LLMSettings(
                        backend="local",
                        api=protocol["api"],
                        model=protocol["model"],
                        api_key="",
                        provider="",
                        allow_fallbacks=False,
                        require_parameters=False,
                        reasoning_effort="",
                        timeout=protocol["total_limit_s"],
                    ),
                    expect=case["kind"],
                )
                body.update(
                    stream=True,
                    stream_options={"include_usage": True},
                    seed=20260908,
                    cache_prompt=False,
                )
                rendered = response_body(
                    call(output, case["name"] + "-template", base + "/apply-template", body=body)
                )
                prompt = rendered.get("prompt")
                if not isinstance(prompt, str):
                    raise ValueError("Cannot record the full chat template token count")
                measured = response_body(
                    call(
                        output,
                        case["name"] + "-tokens",
                        base + "/tokenize",
                        body={"content": prompt, "add_special": False},
                    )
                )
                save(
                    output / (case["name"] + "-prompt.json"),
                    {"prompt": prompt, "tokens": len(measured["tokens"])},
                )
            exchange = call(
                output, case["name"], url, body=body, stream=True, timeout=protocol["total_limit_s"]
            )
            verdict = assess_case(case, exchange, protocol)
            verdict["wall_s"] = exchange["wall_s"]
            result["results"].append(verdict)
            save(output / "qualification.json", result)
            barrier = idle(
                output, case["name"] + "-idle", base, protocol["idle_limit_s"], call=call
            )
            verdict["idle_after"] = barrier
            if not barrier["idle"]:
                raise ValueError("Server did not become idle; no further inference submitted")
        cancel = call(
            output,
            "cancel",
            base + "/completion",
            body={
                "model": protocol["model"],
                "prompt": tokens[:2000],
                "n_predict": 1024,
                "ignore_eos": True,
                "cache_prompt": False,
                "stream": True,
                "temperature": 0.1,
                "seed": 20260908,
            },
            stream=True,
            timeout=protocol["cancel_after_s"],
        )
        recovery = idle(output, "cancel-idle", base, protocol["idle_limit_s"], call=call)
        cancellation_observed = cancel["timed_out"] and any(
            event["event"] == "chunk"
            or (event["event"] == "headers" and event.get("status") == 200)
            for event in cancel["events"]
        )
        result["cancellation"] = {
            "request_interrupted": cancellation_observed,
            "acceptance_evidence_required": "Successful server headers or streamed event before kill",
            **recovery,
        }
        result["qualified"] = (
            all(item["qualified"] for item in result["results"])
            and cancellation_observed
            and recovery["idle"]
        )
        result["reason"] = (
            "all_serving_checks_passed" if result["qualified"] else "serving_checks_failed"
        )
    except (ValueError, KeyError, TypeError, OSError) as error:
        result["reason"] = str(error)
    finally:
        save(output / "qualification.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--http-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.http_worker:
        http_worker(json.load(sys.stdin))
        return 0
    if args.protocol is None or args.output is None:
        parser.error("--protocol and --output are required")
    result = run(json.loads(args.protocol.read_text()), args.output.resolve())
    print(json.dumps(result, indent=2))
    return 0 if result["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
