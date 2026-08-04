"""Deterministic tests for the action registry and single recorder (issue #36).

No LLM calls: these pin the one-source-of-truth action specs, typed dispatch
errors before side effects, ActionResult round-trips, and the
selected/executed/skipped counter semantics of the StepRecorder.
"""

import json
from pathlib import Path
from unittest.mock import patch

import askme
from askme import (
    ACTION_SPECS,
    OBSERVE_ACTIONS,
    ActionExecutor,
    ActionResult,
    SkippedStep,
    _run_loop,
    _validate_action_contract,
    execute,
)

# --- Registry as the single source of truth ---


class TestActionRegistry:
    def test_registry_covers_the_whole_action_protocol(self):
        assert set(ACTION_SPECS) == {
            "shell",
            "write",
            "edit",
            "read",
            "search",
            "tree",
            "done",
            "fail",
        }

    def test_categories_partition_the_actions(self):
        categories = {name: spec.category for name, spec in ACTION_SPECS.items()}
        assert categories == {
            "shell": "mutate",
            "write": "mutate",
            "edit": "mutate",
            "read": "observe",
            "search": "observe",
            "tree": "observe",
            "done": "control",
            "fail": "control",
        }

    def test_observe_actions_are_derived_from_the_registry(self):
        assert OBSERVE_ACTIONS == frozenset({"read", "search", "tree"})

    def test_only_control_actions_lack_handlers(self):
        for spec in ACTION_SPECS.values():
            if spec.category == "control":
                assert spec.handler is None
            else:
                assert callable(spec.handler)

    def test_tool_definitions_derive_from_the_registry(self):
        """The executor's model-visible action surface is the tool list,
        rendered one-to-one from ACTION_SPECS (the prompt no longer carries
        an action list — the tools parameter does)."""
        assert [t["function"]["name"] for t in askme._ACTION_TOOLS] == list(askme.ACTION_SPECS)
        assert [t["function"]["name"] for t in askme._ACTION_TOOLS] == [
            "shell",
            "write",
            "edit",
            "read",
            "search",
            "tree",
            "done",
            "fail",
        ]

    def test_specs_drive_the_decode_contract(self):
        """Blanking any registry-required field must fail decode validation."""
        complete = {
            "shell": {"action": "shell", "arg": "echo hi"},
            "write": {"action": "write", "arg": "f.txt", "content": "body"},
            "edit": {"action": "edit", "arg": "f.txt", "find": "a", "replace": "b"},
            "read": {"action": "read", "arg": "f.txt"},
            "search": {"action": "search", "arg": "pattern"},
            "tree": {"action": "tree"},
            "done": {"action": "done"},
            "fail": {"action": "fail"},
        }
        for name, spec in ACTION_SPECS.items():
            action = complete[name]
            assert _validate_action_contract(action) is True
            for required in spec.requires:
                broken = dict(action)
                broken[required] = None if required in ("content", "replace") else "  "
                assert _validate_action_contract(broken) is False, (name, required)


# --- Typed dispatch errors before side effects ---


class TestTypedDispatchErrors:
    def test_unknown_action_fails_before_side_effects(self, tmp_path):
        result = execute({"action": "dance", "arg": "x"}, str(tmp_path))
        assert result == {
            "ok": False,
            "output": "unknown action: dance",
            "error_type": "unknown_action",
        }
        assert list(tmp_path.iterdir()) == []

    def test_control_actions_never_reach_handlers(self, tmp_path):
        for name in ("done", "fail"):
            result = execute({"action": name, "arg": "x"}, str(tmp_path))
            assert result["ok"] is False
            assert result["error_type"] == "control_action"
        assert list(tmp_path.iterdir()) == []

    def test_malformed_actions_fail_at_dispatch_before_side_effects(self, tmp_path):
        """The registry contract is enforced at dispatch, not just decode: a
        write without content must not create an empty file."""
        cases = [
            {"action": "write", "arg": "x"},
            {"action": "edit", "arg": "x", "find": "a"},
            {"action": "edit", "arg": "x", "find": "", "replace": "b"},
            {"action": "shell", "arg": " "},
            {"action": "search", "arg": ""},
            {"action": "read", "arg": ""},
        ]
        for action in cases:
            result = execute(dict(action), str(tmp_path))
            assert result["ok"] is False, action
            assert result["error_type"] == "malformed_action", action
        assert list(tmp_path.iterdir()) == []

    def test_non_string_action_names_are_typed_failures_not_crashes(self, tmp_path):
        """Valid JSON can carry an unhashable action name ({"action": []});
        both registry lookups must reject it instead of raising TypeError."""
        for bad_name in ([], {}, 7, None):
            assert _validate_action_contract({"action": bad_name}) is False, bad_name
            result = execute({"action": bad_name, "arg": "x"}, str(tmp_path))
            assert result["ok"] is False, bad_name
            assert result["error_type"] == "malformed_action", bad_name
        assert list(tmp_path.iterdir()) == []

    def test_dispatch_reuses_parser_for_invalid_read_cursor(self, tmp_path):
        """The same parser rejects an invalid cursor before file access."""
        (tmp_path / "f.txt").write_text("hello\n")
        result = execute({"action": "read", "arg": "f.txt", "cursor": -1}, str(tmp_path))
        assert result["error_type"] == "invalid_read_cursor"
        assert "between 0" in result["output"]

    @patch("askme.replan_task", return_value=askme.TaskReplanResult(None, "unknown"))
    @patch("askme.ask_llm")
    def test_unknown_action_is_rejected_before_consuming_a_step(
        self, mock_llm, mock_replan, tmp_path
    ):
        """Issue #68: an unknown-action envelope is a typed schema rejection
        at the executor seam — it never reaches the dispatcher, and it never
        consumes a selected or executed step."""
        mock_llm.side_effect = [
            {"tasks": ["do something"]},
            {"action": "refactor", "arg": "f.txt"},
        ]
        result = _run_loop("do something", str(tmp_path), max_replans=1, max_tasks=1, max_steps=3)
        assert result["status"] == "exhausted"
        assert any(e.startswith("[unknown_action] refactor") for e in result["state"]["errors"])
        assert result["state"]["selected_steps"] == 0
        assert result["state"]["executed_steps"] == 0
        assert result["state"]["all_steps"] == []

    @patch("askme.replan_task", return_value=askme.TaskReplanResult(None, "unknown"))
    @patch("askme.ask_llm")
    def test_cross_type_envelope_is_rejected_before_consuming_a_step(
        self, mock_llm, mock_replan, tmp_path
    ):
        """A planner-shaped reply at the executor seam is a malformed_action
        schema rejection, not a dispatched step."""
        mock_llm.side_effect = [
            {"tasks": ["do something"]},
            {"tasks": ["do something else"]},
        ]
        result = _run_loop("do something", str(tmp_path), max_replans=1, max_tasks=1, max_steps=3)
        assert result["status"] == "exhausted"
        assert any(e.startswith("[malformed_action]") for e in result["state"]["errors"])
        assert result["state"]["selected_steps"] == 0
        assert result["state"]["executed_steps"] == 0
        assert result["state"]["all_steps"] == []


# --- Typed result round-trips at the compatibility seam ---


class TestSkippedStepRecord:
    def test_jsonl_event_shape(self):
        record = SkippedStep(
            task_index=1, step=2, action="read", arg="a" * 200, reason="duplicate_read"
        )
        assert record.jsonl_event() == {
            "event": "step_skipped",
            "task_index": 1,
            "step": 2,
            "action": "read",
            "arg": "a" * 120,
            "reason": "duplicate_read",
        }


class TestActionResult:
    def test_to_dict_omits_unset_error_type(self):
        assert ActionResult(True, "hi").to_dict() == {"ok": True, "output": "hi"}

    def test_round_trip_preserves_details(self):
        raw = {"ok": True, "output": "o", "truncated": True, "truncation_reasons": ["files"]}
        result = ActionResult.from_dict(raw)
        assert result.get("truncated") is True
        assert result.get("truncation_reasons") == ["files"]
        assert result.to_dict() == raw

    def test_from_dict_normalizes_missing_error_type(self):
        result = ActionResult.from_dict({"ok": False, "output": "x"})
        assert result.error_type is None
        assert result.to_dict() == {"ok": False, "output": "x"}

    def test_get_covers_core_fields_and_defaults(self):
        result = ActionResult(False, "boom", "timeout")
        assert result.get("ok") is False
        assert result.get("error_type") == "timeout"
        assert result.get("missing", "default") == "default"


class TestDispatchParity:
    """The typed dispatch and the execute() façade must agree exactly."""

    @staticmethod
    def _prepare(root):
        root.mkdir(parents=True)
        (root / "f.txt").write_text("hello\nworld\n")

    def test_dispatch_and_facade_agree_per_action(self, tmp_path):
        typed_dir = tmp_path / "one" / "w"
        legacy_dir = tmp_path / "two" / "w"
        self._prepare(typed_dir)
        self._prepare(legacy_dir)
        cases = [
            {"action": "shell", "arg": "echo hi"},
            {"action": "shell", "arg": "false"},
            {"action": "write", "arg": "new.txt", "content": "body\n"},
            {"action": "edit", "arg": "f.txt", "find": "world", "replace": "there"},
            {"action": "edit", "arg": "missing.txt", "find": "a", "replace": "b"},
            {"action": "read", "arg": "f.txt"},
            {"action": "read", "arg": "missing.txt"},
            {"action": "search", "arg": "hello"},
            {"action": "search", "arg": ""},
            {"action": "tree", "arg": ""},
            {"action": "tree", "arg": "missing"},
        ]
        for action in cases:
            typed = ActionExecutor(str(typed_dir)).dispatch(dict(action)).to_dict()
            legacy = execute(dict(action), str(legacy_dir))
            typed["output"] = typed["output"].replace(str(typed_dir), "<w>")
            legacy["output"] = legacy["output"].replace(str(legacy_dir), "<w>")
            assert typed == legacy, action


# --- Counter semantics of the single recorder ---


class TestRecorderCounterSemantics:
    @patch("askme.replan_task", return_value=askme.TaskReplanResult(None, "unknown"))
    @patch("askme.ask_llm")
    def test_selected_equals_executed_plus_skipped_plus_control(
        self, mock_llm, mock_replan, tmp_path
    ):
        Path(tmp_path, "f.txt").write_text("hello\n")
        mock_llm.side_effect = [
            {"tasks": ["inspect f.txt"]},
            {"action": "read", "arg": "f.txt"},
            {"action": "read", "arg": "f.txt"},  # duplicate → skipped
            {"action": "done"},
        ]
        result = _run_loop("inspect f.txt", str(tmp_path), max_replans=1, max_tasks=1, max_steps=5)
        state = result["state"]
        assert result["status"] == "complete"
        assert state["selected_steps"] == 3
        assert state["executed_steps"] == 1
        assert state["skipped_steps"] == 1
        # The accepted done closes the identity documented on StepRecorder.
        assert state["selected_steps"] == state["executed_steps"] + state["skipped_steps"] + 1

    @patch("askme.replan_task", return_value=askme.TaskReplanResult(None, "unknown"))
    @patch("askme.ask_llm")
    def test_corrective_observations_stay_out_of_the_structured_record(
        self, mock_llm, mock_replan, tmp_path
    ):
        Path(tmp_path, "f.txt").write_text("hello\n")
        mock_llm.side_effect = [
            {"tasks": ["inspect f.txt"]},
            {"action": "read", "arg": "f.txt"},
            {"action": "read", "arg": "f.txt"},
            {"action": "done"},
        ]
        result = _run_loop("inspect f.txt", str(tmp_path), max_replans=1, max_tasks=1, max_steps=5)
        state = result["state"]
        # Only the dispatched read enters the run-wide record; the duplicate's
        # corrective observation is model-visible sliding state only.
        assert [s["action"] for s in state["all_steps"]] == ["read"]
        assert any("Already read" in s.get("output", "") for s in state["last_steps"])
        assert not any("Already read" in s.get("output", "") for s in state["all_steps"])

    @patch("askme.execute")
    @patch("askme.ask_llm")
    def test_deterministic_receipts_are_recorded_but_never_counted_executed(
        self, mock_llm, mock_execute, tmp_path
    ):
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
            {"ok": True, "output": "Wrote main.c"},  # dispatched repair write
            {"ok": True, "output": "(no output)"},
        ]
        result = _run_loop("compile main.c", str(tmp_path), max_replans=1, max_tasks=1, max_steps=3)
        state = result["state"]
        assert result["status"] == "complete"
        # One model shell dispatched; repair + retry receipts recorded on top.
        assert [s["action"] for s in state["all_steps"]] == ["shell", "write", "shell"]
        assert state["selected_steps"] == 2  # shell + done
        assert state["executed_steps"] == 1
        assert state["skipped_steps"] == 0


# --- JSONL projections flow through the one record path ---


class TestRecorderJsonlProjection:
    @staticmethod
    def _events(run_log_path, run):
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(run_log_path)
        try:
            result = run()
        finally:
            askme.RUN_LOG_PATH = old
        events = [json.loads(line) for line in Path(run_log_path).read_text().splitlines()]
        for event in events:
            event.pop("ts", None)
        return result, events

    @patch("askme.replan_task", return_value=askme.TaskReplanResult(None, "unknown"))
    @patch("askme.ask_llm")
    def test_step_and_skip_events_share_one_record_path(self, mock_llm, mock_replan, tmp_path):
        Path(tmp_path, "f.txt").write_text("hello\n")
        mock_llm.side_effect = [
            {"tasks": ["inspect f.txt"]},
            {"action": "read", "arg": "f.txt"},
            {"action": "read", "arg": "f.txt"},
            {"action": "done"},
        ]
        result, events = self._events(
            tmp_path / "run.jsonl",
            lambda: _run_loop(
                "inspect f.txt", str(tmp_path), max_replans=1, max_tasks=1, max_steps=5
            ),
        )
        assert result["status"] == "complete"
        steps = [e for e in events if e["event"] == "step"]
        assert len(steps) == 1
        assert steps[0]["action"] == "read"
        assert steps[0]["ok"] is True
        assert steps[0]["sha256"]  # hash-linked read audit intact
        skips = [e for e in events if e["event"] == "step_skipped"]
        assert len(skips) == 1
        assert skips[0]["reason"] == "duplicate_read"
        run_end = [e for e in events if e["event"] == "run_end"][-1]
        assert run_end["steps"] == {"selected": 3, "executed": 1, "skipped": 1}

    @patch("askme.execute")
    @patch("askme.ask_llm")
    def test_deterministic_receipts_keep_their_jsonl_shapes(self, mock_llm, mock_execute, tmp_path):
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
            {"ok": True, "output": "Wrote main.c"},  # dispatched repair write
            {"ok": True, "output": "(no output)"},
        ]
        result, events = self._events(
            tmp_path / "run.jsonl",
            lambda: _run_loop(
                "compile main.c", str(tmp_path), max_replans=1, max_tasks=1, max_steps=3
            ),
        )
        assert result["status"] == "complete"
        repairs = [e for e in events if e["event"] == "deterministic_repair"]
        assert repairs == [
            {
                "event": "deterministic_repair",
                "kind": "compile_include",
                "file": "main.c",
                "description": "Auto-inserted #include <stdio.h>",
                "ok": True,
            }
        ]
        retries = [e for e in events if e["event"] == "step" and e.get("deterministic_retry")]
        assert len(retries) == 1
        assert retries[0]["ok"] is True
        assert retries[0]["action"] == "shell"
