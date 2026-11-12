"""Run recorded scenarios through the reference harness and the native port.

A transcript is everything a run leaves behind: its exit code, console lines,
JSONL events, structured result, model requests and final workspace. Wall
times and the workspace location are the only values allowed to differ, so
they are normalized before two transcripts are compared.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE_DIR = ROOT / "native"
UPSTREAM_DRIVER = ROOT / "tests" / "native_replay_upstream.py"
PORT_MAIN = NATIVE_DIR / "askme_native.py"

# Never contacted: every request is answered from the scenario's replies.
RECORDED_ENDPOINT = "http://127.0.0.1:9/v1/chat/completions"

_CONFIG_VARIABLES = (
    "LLM_BACKEND",
    "LLM_API_URL",
    "LLM_MODEL",
    "LLM_CAPABILITY_PROFILE",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "OPENROUTER_PROVIDER",
    "OPENROUTER_ALLOW_FALLBACKS",
    "OPENROUTER_REQUIRE_PARAMETERS",
    "OPENROUTER_REASONING_EFFORT",
    "ALLOW_SYSTEM_INSTALLS",
    "ALLOW_NETWORK",
    "AGENT_COMPILE_REPAIR",
    "AGENT_REASONING_POLICY",
    "AGENT_STEP_POLICY",
    "AGENT_FINAL_VALIDATE",
    "AGENT_GOAL_CONTEXT_CHARS",
    "AGENT_RUN_LOG",
    "ASKME_NATIVE_TIMEOUT_TOOL",
)

_STAMP = re.compile(r"^\[\d\d:\d\d:\d\d\] ")
_SECONDS = re.compile(r"\b\d+\.\d+s\b")
_TIMED_KEYS = ("wall_s", "llm_wall_s")


class Transcript:
    def __init__(self, exit_code, console, stderr, events, result, requests, workspace):
        self.exit_code = exit_code
        self.console = console
        self.stderr = stderr
        self.events = events
        self.result = result
        self.requests = requests
        self.workspace = workspace

    def parts(self):
        return {
            "exit_code": self.exit_code,
            "console": self.console,
            "events": self.events,
            "result": self.result,
            "requests": self.requests,
            "workspace": self.workspace,
        }


def scenario_environment(scenario, run_log):
    """A hermetic environment: only the scenario's settings reach the harness."""
    env = {key: value for key, value in os.environ.items() if key not in _CONFIG_VARIABLES}
    env.update(
        {
            "LLM_BACKEND": "local",
            "LLM_API_URL": RECORDED_ENDPOINT,
            "LLM_MODEL": "recorded-model",
            "AGENT_RUN_LOG": str(run_log),
            # One child-process locale for every runtime: CPython would
            # otherwise export LC_CTYPE=C.UTF-8 under the C locale.
            "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    env.update(scenario["env"])
    return env


def materialize(scenario, workspace):
    workspace.mkdir(parents=True)
    for relative, content in scenario["files"].items():
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _normalize_text(text, workspace):
    resolved = str(workspace.resolve())
    return text.replace(resolved, "<WS>").replace(str(workspace), "<WS>")


def normalize_json(value, workspace):
    if isinstance(value, dict):
        return {
            key: 0
            if key in _TIMED_KEYS and isinstance(item, (int, float))
            else normalize_json(item, workspace)
            for key, item in value.items()
            if key != "ts"
        }
    if isinstance(value, list):
        return [normalize_json(item, workspace) for item in value]
    if isinstance(value, str):
        return _normalize_text(value, workspace)
    return value


def _read_workspace(workspace):
    files = {}
    for path in sorted(workspace.rglob("*")):
        if path.is_file():
            files[str(path.relative_to(workspace))] = path.read_bytes().decode("utf-8", "replace")
    return files


def run_scenario(scenario, base, command):
    """Run one scenario with ``command`` (argv prefix of an AskMe CLI)."""
    workspace = base / "ws"
    materialize(scenario, workspace)
    replay = base / "replay.json"
    replay.write_text(json.dumps(scenario["replay"]), encoding="utf-8")
    run_log = base / "run.jsonl"
    result_json = base / "result.json"
    request_log = base / "requests.jsonl"
    argv = list(command(replay, request_log))
    argv += ["--working-dir", "ws", "--result-json", "result.json"]
    argv += list(scenario["args"])
    argv.append(scenario["prompt"])
    completed = subprocess.run(
        argv,
        cwd=base,
        env=scenario_environment(scenario, run_log),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    console = [
        _SECONDS.sub("0.0s", _normalize_text(_STAMP.sub("", line), workspace))
        for line in completed.stdout.splitlines()
    ]
    events = []
    if run_log.exists():
        for line in run_log.read_text(encoding="utf-8").splitlines():
            events.append(normalize_json(json.loads(line), workspace))
    result = None
    if result_json.exists():
        result = normalize_json(json.loads(result_json.read_text(encoding="utf-8")), workspace)
    requests = []
    if request_log.exists():
        for line in request_log.read_text(encoding="utf-8").splitlines():
            requests.append(normalize_json(json.loads(line), workspace))
    return Transcript(
        completed.returncode,
        console,
        completed.stderr,
        events,
        result,
        requests,
        _read_workspace(workspace),
    )


def reference_command(replay, request_log):
    return [sys.executable, str(UPSTREAM_DRIVER), str(replay), str(request_log)]


def port_command(prefix):
    """Command builder for a port executable given as an argv prefix."""

    def command(replay, request_log):
        return [*prefix, "--replay", str(replay), "--request-log", str(request_log)]

    return command


def first_difference(left, right, path="$"):
    """A readable location and the two values where transcripts first diverge."""
    if type(left) is not type(right):
        return f"{path}: {left!r} != {right!r}"
    if isinstance(left, dict):
        if list(left) != list(right):
            return f"{path}: keys {list(left)!r} != {list(right)!r}"
        for key in left:
            found = first_difference(left[key], right[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(left, list):
        for index, (a, b) in enumerate(zip(left, right)):
            found = first_difference(a, b, f"{path}[{index}]")
            if found:
                return found
        if len(left) != len(right):
            longer = left if len(left) > len(right) else right
            return f"{path}: lengths {len(left)} != {len(right)}; extra {longer[min(len(left), len(right))]!r}"
        return None
    if left != right:
        return f"{path}: {left!r} != {right!r}"
    return None
