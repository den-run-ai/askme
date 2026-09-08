"""Shared mock helpers and integration test runners for agent tests."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_NO_JSON = object()


class FakeHttpResponse:
    """Strict requests.Response subset used by deterministic provider tests."""

    __slots__ = ("_json_body", "status_code", "text")

    def __init__(
        self, *, status_code: int = 200, json_body: Any = _NO_JSON, text: str = ""
    ) -> None:
        self.status_code = status_code
        self._json_body = json_body
        self.text = text

    def json(self) -> Any:
        if self._json_body is _NO_JSON:
            raise ValueError("not json")
        return self._json_body


def mock_http_response(
    *, status_code: int = 200, json_body: Any = _NO_JSON, text: str = ""
) -> FakeHttpResponse:
    """Build a response for JSON, non-JSON, and HTTP-error transport cases."""
    return FakeHttpResponse(status_code=status_code, json_body=json_body, text=text)


def mock_response(
    content: Any,
    *,
    finish_reason: str | None = None,
    usage: dict[str, Any] | None = None,
) -> FakeHttpResponse:
    """Create a chat-completion response whose content is JSON encoded."""
    return mock_response_raw(json.dumps(content), finish_reason=finish_reason, usage=usage)


def mock_llm_response(
    reply: Any,
    *,
    finish_reason: str | None = None,
    usage: dict[str, Any] | None = None,
) -> FakeHttpResponse:
    """Shape a scripted reply the way the wire now carries it.

    An action-shaped dict becomes a native tool call (the executor's only
    transport, issue #68 / E25); every other reply stays a JSON text message
    (planner, task-replan, validation)."""
    if isinstance(reply, dict) and "action" in reply:
        arguments = {key: value for key, value in reply.items() if key != "action"}
        choice: dict[str, Any] = {
            "message": {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_scripted",
                        "type": "function",
                        "function": {
                            "name": reply["action"],
                            "arguments": json.dumps(arguments),
                        },
                    }
                ],
            }
        }
        if finish_reason is not None:
            choice["finish_reason"] = finish_reason
        body: dict[str, Any] = {"choices": [choice]}
        if usage is not None:
            body["usage"] = usage
        return mock_http_response(json_body=body)
    return mock_response(reply, finish_reason=finish_reason, usage=usage)


def mock_response_raw(
    text: str,
    *,
    finish_reason: str | None = None,
    usage: dict[str, Any] | None = None,
) -> FakeHttpResponse:
    """Create a chat-completion response with already-encoded model text."""
    choice: dict[str, Any] = {"message": {"content": text}}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    body: dict[str, Any] = {"choices": [choice]}
    if usage is not None:
        body["usage"] = usage
    return mock_http_response(json_body=body)


# --- Integration test limits ---

# Default tight limits for integration tests.
# INT_MAX_STEPS must be > number of real actions so the LLM has room to emit "done".
# Example: write + compile + run = 3 real actions, needs step 4 for "done".
INT_MAX_REPLANS = 1
INT_MAX_TASKS = 3
INT_MAX_STEPS = 5

# Medium tests: more steps for error recovery within a task (no replans expected)
MED_MAX_REPLANS = 1
MED_MAX_TASKS = 3
MED_MAX_STEPS = 8

# Hard tests: allow replans, more steps and tasks for complex recovery
HARD_MAX_REPLANS = 2
HARD_MAX_TASKS = 5
HARD_MAX_STEPS = 8


def log(msg):
    """Timestamped print that flushes immediately for real-time monitoring."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def int_run(
    user_prompt,
    work_dir,
    max_replans=INT_MAX_REPLANS,
    max_tasks=INT_MAX_TASKS,
    max_steps=INT_MAX_STEPS,
    goal_context_chars=None,
):
    """Integration test agent loop — thin delegation to public run_result().

    int_run callers use max_replans to mean "replans after first plan",
    but the run budget means total plan attempts. Adjust by +1. No ``llm``
    is pinned, so the module-level backend configuration stays in effect.
    ``goal_context_chars=None`` keeps the runtime default executor goal view.
    """
    from askme import RunConfig, run_result

    return run_result(
        user_prompt,
        working_dir=work_dir,
        config=RunConfig(
            max_replans=max_replans + 1,
            max_tasks=max_tasks,
            max_steps=max_steps,
            goal_context_chars=goal_context_chars,
        ),
    )


def assert_file(path, content_contains=None):
    """Assert helper with clear messages."""
    p = Path(path)
    assert p.exists(), f"Expected file not found: {p}"
    if content_contains:
        text = p.read_text()
        assert content_contains.lower() in text.lower(), (
            f"Expected '{content_contains}' in {p}, got: {text[:200]}"
        )


def assert_executable_output(path, expected):
    """Verify a generated executable exists, runs, and prints expected output."""
    p = Path(path)
    assert p.exists(), f"Expected executable not found: {p}"
    assert_command_output([str(p)], p.parent, expected)


def assert_command_output(command, cwd, expected):
    """Run a deterministic postcondition command and verify its output."""
    import subprocess

    result = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"Postcondition command failed ({result.returncode}): {result.stderr[:200]}"
    )
    assert expected in result.stdout, (
        f"Expected '{expected}' in command output, got: {result.stdout[:200]}"
    )


def or_run(
    user_prompt,
    work_dir,
    max_replans=INT_MAX_REPLANS,
    max_tasks=INT_MAX_TASKS,
    max_steps=INT_MAX_STEPS,
    goal_context_chars=None,
):
    """Agent loop pinned to the configured OpenRouter model and provider.

    Immutable per-run configuration (issue #40) replaces the former
    module-global and environment save/restore: the run derives OpenRouter
    settings from the environment through the one LLMSettings derivation
    and pins them for this run only. Token budgets follow the pinned
    backend, not the process-wide import-time backend.
    ``goal_context_chars=None`` keeps the runtime default executor goal view.
    """
    from askme import LLMSettings, RunConfig, run_result

    env = dict(os.environ)
    env["LLM_BACKEND"] = "openrouter"
    return run_result(
        user_prompt,
        working_dir=work_dir,
        config=RunConfig(
            llm=LLMSettings.from_env(env),
            max_replans=max_replans + 1,
            max_tasks=max_tasks,
            max_steps=max_steps,
            goal_context_chars=goal_context_chars,
        ),
    )


# --- Showcase web-app tasks (docs/showcase-tasks.md, T1 family) ---

# The executor's goal view is capped at goal_context_chars (default 300).
# The T1 build prompts carry their full runtime contract in the goal text,
# so web-suite build runs raise the cap instead of letting it truncate.
WEB_GOAL_CONTEXT_CHARS = 900

# Distinct loopback ports per task so a server leaked by one run cannot
# collide with the next test's smoke script. Held-out acceptance never uses
# these: it always rebinds on a fresh ephemeral port. 8080 stays reserved
# for llama-server on local setups.
WEB_STATUS_PORT = 8765
WEB_NOTES_PORT = 8770
WEB_REPAIR_PORT = 8775


def webapp_status_prompt(workdir):
    """T1a build prompt: status service plus self-terminating smoke test."""
    return (
        f"In {workdir}: create app.py, an HTTP server using only the Python standard "
        "library. It must take a port number as its first command-line argument. "
        'GET / returns the plain text MICRO_OK. GET /health returns the JSON {"status": "ok"}. '
        f"Also create test_app.py: it starts 'python3 app.py {WEB_STATUS_PORT}' as a "
        "subprocess, waits until the port accepts connections, fetches / and /health "
        "with urllib, prints WEBAPP_OK and exits 0 if both are correct, and always "
        "terminates the server before exiting. "
        "Run 'python3 test_app.py' and finish when it prints WEBAPP_OK."
    )


def webapp_notes_prompt(workdir):
    """T1b build prompt: in-memory notes service with a POST/GET round trip."""
    return (
        f"In {workdir}: create app.py using only the Python standard library: an HTTP "
        "server that takes a port as its first command-line argument and keeps notes "
        "in memory. POST /notes with a text body appends one note and returns status 201. "
        "GET /notes returns every note in insertion order, one per line. "
        f"Also create test_app.py: it starts 'python3 app.py {WEB_NOTES_PORT}' as a "
        "subprocess, waits for the port, POSTs alpha then beta to /notes, GETs /notes, "
        "checks alpha appears before beta, prints NOTES_OK and exits 0, and always "
        "terminates the server before exiting. "
        "Run 'python3 test_app.py' and finish when it prints NOTES_OK."
    )


def webapp_repair_prompt(workdir):
    """T1c repair prompt; short enough for the default executor goal view."""
    return (
        f"In {workdir}: run 'python3 test_app.py' — it fails and prints which check "
        "failed. Fix the bug in app.py. Do not modify test_app.py. "
        "Run 'python3 test_app.py' again and finish when it prints HEALTH_OK."
    )


# T1c seed: a working status service whose /health payload is wrong. The
# visible feedback script below fails on it; the fix is one value in app.py.
WEBAPP_SEED_APP = """import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._reply(200, "text/plain", b"MICRO_OK\\n")
        elif self.path == "/health":
            payload = json.dumps({"status": "down"})
            self._reply(200, "application/json", payload.encode())
        else:
            self._reply(404, "text/plain", b"not found\\n")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
"""

# T1c protected visible feedback. The port literal must stay WEB_REPAIR_PORT.
WEBAPP_SEED_TEST = """import json
import subprocess
import sys
import time
import urllib.request

PORT = 8775


def fetch(path):
    url = "http://127.0.0.1:%d%s" % (PORT, path)
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, resp.read().decode()


def wait_for_server():
    for _ in range(50):
        try:
            return fetch("/")
        except OSError:
            time.sleep(0.2)
    raise SystemExit("FAIL: server did not start")


def main():
    server = subprocess.Popen([sys.executable, "app.py", str(PORT)])
    try:
        status, body = wait_for_server()
        if status != 200 or "MICRO_OK" not in body:
            print("FAIL: GET / returned %s %r, expected MICRO_OK" % (status, body))
            return 1
        status, body = fetch("/health")
        payload = json.loads(body)
        if status != 200 or payload.get("status") != "ok":
            print("FAIL: GET /health returned %s %s, expected status ok" % (status, payload))
            return 1
        print("HEALTH_OK")
        return 0
    finally:
        server.terminate()
        server.wait()


if __name__ == "__main__":
    sys.exit(main())
"""


def _free_loopback_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(proc: "subprocess.Popen[bytes]", port: int, timeout: float) -> None:
    deadline = time.time() + timeout
    while True:
        assert proc.poll() is None, f"service exited early with code {proc.returncode}"
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            assert time.time() < deadline, f"service did not listen on port {port} in {timeout}s"
            time.sleep(0.1)


def _http_check(port: int, method: str, path: str, body: "str | None") -> tuple[int, str]:
    data = body.encode() if body is not None else None
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode(errors="replace")


def probe_loopback_service(
    app_path: "str | Path",
    checks: "list[tuple[str, str, str | None]]",
    startup_timeout: float = 10.0,
) -> "list[tuple[int, str]]":
    """Held-out acceptance probe: relaunch app.py on a fresh port and drive it.

    Starts ``python3 app.py <ephemeral port>`` — a port the agent never saw,
    so the port-as-argv contract is genuinely exercised — runs each
    ``(method, path, body)`` check over loopback HTTP, and always stops the
    server. Returns one ``(status, body_text)`` pair per check.
    """
    app = Path(app_path)
    port = _free_loopback_port()
    proc = subprocess.Popen(
        [sys.executable, app.name, str(port)],
        cwd=str(app.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(proc, port, startup_timeout)
        return [_http_check(port, method, path, body) for method, path, body in checks]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def assert_status_service(workdir):
    """Held-out acceptance for the T1a build and the T1c repaired service."""
    (root_status, root_body), (health_status, health_body) = probe_loopback_service(
        Path(workdir) / "app.py", [("GET", "/", None), ("GET", "/health", None)]
    )
    assert root_status == 200, f"GET / returned {root_status}: {root_body[:200]}"
    assert "MICRO_OK" in root_body, f"GET / body missing MICRO_OK: {root_body[:200]}"
    assert health_status == 200, f"GET /health returned {health_status}: {health_body[:200]}"
    payload = json.loads(health_body)
    assert payload.get("status") == "ok", f"/health payload was {payload}, expected status ok"


def assert_notes_service(workdir):
    """Held-out acceptance for the T1b notes service round trip."""
    (first_status, _), (second_status, _), (list_status, listing) = probe_loopback_service(
        Path(workdir) / "app.py",
        [("POST", "/notes", "first"), ("POST", "/notes", "second"), ("GET", "/notes", None)],
    )
    assert first_status == 201, f"first POST /notes returned {first_status}, expected 201"
    assert second_status == 201, f"second POST /notes returned {second_status}, expected 201"
    assert list_status == 200, f"GET /notes returned {list_status}: {listing[:200]}"
    assert "first" in listing and "second" in listing, f"notes listing: {listing[:200]}"
    assert listing.index("first") < listing.index("second"), f"order lost: {listing[:200]}"
