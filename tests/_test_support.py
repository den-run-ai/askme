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
    resp.json.return_value = {"choices": [{"message": {"content": json.dumps(content)}}]}
    return resp


def mock_response_raw(text):
    """Create a mock response with raw text (for testing think-tag stripping etc)."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": text}}]}
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


def int_run(
    user_prompt,
    work_dir,
    max_replans=INT_MAX_REPLANS,
    max_tasks=INT_MAX_TASKS,
    max_steps=INT_MAX_STEPS,
):
    """Integration test agent loop — thin delegation to production _run_loop().

    int_run callers use max_replans to mean "replans after first plan",
    but _run_loop uses it as total plan attempts. Adjust by +1.
    """
    from askme import _run_loop

    return _run_loop(
        user_prompt, work_dir, max_replans=max_replans + 1, max_tasks=max_tasks, max_steps=max_steps
    )


def assert_file(path, content_contains=None):
    """Assert helper with clear messages."""
    p = Path(path)
    assert p.exists(), f"Expected file not found: {p}"
    if content_contains:
        text = p.read_text()
        assert content_contains.lower() in text.lower(), (
            f"Expected '{content_contains}' in {p}, got: {text[:200]}"
        )


def assert_executable_output(path, expected):
    """Verify a generated executable exists, runs, and prints expected output."""
    p = Path(path)
    assert p.exists(), f"Expected executable not found: {p}"
    assert_command_output([str(p)], p.parent, expected)


def assert_command_output(command, cwd, expected):
    """Run a deterministic postcondition command and verify its output."""
    import subprocess

    result = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"Postcondition command failed ({result.returncode}): {result.stderr[:200]}"
    )
    assert expected in result.stdout, (
        f"Expected '{expected}' in command output, got: {result.stdout[:200]}"
    )


def or_run(
    user_prompt,
    work_dir,
    max_replans=INT_MAX_REPLANS,
    max_tasks=INT_MAX_TASKS,
    max_steps=INT_MAX_STEPS,
):
    """Agent loop using the configured OpenRouter model and provider."""
    old_env = {
        "LLM_BACKEND": os.environ.get("LLM_BACKEND"),
        "OPENROUTER_MODEL": os.environ.get("OPENROUTER_MODEL"),
        "OPENROUTER_PROVIDER": os.environ.get("OPENROUTER_PROVIDER"),
        "OPENROUTER_ALLOW_FALLBACKS": os.environ.get("OPENROUTER_ALLOW_FALLBACKS"),
        "OPENROUTER_REQUIRE_PARAMETERS": os.environ.get("OPENROUTER_REQUIRE_PARAMETERS"),
    }
    model = old_env["OPENROUTER_MODEL"] or "google/gemma-4-26b-a4b-it"
    provider = (
        old_env["OPENROUTER_PROVIDER"] if old_env["OPENROUTER_PROVIDER"] is not None else "Parasail"
    )
    allow_fallbacks = (old_env["OPENROUTER_ALLOW_FALLBACKS"] or "1") == "1"
    require_parameters = (old_env["OPENROUTER_REQUIRE_PARAMETERS"] or "0") == "1"
    os.environ["LLM_BACKEND"] = "openrouter"
    os.environ["OPENROUTER_MODEL"] = model
    os.environ["OPENROUTER_PROVIDER"] = provider
    os.environ["OPENROUTER_ALLOW_FALLBACKS"] = "1" if allow_fallbacks else "0"
    os.environ["OPENROUTER_REQUIRE_PARAMETERS"] = "1" if require_parameters else "0"

    # Reload module-level config
    import askme

    old_config: dict[str, object] = {
        "LLM_BACKEND": askme.LLM_BACKEND,
        "API": askme.API,
        "MODEL": askme.MODEL,
        "OPENROUTER_MODEL": askme.OPENROUTER_MODEL,
        "OPENROUTER_PROVIDER": askme.OPENROUTER_PROVIDER,
        "OPENROUTER_ALLOW_FALLBACKS": askme.OPENROUTER_ALLOW_FALLBACKS,
        "OPENROUTER_REQUIRE_PARAMETERS": askme.OPENROUTER_REQUIRE_PARAMETERS,
    }
    askme.LLM_BACKEND = "openrouter"
    askme.API = "https://openrouter.ai/api/v1/chat/completions"
    askme.MODEL = model
    askme.OPENROUTER_MODEL = model
    askme.OPENROUTER_PROVIDER = provider
    askme.OPENROUTER_ALLOW_FALLBACKS = allow_fallbacks
    askme.OPENROUTER_REQUIRE_PARAMETERS = require_parameters
    askme.OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

    try:
        return int_run(user_prompt, work_dir, max_replans, max_tasks, max_steps)
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for config_key, config_value in old_config.items():
            setattr(askme, config_key, config_value)
