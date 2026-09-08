"""The archived probe cannot establish more than its retained evidence supports."""

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

from actions import ACTION_SPECS

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


def _analyze_record(record, tmp_path, monkeypatch, capsys):
    path = tmp_path / "synthetic.jsonl"
    path.write_text(json.dumps(record) + "\n")
    monkeypatch.setattr(sys, "argv", ["analyze.py", str(path)])
    analysis.main()
    return capsys.readouterr().out


@pytest.mark.parametrize(
    ("arm", "keys"),
    [
        ("A_short_args", []),
        ("A_short_args", ["arg", "bogus"]),
        ("A_short_args", ["action", "arg"]),
        ("A_short_args", ["arg", "cursor"]),
        ("A_short_args", ["arg", "cursor", "limit"]),
        ("A_short_args", ["arg", "cursor", "sha256"]),
        ("A_short_args", ["arg", "sha256"]),
        ("B_long_write_512", ["arg"]),
        ("B_long_write_512", ["content"]),
        ("B_long_write_2048", ["arg", "bogus", "content"]),
        ("C_delimiter_payload", []),
        ("C_delimiter_payload", ["action", "arg", "content"]),
    ],
)
def test_public_analysis_rejects_schema_failures_proved_by_retained_keys(
    tmp_path, monkeypatch, capsys, arm, keys
):
    record = _parseable_record(arm=arm, tool_name=analysis.EXPECTED_TOOLS[arm], arg_keys=keys)
    output = _analyze_record(record, tmp_path, monkeypatch, capsys)
    assert "{'action_contract_failure': 1}" in output
    assert "parse_ok_schema_unverified" not in output
    assert "=== COMBINED (n=1) ===" in output


@pytest.mark.parametrize(
    ("arm", "keys"),
    [
        ("A_short_args", ["arg"]),
        ("A_short_args", ["arg", "cursor", "limit", "offset", "reasoning", "sha256"]),
        ("B_long_write_512", ["arg", "content"]),
        ("B_long_write_2048", ["append", "arg", "content", "reasoning"]),
        ("C_delimiter_payload", ["arg", "content"]),
    ],
)
def test_public_analysis_valid_keys_still_do_not_prove_unretained_values(
    tmp_path, monkeypatch, capsys, arm, keys
):
    record = _parseable_record(arm=arm, tool_name=analysis.EXPECTED_TOOLS[arm], arg_keys=keys)
    output = _analyze_record(record, tmp_path, monkeypatch, capsys)
    assert "{'parse_ok_schema_unverified': 1}" in output
    assert (
        "Full argument values were not retained: AskMe schema acceptance is unverified." in output
    )


@pytest.mark.parametrize(
    "keys", [["arg", 1], ["arg", []], ["arg", "arg"], ["reasoning", "arg"], "arg", {}]
)
def test_public_analysis_impossible_key_metadata_is_insufficient(
    tmp_path, monkeypatch, capsys, keys
):
    output = _analyze_record(_parseable_record(arg_keys=keys), tmp_path, monkeypatch, capsys)
    assert "{'insufficient_evidence': 1}" in output
    assert "action_contract_failure" not in output


def test_public_analysis_missing_keys_differ_from_retained_non_object(
    tmp_path, monkeypatch, capsys
):
    record = _parseable_record()
    del record["arg_keys"]
    missing = _analyze_record(record, tmp_path, monkeypatch, capsys)
    assert "{'insufficient_evidence': 1}" in missing
    non_object = _analyze_record(_parseable_record(arg_keys=None), tmp_path, monkeypatch, capsys)
    assert "{'action_contract_failure': 1}" in non_object


def test_archived_key_projection_matches_unchanged_canonical_registry():
    # A future schema migration must review this comparison, not silently
    # replace the archival projection and change historical classifications.
    assert set(analysis.ARGUMENT_KEYS) == set(analysis.EXPECTED_TOOLS.values())
    for tool, (required, allowed) in analysis.ARGUMENT_KEYS.items():
        spec = ACTION_SPECS[tool]
        assert required == frozenset(spec.requires)
        assert allowed == frozenset(spec.allowed) - {"action"}
