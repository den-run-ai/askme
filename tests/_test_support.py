"""Shared mock helpers and integration test runners for agent tests."""
import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock


def mock_response(content):
    """Create a mock requests.post response returning content as LLM output."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(content)}}]
    }
    return resp


def mock_response_raw(text):
    """Create a mock response with raw text (for testing think-tag stripping etc)."""
    resp = MagicMock()
    resp.status_code = 200
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
    """Integration test agent loop — thin delegation to production _run_loop().

    int_run callers use max_replans to mean "replans after first plan",
    but _run_loop uses it as total plan attempts. Adjust by +1.
    """
    from askme import _run_loop
    return _run_loop(user_prompt, work_dir, max_replans=max_replans + 1,
                     max_tasks=max_tasks, max_steps=max_steps)


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
