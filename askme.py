#!/usr/bin/env python3
"""Minimal self-contained agent. Takes a user prompt, plans, executes, replans on failure.
Requires: requests. Expects llama-server on localhost:8080."""

import argparse
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

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
STEP_TOKENS = 4096 if LLM_BACKEND == "openrouter" else 256
# Retry budget when a truncated write/edit payload fails to parse.
STEP_WRITE_TOKENS = 8192 if LLM_BACKEND == "openrouter" else 512
PLANNER_MAX_TOKENS = 768  # 256 thinking + 512 output; shared budget on Parasail/bf16
REASONING_POLICIES = ("gated", "off")
DEFAULT_REASONING_POLICY = os.environ.get("AGENT_REASONING_POLICY", "gated").strip().lower()
if DEFAULT_REASONING_POLICY not in REASONING_POLICIES:
    raise ValueError(f"AGENT_REASONING_POLICY must be one of {', '.join(REASONING_POLICIES)}")

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
    ):
        """Call the backend and decode one plan/action/validator reply.

        This loop owns only retry/backoff policy, the parse-retry budget
        escalation, and the typed errors callers rely on (LLMTransportError,
        KeyError for API-error bodies, json.JSONDecodeError with
        malformed_action/response_truncated)."""
        if reasoning_policy not in REASONING_POLICIES:
            raise ValueError(f"reasoning_policy must be one of {', '.join(REASONING_POLICIES)}")
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
                    # needs room for content, not more reasoning. The budget
                    # constant is decode policy, deliberately not a client
                    # setting.
                    if budget < STEP_WRITE_TOKENS and _WRITE_ATTEMPT_RE.search(cleaned):
                        budget = STEP_WRITE_TOKENS
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
):
    """Call the configured backend and decode one plan/action/validator reply.

    Compatibility facade over LLMClient: snapshots the module-level
    configuration for this call and delegates. Retry/backoff policy, the
    parse-retry budget escalation, and the typed errors callers rely on
    (LLMTransportError, KeyError for API-error bodies, json.JSONDecodeError
    with malformed_action/response_truncated) live in LLMClient.ask."""
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


def get_plan(user_prompt, state):
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
    return ask_llm(
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
    return ask_llm(
        [{"role": "system", "content": SYSTEM_STEP}, {"role": "user", "content": user_msg}],
        max_tokens=STEP_TOKENS,
        think=think,
        reasoning_policy=reasoning_policy,
        reasoning_trigger=reasoning_trigger,
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
_last_task_replan_reject_reason = None

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
    failed_task, errors, completed_tasks, state, user_prompt, goal_context_chars=GOAL_CONTEXT_CHARS
):
    """Mini-planner: generate a replacement for one failed task.
    Returns replacement task string, or None if replan fails."""
    global _last_task_replan_reject_reason
    _last_task_replan_reject_reason = None
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
    try:
        result = ask_llm(
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
        )
        task = result.get("task", "")
        if not task or not isinstance(task, str):
            _last_task_replan_reject_reason = "empty"
            return None
        task = task.strip()
        if len(task) <= 3:
            _last_task_replan_reject_reason = "too_short"
            return None
        if task == failed_task.strip():
            _last_task_replan_reject_reason = "exact_duplicate"
            return None
        if _is_near_duplicate_task(failed_task, task):
            _last_task_replan_reject_reason = "near_duplicate"
            return None
        if _is_passive_replacement(failed_task, task):
            _last_task_replan_reject_reason = "passive_downgrade"
            return None
        return task
    except LLMTransportError:
        _last_task_replan_reject_reason = "transport_error"
        return None
    except json.JSONDecodeError:
        _last_task_replan_reject_reason = "parse_error"
        return None
    except KeyError:
        _last_task_replan_reject_reason = "missing_task_key"
        return None


def _should_validate(replan, history, state, user_prompt):
    """Decide whether to run final validation. Returns True if validation should run."""
    if FINAL_VALIDATE == "0":
        return False
    if FINAL_VALIDATE == "always":
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


def _validate_completion(user_prompt, state, working_dir):
    """Run LLM-based final validation. Returns dict or None (fail-open)."""
    deterministic = _deterministic_check(user_prompt, state, working_dir)
    if deterministic is True:
        return {"valid": True, "deterministic": True}
    if deterministic is False:
        return {
            "valid": False,
            "deterministic": True,
            "reason": "deterministic completion check failed",
            "missing": [],
        }

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
    try:
        result = ask_llm(
            [{"role": "system", "content": SYSTEM_VALIDATE}, {"role": "user", "content": user_msg}],
            max_tokens=768,
            think=True,
            think_level="high",
            max_retries=0,
            reasoning_policy=state.get("reasoning_policy", DEFAULT_REASONING_POLICY),
            reasoning_trigger="final_validator",
        )
        if isinstance(result, dict) and isinstance(result.get("valid"), bool):
            return result
        log(f"  Validation returned unexpected format: {result}")
        return None
    except LLMTransportError as e:
        log(f"  Validation transport error (fail-open): {e}")
        return None
    except (json.JSONDecodeError, KeyError) as e:
        log(f"  Validation parse error (fail-open): {e}")
        return None


def _has_new_validation_evidence(state):
    start = state.get("validated_step_count", 0)
    return any(
        s.get("action") in ("write", "edit", "shell") and s.get("ok")
        for s in state.get("all_steps", [])[start:]
    )


_EXPECTED_FAILURE_POS_RE = re.compile(
    r"\b(observe|confirm|verify|check)\b.*\b(fail|error|bug|broken)\b"
    r"|\b(will fail|should fail|expect.*(fail|error)|initial failure|read the error)\b",
    re.I,
)
_EXPECTED_FAILURE_NEG_RE = re.compile(
    r"\b(no|not|without)\s+(fail|failure|error|bug|crash|broken)\b"
    r"|\b(error|failure|bug)\b.{0,20}\b(fixed|resolved|gone)\b"
    r"|\b(fix|repair|resolve)\b.*\b(error|failure|bug)\b",
    re.I,
)


def _expects_failure(task):
    return bool(_EXPECTED_FAILURE_POS_RE.search(task) and not _EXPECTED_FAILURE_NEG_RE.search(task))


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


def _try_compile_repair(error_output, working_dir, cmd):
    """Apply a narrow deterministic source repair. Returns (file, desc) or None."""
    if not COMPILE_REPAIR_ENABLED:
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
        text = f.read_text()
        include = pattern["fix_include"]
        if include in text:
            return None
        lines = text.split("\n")
        insert_idx = 0
        for j, line in enumerate(lines):
            if line.startswith("#include"):
                insert_idx = j + 1
        lines.insert(insert_idx, include)
        f.write_text("\n".join(lines))
        return (f.name, f"Auto-inserted {include}")
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

    def __init__(self, state, history):
        self.state = state
        self.history = history

    def selected(self):
        self.state["selected_steps"] += 1

    def executed(self):
        self.state["executed_steps"] += 1

    def skip(self, task_index, step, act, action, reason):
        """Record a selected-but-not-dispatched action in run metrics + log."""
        self.state["skipped_steps"] += 1
        _run_log(
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
        _run_log(receipt.jsonl_event(task_index, step, wall_s))
        return entry

    def annotate_last(self, key, value):
        """Set a controller annotation on the newest recorded step."""
        self.state["last_steps"][-1][key] = value
        self.state["all_steps"][-1][key] = value

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
    last_successful_edit: tuple[str, str, str] | None = None
    observe_executed: int = 0
    commit_executed: int = 0
    observe_blocked: int = 0

    def write_pressure(self):
        """True once observation spending must yield to a first commit."""
        return (
            self.wants_write
            and self.commit_executed == 0
            and self.observe_executed >= WRITE_PRESSURE_OBSERVATIONS
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

    def __init__(self, reasoning_policy, goal_context_chars):
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
        self.recorder = StepRecorder(self.data, self.history)
        self.started = time.time()
        self.last_write_target = None
        self.consecutive_target_writes = 0

    def elapsed(self):
        """Wall seconds since the run started."""
        return time.time() - self.started

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
            and self.consecutive_target_writes >= REWRITE_SKIP_WRITES
            and target == self.last_write_target
        )

    def validate_pressure_target(self):
        """Basename the executor must verify once rewrites repeat, or None."""
        if (
            self.last_write_target is not None
            and self.consecutive_target_writes >= REWRITE_PRESSURE_WRITES
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
        self,
        user_prompt,
        working_dir,
        max_replans=MAX_REPLANS,
        max_tasks=MAX_TASKS,
        max_steps=MAX_STEPS,
        reasoning_policy=DEFAULT_REASONING_POLICY,
        goal_context_chars=GOAL_CONTEXT_CHARS,
    ):
        if reasoning_policy not in REASONING_POLICIES:
            raise ValueError(f"reasoning_policy must be one of {', '.join(REASONING_POLICIES)}")
        if goal_context_chars < 1:
            raise ValueError("goal_context_chars must be a positive integer")
        self.user_prompt = user_prompt
        self.working_dir = working_dir
        self.max_replans = max_replans
        self.max_tasks = max_tasks
        self.max_steps = max_steps
        self.reasoning_policy = reasoning_policy
        self.goal_context_chars = goal_context_chars
        # Freeze the executor/replanner view once so all policy arms receive the same
        # task context even if module configuration changes while a run is active.
        self.goal_context = user_prompt[:goal_context_chars]
        self.run_state = RunState(reasoning_policy, goal_context_chars)
        self.state = self.run_state.data
        self.history = self.run_state.history
        self.recorder = self.run_state.recorder

    def run(self):
        """Drive planning, task attempts, validation, and finalization."""
        self._log_run_start()
        self._preflight()
        for replan in range(self.max_replans):
            tasks = self._plan(replan)
            if tasks is None:
                continue  # consumes a plan attempt
            if self._execute_tasks(tasks):
                result = self._try_finish(replan)
                if result is not None:
                    return result
        return self._finish_after_exhaustion()

    def _log_run_start(self):
        log(f"Prompt: {self.user_prompt}")
        log(f"Working directory: {self.working_dir}")
        _run_log(
            {
                "event": "run_start",
                "prompt": self.user_prompt,
                "working_dir": self.working_dir,
                "backend": LLM_BACKEND,
                "model": MODEL,
                "provider": OPENROUTER_PROVIDER if LLM_BACKEND == "openrouter" else "",
                "reasoning_effort": (
                    OPENROUTER_REASONING_EFFORT if LLM_BACKEND == "openrouter" else ""
                ),
                "allow_provider_fallbacks": OPENROUTER_ALLOW_FALLBACKS,
                "require_provider_parameters": OPENROUTER_REQUIRE_PARAMETERS,
                "reasoning_policy": self.reasoning_policy,
                # Ablation-arm provenance (issue #41): zero repair receipts cannot
                # distinguish arm B from an arm-A run that never hit the trigger.
                "compile_repair": COMPILE_REPAIR_ENABLED,
                "limits": {
                    "max_replans": self.max_replans,
                    "max_tasks": self.max_tasks,
                    "max_steps": self.max_steps,
                    "goal_context_chars": self.goal_context_chars,
                },
            }
        )

    def _preflight(self):
        # Preflight: probe environment and set policy
        env = preflight_probe(self.working_dir)
        self.state["environment"] = env
        self.state["policy"] = get_policy()
        log(f"Environment: platform={env['platform']} arch={env['arch']}")
        log(f"Available tools: {env['available_tools']}")
        if env["missing_tools"]:
            log(f"Missing tools: {env['missing_tools']}")
        log(f"Package managers: {env['package_managers']}")
        log(f"Policy: allow_system_installs={self.state['policy']['allow_system_installs']}")

    def _plan(self, replan):
        """One planning attempt; returns the task list or None on failure."""
        log("=" * 40)
        t_plan = time.time()
        log(f"Planning (attempt {replan + 1}/{self.max_replans})...")
        self.state["planning_attempt"] = replan
        try:
            plan = get_plan(self.user_prompt, self.state)
        except LLMTransportError as e:
            log(f"  Planner transport error: {e}")
            self.state["errors"].append(f"[unknown] Planner transport error: {str(e)[:100]}")
            self.history.append({"event": "plan_error", "replan": replan, "error": str(e)[:200]})
            _run_log(
                {
                    "event": "plan_error",
                    "replan": replan,
                    "error": str(e)[:200],
                    "wall_s": round(time.time() - t_plan, 2),
                }
            )
            return None
        raw_tasks = plan.get("tasks")
        tasks = raw_tasks[: self.max_tasks] if isinstance(raw_tasks, list) else []
        if not tasks or any(not _valid_nonempty_str(task) for task in tasks):
            error = "[malformed_plan] planner returned no valid tasks"
            self.state["errors"].append(error)
            log(f"  Planner contract error: {error}")
            self.history.append({"event": "plan_error", "replan": replan, "error": error})
            _run_log(
                {
                    "event": "plan_error",
                    "replan": replan,
                    "error": error,
                    "wall_s": round(time.time() - t_plan, 2),
                }
            )
            return None
        self.state["errors"] = []  # reset errors each replan; planner already saw them
        plan_wall = time.time() - t_plan
        log(f"Plan ({plan_wall:.1f}s, planner_wall_time={plan_wall:.1f}s): {tasks}")
        self.history.append({"event": "plan", "replan": replan, "tasks": tasks})
        _run_log({"event": "plan", "replan": replan, "tasks": tasks, "wall_s": round(plan_wall, 2)})
        return tasks

    def _execute_tasks(self, tasks):
        """Run the plan's tasks in order; True when every task completed."""
        all_done = True
        for i, task in enumerate(tasks):
            # Carry over last step from previous task so executor has cross-task context
            prev_last = self.state["last_steps"][-1:] if self.state.get("last_steps") else []
            t_task = time.time()
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
                    log(f"  Task completion refused: {incomplete_name} is incomplete")
                    task_done = False
            if task_done:
                self.state["completed_tasks"].append(task)
                self.state["completed_step_groups"].append(task_steps)
                log(f"  Task complete. ({time.time() - t_task:.1f}s)")
                _run_log(
                    {
                        "event": "task_complete",
                        "task_index": i,
                        "task": task,
                        "wall_s": round(time.time() - t_task, 2),
                    }
                )
            else:
                all_done = False
                log(f"  Task failed, will replan. ({time.time() - t_task:.1f}s)")
                _run_log(
                    {
                        "event": "task_failed",
                        "task_index": i,
                        "task": task,
                        "wall_s": round(time.time() - t_task, 2),
                    }
                )
                break
        return all_done

    def _run_task(self, i, task, tasks, prev_last):
        """Attempt one task with at most MAX_TASK_LOCAL_REPLANS local retries.

        Returns ``(task, done, steps)``; ``task`` is the possibly replaced
        task text the run record must carry forward.
        """
        # E11: inner retry loop — try task-local replan before full replan
        attempt = TaskAttemptState(task=task, wants_write=_is_write_shaped(task))
        saved_errors = []
        for task_attempt in range(1 + MAX_TASK_LOCAL_REPLANS):
            self.state["current_task"] = task
            self.state["task_index"] = f"{i + 1}/{len(tasks)}"
            self.state["last_steps"] = list(prev_last)
            log(f"--- Task {i + 1}/{len(tasks)}: {task} ---")

            # Reset per-attempt execution state (the task may be a replacement)
            attempt = TaskAttemptState(task=task, wants_write=_is_write_shaped(task))
            completed_repair = _task_satisfied_by_deterministic_repair(task, self.state)
            if completed_repair:
                log(
                    f"  auto-done (deterministic repair already satisfied task: {completed_repair.get('output', '')[:60]})"
                )
                attempt.steps.append(completed_repair)
                attempt.done = True
                break
            self._run_attempt(i, attempt)

            if attempt.done:
                break  # break task_attempt loop — success

            # E11: try task-local replan before falling through to full replan
            if task_attempt < MAX_TASK_LOCAL_REPLANS:
                saved_errors = list(self.state["errors"])
                t_lr = time.time()
                replacement = replan_task(
                    task,
                    self.state["errors"],
                    self.state["completed_tasks"],
                    self.state,
                    self.goal_context,
                    goal_context_chars=self.goal_context_chars,
                )
                lr_wall = time.time() - t_lr
                if replacement:
                    log(f"  Task-local replan ({lr_wall:.1f}s): '{replacement[:60]}'")
                    _run_log(
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
                    reject_reason = _last_task_replan_reject_reason or "unknown"
                    log(f"  Task-local replan failed ({lr_wall:.1f}s), will full replan.")
                    _run_log(
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
        t_step = time.time()
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
                write_pressure=attempt.write_pressure(),
                validate_pressure=self.run_state.validate_pressure_target(),
            )
        except LLMTransportError as e:
            log(f"  [{step + 1}] LLM transport error ({time.time() - t_step:.1f}s): {e}")
            self.state["errors"].append(
                f"[unknown] LLM transport error on task '{attempt.task}': {str(e)[:100]}"
            )
            return None
        except (json.JSONDecodeError, KeyError) as e:
            # Typed parse failures (issue #7): the replanner should know
            # whether the action envelope was truncated at the token budget
            # or simply malformed.
            if getattr(e, "response_truncated", False):
                etype = "response_truncated"
            elif getattr(e, "malformed_action", False):
                etype = "malformed_action"
            else:
                etype = "unknown"
            log(f"  [{step + 1}] LLM parse error ({time.time() - t_step:.1f}s) [{etype}]")
            self.state["errors"].append(
                f"[{etype}] LLM parse error on task '{attempt.task}': {str(e)[:100]}"
            )
            _run_log(
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
        self.recorder.selected()
        log(f"  [{step + 1}] {act}: {action['arg'][:80]}")
        return _StepContext(task_index=i, step=step, started=t_step, action=action, act=act)

    def _decide_step(self, ctx, attempt):
        """Run the controller guards; None means dispatch the action."""
        if ctx.act == "done":
            return self._handle_done(ctx, attempt)
        if ctx.act == "fail":
            reason = ctx.action.get("reasoning", "no reason")
            log(f"  FAIL ({time.time() - ctx.started:.1f}s): {reason}")
            self.state["errors"].append(f"Task '{attempt.task}': {reason}")
            return _StepFlow.END_ATTEMPT
        flow = self._prepare_write(ctx)
        if flow is None:
            flow = self._observe_tail_guard(ctx, attempt)
        if flow is None:
            flow = self._rewrite_loop_guard(ctx, attempt)
        if flow is None:
            flow = self._duplicate_guard(ctx, attempt)
        return flow

    def _handle_done(self, ctx, attempt):
        """Accept ``done`` only while no incomplete-write obligation blocks."""
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
            log(f"  [{ctx.step + 1}] skip (done with incomplete write: {incomplete_name})")
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
            log(f"  [{ctx.step + 1}] skip (append before first replacement chunk landed)")
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
                log(f"  [{ctx.step + 1}] skip (write truncated before a complete line)")
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

    def _observe_tail_guard(self, ctx, attempt):
        """Reserve the final steps of a write-shaped task for commitment."""
        # Write-forcing tail reserve (issue #15): on a write-shaped task the
        # final steps are reserved for committing actions.
        if not (
            ctx.act in OBSERVE_ACTIONS
            and attempt.wants_write
            and attempt.commit_executed == 0
            and self.max_steps - ctx.step <= OBSERVE_TAIL_RESERVE
        ):
            return None
        attempt.observe_blocked += 1
        if attempt.observe_blocked >= 2:
            log(f"  [{ctx.step + 1}] auto-fail (observation steps exhausted without a write)")
            self.state["errors"].append(
                f"[stuck_loop] {ctx.act} {ctx.action.get('arg', '')[:60]}: observation steps exhausted without a write"
            )
            self.recorder.skip(
                ctx.task_index, ctx.step, ctx.act, ctx.action, "observe_tail_exhausted"
            )
            return _StepFlow.END_ATTEMPT
        log(f"  [{ctx.step + 1}] skip ({ctx.act} blocked: remaining steps reserved for write)")
        self.recorder.skip(ctx.task_index, ctx.step, ctx.act, ctx.action, "observe_tail_reserved")
        self.recorder.note(
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
        # Rewrite damping (revision 4): after REWRITE_SKIP_WRITES successful
        # full writes of the same target with no intervening successful
        # shell/edit, further full rewrites are skipped — verify, edit, or
        # finish instead.
        if not (
            ctx.act == "write"
            and not ctx.action.get("append")
            and not ctx.truncated_write
            and self.run_state.rewrite_skip_armed(ctx.logical_write_target)
        ):
            return None
        attempt.dup_skip_count += 1
        log(
            f"  [{ctx.step + 1}] skip (rewrite loop: "
            f"{ctx.action.get('arg', '')[:40]} already written "
            f"{self.run_state.consecutive_target_writes}x)"
        )
        self.recorder.skip(ctx.task_index, ctx.step, ctx.act, ctx.action, "rewrite_loop")
        self.recorder.note(
            {
                "action": ctx.act,
                "arg": ctx.action.get("arg", ""),
                "ok": True,
                "output": (
                    f"Already written {self.run_state.consecutive_target_writes} times. "
                    "Do NOT write it again — verify with shell, make a "
                    "targeted edit, or emit done."
                ),
            }
        )
        return _StepFlow.NEXT_STEP

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
                    log(
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
                log(
                    f"  [{ctx.step + 1}] auto-fail (same edit failed twice on {action.get('arg', '')[:40]})"
                )
                self.state["errors"].append(
                    f"[stuck_loop] edit {action.get('arg', '')[:60]}: same find string failed twice"
                )
                self.recorder.skip(ctx.task_index, ctx.step, act, action, "stuck_edit")
                return _StepFlow.END_ATTEMPT
            if is_dup:
                attempt.dup_skip_count += 1
                log(f"  [{ctx.step + 1}] skip (duplicate {act}, same content)")
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
                if act == "edit" and attempt.last_successful_edit and attempt.dup_skip_count >= 2:
                    edit_key = (
                        action.get("arg", ""),
                        action.get("find", ""),
                        action.get("replace", ""),
                    )
                    if edit_key == attempt.last_successful_edit:
                        log(
                            f"  [{ctx.step + 1}] auto-done (edit already succeeded, model re-emitting)"
                        )
                        attempt.done = True
                        return _StepFlow.END_ATTEMPT
                # Defer thinking escalation: first duplicate skip gets a
                # corrective observation only; escalate on 2+ consecutive skips.
                # Saves ~10s of thinking time on harmless first-time duplicates.
                if attempt.dup_skip_count >= 2:
                    attempt.use_think = True
                    attempt.reasoning_trigger = "duplicate_action"
                return _StepFlow.NEXT_STEP
        elif act == "shell" and prev.get("arg", "") == action.get("arg", ""):
            if prev.get("ok"):
                log(f"  [{ctx.step + 1}] auto-done (duplicate successful shell)")
                self.recorder.skip(
                    ctx.task_index, ctx.step, act, action, "duplicate_shell_auto_done"
                )
                attempt.done = True
                return _StepFlow.END_ATTEMPT
            elif prev.get("error_type") == "timeout":
                # Bump timeout for retry: read actual timeout from previous step,
                # not from fresh action (which won't have prior bumps)
                prev_timeout = prev.get("_timeout", _get_shell_timeout(action.get("arg", "")))
                bumped = max(SHELL_TIMEOUT_LONG, prev_timeout * 2)
                action["timeout"] = min(bumped, SHELL_TIMEOUT_MAX)
                log(f"  [{ctx.step + 1}] retrying after timeout ({action['timeout']}s)")
            else:
                log(f"  [{ctx.step + 1}] auto-fail (same shell failed twice)")
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
                    log(
                        f"  [{ctx.step + 1}] auto-fail (same read repeated on {action.get('arg', '')[:40]})"
                    )
                    self.state["errors"].append(
                        f"[stuck_loop] read {action.get('arg', '')[:60]}: same file read repeatedly"
                    )
                    self.recorder.skip(ctx.task_index, ctx.step, act, action, "stuck_read")
                    return _StepFlow.END_ATTEMPT
                log(f"  [{ctx.step + 1}] skip (duplicate read)")
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
                log(f"  [{ctx.step + 1}] auto-fail (same read failed twice)")
                self.state["errors"].append(
                    f"[stuck_loop] read {action.get('arg', '')[:60]} failed twice"
                )
                self.recorder.skip(ctx.task_index, ctx.step, act, action, "stuck_read_failed")
                return _StepFlow.END_ATTEMPT
        return None

    def _execute_step(self, ctx, attempt):
        """Dispatch through the execute() seam and record the receipt."""
        action, act = ctx.action, ctx.act
        attempt.dup_skip_count = 0  # reset on any non-skipped action
        self.recorder.executed()
        if act in OBSERVE_ACTIONS:
            attempt.observe_executed += 1
        # Normalize the seam's legacy dict once; controller policy below
        # runs on the typed result.
        result = ActionResult.from_dict(execute(action, self.working_dir))
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
        if act == "write" and result.ok:
            if ctx.truncated_write:
                # A partial (truncated) write is not a completed rewrite
                # (Codex P1, PR #21): the file is incomplete, and the
                # recovery path may legitimately append to it or restart the
                # write. Reset for truncated append chunks too; otherwise an
                # armed streak can block the clean restart.
                self.run_state.disarm_rewrite_damping()
            elif not action.get("append"):
                self.run_state.note_successful_full_write(ctx.logical_write_target)
        elif act in ("shell", "edit") and result.ok:
            # Verification or a targeted fix breaks the rewrite streak;
            # observations do not (the v6 Gemma loop interleaved tree/read
            # between rewrites).
            self.run_state.break_rewrite_streak()
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
        log(f"  -> {ok_str} ({time.time() - ctx.started:.1f}s): {result.output[:80]}")

        step_entry = self.recorder.record(
            StepReceipt.executed(action, result, self.working_dir, ctx.truncated_write),
            ctx.task_index,
            ctx.step,
            wall_s=round(time.time() - ctx.started, 2),
        )

        if not result.ok:
            return self._recover_failed_step(ctx, attempt, result, step_entry)
        attempt.use_think = False
        attempt.reasoning_trigger = "executor"
        attempt.steps.append(step_entry)
        if act == "edit":
            attempt.last_successful_edit = (
                action.get("arg", ""),
                action.get("find", ""),
                action.get("replace", ""),
            )
        return None

    def _recover_failed_step(self, ctx, attempt, result, step_entry):
        """Expected-failure evidence, deterministic repair, or typed error."""
        action, act = ctx.action, ctx.act
        etype = result.error_type or "unknown"
        if (
            act == "shell"
            and etype in ("compile_error", "unknown")
            and _expects_failure(attempt.task)
        ):
            log("  Expected failure observed; completing task with evidence")
            step_entry["expected_failure"] = True
            self.recorder.annotate_last("expected_failure", True)
            attempt.steps.append(step_entry)
            attempt.done = True
            return _StepFlow.END_ATTEMPT

        if act == "shell" and etype == "compile_error":
            repair = _try_compile_repair(result.output, self.working_dir, action.get("arg", ""))
            if repair:
                # The deterministic source edit is a successful targeted fix,
                # so it breaks an armed rewrite streak just like a
                # model-selected edit.
                self.run_state.break_rewrite_streak()
                log(f"  Deterministic repair: {repair[1]} in {repair[0]}")
                # The repair mutated the workspace outside the action
                # handlers (the tracked #41 exception); its receipt and the
                # scaffold retry still go through the one recorder.
                attempt.steps.append(
                    self.recorder.record(
                        StepReceipt.deterministic_repair(repair[0], repair[1]),
                        ctx.task_index,
                        ctx.step,
                    )
                )

                retry_result = ActionResult.from_dict(execute(action, self.working_dir))
                retry_entry = self.recorder.record(
                    StepReceipt.deterministic_retry(action, retry_result),
                    ctx.task_index,
                    ctx.step,
                    wall_s=round(time.time() - ctx.started, 2),
                )
                if retry_result.ok:
                    log(f"  -> OK deterministic retry: {retry_result.output[:80]}")
                    attempt.steps.append(retry_entry)
                    attempt.use_think = False
                    attempt.reasoning_trigger = "executor"
                    return _StepFlow.NEXT_STEP
                log(f"  -> FAIL deterministic retry: {retry_result.output[:80]}")
                result = retry_result
                etype = result.error_type or "unknown"

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
        """Validate an all-done pass; a result dict ends the run."""
        wants_validation = _should_validate(replan, self.history, self.state, self.user_prompt)
        first_validation = self.state.get("validation_attempts", 0) == 0
        recheck_validation = (
            self.state.get("validation_recheck_needed")
            and self.state.get("validation_attempts", 0) < 2
            and _has_new_validation_evidence(self.state)
        )
        if wants_validation and (first_validation or recheck_validation):
            self.state["validated_once"] = True
            self.state["validation_attempts"] = self.state.get("validation_attempts", 0) + 1
            vresult = _validate_completion(self.user_prompt, self.state, self.working_dir)
            if vresult and vresult.get("valid") is False:
                reason = vresult.get("reason", "validation failed")
                missing = vresult.get("missing", [])
                error_msg = f"[validation_failed] {reason}"
                if missing:
                    error_msg += f" missing: {', '.join(missing)}"
                self.state["errors"].append(error_msg)
                self.state["validation_recheck_needed"] = True
                self.state["validated_step_count"] = len(self.state.get("all_steps", []))
                log(f"  Validation failed: {reason}")
                _run_log(
                    {
                        "event": "validation",
                        "valid": False,
                        "reason": reason,
                        "missing": missing,
                        "deterministic": bool(vresult.get("deterministic")),
                    }
                )
                return None  # replan
            elif vresult is None and recheck_validation:
                # A first optional validator failure remains fail-open, but
                # once validation explicitly failed, an unavailable second
                # verdict cannot erase that known failure.
                log("  Validation recheck produced no verdict; failure remains pending.")
                _run_log(
                    {
                        "event": "validation",
                        "valid": None,
                        "reason": "recheck produced no verdict",
                        "deterministic": False,
                    }
                )
            else:
                self.state["validation_recheck_needed"] = False
                log("  Validation passed.")
                _run_log(
                    {
                        "event": "validation",
                        "valid": True,
                        "deterministic": bool(vresult and vresult.get("deterministic")),
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
            log(f"  Completion refused: {reason}")
            _run_log({"event": "validation_pending", "reason": reason})
            return None
        total_wall = self.run_state.elapsed()
        log(f"All tasks complete. ({total_wall:.1f}s total)")
        log(f"Output in: {self.working_dir}")
        _run_log(
            {
                "event": "run_end",
                "status": "complete",
                "replans": replan,
                "wall_s": round(total_wall, 2),
                "completed_tasks": len(self.state["completed_tasks"]),
                "steps": {
                    "selected": self.state["selected_steps"],
                    "executed": self.state["executed_steps"],
                    "skipped": self.state["skipped_steps"],
                },
            }
        )
        return {"status": "complete", "state": self.state, "log": self.history}

    def _finish_after_exhaustion(self):
        """Deterministic reconciliation, then the exhausted result."""
        total_wall = self.run_state.elapsed()
        deterministic = _deterministic_check(self.user_prompt, self.state, self.working_dir)
        if deterministic is True and not self.state.get("validation_recheck_needed"):
            log(f"Deterministic reconciliation passed after exhaustion. ({total_wall:.1f}s total)")
            log(f"Output in: {self.working_dir}")
            _run_log(
                {
                    "event": "run_end",
                    "status": "complete_deterministic_after_exhausted",
                    "replans": self.max_replans,
                    "wall_s": round(total_wall, 2),
                    "completed_tasks": len(self.state["completed_tasks"]),
                    "steps": {
                        "selected": self.state["selected_steps"],
                        "executed": self.state["executed_steps"],
                        "skipped": self.state["skipped_steps"],
                    },
                }
            )
            return {"status": "complete", "state": self.state, "log": self.history}

        log(f"Exhausted {self.max_replans} replan attempts. ({total_wall:.1f}s total)")
        log(f"Errors: {self.state['errors']}")
        log(f"Output in: {self.working_dir}")
        _run_log(
            {
                "event": "run_end",
                "status": "exhausted",
                "replans": self.max_replans,
                "wall_s": round(total_wall, 2),
                "errors": self.state["errors"][-5:],
                "steps": {
                    "selected": self.state["selected_steps"],
                    "executed": self.state["executed_steps"],
                    "skipped": self.state["skipped_steps"],
                },
            }
        )
        return {"status": "exhausted", "state": self.state, "log": self.history}


def _run_loop(
    user_prompt,
    working_dir,
    max_replans=MAX_REPLANS,
    max_tasks=MAX_TASKS,
    max_steps=MAX_STEPS,
    reasoning_policy=DEFAULT_REASONING_POLICY,
    goal_context_chars=GOAL_CONTEXT_CHARS,
):
    """Core agent loop. Returns structured result dict.

    Used by run() (public API, returns bool) and by integration test harness
    (needs rich dict with state + log). All production behavior lives in
    _RunController; this wrapper remains the stable seam callers target.
    """
    return _RunController(
        user_prompt,
        working_dir,
        max_replans=max_replans,
        max_tasks=max_tasks,
        max_steps=max_steps,
        reasoning_policy=reasoning_policy,
        goal_context_chars=goal_context_chars,
    ).run()


def run(user_prompt, working_dir=None):
    """Public API: run agent and return True (success) or False (failure)."""
    # Create isolated temp directory per run unless caller provides one
    if working_dir is None:
        working_dir = tempfile.mkdtemp(prefix="askme_")
    result = _run_loop(user_prompt, working_dir)
    return result["status"] == "complete"


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
        working_dir = tempfile.mkdtemp(prefix="askme_")
    else:
        workspace = Path(args.working_dir)
        if not workspace.is_dir():
            parser.error("--working-dir must name an existing directory")
        working_dir = str(workspace)

    result = _run_loop(
        user_prompt,
        working_dir,
        max_replans=args.max_replans,
        max_tasks=args.max_tasks,
        max_steps=args.max_steps,
        reasoning_policy=args.reasoning_policy,
        goal_context_chars=args.goal_context_chars,
    )
    if args.result_json:
        try:
            Path(args.result_json).write_text(json.dumps(result, indent=2, default=str) + "\n")
        except OSError as e:
            parser.error(f"cannot write --result-json: {e}")
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(_main())
