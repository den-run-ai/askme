"""Contract guards for the GitHub Actions workflows.

String-level assertions (no YAML dependency) in the same spirit as
test_talk_deck_contract.py: pin the properties that keep CI safe —
the unit matrix stays credential-free, and the paid LLM workflow always
preflights, gates, and stays opt-in for pull requests.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
LLM_WORKFLOW = ROOT / ".github" / "workflows" / "llm.yml"
PYPROJECT = ROOT / "pyproject.toml"
UV_LOCK = ROOT / "uv.lock"


def test_unit_workflow_stays_hermetic():
    """The unit matrix must never see an OpenRouter credential. With a key in
    scope, the backend-gated OpenRouter suites would stop auto-skipping and
    spend credits on every push and PR, across every Python in the matrix."""
    text = UNIT_WORKFLOW.read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY" not in text
    assert "environment:" not in text


def test_unit_workflow_gates_quality_compatibility_and_coverage():
    text = UNIT_WORKFLOW.read_text(encoding="utf-8")
    assert "uv run --locked ruff check askme.py actions.py tests" in text
    assert "uv run --locked ruff format --check askme.py actions.py tests" in text
    assert "uv run --locked ty check" in text
    assert "mypy" not in text
    assert 'python-version: ["3.10", "3.11", "3.12", "3.13", "3.14"]' in text
    assert "--cov=askme" in text
    assert "--cov=actions" in text
    assert "--cov-report=xml:coverage.xml" in text
    assert text.count("uv sync --locked") == 2
    assert text.count("astral-sh/setup-uv@") == 2
    assert text.count('version: "0.12.1"') == 2
    assert text.count("cache-dependency-glob: uv.lock") == 2
    assert "requirements" not in text
    assert text.count("persist-credentials: false") == 2
    assert "permissions:" in text
    assert "contents: read" in text
    assert "concurrency:" in text
    assert "cancel-in-progress: true" in text
    project = PYPROJECT.read_text(encoding="utf-8")
    assert "fail_under = 90" in project
    assert "[tool.ty.environment]" in project
    assert 'required-version = "==0.12.1"' in project
    assert UV_LOCK.is_file()


def test_unit_workflow_publishes_coverage_reports():
    text = UNIT_WORKFLOW.read_text(encoding="utf-8")
    assert "--cov-report=json:coverage.json" in text
    assert "--cov-report=html:htmlcov" in text
    assert "printf '## Coverage\\n\\n'" in text
    assert '--cov-report=markdown-append:"$GITHUB_STEP_SUMMARY"' in text
    assert text.count("codecov/codecov-action@") == 1
    assert "token: ${{ secrets.CODECOV_TOKEN }}" in text
    assert "files: ./coverage.xml" in text
    assert "disable_search: true" in text
    assert "fail_ci_if_error: true" in text
    assert "github.actor != 'dependabot[bot]'" in text
    assert "pull_request_target" not in text
    for report in ("coverage.xml", "coverage.json", "htmlcov/"):
        assert report in text


def test_workflows_pin_third_party_actions():
    for workflow in (UNIT_WORKFLOW, LLM_WORKFLOW):
        text = workflow.read_text(encoding="utf-8")
        external_actions = re.findall(r"^\s*(?:-\s+)?uses:\s+([^#\s]+)", text, flags=re.MULTILINE)
        assert external_actions
        for action in external_actions:
            assert re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", action), action


def test_workflows_do_not_persist_checkout_credentials():
    text = UNIT_WORKFLOW.read_text(encoding="utf-8") + LLM_WORKFLOW.read_text(encoding="utf-8")
    assert text.count("persist-credentials: false") == text.count("actions/checkout@")


def test_llm_workflow_uses_the_openrouter_environment():
    """All paid jobs use a protected environment and secret-only credential."""
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    assert text.count("environment: Openrouter") == 3
    assert "vars.OPENROUTER_API_KEY" not in text
    assert text.count("OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}") == 6

    for section in _paid_job_sections(text):
        assert not re.search(r"^    env:", section, flags=re.MULTILINE)
        assert section.index("Install dependencies") < section.index("OPENROUTER_API_KEY:")


def _paid_job_sections(text: str) -> tuple[str, str, str]:
    smoke_and_after = text.split("  openrouter-smoke:", 1)[1]
    smoke, berkeley_and_after = smoke_and_after.split("  berkeley-protocol:", 1)
    berkeley, webbench = berkeley_and_after.split("  web-bench-trials:", 1)
    return smoke, berkeley, webbench


def test_llm_workflow_preflights_before_spending():
    """A missing/invalid key must fail loudly, not silently skip every test
    (conftest's skip markers would otherwise turn a bad credential into a
    green run)."""
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    smoke, berkeley, webbench = _paid_job_sections(text)
    assert smoke.count("ci_llm_gate.py preflight") == 1
    assert berkeley.count("ci_llm_gate.py preflight") == 1
    assert webbench.count("ci_llm_gate.py preflight") == 1
    assert smoke.index("ci_llm_gate.py preflight") < smoke.index(
        "uv run --locked pytest tests/test_agent_integration.py"
    )
    for bench_section in (berkeley, webbench):
        assert bench_section.index("ci_llm_gate.py preflight") < bench_section.index(
            "uv run --locked python tests/bench_harness.py"
        )


def test_llm_workflow_smoke_suite_selector_covers_every_suite():
    """The dispatch suite choices and the -k mapping must stay in step; a
    choice without a case branch would exit 1 after the credential preflight."""
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    assert "options: [easy, medium, hard, web]" in text
    for class_name in (
        "TestOpenRouterEasy",
        "TestOpenRouterMedium",
        "TestOpenRouterHard",
        "TestOpenRouterWeb",
    ):
        assert f'K="{class_name}"' in text


def test_llm_workflow_guards_against_silent_skips():
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    # pytest reports skip reasons, and the smoke job asserts the agent
    # actually logged run events — per matrix model, and again after the loop.
    assert "-rs" in text
    assert 'ASKME_RUN_LIVE_LLM_TESTS: "1"' in text
    assert "-m live_llm" in text
    assert 'test -s "llm-logs/smoke-$SLUG.jsonl"' in text
    assert 'for f in llm-logs/smoke-*.jsonl; do test -s "$f"; done' in text


def test_llm_workflow_smoke_supports_model_matrix():
    """The smoke job runs the selected suite once per SMOKE_MODELS entry with
    the model (and optional '@effort' baseline) exported per iteration, keeps
    running after a model fails so the matrix yields complete evidence, and
    fails the job at the end when any model failed."""
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    smoke, _, _ = _paid_job_sections(text)
    assert "SMOKE_MODELS: ${{ inputs.smoke_models || 'google/gemma-4-26b-a4b-it' }}" in smoke
    assert 'OPENROUTER_MODEL="$MODEL"' in smoke
    assert 'OPENROUTER_REASONING_EFFORT="$EFFORT"' in smoke
    assert 'AGENT_RUN_LOG="$GITHUB_WORKSPACE/llm-logs/smoke-$SLUG.jsonl"' in smoke
    assert "FAILED_MODELS" in smoke
    assert "exit 1" in smoke


def test_llm_workflow_gates_bench_results():
    """bench_harness never exits nonzero on test failures; the gate script
    evaluates every cell. A one-trial result is advisory only after merge to
    main; scheduled/manual drift checks and opt-in PR runs stay strict."""
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    smoke, berkeley, webbench = _paid_job_sections(text)
    gate = berkeley.split("      - name: Gate on protocol pass rule", 1)[1].split(
        "      - name: Upload bench logs and summaries", 1
    )[0]
    assert "continue-on-error" not in gate
    assert "--advisory-cell-failures" not in smoke
    assert gate.count("--advisory-cell-failures") == 1
    assert 'if [[ "$GITHUB_EVENT_NAME" == "push" ]]' in gate
    assert gate.index("--advisory-cell-failures") < gate.index("ci_llm_gate.py report")
    assert "--expect-cells" in gate
    assert "--markdown-out" in gate
    # The web trial matrix is always strict: dispatch-only, never advisory.
    assert "continue-on-error" not in webbench
    assert "--advisory-cell-failures" not in webbench
    assert "--expect-cells" in webbench


def test_llm_workflow_web_bench_is_dispatch_only_and_per_cell():
    """web-bench-trials must never fire outside an explicit dispatch opt-in,
    and must bench per (model, test) cell so ci_llm_gate sees every trial."""
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    _, _, webbench = _paid_job_sections(text)
    assert (
        "github.event_name == 'workflow_dispatch'"
        " && inputs.web_trials != '' && inputs.web_trials != '0'"
    ) in webbench
    assert "--suite web" in webbench
    assert '--test "$T"' in webbench
    assert '--trials "$WEB_TRIALS"' in webbench
    assert "EXPECTED_WEB_CELLS" in webbench
    assert "web_trials:\n        description" in text
    assert 'default: "0"' in text


def test_llm_workflow_is_opt_in_for_pull_requests():
    """PRs need the 'llm-tests' label and a same-repository head branch."""
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    guard = (
        "github.event.pull_request.head.repo.full_name == github.repository"
        " && contains(github.event.pull_request.labels.*.name, 'llm-tests')"
    )
    assert text.count(guard) == 2  # both paid jobs: same-repo AND label
    assert text.count("github.event_name != 'pull_request'") == 2


def test_llm_workflow_tracks_locked_dependencies_and_uses_uv_cache():
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    push_block = text.split("  push:", 1)[1].split("  pull_request:", 1)[0]
    for path in ("pyproject.toml", "uv.lock"):
        assert f"- {path}" in push_block
    assert "requirements" not in text
    assert text.count("uv sync --locked") == 3
    assert text.count("astral-sh/setup-uv@") == 3
    assert text.count('version: "0.12.1"') == 3
    assert text.count("cache-dependency-glob: uv.lock") == 3


def test_llm_workflow_supports_effort_pinned_cells():
    """Berkeley model entries may pin a baseline reasoning effort
    ('openai/gpt-oss-20b@low') for always-on reasoners. The loop must strip
    the suffix before --model sees it and forward it as --reasoning-effort,
    with the effort kept in the log-dir slug so cells don't collide."""
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    assert 'EFFORT="${MODEL##*@}"' in text
    assert 'MODEL="${MODEL%@*}"' in text
    assert "--reasoning-effort" in text
    assert "${EFFORT:+-$EFFORT}" in text


def test_llm_workflow_bounds_spend():
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "schedule:" in text
    assert text.count("timeout-minutes:") == 3
    assert "concurrency:" in text
    assert "cancel-in-progress: true" in text
    assert "permissions:" in text
    assert "contents: read" in text
