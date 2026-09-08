"""Keep current runtime transport guidance distinct from dated evidence."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _current_transport_section(path, heading):
    document = (ROOT / path).read_text(encoding="utf-8")
    section = document.split(heading + "\n", 1)[1].split("\n## ", 1)[0]
    return " ".join(section.lower().split())


@pytest.mark.parametrize(
    ("path", "heading"),
    [
        ("docs/ARCHITECTURE.md", "## Current response transports"),
        ("docs/gemma4-setup.md", "## Current AskMe transport"),
    ],
)
def test_current_transport_guidance_keeps_all_three_boundaries(path, heading):
    section = _current_transport_section(path, heading)
    assert 'expect="action"' in section
    assert "single native tool call" in section
    assert 'tool_choice: "auto"' in section
    for response in ("plan", "task_replan", "validation"):
        assert f"`{response}`" in section
    assert "json text" in section
    assert "trusted injected clients" in section
    assert "finish_reason=length" in section
    assert "https://github.com/den-run-ai/askme/issues/94" in section


def test_setup_preserves_dated_upstream_status_without_deferring_native_adoption():
    document = (ROOT / "docs/gemma4-setup.md").read_text(encoding="utf-8")
    assert "dated reference snapshots, not a fresh upstream-status audit" in document
    assert "Relevant only if AskMe adopts native tool calls" not in document
    assert "Gates for ever adopting native tool calls" not in document


def test_architecture_does_not_present_historical_salvage_as_live_behavior():
    document = (ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    history = document.split("### Historical interface revisions\n", 1)[1]
    assert "`<<<CONTENT`" in history  # Retain the old interface's attribution.
    assert "Interface revision 6 removed that wire path" in history
    assert "Interface revision 3 (issue #15) adds a sentinel" not in document
    assert "_repair_json` salvages truncated JSON without retrying" not in document


def test_setup_and_completion_docs_preserve_the_remaining_coupling_boundary():
    document = (ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    core = " ".join(
        document.split("## Core Files\n", 1)[1].split("**Key functions:**", 1)[0].split()
    )
    for stage in (
        "_configure_request",
        "_bind_dependencies",
        "_configure_policies",
        "_freeze_call_contract",
    ):
        assert f"`{stage}`" in core
    assert "`CompletionPolicy.from_context(...)`" in core
    assert "compatibility adapter remains" in core
    assert "`askme.CompletionPolicy(controller)`" in core
    assert "`StepPolicy` and `WriteObligations` remain controller-bound" in core
