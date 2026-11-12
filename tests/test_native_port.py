"""The typed port in ``native/`` must behave like the reference harness.

Every recorded scenario runs through ``askme.py`` and through
``native/askme_native.py``; the two transcripts — model requests, JSONL
events, console lines, structured result, exit code and final workspace —
must be identical once wall times and the workspace location are normalized.

CPython always runs the port. Three more runtimes join when the environment
names their tools, and skip otherwise:

- ``ASKME_NATIVE_PYSTACHY``: a ``pystachy`` command (JIT and a built executable)
- ``ASKME_NATIVE_MYPYC``: a ``mypyc`` command (compiled extension modules)
- ``ASKME_NATIVE_PYPY``: a PyPy interpreter

A skipped runtime is not evidence that the port works there.
"""

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path, PurePosixPath

import pytest
from native_port_support import (
    NATIVE_DIR,
    PORT_MAIN,
    first_difference,
    materialize,
    normalize_json,
    port_command,
    reference_command,
    run_scenario,
    scenario_environment,
)
from native_scenarios import SCENARIO_DIR, SCENARIOS, scenario_json

import actions as reference_actions

sys.path.insert(0, str(NATIVE_DIR))

import native_actions  # noqa: E402
import native_host  # noqa: E402
import native_json  # noqa: E402

# The port's process and HTTP adapters are POSIX code validated on Linux.
# Tests that touch the operating system run there; the pure checks run anywhere.
linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="the native port's adapters are validated on Linux only"
)

# One scenario waits for a real five-second shell timeout.
SLOW = {"timeout_retry"}

# Three-letter labels: every run directory of a scenario has the same length.
RUNTIMES = ("cpy", "pyp", "myc", "jit", "aot")
OPTIONAL_TOOLS = {
    "pyp": "ASKME_NATIVE_PYPY",
    "myc": "ASKME_NATIVE_MYPYC",
    "jit": "ASKME_NATIVE_PYSTACHY",
    "aot": "ASKME_NATIVE_PYSTACHY",
}


@pytest.fixture(scope="module")
def port_prefix(tmp_path_factory):
    """The argv prefix that starts the port on a runtime, built once."""
    built = {}

    def prefix(runtime):
        if runtime in built:
            return built[runtime]
        if runtime == "cpy":
            argv = [sys.executable, str(PORT_MAIN)]
        else:
            variable = OPTIONAL_TOOLS[runtime]
            tool = shlex.split(os.environ.get(variable, ""))
            if not tool:
                pytest.skip(f"set {variable} to test the port on this runtime")
            if runtime == "pyp":
                argv = [*tool, str(PORT_MAIN)]
            elif runtime == "jit":
                argv = [*tool, "run", str(PORT_MAIN)]
            elif runtime == "aot":
                executable = tmp_path_factory.mktemp("native_aot") / "askme"
                subprocess.run([*tool, "build", str(PORT_MAIN), "-o", str(executable)], check=True)
                argv = [str(executable)]
            else:
                build = tmp_path_factory.mktemp("native_mypyc")
                modules = sorted(path.name for path in NATIVE_DIR.glob("*.py"))
                for name in modules:
                    shutil.copy(NATIVE_DIR / name, build / name)
                subprocess.run([*tool, *modules], cwd=build, check=True, capture_output=True)
                for name in modules:
                    (build / name).unlink()  # only the extension modules remain importable
                (build / "run.py").write_text(
                    "import sys\nimport askme_native\n"
                    "assert askme_native.__file__.endswith('.so')\n"
                    "sys.exit(askme_native.main())\n"
                )
                python = Path(shutil.which(tool[0]) or tool[0]).resolve().parent / "python"
                argv = [str(python), str(build / "run.py")]
        built[runtime] = argv
        return argv

    return prefix


CPYTHON_PORT = port_command([sys.executable, str(PORT_MAIN)])


def assert_same_transcript(expected, actual):
    for part, value in expected.parts().items():
        difference = first_difference(value, actual.parts()[part])
        assert difference is None, f"{part} differs at {difference}\nstderr: {actual.stderr}"


@pytest.fixture(scope="module")
def run_root(tmp_path_factory):
    """Run directories whose paths have one length for every runtime.

    Outputs that quote an absolute path are cut at a fixed number of
    characters, so two runs are only comparable from equally long paths:
    ``<root>/<scenario>/<three-letter runtime>``.
    """
    root = tmp_path_factory.mktemp("native_runs")

    def base(name, runtime):
        assert len(runtime) == 3
        return root / name / runtime

    return base


@pytest.fixture(scope="module")
def reference_transcripts(run_root):
    """Each scenario through the reference harness, once per module."""
    cache = {}

    def get(name):
        if name not in cache:
            cache[name] = run_scenario(SCENARIOS[name], run_root(name, "ref"), reference_command)
        return cache[name]

    return get


@linux_only
@pytest.mark.parametrize("runtime", RUNTIMES)
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_port_matches_reference(name, runtime, run_root, reference_transcripts, port_prefix):
    command = port_command(port_prefix(runtime))
    expected = reference_transcripts(name)
    assert expected.exit_code == SCENARIOS[name]["exit_code"], expected.stderr
    assert expected.requests, "the scenario made no model request"
    actual = run_scenario(SCENARIOS[name], run_root(name, runtime), command)
    assert_same_transcript(expected, actual)


@linux_only
def test_scenarios_cover_every_action_and_terminal_status(reference_transcripts):
    actions_seen = set()
    statuses = set()
    skip_reasons = set()
    for name in SCENARIOS:
        if name in SLOW:
            continue
        transcript = reference_transcripts(name)
        statuses.add(transcript.result["status"])
        for event in transcript.events:
            if event["event"] == "step":
                actions_seen.add(event["action"])
            elif event["event"] == "step_control":
                actions_seen.add(event["action"])
            elif event["event"] == "step_skipped":
                skip_reasons.add(event["reason"])
    assert actions_seen == set(reference_actions.ACTION_SPECS)
    assert statuses == {"complete", "complete_unverified", "exhausted"}
    assert {
        "duplicate_write",
        "duplicate_read",
        "duplicate_shell",
        "stuck_read",
        "stuck_shell_repeat",
        "observe_tail_reserved",
        "observe_tail_exhausted",
        "rewrite_loop",
        "lifecycle_unverified_done",
        "lifecycle_verify_before_rewrite",
        "incomplete_write_done",
        "truncated_write_empty",
        "append_after_empty_overwrite",
    } <= skip_reasons


def test_committed_scenarios_match_their_source():
    committed = {
        path.stem: path.read_text(encoding="utf-8") for path in SCENARIO_DIR.glob("*.json")
    }
    assert sorted(committed) == sorted(SCENARIOS)
    for name in SCENARIOS:
        assert committed[name] == scenario_json(name), (
            f"native/scenarios/{name}.json is stale: run python3 tests/native_scenarios.py"
        )


@linux_only
def test_scenario_file_is_a_replay_source(tmp_path):
    """``--replay`` accepts the committed scenario file, as the README shows."""
    workspace = tmp_path / "ws"
    materialize(SCENARIOS["hello"], workspace)
    completed = subprocess.run(
        [
            sys.executable,
            str(PORT_MAIN),
            "--working-dir",
            str(workspace),
            "--replay",
            str(SCENARIO_DIR / "hello.json"),
            SCENARIOS["hello"]["prompt"],
        ],
        env=scenario_environment(SCENARIOS["hello"], tmp_path / "run.jsonl"),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert (workspace / "hello.txt").read_text() == "hello\n"


# --- units that the compiled program cannot borrow from the stdlib ----------


@pytest.mark.parametrize(
    "data",
    [b"", b"abc", b"a" * 55, b"a" * 56, b"a" * 63, b"a" * 64, b"a" * 65, bytes(range(256)) * 9],
)
def test_pure_sha256_matches_hashlib(data):
    assert native_host.sha256_pure(data) == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    "path",
    [
        "a/b/c.txt",
        "/x//y/./z/",
        "",
        ".",
        "/",
        "//net/x",
        "///x",
        "a/..",
        "../a",
        "x",
        "a/b/",
        "./a",
    ],
)
def test_path_helpers_follow_pathlib(path):
    pure = PurePosixPath(path)
    assert native_host.path_str(path) == str(pure)
    assert native_host.path_name(path) == pure.name
    assert native_host.path_parent(path) == str(pure.parent)
    assert native_host.path_depth(path) == len(pure.parts)
    assert native_host.path_is_absolute(path) == pure.is_absolute()
    assert native_host.path_join("/work/dir", path) == str(PurePosixPath("/work/dir") / path)
    assert native_host.path_join("rel", path) == str(PurePosixPath("rel") / path)


def test_path_relative_to_follows_pathlib():
    assert native_host.path_relative_to("/a/b/c", "/a") == "b/c"
    assert native_host.path_relative_to("/a/b", "/a/b") == "."
    assert native_host.path_relative_to("/a/b", "/a/c") is None
    assert native_host.path_relative_to("a/b", "/a") is None


@pytest.mark.parametrize(
    "value",
    [
        {},
        [],
        {"a": [1, 2.5, None, True, "é"], "b": {}, "c": [], "d": {"k": [{"z": 'q"\\'}]}},
        ["x", {"y": None}],
        "text",
        3,
    ],
)
def test_pretty_json_matches_indent_two(value):
    assert native_json.dumps_pretty(value) == json.dumps(value, indent=2)


def _build_workspace(root):
    root.mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src" / "main.c").write_text("int main(){\n  return 0;\n}\n")
    (root / "long.txt").write_text("x" * 5000 + "\nsecond é line\n" + "y" * 3000)
    (root / "crlf.txt").write_text("one\r\ntwo\r\n", newline="")
    (root / "latin1.bin").write_bytes(b"caf\xe9\n")
    deep = root / "many" / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    (deep / "deep.txt").write_text("deep\n")
    (root / "many" / ".hidden").mkdir()
    (root / "many" / "node_modules").mkdir()
    for index in range(70):
        (root / "many" / f"f{index:03d}.txt").write_text(f"needle {index}\n")
    (root / "link").symlink_to("src")


def _snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


ACTION_SCRIPT = [
    {"action": "tree"},
    {"action": "tree", "arg": "many"},
    {"action": "tree", "arg": "nowhere"},
    {"action": "write", "arg": "notes.txt", "content": "alpha\n", "append": True},
    {"action": "write", "arg": "notes.txt", "content": "beta\n", "append": True},
    {"action": "write", "arg": "data.json", "content": {"k": [1, {"z": None}], "s": "é"}},
    {"action": "write", "arg": "src", "content": "is a directory"},
    {"action": "write", "arg": "notes.txt/child", "content": "parent is a file"},
    {"action": "write", "arg": ".", "content": "no name"},
    {"action": "read", "arg": "src/main.c"},
    {"action": "read", "arg": "src/main.c", "offset": 2, "limit": 1},
    {"action": "read", "arg": "src/main.c", "offset": 99},
    {"action": "read", "arg": "long.txt"},
    {"action": "read", "arg": "missing.txt"},
    {"action": "read", "arg": "src"},
    {"action": "read", "arg": "latin1.bin"},
    {"action": "read", "arg": "link/main.c"},
    {"action": "edit", "arg": "src/main.c", "find": "return 0", "replace": "return 1"},
    {"action": "edit", "arg": "src/main.c", "find": "absent", "replace": "x"},
    {"action": "edit", "arg": "src/main.c", "find": " ", "replace": "x"},
    {"action": "edit", "arg": "gone.c", "find": "a", "replace": "b"},
    {"action": "edit", "arg": "crlf.txt", "find": "one", "replace": "uno"},
    {"action": "search", "arg": "needle"},
    {"action": "search", "arg": "return", "path": "src"},
    {"action": "search", "arg": "x", "path": "nowhere"},
    {"action": "search", "arg": "caf"},
    {"action": "shell", "arg": "echo out; echo err >&2"},
    {"action": "shell", "arg": "cat notes.txt; exit 2"},
    {"action": "shell", "arg": "definitely-not-a-command-xyz"},
    {"action": "shell", "arg": "printf 'a\\r\\nb\\rc'"},
    {"action": "shell", "arg": "true"},
    {"action": "shell", "arg": "ls /nonexistent-dir-xyz"},
    {"action": "done"},
    {"action": "bogus", "arg": "x"},
    {"action": "read", "arg": "a", "cursor": 5},
    {"action": "read", "arg": "a", "cursor": 5, "limit": 3},
    {"action": "read", "arg": "a", "limit": 0},
    {"action": "read", "arg": "a", "offset": True},
    {"action": "read", "arg": "a", "sha256": "abc"},
    {"action": "shell", "arg": "x", "timeout": 2},
    {"action": "shell", "arg": "x", "timeout": 5.0},
    {"action": "write", "arg": "a", "content": 5},
    {"action": "write", "arg": "a", "content": "x", "append": "yes"},
    {"action": "write", "arg": "a"},
    {"action": "edit", "arg": "a", "find": "", "replace": ""},
    {"action": "tree", "arg": None, "reasoning": None},
    {"action": "search", "arg": None},
    {"action": "read", "arg": "a", "_target": "x"},
    {"action": "read", "arg": "a", "transport": {}},
    {"action": "read", "arg": "a", "bogus": 1},
    {"action": ""},
    {"action": 7},
    {"arg": "no action"},
    [],
    "text",
]


@linux_only
def test_action_layer_matches_reference(tmp_path, monkeypatch):
    """Parser, handlers and receipts against ``actions.py``, action by action.

    Both layers act on one path in turn, rebuilt between them, so every
    result must be equal without any normalization.
    """
    monkeypatch.setenv("LC_ALL", "C")
    root = tmp_path / "ws"
    workspace = str(root)

    _build_workspace(root)
    reference = reference_actions.ActionExecutor(workspace)
    expected = []
    for step, action in enumerate(ACTION_SCRIPT):
        result = reference.dispatch(action)
        parsed = reference_actions.parse_action_envelope(action)
        record = {"result": json.dumps(result.to_dict())}
        if not isinstance(parsed, reference_actions.ActionEnvelope):
            record["error"] = [parsed.error_type, parsed.message, parsed.field]
        else:
            record["envelope"] = parsed.to_dict()
            if parsed.name not in ("done", "fail"):
                receipt = reference_actions.StepReceipt.executed(parsed, result, workspace)
                record["entry"] = json.dumps(receipt.entry)
                record["jsonl"] = json.dumps(receipt.jsonl_event(1, step, 0.5))
                record["history"] = json.dumps(receipt.history_event(1, step))
        expected.append(record)
    reference_files = _snapshot(root)
    shutil.rmtree(root)

    _build_workspace(root)
    host = native_host.Host()
    port = native_actions.ActionExecutor(workspace, host)
    try:
        for step, action in enumerate(ACTION_SCRIPT):
            result = port.dispatch_value(action)
            parsed = native_actions.parse_action_envelope(action)
            record = {"result": json.dumps(result.to_dict())}
            if parsed.envelope is None:
                record["error"] = [parsed.error_type(), parsed.message(), parsed.field()]
            else:
                record["envelope"] = parsed.envelope.to_dict()
                if parsed.envelope.name not in ("done", "fail"):
                    receipt = native_actions.receipt_executed(parsed.envelope, result, workspace)
                    record["entry"] = json.dumps(receipt.entry)
                    record["jsonl"] = json.dumps(receipt.jsonl_event(1, step, 0.5))
                    record["history"] = json.dumps(receipt.history_event(1, step))
            assert record == expected[step], f"step {step}: {action!r}"
    finally:
        host.cleanup()
    assert _snapshot(root) == reference_files


@linux_only
def test_read_continuations_reconstruct_the_file(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    original = "x" * 5000 + "\nsecond é line\n" + "y" * 3000 + "\n" + "z\n" * 400
    (root / "long.txt").write_text(original)
    host = native_host.Host()
    executor = native_actions.ActionExecutor(str(root), host)
    rebuilt = ""
    action = {"action": "read", "arg": "long.txt", "limit": 200}
    for _ in range(100):
        result = executor.dispatch_value(action)
        assert result.ok
        rebuilt += result.get("content")
        continuation = result.get("continuation")
        if continuation is None:
            break
        action = {
            "action": "read",
            "arg": "long.txt",
            "cursor": continuation["cursor"],
            "limit": continuation["limit"],
            "sha256": continuation["sha256"],
        }
    host.cleanup()
    assert rebuilt == original


@linux_only
@pytest.mark.parametrize("tool", ["timeout", "none"])
def test_shell_deadline_and_capture(tmp_path, monkeypatch, tool):
    if tool == "timeout" and not (shutil.which("timeout") or shutil.which("gtimeout")):
        pytest.skip("no timeout utility on PATH")
    if tool == "none":
        monkeypatch.setenv("ASKME_NATIVE_TIMEOUT_TOOL", "none")
    host = native_host.Host()
    try:
        done = host.run_shell("echo out; echo err >&2; exit 3", 5, str(tmp_path))
        assert (done.returncode, done.stdout, done.stderr, done.timed_out) == (
            3,
            "out\n",
            "err\n",
            False,
        )
        slow = host.run_shell("echo early; sleep 30", 1, str(tmp_path))
        assert slow.timed_out
        own_code = host.run_shell("exit 124", 5, str(tmp_path))
        assert own_code.returncode == 124 and not own_code.timed_out
        assert host.run_shell("pwd", 5, str(tmp_path)).stdout.strip() == str(tmp_path.resolve())
    finally:
        host.cleanup()


class _Recorder(BaseHTTPRequestHandler):
    replies = []
    seen = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        type(self).seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
        status, text = type(self).replies.pop(0)
        payload = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


@pytest.fixture
def recorded_server():
    if shutil.which("curl") is None:
        pytest.skip("curl is not installed")
    _Recorder.replies = []
    _Recorder.seen = []
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions", _Recorder
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@linux_only
def test_curl_transport_sends_body_and_private_headers(recorded_server):
    url, recorder = recorded_server
    recorder.replies = [(200, '{"ok": "é"}'), (404, "missing"), (503, "busy")]
    host = native_host.Host()
    try:
        body = json.dumps({"text": "naïve ✓", "quote": "it's"})
        reply = host.http_post(
            url, body, ["Content-Type: application/json", "Authorization: Bearer k"], 10
        )
        assert (reply.status, reply.text, reply.error) == (200, '{"ok": "é"}', "")
        assert recorder.seen[0]["body"] == body
        assert recorder.seen[0]["headers"]["Authorization"] == "Bearer k"
        assert host.http_post(url, "{}", [], 10).status == 404
        assert host.http_post(url, "{}", [], 10).text == "busy"
        refused = host.http_post("http://127.0.0.1:9/none", "{}", [], 5)
        assert refused.status == 0 and refused.error.startswith("curl exit ")
        assert (
            host.http_post(url, "{}", ["X-Bad: a\r\nInjected: b"], 5).error
            == "invalid header value"
        )
        assert host.http_post("file:///etc/passwd", "{}", [], 5).error.startswith("Invalid URL")
        assert not list(Path(host.scratch()).iterdir()), "transport files must not outlive a call"
    finally:
        host.cleanup()


@linux_only
@pytest.mark.parametrize("name", ["hello", "navigate_and_edit"])
def test_live_http_matches_replay(name, tmp_path, recorded_server):
    """The curl transport and the replay transport drive identical runs."""
    url, recorder = recorded_server
    scenario = dict(SCENARIOS[name])
    scenario["env"] = {**scenario["env"], "LLM_API_URL": url}
    recorder.replies = [(200, json.dumps(entry)) for entry in scenario["replay"]]
    replayed = run_scenario(scenario, tmp_path / "rep", CPYTHON_PORT)
    live = run_scenario(
        scenario,
        tmp_path / "liv",
        lambda _replay, request_log: [
            sys.executable,
            str(PORT_MAIN),
            "--request-log",
            str(request_log),
        ],
    )
    assert_same_transcript(replayed, live)
    assert not recorder.replies, "the live run must consume every recorded reply"
    received = [
        normalize_json(json.loads(seen["body"]), tmp_path / "liv" / "ws") for seen in recorder.seen
    ]
    assert received == [request["body"] for request in live.requests]


def _run_port(args, tmp_path, env=None, prefix=None):
    scenario = {"env": env or {}}
    return subprocess.run(
        [*(prefix or [sys.executable, str(PORT_MAIN)]), *args],
        cwd=tmp_path,
        env=scenario_environment(scenario, tmp_path / "run.jsonl"),
        capture_output=True,
        text=True,
        timeout=60,
    )


@linux_only
@pytest.mark.parametrize("runtime", RUNTIMES)
@pytest.mark.parametrize(
    "args, env, message",
    [
        ([], {}, "provide exactly one of prompt or --prompt-file"),
        (["a", "--prompt-file", "p.txt"], {}, "provide exactly one of prompt or --prompt-file"),
        (["  "], {}, "prompt must not be empty"),
        (["--prompt-file", "absent.txt"], {}, "cannot read --prompt-file"),
        (["x", "--working-dir", "absent"], {}, "--working-dir must name an existing directory"),
        (["x", "--max-steps", "0"], {}, "argument --max-steps: must be a positive integer"),
        (["x", "--replay", "absent.json"], {}, "cannot read --replay"),
        (["x"], {"AGENT_REASONING_POLICY": "loud"}, "AGENT_REASONING_POLICY must be one of"),
        (["x"], {"AGENT_STEP_POLICY": "chaos"}, "AGENT_STEP_POLICY must be one of"),
        (["x"], {"OPENROUTER_REASONING_EFFORT": "max"}, "OPENROUTER_REASONING_EFFORT must be"),
        (["x"], {"LLM_CAPABILITY_PROFILE": "nope"}, "LLM_CAPABILITY_PROFILE must be one of"),
    ],
)
def test_configuration_errors_exit_two_without_a_run(
    args, env, message, runtime, tmp_path, port_prefix
):
    completed = _run_port(args, tmp_path, env, port_prefix(runtime))
    assert completed.returncode == 2
    assert message in completed.stderr
    assert completed.stdout == ""
    assert not (tmp_path / "run.jsonl").exists()


@linux_only
def test_env_file_fills_only_unset_variables(tmp_path):
    (tmp_path / "settings.env").write_text(
        "# comment\nAGENT_GOAL_CONTEXT_CHARS = 17\nLLM_MODEL=from-file\n\nnot a pair\n"
    )
    (tmp_path / "replay.json").write_text(json.dumps(SCENARIOS["hello"]["replay"]))
    completed = _run_port(
        ["--env-file", "settings.env", "--replay", "replay.json", "--result-json", "r.json", "hi"],
        tmp_path,
    )
    assert completed.returncode == 0, completed.stderr
    config = json.loads((tmp_path / "r.json").read_text())["config"]
    assert config["limits"]["goal_context_chars"] == 17
    assert config["model"] == "recorded-model"  # already set by the environment


@linux_only
def test_api_key_never_reaches_logs_or_result(tmp_path):
    secret = SCENARIOS["openrouter"]["env"]["OPENROUTER_API_KEY"]
    transcript = run_scenario(SCENARIOS["openrouter"], tmp_path / "port", CPYTHON_PORT)
    assert transcript.exit_code == 0
    assert "Authorization" in transcript.requests[0]["headers"]
    for part in (transcript.console, transcript.events, transcript.result, transcript.requests):
        assert secret not in json.dumps(part)
