"""Offline contracts for the opt-in CPython/Pystachy coding-agent example."""

import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "native_agent"
ENTRY = EXAMPLE / "agent.py"


def response(name, arguments=None, **metadata):
    """Use the real chat-completions native-tool response shape in replay."""
    return {
        "choices": [
            {
                "finish_reason": metadata.get("finish_reason", "tool_calls"),
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_example",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(
                                    arguments if arguments is not None else {},
                                    ensure_ascii=metadata.get("escaped_arguments", False),
                                ),
                            },
                        }
                    ],
                },
            }
        ]
    }


@pytest.fixture(scope="module")
def example_modules():
    """Load the example independently of the production AskMe facade."""
    saved_path = list(sys.path)
    sys.path.insert(0, str(EXAMPLE))
    modules = {}
    try:
        for name in ("wire_json", "agent"):
            spec = importlib.util.spec_from_file_location(
                f"native_example_{name}", EXAMPLE / f"{name}.py"
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules[name] = module
        yield modules
    finally:
        sys.path[:] = saved_path


def run_replay(
    tmp_path,
    replies,
    initial="wrong\n",
    max_steps=12,
    command=None,
    launcher=None,
    prepare=None,
    escape_replay=False,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "value.txt").write_text(initial, encoding="utf-8")
    if prepare is not None:
        prepare(workspace)
    replay = tmp_path / "replay.json"
    replay.write_text(json.dumps(replies, ensure_ascii=escape_replay), encoding="utf-8")
    code = (
        "from pathlib import Path; import sys; "
        'print("VISIBLE_TEST_PASSED") if Path("value.txt").read_text() == "fixed\\n" '
        "else sys.exit(1)"
    )
    test_command = shlex.quote(sys.executable) + " -c " + shlex.quote(code)
    result = subprocess.run(
        [
            *(launcher if launcher is not None else [sys.executable, str(ENTRY)]),
            str(workspace),
            "Fix value.txt and verify the correction",
            command if command is not None else test_command,
            str(replay),
            str(max_steps),
        ],
        cwd=ROOT,
        env={
            "PATH": os.environ.get("PATH", os.defpath),
            "PYTHONHASHSEED": "0",
            "PYTHONIOENCODING": "utf-8",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert not result.stderr, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert events, "The agent must emit a machine-readable run transcript"
    files = {
        str(path.relative_to(workspace)): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }
    return result, events, files


def assert_status(result, events, expected):
    assert events[-1].get("status") == expected, result.stdout
    assert (result.returncode == 0) == (expected == "complete"), result.stdout


@pytest.mark.skipif(shutil.which("timeout") is None, reason="The example requires timeout")
@pytest.mark.parametrize("start_with_failed_check", [False, True])
def test_replay_repairs_file_and_runs_configured_test(tmp_path, start_with_failed_check):
    replies = [response("read", {"path": "value.txt"})]
    if start_with_failed_check:
        replies.append(response("run_test"))
    replies.extend(
        [
            response("edit", {"path": "value.txt", "old": "wrong", "new": "fixed"}),
            response("run_test"),
            response("done"),
        ]
    )
    result, events, files = run_replay(tmp_path, replies)
    assert_status(result, events, "complete")
    assert files == {"value.txt": b"fixed\n"}
    assert "VISIBLE_TEST_PASSED" in result.stdout


@pytest.mark.skipif(shutil.which("timeout") is None, reason="The example requires timeout")
def test_done_before_verification_is_refused_then_recovers(tmp_path):
    result, events, files = run_replay(
        tmp_path,
        [
            response("done"),
            response("write", {"path": "value.txt", "content": "fixed\n"}),
            response("done"),
            response("run_test"),
            response("done"),
        ],
    )
    assert_status(result, events, "complete")
    assert files == {"value.txt": b"fixed\n"}
    assert sum(event.get("ok") is False for event in events) >= 2, result.stdout


@pytest.mark.skipif(shutil.which("timeout") is None, reason="The example requires timeout")
def test_later_edit_invalidates_successful_verification(tmp_path):
    result, events, files = run_replay(
        tmp_path,
        [
            response("run_test"),
            response("edit", {"path": "value.txt", "old": "fixed", "new": "changed"}),
            response("done"),
            response("fail", {"reason": "The changed file has not passed its test"}),
        ],
        initial="fixed\n",
    )
    assert_status(result, events, "failed")
    assert files == {"value.txt": b"changed\n"}
    assert "VISIBLE_TEST_PASSED" in result.stdout
    assert any(event.get("ok") is False for event in events), result.stdout


@pytest.mark.skipif(shutil.which("timeout") is None, reason="The example requires timeout")
def test_failed_test_cannot_authorize_completion(tmp_path):
    result, events, files = run_replay(
        tmp_path,
        [response("run_test"), response("done"), response("fail", {"reason": "Test failed"})],
    )
    assert_status(result, events, "failed")
    assert files == {"value.txt": b"wrong\n"}
    assert "VISIBLE_TEST_PASSED" not in result.stdout


def test_exhausted_observation_budget_does_not_claim_completion(tmp_path):
    result, events, files = run_replay(
        tmp_path,
        [response("read", {"path": "value.txt"}) for _ in range(3)],
        max_steps=3,
    )
    assert_status(result, events, "exhausted")
    assert files == {"value.txt": b"wrong\n"}


@pytest.mark.parametrize("initial,old", [("wrong wrong\n", "wrong"), ("wrong\n", "absent")])
def test_ambiguous_or_missing_edit_preserves_entire_workspace(tmp_path, initial, old):
    result, events, files = run_replay(
        tmp_path,
        [
            response("edit", {"path": "value.txt", "old": old, "new": "fixed"}),
            response("fail", {"reason": "No unique replacement"}),
        ],
        initial=initial,
    )
    assert_status(result, events, "failed")
    assert files == {"value.txt": initial.encode()}
    assert any(event.get("ok") is False for event in events), result.stdout


def test_write_to_directory_does_not_move_temporary_file_inside_it(tmp_path):
    def prepare(workspace):
        directory = workspace / "target"
        directory.mkdir()
        (directory / "keep.txt").write_text("unchanged\n")

    result, events, files = run_replay(
        tmp_path,
        [
            response("write", {"path": "target", "content": "unexpected"}),
            response("fail", {"reason": "Target was a directory"}),
        ],
        prepare=prepare,
    )
    assert_status(result, events, "failed")
    assert files == {"value.txt": b"wrong\n", "target/keep.txt": b"unchanged\n"}
    assert (tmp_path / "workspace" / "target").is_dir()
    assert any(event.get("action") == "write" and event.get("ok") is False for event in events)


@pytest.mark.skipif(shutil.which("timeout") is None, reason="The example requires timeout")
@pytest.mark.parametrize("action", ["edit", "write"])
def test_mutation_preserves_executable_mode_and_script_behavior(tmp_path, action):
    initial = "#!/bin/sh\nprintf 'wrong\\n'\n"
    expected = "#!/bin/sh\nprintf 'fixed\\n'\n"
    arguments = (
        {"path": "value.txt", "old": "wrong", "new": "fixed"}
        if action == "edit"
        else {"path": "value.txt", "content": expected}
    )
    result, events, files = run_replay(
        tmp_path,
        [response(action, arguments), response("run_test"), response("done")],
        initial=initial,
        command='test "$(./value.txt)" = fixed',
        prepare=lambda workspace: (workspace / "value.txt").chmod(0o755),
    )
    assert_status(result, events, "complete")
    assert files == {"value.txt": expected.encode()}
    script = tmp_path / "workspace" / "value.txt"
    assert script.stat().st_mode & 0o777 == 0o755
    executed = subprocess.run([str(script)], capture_output=True, text=True, timeout=5)
    assert executed.returncode == 0
    assert executed.stdout == "fixed\n"


def test_malformed_multiple_tool_calls_cannot_mutate_workspace(tmp_path):
    malformed = response("write", {"path": "value.txt", "content": "fixed\n"})
    malformed["choices"][0]["message"]["tool_calls"].append(
        response("write", {"path": "extra.txt", "content": "unexpected"})["choices"][0]["message"][
            "tool_calls"
        ][0]
    )
    result, events, files = run_replay(tmp_path, [malformed, malformed], max_steps=2)
    assert_status(result, events, "exhausted")
    assert files == {"value.txt": b"wrong\n"}
    assert sum(event.get("ok") is False for event in events) >= 2, result.stdout


def test_read_budget_is_visible_to_the_model(tmp_path):
    initial = "long line " * 2000
    result, events, files = run_replay(
        tmp_path,
        [response("read", {"path": "value.txt"}), response("fail", {"reason": "Read is bounded"})],
        initial=initial,
    )
    assert_status(result, events, "failed")
    assert files == {"value.txt": initial.encode()}
    assert "truncat" in result.stdout.lower(), result.stdout
    assert len(result.stdout) < len(initial)


@pytest.mark.parametrize(
    "name,arguments,kind,fields",
    [
        ("read", {"path": "dir/файл.py"}, "read", {"path": "dir/файл.py"}),
        (
            "write",
            {"path": "value.txt", "content": 'quotes " and newline\nemoji 🐍'},
            "write",
            {"content": 'quotes " and newline\nemoji 🐍'},
        ),
        (
            "edit",
            {"path": "value.txt", "old": "wrong\r\n", "new": "fixed\r\n"},
            "edit",
            {"old": "wrong\r\n", "new": "fixed\r\n"},
        ),
        ("run_test", {}, "run_test", {}),
        ("done", {}, "done", {}),
        ("fail", {"reason": "Cannot fix the file"}, "fail", {"content": "Cannot fix the file"}),
    ],
)
def test_native_tool_decoder_preserves_typed_arguments(
    example_modules, name, arguments, kind, fields
):
    action = example_modules["agent"].decode_action(json.dumps(response(name, arguments)))
    assert action.kind == kind
    for field, expected in fields.items():
        assert getattr(action, field) == expected


def invalid_replies():
    content_only = {"choices": [{"message": {"content": '{"action":"done"}'}}]}
    multiple = response("done")
    multiple["choices"][0]["message"]["tool_calls"] *= 2
    malformed_arguments = response("write", {"path": "value.txt", "content": "bad"})
    malformed_arguments["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = (
        '{"path":"value.txt","content":"cut off'
    )
    return [
        content_only,
        multiple,
        malformed_arguments,
        response("unknown", {}),
        response("read", {}),
        response("read", {"path": 12}),
        response("read", {"path": "value.txt", "unexpected": "field"}),
        response("write", {"path": "value.txt", "content": None}),
        response("edit", {"path": "value.txt", "old": "", "new": "fixed"}),
        response("run_test", {"command": "echo unrelated"}),
        response("done", {"action": "write", "content": "unexpected"}),
        response("fail", {"reason": False}),
    ]


@pytest.mark.parametrize("reply", invalid_replies())
def test_native_tool_decoder_rejects_invalid_action(example_modules, reply):
    with pytest.raises(ValueError):
        example_modules["agent"].decode_action(json.dumps(reply))


def test_length_telemetry_does_not_reject_complete_arguments(example_modules):
    action = example_modules["agent"].decode_action(
        json.dumps(
            response("write", {"path": "value.txt", "content": "fixed\n"}, finish_reason="length")
        )
    )
    assert action.kind == "write"
    assert action.content == "fixed\n"


def test_live_transport_sends_native_tools_without_key_in_command_or_logs(
    example_modules, tmp_path, monkeypatch, capsys
):
    agent = example_modules["agent"]
    endpoint = "http://127.0.0.1:8080/v1/chat/completions"
    test_key = "synthetic-native-agent-test-key"
    model = 'model "quoted" 🐍'
    goal = 'Fix "quotes"\nand Unicode файл'
    history = ['{"action":"read","ok":true,"output":"wrong"}']
    monkeypatch.setenv("NATIVE_AGENT_ENDPOINT", endpoint)
    monkeypatch.setenv("NATIVE_AGENT_MODEL", model)
    monkeypatch.setenv("NATIVE_AGENT_API_KEY", test_key)
    commands = []
    requests = []

    def fake_shell(command):
        commands.append(command)
        parts = shlex.split(command)
        assert parts[0] == "curl"
        assert test_key not in command
        headers = Path(parts[parts.index("--header") + 1][1:])
        request = Path(parts[parts.index("--data-binary") + 1][1:])
        reply = Path(parts[parts.index("--output") + 1])
        assert headers.read_text() == (
            "Content-Type: application/json\nAuthorization: Bearer " + test_key + "\n"
        )
        requests.append(json.loads(request.read_text()))
        reply.write_text(json.dumps(response("done")))
        return 0

    monkeypatch.setattr(agent, "shell_status", fake_shell)
    raw = agent.live_reply(goal, history, str(tmp_path))
    assert agent.decode_action(raw).kind == "done"
    assert len(commands) == 1
    request = requests[0]
    assert request["model"] == model
    assert request["messages"][0]["role"] == "system"
    assert request["messages"][1]["role"] == "user"
    assert goal in request["messages"][1]["content"]
    assert history[0] in request["messages"][1]["content"]
    assert request["parallel_tool_calls"] is False
    assert 0 < request["max_tokens"] <= 2048
    tools = {tool["function"]["name"]: tool["function"] for tool in request["tools"]}
    assert set(tools) == {"read", "write", "edit", "run_test", "done", "fail"}
    assert tools["run_test"]["parameters"]["properties"] == {}
    assert tools["run_test"]["parameters"]["additionalProperties"] is False
    assert test_key not in json.dumps(request)
    assert not (tmp_path / "headers").exists()
    assert test_key not in capsys.readouterr().out


def test_live_transport_failure_removes_private_header_without_leaking_key(
    example_modules, tmp_path, monkeypatch, capsys
):
    agent = example_modules["agent"]
    test_key = "synthetic-native-agent-test-key"
    monkeypatch.setenv("NATIVE_AGENT_API_KEY", test_key)
    monkeypatch.setattr(agent, "shell_status", lambda _command: 22)
    with pytest.raises(RuntimeError, match="HTTP transport failed") as error:
        agent.live_reply("Fix value.txt", [], str(tmp_path))
    assert test_key not in str(error.value)
    assert not (tmp_path / "headers").exists()
    assert test_key not in capsys.readouterr().out


@pytest.mark.parametrize("key", ["synthetic\rheader", "synthetic\nheader"])
def test_live_transport_rejects_header_injection_before_launch(
    example_modules, tmp_path, monkeypatch, key
):
    agent = example_modules["agent"]
    monkeypatch.setenv("NATIVE_AGENT_API_KEY", key)
    commands = []
    monkeypatch.setattr(agent, "shell_status", lambda command: commands.append(command))
    with pytest.raises(ValueError, match="invalid API key header"):
        agent.live_reply("Fix value.txt", [], str(tmp_path))
    assert commands == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "value", ["", "ascii", 'quotes " slash \\', "line\n\r\t\b\f", "файл 🐍", "\x00"]
)
def test_json_quote_round_trips_through_cpython_oracle(example_modules, value):
    encoded = example_modules["wire_json"].json_quote(value)
    assert json.loads(encoded) == value


def test_json_document_preserves_nested_unicode_and_raw_response(example_modules):
    payload = {"key": ["файл 🐍", {"empty": "", "null": None, "boolean": True, "number": -12.5e2}]}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    doc = example_modules["wire_json"].JsonDocument(raw)
    children = doc.items(doc.field(doc.root, "key"))
    assert len(children) == 2
    assert doc.string(children[0]) == "файл 🐍"
    assert doc.field(children[1], "missing") == -1
    assert doc.string(doc.field(children[1], "empty")) == ""
    assert json.loads(doc.raw(doc.root)) == payload
    assert json.loads(doc.raw(children[1])) == payload["key"][1]


@pytest.mark.parametrize(
    "raw",
    [
        '{"key":1,"key":2}',
        '{"key":"unescaped\nnewline"}',
        '{"key":"\\ud800"}',
        '{"key":"\\udc00"}',
        '{"key":NaN}',
        '{"key":01}',
        '{"key":1.}',
        '{"key":1e}',
        '{"key":true,}',
        "[1,]",
        "{} trailing",
    ],
)
def test_json_document_rejects_ambiguous_or_invalid_wire_input(example_modules, raw):
    with pytest.raises(ValueError):
        example_modules["wire_json"].JsonDocument(raw)


def native_scenario(name):
    if name == "failed_test":
        return (
            [response("run_test"), response("done"), response("fail", {"reason": "Test failed"})],
            {},
            "failed",
        )
    if name == "post_check_edit":
        return (
            [
                response("run_test"),
                response("edit", {"path": "value.txt", "old": "fixed", "new": "changed"}),
                response("done"),
                response("fail", {"reason": "Latest edit is unverified"}),
            ],
            {"initial": "fixed\n"},
            "failed",
        )
    if name == "invalid_response":
        malformed = response("write", {"path": "value.txt", "content": "unexpected"})
        malformed["choices"][0]["message"]["tool_calls"] *= 2
        return [malformed, response("fail", {"reason": "Invalid response"})], {}, "failed"
    if name == "fail":
        return [response("fail", {"reason": "Cannot repair this task"})], {}, "failed"
    if name == "exhausted":
        return (
            [response("read", {"path": "value.txt"}) for _ in range(3)],
            {"max_steps": 3},
            "exhausted",
        )
    options = {}
    old = "wrong"
    new = "fixed"
    if name in ("unicode_raw", "unicode_escaped", "crlf"):
        initial = 'wrong 🐍 "quotes"\n' if name != "crlf" else "wrong\r\nфайл 🐍\r\n"
        expected = initial.replace("wrong", "fixed")
        old = 'wrong 🐍 "quotes"' if name != "crlf" else "wrong\r\nфайл 🐍\r\n"
        new = 'fixed 🐍 "quotes"' if name != "crlf" else "fixed\r\nфайл 🐍\r\n"
        code = (
            "from pathlib import Path; import sys; "
            'print("VISIBLE_TEST_PASSED") if Path("value.txt").read_bytes() == '
            + repr(expected.encode())
            + " else sys.exit(1)"
        )
        options = {
            "initial": initial,
            "command": shlex.quote(sys.executable) + " -c " + shlex.quote(code),
            "escape_replay": name == "unicode_escaped",
        }
    return (
        [
            response("done"),
            response("read", {"path": "value.txt"}),
            response(
                "edit",
                {"path": "value.txt", "old": old, "new": new},
                escaped_arguments=name == "unicode_escaped",
            ),
            response("run_test"),
            response("done"),
        ],
        options,
        "complete",
    )


@pytest.mark.parametrize("launcher_variable", ["PYSTACHY_AGENT_BINARY", "PYSTACHY_AGENT_JIT"])
@pytest.mark.parametrize(
    "scenario",
    [
        "repair",
        "failed_test",
        "post_check_edit",
        "invalid_response",
        "fail",
        "exhausted",
        "unicode_raw",
        "unicode_escaped",
        "crlf",
    ],
)
@pytest.mark.skipif(shutil.which("timeout") is None, reason="The example requires timeout")
def test_optional_native_replay_matches_cpython_workspace_and_transcript(
    tmp_path, launcher_variable, scenario
):
    launcher_path = os.environ.get(launcher_variable)
    if not launcher_path:
        pytest.skip(f"Set {launcher_variable} to an executable native example launcher")
    replies, options, status = native_scenario(scenario)
    python_root = tmp_path / "python"
    native_root = tmp_path / "native"
    python_root.mkdir()
    native_root.mkdir()
    expected, expected_events, expected_files = run_replay(python_root, replies, **options)
    actual, actual_events, actual_files = run_replay(
        native_root, replies, launcher=[launcher_path], **options
    )
    assert_status(expected, expected_events, status)
    assert_status(actual, actual_events, status)
    assert actual_files == expected_files
    assert actual_events == expected_events
