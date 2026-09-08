"""Run-scoped state, rewrite pressure and the single step recorder.

This leaf module depends only on action records and the standard library.
It never loads environment configuration or imports policy, transport or CLI code.
"""

import time
from pathlib import Path
from typing import Any

from actions import SkippedStep

REWRITE_PRESSURE_WRITES = 2
REWRITE_SKIP_WRITES = 3


def _ignore(*_args, **_kwargs):
    """Default silent event sink for direct state-module use."""


class StepRecorder:
    """The single record-and-count path for controller steps (issue #36).

    Counter semantics: ``selected`` counts every decoded model action,
    including ``done``/``fail``; ``executed`` counts model actions dispatched
    to handlers (deterministic repair/retry receipts are recorded but never
    counted as executed); ``skipped`` counts selected actions a controller
    guard suppressed before dispatch. Per attempt, selected == executed +
    skipped + accepted control actions.
    """

    def __init__(self, state, history, event_sink=None):
        self.state = state
        self.history = history
        # Direct module use is silent unless a sink is injected. The facade
        # supplies its late-bound _run_log callback for compatibility.
        self._event_sink = _ignore if event_sink is None else event_sink

    def _event(self, event):
        self._event_sink(event)

    def selected(self):
        self.state["selected_steps"] += 1

    def executed(self):
        self.state["executed_steps"] += 1

    def control(self, task_index, step, act):
        """Record an accepted model control action, never execution evidence.

        Refused ``done`` claims use ``skip`` instead. Keeping this out of
        last_steps/all_steps preserves duplicate context and validation
        evidence while making accepted done/fail selections observable.
        """
        event = {"event": "step_control", "task_index": task_index, "step": step, "action": act}
        self.history.append(dict(event))
        self._event(event)

    def skip(self, task_index, step, act, action, reason):
        """Record a selected-but-not-dispatched action in run metrics + log."""
        self.state["skipped_steps"] += 1
        record = SkippedStep(
            task_index=task_index,
            step=step,
            action=act,
            arg=action.get("arg", ""),
            reason=reason,
        )
        self._event(record.jsonl_event())

    def note(self, entry):
        """Model-visible corrective observation: enters the sliding window
        only, never the run-wide structured record or the JSONL log."""
        self.state["last_steps"].append(entry)

    def record(self, receipt, task_index, step, wall_s=None):
        """Append a receipt to every projection; returns the live entry."""
        entry = receipt.entry
        self.state["last_steps"].append(entry)
        self.state["all_steps"].append(dict(entry))
        self.history.append(receipt.history_event(task_index, step))
        self._event(receipt.jsonl_event(task_index, step, wall_s))
        return entry

    def append_recovery_hint(self, hint):
        """Suffix the newest recorded step's output with a recovery hint."""
        for steps in (self.state["last_steps"], self.state["all_steps"]):
            steps[-1]["output"] = steps[-1]["output"][:100] + f" → {hint}"


class RunState:
    """Typed owner of run-scoped controller data (issue #31).

    ``data`` remains the structured state dict callers receive in the run
    result and the planner/executor summaries are curated from; the single
    :class:`StepRecorder` projects receipts into it and ``history``. The
    rewrite-damping fields live here because they are run-scoped, not
    attempt-scoped: a task-local retry or full replan must not let the
    executor restart a same-target full-write streak; only the documented
    successful shell/edit and truncation paths disarm it.
    """

    def __init__(
        self,
        reasoning_policy,
        goal_context_chars,
        clock=None,
        event_sink=None,
        rewrite_pressure_writes=None,
        rewrite_skip_writes=None,
        *,
        recorder_factory=StepRecorder,
    ):
        self.clock = time.time if clock is None else clock
        # Resolved per-run guard thresholds (issue #68); None keeps the
        # module constants so direct constructions behave unchanged.
        self.rewrite_pressure_writes = (
            REWRITE_PRESSURE_WRITES if rewrite_pressure_writes is None else rewrite_pressure_writes
        )
        self.rewrite_skip_writes = (
            REWRITE_SKIP_WRITES if rewrite_skip_writes is None else rewrite_skip_writes
        )
        self.data: dict[str, Any] = {
            "completed_tasks": [],
            "errors": [],
            "validated_once": False,
            "validation_attempts": 0,
            "validation_recheck_needed": False,
            "validated_step_count": 0,
            "completed_step_groups": [],
            "all_steps": [],
            # Empty sentinel truncations dispatch no mutation, but a following
            # `done` must not treat the failed write attempt as completion.
            "pending_empty_writes": {},
            "task_start_step_count": 0,
            "reasoning_policy": reasoning_policy,
            "goal_context_chars": goal_context_chars,
            # Selected vs executed accounting (issue #7): the Qwen canary selected
            # 14 reads but only 2 reached the dispatcher — that gap must be
            # first-class in run metrics, not reconstructed from logs.
            "selected_steps": 0,
            "executed_steps": 0,
            "skipped_steps": 0,
        }
        self.history = []
        self.recorder = recorder_factory(self.data, self.history, event_sink=event_sink)
        self.started = self.clock()
        self.last_write_target = None
        self.consecutive_target_writes = 0

    def elapsed(self):
        """Wall seconds since the run started."""
        return self.clock() - self.started

    def disarm_rewrite_damping(self):
        """Forget the streak entirely (documented truncation-recovery paths)."""
        self.last_write_target = None
        self.consecutive_target_writes = 0

    def break_rewrite_streak(self):
        """A successful shell/edit ends the streak; observations never do."""
        self.consecutive_target_writes = 0

    def note_successful_full_write(self, target):
        """Advance or restart the same-target full-write streak."""
        if target == self.last_write_target:
            self.consecutive_target_writes += 1
        else:
            self.last_write_target = target
            self.consecutive_target_writes = 1

    def rewrite_skip_armed(self, target):
        """True when further full rewrites of ``target`` must be skipped."""
        return (
            self.last_write_target is not None
            and self.consecutive_target_writes >= self.rewrite_skip_writes
            and target == self.last_write_target
        )

    def validate_pressure_target(self):
        """Basename the executor must verify once rewrites repeat, or None."""
        if (
            self.last_write_target is not None
            and self.consecutive_target_writes >= self.rewrite_pressure_writes
        ):
            return Path(str(self.last_write_target)).name
        return None
