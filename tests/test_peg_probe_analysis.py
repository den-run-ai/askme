"""The archived probe cannot establish more than its retained evidence supports."""

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

RECORDS = Path(__file__).parent / "bench_records" / "2026-08-29-peg-probe"
SPEC = importlib.util.spec_from_file_location("peg_probe_analysis", RECORDS / "analyze.py")
analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analysis)


def _parseable_record(**changes):
    return {
        "arm": "A_short_args",
        "http_status": 200,
        "n_tool_calls": 1,
        "tool_name": "read",
        "args_parse_ok": True,
        "arg_keys": ["arg", "reasoning"],
        "ok": True,
        "finish_reason": "tool_calls",
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"n_tool_calls": 2},
        {"tool_name": "write"},
        {"arg_keys": None},
    ],
)
def test_raw_ok_cannot_override_recorded_action_contract_failure(changes):
    assert analysis.classify(_parseable_record(**changes)) == "action_contract_failure"


def test_parseable_arguments_do_not_establish_unretained_value_types():
    assert analysis.classify(_parseable_record()) == "parse_ok_schema_unverified"
    assert (
        analysis.classify(_parseable_record(finish_reason="length")) == "parse_ok_schema_unverified"
    )


def test_missing_parse_evidence_cannot_be_classified_from_raw_ok():
    assert analysis.classify(_parseable_record(args_parse_ok=None)) == "insufficient_evidence"


@pytest.mark.parametrize(
    ("finish_reason", "expected"),
    [("length", "budget_truncation"), ("stop", "uncapped_parse_failure")],
)
def test_parse_failure_is_separate_from_budget_cutoff(finish_reason, expected):
    record = _parseable_record(
        ok=False, args_parse_ok=False, failure="args_not_json", finish_reason=finish_reason
    )
    assert analysis.classify(record) == expected


def test_retained_runs_support_only_qualified_parse_counts():
    rows = [
        json.loads(line)
        for name in ("peg_probe_results_run1.jsonl", "peg_probe_results_run2.jsonl")
        for line in (RECORDS / name).read_text().splitlines()
    ]
    assert Counter(map(analysis.classify, rows)) == {
        "parse_ok_schema_unverified": 62,
        "budget_truncation": 2,
    }


def test_missing_input_cannot_silently_shrink_denominator(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["analyze.py", str(tmp_path / "missing.jsonl")])
    with pytest.raises(FileNotFoundError):
        analysis.main()
