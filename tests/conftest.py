"""Shared pytest fixtures and skip markers for agent tests."""

import os
import sys
from pathlib import Path

import pytest

# Allow `from askme import ...` from any test file.
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def work_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture(autouse=True)
def disable_validation():
    """Disable final validation in all unit tests by default.
    TestFinalValidation tests explicitly re-enable it."""
    import askme

    old = askme.FINAL_VALIDATE
    askme.FINAL_VALIDATE = "0"
    yield
    askme.FINAL_VALIDATE = old


# --- Availability probes (evaluated once at collection time) ---


def _llm_available():
    try:
        import requests

        r = requests.get("http://localhost:8080/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _openrouter_available():
    """Check if OpenRouter API is accessible with a valid key."""
    try:
        import requests

        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
        key = os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            return False
        r = requests.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


live_llm_enabled = os.environ.get("ASKME_RUN_LIVE_LLM_TESTS") == "1"

skip_no_llm = pytest.mark.skipif(
    not live_llm_enabled or not _llm_available(),
    reason="live LLM tests not enabled or llama-server not running on :8080",
)

skip_no_openrouter = pytest.mark.skipif(
    not live_llm_enabled or not _openrouter_available(),
    reason="live LLM tests not enabled or OpenRouter API not available",
)
