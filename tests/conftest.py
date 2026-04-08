"""Shared pytest fixtures and skip markers for agent tests."""
import sys
from pathlib import Path

import pytest

# Allow `from askme import ...` from any test file.
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def work_dir(tmp_path):
    return str(tmp_path)


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
        import os
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
        r = requests.get("https://openrouter.ai/api/v1/models",
                         headers={"Authorization": f"Bearer {key}"}, timeout=10)
        return r.status_code == 200
    except Exception:
        return False


skip_no_llm = pytest.mark.skipif(
    not _llm_available(), reason="llama-server not running on :8080")

skip_no_openrouter = pytest.mark.skipif(
    not _openrouter_available(), reason="OpenRouter API not available")
