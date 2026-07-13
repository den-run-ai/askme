"""Regression guard for the Berkeley talk's reviewer-approved narrative."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TALK = ROOT / "talks" / "berkeley-agentic-ai-summit-2026"
SLIDES = TALK / "slides.md"
SPEC = TALK / "DECK_SPEC.md"
BLOG = TALK / "blog.md"
README = TALK / "README.md"


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
    assert "4 hosted model variants × 2 simple checks × 1 unseeded run/cell" in comparison
    assert "35B total · 3B active" in comparison
    assert "Supported" in comparison
    assert "Not a clean model comparison" in comparison
    assert "No Qwen-vs-Gemma, larger-vs-smaller" in comparison
    for misleading_timing in ("603.6s", "66.5s", "47.9s", "17.7s"):
        assert misleading_timing not in comparison

    pilot = slides[5]
    assert "Next evidence · two separate steps" in pilot
    assert "An AskMe setting A/B test—not an external benchmark" in pilot
    assert "Off:" in pilot
    assert "Gated:" in pilot
    assert "Native A/B not ready" in pilot
    assert "24-run scope" in pilot
    assert "0 measured runs" in pilot
    assert "not reliability, model size, or Qwen vs Gemma" in pilot
    assert "External evaluation is also not ready" in pilot
    assert "FeatureBench first" in pilot
    assert "Vals only if access permits" in pilot

    conclusion = slides[6]
    assert "Conclusion + limits" in conclusion
    assert "Promising—with a tight harness." in conclusion
    for label in ("Observed", "Supported", "Still open"):
        assert label in conclusion
    assert "The smoke exercises the measurement path" in conclusion
    assert "validates this interface" not in conclusion

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
    assert "FeatureBench adaptation comes later" in text
    assert "Vals requires access" in text
    assert "ProgramBench" not in text
    for out_of_scope in (
        "Claw-SWE-Bench",
        "deep-swe",
        "SWE-bench-Live",
        "Terminal-Bench",
        "ViBench",
        "RACE-bench",
    ):
        assert out_of_scope not in text

    spec = SPEC.read_text(encoding="utf-8")
    for requirement in (
        "Are Small LLMs Ready for Coding Agents?",
        "Sr Staff Research Scientist at ServiceNow",
        "https://x.com/den-run-ai",
        "Removing the Gemma/Qwen two-variant comparison was a regression",
        "Slide 2 contains no product/vendor taxonomy",
        "FeatureBench for feature development and Vals Vibe Code Bench",
        "A one-task `gron` run may qualify the adapter",
        '"small" is an engineering and deployment class',
        "This is 24 scheduled runs total",
        "does not validate a causal harness benefit",
        "AskMe-owned A/B pilot",
        "Neither has an AskMe adapter",
        'Avoid "large policy effect."',
    ):
        assert requirement in spec


def test_companion_benchmark_shortlist_stays_bounded():
    blog = BLOG.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    companion = "\n".join(
        path.read_text(encoding="utf-8") for path in (BLOG, README, SPEC)
    )
    for benchmark in ("FeatureBench", "Vals Vibe Code Bench", "ProgramBench"):
        assert benchmark in companion
    assert "small is a deployment class, not a strict parameter threshold" in blog
    assert "one of four workflows is qualified" in blog
    assert "24 runs total" in blog
    assert "24 runs per model" not in blog
    assert "The smoke exercises the measurement path" in readme
    assert "model-size claim requires a separate predeclared, repeated design" in readme
    for out_of_scope in (
        "Claw-SWE-Bench",
        "deep-swe",
        "SWE-bench-Live",
        "Terminal-Bench",
        "ViBench",
        "RACE-bench",
    ):
        assert out_of_scope not in companion

    shortlist = re.search(
        r"(?ms)^### Coding-agent benchmark shortlist\s+(.*?)^### Other sources",
        readme,
    )
    assert shortlist is not None
    assert re.findall(r"(?m)^- \[([^]]+)\]", shortlist.group(1)) == [
        "FeatureBench",
        "Vals Vibe Code Bench",
        "ProgramBench",
    ]
    assert "access-dependent full-app reference" in shortlist.group(1)
    assert "later clean-room stress test; `gron` canary only" in shortlist.group(1)

    roadmap = re.search(
        r"External generalization should answer three distinct questions at "
        r"most\.(.*?)\n\nThis pilot",
        blog,
        flags=re.DOTALL,
    )
    assert roadmap is not None
    for benchmark in ("FeatureBench-fast", "Vals Vibe Code Bench", "ProgramBench"):
        assert benchmark in roadmap.group(1)
    assert "one-task `gron` adapter canary" in roadmap.group(1)
    assert "not a model result" in roadmap.group(1)
    assert "not a commitment to run all three" in roadmap.group(1)
