"""Completion decisions depend on a live view and services, not a controller."""

from dataclasses import dataclass, replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import policies


@dataclass(frozen=True)
class Verdict:
    valid: bool
    reason: str = ""
    missing: tuple[str, ...] = ()
    deterministic: bool = False


def _view():
    return policies.CompletionView(
        state={
            "errors": [],
            "all_steps": [],
            "completed_tasks": ["write greeting"],
            "selected_steps": 2,
            "executed_steps": 1,
            "skipped_steps": 0,
        },
        history=[],
        user_prompt="deliver greeting",
        working_dir="synthetic-workspace",
        final_validate="always",
        max_replans=3,
    )


def _context(validator, *, mode="always"):
    current = {"view": replace(_view(), final_validate=mode)}
    events, lines = [], []
    context = policies.CompletionContext(
        current_view=lambda: current["view"],
        validate=validator,
        decide_validation=policies._should_validate,
        elapsed=lambda: 1.25,
        emit=lines.append,
        event=events.append,
    )
    return context, current, events, lines


@pytest.mark.parametrize(
    ("verdict", "mode", "status", "validation", "expected_event"),
    [
        (
            None,
            "always",
            "complete_unverified",
            "unavailable",
            {
                "event": "validation",
                "valid": None,
                "reason": "validator unavailable",
                "deterministic": False,
            },
        ),
        (
            Verdict(True),
            "always",
            "complete",
            "passed",
            {"event": "validation", "valid": True, "deterministic": False},
        ),
        (
            Verdict(True, deterministic=True),
            "always",
            "complete",
            "deterministic",
            {"event": "validation", "valid": True, "deterministic": True},
        ),
        (None, "0", "complete", "skipped", None),
    ],
)
def test_completion_without_controller_preserves_terminal_contract(
    verdict, mode, status, validation, expected_event
):
    validator = Mock(return_value=verdict)
    context, current, events, lines = _context(validator, mode=mode)
    policy = policies.CompletionPolicy.from_context(context)
    assert not hasattr(policy, "controller")
    assert policy.validation.data is current["view"].state
    outcome = policy.try_finish(0)
    assert outcome.describe() == {
        "status": status,
        "validation": validation,
        "replans": 0,
        "wall_s": 1.25,
        "completed_tasks": 1,
        "steps": {"selected": 2, "executed": 1, "skipped": 0},
    }
    assert events == ([] if expected_event is None else [expected_event])
    assert lines[-2:] == [
        "All tasks complete. (1.2s total)",
        "Output in: synthetic-workspace",
    ]
    assert validator.call_count == (0 if mode == "0" else 1)


@pytest.mark.parametrize(
    "evidence", [{"action": "read", "ok": True}, {"action": "write", "ok": False}]
)
def test_rejection_needs_new_successful_mutation_or_shell_evidence(evidence):
    validator = Mock(return_value=Verdict(False, "missing result", ("result.txt",)))
    context, current, events, _ = _context(validator)
    policy = policies.CompletionPolicy.from_context(context)
    state = current["view"].state
    assert policy.try_finish(0) is None
    state["all_steps"].append(evidence)
    assert policy.try_finish(1) is None
    validator.assert_called_once_with()
    assert state["errors"] == [
        "[validation_failed] missing result missing: result.txt",
        "[validation_failed] completion after failed validation requires new write, edit, or shell evidence",
    ]
    assert events[0] == {
        "event": "validation",
        "valid": False,
        "reason": "missing result",
        "missing": ["result.txt"],
        "deterministic": False,
    }
    assert policy.exhausted().describe()["validation"] == "failed"


@pytest.mark.parametrize("second", [None, Verdict(False, "still missing"), Verdict(True)])
def test_recheck_can_clear_rejection_only_with_an_explicit_pass(second):
    validator = Mock(side_effect=[Verdict(False, "missing"), second])
    context, current, events, _ = _context(validator)
    policy = policies.CompletionPolicy.from_context(context)
    state = current["view"].state
    assert policy.try_finish(0) is None
    state["all_steps"].append({"action": "shell", "ok": True})
    outcome = policy.try_finish(1)
    assert validator.call_count == 2
    if second is not None and second.valid:
        assert outcome.status == "complete"
        assert outcome.validation == "passed"
        assert state["validation_recheck_needed"] is False
    else:
        assert outcome is None
        # More successful evidence cannot authorize a third validation call.
        state["all_steps"].append({"action": "edit", "ok": True})
        assert policy.try_finish(2) is None
        assert validator.call_count == 2
        assert policy.exhausted().validation == "failed"
        assert events[-1] == {
            "event": "validation_pending",
            "reason": "validation remains failed after the maximum checks",
        }


def test_exhaustion_uses_live_view_and_never_reconciles_shell_success():
    validator = Mock(side_effect=AssertionError("exhaustion must not validate"))
    context, current, events, lines = _context(validator)
    policy = policies.CompletionPolicy.from_context(context)
    state = current["view"].state
    state["all_steps"].append({"action": "shell", "ok": True})
    state["errors"].extend(f"error {i}" for i in range(7))
    state["selected_steps"] = 8
    current["view"] = replace(current["view"], max_replans=5, working_dir="new-workspace")
    outcome = policy.exhausted()
    assert outcome.run_end_event() == {
        "event": "run_end",
        "status": "exhausted",
        "replans": 5,
        "wall_s": 1.25,
        "errors": [f"error {i}" for i in range(2, 7)],
        "steps": {"selected": 8, "executed": 1, "skipped": 0},
    }
    assert lines[0] == "Exhausted 5 replan attempts. (1.2s total)"
    assert lines[-1] == "Output in: new-workspace"
    assert events == []
    validator.assert_not_called()


def test_callback_state_replacement_preserves_original_validation_binding():
    context, current, events, _ = _context(Mock())
    original = current["view"].state
    replacement = {**original, "errors": [], "selected_steps": 9}

    def reject():
        current["view"] = replace(
            current["view"], state=replacement, working_dir="replacement-workspace"
        )
        return Verdict(False, "missing")

    policy = policies.CompletionPolicy.from_context(replace(context, validate=reject))
    assert policy.try_finish(0) is None
    # Preserve the historical aliasing contract; this cleanup does not redefine
    # what replacing the public compatibility dictionary means.
    assert policy.validation.data is original
    assert original["validation_recheck_needed"] is True
    assert "validation_recheck_needed" not in replacement
    assert original["errors"] == []
    assert replacement["errors"] == ["[validation_failed] missing"]
    assert policy.exhausted().selected_steps == 9
    assert events[0]["valid"] is False


def test_legacy_adapter_reads_changed_request_settings_and_callbacks_at_use_time():
    view = _view()
    stale = Mock(side_effect=AssertionError("stale service"))
    controller = SimpleNamespace(
        **vars(view),
        run_state=SimpleNamespace(elapsed=stale),
        _llm_kwargs=stale,
        _log_sink=stale,
        _budgets={"final_validation_max_tokens": 1},
        _timeouts={"final_validation": 2},
        _retries={"final_validation": 0},
        _emit=stale,
        _event=stale,
    )
    validator = Mock(return_value=None)
    policy = policies.CompletionPolicy(controller, validator=validator)
    events, lines = [], []
    client = object()
    controller.user_prompt = "new prompt"
    controller.working_dir = "new-workspace"
    controller._budgets = {"final_validation_max_tokens": 31}
    controller._timeouts = {"final_validation": 17}
    controller._retries = {"final_validation": 1}
    controller._llm_kwargs = lambda: {"client": client}
    controller._log_sink = lines.append
    controller._emit = lines.append
    controller._event = events.append
    controller.run_state.elapsed = lambda: 2.5
    outcome = policy.try_finish(0)
    validator.assert_called_once_with(
        "new prompt",
        view.state,
        "new-workspace",
        max_tokens=31,
        timeout=17,
        max_retries=1,
        client=client,
        log_sink=lines.append,
    )
    assert outcome.status == "complete_unverified"
    assert outcome.wall_s == 2.5
    assert lines[-1] == "Output in: new-workspace"
    assert events[0]["valid"] is None
    stale.assert_not_called()


@pytest.mark.parametrize("terminal", ["exhausted", "try_finish"])
def test_legacy_terminal_paths_do_not_read_unused_controller_fields(terminal):
    view = _view()
    lines = []
    controller = SimpleNamespace(
        state=view.state,
        working_dir=view.working_dir,
        run_state=SimpleNamespace(elapsed=lambda: 1.25),
        _emit=lines.append,
    )
    if terminal == "exhausted":
        # Exhaustion never needed planner context, validation mode, or services.
        controller.max_replans = 3
    else:
        # A skipped validation never needed max_replans or validation services.
        controller.history = view.history
        controller.user_prompt = view.user_prompt
        controller.final_validate = "0"
    validator = Mock(side_effect=AssertionError("unused validator"))
    policy = policies.CompletionPolicy(controller, validator=validator)
    outcome = policy.exhausted() if terminal == "exhausted" else policy.try_finish(0)
    assert outcome.describe() == {
        "status": "exhausted" if terminal == "exhausted" else "complete",
        "validation": "skipped",
        "replans": 3 if terminal == "exhausted" else 0,
        "wall_s": 1.25,
        "completed_tasks": 1,
        "steps": {"selected": 2, "executed": 1, "skipped": 0},
    }
    assert lines[-1] == "Output in: synthetic-workspace"
    validator.assert_not_called()


@pytest.mark.parametrize("accepted", [False, True])
def test_legacy_callback_mutations_preserve_terminal_read_order(accepted):
    """Characterized against accfce6: later phases observe callback mutations."""
    view = _view()
    calls = []
    controller = SimpleNamespace(
        **vars(view),
        _log_sink=None,
        _budgets={"final_validation_max_tokens": 5},
        _timeouts={"final_validation": 7},
        _retries={"final_validation": 0},
    )

    def decide(replan, history, state, prompt, final_validate=None):
        calls.append(("decision", prompt))
        assert history is view.history
        assert state is view.state
        controller.user_prompt = "changed by decision"
        return True

    def kwargs():
        calls.append(("kwargs",))
        controller.working_dir = "changed by kwargs"
        return {}

    def validate(prompt, state, working_dir, **kwargs):
        calls.append(("validate", prompt, working_dir))
        assert state is view.state
        assert kwargs == {"max_tokens": 5, "timeout": 7, "max_retries": 0}
        controller.state = {
            **view.state,
            "errors": [],
            "completed_tasks": ["one", "two"],
            "selected_steps": 4,
        }
        controller.working_dir = "changed by validator"
        return Verdict(accepted, "missing")

    def elapsed():
        calls.append(("elapsed",))
        controller.state = {**view.state, "errors": [], "selected_steps": 99}
        return 2.5

    def emit(message):
        calls.append(("emit", message))
        if message.startswith(("All tasks", "Exhausted")):
            controller.working_dir = "changed by emit"

    controller._llm_kwargs = kwargs
    controller.run_state = SimpleNamespace(elapsed=elapsed)
    controller._emit = emit
    controller._event = lambda event: calls.append(("event", event))
    policy = policies.CompletionPolicy(controller, validator=validate, decide_validation=decide)
    outcome = policy.try_finish(0)
    if not accepted:
        assert outcome is None
        outcome = policy.exhausted()
    assert outcome.describe() == {
        "status": "complete" if accepted else "exhausted",
        "validation": "passed" if accepted else "failed",
        "replans": 0 if accepted else 3,
        "wall_s": 2.5,
        "completed_tasks": 2,
        # Snapshot state is selected before elapsed() replaces the controller
        # dictionary; changing that ordering would report 99 here.
        "steps": {"selected": 4, "executed": 1, "skipped": 0},
    }
    verdict_event = {"event": "validation", "valid": accepted, "deterministic": False}
    if not accepted:
        verdict_event.update(reason="missing", missing=[])
    assert calls == [
        ("decision", "deliver greeting"),
        ("kwargs",),
        ("validate", "changed by decision", "changed by kwargs"),
        ("emit", "  Validation passed." if accepted else "  Validation failed: missing"),
        ("event", verdict_event),
        ("elapsed",),
        (
            "emit",
            "All tasks complete. (2.5s total)"
            if accepted
            else "Exhausted 3 replan attempts. (2.5s total)",
        ),
        *([] if accepted else [("emit", "Errors: []")]),
        ("emit", "Output in: changed by emit"),
    ]
