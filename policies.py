"""Step, write-obligation and completion policies for one AskMe run.

Policy algorithms own their decisions and consume explicit progress views and
services. Model calls remain injected; importing this module does not load
configuration, credentials, the CLI or the provider client. Completion claims,
validation verdicts and terminal records remain separate evidence.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol, TypeVar

from actions import (
    OBSERVE_ACTIONS,
    SHELL_TIMEOUT_LONG,
    SHELL_TIMEOUT_MAX,
    _get_shell_timeout,
    _mutation_target_key,
    _read_key,
    _target_recovery_arg,
    _valid_nonempty_str,
)
from state import PendingWrite, RunProgress

_VALIDATE_KEYWORDS = re.compile(
    r"\b(compile|build|test|run|execute|fix|debug|repair|verify|install|server|api|script|program)\b",
    re.I,
)


class _StepFlow(Enum):
    """Control-flow outcome of a step decision inside one task attempt."""

    NEXT_STEP = "next_step"  # handled; select the next executor action
    END_ATTEMPT = "end_attempt"  # attempt over: done, fail, stuck, or error


@dataclass(frozen=True)
class RunOutcome:
    """Typed terminal record for one run (issue #68).

    The controller builds exactly one of these at its terminal site; the
    JSONL ``run_end`` event and the structured result's ``status`` and
    ``outcome`` fields are projections of it, so the terminal claim cannot
    diverge between the log and the returned record. ``validation`` is the
    final-validation disposition — ``passed``, ``deterministic``,
    ``unavailable``, ``failed``, or ``skipped`` — kept separate from
    ``status`` because completion and verification are distinct claims: an
    agent-reported ``done`` is a claim, and only ``passed`` or
    ``deterministic`` record an independent verdict.
    """

    status: str  # "complete" | "complete_unverified" | "exhausted"
    validation: str
    replans: int
    wall_s: float
    completed_tasks: int
    selected_steps: int
    executed_steps: int
    skipped_steps: int
    errors: tuple[str, ...] = ()

    def _steps(self):
        return {
            "selected": self.selected_steps,
            "executed": self.executed_steps,
            "skipped": self.skipped_steps,
        }

    def run_end_event(self):
        """The historical ``run_end`` JSONL event shape for this outcome."""
        event: dict[str, Any] = {
            "event": "run_end",
            "status": self.status,
            "replans": self.replans,
            "wall_s": self.wall_s,
        }
        if self.status == "exhausted":
            event["errors"] = list(self.errors)
        else:
            event["completed_tasks"] = self.completed_tasks
        event["steps"] = self._steps()
        return event

    def describe(self):
        """JSON-ready terminal record for the structured run result."""
        return {
            "status": self.status,
            "validation": self.validation,
            "replans": self.replans,
            "wall_s": self.wall_s,
            "completed_tasks": self.completed_tasks,
            "steps": self._steps(),
        }


def _unresolved_incomplete_writes(steps, working_dir=None):
    """Latest unresolved truncated writes, keyed by normalized target.

    Only a later complete write/append to the same target resolves truncation;
    an edit cannot reconstruct the suffix that never arrived.
    """
    unresolved = {}
    for idx, step in enumerate(steps):
        if step.get("action") != "write" or not step.get("ok"):
            continue
        target = _mutation_target_key(step, working_dir)
        if target is None:
            continue
        if step.get("_truncated_write"):
            unresolved[target] = (idx, step)
        else:
            unresolved.pop(target, None)
    return unresolved


def _incomplete_step_hint(target, step):
    """Return the display name and actionable target for a partial write."""
    arg = step.get("arg", "")
    name = Path(arg).name if arg else "file"
    recovery_arg = step.get("_recovery_arg")
    if not _valid_nonempty_str(recovery_arg):
        recovery_arg = target if _valid_nonempty_str(target) else arg
    return name, recovery_arg if _valid_nonempty_str(recovery_arg) else name


def _pending_empty_hint(target, info):
    """Return the display name, actionable target, and recovery mode."""
    if isinstance(info, dict):
        raw_name = info.get("name", "")
        append_allowed = info.get("append_allowed", False)
        recovery_arg = info.get("recovery_arg")
    else:
        raw_name = info
        append_allowed = False
        recovery_arg = None
    name = Path(str(raw_name)).name if raw_name else "file"
    if not _valid_nonempty_str(recovery_arg):
        recovery_arg = target if _valid_nonempty_str(target) else raw_name
    if not _valid_nonempty_str(recovery_arg):
        recovery_arg = name
    return name, recovery_arg, bool(append_allowed)


def _restrictive_pending_empty(pending):
    """Return the first pending obligation that forbids append recovery."""
    for target, info in pending.items():
        if not (isinstance(info, dict) and info.get("append_allowed", False)):
            return target, info
    return None


def _next_pending_empty(pending):
    """Choose an actionable obligation, with restrictive overwrites first."""
    restrictive = _restrictive_pending_empty(pending)
    if restrictive is not None:
        return restrictive
    return next(iter(pending.items()))


def _incomplete_write_visibility(all_steps, pending_empty_writes=None):
    """Run-scoped incomplete artifact state for either replanner."""
    pending = pending_empty_writes or {}
    restrictive = _restrictive_pending_empty(pending)
    if restrictive is not None:
        target, pending_info = restrictive
        name, recovery_arg, _ = _pending_empty_hint(target, pending_info)
        return {
            "incomplete_write": name,
            "incomplete_write_target": recovery_arg,
            "incomplete_write_append_allowed": False,
        }

    unresolved = _unresolved_incomplete_writes(all_steps)
    if unresolved:
        target, (_, last_step) = max(unresolved.items(), key=lambda item: item[1][0])
        name, recovery_arg = _incomplete_step_hint(target, last_step)
        return {
            "incomplete_write": name,
            "incomplete_write_target": recovery_arg,
            "incomplete_write_append_allowed": True,
        }

    if pending:
        # A restrictive overwrite can block recovery of an older permissive
        # append obligation. Surface it first so following the hint always
        # makes progress; all completion paths use this same selection.
        target, pending_info = _next_pending_empty(pending)
        name, recovery_arg, append_allowed = _pending_empty_hint(target, pending_info)
        return {
            "incomplete_write": name,
            "incomplete_write_target": recovery_arg,
            "incomplete_write_append_allowed": append_allowed,
        }
    return None


def _completion_blocker(state, working_dir):
    """Compatibility mapping boundary for the canonical typed completion gate."""
    return _progress_completion_blocker(RunProgress(state), working_dir)


def _progress_completion_blocker(progress: RunProgress, working_dir):
    """Single finish-eligibility gate for incomplete-write obligations (#31).

    Both completion sites — the executor's ``done`` claim and post-task
    acceptance — must refuse while any truncated or zero-byte write obligation
    is unresolved, and must steer recovery at the same target the replanner
    visibility reports. Returns ``(name, recovery_arg, append_allowed)`` for
    the most actionable obligation, or ``None`` when nothing blocks
    completion. Selection order matches ``_incomplete_write_visibility``: a
    restrictive pending overwrite first, then the newest unresolved truncated
    write, then any remaining pending obligation.
    """
    unresolved = _unresolved_incomplete_writes(progress.optional_all_steps, working_dir)
    pending = progress.optional_pending_empty_writes
    if not unresolved and not pending:
        return None
    restrictive = _restrictive_pending_empty(pending)
    if restrictive is not None:
        target, pending_info = restrictive
        return _pending_empty_hint(target, pending_info)
    if unresolved:
        target, (_, incomplete_step) = max(unresolved.items(), key=lambda item: item[1][0])
        name, recovery_arg = _incomplete_step_hint(target, incomplete_step)
        return name, recovery_arg, True
    target, pending_info = _next_pending_empty(pending)
    return _pending_empty_hint(target, pending_info)


def _pending_append_targets(info):
    """Normalized referent guards from current and legacy pending records."""
    if not isinstance(info, dict):
        return ()
    targets = []
    plural = info.get("append_targets", ())
    if isinstance(plural, (list, tuple, set)):
        for target in plural:
            if _valid_nonempty_str(target) and target not in targets:
                targets.append(target)
    legacy = info.get("append_target")
    if _valid_nonempty_str(legacy) and legacy not in targets:
        targets.append(legacy)
    return tuple(targets)


def _pending_empty_recovery(pending, logical_target, operation_target, is_append):
    """Find a pending zero-byte recovery by pathname or append referent."""
    recovery = pending.get(logical_target) if logical_target is not None else None
    if not is_append:
        return recovery
    matches = []
    for key, info in pending.items():
        append_allowed = isinstance(info, dict) and info.get("append_allowed", False)
        same_operation = (
            operation_target is not None
            and isinstance(info, dict)
            and operation_target in _pending_append_targets(info)
        )
        # A pending overwrite is tied to the logical pathname and also blocks
        # physical aliases. A permissive append obligation follows only the
        # referent observed when it was created, so retargeting cannot satisfy it.
        if same_operation or (not append_allowed and key == logical_target):
            matches.append(info)
    if not matches:
        return None
    # Multiple aliases can carry different recovery obligations for one
    # referent. A pending overwrite always wins over a permissive append.
    return {
        "append_allowed": all(
            isinstance(info, dict) and info.get("append_allowed", False) for info in matches
        )
    }


def _clear_pending_empty_writes(pending, logical_target, operation_target, is_append):
    """Clear zero-byte obligations satisfied by a successful write."""
    keys = set()
    if not is_append and logical_target is not None:
        keys.add(logical_target)
    if operation_target is not None:
        for key, info in pending.items():
            if (
                isinstance(info, dict)
                and info.get("append_allowed", False)
                and operation_target in _pending_append_targets(info)
            ):
                keys.add(key)
    for key in keys:
        pending.pop(key, None)


def _write_visibility_flag(task_steps):
    """Replanner visibility for a failed write-shaped task (issue #15 / rev 4).

    Returns {"no_write_executed": True} when the task never landed a write,
    {"incomplete_write": <basename>} while any target has an unresolved
    truncated partial write, and
    {"unvalidated_write": <basename>} when it wrote but never verified after
    the last successful write — the v6 Gemma replans restated the task while
    an applied-but-unresolved artifact sat on disk — or None.
    """
    ok_mutations = [
        idx
        for idx, s in enumerate(task_steps)
        if s.get("action") in ("write", "edit") and s.get("ok")
    ]
    if not ok_mutations:
        return {"no_write_executed": True}
    unresolved = _unresolved_incomplete_writes(task_steps)
    if unresolved:
        # Incomplete state wins over unrelated later mutations and shells.
        _, last_step = max(unresolved.values(), key=lambda item: item[0])
        arg = last_step.get("arg", "")
        return {"incomplete_write": Path(arg).name if arg else True}
    last_mutation = ok_mutations[-1]
    last_step = task_steps[last_mutation]
    arg = last_step.get("arg", "")
    validated = any(
        s.get("action") == "shell" and s.get("ok") for s in task_steps[last_mutation + 1 :]
    )
    if validated:
        return None
    return {"unvalidated_write": Path(arg).name if arg else True}


def _read_continuation_hint(continuation):
    """Render an action-ready continuation for the executor prompt."""
    if isinstance(continuation, dict):
        return (
            f"cursor={continuation['cursor']}, "
            f"limit={continuation['limit']}, "
            f"sha256={continuation['sha256']}"
        )
    # Compatibility with run state created by the revision-2 line contract.
    return f"offset={continuation}"


def _should_validate(replan, history, state, user_prompt, final_validate=None):
    """Compatibility mapping boundary for the shared validation decision."""
    return _progress_should_validate(
        replan, history, RunProgress(state), user_prompt, final_validate=final_validate
    )


def _progress_should_validate(
    replan, history, progress: RunProgress, user_prompt, final_validate=None
):
    """Decide whether to run final validation. Returns True if validation should run.

    ``final_validate`` is the run's resolved mode (issue #68). Direct
    callers default to auto; the CLI facade resolves environment defaults."""
    mode = "auto" if final_validate is None else final_validate
    if mode == "0":
        return False
    if mode == "always":
        return True
    # auto mode: trigger on complexity/risk signals
    if replan > 0:
        return True
    # Any failed steps in history
    if any(e.get("event") == "step" and not e.get("result", {}).get("ok", True) for e in history):
        return True
    completed = progress.optional_completed_tasks
    if len(completed) >= 3:
        return True
    # Count total steps
    total_steps = sum(1 for e in history if e.get("event") == "step")
    if total_steps >= 5:
        return True
    if _VALIDATE_KEYWORDS.search(user_prompt):
        return True
    return False


def _has_new_validation_evidence(state):
    start = state.get("validated_step_count", 0)
    return any(
        s.get("action") in ("write", "edit", "shell") and s.get("ok")
        for s in state.get("all_steps", [])[start:]
    )


class _PolicyRecorder(Protocol):
    """Only the normal recorder's corrective-note and skipped-step surfaces."""

    def skip(
        self, task_index: int, step: int, act: str, action: Mapping[str, Any], reason: str
    ) -> None: ...

    def note(self, entry: dict[str, Any]) -> None: ...


class _RewritePressure(Protocol):
    """Run-wide rewrite pressure, independent of clocks, logging and run setup."""

    @property
    def consecutive_target_writes(self) -> int: ...

    def validate_pressure_target(self) -> str | None: ...

    def rewrite_skip_armed(self, target: str | None) -> bool: ...

    def disarm_rewrite_damping(self) -> None: ...

    def note_successful_full_write(self, target: str | None) -> None: ...

    def break_rewrite_streak(self) -> None: ...


@dataclass(frozen=True)
class _PolicyServices:
    """Live data/recording access shared by step and write-obligation policies.

    Getters do not capture mappings or bound recorder methods. A callback may
    replace a compatibility collaborator before the next operation uses it.
    """

    progress: Callable[[], RunProgress]
    working_dir: Callable[[], str]
    recorder: Callable[[], _PolicyRecorder]
    emit: Callable[[str], None]


@dataclass(frozen=True)
class StepPolicyContext(_PolicyServices):
    """Explicit collaborators for the selectable step-policy algorithms."""

    max_steps: Callable[[], int]
    observe_tail_reserve: Callable[[], int]
    rewrite: Callable[[], _RewritePressure]
    shell_timeout: Callable[[str], int]
    timeout_bounds: Callable[[], tuple[int, int]]


@dataclass(frozen=True)
class WriteObligationContext(_PolicyServices):
    """Shared incomplete-write services; no dispatch or terminal authority."""

    disarm_rewrite: Callable[[], None]


_StepPolicyType = TypeVar("_StepPolicyType", bound="StepPolicy")


class StepPolicy:
    """Pluggable step/completion-pressure policy for one run (issue #31).

    The policy owns how the controller pressures the model toward progress
    and when it may accept ``done`` beyond the shared invariants: observation
    discipline, rewrite/verification discipline, and the executor prompt
    pressure signals. Everything else stays controller-owned and identical
    across arms — duplicate/stuck loop protection, incomplete-write
    obligations and the completion blocker, recording and counters, typed
    errors, and validation. Policies are constructed once per run and may
    keep run-scoped state.
    """

    name = "base"

    def __init__(self, controller, *, shell_timeout=None, timeout_bounds=None):
        """Legacy constructor; controller-free users compose ``from_context``."""
        self.controller = controller
        self._shell_timeout = _get_shell_timeout if shell_timeout is None else shell_timeout
        self._timeout_bounds = (
            (lambda: (SHELL_TIMEOUT_LONG, SHELL_TIMEOUT_MAX))
            if timeout_bounds is None
            else timeout_bounds
        )
        self._bind_context(self._legacy_context(lambda: self.controller))
        self._uses_legacy_owner = True

    def _legacy_context(self, owner) -> StepPolicyContext:
        """One adapter for live owner lookup or a method-local captured owner."""
        return StepPolicyContext(
            progress=lambda: RunProgress(owner().state),
            working_dir=lambda: owner().working_dir,
            recorder=lambda: owner().recorder,
            emit=lambda message: owner()._emit(message),
            max_steps=lambda: owner().max_steps,
            observe_tail_reserve=lambda: owner().guards.observe_tail_reserve,
            rewrite=lambda: owner().run_state,
            shell_timeout=lambda command: self._shell_timeout(command),
            timeout_bounds=lambda: self._timeout_bounds(),
        )

    @classmethod
    def from_context(cls: type[_StepPolicyType], context: StepPolicyContext) -> _StepPolicyType:
        policy = cls.__new__(cls)
        policy._bind_context(context)
        return policy

    def _bind_context(self, context: StepPolicyContext):
        self._context = context
        self._uses_legacy_owner = False

    def _capture_context(self) -> StepPolicyContext:
        """Match only legacy methods that selected their controller at entry."""
        if not self._uses_legacy_owner:
            return self._context
        owner = self.controller
        return self._legacy_context(lambda: owner)

    def write_pressure(self, attempt):
        """True when the executor prompt must demand a committing action."""
        return False

    def validate_pressure(self, attempt):
        """Basename the executor prompt must steer to verifying, or None."""
        return None

    def guard_done(self, ctx, attempt):
        """Extra policy conditions on ``done`` after the shared blocker."""
        return None

    def allows_deterministic_completion(self):
        """May a deterministic-repair receipt auto-complete a matching task?

        Automatic completion paths must pass the same policy discipline as a
        model ``done``; arms with an open verification obligation refuse."""
        return True

    def guard_action(self, ctx, attempt):
        """Pre-dispatch discipline for a non-control action; None dispatches."""
        return None

    def guard_duplicate(self, ctx, attempt):
        """Shared per-action-type loop protection (issue #68): duplicates and
        stuck repeats are suppressed or reported, never converted into
        completion. One implementation serves every arm; an arm may override
        only to tighten it."""
        context = self._capture_context()
        action, act = ctx.action, ctx.act
        last = context.progress().last_steps[-1:] if context.progress().last_steps else []
        if not last or last[0]["action"] != act:
            return None
        prev = last[0]
        same_mutation_target = False
        if act in ("write", "edit"):
            current_target_step = {
                "arg": action.get("arg", ""),
            }
            if act == "write" and action.get("append"):
                current_target_step["append"] = True
            current_target = _mutation_target_key(current_target_step, context.working_dir())
            same_mutation_target = (
                current_target is not None
                and _mutation_target_key(prev, context.working_dir()) == current_target
            )
        if act in ("write", "edit") and same_mutation_target:
            # write: same content = duplicate; edit: same find+replace = duplicate
            is_dup = False
            if act == "write" and action.get("append"):
                # Chunked append is never a no-op — an identical consecutive
                # chunk is a stuck loop, not a duplicate.
                if prev.get("_append") and prev.get("_content", "") == action.get("content", ""):
                    context.emit(
                        f"  [{ctx.step + 1}] auto-fail (same chunk appended twice to {action.get('arg', '')[:40]})"
                    )
                    context.progress().errors.append(
                        f"[stuck_loop] write {action.get('arg', '')[:60]}: same chunk appended twice"
                    )
                    context.recorder().skip(ctx.task_index, ctx.step, act, action, "stuck_append")
                    return _StepFlow.END_ATTEMPT
            elif (
                act == "write"
                and not ctx.truncated_write
                and prev.get("ok")
                and not prev.get("_truncated_write")
                and not prev.get("_append")
                and prev.get("_content", "") == action.get("content", "")
            ):
                is_dup = True
            elif (
                act == "edit"
                and prev.get("ok")
                and prev.get("_find", "") == action.get("find", "")
                and prev.get("_replace", "") == action.get("replace", "")
            ):
                is_dup = True
            # Consecutive identical failed edit → stuck; bail to replan
            elif (
                act == "edit"
                and not prev.get("ok")
                and prev.get("_find", "") == action.get("find", "")
            ):
                context.emit(
                    f"  [{ctx.step + 1}] auto-fail (same edit failed twice on {action.get('arg', '')[:40]})"
                )
                context.progress().errors.append(
                    f"[stuck_loop] edit {action.get('arg', '')[:60]}: same find string failed twice"
                )
                context.recorder().skip(ctx.task_index, ctx.step, act, action, "stuck_edit")
                return _StepFlow.END_ATTEMPT
            if is_dup:
                attempt.dup_skip_count += 1
                context.emit(f"  [{ctx.step + 1}] skip (duplicate {act}, same content)")
                context.recorder().skip(ctx.task_index, ctx.step, act, action, f"duplicate_{act}")
                if prev.get("_truncated_write"):
                    dup_msg = (
                        "File is incomplete — the earlier write was truncated. "
                        "Continue with append:true for the rest."
                    )
                else:
                    dup_msg = "Already done — file unchanged. Move to next action or emit done."
                entry = {
                    "action": act,
                    "arg": action.get("arg", ""),
                    "ok": True,
                    "output": dup_msg,
                }
                # Preserve match metadata so guard still detects duplicates on subsequent turns
                if act == "write":
                    entry["_content"] = action.get("content", "")
                    if prev.get("_truncated_write"):
                        entry["_truncated_write"] = True
                elif act == "edit":
                    entry["_find"] = action.get("find", "")
                    entry["_replace"] = action.get("replace", "")
                context.recorder().note(entry)
                # Defer thinking escalation: first duplicate skip gets a
                # corrective observation only; escalate on 2+ consecutive skips.
                # Saves ~10s of thinking time on harmless first-time duplicates.
                if attempt.dup_skip_count >= 2:
                    attempt.use_think = True
                    attempt.reasoning_trigger = "duplicate_action"
                return _StepFlow.NEXT_STEP
        elif act == "shell" and prev.get("arg", "") == action.get("arg", ""):
            # The sliding window carries prior-task context, not proof
            # that this attempt executed its check (issue #95). Give
            # each new task/attempt a fresh execution after either success
            # or failure; retain same-attempt guards and timeout bumps,
            # including deterministic retries.
            if not any(
                step.get("action") == "shell" and step.get("arg", "") == action.get("arg", "")
                for step in attempt.steps
            ):
                return None
            if prev.get("ok"):
                # Repetition is never completion evidence (issue #68): the
                # duplicate is suppressed as a no-op once, and repeating it
                # again is a stuck loop for the replanner — task acceptance
                # still requires an explicit done.
                attempt.dup_skip_count += 1
                if attempt.dup_skip_count >= 2:
                    context.emit(
                        f"  [{ctx.step + 1}] auto-fail (same successful shell repeated on {action.get('arg', '')[:40]})"
                    )
                    context.progress().errors.append(
                        f"[stuck_loop] shell {action.get('arg', '')[:60]}: same successful command repeated"
                    )
                    context.recorder().skip(
                        ctx.task_index, ctx.step, act, action, "stuck_shell_repeat"
                    )
                    return _StepFlow.END_ATTEMPT
                context.emit(f"  [{ctx.step + 1}] skip (duplicate successful shell)")
                context.recorder().skip(ctx.task_index, ctx.step, act, action, "duplicate_shell")
                context.recorder().note(
                    {
                        "action": act,
                        "arg": action.get("arg", ""),
                        "ok": True,
                        "output": (
                            "Already ran successfully — use the earlier output. "
                            "Take the next action, or emit done/fail."
                        ),
                    }
                )
                return _StepFlow.NEXT_STEP
            elif prev.get("error_type") == "timeout":
                # Bump timeout for retry: read actual timeout from previous step,
                # not from fresh action (which won't have prior bumps)
                prev_timeout = prev.get("_timeout", context.shell_timeout(action.get("arg", "")))
                timeout_long, timeout_max = context.timeout_bounds()
                bumped = max(timeout_long, prev_timeout * 2)
                action = action.with_updates(timeout=min(bumped, timeout_max))
                ctx.action = action
                context.emit(f"  [{ctx.step + 1}] retrying after timeout ({action['timeout']}s)")
            else:
                context.emit(f"  [{ctx.step + 1}] auto-fail (same shell failed twice)")
                context.progress().errors.append(
                    f"Stuck: {act} {action.get('arg', '')[:60]} failed twice"
                )
                context.recorder().skip(ctx.task_index, ctx.step, act, action, "stuck_shell")
                return _StepFlow.END_ATTEMPT
        elif act == "read" and prev.get("arg", "") == action.get("arg", ""):
            # Range-aware: new line windows and exact cursor continuations
            # are legitimate navigation.
            prev_key = prev.get("_read_key") or _read_key(prev)
            cur_key = _read_key(action)
            if prev_key != cur_key:
                pass  # different range — execute normally
            elif prev.get("ok"):
                attempt.dup_skip_count += 1
                if attempt.dup_skip_count >= 2:
                    context.emit(
                        f"  [{ctx.step + 1}] auto-fail (same read repeated on {action.get('arg', '')[:40]})"
                    )
                    context.progress().errors.append(
                        f"[stuck_loop] read {action.get('arg', '')[:60]}: same file read repeatedly"
                    )
                    context.recorder().skip(ctx.task_index, ctx.step, act, action, "stuck_read")
                    return _StepFlow.END_ATTEMPT
                context.emit(f"  [{ctx.step + 1}] skip (duplicate read)")
                context.recorder().skip(ctx.task_index, ctx.step, act, action, "duplicate_read")
                cont = prev.get("_continuation")
                if cont:
                    obs = (
                        "Already read this range. Continue with "
                        f"{_read_continuation_hint(cont)}; "
                        f"or search, edit, done, or fail."
                    )
                else:
                    obs = "Already read. Use previous content; edit, write, shell, done, or fail."
                entry = {
                    "action": "read",
                    "arg": action.get("arg", ""),
                    "ok": True,
                    "output": obs,
                    "_read_key": cur_key,
                }
                if cont:
                    entry["_continuation"] = cont
                context.recorder().note(entry)
                return _StepFlow.NEXT_STEP
            else:
                context.emit(f"  [{ctx.step + 1}] auto-fail (same read failed twice)")
                context.progress().errors.append(
                    f"[stuck_loop] read {action.get('arg', '')[:60]} failed twice"
                )
                context.recorder().skip(ctx.task_index, ctx.step, act, action, "stuck_read_failed")
                return _StepFlow.END_ATTEMPT
        return None

    def note_result(self, ctx, attempt, result):
        """Observe one dispatched action's result for policy state."""

    def note_deterministic_repair(self, target):
        """Observe a dispatched #41 repair mutation."""

    def note_deterministic_retry(self, result):
        """Observe the scaffold shell retry after a repair."""


class HeuristicStepPolicy(StepPolicy):
    """Today's guard/counter baseline (issues #15/#31, revisions 3-4).

    Keyword write-shaping plus counters: write pressure after observation
    spending, an observation tail reserve, and same-target rewrite damping
    with validate pressure. Behavior-preserving extraction of the former
    controller-inline policy; every log line, skip reason, counter, and
    threshold is unchanged.
    """

    name = "heuristic"

    def write_pressure(self, attempt):
        return attempt.write_pressure()

    def validate_pressure(self, attempt):
        return self._context.rewrite().validate_pressure_target()

    def guard_action(self, ctx, attempt):
        flow = self._observe_tail_guard(ctx, attempt)
        if flow is None:
            flow = self._rewrite_loop_guard(ctx, attempt)
        return flow

    def _observe_tail_guard(self, ctx, attempt):
        """Reserve the final steps of a write-shaped task for commitment."""
        context = self._capture_context()
        # Write-forcing tail reserve (issue #15): on a write-shaped task the
        # final steps are reserved for committing actions.
        if not (
            ctx.act in OBSERVE_ACTIONS
            and attempt.wants_write
            and attempt.commit_executed == 0
            and context.max_steps() - ctx.step <= context.observe_tail_reserve()
        ):
            return None
        attempt.observe_blocked += 1
        if attempt.observe_blocked >= 2:
            context.emit(
                f"  [{ctx.step + 1}] auto-fail (observation steps exhausted without a write)"
            )
            context.progress().errors.append(
                f"[stuck_loop] {ctx.act} {ctx.action.get('arg', '')[:60]}: observation steps exhausted without a write"
            )
            context.recorder().skip(
                ctx.task_index, ctx.step, ctx.act, ctx.action, "observe_tail_exhausted"
            )
            return _StepFlow.END_ATTEMPT
        context.emit(
            f"  [{ctx.step + 1}] skip ({ctx.act} blocked: remaining steps reserved for write)"
        )
        context.recorder().skip(
            ctx.task_index, ctx.step, ctx.act, ctx.action, "observe_tail_reserved"
        )
        context.recorder().note(
            {
                "action": ctx.act,
                "arg": ctx.action.get("arg", ""),
                "ok": True,
                "output": "Observation budget exhausted. Next action MUST be write, edit, or shell — or fail with reason.",
            }
        )
        return _StepFlow.NEXT_STEP

    def _rewrite_loop_guard(self, ctx, attempt):
        """Skip a same-target full rewrite once the streak is armed."""
        context = self._capture_context()
        # Rewrite damping (revision 4): after rewrite_skip_writes successful
        # full writes of the same target with no intervening successful
        # shell/edit, further full rewrites are skipped — verify, edit, or
        # finish instead.
        if not (
            ctx.act == "write"
            and not ctx.action.get("append")
            and not ctx.truncated_write
            and context.rewrite().rewrite_skip_armed(ctx.logical_write_target)
        ):
            return None
        attempt.dup_skip_count += 1
        context.emit(
            f"  [{ctx.step + 1}] skip (rewrite loop: "
            f"{ctx.action.get('arg', '')[:40]} already written "
            f"{context.rewrite().consecutive_target_writes}x)"
        )
        context.recorder().skip(ctx.task_index, ctx.step, ctx.act, ctx.action, "rewrite_loop")
        context.recorder().note(
            {
                "action": ctx.act,
                "arg": ctx.action.get("arg", ""),
                "ok": True,
                "output": (
                    f"Already written {context.rewrite().consecutive_target_writes} times. "
                    "Do NOT write it again — verify with shell, make a "
                    "targeted edit, or emit done."
                ),
            }
        )
        return _StepFlow.NEXT_STEP

    def note_result(self, ctx, attempt, result):
        run_state = self._context.rewrite()
        if ctx.act == "write" and result.ok:
            if ctx.truncated_write:
                # A partial (truncated) write is not a completed rewrite
                # (Codex P1, PR #21): the file is incomplete, and the
                # recovery path may legitimately append to it or restart the
                # write. Reset for truncated append chunks too; otherwise an
                # armed streak can block the clean restart.
                run_state.disarm_rewrite_damping()
            elif not ctx.action.get("append"):
                run_state.note_successful_full_write(ctx.logical_write_target)
        elif ctx.act in ("shell", "edit") and result.ok:
            # Verification or a targeted fix breaks the rewrite streak;
            # observations do not (the v6 Gemma loop interleaved tree/read
            # between rewrites).
            run_state.break_rewrite_streak()

    def note_deterministic_repair(self, target):
        # The deterministic source fix is a successful targeted repair, so
        # it breaks an armed rewrite streak just like a model-selected edit.
        self._context.rewrite().break_rewrite_streak()


class LifecycleStepPolicy(StepPolicy):
    """Explicit inspect → modify → verify → finish arm (issue #31).

    Replaces the tail reserve and the rewrite counters with two phase
    invariants, run-scoped so a task-local replan cannot silently move the
    lifecycle backward:

    - a successful mutation marks its target ``needs_verification``; only a
      later successful shell check clears it (a failed check does not);
    - while a target needs verification, a same-target full rewrite is
      steered to verification, and ``done`` is refused with a corrective
      note — repetition or observation never moves the lifecycle forward.

    The keyword task classification is retained only for the observation
    write-pressure nudge; incomplete-write obligations, duplicate/stuck
    protection, and the completion blocker are shared invariants. Any
    successful shell counts as verification evidence — the same evidence
    definition as the heuristic arm's ``unvalidated_write`` — so a mutating
    shell (for example ``sed -i``) is not itself tracked as a mutation;
    classifying shell intent is deliberately out of scope for this arm and
    the final validator remains the independent check. This arm is an
    alternative to measure (#63/#64), not an assumed improvement.
    """

    name = "lifecycle"

    def _bind_context(self, context: StepPolicyContext):
        super()._bind_context(context)
        self.needs_verification = False
        self.unverified_target = None

    def write_pressure(self, attempt):
        return attempt.write_pressure()

    def _target_has_open_obligation(self, target):
        """True while incomplete-write recovery legitimately rewrites it."""
        progress = self._context.progress()
        if target in progress.optional_pending_empty_writes:
            return True
        return target in _unresolved_incomplete_writes(
            progress.optional_all_steps, self._context.working_dir()
        )

    def guard_done(self, ctx, attempt):
        if not self.needs_verification:
            return None
        context = self._capture_context()
        name = Path(str(self.unverified_target or "file")).name
        context.emit(f"  [{ctx.step + 1}] skip (done before verifying {name})")
        context.recorder().skip(
            ctx.task_index, ctx.step, ctx.act, ctx.action, "lifecycle_unverified_done"
        )
        context.recorder().note(
            {
                "action": "done",
                "arg": "",
                "ok": True,
                "output": (
                    f"Cannot finish: {name} was modified but never verified. "
                    "Run a shell command that checks it, or fail with a reason."
                ),
            }
        )
        return _StepFlow.NEXT_STEP

    def guard_action(self, ctx, attempt):
        if not (
            ctx.act == "write"
            and not ctx.action.get("append")
            and not ctx.truncated_write
            and self.needs_verification
            and ctx.logical_write_target == self.unverified_target
            and not self._target_has_open_obligation(ctx.logical_write_target)
        ):
            return None
        context = self._capture_context()
        name = Path(str(self.unverified_target)).name
        attempt.dup_skip_count += 1
        context.emit(f"  [{ctx.step + 1}] skip (rewrite of unverified {name})")
        context.recorder().skip(
            ctx.task_index, ctx.step, ctx.act, ctx.action, "lifecycle_verify_before_rewrite"
        )
        context.recorder().note(
            {
                "action": ctx.act,
                "arg": ctx.action.get("arg", ""),
                "ok": True,
                "output": (
                    f"{name} is already written but unverified. Do NOT rewrite "
                    "it — verify it with a shell check or make a targeted edit."
                ),
            }
        )
        return _StepFlow.NEXT_STEP

    def note_result(self, ctx, attempt, result):
        if not result.ok:
            # Failures never move the lifecycle: a failed check does not
            # verify, and a failed mutation creates nothing to verify.
            return
        if ctx.act in ("write", "edit"):
            self.needs_verification = True
            if ctx.act == "write":
                self.unverified_target = ctx.logical_write_target
            else:
                self.unverified_target = _mutation_target_key(
                    {"arg": ctx.action.get("arg", "")}, self._context.working_dir()
                )
        elif ctx.act == "shell":
            self.needs_verification = False
            self.unverified_target = None

    def note_deterministic_repair(self, target):
        self.needs_verification = True
        self.unverified_target = target

    def note_deterministic_retry(self, result):
        if result.ok:
            self.needs_verification = False
            self.unverified_target = None

    def allows_deterministic_completion(self):
        # A repaired-but-unverified target gates the auto-done exactly like
        # a model done: verify first.
        return not self.needs_verification


_STEP_POLICY_ARMS = {
    HeuristicStepPolicy.name: HeuristicStepPolicy,
    LifecycleStepPolicy.name: LifecycleStepPolicy,
}


class WriteObligations:
    """Typed owner of incomplete-write obligations for one run (issue #69).

    Truncation classification, zero-byte obligation records, append-safety
    and recovery-order decisions, the completion refusal, resume anchors,
    and obligation clearing all live here — a shared invariant every policy
    arm runs under. The ``pending_empty_writes`` dictionary stays projected
    into run state because the structured result and replanner visibility
    read it there; that projection is the documented boundary.
    """

    def __init__(self, controller):
        self.controller = controller
        self._bind_context(self._legacy_context(lambda: self.controller))
        self._uses_legacy_owner = True

    def _legacy_context(self, owner) -> WriteObligationContext:
        return WriteObligationContext(
            progress=lambda: RunProgress(owner().state),
            working_dir=lambda: owner().working_dir,
            recorder=lambda: owner().recorder,
            emit=lambda message: owner()._emit(message),
            disarm_rewrite=lambda: owner().run_state.disarm_rewrite_damping(),
        )

    @classmethod
    def from_context(cls, context: WriteObligationContext) -> "WriteObligations":
        obligations = cls.__new__(cls)
        obligations._bind_context(context)
        return obligations

    def _bind_context(self, context: WriteObligationContext):
        self._context = context
        self._uses_legacy_owner = False

    def _capture_context(self) -> WriteObligationContext:
        if not self._uses_legacy_owner:
            return self._context
        owner = self.controller
        return self._legacy_context(lambda: owner)

    def completion_blocker(self):
        """The most actionable open obligation, or None (see
        :func:`_completion_blocker`)."""
        return _progress_completion_blocker(self._context.progress(), self._context.working_dir())

    def refuse_done(self, ctx):
        """Skip a ``done`` claim while any obligation is unresolved."""
        context = self._capture_context()
        blocker = self.completion_blocker()
        if blocker is None:
            return None
        incomplete_name, recovery_arg, append_allowed = blocker
        if append_allowed:
            recovery = (
                "Retry that exact target with append:true if it "
                "still identifies the intended file, or restart "
                "it with a complete append:false write."
            )
        else:
            recovery = (
                "Resend a shorter write to that exact target with "
                "append:false before using append:true."
            )
        context.emit(f"  [{ctx.step + 1}] skip (done with incomplete write: {incomplete_name})")
        context.recorder().skip(
            ctx.task_index, ctx.step, ctx.act, ctx.action, "incomplete_write_done"
        )
        context.recorder().note(
            {
                "action": "done",
                "arg": "",
                "ok": True,
                "output": (
                    f"Cannot finish: {incomplete_name} is incomplete at {recovery_arg}. {recovery}"
                ),
            }
        )
        return _StepFlow.NEXT_STEP

    def prepare(self, ctx):
        """Classify write truncation and enforce zero-byte recovery order."""
        context = self._capture_context()
        action, act = ctx.action, ctx.act
        # Sentinel transport truncation (issue #15): keep the complete lines
        # that arrived and steer the model to finish the file with chunked
        # append instead of failing the step.
        ctx.truncated_write = act == "write" and ctx.transport.content_truncated
        ctx.logical_write_target = (
            _mutation_target_key({"arg": action.get("arg", "")}, context.working_dir())
            if act == "write"
            else None
        )
        ctx.operation_write_target = (
            _mutation_target_key(
                {
                    "arg": action.get("arg", ""),
                    "append": bool(action.get("append")),
                },
                context.working_dir(),
            )
            if act == "write"
            else None
        )
        pending_recovery = _pending_empty_recovery(
            context.progress().pending_empty_writes,
            ctx.logical_write_target,
            ctx.operation_write_target,
            bool(action.get("append")),
        )
        if (
            act == "write"
            and action.get("append")
            and pending_recovery
            and not pending_recovery.get("append_allowed", False)
        ):
            context.emit(f"  [{ctx.step + 1}] skip (append before first replacement chunk landed)")
            context.recorder().skip(
                ctx.task_index, ctx.step, act, action, "append_after_empty_overwrite"
            )
            context.recorder().note(
                {
                    "action": act,
                    "arg": action.get("arg", ""),
                    "ok": True,
                    "output": (
                        "The replacement's first chunk wrote no bytes. "
                        "Resend a shorter write with append:false before "
                        "using append:true."
                    ),
                }
            )
            return _StepFlow.NEXT_STEP
        if ctx.truncated_write:
            kept = action.get("content", "")
            kept = kept[: kept.rfind("\n") + 1]
            if not kept:
                context.emit(f"  [{ctx.step + 1}] skip (write truncated before a complete line)")
                context.recorder().skip(
                    ctx.task_index, ctx.step, act, action, "truncated_write_empty"
                )
                # The recovery instruction asks for a clean resend; disarm
                # rewrite damping before that resend even though this empty
                # partial attempt wrote no bytes.
                context.disarm_rewrite()
                # Empty append attempts are obligations on the referent
                # observed at dispatch time. Key them by that operation
                # target so retargeting a leaf symlink cannot overwrite an
                # older obligation.
                pending_target = (
                    ctx.operation_write_target if action.get("append") else ctx.logical_write_target
                )
                recovery_arg = action.get("arg", "") or "file"
                if pending_target is not None:
                    existing = context.progress().pending_empty_writes.get(pending_target)
                    append_allowed = bool(action.get("append"))
                    if isinstance(existing, dict):
                        append_allowed = existing.get("append_allowed", False) and append_allowed
                    append_target = _mutation_target_key(
                        {
                            "arg": action.get("arg", ""),
                            "append": True,
                        },
                        context.working_dir(),
                    )
                    append_targets = list(_pending_append_targets(existing))
                    if append_target is not None and append_target not in append_targets:
                        append_targets.append(append_target)
                    recovery_arg = _target_recovery_arg(pending_target, context.working_dir())
                    context.progress().set_pending_write(
                        pending_target,
                        PendingWrite(
                            name=Path(action.get("arg", "") or "file").name,
                            append_allowed=append_allowed,
                            append_targets=tuple(append_targets),
                            recovery_arg=recovery_arg,
                        ),
                    )
                # Nothing was written: the first dispatched chunk must stay a
                # non-append write (append would land on a stale existing
                # file), only later chunks may append.
                if action.get("append"):
                    obs = (
                        "Append truncated before a complete line. "
                        "Resend a smaller append:true chunk at the "
                        f"exact target {recovery_arg}."
                    )
                else:
                    obs = (
                        "Write truncated before a complete line. Resend the "
                        f"write (no append) to the exact target {recovery_arg} "
                        "with a shorter first chunk, then continue with "
                        "append:true chunks."
                    )
                context.recorder().note(
                    {
                        "action": act,
                        "arg": action.get("arg", ""),
                        "ok": True,
                        "output": obs,
                    }
                )
                return _StepFlow.NEXT_STEP
            action = action.with_updates(content=kept)
            ctx.action = action
        return None

    def note_successful_write(self, ctx, action):
        """A complete write clears the obligations it satisfies."""
        _clear_pending_empty_writes(
            self._context.progress().pending_empty_writes,
            ctx.logical_write_target,
            ctx.operation_write_target,
            bool(action.get("append")),
        )

    def append_resume_anchor(self, ctx, action, result):
        """Suffix a truncated write's output with its exact resume point.

        The executor is stateless per step: without a resume anchor the
        model cannot know where the write stopped. The kept content is
        exactly what :meth:`prepare` trimmed to the last complete line."""
        kept = action.get("content", "")
        anchor = kept.splitlines()[-1][-80:]
        recovery_arg = _target_recovery_arg(ctx.operation_write_target, self._context.working_dir())
        result.output += (
            f" (truncated after {kept.count(chr(10))} lines; "
            f"last written line: {anchor!r}; continue with "
            f"append:true at {recovery_arg} starting after "
            "that line)"
        )


class ValidationState:
    """Typed owner of run-scoped validation state (issue #69).

    Projects through the structured state keys the result schema already
    carries (``validated_once``, ``validation_attempts``,
    ``validation_recheck_needed``, ``validated_step_count``); that
    dictionary projection is the documented boundary.
    """

    def __init__(self, data):
        self.data = data

    @property
    def attempts(self):
        return self.data.get("validation_attempts", 0)

    @property
    def recheck_needed(self):
        return bool(self.data.get("validation_recheck_needed"))

    def note_attempt(self):
        self.data["validated_once"] = True
        self.data["validation_attempts"] = self.attempts + 1

    def mark_failed(self):
        """An explicit rejection blocks completion until new evidence."""
        self.data["validation_recheck_needed"] = True
        self.data["validated_step_count"] = len(self.data.get("all_steps", []))

    def clear_failure(self):
        self.data["validation_recheck_needed"] = False

    def has_new_evidence(self):
        """New successful mutation or shell evidence since the failure."""
        return _has_new_validation_evidence(self.data)


class CompletionVerdict(Protocol):
    """Structural validation result; policies do not depend on the provider module."""

    @property
    def valid(self) -> bool: ...

    @property
    def reason(self) -> str: ...

    @property
    def missing(self) -> tuple[str, ...]: ...

    @property
    def deterministic(self) -> bool: ...


class CompletionDecision(Protocol):
    """Validation-mode decision, including the compatibility keyword argument."""

    def __call__(
        self,
        replan: int,
        history: list[dict[str, Any]],
        state: dict[str, Any],
        user_prompt: str,
        final_validate: str | None = None,
    ) -> bool: ...


class _CompletionInputs(Protocol):
    """Fields read by the policy; implementations may resolve them lazily."""

    @property
    def state(self) -> dict[str, Any]: ...

    @property
    def history(self) -> list[dict[str, Any]]: ...

    @property
    def user_prompt(self) -> str: ...

    @property
    def working_dir(self) -> str: ...

    @property
    def final_validate(self) -> str | None: ...

    @property
    def max_replans(self) -> int: ...


@dataclass(frozen=True)
class CompletionView:
    """Current completion inputs, retaining the run's state and history by identity.

    This is a view, not a new state owner or serialized copy. Supplying a fresh
    view at use time preserves changes made through the compatibility facade.
    """

    state: dict[str, Any]
    history: list[dict[str, Any]]
    user_prompt: str
    working_dir: str
    final_validate: str | None
    max_replans: int


@dataclass(frozen=True)
class CompletionContext:
    """Narrow terminal-policy services, separate from transport and controller setup.

    ``validate`` owns request configuration and returns a verdict or no verdict;
    the policy owns when to call it and how that result affects completion.
    Callbacks remain live, including views and sinks. They must not copy state
    or expose held-out acceptance evidence to the policy.
    """

    current_view: Callable[[], _CompletionInputs]
    validate: Callable[[], CompletionVerdict | None]
    decide_validation: CompletionDecision
    elapsed: Callable[[], float]
    emit: Callable[[str], None]
    event: Callable[[dict[str, Any]], None]


class _LegacyCompletionView:
    """Resolve only fields actually used by the legacy terminal path.

    Minimal duck-typed controllers can omit unrelated services and fields:
    exhaustion needs no prompt or validation mode, while ``try_finish`` needs
    no replan limit. Explicit properties preserve both that behavior and the
    original order of reads around callbacks; no fallback values are invented.
    """

    def __init__(self, controller):
        self._controller = controller

    @property
    def state(self) -> dict[str, Any]:
        return self._controller.state

    @property
    def history(self) -> list[dict[str, Any]]:
        return self._controller.history

    @property
    def user_prompt(self) -> str:
        return self._controller.user_prompt

    @property
    def working_dir(self) -> str:
        return self._controller.working_dir

    @property
    def final_validate(self) -> str | None:
        return self._controller.final_validate

    @property
    def max_replans(self) -> int:
        return self._controller.max_replans


class CompletionPolicy:
    """Terminal policy for one run (issue #69): validation gating, verdict
    handling, the evidence-gated recheck, and the typed terminal outcome
    for both completion and exhaustion. The controller sequences planning
    and attempts; this class decides how a run may end. Verdict semantics
    follow issue #68: no verdict is never a pass, an explicit rejection
    cannot be erased without new evidence, and exhaustion never reconciles
    to success."""

    def __init__(self, controller, *, validator, decide_validation=None):
        """Compatibility adapter; new composition can use ``from_context``."""
        self.controller = controller
        self._validator = validator
        self._decide_validation = (
            _should_validate if decide_validation is None else decide_validation
        )
        self._bind_context(
            CompletionContext(
                current_view=self._legacy_view,
                validate=self._legacy_validate,
                decide_validation=lambda *args, **kwargs: self._decide_validation(*args, **kwargs),
                elapsed=lambda: self.controller.run_state.elapsed(),
                emit=lambda message: self.controller._emit(message),
                event=lambda event: self.controller._event(event),
            ),
            validation_data=controller.state,
        )

    @classmethod
    def from_context(cls, context: CompletionContext) -> "CompletionPolicy":
        """Compose the canonical policy without a controller or provider client."""
        policy = cls.__new__(cls)
        policy._bind_context(context)
        return policy

    def _bind_context(
        self, context: CompletionContext, *, validation_data: dict[str, Any] | None = None
    ):
        """Share the one implementation with facade constructors.

        ValidationState historically binds the original dictionary even when a
        compatibility caller later replaces its controller's state attribute.
        An explicit initial dictionary preserves that binding without reading
        unused view fields during construction.
        """
        self._context = context
        self.validation = ValidationState(
            context.current_view().state if validation_data is None else validation_data
        )

    def _legacy_view(self) -> _LegacyCompletionView:
        return _LegacyCompletionView(self.controller)

    def _legacy_validate(self):
        """Translate the old controller-shaped API only at the compatibility edge."""
        controller = self.controller
        validate_kwargs = controller._llm_kwargs()
        if controller._log_sink is not None:
            validate_kwargs["log_sink"] = controller._log_sink
        return self._validator(
            controller.user_prompt,
            controller.state,
            controller.working_dir,
            max_tokens=controller._budgets["final_validation_max_tokens"],
            timeout=controller._timeouts["final_validation"],
            max_retries=controller._retries["final_validation"],
            **validate_kwargs,
        )

    def try_finish(self, replan):
        """Validate an all-done pass; a :class:`RunOutcome` ends the run."""
        context = self._context
        view = context.current_view()
        status, validation = "complete", "skipped"
        wants_validation = context.decide_validation(
            replan,
            view.history,
            view.state,
            view.user_prompt,
            final_validate=view.final_validate,
        )
        first_validation = self.validation.attempts == 0
        recheck_validation = (
            self.validation.recheck_needed
            and self.validation.attempts < 2
            and self.validation.has_new_evidence()
        )
        if wants_validation and (first_validation or recheck_validation):
            self.validation.note_attempt()
            vresult = context.validate()
            if vresult is not None and vresult.valid is False:
                reason = vresult.reason or "validation failed"
                missing = list(vresult.missing)
                error_msg = f"[validation_failed] {reason}"
                if missing:
                    error_msg += f" missing: {', '.join(missing)}"
                RunProgress(context.current_view().state).errors.append(error_msg)
                self.validation.mark_failed()
                context.emit(f"  Validation failed: {reason}")
                context.event(
                    {
                        "event": "validation",
                        "valid": False,
                        "reason": reason,
                        "missing": missing,
                        "deterministic": vresult.deterministic,
                    }
                )
                return None  # replan
            elif vresult is None and recheck_validation:
                # Once validation explicitly failed, an unavailable second
                # verdict cannot erase that known failure.
                context.emit("  Validation recheck produced no verdict; failure remains pending.")
                context.event(
                    {
                        "event": "validation",
                        "valid": None,
                        "reason": "recheck produced no verdict",
                        "deterministic": False,
                    }
                )
            elif vresult is None:
                # An unavailable or malformed first verdict is not a pass
                # (issue #68): the completed tasks stand, but the run is
                # typed ``complete_unverified`` instead of claiming
                # "Validation passed" from missing evidence.
                status, validation = "complete_unverified", "unavailable"
                context.emit("  Validation produced no verdict; completing unverified.")
                context.event(
                    {
                        "event": "validation",
                        "valid": None,
                        "reason": "validator unavailable",
                        "deterministic": False,
                    }
                )
            else:
                validation = "deterministic" if vresult.deterministic else "passed"
                self.validation.clear_failure()
                context.emit("  Validation passed.")
                context.event(
                    {
                        "event": "validation",
                        "valid": True,
                        "deterministic": vresult.deterministic,
                    }
                )
        if self.validation.recheck_needed:
            if self.validation.attempts >= 2:
                reason = "validation remains failed after the maximum checks"
            else:
                reason = (
                    "completion after failed validation requires new write, edit, or shell evidence"
                )
            RunProgress(context.current_view().state).errors.append(f"[validation_failed] {reason}")
            context.emit(f"  Completion refused: {reason}")
            context.event({"event": "validation_pending", "reason": reason})
            return None
        outcome = self._build_outcome(status, validation, replan)
        context.emit(f"All tasks complete. ({outcome.wall_s:.1f}s total)")
        context.emit(f"Output in: {context.current_view().working_dir}")
        return outcome

    def exhausted(self):
        """Exhaustion is terminal: no shell-success reconciliation (issue #68).

        The former deterministic pass could convert an exhausted budget into
        ``complete`` from a broad "latest shell succeeded" heuristic even
        though the plan never finished; that is evaluation-contaminating
        false success, so the run now reports ``exhausted`` unconditionally.
        """
        context = self._context
        validation = "failed" if self.validation.recheck_needed else "skipped"
        outcome = self._build_outcome("exhausted", validation, context.current_view().max_replans)
        context.emit(
            f"Exhausted {context.current_view().max_replans} replan attempts. "
            f"({outcome.wall_s:.1f}s total)"
        )
        context.emit(f"Errors: {RunProgress(context.current_view().state).errors}")
        context.emit(f"Output in: {context.current_view().working_dir}")
        return outcome

    def _build_outcome(self, status, validation, replans):
        """Snapshot the terminal record from the run-scoped state."""
        progress = RunProgress(self._context.current_view().state)
        return RunOutcome(
            status=status,
            validation=validation,
            replans=replans,
            wall_s=round(self._context.elapsed(), 2),
            completed_tasks=len(progress.completed_tasks),
            selected_steps=progress.selected_steps,
            executed_steps=progress.executed_steps,
            skipped_steps=progress.skipped_steps,
            errors=tuple(progress.errors[-5:]) if status == "exhausted" else (),
        )
