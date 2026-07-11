"""Qualification tests for independently authored workflow alternatives."""

import shutil
from pathlib import Path

from workflow_eval import evaluate_workflow


TESTS_ROOT = Path(__file__).parent
MANIFEST = TESTS_ROOT / "workflows" / "config_precedence" / "manifest.json"
ALTERNATIVE = (
    TESTS_ROOT
    / "workflow_alternatives"
    / "config_precedence"
    / "config_cli.py"
)


def test_independent_config_precedence_alternative_passes_all_checks(tmp_path):
    """Run the separately authored implementation through the full evaluator."""

    def install_alternative(_prompt, workspace):
        shutil.copy2(ALTERNATIVE, workspace / "config_cli.py")
        return {"status": "complete"}

    result = evaluate_workflow(
        MANIFEST,
        install_alternative,
        workspace=tmp_path / "independent-alternative-workspace",
    )

    assert result["run_valid"] is True
    assert result["regression_passed"] is True
    assert result["feedback_passed"] is True
    assert result["acceptance_passed"] is True
    assert result["artifact_accepted"] is True
    assert result["outcome"] == "clean_success"
