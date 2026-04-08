"""Shared mock helpers and integration test runners for agent tests."""
import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock


def mock_response(content):
    """Create a mock requests.post response returning content as LLM output."""
    resp = MagicMock()
    resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(content)}}]
    }
    return resp


def mock_response_raw(text):
    """Create a mock response with raw text (for testing think-tag stripping etc)."""
    resp = MagicMock()
    resp.json.return_value = {
        "choices": [{"message": {"content": text}}]
    }
    return resp


# --- Integration test limits ---

# Default tight limits for integration tests.
# INT_MAX_STEPS must be > number of real actions so the LLM has room to emit "done".
# Example: write + compile + run = 3 real actions, needs step 4 for "done".
INT_MAX_REPLANS = 1
INT_MAX_TASKS = 3
INT_MAX_STEPS = 5

# Medium tests: more steps for error recovery within a task (no replans expected)
MED_MAX_REPLANS = 1
MED_MAX_TASKS = 3
MED_MAX_STEPS = 8

# Hard tests: allow replans, more steps and tasks for complex recovery
HARD_MAX_REPLANS = 2
HARD_MAX_TASKS = 5
HARD_MAX_STEPS = 8


def log(msg):
    """Timestamped print that flushes immediately for real-time monitoring."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def int_run(user_prompt, work_dir, max_replans=INT_MAX_REPLANS,
            max_tasks=INT_MAX_TASKS, max_steps=INT_MAX_STEPS):
    """Minimal agent loop with configurable limits and live progress output."""
    from askme import get_plan, get_step, execute, _warm_cache
    state = {"completed_tasks": [], "errors": [], "working_dir": work_dir}
    history = []

    log(f"PROMPT: {user_prompt}")
    log(f"WORKDIR: {work_dir}")
    log(f"LIMITS: replans={max_replans} tasks={max_tasks} steps={max_steps}")
    _warm_cache()

    for replan in range(max_replans + 1):
        log(f"PLAN attempt {replan + 1}/{max_replans + 1} ...")
        t0 = time.time()
        plan = get_plan(user_prompt, state)
        tasks = plan.get("tasks", [])[:max_tasks]
        log(f"PLAN got {len(tasks)} tasks in {time.time()-t0:.1f}s: {tasks}")
        history.append({"event": "plan", "replan": replan, "tasks": tasks})

        all_done = True
        for i, task in enumerate(tasks):
            state["current_task"] = task
            state["task_index"] = f"{i + 1}/{len(tasks)}"
            # Carry over last step from previous task for cross-task context
            prev_last = state["last_steps"][-1:] if state.get("last_steps") else []
            state["last_steps"] = prev_last
            log(f"TASK {i+1}/{len(tasks)}: {task}")

            task_done = False
            use_think = False  # enable thinking after failed step execution
            dup_skip_count = 0  # consecutive duplicate write skips
            for step in range(max_steps):
                # Debug: show slim state sent to LLM
                recent = state["last_steps"][-3:]
                slim_debug = [{"action": s["action"], "ok": s["ok"], "output": s.get("output","")[:60]} for s in recent]
                log(f"  STEP {step+1}/{max_steps} state={json.dumps(slim_debug)}{' [think]' if use_think else ''}")
                t0 = time.time()
                try:
                    action = get_step(task, state, goal=user_prompt, step_num=step, max_steps=max_steps, think=use_think)
                except (json.JSONDecodeError, KeyError, Exception) as e:
                    elapsed = time.time() - t0
                    log(f"  STEP {step+1} LLM error ({elapsed:.1f}s): {e}")
                    state["errors"].append(f"[unknown] LLM parse error on task '{task}': {str(e)[:100]}")
                    break
                elapsed = time.time() - t0
                act = action.get("action", "")
                arg = (action.get("arg") or "")[:80]
                reason = (action.get("reasoning") or "")[:60]
                log(f"  STEP {step+1} <- {act} {arg} ({reason}) [{elapsed:.1f}s]")
                history.append({"event": "step", "task": i, "step": step, "action": action})

                if act == "done":
                    log(f"  TASK {i+1} DONE")
                    task_done = True
                    break
                if act == "fail":
                    log(f"  TASK {i+1} FAIL: {reason}")
                    state["errors"].append(f"Task '{task}': {action.get('reasoning', '')}")
                    break

                # Duplicate action guard — per-action-type loop detection
                last = state["last_steps"][-1:] if state["last_steps"] else []
                if last and last[0]["action"] == act:
                    prev = last[0]
                    if act in ("write", "edit") and prev.get("arg", "") == action.get("arg", ""):
                        is_dup = False
                        if act == "write" and prev.get("ok") and prev.get("_content", "") == action.get("content", ""):
                            is_dup = True
                        elif act == "edit" and prev.get("ok") and prev.get("_find", "") == action.get("find", "") and prev.get("_replace", "") == action.get("replace", ""):
                            is_dup = True
                        if is_dup:
                            dup_skip_count += 1
                            log(f"  STEP {step+1} skip (duplicate {act}, same content)")
                            if dup_skip_count >= 2:
                                state["last_steps"].append({
                                    "action": act, "arg": action.get("arg", ""),
                                    "ok": True,
                                    "output": f"Already applied (skipped {dup_skip_count}x). Choose a different action or emit done."
                                })
                                use_think = True
                            continue
                    elif act == "shell" and prev.get("arg", "") == action.get("arg", ""):
                        if prev.get("ok"):
                            log(f"  STEP {step+1} auto-done (duplicate successful shell)")
                            task_done = True
                            break
                        else:
                            log(f"  STEP {step+1} auto-fail (same shell failed twice)")
                            state["errors"].append(f"Stuck: {act} {action.get('arg','')[:60]} failed twice")
                            break

                dup_skip_count = 0  # reset on any non-skipped action
                try:
                    result = execute(action, work_dir)
                except Exception as e:
                    result = {"ok": False, "output": f"execute error: {str(e)[:100]}"}
                ok_str = "OK" if result["ok"] else "FAIL"
                log(f"  EXEC -> {ok_str}: {result['output'][:80]}")

                step_entry = {
                    "action": act, "arg": action.get("arg", ""),
                    "ok": result["ok"], "output": result["output"][:200]
                }
                if act == "write":
                    step_entry["_content"] = action.get("content", "")
                if act == "edit":
                    step_entry["_find"] = action.get("find", "")
                    step_entry["_replace"] = action.get("replace", "")
                state["last_steps"].append(step_entry)
                if not result["ok"]:
                    state["errors"].append(f"{act} failed: {result['output'][:100]}")
                    use_think = True  # think harder on next step after failure
                else:
                    use_think = False

            if task_done:
                state["completed_tasks"].append(task)
            else:
                all_done = False
                log(f"REPLAN needed (task {i+1} failed)")
                break

        if all_done:
            log(f"ALL DONE — completed: {state['completed_tasks']}")
            return {"status": "complete", "state": state, "log": history}

    log(f"EXHAUSTED — errors: {state['errors']}")
    return {"status": "exhausted", "state": state, "log": history}


def assert_file(path, content_contains=None):
    """Assert helper with clear messages."""
    p = Path(path)
    assert p.exists(), f"Expected file not found: {p}"
    if content_contains:
        text = p.read_text()
        assert content_contains.lower() in text.lower(), \
            f"Expected '{content_contains}' in {p}, got: {text[:200]}"


def or_run(user_prompt, work_dir, max_replans=INT_MAX_REPLANS,
           max_tasks=INT_MAX_TASKS, max_steps=INT_MAX_STEPS):
    """Agent loop using OpenRouter backend (gemma-4-26b-a4b via Parasail)."""
    # Temporarily switch backend to openrouter
    old_backend = os.environ.get("LLM_BACKEND", "")
    old_model = os.environ.get("OPENROUTER_MODEL", "")
    os.environ["LLM_BACKEND"] = "openrouter"
    os.environ["OPENROUTER_MODEL"] = "google/gemma-4-26b-a4b-it"

    # Reload module-level config
    import askme
    askme.LLM_BACKEND = "openrouter"
    askme.API = "https://openrouter.ai/api/v1/chat/completions"
    askme.MODEL = "google/gemma-4-26b-a4b-it"
    askme.OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

    try:
        return int_run(user_prompt, work_dir, max_replans, max_tasks, max_steps)
    finally:
        # Restore
        if old_backend:
            os.environ["LLM_BACKEND"] = old_backend
        else:
            os.environ.pop("LLM_BACKEND", None)
        if old_model:
            os.environ["OPENROUTER_MODEL"] = old_model
        else:
            os.environ.pop("OPENROUTER_MODEL", None)
        askme.LLM_BACKEND = old_backend or "local"
        askme.API = "http://localhost:8080/v1/chat/completions"
        askme.MODEL = "gemma-4-e4b"
