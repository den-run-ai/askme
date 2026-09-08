"""Offline, subprocess-safe transcripts for behavior-preserving module moves.

Capture against a selected runtime checkout; never call a model or host shell.
The baseline fixture records hashes of complete normalized transcripts, not
only terminal statuses. Existing scenario tests retain detailed assertions.
"""

import argparse
import contextlib
import copy
import hashlib
import importlib
import inspect
import io
import json
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


def _write(content):
    return {"action": "write", "arg": "result.txt", "content": content}


def cases():
    done = {"action": "done"}
    verify = {"action": "shell", "arg": "verify result.txt"}
    return {
        "write_edit_verify": {
            "actions": [
                {"action": "read", "arg": "seed.txt"},
                _write("first\n"),
                {"action": "edit", "arg": "result.txt", "find": "first", "replace": "final"},
                verify,
                done,
            ],
        },
        "lifecycle_rewrite": {
            "config": {"step_policy": "lifecycle"},
            "actions": [
                _write("first\n"),
                _write("blocked\n"),
                verify,
                _write("final\n"),
                verify,
                done,
            ],
        },
        "duplicate_write": {
            "actions": [_write("first\n"), _write("first\n"), _write("first\n"), verify, done],
        },
        "observation_stall": {
            "config": {"max_steps": 4},
            "actions": [{"action": "read", "arg": "seed.txt"}] * 4,
        },
        "accepted_fail": {"actions": [{"action": "fail", "arg": "cannot proceed"}]},
        "empty_write": {"actions": [_write(""), done]},
        "partial_write": {
            "actions": [
                {**_write("first\n"), "_scripted_partial": True},
                done,
                {**_write("last\n"), "append": True},
                verify,
                done,
            ],
        },
        "validation_recheck": {
            "config": {"max_replans": 2, "final_validate": "always"},
            "actions": [_write("first\n"), done, _write("final\n"), verify, done],
            "validations": [
                {"valid": False, "reason": "needs final text", "missing": ["final text"]},
                {"valid": True, "reason": "verified", "missing": []},
            ],
        },
        "validator_unavailable": {
            "config": {"final_validate": "always"},
            "actions": [_write("first\n"), done],
            "validations": [None],
        },
    }


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def capture(module, spec, workspace):
    workspace.mkdir()
    (workspace / "seed.txt").write_text("seed\n", encoding="utf-8")
    replies = {
        "action": copy.deepcopy(spec["actions"]),
        "validation": copy.deepcopy(spec.get("validations", [])),
    }
    calls, events, lines, dispatched = [], [], [], []
    settings = module.LLMSettings.from_env({"LLM_MODEL": "offline-characterization"})

    class Client:
        def __init__(self):
            self.settings = settings

        def ask(self, messages, **kwargs):
            calls.append(copy.deepcopy({"messages": messages, **kwargs}))
            expected = kwargs["expect"]
            if expected == "plan":
                return {"tasks": ["write result.txt"]}
            if expected not in replies or not replies[expected]:
                raise AssertionError(f"Unexpected scripted call: {expected}")
            value = replies[expected].pop(0)
            if value is None:
                raise module.LLMTransportError("scripted unavailable validator")
            if value.pop("_scripted_partial", False):
                return module.DecodedAction(
                    module.parse_action_envelope(value),
                    module.ActionTransport(content_truncated=True),
                )
            return value

    class Executor(module.ActionExecutor):
        def dispatch(self, action):
            dispatched.append(copy.deepcopy(dict(action)))
            if action["action"] == "shell":
                assert action["arg"] == "verify result.txt"
                return module.ActionResult(True, (workspace / "result.txt").read_text())
            return super().dispatch(action)

    config = {
        "llm": settings,
        "allow_network": False,
        "allow_system_installs": False,
        "reasoning_policy": "off",
        "max_replans": 1,
        "max_tasks": 1,
        "max_steps": 8,
        "goal_context_chars": 300,
        "final_validate": "0",
        "compile_repair": False,
        "step_policy": "heuristic",
        "write_pressure_observations": 3,
        "observe_tail_reserve": 3,
        "rewrite_pressure_writes": 2,
        "rewrite_skip_writes": 3,
        "max_task_local_replans": 0,
        **spec.get("config", {}),
    }
    environment = {
        "platform": "test",
        "arch": "test",
        "working_dir": str(workspace),
        "available_tools": [],
        "missing_tools": [],
        "package_managers": [],
        "dir_listing": ["seed.txt"],
    }
    with patch.object(module, "preflight_probe", return_value=environment):
        result = module.run_result(
            "write result.txt",
            working_dir=str(workspace),
            config=module.RunConfig(**config),
            dependencies=module.RunDependencies(
                llm_client=Client(),
                action_executor=Executor(str(workspace)),
                clock=lambda: 0.0,
                log_sink=lines.append,
                event_sink=lambda event: events.append(copy.deepcopy(event)),
            ),
        )

    # Runtime mutation identities resolve symlinks, while prompts retain the
    # supplied path. Normalize both roots, longest first: macOS /tmp resolves
    # to /private/tmp, where replacing only /tmp would leave /private behind.
    workspace_prefixes = sorted({str(workspace), str(workspace.resolve())}, key=len, reverse=True)

    def normalized(value):
        if isinstance(value, str):
            for prefix in workspace_prefixes:
                value = value.replace(prefix, "<workspace>")
            return value
        if isinstance(value, dict):
            return {normalized(key): normalized(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [normalized(item) for item in value]
        return value

    trace = normalized(
        {
            "calls": calls,
            "events": events,
            "lines": lines,
            "result": result,
            "dispatched": dispatched,
        }
    )
    return {
        "transcript_sha256": {key: _digest(value) for key, value in trace.items()},
        "status": result["status"],
        "outcome": result["outcome"],
        "remaining_scripted_replies": {key: len(value) for key, value in replies.items()},
        "workspace": {
            path.relative_to(workspace).as_posix(): path.read_bytes().hex()
            for path in sorted(workspace.rglob("*"))
            if path.is_file()
        },
    }


def public_contract(module):
    names = [
        "ask_llm",
        "run",
        "run_result",
        "execute",
        "get_plan",
        "get_step",
        "replan_task",
        "LLMClient",
        "LLMSettings",
        "RunConfig",
        "RunDependencies",
        "RunState",
        "TaskAttemptState",
        "StepRecorder",
    ]
    help_output = io.StringIO()
    with contextlib.redirect_stdout(help_output):
        try:
            module._main(["--help"])
        except SystemExit as exc:
            assert exc.code == 0
    return {
        "public_names": sorted(name for name in vars(module) if not name.startswith("_")),
        # A re-export may change a type's defining module, not its call contract.
        "signatures": {
            name: re.sub(
                r"\b(?:askme|llm|policies|loop)\.",
                "",
                str(inspect.signature(getattr(module, name))),
            )
            for name in names
        },
        "cli_help": help_output.getvalue().replace(Path(sys.argv[0]).name, "askme.py"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.runtime_root.resolve()))
    import requests

    def forbidden(*args, **kwargs):
        raise AssertionError("Characterization must not make HTTP requests")

    with patch.object(requests.sessions.Session, "send", forbidden):
        module = importlib.import_module("askme")
        with tempfile.TemporaryDirectory(prefix="askme-parity-") as directory:
            result = {
                "contract": public_contract(module),
                "cases": {
                    name: capture(module, spec, Path(directory) / name)
                    for name, spec in cases().items()
                },
            }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
