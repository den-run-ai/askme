"""Contracts which failed in the archived pi draft, plus the one-shot gate."""

import json
from pathlib import Path

import pytest

from tests.featurebench.pi_comparison import (
    PROTOCOL,
    ROOT,
    RUNTIME,
    digest,
    episode_completion,
    normalize_acceptance,
    parse_events,
    stage_runtime,
    validate_claim,
)


def test_repository_root_is_portable():
    assert ROOT == Path(__file__).resolve().parents[1]
    assert (ROOT / "askme.py").is_file()


def test_staging_does_not_expose_historical_results_or_git(tmp_path):
    source, target = tmp_path / "source", tmp_path / "runtime"
    source.mkdir()
    for file in RUNTIME:
        (source / file).write_text("# runtime\n")
    (source / "gold.patch").write_text("hidden answer")
    (source / ".git").mkdir()
    (source / "tests").mkdir()
    (source / "tests" / "results.json").write_text("hidden evidence")
    stage_runtime(source, target)
    assert sorted(p.name for p in target.rglob("*")) == sorted(RUNTIME)


@pytest.mark.parametrize(
    "exit_code,events,want",
    [
        (0, [], False),
        (0, [{"type": "agent_end"}], False),
        (
            0,
            [{"type": "agent_end", "messages": [{"role": "assistant", "stopReason": "stop"}]}],
            True,
        ),
        (
            0,
            [
                {
                    "type": "agent_end",
                    "messages": [
                        {"role": "assistant", "stopReason": "error", "errorMessage": "bad request"}
                    ],
                }
            ],
            False,
        ),
        (
            0,
            [{"type": "agent_end", "messages": [{"role": "assistant", "stopReason": "aborted"}]}],
            False,
        ),
        (1, [{"type": "agent_end"}], False),
        (124, [{"type": "agent_end"}], False),
        (0, [{"type": "agent_end"}, {"type": "error"}], False),
        (0, [{"type": "message_end"}], False),
    ],
)
def test_pi_completion_requires_valid_agent_end(exit_code, events, want):
    assert episode_completion("pi", exit_code, events) is want


def test_malformed_event_stream_is_retained_and_disqualified(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"type":"agent_end"}\n{truncated\n[]\n')
    events, malformed = parse_events(path)
    assert events == [{"type": "agent_end"}]
    assert malformed == 2


@pytest.mark.parametrize(
    "status,want",
    [("complete", True), ("complete_unverified", True), ("exhausted", False), (None, False)],
)
def test_askme_uses_current_completion_contract(status, want):
    assert episode_completion("askme", 0, {"status": status}) is want


@pytest.mark.parametrize(
    "error", [{"error": "failed"}, {"traceback": "trace"}, {"featurebench_eval_completed": False}]
)
def test_evaluator_failure_is_not_unresolved(error):
    outcome = normalize_acceptance(
        {"task": {"featurebench_eval_completed": True, "resolved": False, **error}}, "task"
    )
    assert outcome["evaluator_valid"] is False
    assert outcome["resolved"] is None
    assert outcome["patch_applied"] is None


def test_valid_unresolved_applying_patch_is_preserved():
    outcome = normalize_acceptance(
        {
            "task": {
                "featurebench_eval_completed": True,
                "resolved": False,
                "patch_successfully_applied": True,
            }
        },
        "task",
    )
    assert outcome["evaluator_valid"] is True
    assert outcome["resolved"] is False
    assert outcome["patch_applied"] is True


def valid_claim():
    env = {
        "GITHUB_REPOSITORY": "den-run-ai/askme",
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
    }
    claim = {
        "protocol": json.loads(PROTOCOL.read_text())["protocol"],
        "protocol_sha256": digest(PROTOCOL),
        "run_id": 123,
        "workflow_revision": "abc",
    }
    return claim, env


def test_only_claimed_workflow_is_allowed():
    claim, env = valid_claim()
    assert validate_claim(claim, PROTOCOL, env, "abc") == claim


@pytest.mark.parametrize(
    "change",
    [
        {"GITHUB_RUN_ID": "124"},
        {"GITHUB_RUN_ATTEMPT": "2"},
        {"GITHUB_REPOSITORY": "other/askme"},
        {"GITHUB_RUN_ID": "0"},
    ],
)
def test_relabel_or_rerun_cannot_spend(change):
    claim, env = valid_claim()
    with pytest.raises(ValueError):
        validate_claim(claim, PROTOCOL, env | change, "abc")


def test_changed_source_or_manifest_cannot_reuse_claim(tmp_path):
    claim, env = valid_claim()
    with pytest.raises(ValueError):
        validate_claim(claim, PROTOCOL, env, "other")
    changed = tmp_path / "protocol.json"
    changed.write_text(PROTOCOL.read_text() + "\n")
    with pytest.raises(ValueError):
        validate_claim(claim, changed, env, "abc")


def test_four_cells_are_counterbalanced_and_within_model_routes_match():
    p = json.loads(PROTOCOL.read_text())
    cells = p["cells"]
    assert [c["harness"] for c in cells] == ["askme", "pi", "pi", "askme"]
    for pair in (cells[:2], cells[2:]):
        assert {k: v for k, v in pair[0].items() if k not in {"id", "harness"}} == {
            k: v for k, v in pair[1].items() if k not in {"id", "harness"}
        }
