"""Consistency guards for the Berkeley talk eval evidence that seeds LLM CI.

The LLM CI workflow (.github/workflows/llm.yml) replays the two task cells
from talks/berkeley-agentic-ai-summit-2026/evals/, so this module pins:

- the linkage: protocol selectors -> pytest OpenRouter suite -> CI workflow,
  so a renamed test or suite breaks loudly instead of silently diverging
  from the published protocol;
- the internal consistency of draft-results.json (totals equal cell sums,
  the predeclared pass rule holds per cell, billing reconciles), in the same
  spirit as test_talk_deck_contract.py for the narrative.

Offline only — no LLM or network involved.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RESULTS = (ROOT / "talks" / "berkeley-agentic-ai-summit-2026"
           / "evals" / "draft-results.json")
LLM_WORKFLOW = ROOT / ".github" / "workflows" / "llm.yml"
INTEGRATION_TESTS = ROOT / "tests" / "test_agent_integration.py"

# Protocol suite -> OpenRouter pytest class that hosts its selector.
SUITE_CLASSES = {"hard": "TestOpenRouterHard", "medium": "TestOpenRouterMedium"}


@pytest.fixture(scope="module")
def results():
    return json.loads(RESULTS.read_text(encoding="utf-8"))


def _selector(task):
    suite, test_name = (part.strip() for part in task["selector"].split("/"))
    return suite, test_name


# --- protocol <-> suite <-> CI linkage ---

def test_protocol_selectors_exist_in_pytest_suite(results):
    text = INTEGRATION_TESTS.read_text(encoding="utf-8")
    for task in results["tasks"].values():
        suite, test_name = _selector(task)
        assert suite in SUITE_CLASSES, suite
        assert "class {}".format(SUITE_CLASSES[suite]) in text
        assert "def {}(".format(test_name) in text


def test_ci_workflow_replays_protocol_selectors(results):
    workflow = LLM_WORKFLOW.read_text(encoding="utf-8")
    assert len(results["tasks"]) == 2  # build + repair
    for task in results["tasks"].values():
        suite, test_name = _selector(task)
        assert "--suite {}".format(suite) in workflow
        assert "--test {}".format(test_name) in workflow
    assert "--backend openrouter" in workflow


# --- draft-results.json internal consistency ---

def test_cell_statuses_follow_the_predeclared_pass_rule(results):
    """Pass rule: pytest pass AND agent_complete AND deterministic
    acceptance pass (study.pass_rule)."""
    for cell in results["cells"]:
        outcomes = cell["outcomes"]
        expected = ("pass" if (
            outcomes["pytest"] == "pass"
            and outcomes["agent_complete"]
            and outcomes["deterministic_postcondition"]["status"] == "pass"
        ) else "fail")
        assert outcomes["cell_status"] == expected, cell["id"]


def test_overall_totals_match_cell_sums(results):
    cells = results["cells"]
    totals = results["overall_totals"]
    assert totals["cells"] == len(cells) == 8

    statuses = [c["outcomes"]["cell_status"] for c in cells]
    assert totals["cell_statuses"]["pass"] == statuses.count("pass")
    assert totals["cell_statuses"]["fail"] == statuses.count("fail")
    pytest_results = [c["outcomes"]["pytest"] for c in cells]
    assert totals["pytest"]["pass"] == pytest_results.count("pass")
    assert totals["pytest"]["fail"] == pytest_results.count("fail")
    completes = [c["outcomes"]["agent_complete"] for c in cells]
    assert totals["agent_complete"]["true"] == completes.count(True)
    assert totals["agent_complete"]["false"] == completes.count(False)

    def metric_sum(key):
        return sum(c["metrics"][key] for c in cells)

    assert totals["agent_wall_s"] == pytest.approx(metric_sum("agent_wall_s"), abs=1e-6)
    for key in ("steps", "failed_steps", "full_replans", "local_replans",
                "thinking_retry_responses", "usage_bearing_responses",
                "prompt_tokens", "completion_tokens", "total_tokens"):
        assert totals[key] == metric_sum(key), key
    assert totals["billed_credits"] == pytest.approx(
        metric_sum("billed_credits"), abs=1e-9)


def test_per_cell_token_totals_are_consistent(results):
    for cell in results["cells"]:
        metrics = cell["metrics"]
        assert metrics["total_tokens"] == (
            metrics["prompt_tokens"] + metrics["completion_tokens"]), cell["id"]


def test_model_totals_match_their_cells(results):
    for model_total in results["model_totals"]:
        model_cells = [c for c in results["cells"]
                       if c["model_id"] == model_total["model_id"]]
        assert len(model_cells) == 2, model_total["model_id"]
        statuses = [c["outcomes"]["cell_status"] for c in model_cells]
        assert model_total["cell_statuses"]["pass"] == statuses.count("pass")
        assert model_total["cell_statuses"]["fail"] == statuses.count("fail")

        def metric_sum(key):
            return sum(c["metrics"][key] for c in model_cells)

        assert model_total["agent_wall_s"] == pytest.approx(
            metric_sum("agent_wall_s"), abs=1e-6)
        for key in ("steps", "failed_steps", "full_replans", "local_replans",
                    "thinking_retry_responses", "usage_bearing_responses",
                    "prompt_tokens", "completion_tokens", "total_tokens"):
            assert model_total[key] == metric_sum(key), (
                model_total["model_id"], key)
        assert model_total["billed_credits"] == pytest.approx(
            metric_sum("billed_credits"), abs=1e-9)


def test_billing_reconciliation_matches_cells(results):
    reconciliation = results["billing_reconciliation"]
    billed = sum(c["metrics"]["billed_credits"] for c in results["cells"])
    combined = reconciliation["combined"]
    assert combined["matches"] is True
    assert combined["sum_of_response_costs"] == pytest.approx(billed, abs=1e-9)
    assert combined["api_key_usage_delta"] == pytest.approx(
        combined["api_key_usage_after"] - combined["api_key_usage_before"],
        abs=1e-9)
    for segment in reconciliation["segments"]:
        assert segment["matches"] is True
        assert segment["api_key_usage_delta"] == pytest.approx(
            segment["api_key_usage_after"] - segment["api_key_usage_before"],
            abs=1e-9)
    segment_sum = sum(s["sum_of_response_costs"]
                      for s in reconciliation["segments"])
    assert segment_sum == pytest.approx(
        combined["sum_of_response_costs"], abs=1e-9)


def test_extension_cells_are_declared(results):
    extensions = {e["id"]: e for e in results["study"]["extensions"]}
    extension_cells = {c["id"] for c in results["cells"]
                       if c.get("extension_id")}
    declared = {cell_id for e in extensions.values()
                for cell_id in e["added_cells"]}
    assert extension_cells == declared
    for cell in results["cells"]:
        if cell.get("extension_id"):
            assert cell["extension_id"] in extensions, cell["id"]


def test_infrastructure_audit_events_stay_out_of_totals(results):
    """The predeclared repeat rule: zero-token infra failures are retained
    as audit evidence but are never model results."""
    for event in results["infrastructure_audit"]:
        assert event["recorded_as_model_result"] is False
        assert event["included_in_totals"] is False
        assert event["billed_credits"] == 0
        assert event["token_events"] == 0
        assert event["model_response_received"] is False
