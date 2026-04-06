#!/usr/bin/env python3
"""Minimal self-contained agent. Takes a user prompt, plans, executes, replans on failure.
Requires: requests. Expects llama-server on localhost:8080."""
import sys, json, subprocess, requests, re, time
from pathlib import Path


def log(msg):
    """Timestamped print for real-time monitoring."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

API = "http://localhost:8080/v1/chat/completions"
MAX_REPLANS = 3
MAX_TASKS = 10
MAX_STEPS = 10
MAX_RESULT = 300  # chars kept from command output
MAX_STEP_HISTORY = 3  # sliding window of recent steps sent to executor

SYSTEM_PLAN = f"""You are a planner. Given a user request and current state, propose a list of tasks.
If a previous plan failed, redesign it based on what went wrong.
Max {MAX_TASKS} tasks. Keep descriptions short (under 15 words each).
Output ONLY valid JSON. No markdown, no explanation.
Format: {{"tasks": ["task1 description", "task2 description"]}}"""

SYSTEM_STEP = """You are a task executor. Output ONLY valid JSON. No markdown, no explanation.
Propose ONE action at a time.
Actions: shell (run a shell command), write (create/edit file), read (read file), done (task complete), fail (cannot proceed).
Format: {"action":"shell|write|read|done|fail","arg":"command or filepath","content":"file content if write","reasoning":"1 sentence why"}"""


MAX_LLM_RETRIES = 2


def ask_llm(messages, max_tokens=256, think=False):
    for attempt in range(MAX_LLM_RETRIES + 1):
        body = {
            "model": "gemma-4-e4b",
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        resp = requests.post(API, json=body)
        text = resp.json()["choices"][0]["message"]["content"]
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


def get_step(task, state, goal=""):
    slim = {
        "task": state.get("current_task", task),
        "task_index": state.get("task_index", ""),
        "last_steps": state.get("last_steps", [])[-MAX_STEP_HISTORY:],
    }
    goal_line = f"GOAL:\n{goal}\n\n" if goal else ""
    return ask_llm([
        {"role": "system", "content": SYSTEM_STEP},
        {"role": "user", "content": f"{goal_line}TASK:\n{task}\n\nSTATE:\n{json.dumps(slim)}"}
    ])


def execute(action, working_dir="."):
    act = action.get("action", "")
    if act == "shell":
        try:
            r = subprocess.run(
                action["arg"], shell=True, capture_output=True,
                text=True, timeout=30, cwd=working_dir
            )
            out = (r.stdout + r.stderr)[:MAX_RESULT]
            return {"ok": r.returncode == 0, "output": out or "(no output)"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": "TIMEOUT"}
    elif act == "write":
        try:
            p = Path(action["arg"])
            if not p.is_absolute():
                p = Path(working_dir) / p
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(action.get("content", ""))
            return {"ok": True, "output": f"Wrote {p}"}
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
            state["last_steps"] = []
            t_task = time.time()
            log(f"--- Task {i + 1}/{len(tasks)}: {task} ---")

            task_done = False
            for step in range(MAX_STEPS):
                t_step = time.time()
                action = get_step(task, state, goal=user_prompt)
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
