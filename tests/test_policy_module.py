"""Policy-module ownership and explicit completion-validation boundary."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from _test_support import ScriptedClient

import actions
import askme
import policies


def test_policy_import_does_not_load_facade_client_or_environment(tmp_path):
    for module in (policies, actions):
        shutil.copy(Path(module.__file__), tmp_path)
    (tmp_path / ".env").write_text("ASKME_IMPORT_SENTINEL=loaded\n")
    env = {"PATH": os.defpath, "AGENT_FINAL_VALIDATE": "always"}
    for name in ("SYSTEMROOT", "WINDIR"):
        if name in os.environ:
            env[name] = os.environ[name]
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, sys, requests\n"
            "def blocked(*args, **kwargs):\n"
            "    raise AssertionError('Unexpected HTTP during policy import')\n"
            "requests.sessions.Session.send = blocked\n"
            "import policies\n"
            "assert 'askme' not in sys.modules\n"
            "assert 'llm' not in sys.modules\n"
            "assert 'ASKME_IMPORT_SENTINEL' not in os.environ\n"
            "assert policies._should_validate(0, [], {}, 'greet') is False\n"
            "print('independent policy import')\n",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert process.stdout == "independent policy import\n"
    assert process.stderr == ""


@pytest.mark.parametrize(
    "name",
    [
        "WriteObligations",
        "ValidationState",
        "RunOutcome",
        "_StepFlow",
        "_VALIDATE_KEYWORDS",
        "_unresolved_incomplete_writes",
        "_incomplete_step_hint",
        "_pending_empty_hint",
        "_restrictive_pending_empty",
        "_next_pending_empty",
        "_incomplete_write_visibility",
        "_completion_blocker",
        "_pending_append_targets",
        "_pending_empty_recovery",
        "_clear_pending_empty_writes",
        "_write_visibility_flag",
        "_read_continuation_hint",
        "_has_new_validation_evidence",
    ],
)
def test_facade_reexports_one_canonical_policy_algorithm_or_record(name):
    assert getattr(askme, name) is getattr(policies, name)


@pytest.mark.parametrize("name", ["StepPolicy", "HeuristicStepPolicy", "LifecycleStepPolicy"])
def test_policy_adapters_preserve_subtyping_and_canonical_algorithms(name):
    facade = getattr(askme, name)
    canonical = getattr(policies, name)
    policy = facade(None)
    assert issubclass(facade, askme.StepPolicy)
    assert isinstance(policy, canonical)
    assert facade.guard_duplicate is canonical.guard_duplicate
    assert facade.guard_action is canonical.guard_action
    assert facade.note_result is canonical.note_result
    if name == "LifecycleStepPolicy":
        assert policy.needs_verification is False
        assert policy.unverified_target is None
    if name != "StepPolicy":
        assert askme._STEP_POLICY_ARMS[policy.name] is facade
        assert policies._STEP_POLICY_ARMS[policy.name] is canonical


def test_validation_mode_facade_resolves_only_its_compatibility_default(monkeypatch):
    monkeypatch.setattr(askme, "FINAL_VALIDATE", "always")
    assert askme._should_validate(0, [], {}, "greet") is True
    assert askme._should_validate(0, [], {}, "greet", final_validate="0") is False
    assert policies._should_validate(0, [], {}, "greet") is False
    assert policies._should_validate(0, [], {}, "greet", final_validate="always") is True


def test_completion_facade_retains_late_validator_lookup(tmp_path, monkeypatch):
    early = Mock(side_effect=AssertionError("stale validator callback"))
    monkeypatch.setattr(askme, "_validate_completion", early)
    controller = askme._RunController(
        "greet", str(tmp_path), config=askme.RunConfig(final_validate="always")
    )
    late = Mock(return_value=askme.ValidationResponse(valid=True))
    monkeypatch.setattr(askme, "_validate_completion", late)
    outcome = controller.completion.try_finish(0)
    assert isinstance(controller.completion, policies.CompletionPolicy)
    assert outcome.status == "complete"
    assert outcome.validation == "passed"
    early.assert_not_called()
    late.assert_called_once()


@pytest.mark.parametrize("name", ["StepPolicy", "HeuristicStepPolicy", "LifecycleStepPolicy"])
def test_policy_facade_retains_late_shell_timeout_defaults(tmp_path, monkeypatch, name):
    controller = askme._RunController("check", str(tmp_path))
    policy = getattr(askme, name)(controller)
    previous = {"action": "shell", "arg": "check", "ok": False, "error_type": "timeout"}
    controller.state["last_steps"] = [previous]
    attempt = askme.TaskAttemptState("check", wants_write=False, steps=[previous])
    ctx = askme._StepContext(
        0, 0, 0.0, askme.parse_action_envelope({"action": "shell", "arg": "check"}), "shell"
    )
    # Historically read at the guard, not when the policy was constructed.
    monkeypatch.setattr(askme, "SHELL_TIMEOUT_LONG", 157)
    monkeypatch.setattr(askme, "SHELL_TIMEOUT_MAX", 171)
    monkeypatch.setattr(askme, "_get_shell_timeout", lambda *args: 100)
    assert policy.guard_duplicate(ctx, attempt) is None
    assert dict(ctx.action) == {"action": "shell", "arg": "check", "timeout": 171}


@pytest.mark.parametrize("arm", ["heuristic", "lifecycle"])
def test_run_dispatches_retry_with_late_compatibility_timeout_bounds(tmp_path, monkeypatch, arm):
    dispatched, events = [], []

    class Executor:
        working_dir = str(tmp_path)

        def dispatch(self, action):
            dispatched.append(dict(action))
            if len(dispatched) == 1:
                return actions.ActionResult(False, "synthetic timeout", error_type="timeout")
            return actions.ActionResult(True, "synthetic check passed")

    client = ScriptedClient(
        [
            {"tasks": ["check"]},
            {"action": "shell", "arg": "check"},
            {"action": "shell", "arg": "check"},
            {"action": "done"},
        ]
    )
    controller = askme._RunController(
        "check",
        str(tmp_path),
        config=askme.RunConfig(
            step_policy=arm, max_replans=1, max_steps=3, final_validate="0", compile_repair=False
        ),
        dependencies=askme.RunDependencies(
            llm_client=client,
            action_executor=Executor(),
            log_sink=lambda message: None,
            event_sink=events.append,
        ),
    )
    monkeypatch.setattr(askme, "SHELL_TIMEOUT_LONG", 157)
    monkeypatch.setattr(askme, "SHELL_TIMEOUT_MAX", 171)
    monkeypatch.setattr(askme, "_get_shell_timeout", lambda *args: 100)
    result = controller.run()
    assert result["status"] == "complete"
    expected = {
        "action": "shell",
        "arg": "check",
        "content": "",
        "reasoning": "",
        "find": "",
        "replace": "",
    }
    assert dispatched == [expected, {**expected, "timeout": 171}]
    executed = [event for event in events if event.get("event") == "step"]
    assert len(executed) == 2
    assert executed[-1]["action"] == "shell"
    assert executed[-1]["ok"] is True
    assert result["state"]["all_steps"][-1]["_timeout"] == 171


def test_completion_facade_resolves_none_validation_mode_for_direct_controllers(monkeypatch):
    controller, _, _ = _controller()
    controller.final_validate = None
    monkeypatch.setattr(askme, "FINAL_VALIDATE", "always")
    validator = Mock(return_value=None)
    monkeypatch.setattr(askme, "_validate_completion", validator)
    outcome = askme.CompletionPolicy(controller).try_finish(0)
    assert outcome.status == "complete_unverified"
    assert outcome.validation == "unavailable"
    validator.assert_called_once()


def _controller():
    events, lines = [], []
    client = object()
    controller = SimpleNamespace(
        state={
            "errors": [],
            "all_steps": [],
            "completed_tasks": ["write result.txt"],
            "selected_steps": 2,
            "executed_steps": 1,
            "skipped_steps": 0,
        },
        history=[],
        user_prompt="deliver greeting",
        working_dir="synthetic-workspace",
        final_validate="always",
        max_replans=3,
        run_state=SimpleNamespace(elapsed=lambda: 1.25),
        _llm_kwargs=lambda: {"client": client},
        _log_sink=lines.append,
        _budgets={"final_validation_max_tokens": 17},
        _timeouts={"final_validation": 11},
        _retries={"final_validation": 0},
        _emit=lines.append,
        _event=events.append,
    )
    return controller, events, client


def test_direct_completion_uses_explicit_validator_and_preserves_unavailable_outcome(monkeypatch):
    monkeypatch.setattr(askme, "_validate_completion", Mock(side_effect=AssertionError("facade")))
    controller, events, client = _controller()
    validator = Mock(return_value=None)
    outcome = policies.CompletionPolicy(controller, validator=validator).try_finish(0)
    validator.assert_called_once_with(
        "deliver greeting",
        controller.state,
        "synthetic-workspace",
        max_tokens=17,
        timeout=11,
        max_retries=0,
        client=client,
        log_sink=controller._log_sink,
    )
    assert outcome.describe() == {
        "status": "complete_unverified",
        "validation": "unavailable",
        "replans": 0,
        "wall_s": 1.25,
        "completed_tasks": 1,
        "steps": {"selected": 2, "executed": 1, "skipped": 0},
    }
    assert events == [
        {
            "event": "validation",
            "valid": None,
            "reason": "validator unavailable",
            "deterministic": False,
        }
    ]


def test_direct_completion_does_not_erase_rejection_with_unavailable_recheck():
    controller, events, _ = _controller()
    validator = Mock(side_effect=[askme.ValidationResponse(valid=False, reason="missing"), None])
    policy = policies.CompletionPolicy(controller, validator=validator)
    assert policy.try_finish(0) is None
    assert policy.try_finish(1) is None
    assert validator.call_count == 1  # No recheck without new mutation/shell evidence.
    controller.state["all_steps"].append({"action": "shell", "ok": True})
    assert policy.try_finish(1) is None
    assert validator.call_count == 2
    outcome = policy.exhausted()
    assert outcome.status == "exhausted"
    assert outcome.validation == "failed"
    assert controller.state["validation_recheck_needed"] is True
    assert events[-1] == {
        "event": "validation_pending",
        "reason": "validation remains failed after the maximum checks",
    }


def test_direct_completion_requires_explicit_validator():
    controller, _, _ = _controller()
    with pytest.raises(TypeError, match="validator"):
        policies.CompletionPolicy(controller)
