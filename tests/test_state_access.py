"""Live state access, recorder identity and terminal evidence remain compatible."""

import ast
import inspect
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _test_support import ScriptedClient

import actions
import askme
import loop
import state as state_module


@pytest.mark.parametrize("owner", [loop._RunController, state_module.StepRecorder])
def test_state_mutations_use_the_typed_owner_boundary(owner):
    tree = ast.parse(inspect.getsource(owner))
    raw_accesses = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "state"
    ]
    assert raw_accesses == []


def test_state_is_a_leaf_import_without_configuration_or_clients(tmp_path):
    for module in (state_module, actions):
        shutil.copy(Path(module.__file__), tmp_path)
    (tmp_path / ".env").write_text("ASKME_STATE_IMPORT_SENTINEL=loaded\n")
    env = {"PATH": os.defpath}
    for name in ("SYSTEMROOT", "WINDIR"):
        if name in os.environ:
            env[name] = os.environ[name]
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, sys\n"
            "import state\n"
            "assert not {'askme', 'loop', 'llm', 'policies'} & set(sys.modules)\n"
            "assert 'ASKME_STATE_IMPORT_SENTINEL' not in os.environ\n"
            "print('independent state import')\n",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert process.stdout == "independent state import\n"
    assert process.stderr == ""


@pytest.mark.parametrize(
    ("name", "before", "after"),
    [
        ("errors", ["old"], ["new"]),
        ("completed_tasks", ["old"], ["new"]),
        ("completed_step_groups", [[]], [[{"action": "read"}]]),
        ("all_steps", [], [{"action": "read"}]),
        ("last_steps", [], [{"action": "write"}]),
        ("selected_steps", 1, 2),
        ("executed_steps", 1, 2),
        ("skipped_steps", 1, 2),
        ("task_start_step_count", 1, 2),
        ("planning_attempt", 0, 1),
        ("current_task", "old", "new"),
        ("task_index", "1/2", "2/2"),
        ("environment", {"old": True}, {"new": True}),
        ("policy", {"allow_network": True}, {"allow_network": False}),
        ("pending_empty_writes", {}, {"target": "legacy"}),
    ],
)
def test_typed_fields_share_live_mapping_values_and_replacements(name, before, after):
    extra = {"keep": []}
    data = {name: before, "legacy_extension": extra}
    progress = state_module.RunProgress(data)
    assert progress.data is data
    assert getattr(progress, name) is before
    setattr(progress, name, after)
    assert data == {name: after, "legacy_extension": extra}
    assert data[name] is after
    data[name] = before
    assert getattr(progress, name) is before
    assert data["legacy_extension"] is extra


def test_sparse_compatibility_mapping_is_not_populated_by_read_views():
    data = {"legacy_extension": "untouched"}
    progress = state_module.RunProgress(data)
    assert progress.optional_last_steps == []
    assert progress.optional_all_steps == []
    assert progress.optional_pending_empty_writes == {}
    progress.optional_last_steps.append({"action": "read"})
    assert progress.optional_last_steps == []
    for field in ("errors", "last_steps", "all_steps", "pending_empty_writes", "selected_steps"):
        with pytest.raises(KeyError, match=field):
            getattr(progress, field)
    assert data == {"legacy_extension": "untouched"}


def test_pending_record_has_detached_historical_projection_without_rewriting_legacy():
    legacy = {"name": "old", "append_target": "old-target", "extra": True}
    data = {"pending_empty_writes": {"old": legacy}}
    progress = state_module.RunProgress(data)
    record = state_module.PendingWrite("result.txt", False, ("physical-target",), "result.txt")
    progress.set_pending_write("new", record)
    assert data["pending_empty_writes"] == {
        "old": legacy,
        "new": {
            "name": "result.txt",
            "append_allowed": False,
            "append_targets": ["physical-target"],
            "recovery_arg": "result.txt",
        },
    }
    assert data["pending_empty_writes"]["old"] is legacy
    data["pending_empty_writes"]["new"]["append_targets"].append("extra")
    assert record.append_targets == ("physical-target",)
    assert record.describe()["append_targets"] == ["physical-target"]


def test_recorder_uses_current_mapping_but_preserves_receipt_projection_identity(tmp_path):
    run_state = state_module.RunState("off", 300, clock=lambda: 7.0)
    original = run_state.data
    assert run_state.recorder.state is original
    assert run_state.recorder.history is run_state.history
    replacement = {**original, "last_steps": [], "all_steps": []}
    run_state.data = replacement
    assert run_state.progress.data is replacement
    # Recorder's public mapping is separately replaceable; no hidden rebinding.
    run_state.recorder.selected()
    assert original["selected_steps"] == 1
    assert replacement["selected_steps"] == 0
    run_state.recorder.state = replacement
    run_state.recorder.executed()
    assert replacement["executed_steps"] == 1
    assert original["executed_steps"] == 0
    action = actions.parse_action_envelope({"action": "read", "arg": "result.txt"})
    receipt = actions.StepReceipt.executed(action, actions.ActionResult(True, "hello"), tmp_path)
    returned = run_state.recorder.record(receipt, 0, 0, wall_s=0.25)
    assert returned is receipt.entry is replacement["last_steps"][0]
    assert replacement["all_steps"][0] == returned
    assert replacement["all_steps"][0] is not returned
    assert run_state.history == [receipt.history_event(0, 0)]


def test_controller_progress_follows_state_replacement_without_rebinding_validation(tmp_path):
    controller = askme._RunController("greet", str(tmp_path))
    original = controller.state
    replacement = {**original, "errors": ["replacement"]}
    controller.state = replacement
    controller.progress.errors.append("still current")
    assert replacement["errors"] == ["replacement", "still current"]
    assert original["errors"] == []
    assert controller.run_state.data is original
    assert controller.recorder.state is original
    assert controller.completion.validation.data is original


def test_exhaustion_retains_empty_errors_after_success_and_duplicate_skips(tmp_path):
    # Scripted characterization, not a replay of the hosted smoke's unretained
    # response content: successful dispatches do not guarantee accepted done.
    write = {"action": "write", "arg": "greeting.txt", "content": "hello\n"}
    client = ScriptedClient([{"tasks": ["write greeting.txt"]}, write, write, write])
    events = []
    result = askme.run_result(
        "Create a greeting file and read it back",
        working_dir=str(tmp_path),
        config=askme.RunConfig(
            max_replans=1,
            max_steps=3,
            max_task_local_replans=0,
            final_validate="0",
            compile_repair=False,
        ),
        dependencies=askme.RunDependencies(
            llm_client=client, log_sink=lambda message: None, event_sink=events.append
        ),
    )
    assert result["status"] == "exhausted"
    assert result["state"]["errors"] == []
    assert result["outcome"]["steps"] == {"selected": 3, "executed": 1, "skipped": 2}
    assert [event["reason"] for event in events if event["event"] == "step_skipped"] == [
        "duplicate_write",
        "duplicate_write",
    ]
    assert not any(event["event"] == "step_control" for event in events)
    assert client.replies == []
    assert {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == {"greeting.txt": b"hello\n"}
