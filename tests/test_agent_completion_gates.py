"""Completion gates that outlive the removed false-success rules (issue #68).

The deleted auto-completion tests incidentally covered two load-bearing
gates; these regressions pin them directly: the deterministic check's
failed-last-command rejection, and the post-task acceptance arm of the
shared completion blocker — the second gate that catches a task the
attempt loop itself never gated (deterministic repair auto-completion).
"""

from unittest.mock import patch

import askme
from actions import ActionExecutor
from askme import _run_loop


class TestDeterministicCheckRejectsFailedCommands:
    def test_failed_last_shell_is_never_a_confident_pass(self, tmp_path):
        state = {
            "all_steps": [
                {"action": "shell", "arg": "python3 app.py", "ok": False, "output": "boom"}
            ]
        }
        assert askme._deterministic_check("run the program", state, str(tmp_path)) is False

    def test_failed_shell_before_a_passing_one_does_not_reject(self, tmp_path):
        state = {
            "all_steps": [
                {"action": "shell", "arg": "python3 app.py", "ok": False, "output": "boom"},
                {"action": "shell", "arg": "python3 app.py", "ok": True, "output": "ok"},
            ]
        }
        assert askme._deterministic_check("run the program", state, str(tmp_path)) is True


class TestPostTaskCompletionBlocker:
    @patch("askme.replan_task", return_value=askme.TaskReplanResult(None, "unknown"))
    @patch("askme.ask_llm")
    def test_repair_auto_completion_cannot_bypass_an_open_obligation(
        self, mock_llm, mock_replan, tmp_path, capsys
    ):
        """Deterministic-repair auto-completion sets task_done without the
        attempt loop's done gate; the post-task acceptance arm of the shared
        completion blocker must still refuse while another target carries an
        unresolved truncated write."""
        src = tmp_path / "main.c"
        src.write_text('int main(){ printf("hi"); return 0; }\n')
        mock_llm.side_effect = [
            {"tasks": ["compile main.c", "create notes.txt"]},
            # Task 1: compile fails, deterministic repair + retry succeed,
            # then the model finishes the task cleanly.
            {"action": "shell", "arg": "cc -o main main.c"},
            {"action": "done"},
            # Task 2: the write is truncated (unresolved obligation) and the
            # done claim is refused; the step budget then exhausts.
            {
                "action": "write",
                "arg": "notes.txt",
                "content": "partial\nmissing tail",
                "content_truncated": True,
            },
            {"action": "done"},
            # Full replan proposes exactly the include task the repair
            # already satisfied: it auto-completes without the done gate.
            {"tasks": ["Edit main.c to add '#include <stdio.h>'"]},
        ]

        def scripted_execute(action, working_dir="."):
            # The compiler passes exactly once the include is on disk; every
            # other action runs through the real executor so the scenario
            # stays valid whether the repair mutates directly or proposes a
            # dispatched write.
            if action.get("action") == "shell":
                if "#include <stdio.h>" in src.read_text():
                    return {"ok": True, "output": "(no output)"}
                return {
                    "ok": False,
                    "output": "main.c:1:13: error: implicit declaration of function 'printf'",
                    "error_type": "compile_error",
                }
            return ActionExecutor(working_dir).dispatch(action).to_dict()

        with patch("askme.execute", side_effect=scripted_execute):
            result = _run_loop(
                "compile main.c and take notes",
                str(tmp_path),
                max_replans=2,
                max_tasks=2,
                max_steps=2,
            )
        out = capsys.readouterr().out
        assert "auto-done (deterministic repair already satisfied task" in out
        assert "Task completion refused" in out
        assert result["status"] == "exhausted"
        assert any(
            e.startswith("[incomplete_write]") and "completion refused" in e
            for e in result["state"]["errors"]
        )
        # The auto-completed claim never entered completed_tasks.
        assert result["state"]["completed_tasks"] == ["compile main.c"]
