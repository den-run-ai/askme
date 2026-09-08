"""Fresh verification and observable completion claims (issue #95)."""

import pytest

from askme import RunConfig, RunDependencies, run_result


class ScriptedClient:
    def __init__(self, replies):
        self.replies = iter(replies)

    def ask(self, messages, **kwargs):
        return next(self.replies)


def _run(tmp_path, replies, **settings):
    events = []
    config = {
        "max_replans": 1,
        "max_tasks": 2,
        "max_steps": 3,
        "max_task_local_replans": 0,
        "final_validate": "0",
        **settings,
    }
    result = run_result(
        "verify the project",
        working_dir=str(tmp_path),
        config=RunConfig(**config),
        dependencies=RunDependencies(
            llm_client=ScriptedClient(replies),
            event_sink=events.append,
            log_sink=lambda message: None,
        ),
    )
    return result, events


@pytest.mark.parametrize("policy", ["heuristic", "lifecycle"])
@pytest.mark.parametrize("boundary", ["task", "local_replan", "full_replan"])
def test_new_attempt_can_execute_previous_successful_check(tmp_path, policy, boundary):
    """Carried context must not make a new verification attempt unsatisfiable.

    The model receives one duplicate correction in the new attempt, then
    claims done. Before the fix, both selections are suppressed and the
    second ends the attempt before the explicit claim can be selected.
    """
    shell = {"action": "shell", "arg": "printf x >> checks.txt"}
    replies = [{"tasks": ["initial check", "verify once more"]}, shell, {"action": "done"}]
    settings = {"step_policy": policy}
    if boundary == "local_replan":
        replies.extend(
            [
                {"action": "fail", "reasoning": "need a different approach"},
                {"task": "execute the project verification command and inspect its output"},
            ]
        )
        settings["max_task_local_replans"] = 1
    elif boundary == "full_replan":
        replies.extend(
            [
                {"action": "fail", "reasoning": "need another plan"},
                {"tasks": ["verify the project again"]},
            ]
        )
        settings["max_replans"] = 2
    replies.extend([shell, shell, {"action": "done"}])

    result, events = _run(tmp_path, replies, **settings)

    assert result["status"] == "complete"
    assert {p.name: p.read_text() for p in tmp_path.iterdir()} == {"checks.txt": "xx"}
    assert result["state"]["executed_steps"] == 2
    assert [e["reason"] for e in events if e["event"] == "step_skipped"] == ["duplicate_shell"]
    assert all(group[-1]["ok"] for group in result["state"]["completed_step_groups"])


@pytest.mark.parametrize("policy", ["heuristic", "lifecycle"])
def test_same_attempt_shell_repetition_still_exhausts_without_done(tmp_path, policy):
    shell = {"action": "shell", "arg": "printf x >> checks.txt"}
    result, events = _run(
        tmp_path, [{"tasks": ["check the project"]}, shell, shell, shell], step_policy=policy
    )

    assert result["status"] == "exhausted"
    assert (tmp_path / "checks.txt").read_text() == "x"
    assert result["state"]["completed_tasks"] == []
    assert [e["reason"] for e in events if e["event"] == "step_skipped"] == [
        "duplicate_shell",
        "stuck_shell_repeat",
    ]


def test_fresh_check_failure_is_recorded_instead_of_reusing_old_success(tmp_path):
    """A prior successful command is context, not fresh acceptance evidence."""
    (tmp_path / "check.py").write_text(
        "from pathlib import Path\n"
        "marker = Path('checked')\n"
        "already_checked = marker.exists()\n"
        "marker.touch()\n"
        "raise SystemExit(1 if already_checked else 0)\n"
    )
    shell = {"action": "shell", "arg": "python3 check.py"}
    result, events = _run(
        tmp_path,
        [
            {"tasks": ["initial check", "verify again"]},
            shell,
            {"action": "done"},
            shell,
            {"action": "fail", "reasoning": "the fresh check failed"},
        ],
    )

    assert result["status"] == "exhausted"
    assert [step["ok"] for step in result["state"]["all_steps"]] == [True, False]
    assert result["state"]["completed_tasks"] == ["initial check"]
    assert not any(e.get("reason") == "duplicate_shell" for e in events)


@pytest.mark.parametrize("policy", ["heuristic", "lifecycle"])
@pytest.mark.parametrize("boundary", ["task", "local_replan", "full_replan"])
def test_new_attempt_can_retry_a_previously_failed_shell(tmp_path, policy, boundary):
    """A transient failure must not prevent a new attempt from trying again."""
    (tmp_path / "check.py").write_text(
        "from pathlib import Path\n"
        "marker = Path('attempts.txt')\n"
        "previous = marker.read_text() if marker.exists() else ''\n"
        "marker.write_text(previous + 'x')\n"
        "raise SystemExit(0 if previous else 1)\n"
    )
    shell = {"action": "shell", "arg": "python3 check.py"}
    settings = {"step_policy": policy}
    if boundary == "full_replan":
        replies = [
            {"tasks": ["check the project"]},
            shell,
            {"action": "fail", "reasoning": "the first check failed"},
            {"tasks": ["confirm the project status now"]},
        ]
        settings["max_replans"] = 2
    else:
        replies = [
            {"tasks": ["record initial check status", "confirm the project status now"]},
            shell,
            {"action": "done"},
        ]
        if boundary == "local_replan":
            replies.extend(
                [
                    {"action": "fail", "reasoning": "need another approach"},
                    {"task": "execute the verification command and inspect its output"},
                ]
            )
            settings["max_task_local_replans"] = 1
    replies.extend([shell, {"action": "done"}])

    result, events = _run(tmp_path, replies, **settings)

    assert result["status"] == "complete"
    assert (tmp_path / "attempts.txt").read_text() == "xx"
    assert [step["ok"] for step in result["state"]["all_steps"]] == [False, True]
    assert not any(e.get("reason") == "stuck_shell" for e in events)


@pytest.mark.parametrize("policy", ["heuristic", "lifecycle"])
def test_rewrite_skip_does_not_prime_first_duplicate_shell(tmp_path, policy):
    """Exercise the feedback's alleged cross-action counter poisoning path.

    The shell after a guarded rewrite must execute and reset the streak;
    its first duplicate then gives a corrective turn, allowing explicit done.
    """
    shell = {"action": "shell", "arg": "cat app.txt"}
    result, events = _run(
        tmp_path,
        [
            {"tasks": ["write and verify app.txt"]},
            {"action": "write", "arg": "app.txt", "content": "first"},
            {"action": "write", "arg": "app.txt", "content": "second"},
            shell,
            shell,
            {"action": "done"},
        ],
        step_policy=policy,
        max_steps=5,
        rewrite_pressure_writes=1,
        rewrite_skip_writes=1,
    )

    assert result["status"] == "complete"
    assert (tmp_path / "app.txt").read_text() == "first"
    reasons = [e["reason"] for e in events if e["event"] == "step_skipped"]
    assert reasons == [
        "rewrite_loop" if policy == "heuristic" else "lifecycle_verify_before_rewrite",
        "duplicate_shell",
    ]


def test_done_and_fail_have_control_receipts_without_becoming_execution_evidence(tmp_path):
    result, events = _run(
        tmp_path,
        [
            {"tasks": ["check the project"]},
            {"action": "fail", "reasoning": "retry"},
            {"tasks": ["report project status"]},
            {"action": "done"},
        ],
        max_replans=2,
    )

    assert result["status"] == "complete"
    controls = [e for e in events if e["event"] == "step_control"]
    assert [e["action"] for e in controls] == ["fail", "done"]
    assert all(e["task_index"] == 0 and e["step"] == 0 for e in controls)
    assert [e for e in result["log"] if e["event"] == "step_control"] == controls
    assert result["state"]["selected_steps"] == 2
    assert result["state"]["executed_steps"] == result["state"]["skipped_steps"] == 0
    assert result["state"]["all_steps"] == []


def test_refused_done_logs_skip_and_only_accepted_claim_logs_control(tmp_path):
    result, events = _run(
        tmp_path,
        [
            {"tasks": ["write and verify app.txt"]},
            {"action": "write", "arg": "app.txt", "content": "ready"},
            {"action": "done"},
            {"action": "shell", "arg": "cat app.txt"},
            {"action": "done"},
        ],
        step_policy="lifecycle",
        max_steps=4,
    )

    assert result["status"] == "complete"
    controls = [e for e in events if e["event"] == "step_control"]
    assert len(controls) == 1
    assert controls[0]["action"] == "done" and controls[0]["step"] == 3
    assert any(e.get("reason") == "lifecycle_unverified_done" for e in events)
    state = result["state"]
    assert state["selected_steps"] == state["executed_steps"] + state["skipped_steps"] + len(
        controls
    )
