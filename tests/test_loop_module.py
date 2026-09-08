"""Standalone loop composition and the public facade's late-binding contracts."""

import dataclasses
import inspect
import json
import os
import shlex
import shutil
import subprocess
import sys
from functools import partial
from pathlib import Path
from unittest.mock import Mock

import pytest
from _test_support import ScriptedClient

import actions
import askme
import llm
import loop
import policies


def test_loop_import_does_not_load_facade_dotenv_or_infer(tmp_path):
    for module in (loop, policies, llm, actions):
        shutil.copy(Path(module.__file__), tmp_path)
    (tmp_path / ".env").write_text("ASKME_IMPORT_SENTINEL=loaded\n")
    env = {
        "PATH": os.defpath,
        "AGENT_REASONING_POLICY": "invalid-unused-policy",
        "AGENT_GOAL_CONTEXT_CHARS": "invalid-unused-budget",
    }
    for name in ("SYSTEMROOT", "WINDIR"):
        if name in os.environ:
            env[name] = os.environ[name]
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, sys, requests\n"
            "def blocked(*args, **kwargs):\n"
            "    raise AssertionError('Unexpected HTTP during loop import')\n"
            "requests.sessions.Session.send = blocked\n"
            "import loop\n"
            "assert 'askme' not in sys.modules\n"
            "assert 'ASKME_IMPORT_SENTINEL' not in os.environ\n"
            "assert loop.RunConfig.from_env({}).llm.model == 'local-model'\n"
            "print('independent loop import')\n",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert process.stdout == "independent loop import\n"
    assert process.stderr == ""


@pytest.mark.parametrize(
    "name",
    [
        "TaskAttemptState",
        "TaskReplanResult",
        "GuardThresholds",
        "RunDependencies",
        "RunWorkspace",
        "SYSTEM_PLAN",
        "SYSTEM_STEP",
        "SYSTEM_TASK_REPLAN",
        "SYSTEM_VALIDATE",
        "_StepContext",
        "_config_hash",
        "_deterministic_check",
        "_compile_repair_candidates",
        "_task_satisfied_by_deterministic_repair",
        "summarize_errors",
    ],
)
def test_facade_reexports_one_canonical_loop_record_or_helper(name):
    assert getattr(askme, name) is getattr(loop, name)


@pytest.mark.parametrize("name", ["StepRecorder", "RunState", "_RunController"])
def test_facade_adapters_inherit_every_algorithm_without_copying(name):
    canonical = getattr(loop, name)
    facade = getattr(askme, name)
    assert issubclass(facade, canonical)
    for method_name, method in vars(canonical).items():
        if inspect.isfunction(method) and method_name != "__init__":
            assert getattr(facade, method_name) is method


def test_run_config_adapter_is_frozen_and_keeps_selected_settings_factory(monkeypatch):
    assert inspect.signature(askme.RunConfig) == inspect.signature(loop.RunConfig)
    assert dataclasses.fields(askme.RunConfig) == dataclasses.fields(loop.RunConfig)
    monkeypatch.setattr(askme, "LLM_TIMEOUT", 31)
    facade = askme.RunConfig.from_env({})
    standalone = loop.RunConfig.from_env({})
    assert type(facade) is askme.RunConfig
    assert type(facade.llm) is askme.LLMSettings
    assert facade.llm.timeout == 31
    assert type(standalone) is loop.RunConfig
    assert standalone.llm.timeout == llm.LLM_TIMEOUT
    assert dataclasses.replace(facade, max_steps=2).max_steps == 2
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(facade, "max_steps", 2)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(facade, "unregistered_field", "mutable")


def _context(client, **defaults):
    return loop.PromptContext(
        defaults=loop.PromptDefaults(**defaults),
        ask=client.ask,
        policy=lambda: {"allow_system_installs": False, "allow_network": False},
        log=Mock(),
        deterministic_check=lambda *args: None,
    )


def test_direct_prompt_builders_use_explicit_defaults_and_compact_evidence(monkeypatch):
    monkeypatch.setattr(askme, "ask_llm", Mock(side_effect=AssertionError("facade used")))
    client = ScriptedClient([{"tasks": ["greet"]}, {"action": "done"}])
    context = _context(
        client,
        system_plan="synthetic plan",
        system_step="synthetic step",
        planner_tokens=21,
        step_tokens=22,
        max_tasks=2,
        max_step_history=1,
        observe_state_chars=4,
    )
    state = {
        "all_steps": [
            {"action": "write", "arg": "a.txt", "ok": True, "_content": "PRIVATE PAYLOAD"}
        ],
        "last_steps": [
            {"action": "read", "arg": "old.txt", "ok": True, "output": "discard"},
            {"action": "read", "arg": "dir/a.txt", "ok": True, "output": "visible"},
        ],
    }
    assert loop.get_plan("greet", state, context=context) == {"tasks": ["greet"]}
    assert loop.get_step("greet", state, context=context) == {"action": "done"}
    plan, step = client.calls
    assert plan["messages"][0]["content"] == "synthetic plan"
    assert step["messages"][0]["content"] == "synthetic step"
    assert (plan["max_tokens"], step["max_tokens"]) == (21, 22)
    assert plan["expect_context"] == {"max_tasks": 2}
    assert "PRIVATE PAYLOAD" not in json.dumps(client.calls)
    slim = json.loads(step["messages"][1]["content"].split("STATE:\n")[1])
    assert slim["last_steps"] == [{"action": "read", "arg": "a.txt", "ok": True, "output": "visi"}]
    assert state["last_steps"][-1]["output"] == "visible"


def test_direct_replan_and_validation_share_client_contracts(tmp_path):
    client = ScriptedClient([{"task": "fix new.py"}, {"valid": True}])
    context = _context(client, task_replan_tokens=23, validation_tokens=24)
    result = loop.replan_task(
        "fix old.py", ["[missing_file] old.py"], [], {}, "fix bug", context=context
    )
    assert result == loop.TaskReplanResult("fix new.py", None)
    verdict = loop._validate_completion("fix bug", {}, tmp_path, context=context)
    assert verdict == llm.ValidationResponse(valid=True)
    assert [call["expect"] for call in client.calls] == ["task_replan", "validation"]
    assert [call["max_tokens"] for call in client.calls] == [23, 24]
    assert all(call["max_retries"] == 0 for call in client.calls)
    deterministic = dataclasses.replace(context, deterministic_check=lambda *args: False)
    assert loop._validate_completion("fix bug", {}, tmp_path, context=deterministic) == (
        llm.ValidationResponse(
            valid=False, deterministic=True, reason="deterministic completion check failed"
        )
    )
    assert len(client.calls) == 2


def test_state_recorder_keeps_live_aliases_and_intentional_receipt_copies(tmp_path):
    events = []
    state = loop.RunState("off", 300, clock=lambda: 7, event_sink=events.append)
    state.data["last_steps"] = []
    assert state.recorder.state is state.data
    assert state.recorder.history is state.history
    action = {"action": "write", "arg": "hi.txt", "content": "hi"}
    receipt = actions.StepReceipt.executed(action, actions.ActionResult(True, "written"), tmp_path)
    state.recorder.selected()
    state.recorder.executed()
    entry = state.recorder.record(receipt, 0, 0)
    assert entry is receipt.entry is state.data["last_steps"][0]
    assert state.data["all_steps"][0] == entry
    assert state.data["all_steps"][0] is not entry
    state.recorder.append_recovery_hint("retry")
    assert state.data["last_steps"][0]["output"] == "written → retry"
    assert state.data["all_steps"][0]["output"] == "written → retry"
    assert (state.data["selected_steps"], state.data["executed_steps"]) == (1, 1)
    assert len(events) == len(state.history) == 1
    assert state.history[0]["result"]["output"] == "written"
    assert "output" not in events[0]  # JSONL's compact projection is not raw tool output.


def _standalone_hooks(context, logs, events):
    """Compose only canonical modules; no askme callbacks or hidden default client."""
    unused = Mock(side_effect=AssertionError("unused settings/client factory called"))
    hooks = loop.LoopHooks(
        current_llm_settings=unused,
        clock_factory=lambda: lambda: 10,
        step_policies=lambda: policies._STEP_POLICY_ARMS,
        get_plan=partial(loop.get_plan, context=context),
        get_step=partial(loop.get_step, context=context),
        replan_task=partial(loop.replan_task, context=context),
        preflight=partial(loop.preflight_probe, tools=(), package_managers=()),
        execute=lambda action, cwd: actions.ActionExecutor(cwd).dispatch(action).to_dict(),
        log=logs.append,
        event=events.append,
        resolve_llm_settings=loop._resolve_run_llm_settings,
        make_client=unused,
        make_frozen_client=unused,
        make_run_state=loop.RunState,
        make_obligations=policies.WriteObligations,
        make_completion=partial(
            policies.CompletionPolicy,
            validator=partial(loop._validate_completion, context=context),
        ),
        compile_repair=loop._compile_repair_action,
        repair_satisfied=loop._task_satisfied_by_deterministic_repair,
    )
    return hooks, unused


@pytest.mark.parametrize("step_policy", ["heuristic", "lifecycle"])
def test_standalone_controller_uses_injected_collaborators_and_one_state(
    tmp_path, monkeypatch, step_policy
):
    for name in ("get_plan", "get_step", "execute", "log", "_run_log"):
        monkeypatch.setattr(askme, name, Mock(side_effect=AssertionError("facade used")))
    client = ScriptedClient(
        [
            {"tasks": ["greet"]},
            {"action": "write", "arg": "hi.txt", "content": "hi\n"},
            {
                "action": "shell",
                "arg": shlex.join(
                    [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; assert Path('hi.txt').read_bytes() == b'hi\\n'",
                    ]
                ),
            },
            {"action": "done"},
        ]
    )
    client.settings = llm.LLMSettings.from_env({})
    logs, events = [], []
    hooks, unused = _standalone_hooks(_context(client), logs, events)
    # An explicit config/client must not validate unrelated raw fallback values.
    controller = loop._RunController(
        "greet",
        str(tmp_path),
        config=loop.RunConfig(
            reasoning_policy="off", max_steps=4, final_validate="0", step_policy=step_policy
        ),
        dependencies=loop.RunDependencies(llm_client=client, event_sink=events.append),
        defaults=loop.LoopDefaults(reasoning_policy="invalid-unused", max_steps="invalid-unused"),
        hooks=hooks,
    )
    result = controller.run()
    assert result["status"] == "complete"
    assert result["state"] is controller.state is controller.run_state.data
    assert result["log"] is controller.history is controller.run_state.history
    assert controller.recorder is controller.run_state.recorder
    assert controller.recorder.state is result["state"]
    assert controller.recorder.history is result["log"]
    assert result["state"]["completed_tasks"] == ["greet"]
    assert [
        result["state"][name] for name in ("selected_steps", "executed_steps", "skipped_steps")
    ] == [
        3,
        2,
        0,
    ]
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == {"hi.txt": b"hi\n"}
    assert events[0]["event"] == "run_start"
    assert events[-1]["event"] == "run_end"
    assert events[0]["llm_provenance"] == "injected_client_settings"
    assert controller.config_metadata()["step_policy"] == step_policy
    assert logs[0] == "Prompt: greet"
    assert not client.replies
    unused.assert_not_called()


def test_facade_keeps_late_planning_dispatch_log_and_recorder_hooks(tmp_path, monkeypatch):
    early = Mock(side_effect=AssertionError("stale facade callback used"))
    for name in ("get_plan", "get_step", "execute", "log", "_run_log", "preflight_probe"):
        monkeypatch.setattr(askme, name, early)
    controller = askme._RunController(
        "greet",
        str(tmp_path),
        config=askme.RunConfig(final_validate="0"),
        dependencies=askme.RunDependencies(llm_client=ScriptedClient([]), clock=lambda: 10),
    )
    logs, events = [], []
    plan = Mock(return_value={"tasks": ["greet"]})
    step = Mock(
        side_effect=[
            {"action": "write", "arg": "hi.txt", "content": "hi\n"},
            {"action": "done"},
        ]
    )
    execute = Mock(
        side_effect=lambda action, cwd: actions.ActionExecutor(cwd).dispatch(action).to_dict()
    )
    monkeypatch.setattr(askme, "get_plan", plan)
    monkeypatch.setattr(askme, "get_step", step)
    monkeypatch.setattr(askme, "execute", execute)
    monkeypatch.setattr(askme, "log", logs.append)
    monkeypatch.setattr(askme, "_run_log", events.append)
    monkeypatch.setattr(
        askme, "preflight_probe", partial(loop.preflight_probe, tools=(), package_managers=())
    )
    result = controller.run()
    assert result["status"] == "complete"
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == {"hi.txt": b"hi\n"}
    assert plan.call_count == execute.call_count == 1
    assert step.call_count == 2
    assert {event["event"] for event in events} >= {"run_start", "step", "step_control", "run_end"}
    assert logs[0] == "Prompt: greet"
    early.assert_not_called()


def test_facade_state_captures_clock_and_guard_defaults_but_not_event_sink(monkeypatch):
    early_events = Mock(side_effect=AssertionError("stale event sink"))
    monkeypatch.setattr(askme, "_run_log", early_events)
    clock = Mock(side_effect=[10, 12])
    monkeypatch.setattr(askme.time, "time", clock)
    monkeypatch.setattr(askme, "REWRITE_PRESSURE_WRITES", 4)
    monkeypatch.setattr(askme, "REWRITE_SKIP_WRITES", 5)
    state = askme.RunState("off", 100)
    events = []
    monkeypatch.setattr(askme, "_run_log", events.append)
    monkeypatch.setattr(askme.time, "time", Mock(side_effect=AssertionError("new clock")))
    monkeypatch.setattr(askme, "REWRITE_SKIP_WRITES", 99)
    state.recorder.control(0, 0, "done")
    assert state.elapsed() == 2
    assert (state.rewrite_pressure_writes, state.rewrite_skip_writes) == (4, 5)
    assert events == [{"event": "step_control", "task_index": 0, "step": 0, "action": "done"}]
    assert isinstance(state.recorder, askme.StepRecorder)
    early_events.assert_not_called()
