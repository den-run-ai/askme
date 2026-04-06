#!/usr/bin/env python3
"""Minimal local agent loop. Requires: requests."""
import json, subprocess, requests, re, time
from pathlib import Path

DIR = Path(".")
STATE = DIR / "state.json"
PLAN = DIR / "plan.json"
LOG = DIR / "log.jsonl"
API = "http://localhost:8080/v1/chat/completions"
MAX_RESULT = 300  # chars kept from command output

SYSTEM = """You are a task executor. Output ONLY valid JSON.
Actions: shell (run command), write (create/edit file), read (read file), done (task complete), fail (cannot proceed).
Format: {"action":"...","arg":"...","content":"...","reasoning":"..."}"""

DEBUG = True

def debug_print(msg):
    if DEBUG:
        print(msg)

def load(path):
    return json.loads(path.read_text())


def save(path, data):
    path.write_text(json.dumps(data, indent=2))


def ask_llm(state, plan):
    resp = requests.post(API, json={
        "model": "gemma-4-e4b",
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"STATE:\n{json.dumps(state)}\n\nPLAN:\n{json.dumps(plan)}"}
        ],
        "temperature": 0.1,
        "max_tokens": 256
    })
    text = resp.json()["choices"][0]["message"]["content"]
    print(text)
    # Strip <think>...</think> if present (Qwen thinking mode)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return json.loads(text)


def execute(action, working_dir):
    act = action["action"]
    if act == "shell":
        try:
            r = subprocess.run(
                action["arg"], shell=True, capture_output=True,
                text=True, timeout=30, cwd=working_dir
            )
            out = (r.stdout + r.stderr)[:MAX_RESULT]
            return {"ok": r.returncode == 0, "output": out}
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
            content = Path(action["arg"]).read_text()[:MAX_RESULT]
            return {"ok": True, "output": content}
        except Exception as e:
            return {"ok": False, "output": str(e)[:MAX_RESULT]}
    elif act == "done":
        return {"ok": True, "output": "step_complete"}
    elif act == "fail":
        return {"ok": False, "output": action.get("reasoning", "failed")}
    return {"ok": False, "output": "unknown action"}


def advance_state(state, plan, result):
    """Move to next step after completion."""
    if result["output"] == "step_complete":
        for s in plan["steps"]:
            if s["id"] == state["step"]:
                s["done"] = True
                debug_print("steps done")
                break
        state["step"] += 1
        if state["step"] > state["total_steps"]:
            state["status"] = "complete"
            debug_print("total steps complete")
        else:
            next_step = next(
                (s for s in plan["steps"] if s["id"] == state["step"]), None
            )
            state["current_task"] = next_step["task"] if next_step else "done"
            debug_print("current task done, no more steps")
    state["last_result"] = result["output"][:200]
    return state, plan


def run():
    state, plan = load(STATE), load(PLAN)
    max_iterations = state["total_steps"] * 3  # safety limit

    for i in range(max_iterations):
        if state["status"] in ("complete", "failed"):
            debug_print(f"breaking iterations with state {state["status"]}")
            break
        print(f"\n--- Step {state['step']}: {state['current_task']} ---")

        action = ask_llm(state, plan)
        print(f"Action: {action['action']} {action.get('arg', '')}")

        # Log everything
        with open(LOG, "a") as f:
            f.write(json.dumps({"iter": i, "action": action}) + "\n")

        result = execute(action, state.get("working_dir", "."))
        print(f"Result: {'OK' if result['ok'] else 'FAIL'} - {result['output'][:80]}")

        state, plan = advance_state(state, plan, result)
        save(STATE, state)
        save(PLAN, plan)

    print(f"\nAgent finished: {state['status']}")


if __name__ == "__main__":
    DIR.mkdir(exist_ok=True)
    run()
