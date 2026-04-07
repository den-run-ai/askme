#!/usr/bin/env python3
"""Minimal self-contained agent. Takes a user prompt, plans, executes, replans on failure.
Requires: requests. Expects llama-server on localhost:8080."""
import sys, json, subprocess, requests, re, time, os, tempfile, shutil
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


MAX_REPLANS = 3
MAX_TASKS = 10
MAX_STEPS = 10
MAX_RESULT = 300  # chars kept from command output
MAX_STEP_HISTORY = 3  # sliding window of recent steps sent to executor
PLANNER_MAX_TOKENS = 768  # 256 thinking + 512 output; shared budget on Parasail/bf16

SYSTEM_PLAN = f"""You are a planner. Given a user request and current state, propose a list of tasks.
If a previous plan failed, redesign it based on what went wrong.
Prefer fewer tasks (1-3). Each task should be a complete goal, not a single command. Max {MAX_TASKS} tasks.
Keep descriptions short (under 15 words each) but include key details:
- File content hints: which includes, defines, or imports are needed
- Use relative filenames (e.g. main.c not /full/path/main.c)
- Never create a task for work already in completed_tasks
POLICY RULES:
- Check state.environment.missing_tools — if a required tool is missing and policy.allow_system_installs is false, do NOT plan installation tasks. Instead plan a single task that fails with a prerequisite message listing what is missing.
- If policy.allow_system_installs is true, you may plan installation tasks using available package managers from state.environment.package_managers.
- Respect state.environment.platform — do not use Linux commands on macOS or vice versa.
Output ONLY valid JSON. No markdown, no explanation.
Format: {{"tasks": ["task1 description", "task2 description"]}}"""

SYSTEM_STEP = """You are a task executor. Output ONLY valid JSON. No markdown, no explanation.
Propose ONE action at a time. Use relative paths (e.g. main.c not /full/path/main.c).
CRITICAL RULES:
- Emit {"action":"done"} ONLY when the FULL task description is satisfied, not after a single successful step. Example: if the task is "create, compile, and run X", writing the file is not done — you must also compile and run.
- If last_steps shows the same error 2+ times, emit {"action":"fail"}.
- completed_tasks are DONE — never redo their work.
- If a required tool is in missing_tools and policy.allow_system_installs is false, emit {"action":"fail"} with reasoning explaining the missing prerequisite. Do NOT attempt to install software.
Actions: shell, write, read, done, fail.
Format: {"action":"...","arg":"...","content":"...","reasoning":"max 10 words"}"""


MAX_LLM_RETRIES = 2


def ask_llm(messages, max_tokens=256, think=False):
    for attempt in range(MAX_LLM_RETRIES + 1):
        # Determine thinking level: caller-requested or auto-escalate on retry
        if think:
            think_level = "high" if attempt >= 1 else "medium"
        elif attempt >= 1:
            think_level = "high" if attempt >= 2 else "medium"
        else:
            think_level = None

        body = {
            "model": MODEL,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        if LLM_BACKEND == "openrouter":
            body["provider"] = {"order": ["Parasail"]}
            if think_level:
                body["reasoning"] = {
                    "enabled": True,
                    "effort": think_level,
                }
                # Reasoning tokens count against max_tokens with Parasail provider,
                # despite OpenRouter docs claiming they're separate. Bump to compensate.
                body["max_tokens"] = max(max_tokens, 2048 if think_level == "high" else 1536)
        elif think_level:
            # Local llama-server: prepend <|think|> to system prompt, bump max_tokens
            msgs = list(messages)
            if msgs and msgs[0]["role"] == "system":
                msgs[0] = dict(msgs[0])
                msgs[0]["content"] = "<|think|>\n" + msgs[0]["content"]
            body["messages"] = msgs
            body["max_tokens"] = max(max_tokens, 768 if think_level == "high" else 512)

        headers = {"Content-Type": "application/json"}
        if LLM_BACKEND == "openrouter" and OPENROUTER_API_KEY:
            headers["Authorization"] = f"Bearer {OPENROUTER_API_KEY}"
        _restore_cache()
        resp = requests.post(API, json=body, headers=headers)
        rj = resp.json()
        # Handle API error responses
        if "error" in rj:
            log(f"  API error: {rj['error'].get('message', rj['error'])}")
            if attempt < MAX_LLM_RETRIES:
                continue
            raise KeyError(f"API error: {rj['error']}")
        # Log token usage if available
        usage = rj.get("usage", {})
        if usage:
            tok_msg = f"  tokens: prompt={usage.get('prompt_tokens',0)} completion={usage.get('completion_tokens',0)} total={usage.get('total_tokens',0)}"
            if think_level:
                tok_msg += f" thinking={think_level}"
            log(tok_msg)
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
            text = text[text.index("{"):]
        try:
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise json.JSONDecodeError("Expected JSON object, got " + type(parsed).__name__, text, 0)
            return parsed
        except json.JSONDecodeError:
            if attempt < MAX_LLM_RETRIES:
                think_str = f" thinking={think_level}" if think_level else ""
                log(f"  [retry {attempt+1}]{think_str} JSON parse failed, raw: {text[:120]}")
            else:
                raise


_KNOWN_ERROR_TYPES = {"timeout", "missing_tool", "permission_denied", "missing_file",
                      "compile_error", "stuck_loop", "unknown"}


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


def get_plan(user_prompt, state):
    # Include environment and policy in planner state
    plan_state = dict(state)
    if "environment" not in plan_state:
        plan_state["environment"] = {}
    if "policy" not in plan_state:
        plan_state["policy"] = get_policy()
    # Summarize errors for compact, typed diagnostics
    if plan_state.get("errors"):
        plan_state["errors"] = summarize_errors(plan_state["errors"])
    return ask_llm([
        {"role": "system", "content": SYSTEM_PLAN},
        {"role": "user", "content": f"REQUEST:\n{user_prompt}\n\nSTATE:\n{json.dumps(plan_state)}"}
    ], max_tokens=PLANNER_MAX_TOKENS, think=True)  # always think — replans need it more


MAX_INPUT = 300  # max chars per field sent to executor

def get_step(task, state, goal="", step_num=0, max_steps=MAX_STEPS, think=False):
    # Build slim step history from recent steps (current task + carryover from previous)
    steps = state.get("last_steps", [])[-MAX_STEP_HISTORY:]
    slim_steps = []
    for s in steps:
        # Use basename for file paths to avoid long tmp_path bloat
        arg = s.get("arg", "")
        if s["action"] in ("write", "read") and "/" in arg:
            arg = Path(arg).name
        else:
            arg = arg[-MAX_INPUT:]
        slim_steps.append({
            "action": s["action"], "arg": arg,
            "ok": s["ok"], "output": s.get("output", "")[:MAX_INPUT]
        })
    slim = {
        "task": state.get("current_task", task)[:MAX_INPUT],
        "task_index": state.get("task_index", ""),
        "step": f"{step_num+1}/{max_steps}",
        "last_steps": slim_steps,
    }
    # Include completed tasks so executor knows what's already done
    completed = state.get("completed_tasks", [])
    if completed:
        slim["completed_tasks"] = [t[:80] for t in completed[-3:]]
    # Include missing tools and policy so executor can fail fast on prerequisites
    env = state.get("environment", {})
    if env.get("missing_tools"):
        slim["missing_tools"] = env["missing_tools"]
    slim["policy"] = state.get("policy", get_policy())
    goal_line = f"GOAL:\n{goal[:MAX_INPUT]}\n\n" if goal else ""
    user_msg = f"{goal_line}TASK:\n{task[:MAX_INPUT]}\n\nSTATE:\n{json.dumps(slim)}"
    # Use higher token budget for OpenRouter (faster model, needs room for write content + reasoning)
    step_tokens = 512 if LLM_BACKEND == "openrouter" else 256
    return ask_llm([
        {"role": "system", "content": SYSTEM_STEP},
        {"role": "user", "content": user_msg}
    ], max_tokens=step_tokens, think=think)


def classify_error(output, action="shell"):
    """Classify an error output into a typed category for structured diagnostics."""
    out = output.lower()
    if "timeout" in out or output == "TIMEOUT":
        return "timeout"
    if "command not found" in out:
        return "missing_tool"
    if "permission denied" in out:
        return "permission_denied"
    if "no such file" in out or "no such file or directory" in out:
        return "missing_file"
    if "syntax error" in out or "error:" in out:
        return "compile_error"
    return "unknown"


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
                result["error_type"] = classify_error(result["output"], "shell")
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
            p.write_text(content)
            return {"ok": True, "output": f"Wrote {p.name}"}
        except Exception as e:
            out = str(e)[:MAX_RESULT]
            return {"ok": False, "output": out, "error_type": classify_error(out, "write")}
    elif act == "read":
        try:
            p = Path(action["arg"])
            if not p.is_absolute():
                p = Path(working_dir) / p
            return {"ok": True, "output": p.read_text()[:MAX_RESULT]}
        except Exception as e:
            out = str(e)[:MAX_RESULT]
            return {"ok": False, "output": out, "error_type": classify_error(out, "read")}
    elif act == "done":
        return {"ok": True, "output": "task_complete"}
    elif act == "fail":
        return {"ok": False, "output": action.get("reasoning", "failed")}
    return {"ok": False, "output": f"unknown action: {act}"}


def run(user_prompt, working_dir=None):
    # Create isolated temp directory per run unless caller provides one
    if working_dir is None:
        working_dir = tempfile.mkdtemp(prefix="nanagent_")
    state = {"completed_tasks": [], "errors": []}
    t_run = time.time()
    log(f"Prompt: {user_prompt}")
    log(f"Working directory: {working_dir}")
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

    for replan in range(MAX_REPLANS):
        log("=" * 40)
        t_plan = time.time()
        log(f"Planning (attempt {replan + 1}/{MAX_REPLANS})...")
        plan = get_plan(user_prompt, state)
        state["errors"] = []  # reset errors each replan; planner already saw them
        tasks = plan.get("tasks", [])
        plan_wall = time.time() - t_plan
        log(f"Plan ({plan_wall:.1f}s, planner_wall_time={plan_wall:.1f}s): {tasks}")

        all_done = True
        for i, task in enumerate(tasks):
            state["current_task"] = task
            state["task_index"] = f"{i + 1}/{len(tasks)}"
            # Carry over last step from previous task so executor has cross-task context
            prev_last = state["last_steps"][-1:] if state.get("last_steps") else []
            state["last_steps"] = prev_last
            t_task = time.time()
            log(f"--- Task {i + 1}/{len(tasks)}: {task} ---")

            task_done = False
            use_think = False  # enable thinking after failed step execution
            dup_skip_count = 0  # consecutive duplicate write skips
            for step in range(MAX_STEPS):
                t_step = time.time()
                try:
                    action = get_step(task, state, goal=user_prompt, step_num=step, think=use_think)
                except (json.JSONDecodeError, KeyError):
                    # Auto-done: if LLM can't produce valid JSON after a successful step,
                    # treat it as implicit task completion (common with small LLMs)
                    last = state["last_steps"][-1:] if state["last_steps"] else []
                    if last and last[0].get("ok"):
                        log(f"  [{step + 1}] auto-done (LLM parse error after success, {time.time()-t_step:.1f}s)")
                        task_done = True
                        break
                    log(f"  [{step + 1}] LLM parse error ({time.time()-t_step:.1f}s)")
                    state["errors"].append(f"LLM parse error on task '{task}'")
                    break
                # Normalize None → "" for optional string fields (models emit "arg": null)
                for _k in ("arg", "content", "reasoning"):
                    if action.get(_k) is None:
                        action[_k] = ""
                act = action.get("action", "")
                log(f"  [{step + 1}] {act}: {action['arg'][:80]}")

                if act == "done":
                    task_done = True
                    break
                if act == "fail":
                    reason = action.get("reasoning", "no reason")
                    log(f"  FAIL ({time.time()-t_step:.1f}s): {reason}")
                    state["errors"].append(f"Task '{task}': {reason}")
                    break

                # Duplicate action guard — per-action-type loop detection
                last = state["last_steps"][-1:] if state["last_steps"] else []
                if last and last[0]["action"] == act:
                    prev = last[0]
                    if act == "write" and prev.get("arg", "") == action.get("arg", ""):
                        if prev.get("ok") and prev.get("_content", "") == action.get("content", ""):
                            dup_skip_count += 1
                            log(f"  [{step + 1}] skip (duplicate write, same content)")
                            if dup_skip_count >= 2:
                                # Break livelock: inject synthetic step so model sees new state
                                state["last_steps"].append({
                                    "action": "write", "arg": action.get("arg", ""),
                                    "ok": True,
                                    "output": f"Already written (skipped {dup_skip_count}x). Choose a different action or emit done."
                                })
                                use_think = True
                            continue
                    elif act == "shell" and prev.get("arg", "") == action.get("arg", ""):
                        if prev.get("ok"):
                            log(f"  [{step + 1}] auto-done (duplicate successful shell)")
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
                            break

                dup_skip_count = 0  # reset on any non-skipped action
                result = execute(action, working_dir)
                ok_str = "OK" if result["ok"] else "FAIL"
                log(f"  -> {ok_str} ({time.time()-t_step:.1f}s): {result['output'][:80]}")

                step_entry = {
                    "action": act,
                    "arg": action.get("arg", ""),
                    "ok": result["ok"],
                    "output": result["output"][:100]
                }
                if not result["ok"] and "error_type" in result:
                    step_entry["error_type"] = result["error_type"]
                if act == "shell" and "timeout" in action:
                    step_entry["_timeout"] = action["timeout"]
                if act == "write":
                    step_entry["_content"] = action.get("content", "")
                state["last_steps"].append(step_entry)

                if not result["ok"]:
                    etype = result.get("error_type", "unknown")
                    state["errors"].append(f"[{etype}] {act} {action.get('arg','')[:60]}: {result['output'][:100]}")
                    use_think = True  # think harder on next step after failure
                else:
                    use_think = False

            if task_done:
                state["completed_tasks"].append(task)
                log(f"  Task complete. ({time.time()-t_task:.1f}s)")
            else:
                all_done = False
                log(f"  Task failed, will replan. ({time.time()-t_task:.1f}s)")
                break

        if all_done:
            log(f"All tasks complete. ({time.time()-t_run:.1f}s total)")
            log(f"Output in: {working_dir}")
            return True

    log(f"Exhausted {MAX_REPLANS} replan attempts. ({time.time()-t_run:.1f}s total)")
    log(f"Errors: {state['errors']}")
    log(f"Output in: {working_dir}")
    return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 askme.py 'your request here'")
        sys.exit(1)
    success = run(sys.argv[1])
    sys.exit(0 if success else 1)
