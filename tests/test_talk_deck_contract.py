"""Regression guard for the Berkeley talk's reviewer-approved narrative."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TALK = ROOT / "talks" / "berkeley-agentic-ai-summit-2026"
SLIDES = TALK / "slides.md"
SPEC = TALK / "DECK_SPEC.md"


def _rendered_slide_sources(text):
    # Marp source begins with one frontmatter block, followed by seven slides.
    parts = re.split(r"(?m)^---\s*$", text)
    return [part for part in parts[2:] if part.strip()]


def test_deck_contract_guards_identity_arc_and_model_rows():
    text = SLIDES.read_text(encoding="utf-8")
    slides = _rendered_slide_sources(text)

    assert len(slides) == 7

    title = slides[0]
    assert "Are Small LLMs Ready for Coding Agents?" in title
    assert "Denis Akhiyarov" in title
    assert "Sr Staff Research Scientist at ServiceNow" in title
    assert 'href="https://x.com/den-run-ai"' in title
    assert "trace-table" not in title

    bridge = slides[1]
    bridge_upper = bridge.upper()
    assert "CONTROLLABLE SMALL LLMS" in bridge_upper
    assert "ASKME LOOP" in bridge_upper
    assert "ACCEPTED FULL WORKFLOW" in bridge_upper
    for stale_label in ("Pi", "Oh My Pi", "OpenHands", "Omnigent", "Databricks"):
        assert stale_label not in bridge
    assert "cloud" not in bridge.lower()
    assert "enterprise" not in bridge.lower()

    comparison = slides[4]
    for model in (
        "Gemma 4 26B A4B",
        "Gemma 4 31B",
        "Qwen3.6-27B",
        "Qwen3.6-35B-A3B",
    ):
        assert model in comparison
    assert "8 / 8" in comparison
    assert "7 / 8" in comparison
    assert "n=1/cell" in comparison
    assert "descriptive only" in comparison

    conclusion = slides[6]
    assert "Answer to the title" in conclusion
    assert "Promising—with a tight harness." in conclusion

    assert "NanAgent" not in text


def test_deck_contract_guards_notes_and_review_spec():
    text = SLIDES.read_text(encoding="utf-8")
    note_blocks = re.findall(
        r"<!--\s*\nSpeaker notes[^\n]*:\s*\n(.*?)\n-->",
        text,
        flags=re.DOTALL,
    )
    assert len(note_blocks) == 7
    assert sum(len(block.split()) for block in note_blocks) == 499

    spec = SPEC.read_text(encoding="utf-8")
    for requirement in (
        "Are Small LLMs Ready for Coding Agents?",
        "Sr Staff Research Scientist at ServiceNow",
        "https://x.com/den-run-ai",
        "Removing the Gemma/Qwen two-variant comparison was a regression",
        "Slide 2 contains no product/vendor taxonomy",
    ):
        assert requirement in spec
