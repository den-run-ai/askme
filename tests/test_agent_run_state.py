"""Controller state seams (issue #31): completion blocker, run/task state types."""

import askme
from askme import (
    REWRITE_PRESSURE_WRITES,
    REWRITE_SKIP_WRITES,
    WRITE_PRESSURE_OBSERVATIONS,
    RunState,
    TaskAttemptState,
    _completion_blocker,
)


def _truncated(arg, **extra):
    return {"action": "write", "arg": arg, "ok": True, "_truncated_write": True, **extra}


def _complete(arg, **extra):
    return {"action": "write", "arg": arg, "ok": True, **extra}


class TestCompletionBlocker:
    """One finish-eligibility gate feeds both `done` and post-task acceptance."""

    def test_clean_state_does_not_block(self, tmp_path):
        state = {"all_steps": [], "pending_empty_writes": {}}
        assert _completion_blocker(state, str(tmp_path)) is None

    def test_missing_keys_do_not_block(self, tmp_path):
        assert _completion_blocker({}, str(tmp_path)) is None

    def test_unresolved_truncated_write_blocks_with_append_recovery(self, tmp_path):
        state = {"all_steps": [_truncated("app.py")], "pending_empty_writes": {}}
        blocker = _completion_blocker(state, str(tmp_path))
        assert blocker is not None
        name, recovery_arg, append_allowed = blocker
        assert name == "app.py"
        assert recovery_arg  # actionable target, never empty
        assert append_allowed is True

    def test_resolved_truncated_write_does_not_block(self, tmp_path):
        state = {
            "all_steps": [_truncated("app.py"), _complete("app.py")],
            "pending_empty_writes": {},
        }
        assert _completion_blocker(state, str(tmp_path)) is None

    def test_newest_unresolved_target_wins(self, tmp_path):
        state = {
            "all_steps": [_truncated("first.py"), _truncated("second.py")],
            "pending_empty_writes": {},
        }
        blocker = _completion_blocker(state, str(tmp_path))
        assert blocker is not None
        assert blocker[0] == "second.py"

    def test_restrictive_pending_overwrite_wins_over_truncated_write(self, tmp_path):
        pending = {
            "t/app.py": {
                "name": "app.py",
                "append_allowed": False,
                "recovery_arg": "app.py",
            }
        }
        state = {"all_steps": [_truncated("other.py")], "pending_empty_writes": pending}
        blocker = _completion_blocker(state, str(tmp_path))
        assert blocker == ("app.py", "app.py", False)

    def test_permissive_pending_append_blocks_with_append_recovery(self, tmp_path):
        pending = {
            "t/notes.txt": {
                "name": "notes.txt",
                "append_allowed": True,
                "recovery_arg": "notes.txt",
            }
        }
        state = {"all_steps": [], "pending_empty_writes": pending}
        blocker = _completion_blocker(state, str(tmp_path))
        assert blocker == ("notes.txt", "notes.txt", True)

    def test_selection_matches_replanner_visibility(self, tmp_path):
        """Both completion sites and the replanner must steer one target."""
        pending = {
            "t/app.py": {
                "name": "app.py",
                "append_allowed": False,
                "recovery_arg": "app.py",
            },
            "t/notes.txt": {
                "name": "notes.txt",
                "append_allowed": True,
                "recovery_arg": "notes.txt",
            },
        }
        state = {"all_steps": [_truncated("other.py")], "pending_empty_writes": pending}
        blocker = _completion_blocker(state, str(tmp_path))
        visibility = askme._incomplete_write_visibility(
            state["all_steps"], state["pending_empty_writes"]
        )
        assert blocker is not None and visibility is not None
        assert blocker[0] == visibility["incomplete_write"]
        assert blocker[1] == visibility["incomplete_write_target"]
        assert blocker[2] == visibility["incomplete_write_append_allowed"]


class TestTaskAttemptState:
    """Attempt-scoped executor state and the write-pressure predicate."""

    def test_fresh_attempt_has_no_pressure_or_history(self):
        attempt = TaskAttemptState(task="write foo.py", wants_write=True)
        assert attempt.done is False
        assert attempt.steps == []
        assert attempt.use_think is False
        assert attempt.reasoning_trigger == "executor"
        assert not attempt.write_pressure()

    def test_write_pressure_arms_at_observation_threshold(self):
        attempt = TaskAttemptState(task="write foo.py", wants_write=True)
        attempt.observe_executed = WRITE_PRESSURE_OBSERVATIONS - 1
        assert not attempt.write_pressure()
        attempt.observe_executed = WRITE_PRESSURE_OBSERVATIONS
        assert attempt.write_pressure()

    def test_write_pressure_needs_write_shaped_task(self):
        attempt = TaskAttemptState(task="inspect foo.py", wants_write=False)
        attempt.observe_executed = WRITE_PRESSURE_OBSERVATIONS
        assert not attempt.write_pressure()

    def test_first_commit_relieves_write_pressure(self):
        attempt = TaskAttemptState(task="write foo.py", wants_write=True)
        attempt.observe_executed = WRITE_PRESSURE_OBSERVATIONS
        attempt.commit_executed = 1
        assert not attempt.write_pressure()


class TestRunStateRewriteDamping:
    """Run-scoped rewrite streak: arming, breaking, and pressure targets."""

    def test_streak_advances_only_on_same_target(self):
        rs = RunState("gated", 300)
        rs.note_successful_full_write("t/app.py")
        rs.note_successful_full_write("t/app.py")
        assert rs.consecutive_target_writes == 2
        rs.note_successful_full_write("t/other.py")
        assert rs.last_write_target == "t/other.py"
        assert rs.consecutive_target_writes == 1

    def test_validate_pressure_reports_basename_at_threshold(self):
        rs = RunState("gated", 300)
        for _ in range(REWRITE_PRESSURE_WRITES - 1):
            rs.note_successful_full_write("t/app.py")
        assert rs.validate_pressure_target() is None
        rs.note_successful_full_write("t/app.py")
        assert rs.validate_pressure_target() == "app.py"

    def test_skip_arms_only_for_streak_target(self):
        rs = RunState("gated", 300)
        for _ in range(REWRITE_SKIP_WRITES):
            rs.note_successful_full_write("t/app.py")
        assert rs.rewrite_skip_armed("t/app.py")
        assert not rs.rewrite_skip_armed("t/other.py")

    def test_shell_or_edit_breaks_streak_but_keeps_target(self):
        rs = RunState("gated", 300)
        for _ in range(REWRITE_SKIP_WRITES):
            rs.note_successful_full_write("t/app.py")
        rs.break_rewrite_streak()
        assert rs.last_write_target == "t/app.py"
        assert rs.consecutive_target_writes == 0
        assert not rs.rewrite_skip_armed("t/app.py")
        assert rs.validate_pressure_target() is None

    def test_disarm_forgets_the_target(self):
        rs = RunState("gated", 300)
        rs.note_successful_full_write("t/app.py")
        rs.disarm_rewrite_damping()
        assert rs.last_write_target is None
        assert rs.consecutive_target_writes == 0
        assert not rs.rewrite_skip_armed(None)

    def test_data_dict_is_the_result_contract(self):
        rs = RunState("off", 120)
        assert rs.data["reasoning_policy"] == "off"
        assert rs.data["goal_context_chars"] == 120
        assert rs.data["selected_steps"] == 0
        assert rs.recorder.state is rs.data
        assert rs.recorder.history is rs.history
