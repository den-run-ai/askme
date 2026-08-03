"""Step-policy arms (issues #31/#68): heuristic baseline vs lifecycle.

The heuristic arm's characterization is the whole existing deterministic
suite (it is the default arm). These tests pin the policy seam itself and
the lifecycle arm's inspect → modify → verify → finish invariants against
the recorded failure shapes: the Qwen-style observation stall, the
Gemma-style rewrite loop, truncated writes, verification failure, and clean
completion. The lifecycle arm is an alternative to measure, not an assumed
improvement.
"""

import json
from unittest.mock import patch

import pytest

import askme
from askme import RunConfig, RunDependencies, run_result


class ScriptedClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def ask(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return self.replies.pop(0)


def _deps(client, events=None, lines=None):
    return RunDependencies(
        llm_client=client,
        log_sink=(lines.append if lines is not None else lambda msg: None),
        event_sink=(events.append if events is not None else lambda event: None),
    )


def _skip_reasons(events):
    return [e["reason"] for e in events if e.get("event") == "step_skipped"]


class TestPolicySelection:
    def test_base_policy_defaults_are_permissive(self):
        """The base class is the neutral contract: no pressure, no guards,
        deterministic completion allowed, observation hooks are no-ops."""
        policy = askme.StepPolicy(controller=None)
        assert policy.write_pressure(attempt=None) is False
        assert policy.validate_pressure(attempt=None) is None
        assert policy.guard_done(ctx=None, attempt=None) is None
        assert policy.guard_action(ctx=None, attempt=None) is None
        assert policy.allows_deterministic_completion() is True
        assert policy.note_result(ctx=None, attempt=None, result=None) is None
        assert policy.note_deterministic_repair(target=None) is None
        assert policy.note_deterministic_retry(result=None) is None

    def test_default_arm_is_heuristic(self, tmp_path):
        controller = askme._RunController("greet", str(tmp_path))
        assert controller.step_policy.name == "heuristic"
        assert controller.config_metadata()["step_policy"] == "heuristic"

    def test_lifecycle_arm_is_selectable_and_hash_logged(self, tmp_path):
        controller = askme._RunController(
            "greet", str(tmp_path), config=RunConfig(step_policy="lifecycle")
        )
        assert controller.step_policy.name == "lifecycle"
        metadata = controller.config_metadata()
        assert metadata["step_policy"] == "lifecycle"
        default_hash = askme._RunController("greet", str(tmp_path)).config_metadata()["config_hash"]
        assert metadata["config_hash"] != default_hash

    def test_unknown_arm_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="step_policy"):
            run_result("greet", working_dir=str(tmp_path), config=RunConfig(step_policy="bogus"))

    def test_from_env_pins_the_arm(self):
        assert RunConfig.from_env({"AGENT_STEP_POLICY": "lifecycle"}).step_policy == "lifecycle"
        assert RunConfig.from_env({}).step_policy == "heuristic"

    def test_module_env_guard_normalizes_and_rejects(self):
        """The import-time AGENT_STEP_POLICY guard fails fast on a typo'd
        arm, exactly like the reasoning-policy guard above it."""
        assert askme._validated_step_policy(" Lifecycle ") == "lifecycle"
        with pytest.raises(ValueError, match="AGENT_STEP_POLICY"):
            askme._validated_step_policy("bogus")


class TestObservationStall:
    """Qwen-style stall (2026-08-01 canary): observation never commits."""

    def _observation_script(self, tmp_path):
        (tmp_path / "f1.txt").write_text("alpha\n")
        (tmp_path / "f2.txt").write_text("beta\n")
        return [
            {"tasks": ["implement feature in app.py"]},
            {"action": "read", "arg": "f1.txt"},
            {"action": "search", "arg": "alpha"},
            {"action": "tree", "arg": "."},
            {"action": "read", "arg": "f2.txt"},
            {"action": "search", "arg": "beta"},
            {"action": "tree", "arg": "sub"},
        ]

    @patch("askme.replan_task", return_value=askme.TaskReplanResult(None, "unknown"))
    def test_heuristic_arm_blocks_the_observation_tail(self, mock_replan, tmp_path):
        events = []
        client = ScriptedClient(self._observation_script(tmp_path))
        result = run_result(
            "implement feature in app.py",
            working_dir=str(tmp_path),
            config=RunConfig(max_replans=1, max_steps=6),
            dependencies=_deps(client, events),
        )
        assert result["status"] == "exhausted"
        reasons = _skip_reasons(events)
        assert "observe_tail_reserved" in reasons
        assert "observe_tail_exhausted" in reasons
        assert result["state"]["executed_steps"] == 3

    @patch("askme.replan_task", return_value=askme.TaskReplanResult(None, "unknown"))
    def test_lifecycle_arm_pressures_without_tail_blocking(self, mock_replan, tmp_path):
        """The lifecycle arm replaces tail blocking with prompt pressure:
        the stall still fails, but as honest budget exhaustion with every
        observation executed and recorded."""
        events = []
        client = ScriptedClient(self._observation_script(tmp_path))
        result = run_result(
            "implement feature in app.py",
            working_dir=str(tmp_path),
            config=RunConfig(step_policy="lifecycle", max_replans=1, max_steps=6),
            dependencies=_deps(client, events),
        )
        assert result["status"] == "exhausted"
        assert result["state"]["completed_tasks"] == []
        assert not any(r.startswith("observe_tail") for r in _skip_reasons(events))
        assert result["state"]["executed_steps"] == 6
        # After the observation budget, the executor prompt demands a commit.
        pressured = [
            c
            for c in client.calls
            if c.get("expect") == "action" and "MUST be write" in c["messages"][1]["content"]
        ]
        assert pressured


class TestRewriteDiscipline:
    """Gemma-style rewrite loop (v6 canary): rewrites without verification."""

    def test_lifecycle_steers_unverified_rewrite_to_verification(self, tmp_path):
        events = []
        client = ScriptedClient(
            [
                {"tasks": ["implement feature in big.py"]},
                {"action": "write", "arg": "big.py", "content": "v1\n"},
                {"action": "write", "arg": "big.py", "content": "v2\n"},  # steered
                {"action": "shell", "arg": "true"},
                {"action": "write", "arg": "big.py", "content": "v3\n"},  # verified: allowed
                {"action": "shell", "arg": "cat big.py"},
                {"action": "done"},
            ]
        )
        result = run_result(
            "implement feature in big.py",
            working_dir=str(tmp_path),
            config=RunConfig(step_policy="lifecycle", max_replans=1, max_steps=8),
            dependencies=_deps(client, events),
        )
        assert result["status"] == "complete"
        assert (tmp_path / "big.py").read_text() == "v3\n"
        reasons = _skip_reasons(events)
        assert reasons.count("lifecycle_verify_before_rewrite") == 1
        assert "rewrite_loop" not in reasons
        # The corrective note reached the model on the next executor turn.
        note_turns = [
            c
            for c in client.calls
            if c.get("expect") == "action" and "unverified" in c["messages"][1]["content"]
        ]
        assert note_turns

    def test_lifecycle_allows_rewriting_a_different_target(self, tmp_path):
        client = ScriptedClient(
            [
                {"tasks": ["implement feature in big.py"]},
                {"action": "write", "arg": "big.py", "content": "v1\n"},
                {"action": "write", "arg": "other.py", "content": "helper\n"},
                {"action": "shell", "arg": "true"},
                {"action": "done"},
            ]
        )
        result = run_result(
            "implement feature in big.py",
            working_dir=str(tmp_path),
            config=RunConfig(step_policy="lifecycle", max_replans=1, max_steps=6),
            dependencies=_deps(client),
        )
        assert result["status"] == "complete"
        assert (tmp_path / "other.py").read_text() == "helper\n"


class TestVerificationGate:
    def test_done_is_refused_until_a_successful_check(self, tmp_path):
        events = []
        client = ScriptedClient(
            [
                {"tasks": ["implement feature in app.py"]},
                {"action": "write", "arg": "app.py", "content": "x = 1\n"},
                {"action": "done"},  # refused: unverified
                {"action": "shell", "arg": "true"},
                {"action": "done"},
            ]
        )
        result = run_result(
            "implement feature in app.py",
            working_dir=str(tmp_path),
            config=RunConfig(step_policy="lifecycle", max_replans=1, max_steps=6),
            dependencies=_deps(client, events),
        )
        assert result["status"] == "complete"
        assert _skip_reasons(events).count("lifecycle_unverified_done") == 1
        note_turns = [
            c
            for c in client.calls
            if c.get("expect") == "action" and "never verified" in c["messages"][1]["content"]
        ]
        assert note_turns

    def test_failed_check_does_not_verify(self, tmp_path):
        """A failing shell is not verification: done stays refused until a
        check succeeds (issue #31 invariant)."""
        events = []
        client = ScriptedClient(
            [
                {"tasks": ["implement feature in app.py"]},
                {"action": "write", "arg": "app.py", "content": "x = 1\n"},
                {"action": "shell", "arg": "false"},  # check fails
                {"action": "done"},  # still refused
                {"action": "shell", "arg": "true"},
                {"action": "done"},
            ]
        )
        result = run_result(
            "implement feature in app.py",
            working_dir=str(tmp_path),
            config=RunConfig(step_policy="lifecycle", max_replans=1, max_steps=8),
            dependencies=_deps(client, events),
        )
        assert result["status"] == "complete"
        assert "lifecycle_unverified_done" in _skip_reasons(events)

    def test_successful_edit_requires_verification_too(self, tmp_path):
        """An edit is a mutation like a write: done stays refused until a
        check succeeds after it."""
        events = []
        client = ScriptedClient(
            [
                {"tasks": ["implement feature in app.py"]},
                {"action": "write", "arg": "app.py", "content": "v1\n"},
                {"action": "shell", "arg": "true"},
                {"action": "edit", "arg": "app.py", "find": "v1", "replace": "v2"},
                {"action": "done"},  # refused: the edit is unverified
                {"action": "shell", "arg": "cat app.py"},
                {"action": "done"},
            ]
        )
        result = run_result(
            "implement feature in app.py",
            working_dir=str(tmp_path),
            config=RunConfig(step_policy="lifecycle", max_replans=1, max_steps=8),
            dependencies=_deps(client, events),
        )
        assert result["status"] == "complete"
        assert (tmp_path / "app.py").read_text() == "v2\n"
        assert _skip_reasons(events).count("lifecycle_unverified_done") == 1

    def test_observation_only_task_can_finish_from_inspect(self, tmp_path):
        (tmp_path / "notes.txt").write_text("the answer\n")
        client = ScriptedClient(
            [
                {"tasks": ["read notes.txt"]},
                {"action": "read", "arg": "notes.txt"},
                {"action": "done"},
            ]
        )
        result = run_result(
            "read notes.txt",
            working_dir=str(tmp_path),
            config=RunConfig(step_policy="lifecycle", max_replans=1, max_steps=4),
            dependencies=_deps(client),
        )
        assert result["status"] == "complete"


class TestTruncatedWriteRecovery:
    def test_restarting_a_truncated_write_is_not_steered(self, tmp_path):
        """The incomplete-write obligation owns truncation recovery: the
        lifecycle rewrite discipline must not block the instructed restart."""
        events = []
        client = ScriptedClient(
            [
                {"tasks": ["implement feature in app.py"]},
                {
                    "action": "write",
                    "arg": "app.py",
                    "content": "partial\nmissing tail",
                    "content_truncated": True,
                },
                {"action": "write", "arg": "app.py", "content": "full\nversion\n"},  # restart
                {"action": "shell", "arg": "true"},
                {"action": "done"},
            ]
        )
        result = run_result(
            "implement feature in app.py",
            working_dir=str(tmp_path),
            config=RunConfig(step_policy="lifecycle", max_replans=1, max_steps=6),
            dependencies=_deps(client, events),
        )
        assert result["status"] == "complete"
        assert (tmp_path / "app.py").read_text() == "full\nversion\n"
        assert "lifecycle_verify_before_rewrite" not in _skip_reasons(events)

    def test_pending_empty_obligation_is_not_steered(self, tmp_path):
        """A zero-byte truncation leaves a pending obligation on the target:
        the instructed clean resend must not be steered to verification even
        though an earlier successful write is still unverified."""
        events = []
        client = ScriptedClient(
            [
                {"tasks": ["implement feature in big.py"]},
                {"action": "write", "arg": "big.py", "content": "v1\n"},
                {
                    "action": "write",
                    "arg": "big.py",
                    "content": "no complete line",
                    "content_truncated": True,
                },  # zero-byte truncation: pending obligation, nothing dispatched
                {"action": "write", "arg": "big.py", "content": "v2\n"},  # clean resend
                {"action": "shell", "arg": "true"},
                {"action": "done"},
            ]
        )
        result = run_result(
            "implement feature in big.py",
            working_dir=str(tmp_path),
            config=RunConfig(step_policy="lifecycle", max_replans=1, max_steps=8),
            dependencies=_deps(client, events),
        )
        assert result["status"] == "complete"
        assert (tmp_path / "big.py").read_text() == "v2\n"
        reasons = _skip_reasons(events)
        assert "truncated_write_empty" in reasons
        assert "lifecycle_verify_before_rewrite" not in reasons

    def test_incomplete_write_still_blocks_done_in_lifecycle(self, tmp_path):
        """The shared completion blocker is arm-independent."""
        events = []
        client = ScriptedClient(
            [
                {"tasks": ["implement feature in app.py"]},
                {
                    "action": "write",
                    "arg": "app.py",
                    "content": "partial\nmissing tail",
                    "content_truncated": True,
                },
                {"action": "done"},  # refused by the shared blocker
                {"action": "write", "arg": "app.py", "append": True, "content": "tail\n"},
                {"action": "shell", "arg": "true"},
                {"action": "done"},
            ]
        )
        result = run_result(
            "implement feature in app.py",
            working_dir=str(tmp_path),
            config=RunConfig(step_policy="lifecycle", max_replans=1, max_steps=8),
            dependencies=_deps(client, events),
        )
        assert result["status"] == "complete"
        assert (tmp_path / "app.py").read_text() == "partial\ntail\n"
        assert "incomplete_write_done" in _skip_reasons(events)


class TestCleanCompletion:
    def test_modify_verify_finish_records_no_skips(self, tmp_path):
        events = []
        client = ScriptedClient(
            [
                {"tasks": ["implement feature in app.py"]},
                {"action": "write", "arg": "app.py", "content": "x = 1\n"},
                {"action": "shell", "arg": "true"},
                {"action": "done"},
            ]
        )
        result = run_result(
            "implement feature in app.py",
            working_dir=str(tmp_path),
            config=RunConfig(step_policy="lifecycle", max_replans=1, max_steps=5),
            dependencies=_deps(client, events),
        )
        assert result["status"] == "complete"
        assert _skip_reasons(events) == []
        assert result["state"]["skipped_steps"] == 0
        assert result["outcome"]["steps"] == {"selected": 3, "executed": 2, "skipped": 0}


class TestDeterministicRepairInterplay:
    @patch("askme.execute")
    @patch("askme.ask_llm")
    def test_successful_repair_retry_verifies_the_lifecycle(self, mock_llm, mock_execute, tmp_path):
        """The dispatched repair mutation needs verification; the scaffold's
        successful shell retry provides it, so done is accepted."""
        src = tmp_path / "main.c"
        src.write_text('int main(){ printf("hi"); return 0; }\n')
        mock_llm.side_effect = [
            {"tasks": ["compile main.c"]},
            {"action": "shell", "arg": "cc -o main main.c"},
            {"action": "done"},
        ]
        mock_execute.side_effect = [
            {
                "ok": False,
                "output": "main.c:1:13: error: implicit declaration of function 'printf'",
                "error_type": "compile_error",
            },
            {"ok": True, "output": "Wrote main.c"},
            {"ok": True, "output": "(no output)"},
        ]
        log_path = tmp_path / "run.jsonl"
        with patch.object(askme, "RUN_LOG_PATH", str(log_path)):
            result = run_result(
                "compile main.c",
                working_dir=str(tmp_path),
                config=RunConfig(step_policy="lifecycle", max_replans=1, max_tasks=1, max_steps=3),
            )
        assert result["status"] == "complete"
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert not any(
            e.get("reason") == "lifecycle_unverified_done"
            for e in events
            if e.get("event") == "step_skipped"
        )

    @patch(
        "askme.replan_task",
        return_value=askme.TaskReplanResult("Edit main.c to add '#include <stdio.h>'", None),
    )
    @patch("askme.execute")
    @patch("askme.ask_llm")
    def test_unverified_repair_cannot_auto_complete_a_matching_task(
        self, mock_llm, mock_execute, mock_replan, tmp_path, capsys
    ):
        """With the repair applied but its retry failed, the lifecycle arm
        refuses the deterministic auto-completion of a matching include task
        until a check succeeds."""
        src = tmp_path / "main.c"
        src.write_text('int main(){ printf("hi"); return 0; }\n')
        mock_llm.side_effect = [
            {"tasks": ["compile main.c"]},
            {"action": "shell", "arg": "cc -o main main.c"},
            {"action": "fail", "reasoning": "still failing"},
            # Replacement include-task attempt: verify, then finish.
            {"action": "shell", "arg": "true"},
            {"action": "done"},
        ]
        mock_execute.side_effect = [
            {
                "ok": False,
                "output": "main.c:1:13: error: implicit declaration of function 'printf'",
                "error_type": "compile_error",
            },
            {"ok": True, "output": "Wrote main.c"},  # dispatched repair write
            {
                "ok": False,
                "output": "main.c:9: error: unrelated breakage",
                "error_type": "compile_error",
            },  # deterministic retry fails: repair stays unverified
            {"ok": True, "output": "(no output)"},  # the model's own check
        ]
        result = run_result(
            "compile main.c",
            working_dir=str(tmp_path),
            config=RunConfig(step_policy="lifecycle", max_replans=1, max_tasks=1, max_steps=4),
        )
        out = capsys.readouterr().out
        assert result["status"] == "complete"
        assert "auto-done (deterministic repair" not in out
