"""Legacy step-service ordering and controller-free policy composition."""

from types import SimpleNamespace

import actions
import loop
import policies


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
