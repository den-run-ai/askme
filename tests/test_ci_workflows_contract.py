"""Contract guards for the GitHub Actions workflows.

String-level assertions (no YAML dependency) in the same spirit as
test_talk_deck_contract.py: pin the properties that keep CI safe —
the unit matrix stays credential-free, and the paid LLM workflow always
preflights, gates, and stays opt-in for pull requests.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
LLM_WORKFLOW = ROOT / ".github" / "workflows" / "llm.yml"


def test_unit_workflow_stays_hermetic():
    """The unit matrix must never see an OpenRouter credential. With a key in
    scope, the backend-gated OpenRouter suites would stop auto-skipping and
    spend credits on every push and PR, across every Python in the matrix."""
    text = UNIT_WORKFLOW.read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY" not in text
    assert "environment:" not in text


def test_llm_workflow_uses_the_openrouter_environment():
    """Both paid jobs must bind the 'Openrouter' deployment environment and
    accept the key from either an environment secret or variable — the key
    is currently stored as a variable (see the environment settings)."""
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    assert text.count("environment: Openrouter") == 2
    assert text.count(
        "${{ secrets.OPENROUTER_API_KEY || vars.OPENROUTER_API_KEY }}") == 2


def test_llm_workflow_preflights_before_spending():
    """A missing/invalid key must fail loudly, not silently skip every test
    (conftest's skip markers would otherwise turn a bad credential into a
    green run)."""
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    assert text.count("ci_llm_gate.py preflight") == 2  # once per paid job
    first_preflight = text.index("ci_llm_gate.py preflight")
    assert first_preflight < text.index(
        "python -m pytest tests/test_agent_integration.py")
    assert first_preflight < text.index("python tests/bench_harness.py")


def test_llm_workflow_guards_against_silent_skips():
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    # pytest reports skip reasons, and the smoke job asserts the agent
    # actually logged run events.
    assert "-rs" in text
    assert "test -s llm-logs/smoke.jsonl" in text


def test_llm_workflow_gates_bench_results():
    """bench_harness never exits nonzero on test failures; the gate script
    is what turns cell failures (or missing cells) into a red job."""
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    assert "ci_llm_gate.py report" in text
    assert "--expect-cells" in text
    assert "--markdown-out" in text


def test_llm_workflow_is_opt_in_for_pull_requests():
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    guard = "contains(github.event.pull_request.labels.*.name, 'llm-tests')"
    assert text.count(guard) == 2  # both paid jobs carry the label gate
    assert text.count("github.event_name != 'pull_request'") == 2


def test_llm_workflow_bounds_spend():
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "schedule:" in text
    assert text.count("timeout-minutes:") == 2
    assert "concurrency:" in text
    assert "cancel-in-progress: true" in text
    assert "permissions:" in text
    assert "contents: read" in text
