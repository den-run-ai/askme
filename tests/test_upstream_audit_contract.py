"""Keep a dated upstream diagnostic separate from current runtime claims."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_architecture_never_equates_e25_with_synthetic_tool_history():
    text = (ROOT / "docs/ARCHITECTURE.md").read_text()
    assert "precisely the shape E25 already ran" not in text
    assert "E25 used AskMe's two-message executor request" in text
    assert "synthetic prior tool conversation" in text
    assert "historical, not newly verified here" in text
    assert "62 parseable/schema-unverified replies" in text


def test_reconciled_setup_keeps_current_transport_and_dated_upstream_boundaries():
    text = (ROOT / "docs/gemma4-setup.md").read_text()
    assert "dated reference snapshots, not a fresh upstream-status audit" in text
    assert "## Current AskMe transport" in text
    assert "Native executor arguments are validated, not repaired or salvaged" in text
    assert "Historical August 29 parser-risk audit, not a deferred adoption gate" in text


def test_new_collector_does_not_upgrade_historical_schema_verdicts():
    text = (ROOT / "tests/bench_records/2026-08-29-peg-probe/README.md").read_text()
    assert "parse_ok_schema_unverified" in text
    assert "peg_probe_v2.py" in text
    assert "no live CLI or default HTTP transport" in text
    assert "A new live campaign still requires a separately registered protocol" in text


def test_tuning_proposal_does_not_claim_a_proven_mtp_cause_or_guaranteed_gain():
    setup = (ROOT / "docs/gemma4-setup.md").read_text()
    experiments = (ROOT / "docs/EXPERIMENTS.md").read_text()
    assert "untested candidate explanation" in setup
    assert "Dated tuning proposal, not a measured benefit or current recommendation" in setup
    for phrase in (
        "mechanistic explanation",
        "Highest-value local lever",
        "actionable locally today",
    ):
        assert phrase not in setup
    assert "shape overlap does not establish an MTP handicap" in experiments
    assert "does not guarantee non-regression or an end-to-end benefit" in experiments
    assert "mechanistically explained" not in experiments
    assert "prerequisite for a fair MTP retry" not in " ".join(setup.split())
    assert "Matched untuned and tuned controls" in setup
