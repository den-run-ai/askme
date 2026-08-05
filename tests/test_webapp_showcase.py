"""Offline qualification for the showcase web-app fixtures (docs/showcase-tasks.md).

Deterministic and model-free. Before any live run of the `web` suite:
a same-author reference implementation must pass each task's held-out
acceptance (gold control), the T1c seed must fail both its visible feedback
script and held-out acceptance (no-op control), and the intended one-value
fix must flip the seed to passing. These tests launch real loopback servers
but make no model or network calls.
"""

import subprocess
import sys

import pytest
from _test_support import (
    WEB_REPAIR_PORT,
    WEBAPP_SEED_APP,
    WEBAPP_SEED_TEST,
    assert_notes_service,
    assert_status_service,
)

# Same-author reference for T1a/T1c acceptance: the seed with its one
# defective value corrected.
REFERENCE_STATUS_APP = WEBAPP_SEED_APP.replace('"status": "down"', '"status": "ok"')

# Same-author reference for T1b acceptance.
REFERENCE_NOTES_APP = """import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

NOTES = []


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/notes":
            self._reply(200, "".join(note + "\\n" for note in NOTES).encode())
        else:
            self._reply(404, b"not found\\n")

    def do_POST(self):
        if self.path == "/notes":
            size = int(self.headers.get("Content-Length") or 0)
            NOTES.append(self.rfile.read(size).decode().strip())
            self._reply(201, b"created\\n")
        else:
            self._reply(404, b"not found\\n")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
"""


def _run_visible_feedback(workdir):
    """Run the protected T1c smoke script exactly as the agent would."""
    return subprocess.run(
        [sys.executable, "test_app.py"],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_reference_status_app_passes_held_out_acceptance(tmp_path):
    assert REFERENCE_STATUS_APP != WEBAPP_SEED_APP, "seed defect disappeared"
    (tmp_path / "app.py").write_text(REFERENCE_STATUS_APP)
    assert_status_service(tmp_path)


def test_reference_notes_app_passes_held_out_acceptance(tmp_path):
    (tmp_path / "app.py").write_text(REFERENCE_NOTES_APP)
    assert_notes_service(tmp_path)


def test_seed_uses_the_repair_port():
    assert f"PORT = {WEB_REPAIR_PORT}" in WEBAPP_SEED_TEST


def test_seed_fails_visible_feedback(tmp_path):
    (tmp_path / "app.py").write_text(WEBAPP_SEED_APP)
    (tmp_path / "test_app.py").write_text(WEBAPP_SEED_TEST)
    result = _run_visible_feedback(tmp_path)
    assert result.returncode != 0, f"seed unexpectedly passed: {result.stdout[:200]}"
    assert "FAIL" in result.stdout, f"no actionable failure line: {result.stdout[:200]}"
    assert "HEALTH_OK" not in result.stdout


def test_seed_fails_held_out_acceptance(tmp_path):
    (tmp_path / "app.py").write_text(WEBAPP_SEED_APP)
    with pytest.raises(AssertionError):
        assert_status_service(tmp_path)


def test_fixed_seed_passes_feedback_and_acceptance(tmp_path):
    (tmp_path / "app.py").write_text(REFERENCE_STATUS_APP)
    (tmp_path / "test_app.py").write_text(WEBAPP_SEED_TEST)
    result = _run_visible_feedback(tmp_path)
    assert result.returncode == 0, f"fixed seed failed: {result.stdout[:200]} {result.stderr[:200]}"
    assert "HEALTH_OK" in result.stdout
    assert_status_service(tmp_path)
