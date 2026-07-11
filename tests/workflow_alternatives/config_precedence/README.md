# Config-precedence alternative

This implementation was independently derived from these allowed public inputs:

- `tests/workflows/config_precedence/manifest.json`
- `tests/workflows/config_precedence/seed/tests/check_regression.py`
- `tests/workflows/config_precedence/seed/tests/check_feedback.py`

It was written without inspecting `tests/test_workflow_eval.py`, anything under
`tests/workflow_evaluators/`, or the reference
`tests/workflows/config_precedence/seed/config_cli.py`. No held-out evaluator was
inspected or invoked.
