"""Offline tests for the Phase 1 evaluation-policy and CLI plumbing."""

import json
from unittest.mock import patch

from _test_support import mock_response_raw

import askme


class TestReasoningPolicy:
    def test_off_suppresses_explicit_and_retry_reasoning_and_logs_decisions(self, tmp_path):
        log_path = tmp_path / "run.jsonl"
        responses = [
            mock_response_raw("not json"),
            mock_response_raw('{"valid": true}'),
        ]
        with (
            patch.object(askme, "LLM_BACKEND", "openrouter"),
            patch.object(askme, "RUN_LOG_PATH", str(log_path)),
            patch("askme.requests.post", side_effect=responses) as mock_post,
        ):
            result = askme.ask_llm(
                [{"role": "user", "content": "check"}],
                think=True,
                reasoning_policy="off",
                reasoning_trigger="final_validator",
                max_retries=1,
            )

        assert result == {"valid": True}
        assert len(mock_post.call_args_list) == 2
        for call in mock_post.call_args_list:
            assert call.kwargs["json"]["reasoning"] == {"enabled": False}

        decisions = [
            json.loads(line)
            for line in log_path.read_text().splitlines()
            if json.loads(line)["event"] == "reasoning_decision"
        ]
        assert [event["requested_policy"] for event in decisions] == ["off", "off"]
        assert [event["requested_trigger"] for event in decisions] == [
            "final_validator",
            "final_validator",
        ]
        assert [event["effective_level"] for event in decisions] == [None, None]

    def test_off_suppresses_automatic_json_retry_reasoning(self):
        responses = [
            mock_response_raw("not json"),
            mock_response_raw('{"ok": true}'),
        ]
        with (
            patch.object(askme, "LLM_BACKEND", "openrouter"),
            patch("askme.requests.post", side_effect=responses) as mock_post,
        ):
            assert askme.ask_llm(
                [{"role": "user", "content": "test"}],
                reasoning_policy="off",
                max_retries=1,
            ) == {"ok": True}

        assert mock_post.call_args_list[0].kwargs["json"]["reasoning"] == {
            "enabled": False,
        }
        assert mock_post.call_args_list[1].kwargs["json"]["reasoning"] == {
            "enabled": False,
        }

    def test_policy_and_triggers_reach_all_reasoning_call_sites(self, tmp_path):
        state = {
            "errors": ["[unknown] behavior mismatch"],
            "completed_tasks": [],
            "reasoning_policy": "off",
            "completed_step_groups": [],
            "all_steps": [],
        }
        with patch("askme.ask_llm", return_value={"tasks": []}) as mock_llm:
            askme.get_plan("fix behavior", state)
        assert mock_llm.call_args.kwargs["reasoning_policy"] == "off"
        assert mock_llm.call_args.kwargs["reasoning_trigger"] == "planner_replan"
        assert mock_llm.call_args.kwargs["think"] is True

        with patch("askme.ask_llm", return_value={"task": "implement alternative"}) as mock_llm:
            assert askme.replan_task(
                "fix behavior", state["errors"], [], state, "goal"
            ) == askme.TaskReplanResult("implement alternative", None)
        assert mock_llm.call_args.kwargs["reasoning_policy"] == "off"
        assert mock_llm.call_args.kwargs["reasoning_trigger"] == "task_local_replan"
        assert mock_llm.call_args.kwargs["think"] is False

        with patch("askme.ask_llm", return_value={"valid": True}) as mock_llm:
            assert askme._validate_completion(
                "deliver artifact", state, tmp_path
            ) == askme.ValidationResponse(valid=True)
        assert mock_llm.call_args.kwargs["reasoning_policy"] == "off"
        assert mock_llm.call_args.kwargs["reasoning_trigger"] == "final_validator"
        assert mock_llm.call_args.kwargs["think_level"] == "high"

    def test_run_control_metadata_is_not_exposed_as_planner_evidence(self):
        state = {
            "errors": [],
            "completed_tasks": [],
            "reasoning_policy": "off",
            "goal_context_chars": 512,
        }
        with patch("askme.ask_llm", return_value={"tasks": []}) as mock_llm:
            askme.get_plan("fix the behavior", state)

        planner_message = mock_llm.call_args.args[0][1]["content"]
        assert "reasoning_policy" not in planner_message
        assert "goal_context_chars" not in planner_message

    def test_execution_error_trigger_is_explicit_under_off_policy(self, tmp_path):
        responses = [
            {"tasks": ["exercise recovery"]},
            {"action": "shell", "arg": "false"},
            {"action": "shell", "arg": "echo recovered"},
            {"action": "done"},
        ]
        with (
            patch("askme.ask_llm", side_effect=responses) as mock_llm,
            patch.object(askme, "FINAL_VALIDATE", "0"),
        ):
            result = askme._run_loop(
                "exercise recovery",
                str(tmp_path),
                max_replans=1,
                max_tasks=1,
                max_steps=3,
                reasoning_policy="off",
            )

        assert result["status"] == "complete"
        recovery_call = mock_llm.call_args_list[2]
        assert recovery_call.kwargs["think"] is True
        assert recovery_call.kwargs["reasoning_policy"] == "off"
        assert recovery_call.kwargs["reasoning_trigger"] == "execution_error:unknown"

    def test_duplicate_escalation_has_named_trigger(self, tmp_path):
        responses = [
            {"tasks": ["write data"]},
            {"action": "write", "arg": "data.txt", "content": "hello"},
            {"action": "write", "arg": "data.txt", "content": "hello"},
            {"action": "write", "arg": "data.txt", "content": "hello"},
            {"action": "done"},
        ]
        with patch("askme.ask_llm", side_effect=responses) as mock_llm:
            result = askme._run_loop(
                "write data",
                str(tmp_path),
                max_replans=1,
                max_tasks=1,
                max_steps=4,
                reasoning_policy="off",
            )

        assert result["status"] == "complete"
        escalated_call = mock_llm.call_args_list[-1]
        assert escalated_call.kwargs["think"] is True
        assert escalated_call.kwargs["reasoning_policy"] == "off"
        assert escalated_call.kwargs["reasoning_trigger"] == "duplicate_action"


class TestGoalContext:
    def test_goal_has_independent_budget_from_other_executor_fields(self):
        long_goal = "g" * 500
        long_task = "t" * 500
        state = {"last_steps": [], "completed_tasks": []}
        with patch("askme.ask_llm", return_value={"action": "done"}) as mock_llm:
            askme.get_step(
                long_task,
                state,
                goal=long_goal,
                goal_context_chars=420,
                reasoning_policy="off",
                reasoning_trigger="executor",
            )

        message = mock_llm.call_args.args[0][1]["content"]
        goal_text = message.split("GOAL:\n", 1)[1].split("\n\nTASK:", 1)[0]
        task_text = message.split("TASK:\n", 1)[1].split("\n\nSTATE:", 1)[0]
        assert goal_text == long_goal[:420]
        assert task_text == long_task[: askme.MAX_INPUT]

    def test_run_freezes_goal_context_and_records_budget(self, tmp_path):
        prompt = "0123456789" * 80
        seen = []

        def step(*args, **kwargs):
            seen.append((kwargs["goal"], kwargs["goal_context_chars"]))
            if len(seen) == 1:
                askme.GOAL_CONTEXT_CHARS = 1
                return {"action": "shell", "arg": "echo ok"}
            return {"action": "done"}

        with (
            patch("askme.get_plan", return_value={"tasks": ["work"]}),
            patch("askme.get_step", side_effect=step),
            patch.object(askme, "GOAL_CONTEXT_CHARS", askme.GOAL_CONTEXT_CHARS),
        ):
            result = askme._run_loop(
                prompt,
                str(tmp_path),
                max_replans=1,
                max_tasks=1,
                max_steps=2,
                goal_context_chars=420,
            )

        assert result["status"] == "complete"
        assert seen == [(prompt[:420], 420), (prompt[:420], 420)]
        assert result["state"]["goal_context_chars"] == 420


class TestPhaseOneCli:
    def test_prompt_file_workspace_result_and_budgets_are_forwarded(self, tmp_path):
        prompt_file = tmp_path / "task.txt"
        prompt_file.write_text("implement the feature")
        result_file = tmp_path / "result.json"
        expected = {"status": "complete", "state": {"ok": True}, "log": []}

        with patch("askme.run_result", return_value=expected) as run_result:
            exit_code = askme._main(
                [
                    "--prompt-file",
                    str(prompt_file),
                    "--working-dir",
                    str(tmp_path),
                    "--result-json",
                    str(result_file),
                    "--reasoning-policy",
                    "off",
                    "--max-replans",
                    "2",
                    "--max-tasks",
                    "4",
                    "--max-steps",
                    "6",
                    "--goal-context-chars",
                    "1200",
                ]
            )

        assert exit_code == 0
        assert json.loads(result_file.read_text()) == expected
        env_config = askme.RunConfig.from_env()
        run_result.assert_called_once_with(
            "implement the feature",
            working_dir=str(tmp_path),
            config=askme.RunConfig(
                llm=env_config.llm,
                allow_system_installs=env_config.allow_system_installs,
                allow_network=env_config.allow_network,
                reasoning_policy="off",
                max_replans=2,
                max_tasks=4,
                max_steps=6,
                goal_context_chars=1200,
            ),
        )
