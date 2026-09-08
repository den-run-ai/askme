"""Planning, run state, recording and controller sequencing.

The runtime consumes explicit defaults and collaborators. This module never
loads dotenv files or imports askme; the CLI facade supplies legacy defaults.
"""

import hashlib
import json
import os
import re
import shlex
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from dataclasses import replace as _dataclass_replace
from pathlib import Path
from typing import Any, Callable, NamedTuple

import llm as _llm
from actions import (
    OBSERVE_ACTIONS,
    OBSERVE_STATE_CHARS,
    ActionEnvelope,
    ActionExecutor,
    ActionProtocolError,
    ActionResult,
    ActionTransport,
    DecodedAction,
    SkippedStep,
    StepReceipt,
    _mutation_target_key,
    _step_path,
    _valid_nonempty_str,
    parse_action_envelope,
)
from llm import (
    ACTION_TRANSPORT,
    REASONING_POLICIES,
    LLMTransportError,
    PlanResponse,
    TaskReplanResponse,
    ValidationResponse,
    _ignore,
)
from policies import (
    _VALIDATE_KEYWORDS,
    _incomplete_write_visibility,
    _StepFlow,
    _unresolved_incomplete_writes,
    _write_visibility_flag,
)

MAX_REPLANS = 3
MAX_TASKS = 10
MAX_STEPS = 10
MAX_STEP_HISTORY = 3
MAX_INPUT = 300
GOAL_CONTEXT_CHARS = 300
DEFAULT_REASONING_POLICY = "gated"
WRITE_PRESSURE_OBSERVATIONS = 3
REWRITE_PRESSURE_WRITES = 2
REWRITE_SKIP_WRITES = 3
MAX_TASK_LOCAL_REPLANS = 1
STEP_POLICIES = ("heuristic", "lifecycle")

PROBE_TOOLS = ["python3", "go", "node", "gcc", "cc", "make", "cargo", "rustc", "java", "javac"]

PROBE_PKG_MANAGERS = ["brew", "apt-get", "dnf", "pacman", "apk"]

_WRITE_TASK_RE = re.compile(
    r"\b(implement|write|create|patch|add|fix|edit|update|replace|insert)\b",
    re.I,
)

_OBSERVE_TASK_RE = re.compile(
    r"^\s*(find|search|locate|inspect|read|list|review|explore|examine|look|check|show)\b",
    re.I,
)

SYSTEM_PLAN = f"""Planner. Propose tasks for the user request.
Rules:
- Prefer 1-3 tasks, max {MAX_TASKS}; each is a complete goal, not one command
- Short tasks (<15 words) with key details: includes, imports, filenames
- Relative filenames only, except preserve an exact incomplete_write_target
  supplied in state. Match state.environment.platform
- Never redo completed_tasks
- If required tool in missing_tools and allow_system_installs=false: single prerequisite/fail task listing missing tools
- If allow_system_installs=true: may use package_managers
Output ONLY valid JSON. No markdown, no explanation.
Format: {{"tasks":["task1","task2"]}}"""

SYSTEM_STEP = """Executor. Call exactly ONE tool per turn. No text outside the tool call.
Rules:
- done only when the FULL task description is satisfied
- fail if same error appears 2+ times
- Never redo completed_tasks
- Relative paths, except use an exact incomplete_write_target supplied in state.
  Recover it before done; append only when incomplete_write_append_allowed=true.
  Reasoning max 10 words
- If missing_tools required and allow_system_installs=false: fail; do NOT install
- Prefer edit over write for existing files
- Prefer search/tree over shell grep/find/ls
- read: initial pages take offset/limit (1-based lines); continuation pages must
  echo the output's cursor, limit, and sha256. Cursors count Unicode code points.
- write: whole file in content; set append=true to append the next chunk"""

SYSTEM_VALIDATE = """You are a completion validator. Given a goal, completed tasks with their execution evidence, and the current working directory listing, determine if the goal was fully achieved.

Examine the evidence carefully:
- Did all required files get created?
- Did compilation/build succeed?
- Did the program run and produce correct output?
- Were all parts of the goal addressed?

Output ONLY valid JSON. No markdown, no explanation.
Format: {"valid": true} or {"valid": false, "reason": "what is missing or wrong", "missing": ["specific missing items"]}"""

SYSTEM_TASK_REPLAN = """You are a task replanner. A single task failed. Given the failed task, errors, and completed tasks, propose a replacement task description.
Do NOT repeat completed work. The replacement must address the failure.
Preserve the original task's outcome. If it was to fix, edit, add, compile, run, create, or write something, do NOT replace it with a read/list/inspect-only preparation task.
Keep the replacement short (under 15 words). Use relative filenames, except
preserve an exact incomplete_write_target supplied in state.
Output ONLY valid JSON. No markdown, no explanation.
Format: {"task": "replacement task description"}"""

_KNOWN_ERROR_TYPES = {
    "timeout",
    "missing_tool",
    "permission_denied",
    "missing_file",
    "compile_error",
    "edit_failed",
    "stuck_loop",
    "unknown",
    "unknown_action",
    "control_action",
    "malformed_action",
    "response_truncated",
    "invalid_read_cursor",
    "invalid_read_limit",
    "invalid_read_offset",
    "invalid_timeout",
    "read_cursor_hash_required",
    "stale_read_cursor",
}

_NO_THINK_ERRORS = frozenset(
    {
        "edit_failed",
        "missing_file",
        "timeout",
        "missing_tool",
        "permission_denied",
        "invalid_read_cursor",
        "invalid_read_limit",
        "invalid_read_offset",
        "invalid_timeout",
        "read_cursor_hash_required",
        "stale_read_cursor",
    }
)

_RECOVERY_HINTS = {
    "edit_failed": "Read the file first, then retry edit with exact text from the file.",
    "missing_file": "Check the filename. Use shell ls to list directory contents.",
    "invalid_read_cursor": "Use cursor, limit, and sha256 exactly from the latest read continuation.",
    "invalid_read_limit": "Use cursor, limit, and sha256 exactly from the latest read continuation.",
    "invalid_read_offset": "Use a positive integer offset within the supported range.",
    "invalid_timeout": "Use an integer timeout from 5 to 300 seconds.",
    "read_cursor_hash_required": "Use cursor, limit, and sha256 exactly from the latest read continuation.",
    "stale_read_cursor": "The file changed. Restart read with offset and limit; do not reuse the old cursor.",
}

_PASSIVE_TASK_RE = re.compile(r"^\s*(read|inspect|view|open|list|check|examine)\b", re.I)

_ACTION_TASK_RE = re.compile(
    r"\b(fix|edit|add|insert|include|compile|build|run|create|write|update|replace|execute|remove)\b",
    re.I,
)

_LOW_VALUE_TASK_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "again",
        "code",
        "correct",
        "file",
        "for",
        "in",
        "of",
        "rebuild",
        "recompile",
        "rerun",
        "run",
        "the",
        "then",
        "to",
        "using",
        "with",
    }
)

_COMPILE_REPAIR_PATTERNS: list[dict[str, Any]] = [
    {
        "diagnostic_re": re.compile(
            r"implicit declaration of function '(printf|puts|fprintf|scanf)'|"
            r"implicitly declaring library function '(printf|puts|fprintf|scanf)'|"
            r"undeclared library function '(printf|puts|fprintf|scanf)'|"
            r"include the header <stdio\.h>|"
            r"stdio\.h.*[Nn]o such file",
            re.I,
        ),
        "fix_include": "#include <stdio.h>",
        "file_pattern": re.compile(r"\.(c|h)$"),
    },
    {
        "diagnostic_re": re.compile(
            r"implicit declaration of function '(strlen|strcmp|strcpy|strcat|memcpy)'|"
            r"undeclared library function '(strlen|strcmp|strcpy|strcat|memcpy)'|"
            r"include the header <string\.h>|"
            r"string\.h.*[Nn]o such file",
            re.I,
        ),
        "fix_include": "#include <string.h>",
        "file_pattern": re.compile(r"\.(c|h)$"),
    },
]


@dataclass(frozen=True)
class PromptDefaults:
    """Raw prompt defaults; an explicitly selected client/budget takes precedence.

    These records intentionally do not validate unused compatibility values.
    Validation belongs to the settings selected for the current call or run.
    """

    system_plan: Any = SYSTEM_PLAN
    system_step: Any = SYSTEM_STEP
    system_task_replan: Any = SYSTEM_TASK_REPLAN
    system_validate: Any = SYSTEM_VALIDATE
    planner_tokens: Any = _llm._GENERAL_PROFILE.planner_tokens
    step_tokens: Any = _llm._GENERAL_PROFILE.step_tokens
    task_replan_tokens: Any = _llm._GENERAL_PROFILE.task_replan_tokens
    validation_tokens: Any = _llm._GENERAL_PROFILE.validation_tokens
    replan_timeout: Any = _llm.LLM_TIMEOUT_REPLAN
    max_tasks: Any = MAX_TASKS
    max_step_history: Any = MAX_STEP_HISTORY
    max_input: Any = MAX_INPUT
    observe_state_chars: Any = OBSERVE_STATE_CHARS
    reasoning_policy: Any = DEFAULT_REASONING_POLICY


@dataclass(frozen=True)
class PromptContext:
    """Explicit dependencies for the shared planner/executor prompt builders."""

    defaults: PromptDefaults
    ask: Callable
    policy: Callable
    log: Callable
    deterministic_check: Callable


@dataclass(frozen=True)
class LoopDefaults:
    """Raw fallback values, resolved only when the corresponding config is unset."""

    reasoning_policy: Any = DEFAULT_REASONING_POLICY
    max_replans: Any = MAX_REPLANS
    max_tasks: Any = MAX_TASKS
    max_steps: Any = MAX_STEPS
    goal_context_chars: Any = GOAL_CONTEXT_CHARS
    allow_system_installs: Any = False
    allow_network: Any = True
    final_validate: Any = "auto"
    compile_repair: Any = True
    step_policy: Any = "heuristic"
    write_pressure_observations: Any = WRITE_PRESSURE_OBSERVATIONS
    observe_tail_reserve: Any = 3
    rewrite_pressure_writes: Any = REWRITE_PRESSURE_WRITES
    rewrite_skip_writes: Any = REWRITE_SKIP_WRITES
    max_task_local_replans: Any = MAX_TASK_LOCAL_REPLANS


@dataclass(frozen=True)
class LoopHooks:
    """Named composition seams; the facade supplies explicit late-bound callbacks.

    The controller owns sequencing, not global configuration or transport setup.
    Factories are lazy so unused defaults cannot invalidate an injected run.
    """

    current_llm_settings: Callable
    clock_factory: Callable
    step_policies: Callable
    get_plan: Callable
    get_step: Callable
    replan_task: Callable
    preflight: Callable
    execute: Callable
    log: Callable
    event: Callable
    resolve_llm_settings: Callable
    make_client: Callable
    make_frozen_client: Callable
    make_run_state: Callable
    make_obligations: Callable
    make_completion: Callable
    compile_repair: Callable
    repair_satisfied: Callable


def _is_write_shaped(task):
    return bool(task) and bool(_WRITE_TASK_RE.search(task)) and not _OBSERVE_TASK_RE.match(task)


def _extract_error_type(err):
    """Extract [type] prefix from error string if present, else classify by heuristic."""
    # Check for existing [type] prefix from classify_error / run loop
    if err.startswith("["):
        bracket_end = err.find("]")
        if bracket_end > 1:
            candidate = err[1:bracket_end]
            if candidate in _KNOWN_ERROR_TYPES:
                return candidate, err[bracket_end + 2 :]  # strip "[type] " prefix
    # Fallback: heuristic classification
    err_lower = err.lower()
    if "timeout" in err_lower:
        return "timeout", err
    if "command not found" in err_lower:
        return "missing_tool", err
    if "permission denied" in err_lower:
        return "permission_denied", err
    if "no such file" in err_lower:
        return "missing_file", err
    if "stuck" in err_lower or "failed twice" in err_lower:
        return "stuck_loop", err
    if "error:" in err_lower or "syntax error" in err_lower:
        return "compile_error", err
    return "unknown", err


def summarize_errors(errors):
    """Compact error strings into typed summary for planner.
    Preserves [type] prefixes from classify_error, groups by type, deduplicates."""
    if not errors:
        return []
    summarized: dict[str, list[str]] = {}
    for err in errors:
        etype, msg = _extract_error_type(err)
        if etype not in summarized:
            summarized[etype] = []
        short = msg[:120]
        if short not in summarized[etype]:
            summarized[etype].append(short)
    result = []
    for etype, msgs in summarized.items():
        for msg in msgs[:3]:  # max 3 per type
            result.append(f"[{etype}] {msg}")
    return result


def _step_digest(steps, count=6):
    """Compact digest of recent executed steps for planning (never file contents)."""
    digest = []
    for s in steps[-count:]:
        digest.append(
            {
                "action": s.get("action"),
                "arg": (s.get("arg") or "")[-120:],
                "ok": s.get("ok"),
                "output": (s.get("output") or "")[:80],
            }
        )
    return digest


class TaskReplanResult(NamedTuple):
    """Structured task-local replan outcome (issue #40).

    ``task`` is the accepted replacement, or None when the replan failed;
    ``reject_reason`` then carries the typed reason. This return value
    replaces the former ``_last_task_replan_reject_reason`` module-global
    side channel."""

    task: str | None
    reject_reason: str | None


def _task_keywords(text):
    """Normalized content words for rejecting near-duplicate task replans."""
    text = text.lower().replace("#include", "include")
    words = re.findall(r"[a-z0-9_.]+", text)
    return {w for w in words if len(w) > 1 and w not in _LOW_VALUE_TASK_WORDS}


def _task_entities(text):
    """Extract concrete files/headers mentioned in a task."""
    text = text.lower().replace("#include", "include")
    return set(re.findall(r"\b[a-z0-9_./-]+\.(?:c|h|py|txt|json|md|js|ts|go|rs|java)\b", text))


def _task_action_words(text):
    """Normalized action words that define the kind of task."""
    return _ACTION_TASK_RE.findall(text.lower())


def _is_near_duplicate_task(original, replacement):
    """True when a replacement only rephrases or appends low-value words."""
    orig = _task_keywords(original)
    repl = _task_keywords(replacement)
    if not orig or not repl:
        return False
    orig_entities = _task_entities(original)
    repl_entities = _task_entities(replacement)
    orig_actions = set(_task_action_words(original))
    repl_actions = set(_task_action_words(replacement))
    if orig_entities and orig_entities == repl_entities:
        edit_like = {"fix", "edit", "add", "insert", "include", "update", "replace", "write"}
        if orig_actions & edit_like and repl_actions & edit_like:
            return True
    overlap = len(orig & repl)
    jaccard = overlap / len(orig | repl)
    coverage = overlap / min(len(orig), len(repl))
    return jaccard >= 0.8 or coverage >= 0.9


def _is_passive_replacement(original, replacement):
    """Reject prep-only replacements that downgrade an actionable task."""
    if not _ACTION_TASK_RE.search(original):
        return False
    if not _PASSIVE_TASK_RE.search(replacement):
        return False
    # Allow compact two-part tasks such as "check error and fix include".
    return not re.search(
        r"\b(then|and)\b.*\b(fix|edit|add|insert|include|compile|build|run|create|write|update|replace|execute|remove)\b",
        replacement,
        re.I,
    )


def _deterministic_check(user_prompt, state, working_dir):
    """Conservative completion check. Returns True, False, or None."""
    all_steps = state.get("all_steps", [])
    if state.get("pending_empty_writes"):
        return False

    # A truncated write is an incomplete artifact, not successful completion
    # evidence. Unrelated edits/shells cannot hide it; only a later complete
    # write/append to the same normalized target resolves it.
    if _unresolved_incomplete_writes(all_steps, working_dir):
        return False

    # Successful writes should leave non-empty files.
    for s in all_steps:
        if s.get("action") == "write" and s.get("ok"):
            arg = s.get("arg", "")
            if not _valid_nonempty_str(arg):
                return False
            p = _step_path(arg, working_dir)
            try:
                if not p.exists() or p.stat().st_size == 0:
                    return False
            except OSError:
                return False

    shell_steps = [(i, s) for i, s in enumerate(all_steps) if s.get("action") == "shell"]
    if _VALIDATE_KEYWORDS.search(user_prompt) and shell_steps:
        last_shell_idx, last_shell = shell_steps[-1]
        if not last_shell.get("ok"):
            return False
        later_mutation = any(
            s.get("action") in ("write", "edit") and s.get("ok")
            for s in all_steps[last_shell_idx + 1 :]
        )
        if not later_mutation and not state.get("errors"):
            return True

    return None


def _resolve_existing_candidates(paths, working_dir):
    root = Path(working_dir)
    resolved = []
    seen = set()
    for p in paths:
        p = Path(p)
        if not p.is_absolute():
            p = root / p
        try:
            key = p.resolve()
        except OSError:
            key = p
        if p.exists() and p.is_file() and key not in seen:
            seen.add(key)
            resolved.append(p)
    return resolved


def _compile_repair_candidates(error_output, cmd, working_dir):
    """Return source-file candidates in safest priority order."""
    diagnostic_paths = re.findall(
        r"([A-Za-z0-9_./-]+\.(?:c|h)):\d+(?::\d+)?:",
        error_output,
    )
    diagnostic_candidates = _resolve_existing_candidates(diagnostic_paths, working_dir)
    if diagnostic_candidates:
        return diagnostic_candidates

    try:
        parts = shlex.split(cmd)
    except ValueError:
        parts = cmd.split()
    command_paths = []
    skip_next = False
    for part in parts[1:]:
        if skip_next:
            skip_next = False
            continue
        if part == "-o":
            skip_next = True
            continue
        if part.endswith((".c", ".h")):
            command_paths.append(part)
    command_candidates = _resolve_existing_candidates(command_paths, working_dir)
    if command_candidates:
        return command_candidates

    c_files = sorted(Path(working_dir).glob("*.c"))
    return c_files if len(c_files) == 1 else []


def _task_satisfied_by_deterministic_repair(task, state):
    """Return repair step if a planned edit task was already done deterministically."""
    task_lower = task.lower()
    if "include" not in task_lower or not re.search(
        r"\b(add|insert|edit|include|fix)\b", task_lower
    ):
        return None
    requested_include = None
    for include in ("#include <stdio.h>", "#include <string.h>"):
        if (
            include.lower() in task_lower
            or include.split("<", 1)[1].rstrip(">").lower() in task_lower
        ):
            requested_include = include
            break
    if not requested_include:
        return None

    entities = {Path(e).name for e in _task_entities(task)}
    for step in reversed(state.get("all_steps", [])):
        if not step.get("deterministic_repair"):
            continue
        if requested_include not in step.get("output", ""):
            continue
        arg_name = Path(step.get("arg", "")).name
        if entities and arg_name not in entities:
            continue
        return dict(step)
    return None


@dataclass
class TaskAttemptState:
    """Executor-facing state scoped to one attempt of one task (issue #31).

    A task-local replan constructs a fresh attempt; nothing here survives
    into the next attempt except what the run-scoped record already holds.
    """

    task: str
    wants_write: bool
    done: bool = False
    steps: list[dict[str, Any]] = field(default_factory=list)
    use_think: bool = False
    reasoning_trigger: str = "executor"
    dup_skip_count: int = 0
    observe_executed: int = 0
    commit_executed: int = 0
    observe_blocked: int = 0
    # Resolved per-run guard threshold (issue #68); the default keeps direct
    # constructions behaving like the module constant.
    write_pressure_observations: int = WRITE_PRESSURE_OBSERVATIONS

    def write_pressure(self):
        """True once observation spending must yield to a first commit."""
        return (
            self.wants_write
            and self.commit_executed == 0
            and self.observe_executed >= self.write_pressure_observations
        )


@dataclass
class _StepContext:
    """Working data for one selected executor action within an attempt."""

    task_index: int
    step: int
    started: float
    action: ActionEnvelope
    act: str
    transport: ActionTransport = field(default_factory=ActionTransport)
    truncated_write: bool = False
    logical_write_target: str | None = None
    operation_write_target: str | None = None


@dataclass(frozen=True)
class GuardThresholds:
    """Resolved controller guard thresholds for one run (issue #68).

    These counters decide when observation must yield to a commit, when
    rewrites must yield to verification, and how many task-local replans an
    attempt gets — outcome-affecting policy, so they are frozen per run and
    enter the hash-logged configuration instead of being read from module
    globals mid-run."""

    write_pressure_observations: int
    observe_tail_reserve: int
    rewrite_pressure_writes: int
    rewrite_skip_writes: int
    max_task_local_replans: int

    def __post_init__(self):
        for name in (
            "write_pressure_observations",
            "observe_tail_reserve",
            "rewrite_pressure_writes",
            "rewrite_skip_writes",
            "max_task_local_replans",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    def describe(self):
        """JSON-ready threshold record for config metadata and hashing."""
        return {
            "write_pressure_observations": self.write_pressure_observations,
            "observe_tail_reserve": self.observe_tail_reserve,
            "rewrite_pressure_writes": self.rewrite_pressure_writes,
            "rewrite_skip_writes": self.rewrite_skip_writes,
            "max_task_local_replans": self.max_task_local_replans,
        }


def _config_hash(payload):
    """Short stable digest of the resolved outcome-affecting configuration.

    Canonical-JSON sha256 prefix; never includes credentials. Two runs with
    the same hash ran the same policy surface (issue #68)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class RunDependencies:
    """Injectable collaborators for one run (issue #40).

    ``None`` fields keep the module seams — ``ask_llm``, ``execute``,
    ``log``, ``_run_log``, and ``time.time`` — resolved at call time, so
    patch-based tests keep intercepting them. An injected ``llm_client``
    (an :class:`LLMClient` or any object with a compatible ``ask``) handles
    every planner, executor, validator, and task-replanner call; its ``ask``
    must accept the keyword arguments :meth:`LLMClient.ask` accepts —
    including ``expect``/``expect_context`` — so duck-typed clients should
    take ``**kwargs`` (the protocol tracks the client, issue #68). A client
    without a ``settings`` attribute is recorded as ``injected_opaque`` in
    the hashed config provenance. An injected
    ``action_executor`` receives every dispatch, including deterministic
    retries; when it names a ``working_dir`` it must be the run's workspace,
    and the run is rejected otherwise. Sinks capture controller-owned
    logging plus all LLM telemetry: supplying a sink without a client gives
    the run a client snapshotted from the module configuration so nothing
    escapes to the module stdout/JSONL sinks."""

    llm_client: Any = None
    action_executor: Any = None
    clock: Any = None
    log_sink: Any = None
    event_sink: Any = None


@dataclass(frozen=True)
class RunWorkspace:
    """Workspace identity and ownership for one run (issue #40).

    ``created`` is True only when the run made the temporary directory, so
    callers can clean up intentionally; supplied directories are never
    removed by AskMe."""

    path: str
    created: bool

    @classmethod
    def resolve(cls, working_dir=None):
        """Use the caller's directory, or create an isolated temporary one."""
        if working_dir is None:
            return cls(path=tempfile.mkdtemp(prefix="askme_"), created=True)
        return cls(path=str(working_dir), created=False)

    def cleanup(self):
        """Remove the directory only if this run created it."""
        if self.created:
            shutil.rmtree(self.path, ignore_errors=True)

    def describe(self):
        """JSON-ready ownership record for the structured run result."""
        return {"path": self.path, "created": self.created}


def preflight_probe(working_dir=".", *, tools=PROBE_TOOLS, package_managers=PROBE_PKG_MANAGERS):
    """Deterministic environment probe. Returns structured dict for planner state."""
    import platform

    env: dict[str, Any] = {
        "platform": platform.system().lower(),  # "darwin", "linux", "windows"
        "arch": platform.machine(),  # "arm64", "x86_64"
        "working_dir": str(Path(working_dir).resolve()),
    }
    # Available tools (fixed allowlist, no prompt inference)
    # Uses shutil.which() — cross-platform, works on Windows/macOS/Linux
    available = []
    missing = []
    for tool in tools:
        if shutil.which(tool):
            available.append(tool)
        else:
            missing.append(tool)
    env["available_tools"] = available
    env["missing_tools"] = missing
    # Package managers
    pkg_managers = []
    for pm in package_managers:
        if shutil.which(pm):
            pkg_managers.append(pm)
    env["package_managers"] = pkg_managers
    # Dir listing (compact)
    try:
        entries = sorted(os.listdir(working_dir))[:20]
        env["dir_listing"] = entries if entries else ["(empty)"]
    except Exception:
        env["dir_listing"] = ["(error reading dir)"]
    return env


def get_plan(
    user_prompt,
    state,
    client=None,
    max_tokens=None,
    max_tasks=None,
    timeout=None,
    replan_timeout=None,
    max_retries=None,
    *,
    context,
):
    # Include environment and policy in planner state.
    # Run-control metadata is logged/returned but is not task evidence for the
    # model, and raw step payloads (write contents) never reach the planner —
    # only a curated digest does. This keeps replan state bounded on
    # write-heavy runs while still telling the planner what already happened.
    plan_state = {
        key: state[key]
        for key in ("completed_tasks", "errors", "environment", "policy")
        if key in state
    }
    recent = _step_digest(state.get("all_steps", []))
    if recent:
        plan_state["recent_steps"] = recent
    # Write-forcing visibility (issue #15): both 2026-08-01 canary models'
    # replans restated the failed task text; make the actual write state
    # visible instead. Incomplete artifacts are run-scoped completion blockers;
    # no_write/unvalidated progress remains scoped to, and classified from,
    # the failed task itself (Codex P2, PR #16).
    task_steps = state.get("all_steps", [])[state.get("task_start_step_count", 0) :]
    current_task = state.get("current_task", "")
    incomplete = _incomplete_write_visibility(
        state.get("all_steps", []), state.get("pending_empty_writes")
    )
    if incomplete:
        plan_state.update(incomplete)
    elif _is_write_shaped(current_task):
        flag = _write_visibility_flag(task_steps)
        if flag:
            plan_state.update(flag)
    if "environment" not in plan_state:
        plan_state["environment"] = {}
    if "policy" not in plan_state:
        plan_state["policy"] = context.policy()
    # Summarize errors for compact, typed diagnostics
    if plan_state.get("errors"):
        plan_state["errors"] = summarize_errors(plan_state["errors"])
    # Think on second/later planning attempts (or stateful direct replans) —
    # first plans don't benefit from thinking, and thinking tokens compete with
    # the task-list budget (768 tokens).
    # Benchmark evidence: think=False produces equal/better plans and avoids
    # token-budget truncation on the local 4B model. See benchmarks/.
    is_replan = bool(
        state.get("planning_attempt", 0) > 0
        or plan_state.get("errors")
        or plan_state.get("completed_tasks")
    )
    # An injected per-run client (issue #40) replaces the patchable module
    # facade only when the caller supplied one.
    ask = context.ask if client is None else client.ask
    settings = getattr(client, "settings", None)
    planner_budget = (
        context.defaults.planner_tokens
        if settings is None
        else settings.resolved_capability_profile().planner_tokens
    )
    request_policy = {}
    if is_replan:
        request_policy["timeout"] = (
            context.defaults.replan_timeout if replan_timeout is None else replan_timeout
        )
    elif timeout is not None:
        request_policy["timeout"] = timeout
    if max_retries is not None:
        request_policy["max_retries"] = max_retries
    return ask(
        [
            {"role": "system", "content": context.defaults.system_plan},
            {
                "role": "user",
                "content": f"REQUEST:\n{user_prompt}\n\nSTATE:\n{json.dumps(plan_state)}",
            },
        ],
        max_tokens=planner_budget if max_tokens is None else max_tokens,
        think=is_replan,
        reasoning_policy=state.get("reasoning_policy", context.defaults.reasoning_policy),
        reasoning_trigger="planner_replan" if is_replan else "initial_plan",
        expect="plan",
        # The decode-time schema truncates exactly like the consuming call
        # site (PR #75 review): the run's task limit, not the module default.
        expect_context={
            "max_tasks": context.defaults.max_tasks if max_tasks is None else max_tasks
        },
        **request_policy,
    )


def get_step(
    task,
    state,
    goal="",
    step_num=0,
    max_steps=MAX_STEPS,
    think=False,
    reasoning_policy=DEFAULT_REASONING_POLICY,
    reasoning_trigger="executor",
    goal_context_chars=GOAL_CONTEXT_CHARS,
    write_pressure=False,
    validate_pressure=None,
    client=None,
    step_tokens=None,
    timeout=None,
    max_retries=None,
    *,
    context,
):
    # Build slim step history from recent steps (current task + carryover from previous)
    steps = state.get("last_steps", [])[-context.defaults.max_step_history :]
    slim_steps = []
    for s in steps:
        # Use basename for file paths to avoid long tmp_path bloat
        arg = s.get("arg", "")
        if s["action"] in ("write", "read", "edit", "tree") and "/" in arg:
            arg = Path(arg).name
        else:
            arg = arg[-context.defaults.max_input :]
        # Observation actions (read/search/tree) carry the content the model
        # navigates by; they get a larger output budget than mutating actions.
        out_cap = (
            context.defaults.observe_state_chars
            if s["action"] in OBSERVE_ACTIONS
            else context.defaults.max_input
        )
        slim_steps.append(
            {
                "action": s["action"],
                "arg": arg,
                "ok": s["ok"],
                "output": s.get("output", "")[:out_cap],
            }
        )
    slim = {
        "task": state.get("current_task", task)[: context.defaults.max_input],
        "task_index": state.get("task_index", ""),
        "step": f"{step_num + 1}/{max_steps}",
        "last_steps": slim_steps,
    }
    incomplete = _incomplete_write_visibility(
        state.get("all_steps", []), state.get("pending_empty_writes")
    )
    if incomplete:
        # Keep the recovery identity/mode structured on every executor turn.
        # Step history is bounded and is reset across a task-local retry.
        slim.update(incomplete)
    # Include completed tasks so executor knows what's already done
    completed = state.get("completed_tasks", [])
    if completed:
        slim["completed_tasks"] = [t[:80] for t in completed[-3:]]
    # Include missing tools and policy so executor can fail fast on prerequisites
    env = state.get("environment", {})
    if env.get("missing_tools"):
        slim["missing_tools"] = env["missing_tools"]
    slim["policy"] = state.get("policy", context.policy())
    goal_line = f"GOAL:\n{goal[:goal_context_chars]}\n\n" if goal else ""
    user_msg = (
        f"{goal_line}TASK:\n{task[: context.defaults.max_input]}\n\nSTATE:\n{json.dumps(slim)}"
    )
    if write_pressure:
        user_msg += (
            "\nNOTE: several observation steps done but no write yet. "
            "Next action MUST be write, edit, or shell — or fail with a one-line reason."
        )
    if validate_pressure:
        user_msg += (
            f"\nNOTE: {validate_pressure} is already written. Do NOT write the whole "
            "file again. Next action MUST be shell (verify it), edit (targeted fix), "
            "or done."
        )
    # The run's resolved budget wins (issue #69); otherwise a pinned
    # client's budget follows that client's capability profile (issues
    # #15/#40/#68), and
    # the module facade keeps the patchable global.
    ask = context.ask if client is None else client.ask
    settings = getattr(client, "settings", None)
    if step_tokens is not None:
        step_budget = step_tokens
    else:
        step_budget = context.defaults.step_tokens if settings is None else settings.step_tokens()
    request_policy = {}
    if timeout is not None:
        request_policy["timeout"] = timeout
    if max_retries is not None:
        request_policy["max_retries"] = max_retries
    return ask(
        [
            {"role": "system", "content": context.defaults.system_step},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=step_budget,
        think=think,
        reasoning_policy=reasoning_policy,
        reasoning_trigger=reasoning_trigger,
        expect="action",
        **request_policy,
    )


def replan_task(
    failed_task,
    errors,
    completed_tasks,
    state,
    user_prompt,
    goal_context_chars=GOAL_CONTEXT_CHARS,
    client=None,
    max_tokens=None,
    timeout=None,
    max_retries=0,
    *,
    context,
):
    """Mini-planner: generate a replacement for one failed task.

    Returns a :class:`TaskReplanResult`; ``task`` is None when the replan
    failed and ``reject_reason`` then names the typed rejection."""
    replan_state = {
        "failed_task": failed_task,
        "errors": summarize_errors(errors),
        "completed_tasks": [t[:80] for t in completed_tasks[-3:]],
    }
    # Stateful replanning: the mini-planner sees what the executor actually did
    # on the failed task (actions + outcomes), not just typed error strings.
    task_steps = state.get("all_steps", [])[state.get("task_start_step_count", 0) :]
    failed_steps = _step_digest(task_steps, count=3)
    if failed_steps:
        replan_state["failed_steps"] = failed_steps
    # failed_steps and advisory progress flags remain task-scoped even when
    # the slice is empty. Incomplete artifacts are checked run-wide because
    # they remain a completion blocker across replacement-task boundaries.
    incomplete = _incomplete_write_visibility(
        state.get("all_steps", []), state.get("pending_empty_writes")
    )
    if incomplete:
        replan_state.update(incomplete)
    elif _is_write_shaped(failed_task):
        flag = _write_visibility_flag(task_steps)
        if flag:
            replan_state.update(flag)
    env = state.get("environment", {})
    if env.get("missing_tools"):
        replan_state["missing_tools"] = env["missing_tools"]
    replan_state["policy"] = state.get("policy", context.policy())
    ask = context.ask if client is None else client.ask
    settings = getattr(client, "settings", None)
    replan_budget = (
        context.defaults.task_replan_tokens
        if settings is None
        else settings.resolved_capability_profile().task_replan_tokens
    )
    try:
        request_policy = {"max_retries": max_retries}
        if timeout is not None:
            request_policy["timeout"] = timeout
        result = ask(
            [
                {"role": "system", "content": context.defaults.system_task_replan},
                {
                    "role": "user",
                    "content": f"GOAL:\n{user_prompt[:goal_context_chars]}\n\nSTATE:\n{json.dumps(replan_state)}",
                },
            ],
            max_tokens=replan_budget if max_tokens is None else max_tokens,
            think=False,
            reasoning_policy=state.get("reasoning_policy", context.defaults.reasoning_policy),
            reasoning_trigger="task_local_replan",
            expect="task_replan",
            **request_policy,
        )
        parsed = TaskReplanResponse.parse(result)
        if parsed is None:
            return TaskReplanResult(None, "empty")
        task = parsed.task.strip()
        if len(task) <= 3:
            return TaskReplanResult(None, "too_short")
        if task == failed_task.strip():
            return TaskReplanResult(None, "exact_duplicate")
        if _is_near_duplicate_task(failed_task, task):
            return TaskReplanResult(None, "near_duplicate")
        if _is_passive_replacement(failed_task, task):
            return TaskReplanResult(None, "passive_downgrade")
        return TaskReplanResult(task, None)
    except LLMTransportError:
        return TaskReplanResult(None, "transport_error")
    except json.JSONDecodeError:
        return TaskReplanResult(None, "parse_error")
    except KeyError:
        return TaskReplanResult(None, "missing_task_key")


def _validate_completion(
    user_prompt,
    state,
    working_dir,
    client=None,
    log_sink=None,
    max_tokens=None,
    timeout=None,
    max_retries=0,
    *,
    context,
):
    """Run final validation. Returns a :class:`ValidationResponse` or None.

    None means no verdict was available — the validator was unreachable or
    replied outside its schema. Callers must treat that as unverified, never
    as a pass (issue #68)."""
    emit = context.log if log_sink is None else log_sink
    deterministic = context.deterministic_check(user_prompt, state, working_dir)
    if deterministic is True:
        return ValidationResponse(valid=True, deterministic=True)
    if deterministic is False:
        return ValidationResponse(
            valid=False,
            deterministic=True,
            reason="deterministic completion check failed",
        )

    completed = state.get("completed_tasks", [])
    step_groups = state.get("completed_step_groups", [])
    # Build evidence: per-task step summaries (action + basename + output snippet, ≤5 per task)
    evidence_lines = []
    for i, task in enumerate(completed):
        evidence_lines.append(f"Task {i + 1}: {task}")
        if i < len(step_groups):
            for s in step_groups[i][:5]:
                arg = s.get("arg", "")
                if "/" in arg:
                    arg = Path(arg).name
                out = s.get("output", "")[:80]
                evidence_lines.append(f"  - {s['action']} {arg}: {out}")
    evidence = "\n".join(evidence_lines)
    # File listing
    try:
        files = sorted(os.listdir(working_dir))[:50]
    except Exception:
        files = []
    user_msg = (
        f"GOAL:\n{user_prompt}\n\n"
        f"COMPLETED TASKS AND EVIDENCE:\n{evidence}\n\n"
        f"FILES IN WORKING DIRECTORY:\n{json.dumps(files)}"
    )
    ask = context.ask if client is None else client.ask
    settings = getattr(client, "settings", None)
    validation_budget = (
        context.defaults.validation_tokens
        if settings is None
        else settings.resolved_capability_profile().validation_tokens
    )
    try:
        request_policy = {"max_retries": max_retries}
        if timeout is not None:
            request_policy["timeout"] = timeout
        result = ask(
            [
                {"role": "system", "content": context.defaults.system_validate},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=validation_budget if max_tokens is None else max_tokens,
            think=True,
            think_level="high",
            reasoning_policy=state.get("reasoning_policy", context.defaults.reasoning_policy),
            reasoning_trigger="final_validator",
            expect="validation",
            **request_policy,
        )
        parsed = ValidationResponse.parse(result)
        if parsed is not None:
            return parsed
        emit(f"  Validation returned unexpected format: {result}")
        return None
    except LLMTransportError as e:
        emit(f"  Validation transport error (no verdict): {e}")
        return None
    except (json.JSONDecodeError, KeyError) as e:
        emit(f"  Validation parse error (no verdict): {e}")
        return None


def _compile_repair_action(error_output, working_dir, cmd, enabled=None):
    """Propose a normal write action for a known C include diagnostic, or None.

    The #41 repair-rule boundary: this function never mutates the workspace.
    It inspects the unique candidate source and, when a known
    missing-include diagnostic matches, returns an ordinary full-file write
    action — workspace-relative ``arg``, repaired ``content``, and the human
    description in ``reasoning`` — for the controller to dispatch through
    the action executor and record like any other step. ``enabled`` is the
    run's resolved ablation arm; None enables the standalone helper. The
    facade resolves its legacy switch before calling this implementation."""
    if not (True if enabled is None else enabled):
        return None
    for pattern in _COMPILE_REPAIR_PATTERNS:
        if not pattern["diagnostic_re"].search(error_output):
            continue
        candidates = [
            f
            for f in _compile_repair_candidates(error_output, cmd, working_dir)
            if pattern["file_pattern"].search(f.name)
        ]
        if len(candidates) != 1:
            return None
        f = candidates[0]
        if f.is_symlink():
            # The atomic write replaces the named leaf, so a symlinked
            # candidate must be repaired at its referent — exactly where the
            # legacy through-symlink write landed — or the repository's
            # symlink layout would be silently destroyed.
            try:
                f = f.resolve(strict=True)
            except OSError:
                return None
        try:
            text = f.read_text()
        except (OSError, UnicodeDecodeError):
            # An unreadable or non-UTF-8 candidate cannot be repaired; the
            # compile error surfaces to the model as a typed failure.
            return None
        include = pattern["fix_include"]
        if include in text:
            return None
        lines = text.split("\n")
        insert_idx = 0
        for j, line in enumerate(lines):
            if line.startswith("#include"):
                insert_idx = j + 1
        lines.insert(insert_idx, include)
        try:
            arg = str(f.relative_to(working_dir))
        except ValueError:
            arg = str(f)
        return {
            "action": "write",
            "arg": arg,
            "content": "\n".join(lines),
            "reasoning": f"Auto-inserted {include}",
        }
    return None


def _resolve_run_llm_settings(
    settings, *, replan_timeout=_llm.LLM_TIMEOUT_REPLAN, max_retries=_llm.MAX_LLM_RETRIES
):
    """Materialize compatibility defaults into one immutable run snapshot."""
    capabilities = settings.resolved_capability_profile()
    resolved = _dataclass_replace(
        settings,
        capability_profile=capabilities,
        step_write_tokens=None,
        step_token_budget=None,
        replan_timeout=(
            replan_timeout if settings.replan_timeout is None else settings.replan_timeout
        ),
        max_retries=max_retries if settings.max_retries is None else settings.max_retries,
        reasoning_token_floors=None,
    )
    for name in ("timeout", "replan_timeout"):
        value = getattr(resolved, name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if (
        not isinstance(resolved.max_retries, int)
        or isinstance(resolved.max_retries, bool)
        or resolved.max_retries < 0
    ):
        raise ValueError("max_retries must be a non-negative integer")
    return resolved


@dataclass(frozen=True)
class RunConfig:
    """Immutable per-run configuration (issues #40/#68).

    ``None`` fields resolve from the supplied defaults when the run starts;
    the CLI facade supplies its patchable compatibility values. Explicit
    fields pin the run: a pinned ``llm`` makes the run
    construct its own :class:`LLMClient` instead of using the module
    ``ask_llm`` facade, so differently configured runs coexist in one
    process without saving or restoring globals. Every outcome-affecting
    setting — validation mode, the #41 compile-repair arm, guard
    thresholds, capability budgets (on ``llm``), and run limits — resolves
    into one frozen per-run surface whose hash is logged at run_start and
    returned in the config metadata."""

    llm: _llm.LLMSettings | None = None
    allow_system_installs: bool | None = None
    allow_network: bool | None = None
    reasoning_policy: str | None = None
    max_replans: int | None = None
    max_tasks: int | None = None
    max_steps: int | None = None
    goal_context_chars: int | None = None
    final_validate: str | None = None
    compile_repair: bool | None = None
    step_policy: str | None = None
    write_pressure_observations: int | None = None
    observe_tail_reserve: int | None = None
    rewrite_pressure_writes: int | None = None
    rewrite_skip_writes: int | None = None
    max_task_local_replans: int | None = None

    @classmethod
    def from_env(cls, env=None):
        return cls._from_env(env)

    @classmethod
    def _from_env(cls, env=None, *, settings_factory=_llm.LLMSettings.from_env):
        """Derive the CLI-boundary configuration from an environment mapping.

        Budgets stay None (module defaults); the CLI overrides them from
        its parsed arguments."""
        e = os.environ if env is None else env
        policy = e.get("AGENT_REASONING_POLICY", "gated").strip().lower()
        if policy not in REASONING_POLICIES:
            raise ValueError(
                f"AGENT_REASONING_POLICY must be one of {', '.join(REASONING_POLICIES)}"
            )
        return cls(
            llm=settings_factory(e),
            allow_system_installs=e.get("ALLOW_SYSTEM_INSTALLS", "0") == "1",
            allow_network=e.get("ALLOW_NETWORK", "1") == "1",
            reasoning_policy=policy,
            goal_context_chars=(
                None
                if not e.get("AGENT_GOAL_CONTEXT_CHARS")
                else int(e["AGENT_GOAL_CONTEXT_CHARS"])
            ),
            final_validate=e.get("AGENT_FINAL_VALIDATE", "auto"),
            compile_repair=e.get("AGENT_COMPILE_REPAIR", "1") == "1",
            step_policy=e.get("AGENT_STEP_POLICY", "heuristic").strip().lower(),
        )


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


class _RunController:
    """Thin coordinator over planning, task attempts, step decisions, and
    finalization (issue #31).

    Behavior-preserving regrouping of the former monolithic ``_run_loop``:
    every log line, JSONL event, history entry, error string, counter, and
    guard decision is unchanged. Run-scoped data lives on :class:`RunState`,
    attempt-scoped data on :class:`TaskAttemptState`; ``done``/``fail``
    remain controller concerns, and every dispatch — normal, deterministic
    repair, and retry — still flows through the one :class:`StepRecorder`.
    """

    def __init__(
        self, user_prompt, working_dir, config=None, dependencies=None, *, defaults, hooks
    ):
        self._hooks = hooks
        cfg = RunConfig() if config is None else config
        deps = RunDependencies() if dependencies is None else dependencies
        # Keep validation/factory ordering: an unused invalid fallback must not
        # invalidate a pinned run, and no state/policy is created before setup.
        injected_settings = self._configure_request(user_prompt, working_dir, cfg, deps, defaults)
        self._bind_dependencies(cfg, deps)
        step_policy = self._configure_policies(cfg, deps, defaults, injected_settings)
        self._freeze_call_contract(step_policy)
        self.run_state = self._hooks.make_run_state(
            self.reasoning_policy,
            self.goal_context_chars,
            clock=self._clock,
            event_sink=deps.event_sink,
            rewrite_pressure_writes=self.guards.rewrite_pressure_writes,
            rewrite_skip_writes=self.guards.rewrite_skip_writes,
        )
        self.state = self.run_state.data
        self.history = self.run_state.history
        self.recorder = self.run_state.recorder
        # The policy components are constructed last so they can hold
        # run-scoped state over the same run_state/guards the controller
        # sequences (issues #31/#69): the selected pressure arm, the shared
        # incomplete-write obligations, and the terminal/validation policy.
        self.step_policy = self._hooks.step_policies()[step_policy](self)
        self.obligations = self._hooks.make_obligations(self)
        self.completion = self._hooks.make_completion(self)

    @staticmethod
    def _config_value(config, defaults, name):
        """Only None selects a fallback; false/zero values stay explicit."""
        selected = getattr(config, name)
        return getattr(defaults, name) if selected is None else selected

    def _configure_request(self, user_prompt, working_dir, cfg, deps, defaults):
        """Resolve limits and the selected model before client construction."""
        reasoning_policy = self._config_value(cfg, defaults, "reasoning_policy")
        if reasoning_policy not in REASONING_POLICIES:
            raise ValueError(f"reasoning_policy must be one of {', '.join(REASONING_POLICIES)}")
        self.user_prompt = user_prompt
        self.working_dir = working_dir
        self.max_replans = self._config_value(cfg, defaults, "max_replans")
        self.max_tasks = self._config_value(cfg, defaults, "max_tasks")
        self.max_steps = self._config_value(cfg, defaults, "max_steps")
        # The public config path enforces the same positive-budget contract
        # as the CLI's _positive_int (Codex P2, PR #65): a zero budget would
        # silently report a plausible failure without doing any work.
        for budget_name in ("max_replans", "max_tasks", "max_steps"):
            if getattr(self, budget_name) < 1:
                raise ValueError(f"{budget_name} must be a positive integer")
        self.reasoning_policy = reasoning_policy
        # Select the run's settings source before resolving it: invalid state
        # in an unused compatibility global must not block a pinned or injected
        # run. The default path retains the patchable ask_llm facade through a
        # run-local adapter, but the adapter supplies this one snapshot on every
        # call instead of re-reading MODEL/API/provider/timeouts.
        injected_settings = getattr(deps.llm_client, "settings", None)
        source_settings = (
            injected_settings
            if injected_settings is not None
            else (cfg.llm if cfg.llm is not None else self._hooks.current_llm_settings())
        )
        self._llm_meta = self._hooks.resolve_llm_settings(source_settings)
        goal_context_chars = self._config_value(cfg, defaults, "goal_context_chars")
        if (
            not isinstance(goal_context_chars, int)
            or isinstance(goal_context_chars, bool)
            or goal_context_chars < 1
        ):
            raise ValueError("goal_context_chars must be a positive integer")
        self.goal_context_chars = goal_context_chars
        # Freeze the executor/replanner view once so all policy arms receive the same
        # task context even if module configuration changes while a run is active.
        self.goal_context = user_prompt[:goal_context_chars]
        return injected_settings

    def _bind_dependencies(self, cfg, deps):
        """Bind one client/executor/clock while preserving workspace ownership."""
        # Dependency seams (issue #40): a pinned llm config builds this run's
        # own client, and injected sinks own the matching client telemetry.
        # Transport still resolves requests.post at call time. The ordinary
        # module facade remains patchable for compatibility, while its settings
        # no longer change between calls in one run.
        if deps.llm_client is not None:
            self._client = deps.llm_client
        elif cfg.llm is not None or deps.log_sink is not None or deps.event_sink is not None:
            self._client = self._hooks.make_client(
                settings=self._llm_meta,
                log_sink=deps.log_sink,
                event_sink=deps.event_sink,
            )
        else:
            self._client = self._hooks.make_frozen_client(self._llm_meta)
        # An injected executor that names a workspace must name this run's
        # workspace (Codex P1, PR #65): otherwise actions would mutate one
        # directory while the result identifies another. Scripted stand-ins
        # without a working_dir attribute stay accepted.
        executor_dir = getattr(deps.action_executor, "working_dir", None)
        if (
            executor_dir is not None
            and Path(executor_dir).resolve() != Path(self.working_dir).resolve()
        ):
            raise ValueError(
                "action_executor is bound to a different directory than the run workspace"
            )
        self._action_executor = deps.action_executor
        self._clock = self._hooks.clock_factory() if deps.clock is None else deps.clock
        self._log_sink = deps.log_sink
        self._event_sink = deps.event_sink

    def _configure_policies(self, cfg, deps, defaults, injected_settings):
        """Resolve provenance, execution policy and guards in their original order."""
        # Provenance of the hashed LLM identity (PR #72 review): an injected
        # duck-typed client without settings leaves the payload describing
        # the module snapshot, so the record must say the identity is opaque
        # rather than combining unlike runs under one hash.
        if deps.llm_client is not None:
            self._llm_provenance = (
                "injected_client_settings" if injected_settings is not None else "injected_opaque"
            )
        elif cfg.llm is not None:
            self._llm_provenance = "pinned_config"
        else:
            self._llm_provenance = "module_snapshot"
        self._policy = {
            "allow_system_installs": self._config_value(cfg, defaults, "allow_system_installs"),
            "allow_network": self._config_value(cfg, defaults, "allow_network"),
        }
        # Outcome-affecting settings resolve once here (issue #68): validation
        # mode, the #41 compile-repair arm, and the guard thresholds are
        # frozen for the run — mid-run changes to the module globals cannot
        # change this run's policy, and the hash below pins what actually ran.
        self.final_validate = self._config_value(cfg, defaults, "final_validate")
        self.compile_repair = self._config_value(cfg, defaults, "compile_repair")
        step_policy = self._config_value(cfg, defaults, "step_policy")
        if step_policy not in self._hooks.step_policies():
            raise ValueError(f"step_policy must be one of {', '.join(STEP_POLICIES)}")
        self.guards = GuardThresholds(
            write_pressure_observations=self._config_value(
                cfg, defaults, "write_pressure_observations"
            ),
            observe_tail_reserve=self._config_value(cfg, defaults, "observe_tail_reserve"),
            rewrite_pressure_writes=self._config_value(cfg, defaults, "rewrite_pressure_writes"),
            rewrite_skip_writes=self._config_value(cfg, defaults, "rewrite_skip_writes"),
            max_task_local_replans=self._config_value(cfg, defaults, "max_task_local_replans"),
        )
        return step_policy

    def _freeze_call_contract(self, step_policy):
        """Snapshot budgets and the credential-free metadata/hash once per run."""
        meta = self._llm_meta
        self._timeouts = {
            "planner_initial": meta.timeout,
            "planner_replan": meta.replan_timeout,
            "executor": meta.timeout,
            "task_replan": meta.timeout,
            "final_validation": meta.timeout,
        }
        self._retries = {
            "planner": meta.max_retries,
            "executor": meta.max_retries,
            # These two deliberately remain single-shot calls.
            "task_replan": 0,
            "final_validation": 0,
        }
        capabilities = meta.resolved_capability_profile()
        token_budgets = {
            "step_tokens": capabilities.step_tokens,
            "step_write_tokens": capabilities.step_write_tokens,
            "planner_max_tokens": capabilities.planner_tokens,
            "task_replan_max_tokens": capabilities.task_replan_tokens,
            "final_validation_max_tokens": capabilities.validation_tokens,
        }
        for name, value in token_budgets.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self._config_payload = {
            "backend": meta.backend,
            # The endpoint identity and request deadline are outcome-affecting
            # (PR #72 review): two local servers or two timeouts must not share
            # a hash. `api` is operator-supplied endpoint configuration; the
            # Authorization credential never rides in it.
            "api": meta.api,
            "model": meta.model,
            "capability_profile": capabilities.describe(),
            # The executor transport is native tool calls; the constant stays
            # in the hash-logged record so run configurations remain
            # distinguishable from pre-removal JSON-envelope runs.
            "action_transport": ACTION_TRANSPORT,
            "provider": meta.provider if meta.backend == "openrouter" else "",
            "reasoning_effort": meta.reasoning_effort if meta.backend == "openrouter" else "",
            "allow_provider_fallbacks": meta.allow_fallbacks,
            "require_provider_parameters": meta.require_parameters,
            "timeout_s": meta.timeout,
            "timeouts_s": dict(self._timeouts),
            "retry_budgets": dict(self._retries),
            "llm_provenance": self._llm_provenance,
            "policy": dict(self._policy),
            "reasoning_policy": self.reasoning_policy,
            "final_validate": self.final_validate,
            "compile_repair": self.compile_repair,
            "step_policy": step_policy,
            "guards": self.guards.describe(),
            "budgets": {
                **token_budgets,
                "llm_max_retries": meta.max_retries,
                # OpenRouter reasoning tokens share the HTTP completion
                # allowance; request construction floors max_tokens by the
                # selected effort using this frozen table.
                "reasoning_token_floors": meta.reasoning_token_floor_metadata(),
            },
            "limits": {
                "max_replans": self.max_replans,
                "max_tasks": self.max_tasks,
                "max_steps": self.max_steps,
                "goal_context_chars": self.goal_context_chars,
            },
        }
        self.config_hash = _config_hash(self._config_payload)
        # Resolved token budgets are threaded into every LLM-backed helper
        # call so no outcome-affecting module global is read after run
        # construction (issue #69).
        self._budgets = self._config_payload["budgets"]

    def _emit(self, msg):
        """Console line through the injected sink, defaulting to log()."""
        (self._hooks.log if self._log_sink is None else self._log_sink)(msg)

    def _event(self, event):
        """JSONL event through the injected sink, defaulting to _run_log()."""
        (self._hooks.event if self._event_sink is None else self._event_sink)(event)

    def _llm_kwargs(self):
        """Extra kwargs for LLM-backed helpers; empty on the module facade
        path so patched helpers keep their legacy call signatures."""
        return {} if self._client is None else {"client": self._client}

    def _dispatch(self, action):
        """One normalized dispatch through the action seam (issue #40): the
        injected executor when provided, else the patchable execute()."""
        if self._action_executor is None:
            raw = self._hooks.execute(action, self.working_dir)
        elif isinstance(self._action_executor, ActionExecutor):
            # A supplied built-in executor is still part of the strict typed
            # boundary; subclasses inherit the same parser-first dispatch.
            raw = self._action_executor.dispatch(action).to_dict()
        else:
            # RunDependencies' injected-executor seam predates typed actions
            # and documents the controller's legacy optional-string
            # projection.  Keep it only at this trusted compatibility seam;
            # model decode and the built-in ActionExecutor both receive the
            # strict envelope, so cross-action fields remain rejected.
            # Deterministic recovery also enters through this seam with an
            # ordinary mapping. Normalize it through the same strict parser
            # before projecting, rather than assuming a typed envelope or
            # forwarding an unvalidated mapping to a duck-typed executor.
            normalized = parse_action_envelope(action)
            if isinstance(normalized, ActionProtocolError):
                return ActionResult(False, normalized.message, normalized.error_type)
            projected = normalized.to_dict()
            for field_name in ("arg", "content", "reasoning", "find", "replace"):
                projected.setdefault(field_name, "")
            raw = self._action_executor.dispatch(projected).to_dict()
        return ActionResult.from_dict(raw)

    def config_metadata(self):
        """Resolved immutable run configuration for the structured result.

        A fresh projection of the hash-logged payload — the same fields the
        ``run_start`` event carries — plus ``config_hash``. Never includes
        credentials."""
        metadata = json.loads(json.dumps(self._config_payload))
        metadata["config_hash"] = self.config_hash
        return metadata

    def run(self):
        """Drive planning, task attempts, validation, and finalization.

        The one typed :class:`RunOutcome` built at the terminal site is
        projected into both the ``run_end`` event and the structured
        result, so the two records cannot disagree."""
        self._log_run_start()
        self._preflight()
        outcome = None
        for replan in range(self.max_replans):
            tasks = self._plan(replan)
            if tasks is None:
                continue  # consumes a plan attempt
            if self._execute_tasks(tasks):
                outcome = self.completion.try_finish(replan)
                if outcome is not None:
                    break
        if outcome is None:
            outcome = self.completion.exhausted()
        self._event(outcome.run_end_event())
        return {
            "status": outcome.status,
            "state": self.state,
            "log": self.history,
            "outcome": outcome.describe(),
        }

    def _log_run_start(self):
        self._emit(f"Prompt: {self.user_prompt}")
        self._emit(f"Working directory: {self.working_dir}")
        # The full resolved configuration — including the #41 compile-repair
        # arm, whose provenance zero repair receipts cannot reconstruct — and
        # its hash are pinned into the run record before any model call.
        self._event(
            {
                "event": "run_start",
                "prompt": self.user_prompt,
                "working_dir": self.working_dir,
                **self.config_metadata(),
            }
        )

    def _preflight(self):
        # Preflight: probe environment and set the run's resolved policy
        env = self._hooks.preflight(self.working_dir)
        self.state["environment"] = env
        self.state["policy"] = dict(self._policy)
        self._emit(f"Environment: platform={env['platform']} arch={env['arch']}")
        self._emit(f"Available tools: {env['available_tools']}")
        if env["missing_tools"]:
            self._emit(f"Missing tools: {env['missing_tools']}")
        self._emit(f"Package managers: {env['package_managers']}")
        self._emit(f"Policy: allow_system_installs={self.state['policy']['allow_system_installs']}")

    def _plan(self, replan):
        """One planning attempt; returns the task list or None on failure."""
        self._emit("=" * 40)
        t_plan = self._clock()
        self._emit(f"Planning (attempt {replan + 1}/{self.max_replans})...")
        self.state["planning_attempt"] = replan
        try:
            plan = self._hooks.get_plan(
                self.user_prompt,
                self.state,
                max_tokens=self._budgets["planner_max_tokens"],
                max_tasks=self.max_tasks,
                timeout=self._timeouts["planner_initial"],
                replan_timeout=self._timeouts["planner_replan"],
                max_retries=self._retries["planner"],
                **self._llm_kwargs(),
            )
        except (LLMTransportError, KeyError) as e:
            self._emit(f"  Planner transport error: {e}")
            self.state["errors"].append(f"[unknown] Planner transport error: {str(e)[:100]}")
            self.history.append({"event": "plan_error", "replan": replan, "error": str(e)[:200]})
            self._event(
                {
                    "event": "plan_error",
                    "replan": replan,
                    "error": str(e)[:200],
                    "wall_s": round(self._clock() - t_plan, 2),
                }
            )
            return None
        except json.JSONDecodeError:
            # The client already retried the plan schema (issue #68); a
            # persistently malformed reply consumes this planning attempt.
            parsed = None
        else:
            parsed = PlanResponse.parse(plan, self.max_tasks)
        if parsed is None:
            error = "[malformed_plan] planner returned no valid tasks"
            self.state["errors"].append(error)
            self._emit(f"  Planner contract error: {error}")
            self.history.append({"event": "plan_error", "replan": replan, "error": error})
            self._event(
                {
                    "event": "plan_error",
                    "replan": replan,
                    "error": error,
                    "wall_s": round(self._clock() - t_plan, 2),
                }
            )
            return None
        tasks = list(parsed.tasks)
        self.state["errors"] = []  # reset errors each replan; planner already saw them
        plan_wall = self._clock() - t_plan
        self._emit(f"Plan ({plan_wall:.1f}s, planner_wall_time={plan_wall:.1f}s): {tasks}")
        self.history.append({"event": "plan", "replan": replan, "tasks": tasks})
        self._event(
            {"event": "plan", "replan": replan, "tasks": tasks, "wall_s": round(plan_wall, 2)}
        )
        return tasks

    def _execute_tasks(self, tasks):
        """Run the plan's tasks in order; True when every task completed."""
        all_done = True
        for i, task in enumerate(tasks):
            # Carry over last step from previous task so executor has cross-task context
            prev_last = self.state["last_steps"][-1:] if self.state.get("last_steps") else []
            t_task = self._clock()
            # Scope for no_write_executed: an earlier task's write must not
            # mask a stall in this one.
            self.state["task_start_step_count"] = len(self.state["all_steps"])
            task, task_done, task_steps = self._run_task(i, task, tasks, prev_last)
            if task_done:
                blocker = self.obligations.completion_blocker()
                if blocker is not None:
                    incomplete_name, recovery_arg, _append_allowed = blocker
                    self.state["errors"].append(
                        f"[incomplete_write] {incomplete_name} at "
                        f"{recovery_arg}: completion refused"
                    )
                    self._emit(f"  Task completion refused: {incomplete_name} is incomplete")
                    task_done = False
            if task_done:
                self.state["completed_tasks"].append(task)
                self.state["completed_step_groups"].append(task_steps)
                self._emit(f"  Task complete. ({self._clock() - t_task:.1f}s)")
                self._event(
                    {
                        "event": "task_complete",
                        "task_index": i,
                        "task": task,
                        "wall_s": round(self._clock() - t_task, 2),
                    }
                )
            else:
                all_done = False
                self._emit(f"  Task failed, will replan. ({self._clock() - t_task:.1f}s)")
                self._event(
                    {
                        "event": "task_failed",
                        "task_index": i,
                        "task": task,
                        "wall_s": round(self._clock() - t_task, 2),
                    }
                )
                break
        return all_done

    def _new_attempt(self, task):
        """Fresh attempt state for one task under the run's guard thresholds."""
        return TaskAttemptState(
            task=task,
            wants_write=_is_write_shaped(task),
            write_pressure_observations=self.guards.write_pressure_observations,
        )

    def _run_task(self, i, task, tasks, prev_last):
        """Attempt one task with the run's task-local replan budget.

        Returns ``(task, done, steps)``; ``task`` is the possibly replaced
        task text the run record must carry forward.
        """
        # E11: inner retry loop — try task-local replan before full replan
        attempt = self._new_attempt(task)
        saved_errors = []
        for task_attempt in range(1 + self.guards.max_task_local_replans):
            self.state["current_task"] = task
            self.state["task_index"] = f"{i + 1}/{len(tasks)}"
            self.state["last_steps"] = list(prev_last)
            self._emit(f"--- Task {i + 1}/{len(tasks)}: {task} ---")

            # Reset per-attempt execution state (the task may be a replacement)
            attempt = self._new_attempt(task)
            completed_repair = self._hooks.repair_satisfied(task, self.state)
            if completed_repair and not self.step_policy.allows_deterministic_completion():
                # An automatic completion must pass the same policy gate as a
                # model done: with an open verification obligation the attempt
                # runs normally so the model can verify first.
                completed_repair = None
            if completed_repair:
                self._emit(
                    f"  auto-done (deterministic repair already satisfied task: {completed_repair.get('output', '')[:60]})"
                )
                attempt.steps.append(completed_repair)
                attempt.done = True
                break
            self._run_attempt(i, attempt)

            if attempt.done:
                break  # break task_attempt loop — success

            # E11: try task-local replan before falling through to full replan
            if task_attempt < self.guards.max_task_local_replans:
                saved_errors = list(self.state["errors"])
                t_lr = self._clock()
                replan = self._hooks.replan_task(
                    task,
                    self.state["errors"],
                    self.state["completed_tasks"],
                    self.state,
                    self.goal_context,
                    goal_context_chars=self.goal_context_chars,
                    max_tokens=self._budgets["task_replan_max_tokens"],
                    timeout=self._timeouts["task_replan"],
                    max_retries=self._retries["task_replan"],
                    **self._llm_kwargs(),
                )
                replacement = replan.task
                lr_wall = self._clock() - t_lr
                if replacement:
                    self._emit(f"  Task-local replan ({lr_wall:.1f}s): '{replacement[:60]}'")
                    self._event(
                        {
                            "event": "task_local_replan",
                            "task_index": i,
                            "original": task[:120],
                            "replacement": replacement[:120],
                            "ok": True,
                            "llm_wall_s": round(lr_wall, 2),
                        }
                    )
                    task = replacement
                    tasks[i] = replacement
                    self.state["errors"] = []
                    continue  # retry with replacement
                else:
                    reject_reason = replan.reject_reason or "unknown"
                    self._emit(f"  Task-local replan failed ({lr_wall:.1f}s), will full replan.")
                    self._event(
                        {
                            "event": "task_local_replan",
                            "task_index": i,
                            "original": task[:120],
                            "replacement": None,
                            "ok": False,
                            "llm_wall_s": round(lr_wall, 2),
                            "reject_reason": reject_reason,
                        }
                    )
                    self.state["errors"] = saved_errors
            else:
                # Replacement attempt also failed — merge original errors back
                # so full replan sees both failure contexts
                self.state["errors"] = saved_errors + self.state["errors"]
            # Fall through — task failed, no more local attempts
            break
        return task, attempt.done, attempt.steps

    def _run_attempt(self, i, attempt):
        """One executor pass over the step budget; sets ``attempt.done``."""
        for step in range(self.max_steps):
            ctx = self._select_action(i, attempt, step)
            if ctx is None:
                return
            flow = self._decide_step(ctx, attempt)
            if flow is None:
                flow = self._execute_step(ctx, attempt)
            if flow is _StepFlow.END_ATTEMPT:
                return

    def _select_action(self, i, attempt, step):
        """Ask the executor for one action; None ends the attempt."""
        t_step = self._clock()
        try:
            action = self._hooks.get_step(
                attempt.task,
                self.state,
                goal=self.goal_context,
                step_num=step,
                max_steps=self.max_steps,
                think=attempt.use_think,
                reasoning_policy=self.reasoning_policy,
                reasoning_trigger=attempt.reasoning_trigger,
                goal_context_chars=self.goal_context_chars,
                write_pressure=self.step_policy.write_pressure(attempt),
                validate_pressure=self.step_policy.validate_pressure(attempt),
                step_tokens=self._budgets["step_tokens"],
                timeout=self._timeouts["executor"],
                max_retries=self._retries["executor"],
                **self._llm_kwargs(),
            )
        except LLMTransportError as e:
            self._emit(f"  [{step + 1}] LLM transport error ({self._clock() - t_step:.1f}s): {e}")
            self.state["errors"].append(
                f"[unknown] LLM transport error on task '{attempt.task}': {str(e)[:100]}"
            )
            return None
        except (json.JSONDecodeError, KeyError) as e:
            # Typed parse failures (issue #7): the replanner should know
            # whether the action envelope was truncated at the token budget,
            # of the wrong response type, or simply malformed.
            envelope = getattr(e, "envelope_error", None)
            if envelope is not None:
                etype = envelope
            elif getattr(e, "response_truncated", False):
                etype = "response_truncated"
            elif getattr(e, "malformed_action", False):
                etype = "malformed_action"
            else:
                etype = "unknown"
            self._emit(f"  [{step + 1}] LLM parse error ({self._clock() - t_step:.1f}s) [{etype}]")
            self.state["errors"].append(
                f"[{etype}] LLM parse error on task '{attempt.task}': {str(e)[:100]}"
            )
            self._event(
                {
                    "event": "step_error",
                    "task_index": i,
                    "step": step,
                    "error_type": etype,
                }
            )
            return None
        transport = ActionTransport()
        candidate = action
        if isinstance(action, DecodedAction):
            transport = action.transport
            candidate = action.envelope
        else:
            # Patched/injected clients historically represented trusted
            # decoder metadata as a dictionary key.  Preserve that test and
            # dependency-injection seam while the real model decoder rejects
            # the same key and returns DecodedAction instead.
            if isinstance(candidate, dict) and "content_truncated" in candidate:
                candidate = dict(candidate)
                legacy_truncated = candidate.pop("content_truncated")
                if legacy_truncated is True and candidate.get("action") == "write":
                    transport = ActionTransport(content_truncated=True)
                else:
                    candidate["content_truncated"] = legacy_truncated
        parsed_action = parse_action_envelope(candidate)
        if isinstance(parsed_action, ActionProtocolError):
            # Response-schema rejection before controller accounting (issue
            # #68): an empty, cross-type, or unknown-action envelope never
            # consumes an execution step. The live decode path enforces the
            # same schema with retries; this arm covers injected clients and
            # patched facades.
            raw_name = candidate.get("action") if isinstance(candidate, dict) else None
            label = raw_name if _valid_nonempty_str(raw_name) else "(no action)"
            envelope = parsed_action.error_type
            self._emit(f"  [{step + 1}] rejected [{envelope}]: {label}")
            self.state["errors"].append(f"[{envelope}] {label}: {parsed_action.message}")
            self._event(
                {
                    "event": "step_error",
                    "task_index": i,
                    "step": step,
                    "error_type": envelope,
                }
            )
            return None
        act = parsed_action.name
        self.recorder.selected()
        self._emit(f"  [{step + 1}] {act}: {parsed_action.get('arg', '')[:80]}")
        return _StepContext(
            task_index=i,
            step=step,
            started=t_step,
            action=parsed_action,
            act=act,
            transport=transport,
        )

    def _decide_step(self, ctx, attempt):
        """Sequence the shared invariants and the policy arm; None dispatches.

        Order: write-truncation/obligation preparation and the completion
        blocker are shared invariants; the policy arm's discipline runs
        next; duplicate/stuck loop protection guards every arm last. The
        individual algorithms live on the components, not here (issue #69).
        """
        if ctx.act == "done":
            return self._handle_done(ctx, attempt)
        if ctx.act == "fail":
            reason = ctx.action.get("reasoning", "no reason")
            self._emit(f"  FAIL ({self._clock() - ctx.started:.1f}s): {reason}")
            self.state["errors"].append(f"Task '{attempt.task}': {reason}")
            self.recorder.control(ctx.task_index, ctx.step, ctx.act)
            return _StepFlow.END_ATTEMPT
        flow = self.obligations.prepare(ctx)
        if flow is None:
            flow = self.step_policy.guard_action(ctx, attempt)
        if flow is None:
            flow = self.step_policy.guard_duplicate(ctx, attempt)
        return flow

    def _handle_done(self, ctx, attempt):
        """Accept ``done`` only past the shared blocker and the policy arm."""
        flow = self.obligations.refuse_done(ctx)
        if flow is None:
            flow = self.step_policy.guard_done(ctx, attempt)
        if flow is not None:
            return flow
        attempt.done = True
        self.recorder.control(ctx.task_index, ctx.step, ctx.act)
        return _StepFlow.END_ATTEMPT

    def _execute_step(self, ctx, attempt):
        """Dispatch through the action seam and record the receipt."""
        action, act = ctx.action, ctx.act
        attempt.dup_skip_count = 0  # reset on any non-skipped action
        self.recorder.executed()
        if act in OBSERVE_ACTIONS:
            attempt.observe_executed += 1
        # Normalize the seam's legacy dict once; controller policy below
        # runs on the typed result.
        result = self._dispatch(action)
        if result.ok and act == "write":
            self.obligations.note_successful_write(ctx, action)
        if act not in OBSERVE_ACTIONS and result.ok:
            # Counted only on success (Codex P2, PR #16): a failed mutation
            # must not disarm write pressure or the observation tail reserve.
            attempt.commit_executed += 1
        self.step_policy.note_result(ctx, attempt, result)
        if ctx.truncated_write and result.ok:
            self.obligations.append_resume_anchor(ctx, action, result)
        ok_str = "OK" if result.ok else "FAIL"
        self._emit(f"  -> {ok_str} ({self._clock() - ctx.started:.1f}s): {result.output[:80]}")

        step_entry = self.recorder.record(
            StepReceipt.executed(action, result, self.working_dir, ctx.truncated_write),
            ctx.task_index,
            ctx.step,
            wall_s=round(self._clock() - ctx.started, 2),
        )

        if not result.ok:
            flow = self._recover_failed_step(ctx, attempt, result)
            if flow is None:
                # The typed failure is task evidence (PR #70 review): a task
                # the model completes after observing a failure must carry
                # that observation into completed_step_groups so the final
                # validator sees what the task actually observed.
                attempt.steps.append(step_entry)
            return flow
        attempt.use_think = False
        attempt.reasoning_trigger = "executor"
        attempt.steps.append(step_entry)
        return None

    def _recover_failed_step(self, ctx, attempt, result):
        """Deterministic repair or typed error; a failure never completes.

        The former task-text expected-failure regex completion is removed
        (issue #68): a failing command is typed evidence for the model and
        the replanner, and only an explicit ``done`` can claim the task.
        """
        action, act = ctx.action, ctx.act
        etype = result.error_type or "unknown"
        if act == "shell" and etype == "compile_error":
            repair_action = self._hooks.compile_repair(
                result.output, self.working_dir, action.get("arg", ""), enabled=self.compile_repair
            )
            if repair_action is not None:
                # The repair rule only proposes an action (issue #41); the
                # mutation happens through the ordinary action seam and the
                # one recorder, exactly like a model-selected step.
                repair_result = self._dispatch(repair_action)
                if repair_result.ok:
                    # The policy arm observes the deterministic mutation like
                    # any targeted fix (heuristic: rewrite streak broken;
                    # lifecycle: the repaired target needs verification).
                    self.step_policy.note_deterministic_repair(
                        _mutation_target_key(
                            {"arg": repair_action.get("arg", "")}, self.working_dir
                        )
                    )
                    self._emit(
                        f"  Deterministic repair: {repair_action['reasoning']} "
                        f"in {Path(repair_action['arg']).name}"
                    )
                    attempt.steps.append(
                        self.recorder.record(
                            StepReceipt.deterministic_repair(repair_action, repair_result),
                            ctx.task_index,
                            ctx.step,
                        )
                    )

                    retry_result = self._dispatch(action)
                    self.step_policy.note_deterministic_retry(retry_result)
                    retry_entry = self.recorder.record(
                        StepReceipt.deterministic_retry(action, retry_result),
                        ctx.task_index,
                        ctx.step,
                        wall_s=round(self._clock() - ctx.started, 2),
                    )
                    if retry_result.ok:
                        self._emit(f"  -> OK deterministic retry: {retry_result.output[:80]}")
                        attempt.steps.append(retry_entry)
                        attempt.use_think = False
                        attempt.reasoning_trigger = "executor"
                        return _StepFlow.NEXT_STEP
                    self._emit(f"  -> FAIL deterministic retry: {retry_result.output[:80]}")
                    result = retry_result
                    etype = result.error_type or "unknown"
                else:
                    # A refused repair dispatch mutated nothing, but the
                    # attempt itself is recorded with its real failed result
                    # so history/JSONL show the dispatch; the original typed
                    # compile error stands for recovery.
                    self._emit(f"  Deterministic repair failed: {repair_result.output[:80]}")
                    self.recorder.record(
                        StepReceipt.deterministic_repair(repair_action, repair_result),
                        ctx.task_index,
                        ctx.step,
                    )

        err_output = result.output[:100]
        hint = _RECOVERY_HINTS.get(etype)
        if hint:
            err_output = f"{err_output} → {hint}"
            self.recorder.append_recovery_hint(hint)
        self.state["errors"].append(f"[{etype}] {act} {action.get('arg', '')[:60]}: {err_output}")
        attempt.use_think = etype not in _NO_THINK_ERRORS
        attempt.reasoning_trigger = f"execution_error:{etype}"
        return None
