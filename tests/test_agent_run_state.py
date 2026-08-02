"""Controller state seams (issue #31): completion blocker, run/task state types."""

import askme
from askme import _completion_blocker


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
