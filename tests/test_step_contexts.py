"""Legacy step-service ordering and controller-free policy composition."""

from types import SimpleNamespace
from typing import TypedDict

import pytest

import actions
import loop
import policies
from state import RunProgress, RunState


def _step(action, *, truncated=False):
    envelope = actions.parse_action_envelope(action)
    return loop._StepContext(
        0,
        0,
        0.0,
        envelope,
        envelope["action"],
        transport=actions.ActionTransport(content_truncated=truncated),
    )


def test_legacy_policies_allow_none_and_minimal_controllers_without_eager_reads():
    assert policies.StepPolicy(None).guard_action(None, None) is None
    lifecycle = policies.LifecycleStepPolicy(None)
    assert lifecycle.guard_done(None, None) is None
    assert lifecycle.allows_deterministic_completion() is True
    minimal = SimpleNamespace(state={"last_steps": []})
    assert (
        policies.StepPolicy(minimal).guard_duplicate(_step({"action": "read", "arg": "a"}), None)
        is None
    )
    obligations = policies.WriteObligations(SimpleNamespace(state={"pending_empty_writes": {}}))
    assert obligations.prepare(_step({"action": "shell", "arg": "check"})) is None


def test_legacy_timeout_default_is_eager_and_emit_is_resolved_after_callbacks():
    calls = []
    previous = {
        "action": "shell",
        "arg": "check",
        "ok": False,
        "error_type": "timeout",
        "_timeout": 8,
    }
    controller = SimpleNamespace(state={"last_steps": [previous]})

    def timeout(command):
        calls.append(("timeout", command))
        return 999

    def bounds():
        calls.append(("bounds",))
        controller._emit = lambda message: calls.append(("emit", message))
        return 20, 30

    policy = policies.StepPolicy(controller, shell_timeout=timeout, timeout_bounds=bounds)
    ctx = _step({"action": "shell", "arg": "check"})
    attempt = loop.TaskAttemptState("check", False, steps=[previous])
    assert policy.guard_duplicate(ctx, attempt) is None
    assert dict(ctx.action) == {"action": "shell", "arg": "check", "timeout": 20}
    assert calls == [
        ("timeout", "check"),
        ("bounds",),
        ("emit", "  [1] retrying after timeout (20s)"),
    ]


def test_legacy_duplicate_reads_replaced_recorder_between_emit_skip_and_note(tmp_path):
    calls = []
    previous = {"action": "write", "arg": "a.txt", "ok": True, "_content": "hi"}
    original = {"last_steps": [previous], "errors": []}
    replacement = {"last_steps": [], "errors": []}
    controller = SimpleNamespace(state=original, working_dir=str(tmp_path))

    def skip(*args):
        calls.append(("skip", args[-1]))
        controller.state = replacement
        controller.recorder = SimpleNamespace(
            note=lambda entry: replacement["last_steps"].append(entry)
        )

    def emit(message):
        calls.append(("emit", message))
        controller.recorder = SimpleNamespace(skip=skip)

    controller._emit = emit
    ctx = _step({"action": "write", "arg": "a.txt", "content": "hi"})
    attempt = loop.TaskAttemptState("write greeting", True)
    assert (
        policies.StepPolicy(controller).guard_duplicate(ctx, attempt)
        is policies._StepFlow.NEXT_STEP
    )
    assert calls == [
        ("emit", "  [1] skip (duplicate write, same content)"),
        ("skip", "duplicate_write"),
    ]
    assert original["last_steps"] == [previous]
    assert replacement["last_steps"] == [
        {
            "action": "write",
            "arg": "a.txt",
            "ok": True,
            "output": "Already done — file unchanged. Move to next action or emit done.",
            "_content": "hi",
        }
    ]
    assert attempt.dup_skip_count == 1


def test_legacy_empty_write_records_skip_before_live_disarm_and_obligation_update(tmp_path):
    calls, notes = [], []
    original = {"pending_empty_writes": {}}
    replacement = {"pending_empty_writes": {}}
    controller = SimpleNamespace(
        state=original, working_dir=str(tmp_path), _emit=lambda message: calls.append("emit")
    )

    def disarm():
        calls.append("disarm")
        controller.recorder = SimpleNamespace(note=notes.append)

    def skip(*args):
        calls.append(args[-1])
        controller.state = replacement
        controller.run_state = SimpleNamespace(disarm_rewrite_damping=disarm)

    controller.recorder = SimpleNamespace(skip=skip)
    ctx = _step({"action": "write", "arg": "a.txt", "content": "partial"}, truncated=True)
    assert policies.WriteObligations(controller).prepare(ctx) is policies._StepFlow.NEXT_STEP
    target = str(tmp_path / "a.txt")
    assert original == {"pending_empty_writes": {}}
    assert replacement == {
        "pending_empty_writes": {
            target: {
                "name": "a.txt",
                "append_allowed": False,
                "append_targets": [target],
                "recovery_arg": target,
            }
        }
    }
    assert calls == ["emit", "truncated_write_empty", "disarm"]
    assert len(notes) == 1
    assert notes[0]["output"] == (
        f"Write truncated before a complete line. Resend the write (no append) to the exact target {target} "
        "with a shorter first chunk, then continue with append:true chunks."
    )
    assert list(tmp_path.iterdir()) == []


def test_legacy_rewrite_guard_reloads_owner_for_note_after_emit(tmp_path):
    notes = []
    controller = SimpleNamespace(
        working_dir=str(tmp_path),
        run_state=SimpleNamespace(
            rewrite_skip_armed=lambda target: True, consecutive_target_writes=3
        ),
        recorder=SimpleNamespace(skip=lambda *args: None, note=notes.append),
    )

    def emit(message):
        assert "already written 3x" in message
        controller.run_state = SimpleNamespace(consecutive_target_writes=9)

    controller._emit = emit
    ctx = _step({"action": "write", "arg": "a.txt", "content": "hi"})
    ctx.logical_write_target = str(tmp_path / "a.txt")
    attempt = loop.TaskAttemptState("write greeting", True)
    policy = policies.HeuristicStepPolicy(controller)
    assert policy.guard_action(ctx, attempt) is policies._StepFlow.NEXT_STEP
    assert notes[0]["output"].startswith("Already written 9 times.")


class _Current(TypedDict):
    owner: RunState
    workspace: str


def _contexts(tmp_path):
    events, lines = [], []
    owner = RunState("off", 100, clock=lambda: 1, event_sink=events.append)
    owner.data["last_steps"] = []
    current: _Current = {"owner": owner, "workspace": str(tmp_path)}
    services = {
        "progress": lambda: RunProgress(current["owner"].data),
        "working_dir": lambda: current["workspace"],
        "recorder": lambda: current["owner"].recorder,
        "emit": lines.append,
    }
    step = policies.StepPolicyContext(
        **services,
        max_steps=lambda: 3,
        observe_tail_reserve=lambda: 1,
        rewrite=lambda: current["owner"],
        shell_timeout=lambda command: 10,
        timeout_bounds=lambda: (20, 30),
    )
    writes = policies.WriteObligationContext(
        **services, disarm_rewrite=lambda: current["owner"].disarm_rewrite_damping()
    )
    return owner, current, step, writes, events, lines


@pytest.mark.parametrize(
    "kind", [policies.StepPolicy, policies.HeuristicStepPolicy, policies.LifecycleStepPolicy]
)
def test_context_only_step_arms_share_duplicate_recording_and_live_progress(tmp_path, kind):
    owner, current, context, _, events, _ = _contexts(tmp_path)
    policy = kind.from_context(context)
    assert isinstance(policy, kind)
    assert not hasattr(policy, "controller")
    original = owner.data
    replacement = {**original, "last_steps": [], "errors": []}
    owner.data = replacement
    # The recorder owns its own reference; an explicit replacement preserves
    # the established shared-dictionary composition, without cached snapshots.
    owner.recorder.state = replacement
    previous = {"action": "write", "arg": "a.txt", "ok": True, "_content": "hi"}
    replacement["last_steps"].append(previous)
    ctx = _step({"action": "write", "arg": "a.txt", "content": "hi"})
    owner.recorder.selected()
    attempt = loop.TaskAttemptState("write greeting", True)
    assert policy.guard_duplicate(ctx, attempt) is policies._StepFlow.NEXT_STEP
    assert context.progress().data is current["owner"].data is owner.recorder.state
    assert original["skipped_steps"] == 0
    assert replacement["skipped_steps"] == 1
    assert replacement["executed_steps"] == 0
    assert replacement["all_steps"] == []  # corrective note is not an execution
    assert replacement["last_steps"][-1]["output"].startswith("Already done")
    assert events[-1]["reason"] == "duplicate_write"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", [policies.HeuristicStepPolicy, policies.LifecycleStepPolicy])
def test_context_only_arms_preserve_distinct_observation_tail_policy(tmp_path, kind):
    owner, _, context, _, events, _ = _contexts(tmp_path)
    policy = kind.from_context(context)
    ctx = _step({"action": "read", "arg": "a.txt"})
    ctx.step = 2
    attempt = loop.TaskAttemptState("write greeting", True)
    if kind is policies.HeuristicStepPolicy:
        assert policy.guard_action(ctx, attempt) is policies._StepFlow.NEXT_STEP
        assert policy.guard_action(ctx, attempt) is policies._StepFlow.END_ATTEMPT
        assert [event["reason"] for event in events] == [
            "observe_tail_reserved",
            "observe_tail_exhausted",
        ]
        assert len(owner.progress.errors) == 1
    else:
        assert policy.guard_action(ctx, attempt) is None
        assert events == []
        assert owner.progress.errors == []


def test_context_only_lifecycle_initializes_and_requires_successful_verification(tmp_path):
    owner, _, context, _, events, _ = _contexts(tmp_path)
    policy = policies.LifecycleStepPolicy.from_context(context)
    assert policy.needs_verification is False
    write = _step({"action": "write", "arg": "a.txt", "content": "hi"})
    write.logical_write_target = str(tmp_path / "a.txt")
    attempt = loop.TaskAttemptState("write greeting", True)
    policy.note_result(write, attempt, actions.ActionResult(True, "written"))
    done = _step({"action": "done"})
    assert policy.guard_done(done, attempt) is policies._StepFlow.NEXT_STEP
    assert policy.allows_deterministic_completion() is False
    check = _step({"action": "shell", "arg": "check"})
    policy.note_result(check, attempt, actions.ActionResult(False, "failed"))
    assert policy.needs_verification is True
    policy.note_result(check, attempt, actions.ActionResult(True, "passed"))
    assert policy.guard_done(done, attempt) is None
    assert policy.allows_deterministic_completion() is True
    assert owner.progress.skipped_steps == 1
    assert events[0]["reason"] == "lifecycle_unverified_done"


def _dispatch_and_record(owner, obligations, ctx, workspace):
    owner.recorder.executed()
    result = actions.ActionExecutor(workspace).dispatch(ctx.action)
    assert result.ok is True
    if ctx.truncated_write:
        obligations.append_resume_anchor(ctx, ctx.action, result)
    else:
        obligations.note_successful_write(ctx, ctx.action)
    receipt = actions.StepReceipt.executed(ctx.action, result, workspace, ctx.truncated_write)
    owner.recorder.record(receipt, ctx.task_index, ctx.step)
    return result


def test_context_only_obligations_preserve_complete_bytes_and_completion_blocker(tmp_path):
    owner, _, _, context, events, _ = _contexts(tmp_path)
    obligations = policies.WriteObligations.from_context(context)
    assert not hasattr(obligations, "controller")
    partial = _step({"action": "write", "arg": "a.txt", "content": "α\nβ\nunfin"}, truncated=True)
    owner.recorder.selected()
    assert obligations.prepare(partial) is None
    assert partial.action["content"] == "α\nβ\n"
    result = _dispatch_and_record(owner, obligations, partial, tmp_path)
    assert "truncated after 2 lines" in result.output
    assert "last written line: 'β'" in result.output
    owner.recorder.selected()
    assert obligations.refuse_done(_step({"action": "done"})) is policies._StepFlow.NEXT_STEP
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == {"a.txt": "α\nβ\n".encode()}
    complete = _step({"action": "write", "arg": "a.txt", "content": "finished\n", "append": True})
    owner.recorder.selected()
    assert obligations.prepare(complete) is None
    _dispatch_and_record(owner, obligations, complete, tmp_path)
    assert obligations.completion_blocker() is None
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == {
        "a.txt": "α\nβ\nfinished\n".encode()
    }
    assert (
        owner.progress.selected_steps,
        owner.progress.executed_steps,
        owner.progress.skipped_steps,
    ) == (3, 2, 1)
    assert [event["event"] for event in events] == ["step", "step_skipped", "step"]


def test_context_only_empty_append_keeps_old_symlink_referent_obligation(tmp_path):
    (tmp_path / "old.txt").write_text("old\n")
    (tmp_path / "new.txt").write_text("new\n")
    link = tmp_path / "link.txt"
    link.symlink_to("old.txt")
    owner, _, _, context, _, _ = _contexts(tmp_path)
    obligations = policies.WriteObligations.from_context(context)
    empty = _step(
        {"action": "write", "arg": "link.txt", "content": "partial", "append": True}, truncated=True
    )
    owner.recorder.selected()
    assert obligations.prepare(empty) is policies._StepFlow.NEXT_STEP
    original_record = owner.progress.pending_empty_writes[str(tmp_path / "old.txt")]
    assert original_record["append_allowed"] is True
    link.unlink()
    link.symlink_to("new.txt")
    later = _step({"action": "write", "arg": "link.txt", "content": "added\n", "append": True})
    owner.recorder.selected()
    assert obligations.prepare(later) is None
    _dispatch_and_record(owner, obligations, later, tmp_path)
    assert owner.progress.pending_empty_writes[str(tmp_path / "old.txt")] is original_record
    assert obligations.completion_blocker() == ("link.txt", str(tmp_path / "old.txt"), True)
    assert (tmp_path / "old.txt").read_bytes() == b"old\n"
    assert (tmp_path / "new.txt").read_bytes() == b"new\nadded\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["link.txt", "new.txt", "old.txt"]


@pytest.mark.parametrize("role", ["duplicate", "empty_write"])
def test_legacy_method_keeps_owner_when_callback_rebinds_policy_controller(tmp_path, role):
    calls = []
    previous = {"action": "write", "arg": "a.txt", "ok": True, "_content": "hi"}

    def controller(label):
        return SimpleNamespace(
            state={"last_steps": [previous], "errors": [], "pending_empty_writes": {}},
            working_dir=str(tmp_path),
            recorder=SimpleNamespace(
                skip=lambda *args: calls.append((label, "skip")),
                note=lambda entry: calls.append((label, "note")),
            ),
            run_state=SimpleNamespace(
                disarm_rewrite_damping=lambda: calls.append((label, "disarm"))
            ),
            _emit=lambda message: calls.append((label, "emit")),
        )

    original, replacement = controller("original"), controller("replacement")
    policy = (
        policies.StepPolicy(original)
        if role == "duplicate"
        else policies.WriteObligations(original)
    )

    def emit(message):
        calls.append(("original", "emit"))
        policy.controller = replacement

    original._emit = emit

    def invoke():
        ctx = _step(
            {"action": "write", "arg": "a.txt", "content": "hi"},
            truncated=role == "empty_write",
        )
        if role == "duplicate":
            return policy.guard_duplicate(ctx, loop.TaskAttemptState("write greeting", True))
        return policy.prepare(ctx)

    assert invoke() is policies._StepFlow.NEXT_STEP
    expected = (
        ["emit", "skip", "note"] if role == "duplicate" else ["emit", "skip", "disarm", "note"]
    )
    assert calls == [("original", event) for event in expected]
    if role == "empty_write":
        assert str(tmp_path / "a.txt") in original.state["pending_empty_writes"]
        assert replacement.state["pending_empty_writes"] == {}
    # The next invocation must capture the replacement, not permanently pin
    # the constructor's controller or alter the canonical context's ownership.
    calls.clear()
    assert invoke() is policies._StepFlow.NEXT_STEP
    assert calls == [("replacement", event) for event in expected]
