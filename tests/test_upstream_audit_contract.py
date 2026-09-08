"""Keep a dated upstream diagnostic separate from current runtime claims."""

import json
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_e25_run_contract_and_retry_evidence_do_not_supply_response_bodies():
    records = ROOT / "tests/bench_records/2026-08-04/tools"
    validity = []
    for path in sorted(records.glob("*/summary.json")):
        summary = json.loads(path.read_text())
        validity.extend(
            valid for test in summary["tests"].values() for valid in test["contract_valid"]
        )
    assert validity == [True] * 27

    logs = sorted(records.glob("*/*.jsonl"))
    assert len(logs) == 27
    rows = [json.loads(line) for path in logs for line in path.read_text().splitlines()]
    attempts = Counter(row["attempt"] for row in rows if row["event"] == "tokens")
    assert attempts == {0: 530, 1: 20, 2: 9}
    assert all(
        not {"raw_response", "response_body", "tool_calls"}.intersection(row) for row in rows
    )


@pytest.mark.parametrize(
    ("document", "heading"),
    [("EXPERIMENTS.md", "### E25 —"), ("PERFORMANCE.md", "## E25 Transport A/B")],
)
def test_e25_docs_separate_run_contract_from_unmeasured_malformed_call_incidence(document, heading):
    section = (ROOT / "docs" / document).read_text().split(heading, 1)[1]
    text = " ".join(section.split("\n##", 1)[0].split())
    for unsupported in (
        "zero malformed",
        "No transport-level failures.",
        "not a transport failure",
        "Every tools failure is one of",
        "every tools failure is one of",
    ):
        assert unsupported not in text
    assert "Run-contract validity does not measure malformed-call incidence" in text
    assert "raw replies and typed decoder failures were not retained" in text
    assert "retry attempts do not identify their causes" in text


def test_e27_draft_requires_response_level_evidence_before_outcome_calls():
    experiments = (ROOT / "docs/EXPERIMENTS.md").read_text()
    section = experiments.split("### E27 —", 1)[1].split("\n##", 1)[0]
    text = " ".join(section.split())
    assert "incomplete draft, blocked before outcome-bearing calls" in text
    assert "every response attempt, including recovered retries" in text
    assert "define the denominator" in text
    assert "typed decoder failures separately from HTTP errors and budget telemetry" in text
    assert "matched runtime, model, settings and request shape" in text
    assert "E23 is historical context, not the matched control" in text
    assert "not a live driver or automatic E01 instrumentation" in text
    assert section.index("**Execution gate") < section.index("**Change")


def test_setup_and_adjacent_drafts_preserve_e27_evidence_gate():
    setup = " ".join((ROOT / "docs/gemma4-setup.md").read_text().split())
    experiments = " ".join((ROOT / "docs/EXPERIMENTS.md").read_text().split())
    assert "E27 remains an incomplete draft" in setup
    assert "before any outcome-bearing calls" in setup
    assert "The E26 sweep remains gated on E27 qualification" in setup
    assert "untyped retry-attempt count" in experiments
    assert (
        "E27's response-level evidence gate applies to any claimed parse-failure metric"
        in experiments
    )


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
