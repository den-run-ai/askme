#!/usr/bin/env python3
"""Minimal self-contained agent. Takes a user prompt, plans, executes, replans on failure.
Requires: requests. Expects llama-server on localhost:8080."""

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from dataclasses import replace as _dataclass_replace
from enum import Enum
from pathlib import Path
from typing import Any, NamedTuple

import requests

import actions as _actions
from actions import (
    ACTION_SPECS,
    OBSERVE_ACTIONS,
    OBSERVE_STATE_CHARS,
    SHELL_TIMEOUT_LONG,
    SHELL_TIMEOUT_MAX,
    ActionExecutor,
    ActionResult,
    StepReceipt,
    _get_shell_timeout,
    _mutation_target_key,
    _read_key,
    _step_path,
    _target_recovery_arg,
    _valid_nonempty_str,
)

# Compatibility re-exports (issue #36): the action layer lives in actions.py;
# tests and downstream code keep importing these names from askme.
MAX_RESULT = _actions.MAX_RESULT
READ_CHARS = _actions.READ_CHARS
READ_LIMIT_MAX = _actions.READ_LIMIT_MAX
READ_LINES = _actions.READ_LINES
SEARCH_MAX_CHARS = _actions.SEARCH_MAX_CHARS
SEARCH_MAX_FILES = _actions.SEARCH_MAX_FILES
SEARCH_MAX_MATCHES = _actions.SEARCH_MAX_MATCHES
SHELL_TIMEOUT = _actions.SHELL_TIMEOUT
TREE_MAX_CHARS = _actions.TREE_MAX_CHARS
TREE_MAX_DEPTH = _actions.TREE_MAX_DEPTH
TREE_MAX_ENTRIES = _actions.TREE_MAX_ENTRIES
classify_error = _actions.classify_error


def log(msg):
    """Timestamped print for real-time monitoring."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _load_env():
    """Load .env from script directory if it exists."""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

# Baseline explicit-reasoning effort for always-on reasoning models
# (e.g. openai/gpt-oss-20b). Harmony-format models expose low/medium/high
# effort but no off switch, so the reasoning-disabled request contract below
# would leave their effort at the provider default on every call. When set,
# every OpenRouter request carries at least this effort; gated escalation can
# raise it but never lower it, and AGENT_REASONING_POLICY=off pins calls to
# exactly this level. Leave unset for hybrid models like Gemma 4, where
# reasoning stays off unless the harness asks.
_EFFORT_RANK = {"low": 0, "medium": 1, "high": 2}
# Reasoning tokens count against max_tokens with Parasail-class providers,
# despite OpenRouter docs claiming they're separate. Floor the budget per
# requested effort to compensate (medium/high keep the pre-existing bumps).
_EFFORT_TOKEN_FLOOR = {"low": 1024, "medium": 1536, "high": 2048}


def _parse_reasoning_effort(raw):
    effort = (raw or "").strip().lower()
    if effort and effort not in _EFFORT_RANK:
        raise ValueError("OPENROUTER_REASONING_EFFORT must be low, medium, or high (or unset)")
    return effort


LLM_TIMEOUT = 120  # seconds; covers slow first-token on local LLM
LLM_TIMEOUT_REPLAN = 180  # replans carry heavier state + thinking

OPENROUTER_CHAT_API = "https://openrouter.ai/api/v1/chat/completions"
_OPENROUTER_DEFAULT_MODEL = "google/gemma-4-26b-a4b-it"


def _step_write_tokens(backend):
    """Backend-shaped retry budget for truncated write/edit payloads.

    The 512-token local bound is a wall-clock product constraint at ~7 tok/s;
    OpenRouter payloads get room for whole files (issue #15). The bound must
    follow the client's backend, not the process-wide import-time backend
    (Codex P2, PR #61)."""
    return 8192 if backend == "openrouter" else 512


def _step_tokens(backend):
    """Backend-shaped executor step budget (issue #15).

    Small caps are a wall-clock necessity at ~7 tok/s locally but a pure
    artifact on OpenRouter, where an implementation file can never fit
    under them. Like the write retry budget, the bound must follow the
    client's backend for pinned per-run configurations (issue #40)."""
    return 4096 if backend == "openrouter" else 256


@dataclass(frozen=True)
class LLMSettings:
    """Immutable client-local LLM configuration (issue #37).

    One derivation (``from_env``) replaces the former import-time global
    branching; the module-level names below remain the env-derived
    compatibility surface that tests patch and ``ask_llm`` snapshots per
    call (``current``). Distinct settings let two clients share one process
    without global leakage. Run-level configuration and composition stay
    with issue #40.
    """

    backend: str
    api: str
    model: str
    api_key: str
    provider: str
    allow_fallbacks: bool
    require_parameters: bool
    reasoning_effort: str
    timeout: int
    # None derives the truncated-write retry budget from `backend`; `current`
    # pins the module global so patched values keep reaching the facade.
    step_write_tokens: int | None = None
    # None derives the executor step budget from `backend`. These two are the
    # per-run capability budgets (issue #68): pin them to freeze a run's
    # output budgets independent of the backend-name heuristic.
    step_token_budget: int | None = None

    def write_retry_tokens(self):
        """Decode-retry budget for truncated write/edit payloads."""
        if self.step_write_tokens is not None:
            return self.step_write_tokens
        return _step_write_tokens(self.backend)

    def step_tokens(self):
        """Executor step budget for this run (issues #15/#40/#68)."""
        if self.step_token_budget is not None:
            return self.step_token_budget
        return _step_tokens(self.backend)

    @classmethod
    def from_env(cls, env=None):
        """Derive settings from an environment mapping (default os.environ)."""
        e = os.environ if env is None else env
        backend = e.get("LLM_BACKEND", "local")  # "local" or "openrouter"
        if backend == "openrouter":
            api = OPENROUTER_CHAT_API
            model = e.get("OPENROUTER_MODEL", _OPENROUTER_DEFAULT_MODEL)
        else:
            api = e.get("LLM_API_URL", "http://localhost:8080/v1/chat/completions")
            model = e.get("LLM_MODEL", "gemma-4-e4b")
        return cls(
            backend=backend,
            api=api,
            model=model,
            api_key=e.get("OPENROUTER_API_KEY", ""),
            provider=e.get("OPENROUTER_PROVIDER", "Parasail").strip(),
            allow_fallbacks=e.get("OPENROUTER_ALLOW_FALLBACKS", "1") == "1",
            require_parameters=e.get("OPENROUTER_REQUIRE_PARAMETERS", "0") == "1",
            reasoning_effort=_parse_reasoning_effort(e.get("OPENROUTER_REASONING_EFFORT")),
            timeout=LLM_TIMEOUT,
        )

    @classmethod
    def current(cls):
        """Snapshot the module-level (patchable) configuration."""
        return cls(
            backend=LLM_BACKEND,
            api=API,
            model=MODEL,
            api_key=OPENROUTER_API_KEY,
            provider=OPENROUTER_PROVIDER,
            allow_fallbacks=OPENROUTER_ALLOW_FALLBACKS,
            require_parameters=OPENROUTER_REQUIRE_PARAMETERS,
            reasoning_effort=OPENROUTER_REASONING_EFFORT,
            timeout=LLM_TIMEOUT,
            step_write_tokens=STEP_WRITE_TOKENS,
            step_token_budget=STEP_TOKENS,
        )


# Backend config: set LLM_BACKEND=openrouter to use OpenRouter API. These
# module-level mirrors of the one from_env derivation remain the
# compatibility surface that tests and the integration helpers patch;
# ask_llm snapshots them per call via LLMSettings.current().
_DEFAULT_LLM_SETTINGS = LLMSettings.from_env()
LLM_BACKEND = _DEFAULT_LLM_SETTINGS.backend  # "local" or "openrouter"
OPENROUTER_API_KEY = _DEFAULT_LLM_SETTINGS.api_key
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", _OPENROUTER_DEFAULT_MODEL)
OPENROUTER_PROVIDER = _DEFAULT_LLM_SETTINGS.provider
OPENROUTER_ALLOW_FALLBACKS = _DEFAULT_LLM_SETTINGS.allow_fallbacks
OPENROUTER_REQUIRE_PARAMETERS = _DEFAULT_LLM_SETTINGS.require_parameters
OPENROUTER_REASONING_EFFORT = _DEFAULT_LLM_SETTINGS.reasoning_effort
API = _DEFAULT_LLM_SETTINGS.api
MODEL = _DEFAULT_LLM_SETTINGS.model


def _merge_effort(think_level, baseline=None):
    """Effort to request from OpenRouter: the gated level, raised to the
    baseline (the module default when unspecified). Falsy result means
    request reasoning disabled (hybrid contract)."""
    if baseline is None:
        baseline = OPENROUTER_REASONING_EFFORT
    if baseline and think_level:
        return max(baseline, think_level, key=_EFFORT_RANK.__getitem__)
    return baseline or think_level


# Execution policy — controls what the agent is allowed to do
ALLOW_SYSTEM_INSTALLS = os.environ.get("ALLOW_SYSTEM_INSTALLS", "0") == "1"
ALLOW_NETWORK = os.environ.get("ALLOW_NETWORK", "1") == "1"
# Ablation switch for the tracked #41 C-header repair path. Default on keeps
# current behavior; the preregistered ablation (docs/ablation-compile-repair.md)
# runs its off arm with AGENT_COMPILE_REPAIR=0 at one pinned revision.
COMPILE_REPAIR_ENABLED = os.environ.get("AGENT_COMPILE_REPAIR", "1") == "1"

PROBE_TOOLS = ["python3", "go", "node", "gcc", "cc", "make", "cargo", "rustc", "java", "javac"]
PROBE_PKG_MANAGERS = ["brew", "apt-get", "dnf", "pacman", "apk"]


def preflight_probe(working_dir="."):
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
    for tool in PROBE_TOOLS:
        if shutil.which(tool):
            available.append(tool)
        else:
            missing.append(tool)
    env["available_tools"] = available
    env["missing_tools"] = missing
    # Package managers
    pkg_managers = []
    for pm in PROBE_PKG_MANAGERS:
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


def get_policy():
    """Return execution policy dict for planner/executor state."""
    return {
        "allow_system_installs": ALLOW_SYSTEM_INSTALLS,
        "allow_network": ALLOW_NETWORK,
    }


class LLMTransportError(Exception):
    """Raised when all LLM request retries are exhausted."""

    pass


MAX_REPLANS = 3  # Total planning attempts (initial plan + up to 2 replans)
MAX_TASKS = 10
MAX_STEPS = 10
MAX_STEP_HISTORY = 3  # sliding window of recent steps sent to executor

# Write-forcing executor policy (issue #15): on a write-shaped task,
# observation may not consume the whole step budget — the 2026-08-01 Qwen
# canary spent all 27 executed steps on tree/read and never selected a write.
WRITE_PRESSURE_OBSERVATIONS = 3  # observation steps before the executor must commit
OBSERVE_TAIL_RESERVE = 3  # final steps per attempt reserved for commitment
# Validate-after-write policy (revision 4): on a write-shaped task, repeated
# whole-file rewrites of the same target may not consume the step budget —
# the 2026-08-01 v6 Gemma canary rewrote one file 18 times without ever
# verifying it or emitting done.
REWRITE_PRESSURE_WRITES = 2  # same-target full writes before the executor must verify
REWRITE_SKIP_WRITES = 3  # same-target full writes after which further rewrites are skipped
# "include" dropped (Codex P2, PR #16): it matched passive phrasing like
# "files that include deprecated.h" and misclassified observation tasks.
_WRITE_TASK_RE = re.compile(
    r"\b(implement|write|create|patch|add|fix|edit|update|replace|insert)\b",
    re.I,
)
# A leading observation verb marks inspection intent even when a mutation
# verb appears later ("find where to add the import").
_OBSERVE_TASK_RE = re.compile(
    r"^\s*(find|search|locate|inspect|read|list|review|explore|examine|look|check|show)\b",
    re.I,
)


def _is_write_shaped(task):
    return bool(task) and bool(_WRITE_TASK_RE.search(task)) and not _OBSERVE_TASK_RE.match(task)


# Observation-action budgets (issue #7): reads/searches/trees are the navigation
# surface for app development; they get their own bounded windows so large repos
# stay navigable without blowing up executor state.
# Backend-aware output budgets (issue #15): small caps are a wall-clock
# necessity at ~7 tok/s locally but a pure artifact on OpenRouter, where an
# 8KB implementation file is ~3000 tokens and can never fit under 512/1536.
# Local values are unchanged — the local path keeps chunked append instead.
STEP_TOKENS = _step_tokens(LLM_BACKEND)
# Retry budget when a truncated write/edit payload fails to parse.
STEP_WRITE_TOKENS = _step_write_tokens(LLM_BACKEND)
PLANNER_MAX_TOKENS = 768  # 256 thinking + 512 output; shared budget on Parasail/bf16
REASONING_POLICIES = ("gated", "off")
DEFAULT_REASONING_POLICY = os.environ.get("AGENT_REASONING_POLICY", "gated").strip().lower()
if DEFAULT_REASONING_POLICY not in REASONING_POLICIES:
    raise ValueError(f"AGENT_REASONING_POLICY must be one of {', '.join(REASONING_POLICIES)}")

# Step-policy arms (issues #31/#68): "heuristic" is today's guard/counter
# baseline; "lifecycle" is the explicit inspect → modify → verify → finish
# alternative. The arm is outcome-affecting per-run configuration — it enters
# the hash-logged config — and only #63/#64 measurements may retire an arm.
STEP_POLICIES = ("heuristic", "lifecycle")


def _validated_step_policy(value):
    """Normalize an AGENT_STEP_POLICY value or raise on an unknown arm."""
    policy = value.strip().lower()
    if policy not in STEP_POLICIES:
        raise ValueError(f"AGENT_STEP_POLICY must be one of {', '.join(STEP_POLICIES)}")
    return policy


DEFAULT_STEP_POLICY = _validated_step_policy(os.environ.get("AGENT_STEP_POLICY", "heuristic"))

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

SYSTEM_VALIDATE = """You are a completion validator. Given a goal, completed tasks with their execution evidence, and the current working directory listing, determine if the goal was fully achieved.

Examine the evidence carefully:
- Did all required files get created?
- Did compilation/build succeed?
- Did the program run and produce correct output?
- Were all parts of the goal addressed?

Output ONLY valid JSON. No markdown, no explanation.
Format: {"valid": true} or {"valid": false, "reason": "what is missing or wrong", "missing": ["specific missing items"]}"""

# Final validation config
FINAL_VALIDATE = os.environ.get("AGENT_FINAL_VALIDATE", "auto")

# Structured run log: when set to a path, each run appends JSONL events.
# Surfaces tokens / wall times / plan+step events for PERFORMANCE.md comparisons.
RUN_LOG_PATH = os.environ.get("AGENT_RUN_LOG", "")


def _run_log(event):
    """Append one JSON event to AGENT_RUN_LOG. Never fails the run."""
    if not RUN_LOG_PATH:
        return
    try:
        event = {"ts": time.time(), **event}
        with open(RUN_LOG_PATH, "a") as f:
            f.write(json.dumps(event, default=str) + "\n")
    except Exception:
        pass


_VALIDATE_KEYWORDS = re.compile(
    r"\b(compile|build|test|run|execute|fix|debug|repair|verify|install|server|api|script|program)\b",
    re.I,
)

SYSTEM_STEP = """Executor. ONE action per turn as JSON. Output ONLY valid JSON. No markdown, no explanation.
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
Actions: __ACTION_NAMES__.
read: initial pages take "offset"/"limit" (1-based lines); continuation pages must echo
      the output's "cursor", "limit", and "sha256". Cursors count Unicode code points.
search: literal pattern in "arg", optional "path" (default "."); bounded matches
tree: directory in "arg" (default "."); bounded listing
write: whole file; add "append":true to append the next chunk instead
write content may follow the JSON between sentinel lines instead of "content":
{"action":"write","arg":"f.py","reasoning":"..."}
<<<CONTENT
raw file lines, no escaping
CONTENT>>>
edit: {"action":"edit","arg":"file","find":"exact old","replace":"new","reasoning":"..."}
Format: {"action":"...","arg":"...","content":"...","reasoning":"..."}""".replace(
    "__ACTION_NAMES__", ", ".join(ACTION_SPECS)
)


MAX_LLM_RETRIES = 2


def _repair_json(text):
    """Try to salvage broken JSON from truncation artifacts. Returns dict or None."""
    if not text or "{" not in text:
        return None
    # Strip trailing prose after a complete JSON object (model commentary after })
    # Find the last } and discard everything after it
    last_brace = text.rfind("}")
    if last_brace >= 0 and last_brace < len(text) - 1:
        candidate = text[: last_brace + 1]
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass  # fall through to other repairs
    # Strip trailing incomplete key-value pair (truncation mid-field)
    text = re.sub(r',\s*"[^"]*$', "", text)
    # Strip trailing incomplete value after a key (e.g. "key": "val...)
    text = re.sub(r',\s*"[^"]*":\s*"?[^"}\]]*$', "", text)
    # Strip trailing commas before close
    text = re.sub(r",\s*}", "}", text)
    # Close missing braces
    opens = text.count("{") - text.count("}")
    if opens > 0:
        text = text + "}" * opens
    elif opens < 0:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _validate_action_contract(obj):
    """Return True for planner/validator dicts and complete action dicts.

    Required fields and per-action contracts come from ACTION_SPECS (issue
    #36). Unknown actions pass here so the run loop can record them as an
    executed step with a typed dispatch error, not a decode failure.
    """
    if not isinstance(obj, dict) or "action" not in obj:
        return True
    act = obj.get("action", "")
    if not isinstance(act, str):
        # Valid JSON can carry an unhashable action name ({"action": []});
        # reject it as malformed instead of letting the registry lookup raise.
        return False
    spec = ACTION_SPECS.get(act)
    if spec is None:
        return True
    if not all(_valid_nonempty_str(obj.get(name)) for name in spec.requires):
        return False
    return spec.contract(obj) if spec.contract else True


def _accept_or_raise(obj, text):
    if _validate_action_contract(obj):
        return obj
    raise json.JSONDecodeError("Incomplete action JSON", text, 0)


# --- Response-specific schemas and records (issue #68) ---
#
# Every LLM call site names the response type it expects; the client rejects
# an envelope of the wrong type with its normal parse-retry policy, and the
# call site converts the accepted dict into a typed record before any
# controller accounting. The permissive shared decoder is thereby split into
# per-response contracts: a plan cannot reach the executor, a stray action
# cannot reach the planner, and an unknown action never consumes an
# execution step.


def _action_envelope_error(obj):
    """Typed rejection reason for a non-dispatchable executor reply.

    Returns None for an action envelope — a known action name whose fields
    satisfy the registry contract, including the control actions — and the
    typed error otherwise: empty and cross-type envelopes (a plan or
    validator reply at the action seam) are ``malformed_action``; a
    well-formed envelope naming an unknown action is ``unknown_action``.
    """
    if not isinstance(obj, dict) or not obj:
        return "malformed_action"
    act = obj.get("action")
    if not _valid_nonempty_str(act):
        return "malformed_action"
    spec = ACTION_SPECS.get(act)
    if spec is None:
        return "unknown_action"
    if not all(_valid_nonempty_str(obj.get(name)) for name in spec.requires):
        return "malformed_action"
    if spec.contract is not None and not spec.contract(obj):
        return "malformed_action"
    return None


@dataclass(frozen=True)
class PlanResponse:
    """Typed planner reply: a bounded, non-empty task list."""

    tasks: tuple[str, ...]

    @classmethod
    def parse(cls, obj, max_tasks):
        """Accept a planner reply, or return None for a malformed envelope.

        Mirrors the historical call-site contract: the list is truncated to
        ``max_tasks`` first and every kept entry must be a non-empty string.
        """
        if not isinstance(obj, dict):
            return None
        raw = obj.get("tasks")
        if not isinstance(raw, list):
            return None
        tasks = raw[:max_tasks]
        if not tasks or any(not _valid_nonempty_str(task) for task in tasks):
            return None
        return cls(tasks=tuple(tasks))


@dataclass(frozen=True)
class TaskReplanResponse:
    """Typed task-replanner reply: one replacement task description."""

    task: str

    @classmethod
    def parse(cls, obj):
        """Accept a replan reply, or return None for a malformed envelope."""
        if not isinstance(obj, dict):
            return None
        task = obj.get("task")
        if not isinstance(task, str) or not task:
            return None
        return cls(task=task)


@dataclass(frozen=True)
class ValidationResponse:
    """Typed final-validation verdict.

    ``valid`` is the verdict; ``deterministic`` marks a verdict derived from
    the deterministic completion check rather than the LLM validator. An
    unavailable or malformed validator reply never becomes a
    ValidationResponse — the caller sees None and must treat the run as
    unverified, not passed (issue #68)."""

    valid: bool
    reason: str = ""
    missing: tuple[str, ...] = ()
    deterministic: bool = False

    @classmethod
    def parse(cls, obj):
        """Accept a validator reply, or return None for a malformed envelope."""
        if not isinstance(obj, dict) or not isinstance(obj.get("valid"), bool):
            return None
        reason = obj.get("reason")
        missing = obj.get("missing")
        return cls(
            valid=obj["valid"],
            reason=reason if isinstance(reason, str) else "",
            missing=tuple(m for m in missing if _valid_nonempty_str(m))
            if isinstance(missing, list)
            else (),
        )


# Decode-time response schemas by expected type: True accepts the envelope.
RESPONSE_SCHEMAS = {
    "plan": lambda obj: PlanResponse.parse(obj, MAX_TASKS) is not None,
    "action": lambda obj: _action_envelope_error(obj) is None,
    "task_replan": lambda obj: TaskReplanResponse.parse(obj) is not None,
    "validation": lambda obj: ValidationResponse.parse(obj) is not None,
}


_STRICT_JSON_SUFFIX = (
    "Output ONLY the JSON object. No reasoning, no explanation, no text outside the JSON."
)

# Sentinel-framed content transport (issue #15): implementation-scale write
# content travels between sentinel lines after the action JSON instead of
# inside a JSON string — no escaping overhead, and truncation is recoverable:
# a missing closing sentinel at finish_reason=length means "the complete lines
# arrived; continue via chunked append" instead of an all-or-nothing parse
# failure.
CONTENT_OPEN = "<<<CONTENT"
CONTENT_CLOSE = "CONTENT>>>"


def _split_content_block(text):
    """Split a response into (header_text, content, closed).
    content is None when no sentinel block is present. The closing sentinel
    must be flush-left and is matched from the end, so content lines that
    merely resemble it (indented examples, embedded docs) stay content. A
    file whose genuinely-final flush-left line is the sentinel itself cannot
    ride this transport — use JSON "content" or chunked append for that."""
    lines = text.split("\n")
    open_idx = None
    for i, line in enumerate(lines):
        if line.strip() == CONTENT_OPEN:
            open_idx = i
            break
    if open_idx is None:
        return text, None, False
    header = "\n".join(lines[:open_idx])
    for j in range(len(lines) - 1, open_idx, -1):
        if lines[j].rstrip() == CONTENT_CLOSE:
            return header, "\n".join(lines[open_idx + 1 : j]), True
    return header, "\n".join(lines[open_idx + 1 :]), False


# Truncated write/edit payloads are the most common large-output parse failure;
# detect the attempted action so the retry gets a payload-sized budget.
_WRITE_ATTEMPT_RE = re.compile(r'"action"\s*:\s*"(?:write|edit)"')


# LLM client seams (issue #37): reasoning decision, request build, one-shot
# transport, and pure reply decode are independent, individually testable
# steps. ask_llm() below remains the compatibility facade that owns retry
# policy, backoff, and typed errors. Backend settings are read at call time
# so per-test patching of module globals keeps working.


def _reasoning_decision(attempt, think, think_level, reasoning_policy, reasoning_trigger):
    """Per-attempt reasoning escalation: (requested_level, effective_level, trigger).

    E03 contract: an explicit think_level pins every attempt; think=True
    escalates medium -> high, then drops to the strict no-thinking contract on
    the final auto-retry (more thinking doesn't fix truncation/format errors);
    otherwise attempt 1 gets the one reasoning-assisted JSON-contract retry.
    Policy "off" suppresses the effective level entirely — for always-on
    reasoners the OpenRouter baseline effort still applies downstream."""
    if think_level:
        gated = think_level
        requested = think_level
    elif think:
        requested = "adaptive"
        gated = None if attempt >= 2 else ("high" if attempt >= 1 else "medium")
    elif attempt == 1:
        gated = "medium"
        requested = "medium"
    else:
        gated = None
        requested = None
    trigger = "json_retry" if attempt == 1 and not think and not think_level else reasoning_trigger
    effective = gated if reasoning_policy == "gated" else None
    return requested, effective, trigger


def _build_llm_request(messages, budget, effective_think_level, strict, settings=None):
    """Build one backend-specific request: (body, headers, sent_effort).

    `strict` appends the E03 strict-JSON contract as a final user turn after
    backend shaping. Never mutates the caller's message list. `settings`
    defaults to a snapshot of the module-level configuration (issue #37)."""
    cfg = LLMSettings.current() if settings is None else settings
    body = {"model": cfg.model, "messages": messages, "temperature": 0.1, "max_tokens": budget}
    sent_effort = effective_think_level
    if cfg.backend == "openrouter":
        if cfg.provider:
            body["provider"] = {
                "order": [cfg.provider],
                "allow_fallbacks": cfg.allow_fallbacks,
                "require_parameters": cfg.require_parameters,
            }
        # Always-on reasoners: the baseline effort applies to every call —
        # strict E03 retries included, since the model cannot stop
        # reasoning and "no thinking" can only mean the baseline.
        sent_effort = _merge_effort(effective_think_level, baseline=cfg.reasoning_effort)
        if sent_effort:
            body["reasoning"] = {
                "enabled": True,
                "effort": sent_effort,
            }
            body["max_tokens"] = max(budget, _EFFORT_TOKEN_FLOOR[sent_effort])
        else:
            # Some models reason by default. Keep the harness policy model-independent.
            body["reasoning"] = {"enabled": False}
    elif effective_think_level:
        # Local llama-server: prepend <|think|> to system prompt, bump max_tokens
        msgs = list(messages)
        if msgs and msgs[0]["role"] == "system":
            msgs[0] = dict(msgs[0])
            msgs[0]["content"] = "<|think|>\n" + msgs[0]["content"]
        body["messages"] = msgs
        body["max_tokens"] = max(budget, 768 if effective_think_level == "high" else 512)
    if strict:
        msgs = list(body["messages"])
        msgs.append({"role": "user", "content": _STRICT_JSON_SUFFIX})
        body["messages"] = msgs
    headers = {"Content-Type": "application/json"}
    if cfg.backend == "openrouter" and cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
        headers["X-OpenRouter-Metadata"] = "enabled"
    return body, headers, sent_effort


def _llm_http_attempt(body, headers, timeout, post=None, api=None):
    """One HTTP attempt against the configured chat-completions endpoint.

    Pure transport: returns (response_json, None) on success, or
    (None, failure) where failure = {"kind", "detail", "error", "status"}
    classifies the outcome for the caller's retry policy. Kinds:
    "transport" (connection/timeout), "http_retryable" (429/5xx),
    "http_fatal" (other 4xx), "non_json" (unparseable success body).
    `api` defaults to the module-level endpoint (issue #37)."""
    if post is None:
        post = requests.post
    if api is None:
        api = API
    try:
        resp = post(api, json=body, headers=headers, timeout=timeout)
    except (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.RequestException,
    ) as e:
        return None, {
            "kind": "transport",
            "detail": f"{type(e).__name__}: {e}",
            "error": e,
            "status": None,
        }
    sc = resp.status_code
    if sc == 429 or sc >= 500:
        return None, {"kind": "http_retryable", "detail": f"HTTP {sc}", "error": None, "status": sc}
    if 400 <= sc < 500:
        return None, {
            "kind": "http_fatal",
            "detail": f"HTTP {sc}: {resp.text[:200]}",
            "error": None,
            "status": sc,
        }
    try:
        return resp.json(), None
    except ValueError as e:
        return None, {"kind": "non_json", "detail": resp.text[:100], "error": e, "status": sc}


def _extract_message_text(rj):
    """Message text with the OpenRouter empty-content reasoning fallback
    (models may put JSON in reasoning when the token budget is tight)."""
    msg = rj["choices"][0]["message"]
    text = msg.get("content") or ""
    if not text.strip():
        reasoning = msg.get("reasoning_content") or ""
        if not reasoning:
            r = msg.get("reasoning", "")
            reasoning = r.get("content", "") if isinstance(r, dict) else (r or "")
        text = reasoning
    return text


def _decode_action_reply(text, finish_reason):
    """Pure decode of one model reply into a contract-valid dict.

    Owns reasoning/fence stripping, sentinel content extraction, JSON
    extraction/repair, and action-contract validation. Returns
    (obj, cleaned_text, repaired). Raises json.JSONDecodeError carrying a
    .cleaned_text attribute when no valid object can be recovered; retry
    policy and typed classification stay with the caller."""
    # The strip chain below removes a trailing newline; remember it so a
    # truncated sentinel block can keep its last complete line.
    ends_with_newline = text.endswith("\n")
    # Strip <think>...</think> (closed) or <think>... (unclosed, truncated at max_tokens)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"<think>.*", "", text, flags=re.DOTALL).strip()
    # Strip <|channel>...<channel|> blocks (local llama-server thinking format)
    text = re.sub(r"<\|channel\>.*?<channel\|>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"<\|channel\>.*", "", text, flags=re.DOTALL).strip()
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Sentinel content block (issue #15): content rides outside the JSON.
    text, block, block_closed = _split_content_block(text)
    # Try to extract JSON object from anywhere in the text
    if not text.startswith("{") and "{" in text:
        text = text[text.index("{") :]

    def _attach_block(obj):
        if block is not None and isinstance(obj, dict):
            content = block
            if not block_closed and finish_reason == "length":
                obj["content_truncated"] = True
                # The cutoff landed on a line boundary: the last line is
                # complete, not partial — restore the stripped newline so
                # the run loop's partial-line trim keeps it.
                if ends_with_newline:
                    content += "\n"
            obj["content"] = content
        return obj

    try:
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise json.JSONDecodeError(
                "Expected JSON object, got " + type(parsed).__name__, text, 0
            )
        return _accept_or_raise(_attach_block(parsed), text), text, False
    except json.JSONDecodeError as parse_err:
        # E03: attempt mechanical repair before burning a retry
        repaired = _repair_json(text)
        if repaired is not None:
            try:
                return _accept_or_raise(_attach_block(repaired), text), text, True
            except json.JSONDecodeError:
                pass
        setattr(parse_err, "cleaned_text", text)
        raise


def _log_llm_usage(
    rj, sent_effort, attempt, finish_reason, settings=None, log_sink=None, event_sink=None
):
    """Console + JSONL usage/route telemetry for one decoded HTTP success.

    `settings` and the sinks default to the module-level configuration,
    `log`, and `_run_log` (issue #37)."""
    cfg = LLMSettings.current() if settings is None else settings
    emit = log if log_sink is None else log_sink
    record = _run_log if event_sink is None else event_sink
    usage = rj.get("usage", {})
    if not usage:
        return
    metadata = rj.get("openrouter_metadata") or {}
    route = metadata.get("endpoints", {})
    available = route.get("available", []) if isinstance(route, dict) else []
    selected = next(
        (
            endpoint
            for endpoint in available
            if isinstance(endpoint, dict) and endpoint.get("selected")
        ),
        {},
    )
    tok_msg = f"  tokens: prompt={usage.get('prompt_tokens', 0)} completion={usage.get('completion_tokens', 0)} total={usage.get('total_tokens', 0)}"
    if sent_effort:
        tok_msg += f" thinking={sent_effort}"
    emit(tok_msg)
    record(
        {
            "event": "tokens",
            "prompt": usage.get("prompt_tokens", 0),
            "completion": usage.get("completion_tokens", 0),
            "total": usage.get("total_tokens", 0),
            "openrouter_cost": usage.get("cost", 0),
            "model": selected.get("model") or rj.get("model", cfg.model),
            "provider": selected.get("provider") or rj.get("provider", ""),
            "route_attempt": selected.get("attempt"),
            "thinking": sent_effort,
            "finish_reason": finish_reason,
            "attempt": attempt,
        }
    )


class LLMClient:
    """LLM provider client (issue #37): immutable per-client settings plus
    injectable transport `post`, sleeper, and log/event sinks.

    ``ask_llm`` stays the module-level compatibility facade: it snapshots
    the module configuration into a fresh client per call, so callers and
    tests that patch the module globals keep working. Constructing clients
    explicitly gives two backends/models in one process with no global
    leakage; injecting a client into the run composition belongs to #40.
    """

    def __init__(self, settings=None, post=None, sleep=None, log_sink=None, event_sink=None):
        self.settings = LLMSettings.current() if settings is None else settings
        # None means "resolve the module default at call time" so patched
        # requests.post / log / _run_log stay effective for the facade.
        self._post = post
        self._sleep = time.sleep if sleep is None else sleep
        self._log = log if log_sink is None else log_sink
        self._event = _run_log if event_sink is None else event_sink

    def ask(
        self,
        messages,
        max_tokens=256,
        think=False,
        think_level=None,
        max_retries=MAX_LLM_RETRIES,
        raw=False,
        timeout=None,
        reasoning_policy=DEFAULT_REASONING_POLICY,
        reasoning_trigger="unspecified",
        expect=None,
    ):
        """Call the backend and decode one plan/action/validator reply.

        This loop owns only retry/backoff policy, the parse-retry budget
        escalation, and the typed errors callers rely on (LLMTransportError,
        KeyError for API-error bodies, json.JSONDecodeError with
        malformed_action/response_truncated). ``expect`` names the response
        schema this call site accepts (issue #68): a decoded envelope of the
        wrong type — empty, cross-type, or an unknown action — is retried
        like any parse failure and raises typed after the retry budget."""
        if reasoning_policy not in REASONING_POLICIES:
            raise ValueError(f"reasoning_policy must be one of {', '.join(REASONING_POLICIES)}")
        if expect is not None and expect not in RESPONSE_SCHEMAS:
            raise ValueError(f"expect must be one of {', '.join(sorted(RESPONSE_SCHEMAS))}")
        cfg = self.settings
        budget = max_tokens
        for attempt in range(max_retries + 1):
            requested_level, effective_think_level, effective_trigger = _reasoning_decision(
                attempt, think, think_level, reasoning_policy, reasoning_trigger
            )
            self._event(
                {
                    "event": "reasoning_decision",
                    "requested_policy": reasoning_policy,
                    "requested_trigger": effective_trigger,
                    "requested_level": requested_level,
                    "effective_level": effective_think_level,
                    "baseline_effort": (cfg.reasoning_effort or None)
                    if cfg.backend == "openrouter"
                    else None,
                    "attempt": attempt,
                }
            )

            # E03 strict contract on the final auto-retry — suppress reasoning leaks
            body, headers, sent_effort = _build_llm_request(
                messages,
                budget,
                effective_think_level,
                strict=attempt >= 2 and not think_level,
                settings=cfg,
            )
            # One transport attempt; retry/backoff policy is enacted here.
            rj, failure = _llm_http_attempt(
                body, headers, timeout or cfg.timeout, post=self._post, api=cfg.api
            )
            if failure is not None:
                kind = failure["kind"]
                if kind == "http_fatal":
                    # Client errors fail fast: retrying an auth/request-shape bug wastes budget.
                    raise LLMTransportError(failure["detail"])
                if kind == "transport":
                    self._log(f"  Transport error: {failure['detail']}")
                elif kind == "http_retryable":
                    self._log(f"  HTTP {failure['status']}, retrying...")
                else:  # non_json: proxy/gateway glitch returned an unparseable body
                    self._log(f"  Non-JSON response body: {failure['detail']}")
                if attempt < max_retries:
                    self._sleep(1 if attempt == 0 else 3)
                    continue
                if kind == "transport":
                    raise LLMTransportError(
                        f"Transport failed after {max_retries + 1} attempts: {failure['error']}"
                    ) from failure["error"]
                if kind == "http_retryable":
                    raise LLMTransportError(
                        f"HTTP {failure['status']} after {max_retries + 1} attempts"
                    )
                raise LLMTransportError(
                    f"Non-JSON response after {max_retries + 1} attempts"
                ) from failure["error"]
            # Handle API error responses (JSON body with "error" key)
            if "error" in rj:
                self._log(
                    f"  API error: {rj['error'].get('message', rj['error']) if isinstance(rj['error'], dict) else rj['error']}"
                )
                if attempt < max_retries:
                    continue
                raise KeyError(f"API error: {rj['error']}")
            finish_reason = (rj.get("choices") or [{}])[0].get("finish_reason", "")
            _log_llm_usage(
                rj,
                sent_effort,
                attempt,
                finish_reason,
                settings=cfg,
                log_sink=self._log,
                event_sink=self._event,
            )
            if finish_reason == "length":
                self._log("  output hit token budget (finish_reason=length)")
            text = _extract_message_text(rj)
            if raw:
                return text
            try:
                obj, _decoded_text, repaired = _decode_action_reply(text, finish_reason)
            except json.JSONDecodeError as parse_err:
                cleaned = getattr(parse_err, "cleaned_text", "")
                if attempt < max_retries:
                    # Action-specific budget: a truncated write/edit payload
                    # needs room for content, not more reasoning. The bound
                    # follows this client's backend (Codex P2, PR #61).
                    write_budget = cfg.write_retry_tokens()
                    if budget < write_budget and _WRITE_ATTEMPT_RE.search(cleaned):
                        budget = write_budget
                        self._log(f"  write/edit payload budget -> {budget}")
                    think_str = f" thinking={sent_effort}" if sent_effort else ""
                    self._log(
                        f"  [retry {attempt + 1}]{think_str} JSON parse failed, raw: {cleaned[:120]}"
                    )
                    continue
                # Typed classification for the caller (issue #7): output that
                # hit the token budget is a transport failure of the action
                # envelope, not model noise — the recovery differs.
                setattr(parse_err, "malformed_action", True)
                setattr(parse_err, "response_truncated", finish_reason == "length")
                raise
            if repaired:
                self._log(f"  JSON repaired on attempt {attempt}")
            if expect is not None and not RESPONSE_SCHEMAS[expect](obj):
                if attempt < max_retries:
                    self._log(f"  [retry {attempt + 1}] reply failed the {expect} schema")
                    continue
                schema_err = json.JSONDecodeError(
                    f"Reply failed the {expect} response schema", _decoded_text or "", 0
                )
                setattr(schema_err, "cleaned_text", _decoded_text)
                setattr(schema_err, "malformed_action", True)
                setattr(schema_err, "response_truncated", finish_reason == "length")
                if expect == "action":
                    setattr(schema_err, "envelope_error", _action_envelope_error(obj))
                raise schema_err
            return obj


def ask_llm(
    messages,
    max_tokens=256,
    think=False,
    think_level=None,
    max_retries=MAX_LLM_RETRIES,
    raw=False,
    timeout=None,
    reasoning_policy=DEFAULT_REASONING_POLICY,
    reasoning_trigger="unspecified",
    expect=None,
):
    """Call the configured backend and decode one plan/action/validator reply.

    Compatibility facade over LLMClient: snapshots the module-level
    configuration for this call and delegates. Retry/backoff policy, the
    parse-retry budget escalation, response-schema enforcement (``expect``),
    and the typed errors callers rely on (LLMTransportError, KeyError for
    API-error bodies, json.JSONDecodeError with
    malformed_action/response_truncated) live in LLMClient.ask."""
    return LLMClient().ask(
        messages,
        max_tokens=max_tokens,
        think=think,
        think_level=think_level,
        max_retries=max_retries,
        raw=raw,
        timeout=timeout,
        reasoning_policy=reasoning_policy,
        reasoning_trigger=reasoning_trigger,
        expect=expect,
    )


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
    "read_cursor_hash_required",
    "stale_read_cursor",
}

# E05: Error types where thinking escalation is counterproductive.
# These are structural failures — the scaffold knows what went wrong and the model
# needs different information or parameters, not deeper reasoning.
# Semantic failures (compile_error, unknown) keep thinking escalation.
_NO_THINK_ERRORS = frozenset(
    {
        "edit_failed",
        "missing_file",
        "timeout",
        "missing_tool",
        "permission_denied",
        "invalid_read_cursor",
        "invalid_read_limit",
        "read_cursor_hash_required",
        "stale_read_cursor",
    }
)

# E06: Short recovery hints injected into step output after typed failures.
# Tells the model what to do next without needing thinking tokens to rediscover it.
_RECOVERY_HINTS = {
    "edit_failed": "Read the file first, then retry edit with exact text from the file.",
    "missing_file": "Check the filename. Use shell ls to list directory contents.",
    "invalid_read_cursor": "Use cursor, limit, and sha256 exactly from the latest read continuation.",
    "invalid_read_limit": "Use cursor, limit, and sha256 exactly from the latest read continuation.",
    "read_cursor_hash_required": "Use cursor, limit, and sha256 exactly from the latest read continuation.",
    "stale_read_cursor": "The file changed. Restart read with offset and limit; do not reuse the old cursor.",
}


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
    unresolved = _unresolved_incomplete_writes(state.get("all_steps", []), working_dir)
    pending = state.get("pending_empty_writes", {})
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


def get_plan(user_prompt, state, client=None):
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
        plan_state["policy"] = get_policy()
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
    ask = ask_llm if client is None else client.ask
    return ask(
        [
            {"role": "system", "content": SYSTEM_PLAN},
            {
                "role": "user",
                "content": f"REQUEST:\n{user_prompt}\n\nSTATE:\n{json.dumps(plan_state)}",
            },
        ],
        max_tokens=PLANNER_MAX_TOKENS,
        think=is_replan,
        timeout=LLM_TIMEOUT_REPLAN if is_replan else None,
        reasoning_policy=state.get("reasoning_policy", DEFAULT_REASONING_POLICY),
        reasoning_trigger="planner_replan" if is_replan else "initial_plan",
        expect="plan",
    )


MAX_INPUT = 300  # max chars per non-goal field sent to executor
GOAL_CONTEXT_CHARS = int(os.environ.get("AGENT_GOAL_CONTEXT_CHARS", "300"))
if GOAL_CONTEXT_CHARS < 1:
    raise ValueError("AGENT_GOAL_CONTEXT_CHARS must be a positive integer")


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
):
    # Build slim step history from recent steps (current task + carryover from previous)
    steps = state.get("last_steps", [])[-MAX_STEP_HISTORY:]
    slim_steps = []
    for s in steps:
        # Use basename for file paths to avoid long tmp_path bloat
        arg = s.get("arg", "")
        if s["action"] in ("write", "read", "edit", "tree") and "/" in arg:
            arg = Path(arg).name
        else:
            arg = arg[-MAX_INPUT:]
        # Observation actions (read/search/tree) carry the content the model
        # navigates by; they get a larger output budget than mutating actions.
        out_cap = OBSERVE_STATE_CHARS if s["action"] in OBSERVE_ACTIONS else MAX_INPUT
        slim_steps.append(
            {
                "action": s["action"],
                "arg": arg,
                "ok": s["ok"],
                "output": s.get("output", "")[:out_cap],
            }
        )
    slim = {
        "task": state.get("current_task", task)[:MAX_INPUT],
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
    slim["policy"] = state.get("policy", get_policy())
    goal_line = f"GOAL:\n{goal[:goal_context_chars]}\n\n" if goal else ""
    user_msg = f"{goal_line}TASK:\n{task[:MAX_INPUT]}\n\nSTATE:\n{json.dumps(slim)}"
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
    # A pinned per-run client's step budget follows that client's backend
    # (issues #15/#40); the module facade keeps the patchable global.
    ask = ask_llm if client is None else client.ask
    settings = getattr(client, "settings", None)
    step_budget = STEP_TOKENS if settings is None else settings.step_tokens()
    return ask(
        [{"role": "system", "content": SYSTEM_STEP}, {"role": "user", "content": user_msg}],
        max_tokens=step_budget,
        think=think,
        reasoning_policy=reasoning_policy,
        reasoning_trigger=reasoning_trigger,
        expect="action",
    )


SYSTEM_TASK_REPLAN = """You are a task replanner. A single task failed. Given the failed task, errors, and completed tasks, propose a replacement task description.
Do NOT repeat completed work. The replacement must address the failure.
Preserve the original task's outcome. If it was to fix, edit, add, compile, run, create, or write something, do NOT replace it with a read/list/inspect-only preparation task.
Keep the replacement short (under 15 words). Use relative filenames, except
preserve an exact incomplete_write_target supplied in state.
Output ONLY valid JSON. No markdown, no explanation.
Format: {"task": "replacement task description"}"""

MAX_TASK_LOCAL_REPLANS = 1
TASK_REPLAN_MAX_TOKENS = 96


class TaskReplanResult(NamedTuple):
    """Structured task-local replan outcome (issue #40).

    ``task`` is the accepted replacement, or None when the replan failed;
    ``reject_reason`` then carries the typed reason. This return value
    replaces the former ``_last_task_replan_reject_reason`` module-global
    side channel."""

    task: str | None
    reject_reason: str | None


def _coerce_task_replan(result):
    """Normalize the replan seam's return to :class:`TaskReplanResult`.

    Tests that script ``askme.replan_task`` with the legacy ``str | None``
    stand-in keep working; a bare falsy return carries no typed reason."""
    if isinstance(result, TaskReplanResult):
        return result
    if isinstance(result, tuple) and len(result) == 2:
        return TaskReplanResult(*result)
    return TaskReplanResult(result, None if result else "unknown")


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


def replan_task(
    failed_task,
    errors,
    completed_tasks,
    state,
    user_prompt,
    goal_context_chars=GOAL_CONTEXT_CHARS,
    client=None,
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
    replan_state["policy"] = state.get("policy", get_policy())
    ask = ask_llm if client is None else client.ask
    try:
        result = ask(
            [
                {"role": "system", "content": SYSTEM_TASK_REPLAN},
                {
                    "role": "user",
                    "content": f"GOAL:\n{user_prompt[:goal_context_chars]}\n\nSTATE:\n{json.dumps(replan_state)}",
                },
            ],
            max_tokens=TASK_REPLAN_MAX_TOKENS,
            think=False,
            max_retries=0,
            reasoning_policy=state.get("reasoning_policy", DEFAULT_REASONING_POLICY),
            reasoning_trigger="task_local_replan",
            expect="task_replan",
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


def _should_validate(replan, history, state, user_prompt, final_validate=None):
    """Decide whether to run final validation. Returns True if validation should run.

    ``final_validate`` is the run's resolved mode (issue #68); None keeps
    the module-level compatibility surface."""
    mode = FINAL_VALIDATE if final_validate is None else final_validate
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
    completed = state.get("completed_tasks", [])
    if len(completed) >= 3:
        return True
    # Count total steps
    total_steps = sum(1 for e in history if e.get("event") == "step")
    if total_steps >= 5:
        return True
    if _VALIDATE_KEYWORDS.search(user_prompt):
        return True
    return False


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


def _validate_completion(user_prompt, state, working_dir, client=None, log_sink=None):
    """Run final validation. Returns a :class:`ValidationResponse` or None.

    None means no verdict was available — the validator was unreachable or
    replied outside its schema. Callers must treat that as unverified, never
    as a pass (issue #68)."""
    emit = log if log_sink is None else log_sink
    deterministic = _deterministic_check(user_prompt, state, working_dir)
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
    ask = ask_llm if client is None else client.ask
    try:
        result = ask(
            [{"role": "system", "content": SYSTEM_VALIDATE}, {"role": "user", "content": user_msg}],
            max_tokens=768,
            think=True,
            think_level="high",
            max_retries=0,
            reasoning_policy=state.get("reasoning_policy", DEFAULT_REASONING_POLICY),
            reasoning_trigger="final_validator",
            expect="validation",
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


def _has_new_validation_evidence(state):
    start = state.get("validated_step_count", 0)
    return any(
        s.get("action") in ("write", "edit", "shell") and s.get("ok")
        for s in state.get("all_steps", [])[start:]
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


def _compile_repair_action(error_output, working_dir, cmd, enabled=None):
    """Propose a normal write action for a known C include diagnostic, or None.

    The #41 repair-rule boundary: this function never mutates the workspace.
    It inspects the unique candidate source and, when a known
    missing-include diagnostic matches, returns an ordinary full-file write
    action — workspace-relative ``arg``, repaired ``content``, and the human
    description in ``reasoning`` — for the controller to dispatch through
    the action executor and record like any other step. ``enabled`` is the
    run's resolved ablation arm; None keeps the module-level compatibility
    surface."""
    if not (COMPILE_REPAIR_ENABLED if enabled is None else enabled):
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


def execute(action, working_dir="."):
    """Dispatch one action and return the legacy result dict.

    Compatibility façade: tests and downstream code patch or call this seam,
    so the run loop routes every dispatch — including deterministic retries —
    through it.
    """
    return ActionExecutor(working_dir).dispatch(action).to_dict()


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
        # None resolves the module _run_log at call time so patched sinks
        # and RUN_LOG_PATH swaps keep working (issue #40).
        self._event_sink = event_sink

    def _event(self, event):
        (_run_log if self._event_sink is None else self._event_sink)(event)

    def selected(self):
        self.state["selected_steps"] += 1

    def executed(self):
        self.state["executed_steps"] += 1

    def skip(self, task_index, step, act, action, reason):
        """Record a selected-but-not-dispatched action in run metrics + log."""
        self.state["skipped_steps"] += 1
        self._event(
            {
                "event": "step_skipped",
                "task_index": task_index,
                "step": step,
                "action": act,
                "arg": action.get("arg", "")[:120],
                "reason": reason,
            }
        )

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


class _StepFlow(Enum):
    """Control-flow outcome of a step decision inside one task attempt."""

    NEXT_STEP = "next_step"  # handled; select the next executor action
    END_ATTEMPT = "end_attempt"  # attempt over: done, fail, stuck, or error


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
    action: dict[str, Any]
    act: str
    truncated_write: bool = False
    logical_write_target: str | None = None
    operation_write_target: str | None = None


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
        self.recorder = StepRecorder(self.data, self.history, event_sink=event_sink)
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
class RunConfig:
    """Immutable per-run configuration (issues #40/#68).

    ``None`` fields resolve from the module-level compatibility surface when
    the run starts, so a default config behaves exactly like the patchable
    globals. Explicit fields pin the run: a pinned ``llm`` makes the run
    construct its own :class:`LLMClient` instead of using the module
    ``ask_llm`` facade, so differently configured runs coexist in one
    process without saving or restoring globals. Every outcome-affecting
    setting — validation mode, the #41 compile-repair arm, guard
    thresholds, capability budgets (on ``llm``), and run limits — resolves
    into one frozen per-run surface whose hash is logged at run_start and
    returned in the config metadata."""

    llm: LLMSettings | None = None
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
            llm=LLMSettings.from_env(e),
            allow_system_installs=e.get("ALLOW_SYSTEM_INSTALLS", "0") == "1",
            allow_network=e.get("ALLOW_NETWORK", "1") == "1",
            reasoning_policy=policy,
            final_validate=e.get("AGENT_FINAL_VALIDATE", "auto"),
            compile_repair=e.get("AGENT_COMPILE_REPAIR", "1") == "1",
            step_policy=e.get("AGENT_STEP_POLICY", "heuristic").strip().lower(),
        )


@dataclass(frozen=True)
class RunDependencies:
    """Injectable collaborators for one run (issue #40).

    ``None`` fields keep the module seams — ``ask_llm``, ``execute``,
    ``log``, ``_run_log``, and ``time.time`` — resolved at call time, so
    patch-based tests keep intercepting them. An injected ``llm_client``
    (an :class:`LLMClient` or any object with a compatible ``ask``) handles
    every planner, executor, validator, and task-replanner call. An injected
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

    def __init__(self, controller):
        self.controller = controller

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
        return self.controller.run_state.validate_pressure_target()

    def guard_action(self, ctx, attempt):
        flow = self._observe_tail_guard(ctx, attempt)
        if flow is None:
            flow = self._rewrite_loop_guard(ctx, attempt)
        return flow

    def _observe_tail_guard(self, ctx, attempt):
        """Reserve the final steps of a write-shaped task for commitment."""
        controller = self.controller
        # Write-forcing tail reserve (issue #15): on a write-shaped task the
        # final steps are reserved for committing actions.
        if not (
            ctx.act in OBSERVE_ACTIONS
            and attempt.wants_write
            and attempt.commit_executed == 0
            and controller.max_steps - ctx.step <= controller.guards.observe_tail_reserve
        ):
            return None
        attempt.observe_blocked += 1
        if attempt.observe_blocked >= 2:
            controller._emit(
                f"  [{ctx.step + 1}] auto-fail (observation steps exhausted without a write)"
            )
            controller.state["errors"].append(
                f"[stuck_loop] {ctx.act} {ctx.action.get('arg', '')[:60]}: observation steps exhausted without a write"
            )
            controller.recorder.skip(
                ctx.task_index, ctx.step, ctx.act, ctx.action, "observe_tail_exhausted"
            )
            return _StepFlow.END_ATTEMPT
        controller._emit(
            f"  [{ctx.step + 1}] skip ({ctx.act} blocked: remaining steps reserved for write)"
        )
        controller.recorder.skip(
            ctx.task_index, ctx.step, ctx.act, ctx.action, "observe_tail_reserved"
        )
        controller.recorder.note(
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
        controller = self.controller
        # Rewrite damping (revision 4): after rewrite_skip_writes successful
        # full writes of the same target with no intervening successful
        # shell/edit, further full rewrites are skipped — verify, edit, or
        # finish instead.
        if not (
            ctx.act == "write"
            and not ctx.action.get("append")
            and not ctx.truncated_write
            and controller.run_state.rewrite_skip_armed(ctx.logical_write_target)
        ):
            return None
        attempt.dup_skip_count += 1
        controller._emit(
            f"  [{ctx.step + 1}] skip (rewrite loop: "
            f"{ctx.action.get('arg', '')[:40]} already written "
            f"{controller.run_state.consecutive_target_writes}x)"
        )
        controller.recorder.skip(ctx.task_index, ctx.step, ctx.act, ctx.action, "rewrite_loop")
        controller.recorder.note(
            {
                "action": ctx.act,
                "arg": ctx.action.get("arg", ""),
                "ok": True,
                "output": (
                    f"Already written {controller.run_state.consecutive_target_writes} times. "
                    "Do NOT write it again — verify with shell, make a "
                    "targeted edit, or emit done."
                ),
            }
        )
        return _StepFlow.NEXT_STEP

    def note_result(self, ctx, attempt, result):
        run_state = self.controller.run_state
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
        self.controller.run_state.break_rewrite_streak()


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

    def __init__(self, controller):
        super().__init__(controller)
        self.needs_verification = False
        self.unverified_target = None

    def write_pressure(self, attempt):
        return attempt.write_pressure()

    def _target_has_open_obligation(self, target):
        """True while incomplete-write recovery legitimately rewrites it."""
        state = self.controller.state
        if target in state.get("pending_empty_writes", {}):
            return True
        return target in _unresolved_incomplete_writes(
            state.get("all_steps", []), self.controller.working_dir
        )

    def guard_done(self, ctx, attempt):
        if not self.needs_verification:
            return None
        controller = self.controller
        name = Path(str(self.unverified_target or "file")).name
        controller._emit(f"  [{ctx.step + 1}] skip (done before verifying {name})")
        controller.recorder.skip(
            ctx.task_index, ctx.step, ctx.act, ctx.action, "lifecycle_unverified_done"
        )
        controller.recorder.note(
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
        controller = self.controller
        name = Path(str(self.unverified_target)).name
        attempt.dup_skip_count += 1
        controller._emit(f"  [{ctx.step + 1}] skip (rewrite of unverified {name})")
        controller.recorder.skip(
            ctx.task_index, ctx.step, ctx.act, ctx.action, "lifecycle_verify_before_rewrite"
        )
        controller.recorder.note(
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
                    {"arg": ctx.action.get("arg", "")}, self.controller.working_dir
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

    def __init__(self, user_prompt, working_dir, config=None, dependencies=None):
        cfg = RunConfig() if config is None else config
        deps = RunDependencies() if dependencies is None else dependencies
        reasoning_policy = (
            DEFAULT_REASONING_POLICY if cfg.reasoning_policy is None else cfg.reasoning_policy
        )
        if reasoning_policy not in REASONING_POLICIES:
            raise ValueError(f"reasoning_policy must be one of {', '.join(REASONING_POLICIES)}")
        goal_context_chars = (
            GOAL_CONTEXT_CHARS if cfg.goal_context_chars is None else cfg.goal_context_chars
        )
        if goal_context_chars < 1:
            raise ValueError("goal_context_chars must be a positive integer")
        self.user_prompt = user_prompt
        self.working_dir = working_dir
        self.max_replans = MAX_REPLANS if cfg.max_replans is None else cfg.max_replans
        self.max_tasks = MAX_TASKS if cfg.max_tasks is None else cfg.max_tasks
        self.max_steps = MAX_STEPS if cfg.max_steps is None else cfg.max_steps
        # The public config path enforces the same positive-budget contract
        # as the CLI's _positive_int (Codex P2, PR #65): a zero budget would
        # silently report a plausible failure without doing any work.
        for budget_name in ("max_replans", "max_tasks", "max_steps"):
            if getattr(self, budget_name) < 1:
                raise ValueError(f"{budget_name} must be a positive integer")
        self.reasoning_policy = reasoning_policy
        self.goal_context_chars = goal_context_chars
        # Freeze the executor/replanner view once so all policy arms receive the same
        # task context even if module configuration changes while a run is active.
        self.goal_context = user_prompt[:goal_context_chars]
        # Dependency seams (issue #40): None keeps the patchable module
        # behavior; a pinned llm config builds this run's own client, and
        # injected sinks alone snapshot the module configuration into a
        # run-local client so LLM retry logs and telemetry cannot escape to
        # the module stdout/JSONL sinks (Codex P2, PR #65). Transport still
        # resolves requests.post at call time either way.
        if deps.llm_client is not None:
            self._client = deps.llm_client
        elif cfg.llm is not None:
            self._client = LLMClient(
                settings=cfg.llm, log_sink=deps.log_sink, event_sink=deps.event_sink
            )
        elif deps.log_sink is not None or deps.event_sink is not None:
            self._client = LLMClient(
                settings=LLMSettings.current(),
                log_sink=deps.log_sink,
                event_sink=deps.event_sink,
            )
        else:
            self._client = None
        # An injected executor that names a workspace must name this run's
        # workspace (Codex P1, PR #65): otherwise actions would mutate one
        # directory while the result identifies another. Scripted stand-ins
        # without a working_dir attribute stay accepted.
        executor_dir = getattr(deps.action_executor, "working_dir", None)
        if executor_dir is not None and Path(executor_dir).resolve() != Path(working_dir).resolve():
            raise ValueError(
                "action_executor is bound to a different directory than the run workspace"
            )
        self._action_executor = deps.action_executor
        self._clock = time.time if deps.clock is None else deps.clock
        self._log_sink = deps.log_sink
        self._event_sink = deps.event_sink
        # Resolved, immutable run metadata for run_start and the structured
        # result. The module snapshot keeps the default path identical to
        # the former global reads at run start.
        client_settings = getattr(self._client, "settings", None)
        if client_settings is not None:
            self._llm_meta = client_settings
        else:
            self._llm_meta = cfg.llm if cfg.llm is not None else LLMSettings.current()
        self._policy = {
            "allow_system_installs": (
                ALLOW_SYSTEM_INSTALLS
                if cfg.allow_system_installs is None
                else cfg.allow_system_installs
            ),
            "allow_network": (ALLOW_NETWORK if cfg.allow_network is None else cfg.allow_network),
        }
        # Outcome-affecting settings resolve once here (issue #68): validation
        # mode, the #41 compile-repair arm, and the guard thresholds are
        # frozen for the run — mid-run changes to the module globals cannot
        # change this run's policy, and the hash below pins what actually ran.
        self.final_validate = FINAL_VALIDATE if cfg.final_validate is None else cfg.final_validate
        self.compile_repair = (
            COMPILE_REPAIR_ENABLED if cfg.compile_repair is None else cfg.compile_repair
        )
        step_policy = DEFAULT_STEP_POLICY if cfg.step_policy is None else cfg.step_policy
        if step_policy not in _STEP_POLICY_ARMS:
            raise ValueError(f"step_policy must be one of {', '.join(STEP_POLICIES)}")
        self.guards = GuardThresholds(
            write_pressure_observations=(
                WRITE_PRESSURE_OBSERVATIONS
                if cfg.write_pressure_observations is None
                else cfg.write_pressure_observations
            ),
            observe_tail_reserve=(
                OBSERVE_TAIL_RESERVE
                if cfg.observe_tail_reserve is None
                else cfg.observe_tail_reserve
            ),
            rewrite_pressure_writes=(
                REWRITE_PRESSURE_WRITES
                if cfg.rewrite_pressure_writes is None
                else cfg.rewrite_pressure_writes
            ),
            rewrite_skip_writes=(
                REWRITE_SKIP_WRITES if cfg.rewrite_skip_writes is None else cfg.rewrite_skip_writes
            ),
            max_task_local_replans=(
                MAX_TASK_LOCAL_REPLANS
                if cfg.max_task_local_replans is None
                else cfg.max_task_local_replans
            ),
        )
        meta = self._llm_meta
        self._config_payload = {
            "backend": meta.backend,
            "model": meta.model,
            "provider": meta.provider if meta.backend == "openrouter" else "",
            "reasoning_effort": meta.reasoning_effort if meta.backend == "openrouter" else "",
            "allow_provider_fallbacks": meta.allow_fallbacks,
            "require_provider_parameters": meta.require_parameters,
            "policy": dict(self._policy),
            "reasoning_policy": self.reasoning_policy,
            "final_validate": self.final_validate,
            "compile_repair": self.compile_repair,
            "step_policy": step_policy,
            "guards": self.guards.describe(),
            "budgets": {
                "step_tokens": meta.step_tokens(),
                "step_write_tokens": meta.write_retry_tokens(),
                "planner_max_tokens": PLANNER_MAX_TOKENS,
                "task_replan_max_tokens": TASK_REPLAN_MAX_TOKENS,
            },
            "limits": {
                "max_replans": self.max_replans,
                "max_tasks": self.max_tasks,
                "max_steps": self.max_steps,
                "goal_context_chars": self.goal_context_chars,
            },
        }
        self.config_hash = _config_hash(self._config_payload)
        self.run_state = RunState(
            reasoning_policy,
            goal_context_chars,
            clock=self._clock,
            event_sink=deps.event_sink,
            rewrite_pressure_writes=self.guards.rewrite_pressure_writes,
            rewrite_skip_writes=self.guards.rewrite_skip_writes,
        )
        self.state = self.run_state.data
        self.history = self.run_state.history
        self.recorder = self.run_state.recorder
        # The pressure/completion policy arm is constructed last so it can
        # hold run-scoped state over the same run_state/guards the
        # controller uses (issue #31).
        self.step_policy = _STEP_POLICY_ARMS[step_policy](self)

    def _emit(self, msg):
        """Console line through the injected sink, defaulting to log()."""
        (log if self._log_sink is None else self._log_sink)(msg)

    def _event(self, event):
        """JSONL event through the injected sink, defaulting to _run_log()."""
        (_run_log if self._event_sink is None else self._event_sink)(event)

    def _llm_kwargs(self):
        """Extra kwargs for LLM-backed helpers; empty on the module facade
        path so patched helpers keep their legacy call signatures."""
        return {} if self._client is None else {"client": self._client}

    def _dispatch(self, action):
        """One normalized dispatch through the action seam (issue #40): the
        injected executor when provided, else the patchable execute()."""
        if self._action_executor is None:
            raw = execute(action, self.working_dir)
        else:
            raw = self._action_executor.dispatch(action).to_dict()
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
                outcome = self._try_finish(replan)
                if outcome is not None:
                    break
        if outcome is None:
            outcome = self._finish_after_exhaustion()
        self._event(outcome.run_end_event())
        return {
            "status": outcome.status,
            "state": self.state,
            "log": self.history,
            "outcome": outcome.describe(),
        }

    def _build_outcome(self, status, validation, replans):
        """Snapshot the terminal record from the run-scoped state."""
        return RunOutcome(
            status=status,
            validation=validation,
            replans=replans,
            wall_s=round(self.run_state.elapsed(), 2),
            completed_tasks=len(self.state["completed_tasks"]),
            selected_steps=self.state["selected_steps"],
            executed_steps=self.state["executed_steps"],
            skipped_steps=self.state["skipped_steps"],
            errors=tuple(self.state["errors"][-5:]) if status == "exhausted" else (),
        )

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
        env = preflight_probe(self.working_dir)
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
            plan = get_plan(self.user_prompt, self.state, **self._llm_kwargs())
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
                blocker = _completion_blocker(self.state, self.working_dir)
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
            completed_repair = _task_satisfied_by_deterministic_repair(task, self.state)
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
                replan = _coerce_task_replan(
                    replan_task(
                        task,
                        self.state["errors"],
                        self.state["completed_tasks"],
                        self.state,
                        self.goal_context,
                        goal_context_chars=self.goal_context_chars,
                        **self._llm_kwargs(),
                    )
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
            action = get_step(
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
        # Normalize None → "" for optional string fields (models emit "arg": null)
        for _k in ("arg", "content", "reasoning", "find", "replace"):
            if action.get(_k) is None:
                action[_k] = ""
        act = action.get("action", "")
        envelope = _action_envelope_error(action)
        if envelope is not None:
            # Response-schema rejection before controller accounting (issue
            # #68): an empty, cross-type, or unknown-action envelope never
            # consumes an execution step. The live decode path enforces the
            # same schema with retries; this arm covers injected clients and
            # patched facades.
            label = act if _valid_nonempty_str(act) else "(no action)"
            detail = (
                "not a known action"
                if envelope == "unknown_action"
                else "reply is not a dispatchable action envelope"
            )
            self._emit(f"  [{step + 1}] rejected [{envelope}]: {label}")
            self.state["errors"].append(
                f"[{envelope}] {label} {action.get('arg', '')[:60]}: {detail}"
            )
            self._event(
                {
                    "event": "step_error",
                    "task_index": i,
                    "step": step,
                    "error_type": envelope,
                }
            )
            return None
        self.recorder.selected()
        self._emit(f"  [{step + 1}] {act}: {action['arg'][:80]}")
        return _StepContext(task_index=i, step=step, started=t_step, action=action, act=act)

    def _decide_step(self, ctx, attempt):
        """Run the shared invariants and the policy arm; None dispatches.

        Order: write-truncation/obligation preparation and the completion
        blocker are shared invariants; the policy arm's discipline runs
        next; duplicate/stuck loop protection guards every arm last.
        """
        if ctx.act == "done":
            return self._handle_done(ctx, attempt)
        if ctx.act == "fail":
            reason = ctx.action.get("reasoning", "no reason")
            self._emit(f"  FAIL ({self._clock() - ctx.started:.1f}s): {reason}")
            self.state["errors"].append(f"Task '{attempt.task}': {reason}")
            return _StepFlow.END_ATTEMPT
        flow = self._prepare_write(ctx)
        if flow is None:
            flow = self.step_policy.guard_action(ctx, attempt)
        if flow is None:
            flow = self._duplicate_guard(ctx, attempt)
        return flow

    def _handle_done(self, ctx, attempt):
        """Accept ``done`` only past the shared blocker and the policy arm."""
        blocker = _completion_blocker(self.state, self.working_dir)
        if blocker is not None:
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
            self._emit(f"  [{ctx.step + 1}] skip (done with incomplete write: {incomplete_name})")
            self.recorder.skip(
                ctx.task_index, ctx.step, ctx.act, ctx.action, "incomplete_write_done"
            )
            self.recorder.note(
                {
                    "action": "done",
                    "arg": "",
                    "ok": True,
                    "output": (
                        f"Cannot finish: {incomplete_name} is incomplete "
                        f"at {recovery_arg}. {recovery}"
                    ),
                }
            )
            return _StepFlow.NEXT_STEP
        flow = self.step_policy.guard_done(ctx, attempt)
        if flow is not None:
            return flow
        attempt.done = True
        return _StepFlow.END_ATTEMPT

    def _prepare_write(self, ctx):
        """Classify write truncation and enforce zero-byte recovery order."""
        action, act = ctx.action, ctx.act
        # Sentinel transport truncation (issue #15): keep the complete lines
        # that arrived and steer the model to finish the file with chunked
        # append instead of failing the step.
        ctx.truncated_write = act == "write" and action.pop("content_truncated", False)
        ctx.logical_write_target = (
            _mutation_target_key({"arg": action.get("arg", "")}, self.working_dir)
            if act == "write"
            else None
        )
        ctx.operation_write_target = (
            _mutation_target_key(
                {
                    "arg": action.get("arg", ""),
                    "append": bool(action.get("append")),
                },
                self.working_dir,
            )
            if act == "write"
            else None
        )
        pending_recovery = _pending_empty_recovery(
            self.state["pending_empty_writes"],
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
            self._emit(f"  [{ctx.step + 1}] skip (append before first replacement chunk landed)")
            self.recorder.skip(
                ctx.task_index, ctx.step, act, action, "append_after_empty_overwrite"
            )
            self.recorder.note(
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
                self._emit(f"  [{ctx.step + 1}] skip (write truncated before a complete line)")
                self.recorder.skip(ctx.task_index, ctx.step, act, action, "truncated_write_empty")
                # The recovery instruction asks for a clean resend; disarm
                # rewrite damping before that resend even though this empty
                # partial attempt wrote no bytes.
                self.run_state.disarm_rewrite_damping()
                # Empty append attempts are obligations on the referent
                # observed at dispatch time. Key them by that operation
                # target so retargeting a leaf symlink cannot overwrite an
                # older obligation.
                pending_target = (
                    ctx.operation_write_target if action.get("append") else ctx.logical_write_target
                )
                recovery_arg = action.get("arg", "") or "file"
                if pending_target is not None:
                    existing = self.state["pending_empty_writes"].get(pending_target)
                    append_allowed = bool(action.get("append"))
                    if isinstance(existing, dict):
                        append_allowed = existing.get("append_allowed", False) and append_allowed
                    append_target = _mutation_target_key(
                        {
                            "arg": action.get("arg", ""),
                            "append": True,
                        },
                        self.working_dir,
                    )
                    append_targets = list(_pending_append_targets(existing))
                    if append_target is not None and append_target not in append_targets:
                        append_targets.append(append_target)
                    recovery_arg = _target_recovery_arg(pending_target, self.working_dir)
                    self.state["pending_empty_writes"][pending_target] = {
                        "name": Path(action.get("arg", "") or "file").name,
                        "append_allowed": append_allowed,
                        "append_targets": append_targets,
                        "recovery_arg": recovery_arg,
                    }
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
                self.recorder.note(
                    {
                        "action": act,
                        "arg": action.get("arg", ""),
                        "ok": True,
                        "output": obs,
                    }
                )
                return _StepFlow.NEXT_STEP
            action["content"] = kept
        return None

    def _duplicate_guard(self, ctx, attempt):
        """Per-action-type loop detection against the previous step."""
        action, act = ctx.action, ctx.act
        last = self.state["last_steps"][-1:] if self.state["last_steps"] else []
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
            current_target = _mutation_target_key(current_target_step, self.working_dir)
            same_mutation_target = (
                current_target is not None
                and _mutation_target_key(prev, self.working_dir) == current_target
            )
        if act in ("write", "edit") and same_mutation_target:
            # write: same content = duplicate; edit: same find+replace = duplicate
            is_dup = False
            if act == "write" and action.get("append"):
                # Chunked append is never a no-op — an identical consecutive
                # chunk is a stuck loop, not a duplicate.
                if prev.get("_append") and prev.get("_content", "") == action.get("content", ""):
                    self._emit(
                        f"  [{ctx.step + 1}] auto-fail (same chunk appended twice to {action.get('arg', '')[:40]})"
                    )
                    self.state["errors"].append(
                        f"[stuck_loop] write {action.get('arg', '')[:60]}: same chunk appended twice"
                    )
                    self.recorder.skip(ctx.task_index, ctx.step, act, action, "stuck_append")
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
                self._emit(
                    f"  [{ctx.step + 1}] auto-fail (same edit failed twice on {action.get('arg', '')[:40]})"
                )
                self.state["errors"].append(
                    f"[stuck_loop] edit {action.get('arg', '')[:60]}: same find string failed twice"
                )
                self.recorder.skip(ctx.task_index, ctx.step, act, action, "stuck_edit")
                return _StepFlow.END_ATTEMPT
            if is_dup:
                attempt.dup_skip_count += 1
                self._emit(f"  [{ctx.step + 1}] skip (duplicate {act}, same content)")
                self.recorder.skip(ctx.task_index, ctx.step, act, action, f"duplicate_{act}")
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
                self.recorder.note(entry)
                # Defer thinking escalation: first duplicate skip gets a
                # corrective observation only; escalate on 2+ consecutive skips.
                # Saves ~10s of thinking time on harmless first-time duplicates.
                if attempt.dup_skip_count >= 2:
                    attempt.use_think = True
                    attempt.reasoning_trigger = "duplicate_action"
                return _StepFlow.NEXT_STEP
        elif act == "shell" and prev.get("arg", "") == action.get("arg", ""):
            if prev.get("ok"):
                # Repetition is never completion evidence (issue #68): the
                # duplicate is suppressed as a no-op once, and repeating it
                # again is a stuck loop for the replanner — task acceptance
                # still requires an explicit done.
                attempt.dup_skip_count += 1
                if attempt.dup_skip_count >= 2:
                    self._emit(
                        f"  [{ctx.step + 1}] auto-fail (same successful shell repeated on {action.get('arg', '')[:40]})"
                    )
                    self.state["errors"].append(
                        f"[stuck_loop] shell {action.get('arg', '')[:60]}: same successful command repeated"
                    )
                    self.recorder.skip(ctx.task_index, ctx.step, act, action, "stuck_shell_repeat")
                    return _StepFlow.END_ATTEMPT
                self._emit(f"  [{ctx.step + 1}] skip (duplicate successful shell)")
                self.recorder.skip(ctx.task_index, ctx.step, act, action, "duplicate_shell")
                self.recorder.note(
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
                prev_timeout = prev.get("_timeout", _get_shell_timeout(action.get("arg", "")))
                bumped = max(SHELL_TIMEOUT_LONG, prev_timeout * 2)
                action["timeout"] = min(bumped, SHELL_TIMEOUT_MAX)
                self._emit(f"  [{ctx.step + 1}] retrying after timeout ({action['timeout']}s)")
            else:
                self._emit(f"  [{ctx.step + 1}] auto-fail (same shell failed twice)")
                self.state["errors"].append(
                    f"Stuck: {act} {action.get('arg', '')[:60]} failed twice"
                )
                self.recorder.skip(ctx.task_index, ctx.step, act, action, "stuck_shell")
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
                    self._emit(
                        f"  [{ctx.step + 1}] auto-fail (same read repeated on {action.get('arg', '')[:40]})"
                    )
                    self.state["errors"].append(
                        f"[stuck_loop] read {action.get('arg', '')[:60]}: same file read repeatedly"
                    )
                    self.recorder.skip(ctx.task_index, ctx.step, act, action, "stuck_read")
                    return _StepFlow.END_ATTEMPT
                self._emit(f"  [{ctx.step + 1}] skip (duplicate read)")
                self.recorder.skip(ctx.task_index, ctx.step, act, action, "duplicate_read")
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
                self.recorder.note(entry)
                return _StepFlow.NEXT_STEP
            else:
                self._emit(f"  [{ctx.step + 1}] auto-fail (same read failed twice)")
                self.state["errors"].append(
                    f"[stuck_loop] read {action.get('arg', '')[:60]} failed twice"
                )
                self.recorder.skip(ctx.task_index, ctx.step, act, action, "stuck_read_failed")
                return _StepFlow.END_ATTEMPT
        return None

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
            _clear_pending_empty_writes(
                self.state["pending_empty_writes"],
                ctx.logical_write_target,
                ctx.operation_write_target,
                bool(action.get("append")),
            )
        if act not in OBSERVE_ACTIONS and result.ok:
            # Counted only on success (Codex P2, PR #16): a failed mutation
            # must not disarm write pressure or the observation tail reserve.
            attempt.commit_executed += 1
        self.step_policy.note_result(ctx, attempt, result)
        if ctx.truncated_write and result.ok:
            # The executor is stateless per step: without a resume anchor
            # the model cannot know where the write stopped. The kept
            # content is exactly what _prepare_write trimmed to the last
            # complete line.
            kept = action.get("content", "")
            anchor = kept.splitlines()[-1][-80:]
            recovery_arg = _target_recovery_arg(ctx.operation_write_target, self.working_dir)
            result.output += (
                f" (truncated after {kept.count(chr(10))} lines; "
                f"last written line: {anchor!r}; continue with "
                f"append:true at {recovery_arg} starting after "
                "that line)"
            )
        ok_str = "OK" if result.ok else "FAIL"
        self._emit(f"  -> {ok_str} ({self._clock() - ctx.started:.1f}s): {result.output[:80]}")

        step_entry = self.recorder.record(
            StepReceipt.executed(action, result, self.working_dir, ctx.truncated_write),
            ctx.task_index,
            ctx.step,
            wall_s=round(self._clock() - ctx.started, 2),
        )

        if not result.ok:
            return self._recover_failed_step(ctx, attempt, result)
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
            repair_action = _compile_repair_action(
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

    def _try_finish(self, replan):
        """Validate an all-done pass; a :class:`RunOutcome` ends the run."""
        status, validation = "complete", "skipped"
        wants_validation = _should_validate(
            replan, self.history, self.state, self.user_prompt, final_validate=self.final_validate
        )
        first_validation = self.state.get("validation_attempts", 0) == 0
        recheck_validation = (
            self.state.get("validation_recheck_needed")
            and self.state.get("validation_attempts", 0) < 2
            and _has_new_validation_evidence(self.state)
        )
        if wants_validation and (first_validation or recheck_validation):
            self.state["validated_once"] = True
            self.state["validation_attempts"] = self.state.get("validation_attempts", 0) + 1
            validate_kwargs = self._llm_kwargs()
            if self._log_sink is not None:
                validate_kwargs["log_sink"] = self._log_sink
            vresult = _validate_completion(
                self.user_prompt, self.state, self.working_dir, **validate_kwargs
            )
            if vresult is not None and vresult.valid is False:
                reason = vresult.reason or "validation failed"
                missing = list(vresult.missing)
                error_msg = f"[validation_failed] {reason}"
                if missing:
                    error_msg += f" missing: {', '.join(missing)}"
                self.state["errors"].append(error_msg)
                self.state["validation_recheck_needed"] = True
                self.state["validated_step_count"] = len(self.state.get("all_steps", []))
                self._emit(f"  Validation failed: {reason}")
                self._event(
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
                self._emit("  Validation recheck produced no verdict; failure remains pending.")
                self._event(
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
                self._emit("  Validation produced no verdict; completing unverified.")
                self._event(
                    {
                        "event": "validation",
                        "valid": None,
                        "reason": "validator unavailable",
                        "deterministic": False,
                    }
                )
            else:
                validation = "deterministic" if vresult.deterministic else "passed"
                self.state["validation_recheck_needed"] = False
                self._emit("  Validation passed.")
                self._event(
                    {
                        "event": "validation",
                        "valid": True,
                        "deterministic": vresult.deterministic,
                    }
                )
        if self.state.get("validation_recheck_needed"):
            if self.state.get("validation_attempts", 0) >= 2:
                reason = "validation remains failed after the maximum checks"
            else:
                reason = (
                    "completion after failed validation requires new write, edit, or shell evidence"
                )
            self.state["errors"].append(f"[validation_failed] {reason}")
            self._emit(f"  Completion refused: {reason}")
            self._event({"event": "validation_pending", "reason": reason})
            return None
        outcome = self._build_outcome(status, validation, replan)
        self._emit(f"All tasks complete. ({outcome.wall_s:.1f}s total)")
        self._emit(f"Output in: {self.working_dir}")
        return outcome

    def _finish_after_exhaustion(self):
        """Exhaustion is terminal: no shell-success reconciliation (issue #68).

        The former deterministic pass could convert an exhausted budget into
        ``complete`` from a broad "latest shell succeeded" heuristic even
        though the plan never finished; that is evaluation-contaminating
        false success, so the run now reports ``exhausted`` unconditionally.
        """
        validation = "failed" if self.state.get("validation_recheck_needed") else "skipped"
        outcome = self._build_outcome("exhausted", validation, self.max_replans)
        self._emit(f"Exhausted {self.max_replans} replan attempts. ({outcome.wall_s:.1f}s total)")
        self._emit(f"Errors: {self.state['errors']}")
        self._emit(f"Output in: {self.working_dir}")
        return outcome


def run_result(user_prompt, working_dir=None, config=None, dependencies=None):
    """Public structured-run API (issue #40).

    Resolves the workspace (creating an isolated temporary directory when
    ``working_dir`` is None), composes the controller from the immutable
    :class:`RunConfig` and the injectable :class:`RunDependencies`, and
    returns the structured result: ``status``, ``state``, and ``log``, plus
    the resolved ``config`` metadata (never credentials) and the
    ``workspace`` ownership record. ``run()``, ``_run_loop``, and the CLI
    are wrappers over this one path.
    """
    workspace = RunWorkspace.resolve(working_dir)
    try:
        controller = _RunController(
            user_prompt, workspace.path, config=config, dependencies=dependencies
        )
    except BaseException:
        # Invalid configuration must not leak an undisclosed temporary
        # directory (Codex P2, PR #65); cleanup never touches a supplied one.
        workspace.cleanup()
        raise
    result = controller.run()
    result["config"] = controller.config_metadata()
    result["workspace"] = workspace.describe()
    return result


def _run_loop(
    user_prompt,
    working_dir,
    max_replans=MAX_REPLANS,
    max_tasks=MAX_TASKS,
    max_steps=MAX_STEPS,
    reasoning_policy=DEFAULT_REASONING_POLICY,
    goal_context_chars=GOAL_CONTEXT_CHARS,
):
    """Compatibility seam over run_result() (issues #31/#40).

    Existing callers and tests keep this kwargs signature; composition and
    all production behavior live in run_result()/_RunController. The
    module-level LLM facade stays in effect because no per-run ``llm``
    configuration is pinned here.
    """
    return run_result(
        user_prompt,
        working_dir=working_dir,
        config=RunConfig(
            reasoning_policy=reasoning_policy,
            max_replans=max_replans,
            max_tasks=max_tasks,
            max_steps=max_steps,
            goal_context_chars=goal_context_chars,
        ),
    )


# Terminal statuses under which every planned task finished (issue #68).
# "complete_unverified" marks a completed run whose wanted final validation
# produced no verdict: completion stands, but it is never reported as a
# verified pass. "exhausted" is the only failure status.
COMPLETE_STATUSES = ("complete", "complete_unverified")


def run(user_prompt, working_dir=None):
    """Public API: run agent and return True (success) or False (failure).

    Compatibility wrapper over run_result(); an isolated temporary
    directory is created per run unless the caller provides one. Success
    means the run completed, verified or not; the structured statuses stay
    on run_result()."""
    return run_result(user_prompt, working_dir=working_dir)["status"] in COMPLETE_STATUSES


def _positive_int(value):
    """argparse type for enforced, non-zero run budgets."""
    try:
        parsed = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError("must be an integer") from e
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run AskMe against an isolated or existing working directory."
    )
    parser.add_argument("prompt", nargs="?", help="Task request")
    parser.add_argument("--prompt-file", help="Read the task request from this file")
    parser.add_argument("--working-dir", help="Existing workspace for the agent")
    parser.add_argument("--result-json", help="Write the structured run result here")
    parser.add_argument(
        "--reasoning-policy",
        choices=REASONING_POLICIES,
        default=DEFAULT_REASONING_POLICY,
        help="Explicit-reasoning policy (default: %(default)s)",
    )
    parser.add_argument(
        "--max-replans",
        type=_positive_int,
        default=MAX_REPLANS,
        help="Maximum planning attempts, including the initial plan",
    )
    parser.add_argument(
        "--max-tasks",
        type=_positive_int,
        default=MAX_TASKS,
        help="Maximum tasks accepted from each plan",
    )
    parser.add_argument(
        "--max-steps",
        type=_positive_int,
        default=MAX_STEPS,
        help="Maximum executor steps per task attempt",
    )
    parser.add_argument(
        "--goal-context-chars",
        type=_positive_int,
        default=GOAL_CONTEXT_CHARS,
        help="Frozen goal characters available to executor and task replanner",
    )
    args = parser.parse_args(argv)

    if (args.prompt is None) == (args.prompt_file is None):
        parser.error("provide exactly one of prompt or --prompt-file")

    if args.prompt_file is not None:
        try:
            user_prompt = Path(args.prompt_file).read_text()
        except OSError as e:
            parser.error(f"cannot read --prompt-file: {e}")
    else:
        user_prompt = args.prompt
    if not user_prompt or not user_prompt.strip():
        parser.error("prompt must not be empty")

    if args.working_dir is None:
        working_dir = None  # run_result creates and records an isolated workspace
    else:
        workspace = Path(args.working_dir)
        if not workspace.is_dir():
            parser.error("--working-dir must name an existing directory")
        working_dir = str(workspace)

    # Immutable per-run configuration loaded from the environment at the CLI
    # boundary (issue #40); parsed arguments override policy and budgets.
    config = _dataclass_replace(
        RunConfig.from_env(),
        reasoning_policy=args.reasoning_policy,
        max_replans=args.max_replans,
        max_tasks=args.max_tasks,
        max_steps=args.max_steps,
        goal_context_chars=args.goal_context_chars,
    )
    result = run_result(user_prompt, working_dir=working_dir, config=config)
    if args.result_json:
        try:
            Path(args.result_json).write_text(json.dumps(result, indent=2, default=str) + "\n")
        except OSError as e:
            parser.error(f"cannot write --result-json: {e}")
    return 0 if result["status"] in COMPLETE_STATUSES else 1


if __name__ == "__main__":
    sys.exit(_main())
