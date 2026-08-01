#!/usr/bin/env python3
"""Route-pinning proxy for the pi harness ablation.

Runs on the host. The pi coding agent (inside the task container) is pointed
at this proxy as an OpenAI-compatible endpoint with a per-run dummy secret.
The proxy injects OpenRouter's strict provider-routing block, enforces the
allowed model list and a call cap, forwards to OpenRouter with the real key,
and tees every request/response body for the post-run route audit.

The real OPENROUTER_API_KEY never enters the container, any retained
artifact, or a command line. stdlib only.

Environment:
  OPENROUTER_API_KEY      real key (required)
  PI_PROXY_SECRET         per-run dummy bearer the client must present (required)
  PI_PROXY_LOG_DIR        directory for route log + teed bodies (required)
  PI_PROXY_ALLOWED_MODELS comma-separated allowed request models (required)
  PI_PROXY_PORT           listen port (default 8787)
  PI_PROXY_MAX_CALLS      completion-call cap (default 200)
"""

import json
import os
import sys
import threading
import time
from http.client import HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REAL_KEY = os.environ["OPENROUTER_API_KEY"]
SECRET = os.environ["PI_PROXY_SECRET"]
LOG_DIR = Path(os.environ["PI_PROXY_LOG_DIR"])
ALLOWED = {m.strip() for m in os.environ["PI_PROXY_ALLOWED_MODELS"].split(",") if m.strip()}
PORT = int(os.environ.get("PI_PROXY_PORT", "8787"))
MAX_CALLS = int(os.environ.get("PI_PROXY_MAX_CALLS", "200"))

PROVIDER_BLOCK = {
    "order": ["siliconflow"],
    "allow_fallbacks": False,
    "require_parameters": True,
}

LOG_DIR.mkdir(parents=True, exist_ok=True)
_route_log = open(LOG_DIR / "route-log.jsonl", "a", encoding="utf-8")
_lock = threading.Lock()
_calls = 0


def _log(entry):
    with _lock:
        _route_log.write(json.dumps(entry, sort_keys=True) + "\n")
        _route_log.flush()


class Handler(BaseHTTPRequestHandler):
    server_version = "PiPinProxy/1"

    def log_message(self, fmt, *args):  # keep stderr quiet; route log is canonical
        pass

    def _reject(self, code, message):
        body = json.dumps({"error": {"message": message, "code": code}}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("health"):
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._reject(404, "not found")

    def do_POST(self):
        global _calls
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._reject(404, "only chat completions are proxied")
            return
        if self.headers.get("Authorization") != f"Bearer {SECRET}":
            self._reject(401, "bad proxy secret")
            return
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.loads(raw)
        except (ValueError, KeyError):
            self._reject(400, "unparseable request body")
            return
        model = body.get("model", "")
        if model not in ALLOWED:
            _log({"ts": time.time(), "event": "denied_model", "model": model})
            self._reject(403, f"model not allowed by ablation pin: {model}")
            return
        with _lock:
            _calls += 1
            n = _calls
        if n > MAX_CALLS:
            _log({"ts": time.time(), "event": "call_cap", "n": n})
            self._reject(429, "ablation call cap reached")
            return

        body["provider"] = PROVIDER_BLOCK
        usage = body.get("usage")
        body["usage"] = dict(usage or {}, include=True) if isinstance(usage, dict) else {"include": True}
        payload = json.dumps(body).encode()

        req_file = LOG_DIR / f"call-{n:04d}-request.json"
        resp_file = LOG_DIR / f"call-{n:04d}-response.bin"
        req_file.write_bytes(payload)

        entry = {
            "ts": time.time(), "event": "forward", "n": n,
            "request_model": model,
            "request_max_tokens": body.get("max_tokens") or body.get("max_completion_tokens"),
            "stream": bool(body.get("stream")),
            "request_file": req_file.name, "response_file": resp_file.name,
        }
        try:
            conn = HTTPSConnection("openrouter.ai", 443, timeout=900)
            conn.request("POST", "/api/v1/chat/completions", body=payload, headers={
                "Authorization": f"Bearer {REAL_KEY}",
                "Content-Type": "application/json",
                "Accept": self.headers.get("Accept", "application/json"),
            })
            resp = conn.getresponse()
            entry["status"] = resp.status
            self.send_response(resp.status)
            ctype = resp.getheader("Content-Type") or "application/json"
            self.send_header("Content-Type", ctype)
            self.send_header("Connection", "close")
            self.end_headers()
            with open(resp_file, "wb") as tee:
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    tee.write(chunk)
                    self.wfile.write(chunk)
                    self.wfile.flush()
            conn.close()
        except Exception as exc:  # network failure surfaces to client + log
            entry["proxy_error"] = repr(exc)
            try:
                self._reject(502, f"proxy forward failed: {exc}")
            except Exception:
                pass
        _log(entry)


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    _log({"ts": time.time(), "event": "proxy_start", "port": PORT,
          "allowed_models": sorted(ALLOWED), "max_calls": MAX_CALLS})
    print(f"pin-proxy listening on :{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
