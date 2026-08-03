"""Response-specific schemas and the typed terminal outcome (issue #68).

Covers the per-call-site response records (Plan/TaskReplan/Validation and the
action envelope), decode-time schema enforcement with the client's normal
retry policy, and the RunOutcome projection that keeps the run_end event and
the structured result in agreement.
"""

import json
from unittest.mock import patch

import pytest

import askme
from askme import (
    LLMClient,
    PlanResponse,
    RunOutcome,
    TaskReplanResponse,
    ValidationResponse,
    _action_envelope_error,
    _run_loop,
    ask_llm,
)
from tests._test_support import mock_response

# --- Typed response records ---


class TestPlanResponse:
    def test_accepts_valid_task_list(self):
        parsed = PlanResponse.parse({"tasks": ["a", "b"]}, max_tasks=10)
        assert parsed == PlanResponse(tasks=("a", "b"))

    def test_truncates_to_max_tasks_before_validating(self):
        parsed = PlanResponse.parse({"tasks": ["a", "b", "c"]}, max_tasks=2)
        assert parsed == PlanResponse(tasks=("a", "b"))

    @pytest.mark.parametrize(
        "reply",
        [
            {"tasks": []},
            {"tasks": ["a", "  "]},
            {"tasks": ["a", 3]},
            {"tasks": "a"},
            {"action": "shell", "arg": "ls"},
            {},
            None,
            "tasks",
        ],
    )
    def test_rejects_empty_and_cross_type_envelopes(self, reply):
        assert PlanResponse.parse(reply, max_tasks=10) is None


class TestTaskReplanResponse:
    def test_accepts_replacement_task(self):
        assert TaskReplanResponse.parse({"task": "fix it"}) == TaskReplanResponse(task="fix it")

    @pytest.mark.parametrize(
        "reply",
        [{"task": ""}, {"task": 3}, {"tasks": ["a"]}, {"action": "done"}, {}, None],
    )
    def test_rejects_empty_and_cross_type_envelopes(self, reply):
        assert TaskReplanResponse.parse(reply) is None


class TestValidationResponse:
    def test_accepts_boolean_verdicts(self):
        assert ValidationResponse.parse({"valid": True}) == ValidationResponse(valid=True)
        parsed = ValidationResponse.parse(
            {"valid": False, "reason": "missing file", "missing": ["app.py", "  ", 3]}
        )
        assert parsed == ValidationResponse(valid=False, reason="missing file", missing=("app.py",))

    @pytest.mark.parametrize(
        "reply",
        [{"valid": "true"}, {"valid": None}, {"tasks": ["a"]}, {"action": "done"}, {}, None],
    )
    def test_rejects_non_boolean_and_cross_type_envelopes(self, reply):
        assert ValidationResponse.parse(reply) is None

    def test_normalizes_malformed_reason_and_missing(self):
        parsed = ValidationResponse.parse({"valid": False, "reason": 7, "missing": "app.py"})
        assert parsed == ValidationResponse(valid=False, reason="", missing=())


class TestActionEnvelope:
    @pytest.mark.parametrize(
        "action",
        [
            {"action": "shell", "arg": "ls"},
            {"action": "write", "arg": "f.py", "content": "x = 1\n"},
            {"action": "write", "arg": "f.json", "content": {"k": "v"}},
            {"action": "edit", "arg": "f.py", "find": "x", "replace": "y"},
            {"action": "read", "arg": "f.py"},
            {"action": "search", "arg": "pattern"},
            {"action": "tree"},
            {"action": "done"},
            {"action": "fail", "reasoning": "cannot"},
        ],
    )
    def test_accepts_dispatchable_and_control_envelopes(self, action):
        assert _action_envelope_error(action) is None

    @pytest.mark.parametrize(
        "reply",
        [{}, None, "done", {"tasks": ["a"]}, {"valid": True}, {"action": ""}, {"action": 3}],
    )
    def test_empty_and_cross_type_envelopes_are_malformed(self, reply):
        assert _action_envelope_error(reply) == "malformed_action"

    def test_unknown_action_is_typed_separately(self):
        assert _action_envelope_error({"action": "refactor", "arg": "f"}) == "unknown_action"

    @pytest.mark.parametrize(
        "action",
        [
            {"action": "shell"},
            {"action": "write", "arg": "f.py"},
            {"action": "write", "arg": "f.py", "content": "  "},
            {"action": "edit", "arg": "f.py", "find": "x"},
            {"action": "read", "arg": ""},
        ],
    )
    def test_contract_violations_are_malformed(self, action):
        assert _action_envelope_error(action) == "malformed_action"


# --- Decode-time schema enforcement through the client retry policy ---


class TestDecodeSchemaEnforcement:
    @patch("askme.requests.post")
    def test_cross_type_reply_at_action_site_retries_then_types(self, mock_post):
        """A plan reply at the action seam burns the parse retries and raises
        with the typed envelope classification."""
        mock_post.return_value = mock_response({"tasks": ["do it"]})
        with pytest.raises(json.JSONDecodeError) as excinfo:
            ask_llm([{"role": "user", "content": "t"}], expect="action")
        assert mock_post.call_count == askme.MAX_LLM_RETRIES + 1
        assert getattr(excinfo.value, "envelope_error") == "malformed_action"
        assert getattr(excinfo.value, "malformed_action") is True

    @patch("askme.requests.post")
    def test_unknown_action_reply_raises_typed_after_retries(self, mock_post):
        mock_post.return_value = mock_response({"action": "refactor", "arg": "f"})
        with pytest.raises(json.JSONDecodeError) as excinfo:
            ask_llm([{"role": "user", "content": "t"}], expect="action")
        assert getattr(excinfo.value, "envelope_error") == "unknown_action"

    @patch("askme.requests.post")
    def test_schema_retry_can_recover_a_valid_action(self, mock_post):
        mock_post.side_effect = [
            mock_response({"tasks": ["not an action"]}),
            mock_response({"action": "done"}),
        ]
        result = ask_llm([{"role": "user", "content": "t"}], expect="action")
        assert result == {"action": "done"}

    @patch("askme.requests.post")
    def test_action_reply_at_plan_site_is_rejected(self, mock_post):
        mock_post.return_value = mock_response({"action": "shell", "arg": "ls"})
        with pytest.raises(json.JSONDecodeError):
            ask_llm([{"role": "user", "content": "t"}], expect="plan")

    @patch("askme.requests.post")
    def test_plan_schema_retry_can_recover_a_valid_plan(self, mock_post):
        mock_post.side_effect = [
            mock_response({"tasks": []}),
            mock_response({"tasks": ["write app.py"]}),
        ]
        result = ask_llm([{"role": "user", "content": "t"}], expect="plan")
        assert result == {"tasks": ["write app.py"]}

    @patch("askme.requests.post")
    def test_no_expect_keeps_the_permissive_decode(self, mock_post):
        mock_post.return_value = mock_response({"tasks": ["anything"]})
        assert ask_llm([{"role": "user", "content": "t"}]) == {"tasks": ["anything"]}

    @patch("askme.requests.post")
    def test_plan_schema_honors_the_configured_task_limit(self, mock_post):
        """PR #75 review: decode-time truncation follows the run's max_tasks,
        so an entry past the configured limit cannot fail a valid plan."""
        mock_post.return_value = mock_response({"tasks": ["valid task", None]})
        result = ask_llm(
            [{"role": "user", "content": "t"}],
            expect="plan",
            expect_context={"max_tasks": 1},
        )
        assert result == {"tasks": ["valid task", None]}
        with pytest.raises(json.JSONDecodeError):
            ask_llm([{"role": "user", "content": "t"}], expect="plan")

    def test_unknown_expect_is_rejected(self):
        with pytest.raises(ValueError, match="expect"):
            LLMClient().ask([{"role": "user", "content": "t"}], expect="poem")


# --- Typed terminal outcome ---


class TestRunOutcome:
    def _events(self, log_path):
        return [json.loads(line) for line in log_path.read_text().splitlines()]

    def test_result_outcome_matches_run_end_event(self, tmp_path):
        log_path = tmp_path / "run.jsonl"
        responses = [
            {"tasks": ["write hi.txt"]},
            {"action": "write", "arg": "hi.txt", "content": "hi"},
            {"action": "done"},
        ]
        with (
            patch.object(askme, "RUN_LOG_PATH", str(log_path)),
            patch("askme.ask_llm", side_effect=responses),
        ):
            result = _run_loop("write hi.txt", str(tmp_path), max_replans=1)
        assert result["status"] == "complete"
        outcome = result["outcome"]
        assert outcome["status"] == "complete"
        assert outcome["validation"] == "skipped"
        assert outcome["completed_tasks"] == 1
        assert outcome["steps"] == {"selected": 2, "executed": 1, "skipped": 0}
        run_end = [e for e in self._events(log_path) if e["event"] == "run_end"][-1]
        assert run_end["status"] == outcome["status"]
        assert run_end["steps"] == outcome["steps"]
        assert run_end["wall_s"] == outcome["wall_s"]
        assert run_end["completed_tasks"] == outcome["completed_tasks"]

    def test_llm_validation_pass_disposition(self, tmp_path):
        responses = [
            {"tasks": ["write hi.txt"]},
            {"action": "write", "arg": "hi.txt", "content": "hi"},
            {"action": "done"},
            {"valid": True},
        ]
        with (
            patch.object(askme, "FINAL_VALIDATE", "always"),
            patch("askme.ask_llm", side_effect=responses),
        ):
            result = _run_loop("write hi.txt", str(tmp_path), max_replans=1)
        assert result["status"] == "complete"
        assert result["outcome"]["validation"] == "passed"

    def test_deterministic_validation_disposition(self, tmp_path):
        responses = [
            {"tasks": ["run the check"]},
            {"action": "shell", "arg": "true"},
            {"action": "done"},
        ]
        with (
            patch.object(askme, "FINAL_VALIDATE", "always"),
            patch("askme.ask_llm", side_effect=responses),
        ):
            result = _run_loop("run the check", str(tmp_path), max_replans=1)
        assert result["status"] == "complete"
        assert result["outcome"]["validation"] == "deterministic"

    def test_unavailable_validation_disposition(self, tmp_path):
        def mock_ask_llm(messages, **kwargs):
            if messages and "completion validator" in messages[0].get("content", ""):
                raise askme.LLMTransportError("unreachable")
            return mock_ask_llm.replies.pop(0)

        mock_ask_llm.replies = [
            {"tasks": ["write hi.txt"]},
            {"action": "write", "arg": "hi.txt", "content": "hi"},
            {"action": "done"},
        ]
        with (
            patch.object(askme, "FINAL_VALIDATE", "always"),
            patch("askme.ask_llm", side_effect=mock_ask_llm),
        ):
            result = _run_loop("write hi.txt", str(tmp_path), max_replans=1)
        assert result["status"] == "complete_unverified"
        assert result["outcome"]["validation"] == "unavailable"

    @patch("askme.replan_task", return_value=askme.TaskReplanResult(None, "unknown"))
    def test_exhausted_outcome_records_pending_validation_failure(self, mock_replan, tmp_path):
        responses = [
            {"tasks": ["fix app.py"]},
            {"action": "write", "arg": "app.py", "content": "x = 1\n"},
            {"action": "done"},
            {"valid": False, "reason": "not verified", "missing": []},
            {"tasks": ["finish app.py"]},
            {"action": "done"},
        ]
        with (
            patch.object(askme, "FINAL_VALIDATE", "always"),
            patch("askme.ask_llm", side_effect=responses),
        ):
            result = _run_loop("fix app.py", str(tmp_path), max_replans=2, max_steps=2)
        assert result["status"] == "exhausted"
        outcome = result["outcome"]
        assert outcome["validation"] == "failed"
        assert result["state"]["validation_recheck_needed"] is True

    @patch("askme.replan_task", return_value=askme.TaskReplanResult(None, "unknown"))
    @patch("askme.ask_llm")
    def test_exhausted_event_keeps_the_historical_error_shape(
        self, mock_llm, mock_replan, tmp_path
    ):
        log_path = tmp_path / "run.jsonl"
        mock_llm.side_effect = [
            {"tasks": ["run check"]},
            {"action": "fail", "reasoning": "cannot"},
        ]
        with patch.object(askme, "RUN_LOG_PATH", str(log_path)):
            result = _run_loop("run check", str(tmp_path), max_replans=1, max_steps=2)
        assert result["status"] == "exhausted"
        run_end = [e for e in self._events(log_path) if e["event"] == "run_end"][-1]
        assert run_end["errors"] == result["state"]["errors"][-5:]
        assert "completed_tasks" not in run_end
        assert result["outcome"]["validation"] == "skipped"

    def test_run_end_event_shape_is_exact(self):
        outcome = RunOutcome(
            status="complete",
            validation="passed",
            replans=1,
            wall_s=2.5,
            completed_tasks=2,
            selected_steps=5,
            executed_steps=4,
            skipped_steps=1,
        )
        assert outcome.run_end_event() == {
            "event": "run_end",
            "status": "complete",
            "replans": 1,
            "wall_s": 2.5,
            "completed_tasks": 2,
            "steps": {"selected": 5, "executed": 4, "skipped": 1},
        }
        exhausted = RunOutcome(
            status="exhausted",
            validation="skipped",
            replans=3,
            wall_s=9.0,
            completed_tasks=0,
            selected_steps=2,
            executed_steps=1,
            skipped_steps=1,
            errors=("[stuck_loop] shell x: repeated",),
        )
        assert exhausted.run_end_event() == {
            "event": "run_end",
            "status": "exhausted",
            "replans": 3,
            "wall_s": 9.0,
            "errors": ["[stuck_loop] shell x: repeated"],
            "steps": {"selected": 2, "executed": 1, "skipped": 1},
        }
