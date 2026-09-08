"""Provider settings, native response codecs, and the injectable LLM client.

Importing this module does not load .env, configure the CLI, or call a provider.
Standalone clients take explicit immutable settings and optional transport/sinks;
askme.py supplies the historical environment and logging compatibility defaults.
"""

import json
import os
import re
import time
from dataclasses import dataclass
from dataclasses import replace as _dataclass_replace

import requests

from actions import (
    ACTION_SPECS,
    ActionEnvelope,
    ActionProtocolError,
    ActionTransport,
    DecodedAction,
    _valid_nonempty_str,
    parse_action_envelope,
)

REASONING_POLICIES = ("gated", "off")


def _ignore(_value):
    """Standalone clients are silent unless the caller supplies a sink."""


_EFFORT_RANK = {"low": 0, "medium": 1, "high": 2}


def _parse_reasoning_effort(raw):
    effort = (raw or "").strip().lower()
    if effort and effort not in _EFFORT_RANK:
        raise ValueError("OPENROUTER_REASONING_EFFORT must be low, medium, or high (or unset)")
    return effort


LLM_TIMEOUT = 120  # seconds; covers slow first-token on local LLM
LLM_TIMEOUT_REPLAN = 180  # replans carry heavier state + thinking
MAX_LLM_RETRIES = 2

OPENROUTER_CHAT_API = "https://openrouter.ai/api/v1/chat/completions"
_OPENROUTER_DEFAULT_MODEL = "google/gemma-4-26b-a4b-it"


@dataclass(frozen=True)
class CapabilityProfile:
    """Model-facing context and output limits for one immutable run.

    The original limits were selected from the transport backend, which made
    two identical models receive different contracts depending on where they
    were served. Profiles make that contract explicit and hashable (issue
    #68). ``context_window`` and ``server_slots`` are optional declared
    deployment expectations; generic profiles do not invent server topology.
    """

    name: str
    step_tokens: int
    step_write_tokens: int
    planner_tokens: int = 768
    task_replan_tokens: int = 96
    validation_tokens: int = 768
    reasoning_token_floors: tuple[int, int, int] = (1024, 1536, 2048)
    context_window: int | None = None
    server_slots: int | None = None

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("capability profile name must be non-empty")
        for field_name in (
            "step_tokens",
            "step_write_tokens",
            "planner_tokens",
            "task_replan_tokens",
            "validation_tokens",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        for field_name in ("context_window", "server_slots"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 1
            ):
                raise ValueError(f"{field_name} must be a positive integer or None")
        floors = self.reasoning_token_floors
        if (
            not isinstance(floors, tuple)
            or len(floors) != 3
            or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 1
                for value in floors
            )
        ):
            raise ValueError("reasoning_token_floors must contain three positive integers")

    def describe(self):
        return {
            "name": self.name,
            "step_tokens": self.step_tokens,
            "step_write_tokens": self.step_write_tokens,
            "planner_max_tokens": self.planner_tokens,
            "task_replan_max_tokens": self.task_replan_tokens,
            "final_validation_max_tokens": self.validation_tokens,
            "reasoning_token_floors": dict(
                zip(("low", "medium", "high"), self.reasoning_token_floors)
            ),
            "context_window": self.context_window,
            "server_slots": self.server_slots,
        }


_LEGACY_E4B_PROFILE = CapabilityProfile(
    name="legacy-e4b-m1-16k-v1",
    step_tokens=256,
    step_write_tokens=512,
    reasoning_token_floors=(512, 512, 768),
    context_window=16384,
    server_slots=1,
)
_GENERAL_PROFILE = CapabilityProfile(
    name="generic-feature-scale-v1",
    step_tokens=4096,
    step_write_tokens=8192,
)
_CAPABILITY_PROFILES = {
    _GENERAL_PROFILE.name: _GENERAL_PROFILE,
    _LEGACY_E4B_PROFILE.name: _LEGACY_E4B_PROFILE,
}
CAPABILITY_PROFILE_NAMES = tuple(_CAPABILITY_PROFILES)


def get_capability_profile(name):
    """Return a built-in immutable profile by name."""
    try:
        return _CAPABILITY_PROFILES[name]
    except KeyError as exc:
        choices = ", ".join(CAPABILITY_PROFILE_NAMES)
        raise ValueError(f"LLM_CAPABILITY_PROFILE must be one of {choices}") from exc


def _default_capability_profile():
    """Return the generic contract; legacy reproduction is always explicit."""
    return _GENERAL_PROFILE


def _capability_profile_from_env(env):
    requested = (env.get("LLM_CAPABILITY_PROFILE") or "").strip()
    return get_capability_profile(requested) if requested else _default_capability_profile()


# Executor action transport (issue #68): native tool calls only. The paired
# 2026-08-04 local bench retired the historical JSON envelope-in-text
# contract together with its repair/sentinel salvage; planner, task-replan,
# and validation responses remain plain JSON because they are not actions.
ACTION_TRANSPORT = "tools"


@dataclass(frozen=True)
class LLMSettings:
    """Immutable client-local LLM configuration (issue #37).

    ``from_env`` reads a supplied mapping without loading a dotenv file.
    The askme facade owns its env-derived compatibility mirrors and
    ``current`` snapshot. Distinct settings let two clients share one
    process without global leakage. Run composition resolves compatibility
    defaults into one request-policy snapshot before the first call.
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
    # Compatibility overrides from the pre-profile API. Run resolution folds
    # either value into a detached custom profile, and ``current`` snapshots
    # patched module globals directly into its profile.
    step_write_tokens: int | None = None
    step_token_budget: int | None = None
    # Run composition resolves these optional compatibility defaults once.
    # Keeping them on the client settings makes the request policy travel
    # with a pinned/injected client instead of consulting module globals
    # after a run has started.
    replan_timeout: int | None = None
    max_retries: int | None = None
    reasoning_token_floors: tuple[int, int, int] | None = None
    # Added at the end to preserve positional compatibility. None resolves to
    # the generic profile; transport/provider and model aliases never select it.
    capability_profile: CapabilityProfile | None = None

    def resolved_capability_profile(self):
        """Return the effective detached profile, including legacy overrides."""
        profile = self.capability_profile or _default_capability_profile()
        for field_name in ("step_token_budget", "step_write_tokens"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 1
            ):
                raise ValueError(f"{field_name} must be a positive integer")
        step_tokens = (
            profile.step_tokens if self.step_token_budget is None else self.step_token_budget
        )
        write_tokens = (
            profile.step_write_tokens if self.step_write_tokens is None else self.step_write_tokens
        )
        floors = (
            profile.reasoning_token_floors
            if self.reasoning_token_floors is None
            else self.reasoning_token_floors
        )
        if (
            not isinstance(floors, tuple)
            or len(floors) != 3
            or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 1
                for value in floors
            )
        ):
            raise ValueError("reasoning_token_floors must contain three positive integers")
        if (
            step_tokens == profile.step_tokens
            and write_tokens == profile.step_write_tokens
            and floors == profile.reasoning_token_floors
        ):
            return profile
        return _dataclass_replace(
            profile,
            name=f"{profile.name}+custom",
            step_tokens=step_tokens,
            step_write_tokens=write_tokens,
            reasoning_token_floors=floors,
        )

    def write_retry_tokens(self):
        """Decode-retry budget for truncated write/edit payloads."""
        return self.resolved_capability_profile().step_write_tokens

    def step_tokens(self):
        """Executor step budget for this run (issues #15/#40/#68)."""
        return self.resolved_capability_profile().step_tokens

    def resolved_reasoning_token_floors(self):
        """Low/medium/high HTTP completion floors for reasoning requests."""
        return self.resolved_capability_profile().reasoning_token_floors

    def reasoning_token_floor(self, effort):
        return dict(zip(("low", "medium", "high"), self.resolved_reasoning_token_floors()))[effort]

    def reasoning_token_floor_metadata(self):
        return dict(zip(("low", "medium", "high"), self.resolved_reasoning_token_floors()))

    @classmethod
    def from_env(cls, env=None):
        """Derive settings from an environment mapping (default os.environ)."""
        return cls._from_env(env)

    @classmethod
    def _from_env(
        cls,
        env=None,
        *,
        timeout=LLM_TIMEOUT,
        replan_timeout=LLM_TIMEOUT_REPLAN,
        max_retries=MAX_LLM_RETRIES,
        openrouter_api=OPENROUTER_CHAT_API,
        openrouter_default_model=_OPENROUTER_DEFAULT_MODEL,
    ):
        """One environment derivation with explicit compatibility defaults."""
        e = os.environ if env is None else env
        backend = e.get("LLM_BACKEND", "local")  # "local" or "openrouter"
        if backend == "openrouter":
            api = openrouter_api
            model = e.get("OPENROUTER_MODEL", openrouter_default_model)
        else:
            api = e.get("LLM_API_URL", "http://localhost:8080/v1/chat/completions")
            model = e.get("LLM_MODEL", "local-model")
        return cls(
            backend=backend,
            api=api,
            model=model,
            api_key=e.get("OPENROUTER_API_KEY", ""),
            provider=e.get("OPENROUTER_PROVIDER", "Parasail").strip(),
            allow_fallbacks=e.get("OPENROUTER_ALLOW_FALLBACKS", "1") == "1",
            require_parameters=e.get("OPENROUTER_REQUIRE_PARAMETERS", "0") == "1",
            reasoning_effort=_parse_reasoning_effort(e.get("OPENROUTER_REASONING_EFFORT")),
            timeout=timeout,
            capability_profile=_capability_profile_from_env(e),
            replan_timeout=replan_timeout,
            max_retries=max_retries,
        )


def _merge_effort(think_level, baseline=""):
    """Effort to request from OpenRouter: the gated level, raised to the
    explicit baseline. Falsy result means
    request reasoning disabled (hybrid contract)."""
    if baseline and think_level:
        return max(baseline, think_level, key=_EFFORT_RANK.__getitem__)
    return baseline or think_level


class LLMTransportError(Exception):
    """Raised when all LLM request retries are exhausted."""

    pass


_TOOL_FIELD_SCHEMAS = {
    "arg": {"type": "string"},
    "content": {"type": "string"},
    "append": {"type": "boolean"},
    "find": {"type": "string"},
    "replace": {"type": "string"},
    "offset": {"type": "integer"},
    "limit": {"type": "integer"},
    "cursor": {"type": "integer"},
    "sha256": {"type": "string"},
    "path": {"type": "string"},
    "timeout": {"type": "integer"},
    "reasoning": {"type": "string"},
}
_TOOL_DESCRIPTIONS = {
    "shell": "Run a shell command in the working directory.",
    "write": "Create or overwrite a file; set append=true to append the next chunk.",
    "edit": "Replace exact text in a file.",
    "read": "Read a file window; continuation pages echo the output's cursor/limit/sha256.",
    "search": "Bounded literal search; pattern in arg, optional path (default '.').",
    "tree": "Bounded directory listing; directory in arg (default '.').",
    "done": "Declare the FULL task complete.",
    "fail": "Give up on the task; one-line reason in arg.",
}


def _action_tools():
    """OpenAI-style tool definitions for the registry's action set."""
    tools = []
    for name, spec in ACTION_SPECS.items():
        fields = [field for field in spec.allowed if field != "action"]
        parameters = {
            "type": "object",
            "properties": {field: dict(_TOOL_FIELD_SCHEMAS[field]) for field in fields},
        }
        if spec.requires:
            parameters["required"] = list(spec.requires)
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": _TOOL_DESCRIPTIONS[name],
                    "parameters": parameters,
                },
            }
        )
    return tools


_ACTION_TOOLS = _action_tools()


def _repair_json(text):
    """Return a semantics-preserving JSON object repair, or ``None``.

    Safe trailing prose extraction and insertion-only container completion
    are allowed.  A partial key/value, string, or scalar is never deleted,
    defaulted, or rewritten to make an action executable (issue #79).
    """
    if not text or "{" not in text:
        return None
    candidate = text[text.index("{") :].strip()
    try:
        obj, end = json.JSONDecoder().raw_decode(candidate)
        remainder = candidate[end:].lstrip()
        if isinstance(obj, dict) and (not remainder or remainder[0] not in '{}[],:"'):
            return obj
    except json.JSONDecodeError:
        pass

    # A trailing comma is syntax only; removing exactly that delimiter does
    # not remove or default an action field.
    without_trailing_comma = re.sub(r",(\s*})\s*$", r"\1", candidate)
    if without_trailing_comma != candidate:
        try:
            # The candidate begins at its first ``{``, so any successful
            # parse is necessarily an object; there is no scalar/list arm.
            return json.loads(without_trailing_comma)
        except json.JSONDecodeError:
            pass

    stack = []
    in_string = False
    escaped = False
    pairs = {"}": "{", "]": "["}
    for char in candidate:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack or stack.pop() != pairs[char]:
                return None
    if in_string or escaped or not stack:
        return None
    if candidate.rstrip().endswith((",", ":")):
        return None
    # A number at EOF may itself be truncated (``12`` vs ``120``).  Only
    # close a container after a value token with an unambiguous terminator:
    # string, true/false, null, object, or list.
    if candidate.rstrip()[-1] not in {'"', "e", "l", "}", "]"}:
        return None
    suffix = "".join("}" if opener == "{" else "]" for opener in reversed(stack))
    try:
        obj = json.loads(candidate + suffix)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _validate_action_contract(obj):
    """Return True for planner/validator dicts and complete action dicts.

    Required fields and per-action contracts come from ACTION_SPECS (issue
    #36). Unknown actions pass here so the run loop can record them as an
    executed step with a typed dispatch error, not a decode failure.
    """
    typed = isinstance(obj, (ActionEnvelope, DecodedAction))
    if not typed and (not isinstance(obj, dict) or "action" not in obj):
        return True
    parsed = parse_action_envelope(obj)
    if isinstance(parsed, ActionProtocolError) and parsed.error_type == "unknown_action":
        return True
    return isinstance(parsed, ActionEnvelope)


def _accept_or_raise(obj, text, transport=None):
    if not isinstance(obj, dict) or "action" not in obj:
        return obj
    parsed = parse_action_envelope(obj)
    if isinstance(parsed, ActionEnvelope):
        return DecodedAction(parsed, transport or ActionTransport())
    if parsed.error_type == "unknown_action":
        # Preserve the historical permissive decoder; the action response
        # schema supplies the typed unknown_action classification.
        return obj
    error = json.JSONDecodeError(f"Incomplete action: {parsed.message}", text, 0)
    setattr(error, "action_protocol_error", parsed)
    raise error


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
    typed = isinstance(obj, (ActionEnvelope, DecodedAction))
    if not typed and (not isinstance(obj, dict) or not obj):
        return "malformed_action"
    parsed = parse_action_envelope(obj)
    return parsed.error_type if isinstance(parsed, ActionProtocolError) else None


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
# Each schema receives the caller's ``expect_context`` so per-run limits (for
# example the plan's configured max_tasks) shape validation exactly like the
# consuming call site will.
def _response_schemas(max_tasks=lambda: 10):
    """Build the canonical response contracts with an explicit planner-limit default."""
    return {
        "plan": lambda obj, context: (
            PlanResponse.parse(obj, (context or {}).get("max_tasks", max_tasks())) is not None
        ),
        "action": lambda obj, context: _action_envelope_error(obj) is None,
        "task_replan": lambda obj, context: TaskReplanResponse.parse(obj) is not None,
        "validation": lambda obj, context: ValidationResponse.parse(obj) is not None,
    }


RESPONSE_SCHEMAS = _response_schemas()


_STRICT_JSON_SUFFIX = (
    "Output ONLY the JSON object. No reasoning, no explanation, no text outside the JSON."
)
_STRICT_TOOL_SUFFIX = "Call exactly one tool now. No reasoning, no text outside the tool call."

# Truncated write/edit payloads are the most common large-output parse failure;
# detect the attempted action so the retry gets a payload-sized budget.
_WRITE_ATTEMPT_RE = re.compile(r'"action"\s*:\s*"(?:write|edit)"')


# LLM client seams (issue #37): reasoning decision, request build, one-shot
# transport, and pure reply decode are independent, individually testable
# steps. LLMClient owns retry policy, backoff, and typed errors; askme's
# compatibility adapters supply its patchable defaults explicitly.


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


def _build_llm_request(messages, budget, effective_think_level, strict, settings=None, expect=None):
    """Build one backend-specific request: (body, headers, sent_effort).

    `strict` appends the E03 strict contract as a final user turn after
    backend shaping. Never mutates the caller's message list. `settings`
    defaults to an explicit environment derivation when omitted.
    An ``expect="action"`` call carries the registry-derived tool definitions
    (issue #68); every other response type keeps the plain JSON contract."""
    cfg = LLMSettings.from_env() if settings is None else settings
    body = {"model": cfg.model, "messages": messages, "temperature": 0.1, "max_tokens": budget}
    tools_transport = expect == "action"
    if tools_transport:
        body["tools"] = _ACTION_TOOLS
        # "auto", never "required": grammar-forced tool choice corrupted the
        # native Gemma 4 string delimiters into argument text on the b9618
        # PEG parser (2026-08-04 smoke); empty replies are handled by the
        # caller's normal parse-retry policy instead.
        body["tool_choice"] = "auto"
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
            body["max_tokens"] = max(budget, cfg.reasoning_token_floor(sent_effort))
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
        body["max_tokens"] = max(budget, cfg.reasoning_token_floor(effective_think_level))
    if strict:
        msgs = list(body["messages"])
        msgs.append(
            {
                "role": "user",
                "content": _STRICT_TOOL_SUFFIX if tools_transport else _STRICT_JSON_SUFFIX,
            }
        )
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
    `api` defaults to the environment-derived endpoint when omitted."""
    if post is None:
        post = requests.post
    if api is None:
        api = LLMSettings.from_env().api
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
    """Pure decode of one JSON text reply (plan/replan/validation) into a dict.

    Owns reasoning/fence stripping and JSON extraction/repair. Actions no
    longer travel as text — the executor decodes native tool calls via
    :func:`_decode_tool_call_reply` — but an action-shaped object here still
    lands in the envelope contract for ``ask_llm`` compatibility. Returns
    (obj, cleaned_text, repaired). Raises json.JSONDecodeError carrying a
    .cleaned_text attribute when no valid object can be recovered; retry
    policy and typed classification stay with the caller."""
    # Strip <think>...</think> (closed) or <think>... (unclosed, truncated at max_tokens)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"<think>.*", "", text, flags=re.DOTALL).strip()
    # Strip <|channel>...<channel|> blocks (local llama-server thinking format)
    text = re.sub(r"<\|channel\>.*?<channel\|>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"<\|channel\>.*", "", text, flags=re.DOTALL).strip()
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Try to extract JSON object from anywhere in the text
    if not text.startswith("{") and "{" in text:
        text = text[text.index("{") :]
    try:
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise json.JSONDecodeError(
                "Expected JSON object, got " + type(parsed).__name__, text, 0
            )
        return _accept_or_raise(parsed, text), text, False
    except json.JSONDecodeError as parse_err:
        # A length-truncated reply must retry; other replies may use
        # insertion-only repair.
        repaired = None if finish_reason == "length" else _repair_json(text)
        if repaired is not None:
            try:
                return _accept_or_raise(repaired, text), text, True
            except json.JSONDecodeError as repaired_err:
                if hasattr(repaired_err, "action_protocol_error"):
                    setattr(parse_err, "action_protocol_error", repaired_err.action_protocol_error)
                pass
        setattr(parse_err, "cleaned_text", text)
        raise


def _tool_reply_error(reason, cleaned):
    error = json.JSONDecodeError(reason, cleaned or "", 0)
    setattr(error, "cleaned_text", cleaned or "")
    return error


def _decode_tool_call_reply(rj, finish_reason):
    """Decode one native tool-call reply into the action-envelope contract.

    Mirrors :func:`_decode_action_reply`'s return/raise contract so the
    client's retry policy, budget escalation, and typed classification apply
    unchanged. The synthesized ``cleaned_text`` embeds the attempted action
    name in envelope form so a truncated write/edit argument payload still
    triggers the caller's write-budget escalation."""
    msg = (rj.get("choices") or [{}])[0].get("message") or {}
    calls = msg.get("tool_calls") or []
    if not calls:
        raise _tool_reply_error(
            "reply contains no tool call", (msg.get("content") or "").strip()[:200]
        )
    if len(calls) > 1:
        names = ", ".join(str((call.get("function") or {}).get("name")) for call in calls)
        raise _tool_reply_error("reply contains multiple tool calls", names[:200])
    function = calls[0].get("function") or {}
    name = function.get("name")
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        raw_arguments = arguments
    elif isinstance(arguments, dict):
        raw_arguments = json.dumps(arguments)
    else:
        raw_arguments = ""
    cleaned = f'{{"action": "{name}", "arguments": {raw_arguments[:400]}}}'
    if not isinstance(name, str) or not name:
        raise _tool_reply_error("tool call is missing a function name", cleaned)
    try:
        args = json.loads(raw_arguments) if raw_arguments.strip() else {}
    except json.JSONDecodeError:
        suffix = " (truncated)" if finish_reason == "length" else ""
        raise _tool_reply_error(
            f"tool call arguments are not valid JSON{suffix}", cleaned
        ) from None
    if not isinstance(args, dict):
        raise _tool_reply_error("tool call arguments must be a JSON object", cleaned)
    if "action" in args:
        raise _tool_reply_error("tool call arguments may not carry an action field", cleaned)
    envelope = {"action": name, **args}
    return _accept_or_raise(envelope, cleaned, ActionTransport()), cleaned, False


def _log_llm_usage(
    rj, sent_effort, attempt, finish_reason, settings=None, log_sink=None, event_sink=None
):
    """Console + JSONL usage/route telemetry for one decoded HTTP success.

    Omitted settings use an environment derivation; omitted sinks are silent.
    The askme compatibility adapter supplies its own default sinks."""
    cfg = LLMSettings.from_env() if settings is None else settings
    emit = _ignore if log_sink is None else log_sink
    record = _ignore if event_sink is None else event_sink
    usage = rj.get("usage") or {}
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
    metadata_model = selected.get("model")
    response_model = rj.get("model")
    if metadata_model:
        served_model = metadata_model
        served_model_source = "openrouter_metadata"
    elif response_model:
        served_model = response_model
        served_model_source = "response"
    else:
        served_model = None
        served_model_source = "unobserved"
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
            "usage_observed": bool(usage),
            # ``model`` remains the backwards-compatible display field. The
            # benchmark contract consumes only ``served_model``: substituting
            # the requested alias when a provider omits route metadata would
            # otherwise manufacture evidence that the expected model served.
            "model": served_model or cfg.model,
            "requested_model": cfg.model,
            "served_model": served_model,
            "served_model_source": served_model_source,
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

    Settings are explicit; omitted sinks are silent. The askme facade
    supplies its historical defaults without a back-import. Constructing
    clients explicitly gives two backends/models in one process without
    global leakage. An explicit schema mapping stays bound to its client;
    an optional provider supports late registry lookup for compatibility.
    """

    def __init__(
        self,
        settings,
        post=None,
        sleep=None,
        log_sink=None,
        event_sink=None,
        *,
        response_schemas=None,
        response_schemas_provider=None,
    ):
        if response_schemas is not None and response_schemas_provider is not None:
            raise ValueError("Choose response_schemas or response_schemas_provider, not both")
        self.settings = settings
        self._schemas = RESPONSE_SCHEMAS if response_schemas is None else response_schemas
        self._schemas_provider = response_schemas_provider
        # The default transport resolves requests.post at call time.
        self._post = post
        self._sleep = time.sleep if sleep is None else sleep
        self._log = _ignore if log_sink is None else log_sink
        self._event = _ignore if event_sink is None else event_sink

    def _schema_registry(self):
        """Resolve at admission and response validation, as the legacy facade did."""
        return self._schemas if self._schemas_provider is None else self._schemas_provider()

    def ask(
        self,
        messages,
        max_tokens=256,
        think=False,
        think_level=None,
        max_retries=MAX_LLM_RETRIES,
        raw=False,
        timeout=None,
        reasoning_policy="gated",
        reasoning_trigger="unspecified",
        expect=None,
        expect_context=None,
    ):
        """Call the backend and decode one plan/action/validator reply.

        This loop owns only retry/backoff policy, the parse-retry budget
        escalation, and the typed errors callers rely on (LLMTransportError,
        KeyError for API-error bodies, json.JSONDecodeError with
        malformed_action/response_truncated). ``expect`` names the response
        schema this call site accepts (issue #68): a decoded envelope of the
        wrong type — empty, cross-type, or an unknown action — is retried
        like any parse failure and raises typed after the retry budget.
        ``expect_context`` passes the call site's per-run limits (for example
        the plan's configured max_tasks) into that schema."""
        if reasoning_policy not in REASONING_POLICIES:
            raise ValueError(f"reasoning_policy must be one of {', '.join(REASONING_POLICIES)}")
        if expect is not None:
            schemas = self._schema_registry()
            if expect not in schemas:
                raise ValueError(f"expect must be one of {', '.join(sorted(schemas))}")
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
                expect=expect,
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
                if expect == "action":
                    obj, _decoded_text, repaired = _decode_tool_call_reply(rj, finish_reason)
                else:
                    obj, _decoded_text, repaired = _decode_action_reply(text, finish_reason)
            except json.JSONDecodeError as parse_err:
                cleaned = getattr(parse_err, "cleaned_text", "")
                if attempt < max_retries:
                    # Action-specific budget: a truncated write/edit payload
                    # needs room for content, not more reasoning. The bound
                    # follows this client's capability profile (Codex P2, PR #61).
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
            if expect is not None and not self._schema_registry()[expect](obj, expect_context):
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
