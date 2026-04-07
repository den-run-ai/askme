#!/usr/bin/env python3
"""Minimal self-contained agent. Takes a user prompt, plans, executes, replans on failure.
Requires: requests. Expects llama-server on localhost:8080."""
import sys, json, subprocess, requests, re, time, os
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

if LLM_BACKEND == "openrouter":
    API = "https://openrouter.ai/api/v1/chat/completions"
    MODEL = OPENROUTER_MODEL
else:
    API = os.environ.get("LLM_API_URL", "http://localhost:8080/v1/chat/completions")
    MODEL = os.environ.get("LLM_MODEL", "gemma-4-e4b")
MAX_REPLANS = 3
MAX_TASKS = 10
MAX_STEPS = 10
MAX_RESULT = 300  # chars kept from command output
MAX_STEP_HISTORY = 3  # sliding window of recent steps sent to executor

SYSTEM_PLAN = f"""You are a planner. Given a user request and current state, propose a list of tasks.
If a previous plan failed, redesign it based on what went wrong.
Prefer fewer tasks (1-3). Each task should be a complete goal, not a single command. Max {MAX_TASKS} tasks.
Keep descriptions short (under 15 words each).
Output ONLY valid JSON. No markdown, no explanation.
Format: {{"tasks": ["task1 description", "task2 description"]}}"""

SYSTEM_STEP = """You are a task executor. Output ONLY valid JSON. No markdown, no explanation.
Propose ONE action at a time. Use relative paths (e.g. main.c not /full/path/main.c).
CRITICAL RULES:
- If last_steps shows a write/shell with ok=true, that action SUCCEEDED. Emit {"action":"done"} immediately.
- If last_steps shows the same error 2+ times, emit {"action":"fail"}.
- completed_tasks are DONE — never redo their work.
Actions: shell, write, read, done, fail.
Format: {"action":"...","arg":"...","content":"...","reasoning":"max 10 words"}"""


MAX_LLM_RETRIES = 2


def ask_llm(messages, max_tokens=256, think=False):
    for attempt in range(MAX_LLM_RETRIES + 1):
        body = {
            "model": MODEL,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        if LLM_BACKEND == "openrouter":
            body["provider"] = {"order": ["Parasail"]}
        headers = {"Content-Type": "application/json"}
        if LLM_BACKEND == "openrouter" and OPENROUTER_API_KEY:
            headers["Authorization"] = f"Bearer {OPENROUTER_API_KEY}"
        resp = requests.post(API, json=body, headers=headers)
        rj = resp.json()
        # Log token usage if available
        usage = rj.get("usage", {})
        if usage:
            log(f"  tokens: prompt={usage.get('prompt_tokens',0)} completion={usage.get('completion_tokens',0)} total={usage.get('total_tokens',0)}")
        text = rj["choices"][0]["message"]["content"]
        # Strip <think>...</think> (closed) or <think>... (unclosed, truncated at max_tokens)
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        text = re.sub(r"<think>.*", "", text, flags=re.DOTALL).strip()
        # Strip markdown code fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        # Try to extract JSON object from anywhere in the text
        if not text.startswith("{") and "{" in text:
            text = text[text.index("{"):]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            if attempt < MAX_LLM_RETRIES:
                log(f"  [retry {attempt+1}] JSON parse failed, raw: {text[:120]}")
            else:
                raise


def get_plan(user_prompt, state):
    return ask_llm([
        {"role": "system", "content": SYSTEM_PLAN},
        {"role": "user", "content": f"REQUEST:\n{user_prompt}\n\nSTATE:\n{json.dumps(state)}"}
    ], max_tokens=512)


MAX_INPUT = 300  # max chars per field sent to executor

def get_step(task, state, goal="", step_num=0, max_steps=MAX_STEPS):
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
    goal_line = f"GOAL:\n{goal[:MAX_INPUT]}\n\n" if goal else ""
    user_msg = f"{goal_line}TASK:\n{task[:MAX_INPUT]}\n\nSTATE:\n{json.dumps(slim)}"
    # Use higher token budget for OpenRouter (faster model, needs room for write content + reasoning)
    step_tokens = 512 if LLM_BACKEND == "openrouter" else 256
    return ask_llm([
        {"role": "system", "content": SYSTEM_STEP},
        {"role": "user", "content": user_msg}
    ], max_tokens=step_tokens)


def execute(action, working_dir="."):
    act = action.get("action", "")
    if act == "shell":
        try:
            r = subprocess.run(
                action["arg"], shell=True, capture_output=True,
                text=True, timeout=30, cwd=working_dir
            )
            out = r.stdout[:MAX_RESULT] + r.stderr[-MAX_RESULT:]
            return {"ok": r.returncode == 0, "output": out.strip() or "(no output)"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": "TIMEOUT"}
    elif act == "write":
        try:
            p = Path(action["arg"])
            if not p.is_absolute():
                p = Path(working_dir) / p
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(action.get("content", ""))
            return {"ok": True, "output": f"Wrote {p.name}"}
        except Exception as e:
            return {"ok": False, "output": str(e)[:MAX_RESULT]}
    elif act == "read":
        try:
            p = Path(action["arg"])
            if not p.is_absolute():
                p = Path(working_dir) / p
            return {"ok": True, "output": p.read_text()[:MAX_RESULT]}
        except Exception as e:
            return {"ok": False, "output": str(e)[:MAX_RESULT]}
    elif act == "done":
        return {"ok": True, "output": "task_complete"}
    elif act == "fail":
        return {"ok": False, "output": action.get("reasoning", "failed")}
    return {"ok": False, "output": f"unknown action: {act}"}


def run(user_prompt):
    state = {"completed_tasks": [], "errors": []}
    t_run = time.time()
    log(f"Prompt: {user_prompt}")

    for replan in range(MAX_REPLANS):
        log("=" * 40)
        t_plan = time.time()
        log(f"Planning (attempt {replan + 1}/{MAX_REPLANS})...")
        plan = get_plan(user_prompt, state)
        state["errors"] = []  # reset errors each replan; planner already saw them
        tasks = plan.get("tasks", [])
        log(f"Plan ({time.time()-t_plan:.1f}s): {tasks}")

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
            for step in range(MAX_STEPS):
                t_step = time.time()
                try:
                    action = get_step(task, state, goal=user_prompt, step_num=step)
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
                act = action.get("action", "")
                log(f"  [{step + 1}] {act}: {action.get('arg', '')[:80]}")

                if act == "done":
                    task_done = True
                    break
                if act == "fail":
                    reason = action.get("reasoning", "no reason")
                    log(f"  FAIL ({time.time()-t_step:.1f}s): {reason}")
                    state["errors"].append(f"Task '{task}': {reason}")
                    break

                result = execute(action)
                ok_str = "OK" if result["ok"] else "FAIL"
                log(f"  -> {ok_str} ({time.time()-t_step:.1f}s): {result['output'][:80]}")

                state["last_steps"].append({
                    "action": act,
                    "arg": action.get("arg", ""),
                    "ok": result["ok"],
                    "output": result["output"][:100]
                })

                if not result["ok"]:
                    state["errors"].append(f"Step failed: {act} {action.get('arg','')}: {result['output'][:100]}")

            if task_done:
                state["completed_tasks"].append(task)
                log(f"  Task complete. ({time.time()-t_task:.1f}s)")
            else:
                all_done = False
                log(f"  Task failed, will replan. ({time.time()-t_task:.1f}s)")
                break

        if all_done:
            log(f"All tasks complete. ({time.time()-t_run:.1f}s total)")
            return

    log(f"Exhausted {MAX_REPLANS} replan attempts. ({time.time()-t_run:.1f}s total)")
    log(f"Errors: {state['errors']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 askme.py 'your request here'")
        sys.exit(1)
    run(sys.argv[1])
