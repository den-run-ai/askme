#!/usr/bin/env python3
"""Minimal self-contained agent. Takes a user prompt, plans, executes, replans on failure.
Requires: requests. Expects llama-server on localhost:8080."""
import argparse, sys, json, subprocess, requests, re, time, os, tempfile, shutil, shlex, hashlib
from pathlib import Path


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

# Backend config: set LLM_BACKEND=openrouter to use OpenRouter API
LLM_BACKEND = os.environ.get("LLM_BACKEND", "local")  # "local" or "openrouter"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemma-4-26b-a4b-it")
OPENROUTER_PROVIDER = os.environ.get("OPENROUTER_PROVIDER", "Parasail").strip()
OPENROUTER_ALLOW_FALLBACKS = os.environ.get("OPENROUTER_ALLOW_FALLBACKS", "1") == "1"
OPENROUTER_REQUIRE_PARAMETERS = os.environ.get("OPENROUTER_REQUIRE_PARAMETERS", "0") == "1"

CACHE_WORKAROUND = os.environ.get("CACHE_WORKAROUND", "0") == "1"

# Execution policy — controls what the agent is allowed to do
ALLOW_SYSTEM_INSTALLS = os.environ.get("ALLOW_SYSTEM_INSTALLS", "0") == "1"
ALLOW_NETWORK = os.environ.get("ALLOW_NETWORK", "1") == "1"

if LLM_BACKEND == "openrouter":
    API = "https://openrouter.ai/api/v1/chat/completions"
    MODEL = OPENROUTER_MODEL
else:
    API = os.environ.get("LLM_API_URL", "http://localhost:8080/v1/chat/completions")
    MODEL = os.environ.get("LLM_MODEL", "gemma-4-e4b")
_BASE_URL = API.rsplit("/v1/", 1)[0] if "/v1/" in API else "http://localhost:8080"
_CACHE_SLOT = "agent-system-prompt"
_cache_warmed = False


def _warm_cache():
    """Pre-process system prompt and save KV state for reuse.
    Called once at start of run(). Non-fatal — falls back to no caching."""
    global _cache_warmed
    if not CACHE_WORKAROUND or LLM_BACKEND != "local":
        return
    try:
        requests.post(f"{_BASE_URL}/v1/chat/completions", json={
            "model": MODEL,
            "messages": [{"role": "system", "content": SYSTEM_PLAN},
                         {"role": "user", "content": "hi"}],
            "max_tokens": 1,
        }, timeout=60)
        resp = requests.post(f"{_BASE_URL}/slots/0?action=save",
                             json={"filename": _CACHE_SLOT}, timeout=10)
        if resp.status_code == 200 and resp.json().get("n_saved", 0) > 0:
            _cache_warmed = True
            log(f"Cache warm: saved {resp.json()['n_saved']} tokens")
        else:
            log(f"Cache warm: save failed ({resp.status_code}), continuing without cache")
    except Exception as e:
        log(f"Cache warm: error ({e}), continuing without cache")


def _restore_cache():
    """Restore system prompt KV state before each LLM request.
    Non-fatal — silently skips if cache wasn't warmed or restore fails."""
    if not _cache_warmed:
        return
    try:
        requests.post(f"{_BASE_URL}/slots/0?action=restore",
                      json={"filename": _CACHE_SLOT}, timeout=10)
    except Exception:
        pass


PROBE_TOOLS = ["python3", "go", "node", "gcc", "cc", "make", "cargo", "rustc", "java", "javac"]
PROBE_PKG_MANAGERS = ["brew", "apt-get", "dnf", "pacman", "apk"]


def preflight_probe(working_dir="."):
    """Deterministic environment probe. Returns structured dict for planner state."""
    import platform
    env = {
        "platform": platform.system().lower(),  # "darwin", "linux", "windows"
        "arch": platform.machine(),              # "arm64", "x86_64"
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


LLM_TIMEOUT = 120  # seconds; covers slow first-token on local LLM
LLM_TIMEOUT_REPLAN = 180  # replans carry heavier state + thinking

MAX_REPLANS = 3  # Total planning attempts (initial plan + up to 2 replans)
MAX_TASKS = 10
MAX_STEPS = 10
MAX_RESULT = 300  # chars kept from command output
MAX_STEP_HISTORY = 3  # sliding window of recent steps sent to executor

# Write-forcing executor policy (issue #15): on a write-shaped task,
# observation may not consume the whole step budget — the 2026-08-01 Qwen
# canary spent all 27 executed steps on tree/read and never selected a write.
WRITE_PRESSURE_OBSERVATIONS = 3  # observation steps before the executor must commit
OBSERVE_TAIL_RESERVE = 3         # final steps per attempt reserved for commitment
# Validate-after-write policy (revision 4): on a write-shaped task, repeated
# whole-file rewrites of the same target may not consume the step budget —
# the 2026-08-01 v6 Gemma canary rewrote one file 18 times without ever
# verifying it or emitting done.
REWRITE_PRESSURE_WRITES = 2  # same-target full writes before the executor must verify
REWRITE_SKIP_WRITES = 3      # same-target full writes after which further rewrites are skipped
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
    return (bool(task) and bool(_WRITE_TASK_RE.search(task))
            and not _OBSERVE_TASK_RE.match(task))

# Observation-action budgets (issue #7): reads/searches/trees are the navigation
# surface for app development; they get their own bounded windows so large repos
# stay navigable without blowing up executor state.
READ_LINES = 60           # max lines per read window
READ_CHARS = 1200         # max chars per read window
READ_LIMIT_MAX = 200      # hard cap on model-specified read limit
SEARCH_MAX_MATCHES = 15   # bounded literal search results
SEARCH_MAX_CHARS = 1500   # bounded search output
SEARCH_MAX_FILES = 500    # bounded search scan
TREE_MAX_ENTRIES = 60     # bounded repository-tree listing
TREE_MAX_CHARS = 1500     # bounded tree output
TREE_MAX_DEPTH = 3        # bounded tree walk depth
OBSERVE_ACTIONS = frozenset({"read", "search", "tree"})
OBSERVE_STATE_CHARS = 1500  # executor-state budget for observation step output
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
    raise ValueError(
        f"AGENT_REASONING_POLICY must be one of {', '.join(REASONING_POLICIES)}"
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
    r'\b(compile|build|test|run|execute|fix|debug|repair|verify|install|server|api|script|program)\b', re.I)

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
Actions: shell, write, edit, read, search, tree, done, fail.
read: may take "offset"/"limit" (lines); if output says "continue: offset=N", read that offset for more
search: literal pattern in "arg", optional "path" (default "."); bounded matches
tree: directory in "arg" (default "."); bounded listing
write: whole file; add "append":true to append the next chunk instead
write content may follow the JSON between sentinel lines instead of "content":
{"action":"write","arg":"f.py","reasoning":"..."}
<<<CONTENT
raw file lines, no escaping
CONTENT>>>
edit: {"action":"edit","arg":"file","find":"exact old","replace":"new","reasoning":"..."}
Format: {"action":"...","arg":"...","content":"...","reasoning":"..."}"""


MAX_LLM_RETRIES = 2


def _repair_json(text):
    """Try to salvage broken JSON from truncation artifacts. Returns dict or None."""
    if not text or "{" not in text:
        return None
    # Strip trailing prose after a complete JSON object (model commentary after })
    # Find the last } and discard everything after it
    last_brace = text.rfind('}')
    if last_brace >= 0 and last_brace < len(text) - 1:
        candidate = text[:last_brace + 1]
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass  # fall through to other repairs
    # Strip trailing incomplete key-value pair (truncation mid-field)
    text = re.sub(r',\s*"[^"]*$', '', text)
    # Strip trailing incomplete value after a key (e.g. "key": "val...)
    text = re.sub(r',\s*"[^"]*":\s*"?[^"}\]]*$', '', text)
    # Strip trailing commas before close
    text = re.sub(r',\s*}', '}', text)
    # Close missing braces
    opens = text.count('{') - text.count('}')
    if opens > 0:
        text = text + '}' * opens
    elif opens < 0:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _valid_nonempty_str(value):
    return isinstance(value, str) and bool(value.strip())


def _validate_action_contract(obj):
    """Return True for planner/validator dicts and complete action dicts."""
    if not isinstance(obj, dict) or "action" not in obj:
        return True
    action = obj.get("action", "")
    if action == "write":
        content = obj.get("content")
        return _valid_nonempty_str(obj.get("arg")) and (
            _valid_nonempty_str(content) or isinstance(content, (dict, list))
        )
    if action == "edit":
        return (_valid_nonempty_str(obj.get("arg"))
                and _valid_nonempty_str(obj.get("find"))
                and "replace" in obj)
    if action in ("shell", "read", "search"):
        return _valid_nonempty_str(obj.get("arg"))
    return True


def _accept_or_raise(obj, text):
    if _validate_action_contract(obj):
        return obj
    raise json.JSONDecodeError("Incomplete action JSON", text, 0)


_STRICT_JSON_SUFFIX = "Output ONLY the JSON object. No reasoning, no explanation, no text outside the JSON."

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
            return header, "\n".join(lines[open_idx + 1:j]), True
    return header, "\n".join(lines[open_idx + 1:]), False

# Truncated write/edit payloads are the most common large-output parse failure;
# detect the attempted action so the retry gets a payload-sized budget.
_WRITE_ATTEMPT_RE = re.compile(r'"action"\s*:\s*"(?:write|edit)"')


def ask_llm(messages, max_tokens=256, think=False, think_level=None,
            max_retries=MAX_LLM_RETRIES, raw=False, timeout=None,
            reasoning_policy=DEFAULT_REASONING_POLICY,
            reasoning_trigger="unspecified"):
    if reasoning_policy not in REASONING_POLICIES:
        raise ValueError(
            f"reasoning_policy must be one of {', '.join(REASONING_POLICIES)}"
        )
    budget = max_tokens
    for attempt in range(max_retries + 1):
        # Determine thinking level: explicit think_level overrides auto-escalation.
        # E03: on final auto-retry (attempt 2), disable thinking and use strict
        # contract instead — more thinking doesn't fix truncation/format errors.
        # Explicit think_level from callers (e.g. validation) is respected by
        # gated policy; off suppresses every explicit and retry-time request.
        if think_level:
            gated_think_level = think_level
            requested_level = think_level
        elif think:
            requested_level = "adaptive"
            if attempt >= 2:
                gated_think_level = None
            else:
                gated_think_level = "high" if attempt >= 1 else "medium"
        elif attempt == 1:
            # Existing JSON-contract recovery: one reasoning-assisted retry.
            gated_think_level = "medium"
            requested_level = "medium"
        else:
            gated_think_level = None
            requested_level = None

        effective_trigger = (
            "json_retry"
            if attempt == 1 and not think and not think_level
            else reasoning_trigger
        )
        effective_think_level = (
            gated_think_level if reasoning_policy == "gated" else None
        )
        _run_log({
            "event": "reasoning_decision",
            "requested_policy": reasoning_policy,
            "requested_trigger": effective_trigger,
            "requested_level": requested_level,
            "effective_level": effective_think_level,
            "attempt": attempt,
        })

        body = {
            "model": MODEL,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": budget,
        }
        if LLM_BACKEND == "openrouter":
            if OPENROUTER_PROVIDER:
                body["provider"] = {
                    "order": [OPENROUTER_PROVIDER],
                    "allow_fallbacks": OPENROUTER_ALLOW_FALLBACKS,
                    "require_parameters": OPENROUTER_REQUIRE_PARAMETERS,
                }
            if effective_think_level:
                body["reasoning"] = {
                    "enabled": True,
                    "effort": effective_think_level,
                }
                # Reasoning tokens count against max_tokens with Parasail provider,
                # despite OpenRouter docs claiming they're separate. Bump to compensate.
                body["max_tokens"] = max(budget, 2048 if effective_think_level == "high" else 1536)
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

        # E03: strict contract on final auto-retry — suppress reasoning leaks
        if attempt >= 2 and not think_level:
            msgs = list(body["messages"])
            msgs.append({"role": "user", "content": _STRICT_JSON_SUFFIX})
            body["messages"] = msgs

        headers = {"Content-Type": "application/json"}
        if LLM_BACKEND == "openrouter" and OPENROUTER_API_KEY:
            headers["Authorization"] = f"Bearer {OPENROUTER_API_KEY}"
            headers["X-OpenRouter-Metadata"] = "enabled"
        _restore_cache()
        # Transport-level error handling with retry + backoff
        try:
            resp = requests.post(API, json=body, headers=headers, timeout=timeout or LLM_TIMEOUT)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                requests.exceptions.RequestException) as e:
            log(f"  Transport error: {type(e).__name__}: {e}")
            if attempt < max_retries:
                time.sleep(1 if attempt == 0 else 3)
                continue
            raise LLMTransportError(f"Transport failed after {max_retries + 1} attempts: {e}") from e
        # HTTP status checks — fail-fast on client errors, retry on server/overload
        sc = resp.status_code
        if sc == 429 or sc >= 500:
            log(f"  HTTP {sc}, retrying...")
            if attempt < max_retries:
                time.sleep(1 if attempt == 0 else 3)
                continue
            raise LLMTransportError(f"HTTP {sc} after {max_retries + 1} attempts")
        if 400 <= sc < 500:
            raise LLMTransportError(f"HTTP {sc}: {resp.text[:200]}")
        # Parse JSON body — retry on non-JSON responses (proxy/gateway glitch)
        try:
            rj = resp.json()
        except ValueError as e:
            log(f"  Non-JSON response body: {resp.text[:100]}")
            if attempt < max_retries:
                time.sleep(1 if attempt == 0 else 3)
                continue
            raise LLMTransportError(f"Non-JSON response after {max_retries + 1} attempts") from e
        # Handle API error responses (JSON body with "error" key)
        if "error" in rj:
            log(f"  API error: {rj['error'].get('message', rj['error']) if isinstance(rj['error'], dict) else rj['error']}")
            if attempt < max_retries:
                continue
            raise KeyError(f"API error: {rj['error']}")
        # Log token usage if available
        usage = rj.get("usage", {})
        finish_reason = (rj.get("choices") or [{}])[0].get("finish_reason", "")
        if usage:
            metadata = rj.get("openrouter_metadata") or {}
            route = metadata.get("endpoints", {})
            available = route.get("available", []) if isinstance(route, dict) else []
            selected = next(
                (endpoint for endpoint in available
                 if isinstance(endpoint, dict) and endpoint.get("selected")),
                {},
            )
            tok_msg = f"  tokens: prompt={usage.get('prompt_tokens',0)} completion={usage.get('completion_tokens',0)} total={usage.get('total_tokens',0)}"
            if effective_think_level:
                tok_msg += f" thinking={effective_think_level}"
            log(tok_msg)
            _run_log({
                "event": "tokens",
                "prompt": usage.get("prompt_tokens", 0),
                "completion": usage.get("completion_tokens", 0),
                "total": usage.get("total_tokens", 0),
                "openrouter_cost": usage.get("cost", 0),
                "model": selected.get("model") or rj.get("model", MODEL),
                "provider": selected.get("provider") or rj.get("provider", ""),
                "route_attempt": selected.get("attempt"),
                "thinking": effective_think_level,
                "finish_reason": finish_reason,
                "attempt": attempt,
            })
        if finish_reason == "length":
            log("  output hit token budget (finish_reason=length)")
        msg = rj["choices"][0]["message"]
        text = msg.get("content") or ""
        # OpenRouter reasoning: if content is null/empty, try reasoning_content
        # (model may put JSON in reasoning when token budget is tight)
        if not text.strip():
            reasoning = msg.get("reasoning_content") or ""
            if not reasoning:
                r = msg.get("reasoning", "")
                reasoning = r.get("content", "") if isinstance(r, dict) else (r or "")
            text = reasoning
        if raw:
            return text
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
            text = text[text.index("{"):]

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
                raise json.JSONDecodeError("Expected JSON object, got " + type(parsed).__name__, text, 0)
            return _accept_or_raise(_attach_block(parsed), text)
        except json.JSONDecodeError as parse_err:
            # E03: attempt mechanical repair before burning a retry
            repaired = _repair_json(text)
            if repaired is not None:
                try:
                    accepted = _accept_or_raise(_attach_block(repaired), text)
                    log(f"  JSON repaired on attempt {attempt}")
                    return accepted
                except json.JSONDecodeError:
                    pass
            if attempt < max_retries:
                # Action-specific budget: a truncated write/edit payload needs
                # room for content, not more reasoning.
                if budget < STEP_WRITE_TOKENS and _WRITE_ATTEMPT_RE.search(text):
                    budget = STEP_WRITE_TOKENS
                    log(f"  write/edit payload budget -> {budget}")
                think_str = f" thinking={effective_think_level}" if effective_think_level else ""
                log(f"  [retry {attempt+1}]{think_str} JSON parse failed, raw: {text[:120]}")
            else:
                # Typed classification for the caller (issue #7): output that
                # hit the token budget is a transport failure of the action
                # envelope, not model noise — the recovery differs.
                parse_err.malformed_action = True
                parse_err.response_truncated = finish_reason == "length"
                raise


_KNOWN_ERROR_TYPES = {"timeout", "missing_tool", "permission_denied", "missing_file",
                      "compile_error", "edit_failed", "stuck_loop", "unknown",
                      "malformed_action", "response_truncated"}

# E05: Error types where thinking escalation is counterproductive.
# These are structural failures — the scaffold knows what went wrong and the model
# needs different information or parameters, not deeper reasoning.
# Semantic failures (compile_error, unknown) keep thinking escalation.
_NO_THINK_ERRORS = frozenset({"edit_failed", "missing_file", "timeout",
                              "missing_tool", "permission_denied"})

# E06: Short recovery hints injected into step output after typed failures.
# Tells the model what to do next without needing thinking tokens to rediscover it.
_RECOVERY_HINTS = {
    "edit_failed": "Read the file first, then retry edit with exact text from the file.",
    "missing_file": "Check the filename. Use shell ls to list directory contents.",
}


def _extract_error_type(err):
    """Extract [type] prefix from error string if present, else classify by heuristic."""
    # Check for existing [type] prefix from classify_error / run loop
    if err.startswith("["):
        bracket_end = err.find("]")
        if bracket_end > 1:
            candidate = err[1:bracket_end]
            if candidate in _KNOWN_ERROR_TYPES:
                return candidate, err[bracket_end + 2:]  # strip "[type] " prefix
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
    summarized = {}
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
        digest.append({
            "action": s.get("action"),
            "arg": (s.get("arg") or "")[-120:],
            "ok": s.get("ok"),
            "output": (s.get("output") or "")[:80],
        })
    return digest


def _mutation_target_key(step, working_dir=None):
    """Stable operation-aware identity for write/edit transition tracking."""
    target = step.get("_target")
    if _valid_nonempty_str(target):
        return target
    arg = step.get("arg", "")
    if not _valid_nonempty_str(arg):
        return None
    if working_dir is not None:
        path = _step_path(arg, working_dir)
        lexical = os.path.abspath(os.fspath(path))
        try:
            if step.get("append") or step.get("_append"):
                # Append opens the target directly and follows a leaf symlink.
                path = path.resolve(strict=False)
            else:
                # Atomic overwrite/edit replaces a leaf symlink rather than
                # mutating its referent. Resolve directory aliases only.
                path = path.parent.resolve(strict=False) / path.name
        except (OSError, RuntimeError, ValueError):
            path = Path(lexical)
        return os.path.normcase(os.path.normpath(os.fspath(path)))
    return os.path.normcase(os.path.normpath(arg))


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


def _target_recovery_arg(target, working_dir):
    """Action-ready spelling for a frozen mutation target."""
    if not _valid_nonempty_str(target):
        return None
    # Keep the canonical absolute identity. A relative spelling can silently
    # retarget if the working-directory path (or one of its parents) is itself
    # a symlink changed by a shell step during recovery.
    return target


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
        if not (isinstance(info, dict)
                and info.get("append_allowed", False)):
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
        target, (_, last_step) = max(
            unresolved.items(), key=lambda item: item[1][0])
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
        name, recovery_arg, append_allowed = _pending_empty_hint(
            target, pending_info)
        return {
            "incomplete_write": name,
            "incomplete_write_target": recovery_arg,
            "incomplete_write_append_allowed": append_allowed,
        }
    return None


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


def _pending_empty_recovery(pending, logical_target, operation_target,
                            is_append):
    """Find a pending zero-byte recovery by pathname or append referent."""
    recovery = pending.get(logical_target) if logical_target is not None else None
    if not is_append:
        return recovery
    matches = []
    for key, info in pending.items():
        append_allowed = (isinstance(info, dict)
                          and info.get("append_allowed", False))
        same_operation = (
            operation_target is not None
            and isinstance(info, dict)
            and operation_target in _pending_append_targets(info))
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
            isinstance(info, dict)
            and info.get("append_allowed", False)
            for info in matches
        )
    }


def _clear_pending_empty_writes(pending, logical_target, operation_target,
                                is_append):
    """Clear zero-byte obligations satisfied by a successful write."""
    keys = set()
    if not is_append and logical_target is not None:
        keys.add(logical_target)
    if operation_target is not None:
        for key, info in pending.items():
            if (isinstance(info, dict)
                    and info.get("append_allowed", False)
                    and operation_target in _pending_append_targets(info)):
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
    ok_mutations = [idx for idx, s in enumerate(task_steps)
                    if s.get("action") in ("write", "edit") and s.get("ok")]
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
    validated = any(s.get("action") == "shell" and s.get("ok")
                    for s in task_steps[last_mutation + 1:])
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
    task_steps = state.get("all_steps", [])[state.get("task_start_step_count", 0):]
    current_task = state.get("current_task", "")
    incomplete = _incomplete_write_visibility(
        state.get("all_steps", []), state.get("pending_empty_writes"))
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
    return ask_llm([
        {"role": "system", "content": SYSTEM_PLAN},
        {"role": "user", "content": f"REQUEST:\n{user_prompt}\n\nSTATE:\n{json.dumps(plan_state)}"}
    ], max_tokens=PLANNER_MAX_TOKENS, think=is_replan,
       timeout=LLM_TIMEOUT_REPLAN if is_replan else None,
       reasoning_policy=state.get("reasoning_policy", DEFAULT_REASONING_POLICY),
       reasoning_trigger="planner_replan" if is_replan else "initial_plan")


MAX_INPUT = 300  # max chars per non-goal field sent to executor
GOAL_CONTEXT_CHARS = int(os.environ.get("AGENT_GOAL_CONTEXT_CHARS", "300"))
if GOAL_CONTEXT_CHARS < 1:
    raise ValueError("AGENT_GOAL_CONTEXT_CHARS must be a positive integer")


def get_step(task, state, goal="", step_num=0, max_steps=MAX_STEPS, think=False,
             reasoning_policy=DEFAULT_REASONING_POLICY,
             reasoning_trigger="executor",
             goal_context_chars=GOAL_CONTEXT_CHARS,
             write_pressure=False, validate_pressure=None):
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
        slim_steps.append({
            "action": s["action"], "arg": arg,
            "ok": s["ok"], "output": s.get("output", "")[:out_cap]
        })
    slim = {
        "task": state.get("current_task", task)[:MAX_INPUT],
        "task_index": state.get("task_index", ""),
        "step": f"{step_num+1}/{max_steps}",
        "last_steps": slim_steps,
    }
    incomplete = _incomplete_write_visibility(
        state.get("all_steps", []), state.get("pending_empty_writes"))
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
        user_msg += ("\nNOTE: several observation steps done but no write yet. "
                     "Next action MUST be write, edit, or shell — or fail with a one-line reason.")
    if validate_pressure:
        user_msg += (f"\nNOTE: {validate_pressure} is already written. Do NOT write the whole "
                     "file again. Next action MUST be shell (verify it), edit (targeted fix), "
                     "or done.")
    return ask_llm([
        {"role": "system", "content": SYSTEM_STEP},
        {"role": "user", "content": user_msg}
    ], max_tokens=STEP_TOKENS, think=think,
       reasoning_policy=reasoning_policy,
       reasoning_trigger=reasoning_trigger)


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
_LOW_VALUE_TASK_WORDS = frozenset({
    "a", "an", "and", "again", "code", "correct", "file", "for", "in",
    "of", "rebuild", "recompile", "rerun", "run", "the", "then", "to",
    "using", "with",
})


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


def replan_task(failed_task, errors, completed_tasks, state, user_prompt,
                goal_context_chars=GOAL_CONTEXT_CHARS):
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
    task_steps = state.get("all_steps", [])[state.get("task_start_step_count", 0):]
    failed_steps = _step_digest(task_steps, count=3)
    if failed_steps:
        replan_state["failed_steps"] = failed_steps
    # failed_steps and advisory progress flags remain task-scoped even when
    # the slice is empty. Incomplete artifacts are checked run-wide because
    # they remain a completion blocker across replacement-task boundaries.
    incomplete = _incomplete_write_visibility(
        state.get("all_steps", []), state.get("pending_empty_writes"))
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
        result = ask_llm([
            {"role": "system", "content": SYSTEM_TASK_REPLAN},
            {"role": "user", "content": f"GOAL:\n{user_prompt[:goal_context_chars]}\n\nSTATE:\n{json.dumps(replan_state)}"}
        ], max_tokens=TASK_REPLAN_MAX_TOKENS, think=False, max_retries=0,
           reasoning_policy=state.get("reasoning_policy", DEFAULT_REASONING_POLICY),
           reasoning_trigger="task_local_replan")
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


def _step_path(arg, working_dir):
    p = Path(arg)
    if not p.is_absolute():
        p = Path(working_dir) / p
    return p


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

    shell_steps = [(i, s) for i, s in enumerate(all_steps)
                   if s.get("action") == "shell"]
    if _VALIDATE_KEYWORDS.search(user_prompt) and shell_steps:
        last_shell_idx, last_shell = shell_steps[-1]
        if not last_shell.get("ok"):
            return False
        later_mutation = any(
            s.get("action") in ("write", "edit") and s.get("ok")
            for s in all_steps[last_shell_idx + 1:]
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
        evidence_lines.append(f"Task {i+1}: {task}")
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
    user_msg = (f"GOAL:\n{user_prompt}\n\n"
                f"COMPLETED TASKS AND EVIDENCE:\n{evidence}\n\n"
                f"FILES IN WORKING DIRECTORY:\n{json.dumps(files)}")
    try:
        result = ask_llm([
            {"role": "system", "content": SYSTEM_VALIDATE},
            {"role": "user", "content": user_msg}
        ], max_tokens=768, think=True, think_level="high", max_retries=0,
           reasoning_policy=state.get("reasoning_policy", DEFAULT_REASONING_POLICY),
           reasoning_trigger="final_validator")
        if (isinstance(result, dict)
                and isinstance(result.get("valid"), bool)):
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


_COMPILER_EXES = frozenset({
    "cc", "gcc", "g++", "clang", "clang++", "c++", "rustc", "javac",
    "make", "cmake", "cargo", "go", "tsc", "swiftc",
})


def _is_compiler_command(cmd):
    """Check if a shell command invokes a compiler or build tool."""
    try:
        parts = shlex.split(cmd)
    except ValueError:
        parts = cmd.split()
    if not parts:
        return False
    exe = os.path.basename(parts[0])
    return exe in _COMPILER_EXES


def classify_error(output, action="shell", cmd=""):
    """Classify an error output into a typed category for structured diagnostics."""
    out = output.lower()
    if "timeout" in out or output == "TIMEOUT":
        return "timeout"
    if "command not found" in out:
        return "missing_tool"
    if "permission denied" in out:
        return "permission_denied"
    if "no such file" in out or "no such file or directory" in out:
        if action == "shell" and cmd and _is_compiler_command(cmd):
            return "compile_error"
        return "missing_file"
    if action == "shell" and cmd and _is_compiler_command(cmd):
        if re.search(
            r"error generated|implicit function declaration|undeclared (?:library )?function|"
            r"include the header <[^>]+>|undefined reference|undefined symbols",
            out,
        ):
            return "compile_error"
    if "syntax error" in out or "error:" in out:
        return "compile_error"
    return "unknown"


_EXPECTED_FAILURE_POS_RE = re.compile(
    r'\b(observe|confirm|verify|check)\b.*\b(fail|error|bug|broken)\b'
    r'|\b(will fail|should fail|expect.*(fail|error)|initial failure|read the error)\b',
    re.I,
)
_EXPECTED_FAILURE_NEG_RE = re.compile(
    r'\b(no|not|without)\s+(fail|failure|error|bug|crash|broken)\b'
    r'|\b(error|failure|bug)\b.{0,20}\b(fixed|resolved|gone)\b'
    r'|\b(fix|repair|resolve)\b.*\b(error|failure|bug)\b',
    re.I,
)


def _expects_failure(task):
    return bool(_EXPECTED_FAILURE_POS_RE.search(task)
                and not _EXPECTED_FAILURE_NEG_RE.search(task))


_COMPILE_REPAIR_PATTERNS = [
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
        r'([A-Za-z0-9_./-]+\.(?:c|h)):\d+(?::\d+)?:',
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
    for pattern in _COMPILE_REPAIR_PATTERNS:
        if not pattern["diagnostic_re"].search(error_output):
            continue
        candidates = [
            f for f in _compile_repair_candidates(error_output, cmd, working_dir)
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
    if "include" not in task_lower or not re.search(r"\b(add|insert|edit|include|fix)\b", task_lower):
        return None
    requested_include = None
    for include in ("#include <stdio.h>", "#include <string.h>"):
        if include.lower() in task_lower or include.split("<", 1)[1].rstrip(">").lower() in task_lower:
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


# Command patterns that need longer timeouts
_LONG_TIMEOUT_PATTERNS = [
    "install", "update", "upgrade",  # package managers
    "cmake", "make", "cargo build", "go build",  # build tools
    "npm install", "pip install", "brew ",  # specific installers
]
SHELL_TIMEOUT = 30       # default
SHELL_TIMEOUT_LONG = 120  # for install/build commands
SHELL_TIMEOUT_MAX = 300   # hard cap for model-specified timeout


def _get_shell_timeout(cmd, hint=None):
    """Return timeout for a shell command. Uses longer timeout for install/build patterns."""
    if hint is not None:
        return min(max(int(hint), 5), SHELL_TIMEOUT_MAX)
    cmd_lower = cmd.lower()
    for pattern in _LONG_TIMEOUT_PATTERNS:
        if pattern in cmd_lower:
            return SHELL_TIMEOUT_LONG
    return SHELL_TIMEOUT


def _atomic_write_text(path, content):
    """Write via temp file + rename so a crashed/interrupted write never
    leaves a partial file behind."""
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(content)
    os.replace(tmp, path)


# VCS / dependency / build directories excluded from search and tree walks.
_REPO_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__",
    ".venv", "venv", "target", "dist", "build",
})


def _iter_repo_files(root, max_files=SEARCH_MAX_FILES):
    """Yield visible repo files under root, bounded and deterministic."""
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in _REPO_SKIP_DIRS and not d.startswith(".")
        )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            yield Path(dirpath) / name
            count += 1
            if count >= max_files:
                return


def _read_offset_limit(action):
    """Normalized 1-based read window (offset, limit) from an action dict."""
    try:
        offset = max(1, int(action.get("offset") or 1))
    except (TypeError, ValueError):
        offset = 1
    try:
        limit = max(1, min(int(action.get("limit") or READ_LINES), READ_LIMIT_MAX))
    except (TypeError, ValueError):
        limit = READ_LINES
    return offset, limit


def execute(action, working_dir="."):
    act = action.get("action", "")
    if act == "shell":
        try:
            timeout = _get_shell_timeout(action["arg"], action.get("timeout"))
            r = subprocess.run(
                action["arg"], shell=True, capture_output=True,
                text=True, timeout=timeout, cwd=working_dir
            )
            out = r.stdout[:MAX_RESULT] + r.stderr[-MAX_RESULT:]
            result = {"ok": r.returncode == 0, "output": out.strip() or "(no output)"}
            if not result["ok"]:
                result["error_type"] = classify_error(result["output"], "shell", cmd=action["arg"])
            return result
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": "TIMEOUT", "error_type": "timeout"}
    elif act == "write":
        try:
            p = Path(action["arg"])
            if not p.is_absolute():
                p = Path(working_dir) / p
            p.parent.mkdir(parents=True, exist_ok=True)
            content = action.get("content", "")
            # Auto-serialize dict/list content — models often output JSON as objects
            # instead of escaped strings (e.g. "content": {"key": "val"} not "content": "{\"key\": \"val\"}")
            if isinstance(content, (dict, list)):
                content = json.dumps(content, indent=2)
            if action.get("append"):
                # Chunked-write transport: append one chunk at a time so large
                # files fit within the executor token budget.
                existed = p.exists()
                with open(p, "a") as f:
                    f.write(content)
                size = p.stat().st_size
                verb = "Appended to" if existed else "Wrote"
                return {"ok": True,
                        "output": f"{verb} {p.name} (+{len(content)} chars, total {size})"}
            _atomic_write_text(p, content)
            return {"ok": True, "output": f"Wrote {p.name}"}
        except Exception as e:
            out = str(e)[:MAX_RESULT]
            return {"ok": False, "output": out, "error_type": classify_error(out, "write")}
    elif act == "edit":
        try:
            p = Path(action["arg"])
            if not p.is_absolute():
                p = Path(working_dir) / p
            if not p.exists():
                return {"ok": False, "output": f"File not found: {p.name}",
                        "error_type": "missing_file"}
            text = p.read_text()
            find = action.get("find", "")
            replace = action.get("replace", "")
            if not find:
                return {"ok": False, "output": "edit requires non-empty 'find'",
                        "error_type": "edit_failed"}
            count = text.count(find)
            if count == 0:
                return {"ok": False, "output": f"No match for find string in {p.name}",
                        "error_type": "edit_failed"}
            if count > 1:
                return {"ok": False,
                        "output": f"Ambiguous: find string matches {count} times in {p.name}",
                        "error_type": "edit_failed"}
            _atomic_write_text(p, text.replace(find, replace, 1))
            return {"ok": True, "output": f"Edited {p.name}"}
        except Exception as e:
            out = str(e)[:MAX_RESULT]
            return {"ok": False, "output": out, "error_type": classify_error(out, "edit")}
    elif act == "read":
        try:
            p = Path(action["arg"])
            if not p.is_absolute():
                p = Path(working_dir) / p
            text = p.read_text()
            lines = text.splitlines()
            total = len(lines)
            offset, limit = _read_offset_limit(action)
            # Structured continuation metadata (issue #7): totals and a content
            # hash let the harness audit reads and detect files changing
            # between windows. Not sent to the model — header stays compact.
            meta = {"total_lines": total,
                    "total_bytes": len(text.encode("utf-8", errors="replace")),
                    "sha256": hashlib.sha256(
                        text.encode("utf-8", errors="replace")).hexdigest()[:12]}
            if offset > total:
                return {"ok": True, "truncated": False,
                        "output": f"[{p.name}: offset {offset} past end of file ({total} lines)]",
                        **meta}
            window = lines[offset - 1: offset - 1 + limit]
            body = "\n".join(window)
            end = offset + len(window) - 1
            truncated = False
            header = f"[{p.name}: lines {offset}-{end} of {total}"
            if len(body) > READ_CHARS:
                body = body[:READ_CHARS]
                truncated = True
                header += f", cut at {READ_CHARS} chars"
            if end < total:
                truncated = True
                header += f"; continue: offset={end + 1}"
            header += "]"
            return {"ok": True, "output": f"{header}\n{body}",
                    "truncated": truncated,
                    "continuation": end + 1 if end < total else None,
                    **meta}
        except Exception as e:
            out = str(e)[:MAX_RESULT]
            return {"ok": False, "output": out, "error_type": classify_error(out, "read")}
    elif act == "search":
        pattern = action.get("arg", "")
        if not pattern:
            return {"ok": False, "output": "search requires non-empty 'arg' (pattern)",
                    "error_type": "unknown"}
        try:
            base = Path(action.get("path") or ".")
            if not base.is_absolute():
                base = Path(working_dir) / base
            if not base.is_dir():
                return {"ok": False, "output": f"Directory not found: {base.name}",
                        "error_type": "missing_file"}
            matches = []
            for f in _iter_repo_files(base):
                try:
                    text = f.read_text(errors="replace")
                except OSError:
                    continue
                if "\0" in text[:2048]:
                    continue  # skip binary files
                for lineno, line in enumerate(text.splitlines(), 1):
                    if pattern in line:
                        matches.append(f"{f.relative_to(base)}:{lineno}: {line.strip()[:100]}")
                        if len(matches) >= SEARCH_MAX_MATCHES:
                            break
                if len(matches) >= SEARCH_MAX_MATCHES:
                    break
            body = "\n".join(matches)
            truncated = len(matches) >= SEARCH_MAX_MATCHES or len(body) > SEARCH_MAX_CHARS
            body = body[:SEARCH_MAX_CHARS]
            header = f"[{len(matches)}{'+' if truncated else ''} matches for '{pattern[:40]}'"
            if truncated:
                header += " — narrow the pattern"
            header += "]"
            return {"ok": True,
                    "output": f"{header}\n{body}" if body else header,
                    "truncated": truncated}
        except Exception as e:
            out = str(e)[:MAX_RESULT]
            return {"ok": False, "output": out, "error_type": classify_error(out, "search")}
    elif act == "tree":
        try:
            base = Path(action.get("arg") or ".")
            if not base.is_absolute():
                base = Path(working_dir) / base
            if not base.is_dir():
                return {"ok": False, "output": f"Directory not found: {base.name}",
                        "error_type": "missing_file"}
            entries = []
            root_depth = len(base.parts)
            for dirpath, dirnames, filenames in os.walk(base):
                depth = len(Path(dirpath).parts) - root_depth
                dirnames[:] = sorted(
                    d for d in dirnames
                    if d not in _REPO_SKIP_DIRS and not d.startswith(".")
                )
                if depth >= TREE_MAX_DEPTH:
                    dirnames[:] = []
                rel_dir = Path(dirpath).relative_to(base)
                prefix = "" if str(rel_dir) == "." else f"{rel_dir}/"
                entries.extend(f"{prefix}{d}/" for d in dirnames)
                entries.extend(
                    f"{prefix}{name}" for name in sorted(filenames)
                    if not name.startswith(".")
                )
                if len(entries) >= TREE_MAX_ENTRIES:
                    entries = entries[:TREE_MAX_ENTRIES]
                    break
            body = "\n".join(entries)
            truncated = len(entries) >= TREE_MAX_ENTRIES or len(body) > TREE_MAX_CHARS
            body = body[:TREE_MAX_CHARS]
            header = f"[tree of {base.name}: {len(entries)} entries"
            if truncated:
                header += f", capped at {TREE_MAX_ENTRIES}"
            header += "]"
            return {"ok": True,
                    "output": f"{header}\n{body}" if body else header,
                    "truncated": truncated}
        except Exception as e:
            out = str(e)[:MAX_RESULT]
            return {"ok": False, "output": out, "error_type": classify_error(out, "tree")}
    elif act == "done":
        return {"ok": True, "output": "task_complete"}
    elif act == "fail":
        return {"ok": False, "output": action.get("reasoning", "failed")}
    return {"ok": False, "output": f"unknown action: {act}"}


def _run_loop(user_prompt, working_dir, max_replans=MAX_REPLANS,
              max_tasks=MAX_TASKS, max_steps=MAX_STEPS,
              reasoning_policy=DEFAULT_REASONING_POLICY,
              goal_context_chars=GOAL_CONTEXT_CHARS):
    """Core agent loop. Returns structured result dict.

    Used by run() (public API, returns bool) and by integration test harness
    (needs rich dict with state + log). All production behavior lives here:
    preflight, policy, null normalization, error reset, timeout retry, etc.
    """
    if reasoning_policy not in REASONING_POLICIES:
        raise ValueError(
            f"reasoning_policy must be one of {', '.join(REASONING_POLICIES)}"
        )
    if goal_context_chars < 1:
        raise ValueError("goal_context_chars must be a positive integer")
    # Freeze the executor/replanner view once so all policy arms receive the same
    # task context even if module configuration changes while a run is active.
    goal_context = user_prompt[:goal_context_chars]
    state = {
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
    history = []

    def _skip_step(task_index, step_num, act, action, reason):
        """Record a selected-but-not-dispatched action in run metrics + log."""
        state["skipped_steps"] += 1
        _run_log({"event": "step_skipped", "task_index": task_index,
                  "step": step_num, "action": act,
                  "arg": action.get("arg", "")[:120], "reason": reason})

    t_run = time.time()
    log(f"Prompt: {user_prompt}")
    log(f"Working directory: {working_dir}")
    _run_log({"event": "run_start", "prompt": user_prompt, "working_dir": working_dir,
              "backend": LLM_BACKEND, "model": MODEL,
              "provider": OPENROUTER_PROVIDER if LLM_BACKEND == "openrouter" else "",
              "allow_provider_fallbacks": OPENROUTER_ALLOW_FALLBACKS,
              "require_provider_parameters": OPENROUTER_REQUIRE_PARAMETERS,
              "reasoning_policy": reasoning_policy,
              "limits": {"max_replans": max_replans, "max_tasks": max_tasks,
                         "max_steps": max_steps,
                         "goal_context_chars": goal_context_chars}})
    # Preflight: probe environment and set policy
    env = preflight_probe(working_dir)
    state["environment"] = env
    state["policy"] = get_policy()
    log(f"Environment: platform={env['platform']} arch={env['arch']}")
    log(f"Available tools: {env['available_tools']}")
    if env["missing_tools"]:
        log(f"Missing tools: {env['missing_tools']}")
    log(f"Package managers: {env['package_managers']}")
    log(f"Policy: allow_system_installs={state['policy']['allow_system_installs']}")
    _warm_cache()

    # Rewrite damping is run-scoped, not attempt-scoped. A task-local retry or
    # full replan must not let the executor restart the same-target full-write
    # streak; only the documented successful shell/edit and truncation paths
    # below disarm it.
    last_write_target = None
    consecutive_target_writes = 0

    for replan in range(max_replans):
        log("=" * 40)
        t_plan = time.time()
        log(f"Planning (attempt {replan + 1}/{max_replans})...")
        state["planning_attempt"] = replan
        try:
            plan = get_plan(user_prompt, state)
        except LLMTransportError as e:
            log(f"  Planner transport error: {e}")
            state["errors"].append(f"[unknown] Planner transport error: {str(e)[:100]}")
            history.append({"event": "plan_error", "replan": replan, "error": str(e)[:200]})
            _run_log({"event": "plan_error", "replan": replan, "error": str(e)[:200],
                      "wall_s": round(time.time() - t_plan, 2)})
            continue  # consumes a plan attempt
        raw_tasks = plan.get("tasks")
        tasks = raw_tasks[:max_tasks] if isinstance(raw_tasks, list) else []
        if not tasks or any(not _valid_nonempty_str(task) for task in tasks):
            error = "[malformed_plan] planner returned no valid tasks"
            state["errors"].append(error)
            log(f"  Planner contract error: {error}")
            history.append({"event": "plan_error", "replan": replan,
                            "error": error})
            _run_log({"event": "plan_error", "replan": replan,
                      "error": error, "wall_s": round(time.time() - t_plan, 2)})
            continue
        state["errors"] = []  # reset errors each replan; planner already saw them
        plan_wall = time.time() - t_plan
        log(f"Plan ({plan_wall:.1f}s, planner_wall_time={plan_wall:.1f}s): {tasks}")
        history.append({"event": "plan", "replan": replan, "tasks": tasks})
        _run_log({"event": "plan", "replan": replan, "tasks": tasks,
                  "wall_s": round(plan_wall, 2)})

        all_done = True
        for i, task in enumerate(tasks):
            # Carry over last step from previous task so executor has cross-task context
            prev_last = state["last_steps"][-1:] if state.get("last_steps") else []
            t_task = time.time()
            # Scope for no_write_executed: an earlier task's write must not
            # mask a stall in this one.
            state["task_start_step_count"] = len(state["all_steps"])

            # E11: inner retry loop — try task-local replan before full replan
            task_done = False
            saved_errors = []
            for task_attempt in range(1 + MAX_TASK_LOCAL_REPLANS):
                state["current_task"] = task
                state["task_index"] = f"{i + 1}/{len(tasks)}"
                state["last_steps"] = list(prev_last)
                log(f"--- Task {i + 1}/{len(tasks)}: {task} ---")

                # Reset per-attempt execution state
                task_done = False
                task_steps = []
                use_think = False
                reasoning_trigger = "executor"
                dup_skip_count = 0
                last_successful_edit = None
                observe_executed = 0
                commit_executed = 0
                observe_blocked = 0
                task_wants_write = _is_write_shaped(task)
                completed_repair = _task_satisfied_by_deterministic_repair(task, state)
                if completed_repair:
                    log(f"  auto-done (deterministic repair already satisfied task: {completed_repair.get('output', '')[:60]})")
                    task_steps.append(completed_repair)
                    task_done = True
                    break
                for step in range(max_steps):
                    t_step = time.time()
                    try:
                        action = get_step(
                            task, state, goal=goal_context, step_num=step,
                            max_steps=max_steps, think=use_think,
                            reasoning_policy=reasoning_policy,
                            reasoning_trigger=reasoning_trigger,
                            goal_context_chars=goal_context_chars,
                            write_pressure=(
                                task_wants_write and commit_executed == 0
                                and observe_executed >= WRITE_PRESSURE_OBSERVATIONS),
                            validate_pressure=(
                                Path(str(last_write_target)).name
                                if last_write_target is not None
                                and consecutive_target_writes >= REWRITE_PRESSURE_WRITES
                                else None),
                        )
                    except LLMTransportError as e:
                        log(f"  [{step + 1}] LLM transport error ({time.time()-t_step:.1f}s): {e}")
                        state["errors"].append(f"[unknown] LLM transport error on task '{task}': {str(e)[:100]}")
                        break
                    except (json.JSONDecodeError, KeyError) as e:
                        # Typed parse failures (issue #7): the replanner should
                        # know whether the action envelope was truncated at the
                        # token budget or simply malformed.
                        if getattr(e, "response_truncated", False):
                            etype = "response_truncated"
                        elif getattr(e, "malformed_action", False):
                            etype = "malformed_action"
                        else:
                            etype = "unknown"
                        log(f"  [{step + 1}] LLM parse error ({time.time()-t_step:.1f}s) [{etype}]")
                        state["errors"].append(f"[{etype}] LLM parse error on task '{task}': {str(e)[:100]}")
                        _run_log({"event": "step_error", "task_index": i, "step": step,
                                  "error_type": etype})
                        break
                    # Normalize None → "" for optional string fields (models emit "arg": null)
                    for _k in ("arg", "content", "reasoning", "find", "replace"):
                        if action.get(_k) is None:
                            action[_k] = ""
                    act = action.get("action", "")
                    state["selected_steps"] += 1
                    log(f"  [{step + 1}] {act}: {action['arg'][:80]}")

                    if act == "done":
                        unresolved = _unresolved_incomplete_writes(
                            state.get("all_steps", []), working_dir)
                        pending_empty = state.get("pending_empty_writes", {})
                        if unresolved or pending_empty:
                            append_allowed = True
                            restrictive = _restrictive_pending_empty(
                                pending_empty)
                            if restrictive is not None:
                                incomplete_target, pending_info = restrictive
                                (incomplete_name, recovery_arg,
                                 append_allowed) = _pending_empty_hint(
                                    incomplete_target, pending_info)
                            elif unresolved:
                                incomplete_target, (_, incomplete_step) = max(
                                    unresolved.items(),
                                    key=lambda item: item[1][0])
                                incomplete_name, recovery_arg = (
                                    _incomplete_step_hint(
                                        incomplete_target, incomplete_step))
                            else:
                                incomplete_target, pending_info = (
                                    _next_pending_empty(pending_empty))
                                (incomplete_name, recovery_arg,
                                 append_allowed) = _pending_empty_hint(
                                    incomplete_target, pending_info)
                            if append_allowed:
                                recovery = (
                                    "Retry that exact target with append:true if it "
                                    "still identifies the intended file, or restart "
                                    "it with a complete append:false write.")
                            else:
                                recovery = (
                                    "Resend a shorter write to that exact target with "
                                    "append:false before using append:true.")
                            log(f"  [{step + 1}] skip (done with incomplete write: "
                                f"{incomplete_name})")
                            _skip_step(i, step, act, action,
                                       "incomplete_write_done")
                            state["last_steps"].append({
                                "action": "done", "arg": "", "ok": True,
                                "output": (
                                    f"Cannot finish: {incomplete_name} is incomplete "
                                    f"at {recovery_arg}. {recovery}"),
                            })
                            continue
                        task_done = True
                        break
                    if act == "fail":
                        reason = action.get("reasoning", "no reason")
                        log(f"  FAIL ({time.time()-t_step:.1f}s): {reason}")
                        state["errors"].append(f"Task '{task}': {reason}")
                        break

                    # Sentinel transport truncation (issue #15): keep the
                    # complete lines that arrived and steer the model to finish
                    # the file with chunked append instead of failing the step.
                    truncated_write = act == "write" and action.pop("content_truncated", False)
                    logical_write_target = (_mutation_target_key(
                        {"arg": action.get("arg", "")}, working_dir)
                        if act == "write" else None)
                    operation_write_target = (_mutation_target_key({
                        "arg": action.get("arg", ""),
                        "append": bool(action.get("append")),
                    }, working_dir) if act == "write" else None)
                    pending_recovery = _pending_empty_recovery(
                        state["pending_empty_writes"], logical_write_target,
                        operation_write_target, bool(action.get("append")))
                    if (act == "write" and action.get("append")
                            and pending_recovery
                            and not pending_recovery.get("append_allowed", False)):
                        log(f"  [{step + 1}] skip (append before first replacement "
                            "chunk landed)")
                        _skip_step(i, step, act, action,
                                   "append_after_empty_overwrite")
                        state["last_steps"].append({
                            "action": act, "arg": action.get("arg", ""),
                            "ok": True,
                            "output": ("The replacement's first chunk wrote no bytes. "
                                       "Resend a shorter write with append:false before "
                                       "using append:true."),
                        })
                        continue
                    if truncated_write:
                        kept = action.get("content", "")
                        kept = kept[: kept.rfind("\n") + 1]
                        if not kept:
                            log(f"  [{step + 1}] skip (write truncated before a complete line)")
                            _skip_step(i, step, act, action, "truncated_write_empty")
                            # The recovery instruction asks for a clean resend;
                            # disarm rewrite damping before that resend even
                            # though this empty partial attempt wrote no bytes.
                            last_write_target = None
                            consecutive_target_writes = 0
                            # Empty append attempts are obligations on the
                            # referent observed at dispatch time. Key them by
                            # that operation target so retargeting a leaf
                            # symlink cannot overwrite an older obligation.
                            pending_target = (
                                operation_write_target
                                if action.get("append")
                                else logical_write_target)
                            recovery_arg = action.get("arg", "") or "file"
                            if pending_target is not None:
                                existing = state["pending_empty_writes"].get(
                                    pending_target)
                                append_allowed = bool(action.get("append"))
                                if isinstance(existing, dict):
                                    append_allowed = (
                                        existing.get("append_allowed", False)
                                        and append_allowed)
                                append_target = _mutation_target_key({
                                    "arg": action.get("arg", ""),
                                    "append": True,
                                }, working_dir)
                                append_targets = list(
                                    _pending_append_targets(existing))
                                if (append_target is not None
                                        and append_target not in append_targets):
                                    append_targets.append(append_target)
                                recovery_arg = _target_recovery_arg(
                                    pending_target, working_dir)
                                state["pending_empty_writes"][pending_target] = {
                                    "name": Path(
                                        action.get("arg", "") or "file").name,
                                    "append_allowed": append_allowed,
                                    "append_targets": append_targets,
                                    "recovery_arg": recovery_arg,
                                }
                            # Nothing was written: the first dispatched chunk
                            # must stay a non-append write (append would land
                            # on a stale existing file), only later chunks
                            # may append.
                            if action.get("append"):
                                obs = ("Append truncated before a complete line. "
                                       "Resend a smaller append:true chunk at the "
                                       f"exact target {recovery_arg}.")
                            else:
                                obs = (
                                    "Write truncated before a complete line. Resend the "
                                    f"write (no append) to the exact target {recovery_arg} "
                                    "with a shorter first chunk, then continue with "
                                    "append:true chunks.")
                            state["last_steps"].append({
                                "action": act, "arg": action.get("arg", ""), "ok": True,
                                "output": obs})
                            continue
                        action["content"] = kept

                    # Write-forcing tail reserve (issue #15): on a write-shaped
                    # task the final steps are reserved for committing actions.
                    if (act in OBSERVE_ACTIONS and task_wants_write
                            and commit_executed == 0
                            and max_steps - step <= OBSERVE_TAIL_RESERVE):
                        observe_blocked += 1
                        if observe_blocked >= 2:
                            log(f"  [{step + 1}] auto-fail (observation steps exhausted without a write)")
                            state["errors"].append(
                                f"[stuck_loop] {act} {action.get('arg', '')[:60]}: observation steps exhausted without a write")
                            _skip_step(i, step, act, action, "observe_tail_exhausted")
                            break
                        log(f"  [{step + 1}] skip ({act} blocked: remaining steps reserved for write)")
                        _skip_step(i, step, act, action, "observe_tail_reserved")
                        state["last_steps"].append({
                            "action": act, "arg": action.get("arg", ""), "ok": True,
                            "output": "Observation budget exhausted. Next action MUST be write, edit, or shell — or fail with reason."})
                        continue

                    # Rewrite damping (revision 4): after REWRITE_SKIP_WRITES
                    # successful full writes of the same target with no
                    # intervening successful shell/edit, further full rewrites
                    # are skipped — verify, edit, or finish instead.
                    if (act == "write" and not action.get("append")
                            and not truncated_write
                            and last_write_target is not None
                            and consecutive_target_writes >= REWRITE_SKIP_WRITES
                            and _mutation_target_key(
                                {"arg": action.get("arg", "")}, working_dir)
                            == last_write_target):
                        dup_skip_count += 1
                        log(f"  [{step + 1}] skip (rewrite loop: "
                            f"{action.get('arg', '')[:40]} already written "
                            f"{consecutive_target_writes}x)")
                        _skip_step(i, step, act, action, "rewrite_loop")
                        state["last_steps"].append({
                            "action": act, "arg": action.get("arg", ""), "ok": True,
                            "output": (f"Already written {consecutive_target_writes} times. "
                                       "Do NOT write it again — verify with shell, make a "
                                       "targeted edit, or emit done."),
                        })
                        continue

                    # Duplicate action guard — per-action-type loop detection
                    last = state["last_steps"][-1:] if state["last_steps"] else []
                    if last and last[0]["action"] == act:
                        prev = last[0]
                        same_mutation_target = False
                        if act in ("write", "edit"):
                            current_target_step = {
                                "arg": action.get("arg", ""),
                            }
                            if act == "write" and action.get("append"):
                                current_target_step["append"] = True
                            current_target = _mutation_target_key(
                                current_target_step, working_dir)
                            same_mutation_target = (
                                current_target is not None
                                and _mutation_target_key(prev, working_dir)
                                == current_target)
                        if act in ("write", "edit") and same_mutation_target:
                            # write: same content = duplicate; edit: same find+replace = duplicate
                            is_dup = False
                            if act == "write" and action.get("append"):
                                # Chunked append is never a no-op — an identical
                                # consecutive chunk is a stuck loop, not a duplicate.
                                if prev.get("_append") and prev.get("_content", "") == action.get("content", ""):
                                    log(f"  [{step + 1}] auto-fail (same chunk appended twice to {action.get('arg','')[:40]})")
                                    state["errors"].append(f"[stuck_loop] write {action.get('arg','')[:60]}: same chunk appended twice")
                                    _skip_step(i, step, act, action, "stuck_append")
                                    break
                            elif (act == "write" and not truncated_write
                                  and prev.get("ok")
                                  and not prev.get("_truncated_write")
                                  and not prev.get("_append")
                                  and prev.get("_content", "") == action.get("content", "")):
                                is_dup = True
                            elif act == "edit" and prev.get("ok") and prev.get("_find", "") == action.get("find", "") and prev.get("_replace", "") == action.get("replace", ""):
                                is_dup = True
                            # Consecutive identical failed edit → stuck; bail to replan
                            elif act == "edit" and not prev.get("ok") and prev.get("_find", "") == action.get("find", ""):
                                log(f"  [{step + 1}] auto-fail (same edit failed twice on {action.get('arg','')[:40]})")
                                state["errors"].append(f"[stuck_loop] edit {action.get('arg','')[:60]}: same find string failed twice")
                                _skip_step(i, step, act, action, "stuck_edit")
                                break
                            if is_dup:
                                dup_skip_count += 1
                                log(f"  [{step + 1}] skip (duplicate {act}, same content)")
                                _skip_step(i, step, act, action, f"duplicate_{act}")
                                if prev.get("_truncated_write"):
                                    dup_msg = ("File is incomplete — the earlier write was truncated. "
                                               "Continue with append:true for the rest.")
                                else:
                                    dup_msg = "Already done — file unchanged. Move to next action or emit done."
                                entry = {
                                    "action": act, "arg": action.get("arg", ""),
                                    "ok": True,
                                    "output": dup_msg
                                }
                                # Preserve match metadata so guard still detects duplicates on subsequent turns
                                if act == "write":
                                    entry["_content"] = action.get("content", "")
                                    if prev.get("_truncated_write"):
                                        entry["_truncated_write"] = True
                                elif act == "edit":
                                    entry["_find"] = action.get("find", "")
                                    entry["_replace"] = action.get("replace", "")
                                state["last_steps"].append(entry)
                                if act == "edit" and last_successful_edit and dup_skip_count >= 2:
                                    edit_key = (
                                        action.get("arg", ""),
                                        action.get("find", ""),
                                        action.get("replace", ""),
                                    )
                                    if edit_key == last_successful_edit:
                                        log(f"  [{step + 1}] auto-done (edit already succeeded, model re-emitting)")
                                        task_done = True
                                        break
                                # Defer thinking escalation: first duplicate skip gets a
                                # corrective observation only; escalate on 2+ consecutive skips.
                                # Saves ~10s of thinking time on harmless first-time duplicates.
                                if dup_skip_count >= 2:
                                    use_think = True
                                    reasoning_trigger = "duplicate_action"
                                continue
                        elif act == "shell" and prev.get("arg", "") == action.get("arg", ""):
                            if prev.get("ok"):
                                log(f"  [{step + 1}] auto-done (duplicate successful shell)")
                                _skip_step(i, step, act, action, "duplicate_shell_auto_done")
                                task_done = True
                                break
                            elif prev.get("error_type") == "timeout":
                                # Bump timeout for retry: read actual timeout from previous step,
                                # not from fresh action (which won't have prior bumps)
                                prev_timeout = prev.get("_timeout",
                                                        _get_shell_timeout(action.get("arg", "")))
                                bumped = max(SHELL_TIMEOUT_LONG, prev_timeout * 2)
                                action["timeout"] = min(bumped, SHELL_TIMEOUT_MAX)
                                log(f"  [{step + 1}] retrying after timeout ({action['timeout']}s)")
                            else:
                                log(f"  [{step + 1}] auto-fail (same shell failed twice)")
                                state["errors"].append(f"Stuck: {act} {action.get('arg','')[:60]} failed twice")
                                _skip_step(i, step, act, action, "stuck_shell")
                                break
                        elif act == "read" and prev.get("arg", "") == action.get("arg", ""):
                            # Offset-aware: navigating to a new range of the same
                            # file is legitimate, not a duplicate.
                            prev_key = prev.get("_read_key") or (prev.get("arg", ""), 1)
                            cur_key = (action.get("arg", ""), _read_offset_limit(action)[0])
                            if prev_key != cur_key:
                                pass  # different range — execute normally
                            elif prev.get("ok"):
                                dup_skip_count += 1
                                if dup_skip_count >= 2:
                                    log(f"  [{step + 1}] auto-fail (same read repeated on {action.get('arg','')[:40]})")
                                    state["errors"].append(f"[stuck_loop] read {action.get('arg','')[:60]}: same file read repeatedly")
                                    _skip_step(i, step, act, action, "stuck_read")
                                    break
                                log(f"  [{step + 1}] skip (duplicate read)")
                                _skip_step(i, step, act, action, "duplicate_read")
                                cont = prev.get("_continuation")
                                if cont:
                                    obs = (f"Already read this range. Continue with offset={cont}; "
                                           f"or search, edit, done, or fail.")
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
                                state["last_steps"].append(entry)
                                continue
                            else:
                                log(f"  [{step + 1}] auto-fail (same read failed twice)")
                                state["errors"].append(f"[stuck_loop] read {action.get('arg','')[:60]} failed twice")
                                _skip_step(i, step, act, action, "stuck_read_failed")
                                break

                    dup_skip_count = 0  # reset on any non-skipped action
                    state["executed_steps"] += 1
                    if act in OBSERVE_ACTIONS:
                        observe_executed += 1
                    result = execute(action, working_dir)
                    if result["ok"] and act == "write":
                        _clear_pending_empty_writes(
                            state["pending_empty_writes"],
                            logical_write_target, operation_write_target,
                            bool(action.get("append")))
                    if act not in OBSERVE_ACTIONS and result["ok"]:
                        # Counted only on success (Codex P2, PR #16): a failed
                        # mutation must not disarm write pressure or the
                        # observation tail reserve.
                        commit_executed += 1
                    if act == "write" and result["ok"]:
                        if truncated_write:
                            # A partial (truncated) write is not a completed
                            # rewrite (Codex P1, PR #21): the file is
                            # incomplete, and the recovery path may
                            # legitimately append to it or restart the write.
                            # Reset for truncated append chunks too; otherwise
                            # an armed streak can block the clean restart.
                            last_write_target = None
                            consecutive_target_writes = 0
                        elif not action.get("append"):
                            target = _mutation_target_key(
                                {"arg": action.get("arg", "")}, working_dir)
                            if target == last_write_target:
                                consecutive_target_writes += 1
                            else:
                                last_write_target = target
                                consecutive_target_writes = 1
                    elif act in ("shell", "edit") and result["ok"]:
                        # Verification or a targeted fix breaks the rewrite
                        # streak; observations do not (the v6 Gemma loop
                        # interleaved tree/read between rewrites).
                        consecutive_target_writes = 0
                    if truncated_write and result["ok"]:
                        # The executor is stateless per step: without a resume
                        # anchor the model cannot know where the write stopped.
                        anchor = kept.splitlines()[-1][-80:]
                        recovery_arg = _target_recovery_arg(
                            operation_write_target, working_dir)
                        result["output"] += (
                            f" (truncated after {kept.count(chr(10))} lines; "
                            f"last written line: {anchor!r}; continue with "
                            f"append:true at {recovery_arg} starting after "
                            "that line)")
                    ok_str = "OK" if result["ok"] else "FAIL"
                    log(f"  -> {ok_str} ({time.time()-t_step:.1f}s): {result['output'][:80]}")

                    # Truncated-write outputs carry the resume anchor the next
                    # step navigates by — observation-class budget, not 100.
                    out_cap = (OBSERVE_STATE_CHARS
                               if act in OBSERVE_ACTIONS or truncated_write else 100)
                    step_entry = {
                        "action": act,
                        "arg": action.get("arg", ""),
                        "ok": result["ok"],
                        "output": result["output"][:out_cap]
                    }
                    if not result["ok"] and "error_type" in result:
                        step_entry["error_type"] = result["error_type"]
                    if act == "shell" and "timeout" in action:
                        step_entry["_timeout"] = action["timeout"]
                    if act == "write":
                        step_entry["_content"] = action.get("content", "")
                        if action.get("append"):
                            step_entry["_append"] = True
                        if truncated_write:
                            step_entry["_truncated_write"] = True
                    if act == "edit":
                        step_entry["_find"] = action.get("find", "")
                        step_entry["_replace"] = action.get("replace", "")
                    if act in ("write", "edit"):
                        target_step = {"arg": action.get("arg", "")}
                        if act == "write" and action.get("append"):
                            target_step["append"] = True
                        target = _mutation_target_key(target_step, working_dir)
                        if target is not None:
                            step_entry["_target"] = target
                            if act == "write" and truncated_write:
                                step_entry["_recovery_arg"] = (
                                    _target_recovery_arg(target, working_dir))
                    if act == "read":
                        step_entry["_read_key"] = (
                            action.get("arg", ""), _read_offset_limit(action)[0])
                        if result.get("continuation"):
                            step_entry["_continuation"] = result["continuation"]
                    state["last_steps"].append(step_entry)
                    state["all_steps"].append(dict(step_entry))
                    history.append({"event": "step", "task": i, "step": step, "action": action,
                                    "result": {"ok": result["ok"], "output": result["output"][:100]}})
                    step_log = {"event": "step", "task_index": i, "step": step,
                                "action": act, "arg": action.get("arg", "")[:120],
                                "ok": result["ok"], "error_type": result.get("error_type"),
                                "wall_s": round(time.time() - t_step, 2)}
                    if result.get("truncated"):
                        step_log["truncated"] = True
                    if truncated_write:
                        step_log["truncated_write"] = True
                    # Hash-linked read audit: which content, how much of it,
                    # and where the window ended.
                    if act == "read" and result.get("sha256"):
                        step_log["sha256"] = result["sha256"]
                        step_log["total_lines"] = result.get("total_lines")
                        step_log["total_bytes"] = result.get("total_bytes")
                        step_log["continuation"] = result.get("continuation")
                    _run_log(step_log)

                    if not result["ok"]:
                        etype = result.get("error_type", "unknown")
                        if (act == "shell" and etype in ("compile_error", "unknown")
                                and _expects_failure(task)):
                            log("  Expected failure observed; completing task with evidence")
                            step_entry["expected_failure"] = True
                            state["last_steps"][-1]["expected_failure"] = True
                            state["all_steps"][-1]["expected_failure"] = True
                            task_steps.append(step_entry)
                            task_done = True
                            break

                        if act == "shell" and etype == "compile_error":
                            repair = _try_compile_repair(
                                result["output"], working_dir, action.get("arg", "")
                            )
                            if repair:
                                # The deterministic source edit is a successful
                                # targeted fix, so it breaks an armed rewrite
                                # streak just like a model-selected edit.
                                consecutive_target_writes = 0
                                repair_step = {
                                    "action": "edit",
                                    "arg": repair[0],
                                    "ok": True,
                                    "output": repair[1],
                                    "deterministic_repair": True,
                                }
                                log(f"  Deterministic repair: {repair[1]} in {repair[0]}")
                                state["last_steps"].append(repair_step)
                                state["all_steps"].append(dict(repair_step))
                                task_steps.append(repair_step)
                                history.append({
                                    "event": "step", "task": i, "step": step,
                                    "action": repair_step,
                                    "result": {"ok": True, "output": repair[1]},
                                    "deterministic_repair": True,
                                })
                                _run_log({"event": "deterministic_repair",
                                          "kind": "compile_include",
                                          "file": repair[0],
                                          "description": repair[1]})

                                retry_result = execute(action, working_dir)
                                retry_step = {
                                    "action": "shell",
                                    "arg": action.get("arg", ""),
                                    "ok": retry_result["ok"],
                                    "output": retry_result["output"][:100],
                                    "deterministic_retry": True,
                                }
                                if not retry_result["ok"] and "error_type" in retry_result:
                                    retry_step["error_type"] = retry_result["error_type"]
                                state["last_steps"].append(retry_step)
                                state["all_steps"].append(dict(retry_step))
                                history.append({
                                    "event": "step", "task": i, "step": step,
                                    "action": {"action": "shell", "arg": action.get("arg", "")},
                                    "result": {
                                        "ok": retry_result["ok"],
                                        "output": retry_result["output"][:100],
                                    },
                                    "deterministic_retry": True,
                                })
                                _run_log({"event": "step", "task_index": i, "step": step,
                                          "action": "shell",
                                          "arg": action.get("arg", "")[:120],
                                          "ok": retry_result["ok"],
                                          "error_type": retry_result.get("error_type"),
                                          "deterministic_retry": True,
                                          "wall_s": round(time.time() - t_step, 2)})
                                if retry_result["ok"]:
                                    log(f"  -> OK deterministic retry: {retry_result['output'][:80]}")
                                    task_steps.append(retry_step)
                                    use_think = False
                                    reasoning_trigger = "executor"
                                    continue
                                log(f"  -> FAIL deterministic retry: {retry_result['output'][:80]}")
                                result = retry_result
                                step_entry = retry_step
                                etype = result.get("error_type", "unknown")

                        err_output = result['output'][:100]
                        hint = _RECOVERY_HINTS.get(etype)
                        if hint:
                            err_output = f"{err_output} → {hint}"
                            state["last_steps"][-1]["output"] = state["last_steps"][-1]["output"][:100] + f" → {hint}"
                            state["all_steps"][-1]["output"] = state["all_steps"][-1]["output"][:100] + f" → {hint}"
                        state["errors"].append(f"[{etype}] {act} {action.get('arg','')[:60]}: {err_output}")
                        use_think = etype not in _NO_THINK_ERRORS
                        reasoning_trigger = f"execution_error:{etype}"
                    else:
                        use_think = False
                        reasoning_trigger = "executor"
                        task_steps.append(step_entry)
                        if act == "edit":
                            last_successful_edit = (
                                action.get("arg", ""),
                                action.get("find", ""),
                                action.get("replace", ""),
                            )

                if task_done:
                    break  # break task_attempt loop — success

                # E11: try task-local replan before falling through to full replan
                if task_attempt < MAX_TASK_LOCAL_REPLANS:
                    saved_errors = list(state["errors"])
                    t_lr = time.time()
                    replacement = replan_task(task, state["errors"],
                                             state["completed_tasks"], state,
                                             goal_context,
                                             goal_context_chars=goal_context_chars)
                    lr_wall = time.time() - t_lr
                    if replacement:
                        log(f"  Task-local replan ({lr_wall:.1f}s): '{replacement[:60]}'")
                        _run_log({"event": "task_local_replan", "task_index": i,
                                  "original": task[:120], "replacement": replacement[:120],
                                  "ok": True, "llm_wall_s": round(lr_wall, 2)})
                        task = replacement
                        tasks[i] = replacement
                        state["errors"] = []
                        continue  # retry with replacement
                    else:
                        reject_reason = _last_task_replan_reject_reason or "unknown"
                        log(f"  Task-local replan failed ({lr_wall:.1f}s), will full replan.")
                        _run_log({"event": "task_local_replan", "task_index": i,
                                  "original": task[:120], "replacement": None,
                                  "ok": False, "llm_wall_s": round(lr_wall, 2),
                                  "reject_reason": reject_reason})
                        state["errors"] = saved_errors
                else:
                    # Replacement attempt also failed — merge original errors back
                    # so full replan sees both failure contexts
                    state["errors"] = saved_errors + state["errors"]
                # Fall through — task failed, no more local attempts
                break

            if task_done:
                unresolved = _unresolved_incomplete_writes(
                    state.get("all_steps", []), working_dir)
                pending_empty = state.get("pending_empty_writes", {})
                if unresolved or pending_empty:
                    restrictive = _restrictive_pending_empty(pending_empty)
                    if restrictive is not None:
                        incomplete_target, pending_info = restrictive
                        incomplete_name, recovery_arg, _ = _pending_empty_hint(
                            incomplete_target, pending_info)
                    elif unresolved:
                        incomplete_target, (_, incomplete_step) = max(
                            unresolved.items(), key=lambda item: item[1][0])
                        incomplete_name, recovery_arg = _incomplete_step_hint(
                            incomplete_target, incomplete_step)
                    else:
                        incomplete_target, pending_info = _next_pending_empty(
                            pending_empty)
                        incomplete_name, recovery_arg, _ = _pending_empty_hint(
                            incomplete_target, pending_info)
                    state["errors"].append(
                        f"[incomplete_write] {incomplete_name} at "
                        f"{recovery_arg}: completion refused")
                    log(f"  Task completion refused: {incomplete_name} is incomplete")
                    task_done = False

            if task_done:
                state["completed_tasks"].append(task)
                state["completed_step_groups"].append(task_steps)
                log(f"  Task complete. ({time.time()-t_task:.1f}s)")
                _run_log({"event": "task_complete", "task_index": i, "task": task,
                          "wall_s": round(time.time() - t_task, 2)})
            else:
                all_done = False
                log(f"  Task failed, will replan. ({time.time()-t_task:.1f}s)")
                _run_log({"event": "task_failed", "task_index": i, "task": task,
                          "wall_s": round(time.time() - t_task, 2)})
                break

        if all_done:
            wants_validation = _should_validate(replan, history, state, user_prompt)
            first_validation = state.get("validation_attempts", 0) == 0
            recheck_validation = (
                state.get("validation_recheck_needed")
                and state.get("validation_attempts", 0) < 2
                and _has_new_validation_evidence(state)
            )
            if wants_validation and (first_validation or recheck_validation):
                state["validated_once"] = True
                state["validation_attempts"] = state.get("validation_attempts", 0) + 1
                vresult = _validate_completion(user_prompt, state, working_dir)
                if vresult and vresult.get("valid") is False:
                    reason = vresult.get("reason", "validation failed")
                    missing = vresult.get("missing", [])
                    error_msg = f"[validation_failed] {reason}"
                    if missing:
                        error_msg += f" missing: {', '.join(missing)}"
                    state["errors"].append(error_msg)
                    state["validation_recheck_needed"] = True
                    state["validated_step_count"] = len(state.get("all_steps", []))
                    log(f"  Validation failed: {reason}")
                    _run_log({"event": "validation", "valid": False, "reason": reason,
                              "missing": missing,
                              "deterministic": bool(vresult.get("deterministic"))})
                    all_done = False
                    continue  # replan
                elif vresult is None and recheck_validation:
                    # A first optional validator failure remains fail-open, but
                    # once validation explicitly failed, an unavailable second
                    # verdict cannot erase that known failure.
                    log("  Validation recheck produced no verdict; failure remains pending.")
                    _run_log({"event": "validation", "valid": None,
                              "reason": "recheck produced no verdict",
                              "deterministic": False})
                else:
                    state["validation_recheck_needed"] = False
                    log(f"  Validation passed.")
                    _run_log({"event": "validation", "valid": True,
                              "deterministic": bool(vresult and vresult.get("deterministic"))})
            if state.get("validation_recheck_needed"):
                if state.get("validation_attempts", 0) >= 2:
                    reason = "validation remains failed after the maximum checks"
                else:
                    reason = ("completion after failed validation requires new "
                              "write, edit, or shell evidence")
                state["errors"].append(f"[validation_failed] {reason}")
                log(f"  Completion refused: {reason}")
                _run_log({"event": "validation_pending", "reason": reason})
                all_done = False
                continue
            total_wall = time.time() - t_run
            log(f"All tasks complete. ({total_wall:.1f}s total)")
            log(f"Output in: {working_dir}")
            _run_log({"event": "run_end", "status": "complete",
                      "replans": replan, "wall_s": round(total_wall, 2),
                      "completed_tasks": len(state["completed_tasks"]),
                      "steps": {"selected": state["selected_steps"],
                                "executed": state["executed_steps"],
                                "skipped": state["skipped_steps"]}})
            return {"status": "complete", "state": state, "log": history}

    total_wall = time.time() - t_run
    deterministic = _deterministic_check(user_prompt, state, working_dir)
    if deterministic is True and not state.get("validation_recheck_needed"):
        log(f"Deterministic reconciliation passed after exhaustion. ({total_wall:.1f}s total)")
        log(f"Output in: {working_dir}")
        _run_log({"event": "run_end", "status": "complete_deterministic_after_exhausted",
                  "replans": max_replans, "wall_s": round(total_wall, 2),
                  "completed_tasks": len(state["completed_tasks"]),
                  "steps": {"selected": state["selected_steps"],
                            "executed": state["executed_steps"],
                            "skipped": state["skipped_steps"]}})
        return {"status": "complete", "state": state, "log": history}

    log(f"Exhausted {max_replans} replan attempts. ({total_wall:.1f}s total)")
    log(f"Errors: {state['errors']}")
    log(f"Output in: {working_dir}")
    _run_log({"event": "run_end", "status": "exhausted",
              "replans": max_replans, "wall_s": round(total_wall, 2),
              "errors": state["errors"][-5:],
              "steps": {"selected": state["selected_steps"],
                        "executed": state["executed_steps"],
                        "skipped": state["skipped_steps"]}})
    return {"status": "exhausted", "state": state, "log": history}


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
        "--reasoning-policy", choices=REASONING_POLICIES,
        default=DEFAULT_REASONING_POLICY,
        help="Explicit-reasoning policy (default: %(default)s)",
    )
    parser.add_argument(
        "--max-replans", type=_positive_int, default=MAX_REPLANS,
        help="Maximum planning attempts, including the initial plan",
    )
    parser.add_argument(
        "--max-tasks", type=_positive_int, default=MAX_TASKS,
        help="Maximum tasks accepted from each plan",
    )
    parser.add_argument(
        "--max-steps", type=_positive_int, default=MAX_STEPS,
        help="Maximum executor steps per task attempt",
    )
    parser.add_argument(
        "--goal-context-chars", type=_positive_int, default=GOAL_CONTEXT_CHARS,
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
            Path(args.result_json).write_text(
                json.dumps(result, indent=2, default=str) + "\n"
            )
        except OSError as e:
            parser.error(f"cannot write --result-json: {e}")
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(_main())
