#!/usr/bin/env python3
"""CI gate for the macOS Apple Silicon / llama.cpp local backend.

Used by .github/workflows/macos.yml. Four subcommands:

  hardware    Record the runner's chip, core count, and physical memory, and
              fail when it is smaller than --min-memory-gb or (with
              --require-apple-silicon) is not arm64 Darwin. The reference
              development machine is an M1 with 16 GB; GitHub-hosted runners
              are smaller, so a lane that needs headroom must assert it
              rather than silently thrash or get OOM-killed mid-run.

  preflight   Fail loudly when llama-server is not reachable or does not
              speak the OpenAI-compatible chat-completions contract AskMe
              uses. tests/conftest.py *skips* local integration tests when
              :8080 is absent; in CI that silent skip would make a broken
              backend look green, so this asserts the server first.

  probe       One native tool-call round trip through the real
              askme.LLMClient (expect="action"). This checks the transport
              seam — tools payload accepted, structured tool call returned,
              envelope decoded — not the model's competence. Advisory by
              default because a CI-sized model on a CI-sized runner is not
              the reference deployment; --strict makes it blocking.

  report      Render the JSON records the subcommands above emit into a
              markdown table for GITHUB_STEP_SUMMARY.

Nothing here is a performance measurement. CI runners are smaller and
slower than the documented M1/16 GB reference deployment, and the model
served in CI is deliberately tiny, so timings produced under this gate must
never be written into docs/PERFORMANCE.md or cited as a local baseline.
"""

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

# Allow `import askme` when invoked as `python tests/ci_local_gate.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_API = "http://localhost:8080/v1/chat/completions"
BYTES_PER_GB = 1024**3

# The documented reference deployment (docs/gemma4-setup.md): an M1 with
# 16 GB running Gemma 4 E4B QAT Q4_0 (5.15 GB) at --ctx-size 16384. No
# GitHub-hosted runner reaches 16 GB; the largest Apple Silicon option is
# macos-*-xlarge at 14 GB, which still fits that model plus its KV cache.
REFERENCE_MEMORY_GB = 16


def _sysctl(name: str, run: Callable[..., Any] | None = None) -> str:
    """Return one sysctl value, or "" when unavailable (non-macOS included)."""
    runner = subprocess.run if run is None else run
    try:
        proc = runner(
            ["sysctl", "-n", name],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return ""
    if getattr(proc, "returncode", 1) != 0:
        return ""
    return (getattr(proc, "stdout", "") or "").strip()


# --- hardware ---


def collect_hardware(run: Callable[..., Any] | None = None, env=None) -> dict[str, Any]:
    """Return a credential-free description of the runner's hardware."""
    env = os.environ if env is None else env
    raw_memory = _sysctl("hw.memsize", run=run)
    try:
        memory_bytes = int(raw_memory)
    except ValueError:
        memory_bytes = 0
    try:
        cpus = int(_sysctl("hw.ncpu", run=run) or 0)
    except ValueError:
        cpus = 0
    return {
        "system": platform.system(),
        "machine": platform.machine(),
        "release": platform.release(),
        "chip": _sysctl("machdep.cpu.brand_string", run=run),
        "hw_model": _sysctl("hw.model", run=run),
        "cpus": cpus,
        "memory_bytes": memory_bytes,
        "memory_gb": round(memory_bytes / BYTES_PER_GB, 2) if memory_bytes else 0.0,
        "runner_label": env.get("ASKME_RUNNER_LABEL", ""),
        "reference_memory_gb": REFERENCE_MEMORY_GB,
    }


def check_hardware(
    hardware: dict[str, Any],
    min_memory_gb: float = 0.0,
    require_apple_silicon: bool = False,
) -> tuple[bool, str]:
    """Return (ok, message) for the collected hardware against the lane's floor."""
    if require_apple_silicon:
        if hardware.get("system") != "Darwin":
            return False, "not macOS: system={!r}".format(hardware.get("system"))
        if hardware.get("machine") != "arm64":
            return False, "not Apple Silicon: machine={!r}".format(hardware.get("machine"))
    memory_gb = float(hardware.get("memory_gb") or 0.0)
    if min_memory_gb and memory_gb < min_memory_gb:
        return False, (
            "runner has {:.2f} GB, below the {:.2f} GB this lane requires. "
            "GitHub-hosted Apple Silicon tops out at 14 GB (macos-*-xlarge, "
            "org-owned repositories only); 16 GB+ parity with the reference "
            "M1 needs a self-hosted or third-party arm64 runner.".format(
                memory_gb, float(min_memory_gb)
            )
        )
    message = "chip={!r} cpus={} memory={:.2f} GB".format(
        hardware.get("chip") or hardware.get("machine"),
        hardware.get("cpus"),
        memory_gb,
    )
    # Passing a lane's floor is not the same as matching the reference
    # machine. Say so on every run, so a green 14 GB result is never read
    # back as a 16 GB reference result.
    if memory_gb and memory_gb < REFERENCE_MEMORY_GB:
        message += "; below the {} GB reference deployment — results are not comparable".format(
            REFERENCE_MEMORY_GB
        )
    return True, message


# --- preflight ---


def check_llama_server(
    api: str = DEFAULT_API,
    get: Callable[..., Any] | None = None,
    post: Callable[..., Any] | None = None,
    timeout: int = 30,
) -> tuple[bool, str, dict[str, Any]]:
    """Return (ok, message, record) for a reachable, OpenAI-compatible server.

    Mirrors tests/conftest.py's availability probe (``/health``) and then
    goes one step further: a server that answers ``/health`` but rejects the
    chat-completions body AskMe sends is a broken backend, not a healthy one.
    """
    base = api.rstrip("/").removesuffix("/v1/chat/completions")
    record: dict[str, Any] = {"api": api, "base_url": base}
    if get is None or post is None:
        import requests

        get = requests.get if get is None else get
        post = requests.post if post is None else post

    try:
        health = get(base + "/health", timeout=timeout)
    except Exception as exc:
        record["health_error"] = repr(exc)
        return False, "llama-server unreachable at {}/health: {!r}".format(base, exc), record
    health_status = getattr(health, "status_code", None)
    record["health_status"] = health_status
    if health_status != 200:
        return False, "llama-server /health returned HTTP {}".format(health_status), record

    try:
        props = get(base + "/props", timeout=timeout)
        if getattr(props, "status_code", None) == 200:
            body = props.json()
            record["served_model"] = body.get("model_path") or body.get(
                "default_generation_settings", {}
            ).get("model", "")
            record["chat_template_tools"] = bool(body.get("chat_template_tool_use"))
    except Exception:  # /props is informational; its absence is not a failure.
        pass

    try:
        resp = post(
            api,
            json={
                "model": "local-model",
                "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
                "temperature": 0.1,
                "max_tokens": 16,
            },
            timeout=timeout,
        )
    except Exception as exc:
        record["chat_error"] = repr(exc)
        return False, "chat-completions request failed: {!r}".format(exc), record
    chat_status = getattr(resp, "status_code", None)
    record["chat_status"] = chat_status
    if chat_status != 200:
        return False, "chat-completions returned HTTP {}".format(chat_status), record
    try:
        payload = resp.json()
        message = payload["choices"][0]["message"]
    except Exception as exc:
        record["chat_error"] = repr(exc)
        return False, "chat-completions body was not an OpenAI reply: {!r}".format(exc), record
    record["chat_ok"] = True
    record["served_model_id"] = payload.get("model", "")
    return (
        True,
        "llama-server healthy and speaking chat-completions (role={!r})".format(
            message.get("role")
        ),
        record,
    )


# --- probe ---

PROBE_TASK = (
    "task: create hello.txt containing exactly: hi\n"
    "state: {'files': [], 'no_write_executed': true}\n"
    "Call exactly one tool now."
)


def probe_action_transport(
    api: str = DEFAULT_API,
    model: str = "local-model",
    timeout: int = 180,
    client_factory: Callable[..., Any] | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Round-trip one ``expect="action"`` call through the real LLMClient.

    A pass means the server accepted the registry-derived tool definitions
    with ``tool_choice: auto`` and returned a structured tool call that
    AskMe's envelope validation decoded. Which action the model picked is
    not asserted — that is model competence, not the transport contract.
    """
    record: dict[str, Any] = {"api": api, "model": model}
    import askme

    if client_factory is None:

        def client_factory(**kwargs):
            return askme.LLMClient(settings=askme.LLMSettings(**kwargs))

    client = client_factory(
        backend="local",
        api=api,
        model=model,
        api_key="",
        provider="",
        allow_fallbacks=True,
        require_parameters=False,
        reasoning_effort="",
        timeout=timeout,
    )
    try:
        action = client.ask(
            [
                {"role": "system", "content": askme.SYSTEM_STEP},
                {"role": "user", "content": PROBE_TASK},
            ],
            max_tokens=256,
            expect="action",
            reasoning_policy="off",
            reasoning_trigger="ci_local_probe",
        )
    except Exception as exc:
        record["error"] = repr(exc)
        return False, "action round trip raised {!r}".format(exc), record
    # expect="action" yields actions.DecodedAction, which is mapping-like
    # (get/keys/items) but is not a dict subclass.
    getter = getattr(action, "get", None)
    action_type = getter("action") if callable(getter) else None
    record["decoded_action"] = action_type
    if not action_type:
        return False, "no action decoded from the tool call", record
    return True, "decoded a native tool call (action={!r})".format(action_type), record


# --- report ---


def render_markdown(records: list[tuple[str, dict[str, Any]]]) -> str:
    """Render collected gate records as a GITHUB_STEP_SUMMARY table."""
    lines = [
        "## macOS local-backend gate",
        "",
        "| Check | Result | Detail |",
        "| --- | --- | --- |",
    ]
    for name, record in records:
        ok = record.get("ok")
        status = "pass" if ok else ("advisory" if record.get("advisory") else "fail")
        detail = str(record.get("message", "")).replace("|", "\\|").replace("\n", " ")
        lines.append("| {} | {} | {} |".format(name, status, detail))
    lines.extend(
        [
            "",
            "Contract smoke only — CI runners are smaller than the documented "
            "M1/16 GB reference deployment and serve a deliberately tiny model. "
            "These runs are not performance evidence and must not be cited in "
            "docs/PERFORMANCE.md.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_json(path: str, payload: dict[str, Any]) -> None:
    if not path:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _append_markdown(path: str, markdown: str) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(markdown)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    hardware = sub.add_parser("hardware", help="record and gate the runner's hardware")
    hardware.add_argument("--min-memory-gb", type=float, default=0.0)
    hardware.add_argument("--require-apple-silicon", action="store_true")
    hardware.add_argument("--json-out", default="")
    hardware.add_argument("--markdown-out", default="")

    preflight = sub.add_parser("preflight", help="fail unless llama-server answers")
    preflight.add_argument("--api", default=os.environ.get("LLM_API_URL", DEFAULT_API))
    preflight.add_argument("--timeout", type=int, default=30)
    preflight.add_argument("--json-out", default="")
    preflight.add_argument("--markdown-out", default="")

    probe = sub.add_parser("probe", help="round-trip one native tool call")
    probe.add_argument("--api", default=os.environ.get("LLM_API_URL", DEFAULT_API))
    probe.add_argument("--model", default=os.environ.get("LLM_MODEL", "local-model"))
    probe.add_argument("--timeout", type=int, default=180)
    probe.add_argument(
        "--strict",
        action="store_true",
        help="exit nonzero when no action decodes (default: advisory)",
    )
    probe.add_argument("--json-out", default="")
    probe.add_argument("--markdown-out", default="")

    args = parser.parse_args(argv)

    if args.command == "hardware":
        collected = collect_hardware()
        ok, message = check_hardware(
            collected,
            min_memory_gb=args.min_memory_gb,
            require_apple_silicon=args.require_apple_silicon,
        )
        record = dict(collected, ok=ok, message=message)
        _write_json(args.json_out, record)
        _append_markdown(args.markdown_out, render_markdown([("hardware", record)]))
        print(("HARDWARE OK: " if ok else "HARDWARE FAILED: ") + message)
        return 0 if ok else 1

    if args.command == "preflight":
        ok, message, record = check_llama_server(api=args.api, timeout=args.timeout)
        record = dict(record, ok=ok, message=message)
        _write_json(args.json_out, record)
        _append_markdown(args.markdown_out, render_markdown([("llama-server", record)]))
        print(("PREFLIGHT OK: " if ok else "PREFLIGHT FAILED: ") + message)
        return 0 if ok else 1

    ok, message, record = probe_action_transport(
        api=args.api, model=args.model, timeout=args.timeout
    )
    record = dict(record, ok=ok, message=message, advisory=not args.strict)
    _write_json(args.json_out, record)
    _append_markdown(args.markdown_out, render_markdown([("tool-call transport", record)]))
    print(("PROBE OK: " if ok else "PROBE FAILED: ") + message)
    if ok:
        return 0
    if args.strict:
        return 1
    print("PROBE ADVISORY: transport probe failed but this lane is advisory.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
