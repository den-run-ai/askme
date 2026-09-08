#!/usr/bin/env python3
"""Minimal self-contained agent. Takes a user prompt, plans, executes, replans on failure.
Requires: requests. Expects llama-server on localhost:8080."""

import argparse
import json
import os
import sys
import time
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import replace as _dataclass_replace
from pathlib import Path

import requests

import actions as _actions
import llm as _llm
import loop as _loop
import policies as _policies

hashlib = _loop.hashlib
re = _loop.re
shlex = _loop.shlex
shutil = _loop.shutil
tempfile = _loop.tempfile
Any = _loop.Any
NamedTuple = _loop.NamedTuple
field = _loop.field
OBSERVE_ACTIONS = _actions.OBSERVE_ACTIONS
OBSERVE_STATE_CHARS = _actions.OBSERVE_STATE_CHARS
ActionEnvelope = _actions.ActionEnvelope
ActionExecutor = _actions.ActionExecutor
ActionProtocolError = _actions.ActionProtocolError
ActionResult = _actions.ActionResult
ActionTransport = _actions.ActionTransport
DecodedAction = _actions.DecodedAction
SkippedStep = _actions.SkippedStep
StepReceipt = _actions.StepReceipt
_mutation_target_key = _actions._mutation_target_key
_step_path = _actions._step_path
_valid_nonempty_str = _actions._valid_nonempty_str
parse_action_envelope = _actions.parse_action_envelope

# Policy compatibility re-exports: one canonical implementation in policies.py.
Enum = _policies.Enum
RunOutcome = _policies.RunOutcome
ValidationState = _policies.ValidationState
WriteObligations = _policies.WriteObligations
_StepFlow = _policies._StepFlow
_VALIDATE_KEYWORDS = _policies._VALIDATE_KEYWORDS
_clear_pending_empty_writes = _policies._clear_pending_empty_writes
_completion_blocker = _policies._completion_blocker
_has_new_validation_evidence = _policies._has_new_validation_evidence
_incomplete_step_hint = _policies._incomplete_step_hint
_incomplete_write_visibility = _policies._incomplete_write_visibility
_next_pending_empty = _policies._next_pending_empty
_pending_append_targets = _policies._pending_append_targets
_pending_empty_hint = _policies._pending_empty_hint
_pending_empty_recovery = _policies._pending_empty_recovery
_read_continuation_hint = _policies._read_continuation_hint
_restrictive_pending_empty = _policies._restrictive_pending_empty
_unresolved_incomplete_writes = _policies._unresolved_incomplete_writes
_write_visibility_flag = _policies._write_visibility_flag

# Client compatibility re-exports: one canonical implementation in llm.py.
ACTION_TRANSPORT = _llm.ACTION_TRANSPORT
CAPABILITY_PROFILE_NAMES = _llm.CAPABILITY_PROFILE_NAMES
CapabilityProfile = _llm.CapabilityProfile
LLMTransportError = _llm.LLMTransportError
LLM_TIMEOUT = _llm.LLM_TIMEOUT
LLM_TIMEOUT_REPLAN = _llm.LLM_TIMEOUT_REPLAN
MAX_LLM_RETRIES = _llm.MAX_LLM_RETRIES
OPENROUTER_CHAT_API = _llm.OPENROUTER_CHAT_API
PlanResponse = _llm.PlanResponse
TaskReplanResponse = _llm.TaskReplanResponse
ValidationResponse = _llm.ValidationResponse
_ACTION_TOOLS = _llm._ACTION_TOOLS
_CAPABILITY_PROFILES = _llm._CAPABILITY_PROFILES
_EFFORT_RANK = _llm._EFFORT_RANK
_GENERAL_PROFILE = _llm._GENERAL_PROFILE
_LEGACY_E4B_PROFILE = _llm._LEGACY_E4B_PROFILE
_OPENROUTER_DEFAULT_MODEL = _llm._OPENROUTER_DEFAULT_MODEL
_STRICT_JSON_SUFFIX = _llm._STRICT_JSON_SUFFIX
_STRICT_TOOL_SUFFIX = _llm._STRICT_TOOL_SUFFIX
_TOOL_DESCRIPTIONS = _llm._TOOL_DESCRIPTIONS
_TOOL_FIELD_SCHEMAS = _llm._TOOL_FIELD_SCHEMAS
_WRITE_ATTEMPT_RE = _llm._WRITE_ATTEMPT_RE
_accept_or_raise = _llm._accept_or_raise
_action_envelope_error = _llm._action_envelope_error
_action_tools = _llm._action_tools
_capability_profile_from_env = _llm._capability_profile_from_env
_decode_action_reply = _llm._decode_action_reply
_decode_tool_call_reply = _llm._decode_tool_call_reply
_default_capability_profile = _llm._default_capability_profile
_extract_message_text = _llm._extract_message_text
_parse_reasoning_effort = _llm._parse_reasoning_effort
_reasoning_decision = _llm._reasoning_decision
_repair_json = _llm._repair_json
_tool_reply_error = _llm._tool_reply_error
_validate_action_contract = _llm._validate_action_contract
get_capability_profile = _llm.get_capability_profile

# Compatibility re-exports (issue #36): the action layer lives in actions.py;
# tests and downstream code keep importing these names from askme.
ACTION_SPECS = _actions.ACTION_SPECS
MAX_RESULT = _actions.MAX_RESULT
READ_CHARS = _actions.READ_CHARS
READ_LIMIT_MAX = _actions.READ_LIMIT_MAX
READ_LINES = _actions.READ_LINES
SEARCH_MAX_CHARS = _actions.SEARCH_MAX_CHARS
SEARCH_MAX_FILES = _actions.SEARCH_MAX_FILES
SEARCH_MAX_MATCHES = _actions.SEARCH_MAX_MATCHES
SHELL_TIMEOUT = _actions.SHELL_TIMEOUT
_target_recovery_arg = _actions._target_recovery_arg
_read_key = _actions._read_key
_get_shell_timeout = _actions._get_shell_timeout
SHELL_TIMEOUT_MAX = _actions.SHELL_TIMEOUT_MAX
SHELL_TIMEOUT_LONG = _actions.SHELL_TIMEOUT_LONG
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


@dataclass(frozen=True)
class LLMSettings(_llm.LLMSettings):
    """Compatibility settings: immutable data with patchable askme defaults."""

    @classmethod
    def from_env(cls, env=None):
        return cls._from_env(
            env,
            timeout=LLM_TIMEOUT,
            replan_timeout=LLM_TIMEOUT_REPLAN,
            max_retries=MAX_LLM_RETRIES,
            openrouter_api=OPENROUTER_CHAT_API,
            openrouter_default_model=_OPENROUTER_DEFAULT_MODEL,
        )

    @classmethod
    def current(cls):
        """Snapshot the module-level (patchable) configuration."""
        try:
            named_capabilities = get_capability_profile(CAPABILITY_PROFILE)
        except ValueError:
            named_capabilities = None
        topology_base = named_capabilities or _DEFAULT_CAPABILITY_PROFILE
        capabilities = CapabilityProfile(
            name=CAPABILITY_PROFILE,
            step_tokens=STEP_TOKENS,
            step_write_tokens=STEP_WRITE_TOKENS,
            planner_tokens=PLANNER_MAX_TOKENS,
            task_replan_tokens=TASK_REPLAN_MAX_TOKENS,
            validation_tokens=VALIDATION_MAX_TOKENS,
            reasoning_token_floors=REASONING_TOKEN_FLOORS,
            context_window=topology_base.context_window,
            server_slots=topology_base.server_slots,
        )
        if named_capabilities is not None and capabilities != named_capabilities:
            capabilities = _dataclass_replace(capabilities, name=f"{CAPABILITY_PROFILE}+custom")
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
            capability_profile=capabilities,
            replan_timeout=LLM_TIMEOUT_REPLAN,
            max_retries=MAX_LLM_RETRIES,
        )


# Backend config: set LLM_BACKEND=openrouter to use OpenRouter API. These
# module-level mirrors of the one from_env derivation remain the
# compatibility surface that tests and the integration helpers patch;
# ask_llm snapshots them per call via LLMSettings.current().
_DEFAULT_LLM_SETTINGS = LLMSettings.from_env()
_DEFAULT_CAPABILITY_PROFILE = _DEFAULT_LLM_SETTINGS.resolved_capability_profile()
CAPABILITY_PROFILE = _DEFAULT_CAPABILITY_PROFILE.name
REASONING_TOKEN_FLOORS = _DEFAULT_CAPABILITY_PROFILE.reasoning_token_floors
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
    """Merge effort with the patchable compatibility baseline."""
    return _llm._merge_effort(
        think_level, OPENROUTER_REASONING_EFFORT if baseline is None else baseline
    )


# Execution policy — controls what the agent is allowed to do
ALLOW_SYSTEM_INSTALLS = os.environ.get("ALLOW_SYSTEM_INSTALLS", "0") == "1"
ALLOW_NETWORK = os.environ.get("ALLOW_NETWORK", "1") == "1"
# Ablation switch for the tracked #41 C-header repair path. Default on keeps
# current behavior; the preregistered ablation (docs/ablation-compile-repair.md)
# runs its off arm with AGENT_COMPILE_REPAIR=0 at one pinned revision.
COMPILE_REPAIR_ENABLED = os.environ.get("AGENT_COMPILE_REPAIR", "1") == "1"

PROBE_TOOLS = _loop.PROBE_TOOLS
PROBE_PKG_MANAGERS = _loop.PROBE_PKG_MANAGERS


def preflight_probe(working_dir="."):
    """Compatibility probe defaults over the canonical environment probe."""
    return _loop.preflight_probe(
        working_dir, tools=PROBE_TOOLS, package_managers=PROBE_PKG_MANAGERS
    )


def get_policy():
    """Return execution policy dict for planner/executor state."""
    return {
        "allow_system_installs": ALLOW_SYSTEM_INSTALLS,
        "allow_network": ALLOW_NETWORK,
    }


MAX_REPLANS = _loop.MAX_REPLANS  # Total planning attempts (initial plan + up to 2 replans)
MAX_TASKS = _loop.MAX_TASKS
MAX_STEPS = _loop.MAX_STEPS
MAX_STEP_HISTORY = _loop.MAX_STEP_HISTORY  # sliding window sent to executor

# Write-forcing executor policy (issue #15): on a write-shaped task,
# observation may not consume the whole step budget — the 2026-08-01 Qwen
# canary spent all 27 executed steps on tree/read and never selected a write.
WRITE_PRESSURE_OBSERVATIONS = _loop.WRITE_PRESSURE_OBSERVATIONS
OBSERVE_TAIL_RESERVE = 3  # final steps per attempt reserved for commitment
# Validate-after-write policy (revision 4): on a write-shaped task, repeated
# whole-file rewrites of the same target may not consume the step budget —
# the 2026-08-01 v6 Gemma canary rewrote one file 18 times without ever
# verifying it or emitting done.
REWRITE_PRESSURE_WRITES = _loop.REWRITE_PRESSURE_WRITES
REWRITE_SKIP_WRITES = _loop.REWRITE_SKIP_WRITES
# "include" dropped (Codex P2, PR #16): it matched passive phrasing like
# "files that include deprecated.h" and misclassified observation tasks.
_WRITE_TASK_RE = _loop._WRITE_TASK_RE
# A leading observation verb marks inspection intent even when a mutation
# verb appears later ("find where to add the import").
_OBSERVE_TASK_RE = _loop._OBSERVE_TASK_RE


_is_write_shaped = _loop._is_write_shaped


# Observation-action budgets (issue #7): reads/searches/trees are the navigation
# surface for app development; they get their own bounded windows so large repos
# stay navigable without blowing up executor state.
# Model-capability output budgets (issue #68). The named legacy E4B profile
# preserves the original slow-local contract; the general profile preserves
# the feature-scale allowance. Serving backend/provider never selects them.
STEP_TOKENS = _DEFAULT_CAPABILITY_PROFILE.step_tokens
# Retry budget when a truncated write/edit payload fails to parse.
STEP_WRITE_TOKENS = _DEFAULT_CAPABILITY_PROFILE.step_write_tokens
PLANNER_MAX_TOKENS = _DEFAULT_CAPABILITY_PROFILE.planner_tokens
REASONING_POLICIES = _llm.REASONING_POLICIES
DEFAULT_REASONING_POLICY = os.environ.get("AGENT_REASONING_POLICY", "gated").strip().lower()
if DEFAULT_REASONING_POLICY not in REASONING_POLICIES:
    raise ValueError(f"AGENT_REASONING_POLICY must be one of {', '.join(REASONING_POLICIES)}")

# Step-policy arms (issues #31/#68): "heuristic" is today's guard/counter
# baseline; "lifecycle" is the explicit inspect → modify → verify → finish
# alternative. The arm is outcome-affecting per-run configuration — it enters
# the hash-logged config — and only #63/#64 measurements may retire an arm.
STEP_POLICIES = _loop.STEP_POLICIES


def _validated_step_policy(value):
    """Normalize an AGENT_STEP_POLICY value or raise on an unknown arm."""
    policy = value.strip().lower()
    if policy not in STEP_POLICIES:
        raise ValueError(f"AGENT_STEP_POLICY must be one of {', '.join(STEP_POLICIES)}")
    return policy


DEFAULT_STEP_POLICY = _validated_step_policy(os.environ.get("AGENT_STEP_POLICY", "heuristic"))

SYSTEM_PLAN = _loop.SYSTEM_PLAN

SYSTEM_VALIDATE = _loop.SYSTEM_VALIDATE

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


# Executor prompt for the native tool-call transport (issue #68). The former
# JSON-envelope prompt and its sentinel-transport instructions were removed
# with the JSON executor path; native tool syntax carries string arguments
# unescaped, so no escaping or sentinel guidance is needed.
SYSTEM_STEP = _loop.SYSTEM_STEP

# Compatibility adapters resolve askme defaults; llm.py owns the algorithms.
RESPONSE_SCHEMAS = _llm._response_schemas(lambda: MAX_TASKS)


def _build_llm_request(messages, budget, effective_think_level, strict, settings=None, expect=None):
    return _llm._build_llm_request(
        messages,
        budget,
        effective_think_level,
        strict,
        LLMSettings.current() if settings is None else settings,
        expect,
    )


def _llm_http_attempt(body, headers, timeout, post=None, api=None):
    return _llm._llm_http_attempt(
        body,
        headers,
        timeout,
        requests.post if post is None else post,
        API if api is None else api,
    )


def _log_llm_usage(
    rj, sent_effort, attempt, finish_reason, settings=None, log_sink=None, event_sink=None
):
    return _llm._log_llm_usage(
        rj,
        sent_effort,
        attempt,
        finish_reason,
        LLMSettings.current() if settings is None else settings,
        log if log_sink is None else log_sink,
        _run_log if event_sink is None else event_sink,
    )


class LLMClient(_llm.LLMClient):
    """Preserve askme constructor defaults without coupling llm to the facade."""

    def __init__(self, settings=None, post=None, sleep=None, log_sink=None, event_sink=None):
        super().__init__(
            LLMSettings.current() if settings is None else settings,
            # Resolve the facade transport at call time, including whole-module patches.
            (lambda *args, **kwargs: requests.post(*args, **kwargs)) if post is None else post,
            time.sleep if sleep is None else sleep,
            log if log_sink is None else log_sink,
            _run_log if event_sink is None else event_sink,
            response_schemas_provider=lambda: RESPONSE_SCHEMAS,
        )

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
        expect_context=None,
    ):
        return super().ask(
            messages,
            max_tokens,
            think,
            think_level,
            max_retries,
            raw,
            timeout,
            reasoning_policy,
            reasoning_trigger,
            expect,
            expect_context,
        )


_RUN_LLM_SETTINGS = ContextVar("askme_run_llm_settings", default=None)


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
    expect_context=None,
):
    """Call the configured backend and decode one plan/action/validator reply.

    Compatibility facade over LLMClient: snapshots the module-level
    configuration for this call and delegates. Retry/backoff policy, the
    parse-retry budget escalation, response-schema enforcement (``expect`` /
    ``expect_context``), and the typed errors callers rely on
    (LLMTransportError, KeyError for API-error bodies, json.JSONDecodeError
    with malformed_action/response_truncated) live in LLMClient.ask."""
    return LLMClient(settings=_RUN_LLM_SETTINGS.get()).ask(
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
        expect_context=expect_context,
    )


class _FrozenLLMFacade:
    """Run-local adapter retaining the patchable ``ask_llm`` seam.

    Production calls use the immutable settings captured by run composition;
    tests and downstream callers that patch ``ask_llm`` still intercept the
    same supported facade instead of having to patch ``LLMClient`` internals.
    """

    def __init__(self, settings):
        self.settings = settings

    def ask(self, messages, **kwargs):
        token = _RUN_LLM_SETTINGS.set(self.settings)
        try:
            return ask_llm(messages, **kwargs)
        finally:
            _RUN_LLM_SETTINGS.reset(token)


_KNOWN_ERROR_TYPES = _loop._KNOWN_ERROR_TYPES

# E05: Error types where thinking escalation is counterproductive.
# These are structural failures — the scaffold knows what went wrong and the model
# needs different information or parameters, not deeper reasoning.
# Semantic failures (compile_error, unknown) keep thinking escalation.
_NO_THINK_ERRORS = _loop._NO_THINK_ERRORS

# E06: Short recovery hints injected into step output after typed failures.
# Tells the model what to do next without needing thinking tokens to rediscover it.
_RECOVERY_HINTS = _loop._RECOVERY_HINTS


_extract_error_type = _loop._extract_error_type


summarize_errors = _loop.summarize_errors


_step_digest = _loop._step_digest


def _prompt_context():
    """Raw model-facing defaults; constructing these never validates unused settings."""
    return _loop.PromptContext(
        defaults=_loop.PromptDefaults(
            system_plan=SYSTEM_PLAN,
            system_step=SYSTEM_STEP,
            system_task_replan=SYSTEM_TASK_REPLAN,
            system_validate=SYSTEM_VALIDATE,
            planner_tokens=PLANNER_MAX_TOKENS,
            step_tokens=STEP_TOKENS,
            task_replan_tokens=TASK_REPLAN_MAX_TOKENS,
            validation_tokens=VALIDATION_MAX_TOKENS,
            replan_timeout=LLM_TIMEOUT_REPLAN,
            max_tasks=MAX_TASKS,
            max_step_history=MAX_STEP_HISTORY,
            max_input=MAX_INPUT,
            observe_state_chars=OBSERVE_STATE_CHARS,
            reasoning_policy=DEFAULT_REASONING_POLICY,
        ),
        ask=lambda *args, **kwargs: ask_llm(*args, **kwargs),
        policy=lambda: get_policy(),
        log=log,
        deterministic_check=lambda *args: _deterministic_check(*args),
    )


def get_plan(
    user_prompt,
    state,
    client=None,
    max_tokens=None,
    max_tasks=None,
    timeout=None,
    replan_timeout=None,
    max_retries=None,
):
    # Include environment and policy in planner state.
    # Run-control metadata is logged/returned but is not task evidence for the
    # model, and raw step payloads (write contents) never reach the planner —
    # only a curated digest does. This keeps replan state bounded on
    # write-heavy runs while still telling the planner what already happened.
    """Compatibility defaults over the canonical planning implementation."""
    return _loop.get_plan(
        user_prompt,
        state,
        client,
        max_tokens,
        max_tasks,
        timeout,
        replan_timeout,
        max_retries,
        context=_prompt_context(),
    )


MAX_INPUT = _loop.MAX_INPUT  # max chars per non-goal field sent to executor
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
    step_tokens=None,
    timeout=None,
    max_retries=None,
):
    # Build slim step history from recent steps (current task + carryover from previous)
    """Compatibility defaults over the canonical planning implementation."""
    return _loop.get_step(
        task,
        state,
        goal,
        step_num,
        max_steps,
        think,
        reasoning_policy,
        reasoning_trigger,
        goal_context_chars,
        write_pressure,
        validate_pressure,
        client,
        step_tokens,
        timeout,
        max_retries,
        context=_prompt_context(),
    )


SYSTEM_TASK_REPLAN = _loop.SYSTEM_TASK_REPLAN

MAX_TASK_LOCAL_REPLANS = _loop.MAX_TASK_LOCAL_REPLANS
TASK_REPLAN_MAX_TOKENS = _DEFAULT_CAPABILITY_PROFILE.task_replan_tokens


TaskReplanResult = _loop.TaskReplanResult


_PASSIVE_TASK_RE = _loop._PASSIVE_TASK_RE
_ACTION_TASK_RE = _loop._ACTION_TASK_RE
_LOW_VALUE_TASK_WORDS = _loop._LOW_VALUE_TASK_WORDS


_task_keywords = _loop._task_keywords


_task_entities = _loop._task_entities


_task_action_words = _loop._task_action_words


_is_near_duplicate_task = _loop._is_near_duplicate_task


_is_passive_replacement = _loop._is_passive_replacement


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
):
    """Compatibility defaults over the canonical planning implementation."""
    return _loop.replan_task(
        failed_task,
        errors,
        completed_tasks,
        state,
        user_prompt,
        goal_context_chars,
        client,
        max_tokens,
        timeout,
        max_retries,
        context=_prompt_context(),
    )


def _should_validate(replan, history, state, user_prompt, final_validate=None):
    """Compatibility default over the policy module's explicit validation mode."""
    return _policies._should_validate(
        replan,
        history,
        state,
        user_prompt,
        final_validate=FINAL_VALIDATE if final_validate is None else final_validate,
    )


_deterministic_check = _loop._deterministic_check


VALIDATION_MAX_TOKENS = _DEFAULT_CAPABILITY_PROFILE.validation_tokens


def _validate_completion(
    user_prompt,
    state,
    working_dir,
    client=None,
    log_sink=None,
    max_tokens=None,
    timeout=None,
    max_retries=0,
):
    """Compatibility defaults over the canonical planning implementation."""
    return _loop._validate_completion(
        user_prompt,
        state,
        working_dir,
        client,
        log_sink,
        max_tokens,
        timeout,
        max_retries,
        context=_prompt_context(),
    )


_COMPILE_REPAIR_PATTERNS = _loop._COMPILE_REPAIR_PATTERNS


_resolve_existing_candidates = _loop._resolve_existing_candidates


_compile_repair_candidates = _loop._compile_repair_candidates


def _compile_repair_action(error_output, working_dir, cmd, enabled=None):
    """Resolve the compatibility ablation switch; the helper only proposes an action."""
    return _loop._compile_repair_action(
        error_output,
        working_dir,
        cmd,
        enabled=COMPILE_REPAIR_ENABLED if enabled is None else enabled,
    )


_task_satisfied_by_deterministic_repair = _loop._task_satisfied_by_deterministic_repair


def execute(action, working_dir="."):
    """Dispatch one action and return the legacy result dict.

    Compatibility façade: tests and downstream code patch or call this seam,
    so the run loop routes every dispatch — including deterministic retries —
    through it.
    """
    return ActionExecutor(working_dir).dispatch(action).to_dict()


class StepRecorder(_loop.StepRecorder):
    """Compatibility default sink; recording has one canonical implementation."""

    def __init__(self, state, history, event_sink=None):
        super().__init__(
            state,
            history,
            event_sink=(lambda event: _run_log(event)) if event_sink is None else event_sink,
        )


TaskAttemptState = _loop.TaskAttemptState


_StepContext = _loop._StepContext


class RunState(_loop.RunState):
    """Resolve legacy state/recorder defaults without duplicating state ownership."""

    def __init__(
        self,
        reasoning_policy,
        goal_context_chars,
        clock=None,
        event_sink=None,
        rewrite_pressure_writes=None,
        rewrite_skip_writes=None,
    ):
        super().__init__(
            reasoning_policy,
            goal_context_chars,
            clock=time.time if clock is None else clock,
            event_sink=event_sink,
            rewrite_pressure_writes=(
                REWRITE_PRESSURE_WRITES
                if rewrite_pressure_writes is None
                else rewrite_pressure_writes
            ),
            rewrite_skip_writes=(
                REWRITE_SKIP_WRITES if rewrite_skip_writes is None else rewrite_skip_writes
            ),
            recorder_factory=StepRecorder,
        )


GuardThresholds = _loop.GuardThresholds


_config_hash = _loop._config_hash


def _resolve_run_llm_settings(settings):
    """Resolve optional client fields using the compatibility defaults."""
    return _loop._resolve_run_llm_settings(
        settings, replan_timeout=LLM_TIMEOUT_REPLAN, max_retries=MAX_LLM_RETRIES
    )


@dataclass(frozen=True)
class RunConfig(_loop.RunConfig):
    """Immutable run configuration with the historical environment adapter."""

    @classmethod
    def from_env(cls, env=None):
        return cls._from_env(env, settings_factory=LLMSettings.from_env)


RunDependencies = _loop.RunDependencies


RunWorkspace = _loop.RunWorkspace


class StepPolicy(_policies.StepPolicy):
    """Compatibility constructor retaining late shell-timeout defaults."""

    def __init__(self, controller):
        super().__init__(
            controller,
            shell_timeout=lambda *args, **kwargs: _get_shell_timeout(*args, **kwargs),
            timeout_bounds=lambda: (SHELL_TIMEOUT_LONG, SHELL_TIMEOUT_MAX),
        )


class HeuristicStepPolicy(StepPolicy, _policies.HeuristicStepPolicy):
    """Canonical heuristic algorithms with the compatibility constructor."""


class LifecycleStepPolicy(StepPolicy, _policies.LifecycleStepPolicy):
    """The facade constructor forwards callbacks through lifecycle initialization."""


_STEP_POLICY_ARMS = {
    HeuristicStepPolicy.name: HeuristicStepPolicy,
    LifecycleStepPolicy.name: LifecycleStepPolicy,
}


class CompletionPolicy(_policies.CompletionPolicy):
    """Compatibility constructor retaining the late-bound validator seam."""

    def __init__(self, controller):
        super().__init__(
            controller,
            validator=lambda *args, **kwargs: _validate_completion(*args, **kwargs),
            decide_validation=lambda *args, **kwargs: _should_validate(*args, **kwargs),
        )


def _loop_defaults():
    """Unvalidated compatibility values; only selected configuration is resolved."""
    return _loop.LoopDefaults(
        reasoning_policy=DEFAULT_REASONING_POLICY,
        max_replans=MAX_REPLANS,
        max_tasks=MAX_TASKS,
        max_steps=MAX_STEPS,
        goal_context_chars=GOAL_CONTEXT_CHARS,
        allow_system_installs=ALLOW_SYSTEM_INSTALLS,
        allow_network=ALLOW_NETWORK,
        final_validate=FINAL_VALIDATE,
        compile_repair=COMPILE_REPAIR_ENABLED,
        step_policy=DEFAULT_STEP_POLICY,
        write_pressure_observations=WRITE_PRESSURE_OBSERVATIONS,
        observe_tail_reserve=OBSERVE_TAIL_RESERVE,
        rewrite_pressure_writes=REWRITE_PRESSURE_WRITES,
        rewrite_skip_writes=REWRITE_SKIP_WRITES,
        max_task_local_replans=MAX_TASK_LOCAL_REPLANS,
    )


def _loop_hooks():
    """Explicit late-bound compatibility seams, with no back-import or global rebinding."""
    return _loop.LoopHooks(
        current_llm_settings=lambda: LLMSettings.current(),
        clock_factory=lambda: time.time,
        step_policies=lambda: _STEP_POLICY_ARMS,
        get_plan=lambda *args, **kwargs: get_plan(*args, **kwargs),
        get_step=lambda *args, **kwargs: get_step(*args, **kwargs),
        replan_task=lambda *args, **kwargs: replan_task(*args, **kwargs),
        preflight=lambda *args, **kwargs: preflight_probe(*args, **kwargs),
        execute=lambda *args, **kwargs: execute(*args, **kwargs),
        log=lambda *args, **kwargs: log(*args, **kwargs),
        event=lambda *args, **kwargs: _run_log(*args, **kwargs),
        resolve_llm_settings=lambda *args, **kwargs: _resolve_run_llm_settings(*args, **kwargs),
        make_client=lambda *args, **kwargs: LLMClient(*args, **kwargs),
        make_frozen_client=lambda *args, **kwargs: _FrozenLLMFacade(*args, **kwargs),
        make_run_state=lambda *args, **kwargs: RunState(*args, **kwargs),
        make_obligations=lambda *args, **kwargs: WriteObligations(*args, **kwargs),
        make_completion=lambda *args, **kwargs: CompletionPolicy(*args, **kwargs),
        compile_repair=lambda *args, **kwargs: _compile_repair_action(*args, **kwargs),
        repair_satisfied=lambda *args, **kwargs: _task_satisfied_by_deterministic_repair(
            *args, **kwargs
        ),
    )


class _RunController(_loop._RunController):
    """Compatibility composition; controller algorithms live only in loop.py."""

    def __init__(self, user_prompt, working_dir, config=None, dependencies=None):
        super().__init__(
            user_prompt,
            working_dir,
            config=RunConfig() if config is None else config,
            dependencies=RunDependencies() if dependencies is None else dependencies,
            defaults=_loop_defaults(),
            hooks=_loop_hooks(),
        )


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
        "--capability-profile",
        choices=CAPABILITY_PROFILE_NAMES,
        help="Model-facing budget/context profile "
        "(default: LLM_CAPABILITY_PROFILE or generic-feature-scale-v1)",
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
        default=None,
        help="Goal characters available to executor and task replanner "
        "(default: AGENT_GOAL_CONTEXT_CHARS or 300)",
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
    config = RunConfig.from_env()
    if args.capability_profile is not None:
        config = _dataclass_replace(
            config,
            llm=_dataclass_replace(
                config.llm,
                capability_profile=get_capability_profile(args.capability_profile),
            ),
        )
    config = _dataclass_replace(
        config,
        reasoning_policy=args.reasoning_policy,
        max_replans=args.max_replans,
        max_tasks=args.max_tasks,
        max_steps=args.max_steps,
        goal_context_chars=(
            config.goal_context_chars
            if args.goal_context_chars is None
            else args.goal_context_chars
        ),
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
