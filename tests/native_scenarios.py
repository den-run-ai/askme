"""Recorded-reply scenarios shared by the reference harness and the native port.

Each scenario fixes a prompt, an initial workspace, the environment and the
exact model replies. ``askme.py`` and ``native/askme_native.py`` must turn one
scenario into the same requests, events, console lines, result and workspace.

The scenarios are written here because tool-call replies nest JSON inside
JSON; ``native/scenarios/*.json`` is the generated, committed form that other
programs replay. ``python3 tests/native_scenarios.py`` rewrites those files
and ``tests/test_native_port.py`` fails when they drift.
"""

import hashlib
import json
import sys
from pathlib import Path

SCENARIO_DIR = Path(__file__).resolve().parents[1] / "native" / "scenarios"


def text(obj, finish="stop", **extra):
    """A plain-JSON reply (planner, task replanner, validator)."""
    return raw(json.dumps(obj), finish, **extra)


def raw(content, finish="stop", **extra):
    """A reply whose message content is the given text."""
    reply = {
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": finish}
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 24, "total_tokens": 144},
        "model": "recorded-model",
    }
    reply.update(extra)
    return reply


def tool(name, finish="tool_calls", **arguments):
    """A native tool-call reply for one executor action."""
    return tool_raw(name, json.dumps(arguments), finish)


def tool_raw(name, arguments, finish="tool_calls"):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_0",
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ],
                },
                "finish_reason": finish,
            }
        ],
        "usage": {"prompt_tokens": 310, "completion_tokens": 42, "total_tokens": 352},
        "model": "recorded-model",
    }


def http(status, body_text):
    """An arbitrary HTTP reply: a status and a raw body."""
    return {"askme_replay": {"status": status, "text": body_text}}


def transport_error(message):
    return {"askme_replay": {"transport_error": message}}


def truncated(reply):
    """Mark a write reply as a trusted partial payload."""
    return {"askme_replay": {"content_truncated": True, "body": reply}}


def plan(*tasks):
    return text({"tasks": list(tasks)})


FAKE_CC = """#!/bin/sh
# A deterministic stand-in for a C compiler: it insists on <stdio.h>.
if grep -q '#include <stdio.h>' "$1"; then
  echo "built $1"
  exit 0
fi
echo "$1:3:5: error: implicit declaration of function 'printf'"
exit 1
"""

HELLO_C = 'int main(void) {\n    printf("hello\\n");\n    return 0;\n}\n'

NOTES = "".join(f"line {i}: the quick brown fox jumps over the lazy dog\n" for i in range(1, 121))
NOTES_SHA = hashlib.sha256(NOTES.encode()).hexdigest()[:12]

LIBRARY = {
    "src/app.py": "import util\n\n\ndef main():\n    return util.total([1, 2, 3])\n",
    "src/util.py": "def total(values):\n    result = 0\n    for value in values:\n"
    "        result -= value\n    return result\n",
    "tests/check.sh": "cd src && python3 -c 'import app; assert app.main() == 6; print(\"ok\")'\n",
    "docs/guide.md": "# Guide\n\nThe total helper adds values.\n",
    ".hidden/secret.txt": "not listed\n",
    "node_modules/pkg/index.js": "skipped\n",
}


def _scenarios():
    scenarios = {}

    def add(name, prompt, replay, files=None, env=None, args=None, exit_code=0):
        scenarios[name] = {
            "prompt": prompt,
            "args": list(args or []),
            "env": dict(env or {}),
            "files": dict(files or {}),
            "exit_code": exit_code,
            "replay": replay,
        }

    add(
        "hello",
        "Create hello.txt containing hello",
        [
            plan("Create hello.txt containing hello"),
            tool("write", arg="hello.txt", content="hello\n"),
            tool("done"),
        ],
    )

    # Compile failure -> deterministic include repair -> retry -> auto-done of
    # the planned include task -> deterministic validation.
    add(
        "compile_repair",
        "Compile main.c with sh cc.sh and fix any build error",
        [
            plan("Build main.c with sh cc.sh main.c", "Add #include <stdio.h> to main.c"),
            tool("shell", arg="sh cc.sh main.c"),
            tool("done", reasoning="built"),
        ],
        files={"cc.sh": FAKE_CC, "main.c": HELLO_C},
    )
    add(
        "compile_repair_disabled",
        "Compile main.c with sh cc.sh and fix any build error",
        [
            plan("Build main.c with sh cc.sh main.c"),
            tool("shell", arg="sh cc.sh main.c"),
            tool("read", arg="main.c"),
            tool("edit", arg="main.c", find="int main", replace="#include <stdio.h>\nint main"),
            tool("shell", arg="sh cc.sh main.c"),
            tool("done"),
        ],
        files={"cc.sh": FAKE_CC, "main.c": HELLO_C},
        env={"AGENT_COMPILE_REPAIR": "0"},
    )

    # Observation actions, a targeted edit, shell verification and the LLM
    # validator (no keyword-driven deterministic verdict is available).
    add(
        "navigate_and_edit",
        "Correct the total helper so the sum is positive",
        [
            plan("Inspect the project layout", "Correct total in src/util.py"),
            tool("tree"),
            tool("search", arg="def total", path="src"),
            tool("done"),
            tool("read", arg="src/util.py"),
            tool("edit", arg="src/util.py", find="result -= value", replace="result += value"),
            tool("shell", arg="sh tests/check.sh"),
            tool("done", reasoning="check passes"),
            text({"valid": True}),
        ],
        files=LIBRARY,
        env={"AGENT_FINAL_VALIDATE": "always"},
    )

    # Paged reads: line windows, cursor continuation, a stale cursor, and typed
    # read-argument errors. No validator reply is recorded, so the failed
    # steps make the run complete unverified.
    add(
        "read_pages",
        "Summarize notes.txt into summary.txt",
        [
            plan("Summarize notes.txt into summary.txt"),
            tool("read", arg="notes.txt"),
            tool("read", arg="notes.txt", cursor=1200, limit=60, sha256=NOTES_SHA),
            tool("read", arg="notes.txt", cursor=2400, limit=60, sha256="000000000000"),
            tool("read", arg="notes.txt", cursor=999999, limit=60, sha256=NOTES_SHA),
            tool("read", arg="notes.txt", cursor=10),
            tool("read", arg="notes.txt", offset=100, limit=5),
            tool("read", arg="notes.txt", offset=500),
            tool("read", arg="missing.txt"),
            tool("write", arg="summary.txt", content="120 numbered lines\n"),
            tool("done"),
        ],
        files={"notes.txt": NOTES},
    )

    # Duplicate/stuck protection for writes, reads and shells, with the
    # task-local replan that follows a stuck attempt.
    add(
        "duplicate_guards",
        "Create data.txt and report its size",
        [
            plan("Create data.txt with three lines"),
            tool("write", arg="data.txt", content="a\nb\nc\n"),
            tool("write", arg="data.txt", content="a\nb\nc\n"),
            tool("read", arg="data.txt"),
            tool("read", arg="data.txt"),
            tool("read", arg="data.txt"),
            text({"task": "Count the lines of data.txt with wc"}),
            tool("shell", arg="wc -l data.txt"),
            tool("shell", arg="wc -l data.txt"),
            tool("shell", arg="wc -l data.txt"),
            plan("Report the size of data.txt"),
            tool("shell", arg="wc -c data.txt"),
            tool("done"),
            text({"valid": True}),
        ],
    )

    # A failed task, a rejected task-local replan (near duplicate), and a full
    # replan that sees the typed errors.
    add(
        "replan_after_failure",
        "Create report.txt from input.txt",
        [
            plan("Copy input.txt to report.txt"),
            tool("shell", arg="cp input.txt report.txt"),
            tool("fail", reasoning="input.txt does not exist"),
            text({"task": "Copy input.txt to report.txt again"}),
            plan("Create report.txt with the text ready"),
            tool("write", arg="report.txt", content="ready\n"),
            tool("done"),
            text({"valid": False, "reason": "report is not verified", "missing": ["a check"]}),
            plan("Verify report.txt"),
            tool("shell", arg="grep -q ready report.txt && echo verified"),
            tool("done"),
            text({"valid": True}),
        ],
    )

    # An explicit validator rejection is not erased by a later pass without
    # new evidence, nor by a recheck that returns no verdict: the run exhausts
    # with validation "failed".
    add(
        "validation_rejected",
        "Write answer.txt",
        [
            plan("Write answer.txt"),
            tool("write", arg="answer.txt", content="41\n"),
            tool("done"),
            text({"valid": False, "reason": "answer must be 42", "missing": ["42", "", 7]}),
            plan("Look at the workspace"),
            tool("tree"),
            tool("done"),
            plan("Check answer.txt"),
            tool("shell", arg="cat answer.txt"),
            tool("done"),
            text({"verdict": "wrong shape"}),
        ],
        env={"AGENT_FINAL_VALIDATE": "always"},
        exit_code=1,
    )

    # Transport and decode retries: HTTP 503, a connection error, an API error
    # body, a non-JSON body, repairable and unrepairable JSON, tool replies
    # with no call, two calls, cut-off arguments and an unknown tool.
    add(
        "retries",
        "Create ok.txt and done.txt",
        [
            http(503, "busy"),
            transport_error("connection reset by peer"),
            raw('```json\n{"tasks": ["Create ok.txt", "Create done.txt"],}\n```'),
            {"error": {"message": "rate limited", "code": 429}},
            raw("I will call a tool next."),
            tool("write", arg="ok.txt", content="ok\n"),
            tool_raw("write", '{"arg": "ok.txt", "content": "ok', finish="length"),
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {"function": {"name": "done", "arguments": "{}"}},
                                {"function": {"name": "tree", "arguments": "{}"}},
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
            tool("done"),
            tool("launch", arg="rocket"),
            http(200, "<html>gateway glitch</html>"),
            tool("write", arg="done.txt", content="done\n"),
            tool_raw("done", '{"action": "done"}'),
            tool_raw("done", "[1, 2]"),
            tool_raw("done", ""),
        ],
    )

    # Every planning attempt fails differently; the run exhausts.
    add(
        "exhausted",
        "Do something impossible",
        [
            raw("not json at all"),
            raw("still not json"),
            raw("no"),
            http(401, '{"error": "bad key"}'),
            plan(""),
            text({"tasks": []}),
            text({"plan": ["wrong key"]}),
        ],
        exit_code=1,
    )

    # Write pressure and the observation tail reserve on a write-shaped task.
    add(
        "write_pressure",
        "Implement the missing feature in feature.py",
        [
            plan("Implement the missing feature in feature.py"),
            tool("tree"),
            tool("read", arg="feature.py"),
            tool("search", arg="feature"),
            tool("tree", arg="."),
            tool("write", arg="feature.py", content="def feature():\n    return 1\n"),
            tool("done"),
        ],
        files={"feature.py": "# TODO\n", "docs/plan.md": "feature\n"},
        args=["--max-steps", "6"],
    )
    add(
        "observe_tail_exhausted",
        "Implement the missing feature in feature.py",
        [
            plan("Implement the missing feature in feature.py"),
            tool("tree"),
            tool("read", arg="feature.py"),
            tool("tree", arg="."),
            tool("search", arg="feature"),
            text({"task": "Inspect feature.py"}),
            plan("Write feature.py"),
            tool("write", arg="feature.py", content="def feature():\n    return 1\n"),
            tool("done"),
            text({"valid": True}),
        ],
        files={"feature.py": "# TODO\n"},
        args=["--max-steps", "4"],
    )

    # Rewrite damping: repeated full rewrites of one target are steered to
    # verification, then skipped.
    add(
        "rewrite_loop",
        "Write poem.txt",
        [
            plan("Write poem.txt"),
            tool("write", arg="poem.txt", content="one\n"),
            tool("write", arg="poem.txt", content="two\n"),
            tool("write", arg="poem.txt", content="three\n"),
            tool("write", arg="poem.txt", content="four\n"),
            tool("shell", arg="cat poem.txt"),
            tool("done"),
        ],
    )

    # The lifecycle arm: done is refused until a shell check verifies the
    # mutation; a same-target rewrite is steered to verification.
    add(
        "lifecycle",
        "Write poem.txt",
        [
            plan("Write poem.txt"),
            tool("write", arg="poem.txt", content="one\n"),
            tool("done"),
            tool("write", arg="poem.txt", content="two\n"),
            tool("shell", arg="cat poem.txt"),
            tool("done"),
        ],
        env={"AGENT_STEP_POLICY": "lifecycle"},
    )

    # Trusted partial-write recovery: a truncated write keeps its complete
    # lines and reports a resume anchor; done is refused until an append
    # completes the file. A write truncated before any complete line leaves a
    # pending obligation that forbids append until a full first chunk lands.
    add(
        "truncated_write",
        "Write log.txt and list.txt",
        [
            plan("Write log.txt", "Write list.txt"),
            truncated(tool("write", arg="log.txt", content="first\nsecond\nthi")),
            tool("done"),
            tool("write", arg="log.txt", content="third\n", append=True),
            tool("done"),
            truncated(tool("write", arg="list.txt", content="partial line with no newline")),
            tool("write", arg="list.txt", content="more\n", append=True),
            tool("done"),
            tool("write", arg="list.txt", content="alpha\nbeta\n"),
            tool("done"),
        ],
    )

    # A shell timeout is retried once with a longer deadline.
    add(
        "timeout_retry",
        "Run the slow step",
        [
            plan("Run slow.sh"),
            tool("shell", arg="sh slow.sh", timeout=5),
            tool("shell", arg="sh slow.sh"),
            tool("done"),
        ],
        files={"slow.sh": "if [ -f marker ]; then echo fast; exit 0; fi\ntouch marker\nsleep 30\n"},
        env={"AGENT_FINAL_VALIDATE": "0"},
    )

    # OpenRouter request shaping: provider routing, reasoning effort, token
    # floors, the strict retry turn, and served-model route metadata.
    add(
        "openrouter",
        "Create note.txt",
        [
            raw("thinking out loud, no JSON"),
            raw('<think>plan it</think>{"tasks": ["Create note.txt"]}'),
            tool(
                "write",
                arg="note.txt",
                content="noté ✓\n",
            ),
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {"function": {"name": "done", "arguments": {"reasoning": "ok"}}}
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 3,
                    "total_tokens": 12,
                    "cost": 0.0002,
                },
                "provider": "Fallback",
                "openrouter_metadata": {
                    "endpoints": {
                        "available": [
                            {"provider": "Other", "model": "vendor/other"},
                            {
                                "provider": "Parasail",
                                "model": "vendor/served-model",
                                "selected": True,
                                "attempt": 2,
                            },
                        ]
                    }
                },
            },
        ],
        env={
            "LLM_BACKEND": "openrouter",
            "OPENROUTER_API_KEY": "sk-recorded-not-a-real-key",
            "OPENROUTER_MODEL": "vendor/requested-model",
            "OPENROUTER_PROVIDER": "Parasail",
            "OPENROUTER_ALLOW_FALLBACKS": "0",
            "OPENROUTER_REQUIRE_PARAMETERS": "1",
            "OPENROUTER_REASONING_EFFORT": "low",
            "LLM_CAPABILITY_PROFILE": "legacy-e4b-m1-16k-v1",
        },
        args=["--reasoning-policy", "gated"],
    )

    # Limits and policy flags supplied as arguments and environment.
    add(
        "limits",
        "Write a.txt then b.txt then c.txt",
        [
            plan("Write a.txt", "Write b.txt", "Write c.txt"),
            tool("write", arg="a.txt", content={"k": [1, 2], "note": "é"}),
            tool("done", arg=None, reasoning=None),
            tool("write", arg="nested/dir/b.txt", content=["x", {"y": None}]),
            tool("tree", arg=None),
            tool("done"),
        ],
        env={
            "ALLOW_SYSTEM_INSTALLS": "1",
            "ALLOW_NETWORK": "0",
            "AGENT_GOAL_CONTEXT_CHARS": "12",
            "AGENT_REASONING_POLICY": "off",
        },
        args=[
            "--max-tasks",
            "2",
            "--max-replans",
            "1",
            "--capability-profile",
            "legacy-e4b-m1-16k-v1",
        ],
    )
    return scenarios


SCENARIOS = _scenarios()


def scenario_json(name):
    return json.dumps(SCENARIOS[name], indent=2, ensure_ascii=False) + "\n"


def main():
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    for name in SCENARIOS:
        (SCENARIO_DIR / f"{name}.json").write_text(scenario_json(name), encoding="utf-8")
    print(f"wrote {len(SCENARIOS)} scenarios to {SCENARIO_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
