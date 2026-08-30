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
MACOS_WORKFLOW = ROOT / ".github" / "workflows" / "macos.yml"
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
    for workflow in (UNIT_WORKFLOW, LLM_WORKFLOW, MACOS_WORKFLOW):
        text = workflow.read_text(encoding="utf-8")
        external_actions = re.findall(r"^\s*(?:-\s+)?uses:\s+([^#\s]+)", text, flags=re.MULTILINE)
        assert external_actions
        for action in external_actions:
            assert re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", action), action


def test_workflows_do_not_persist_checkout_credentials():
    text = (
        UNIT_WORKFLOW.read_text(encoding="utf-8")
        + LLM_WORKFLOW.read_text(encoding="utf-8")
        + MACOS_WORKFLOW.read_text(encoding="utf-8")
    )
    assert text.count("persist-credentials: false") == text.count("actions/checkout@")


def test_llm_workflow_uses_the_openrouter_environment():
    """Both paid jobs use a protected environment and secret-only credential."""
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    assert text.count("environment: Openrouter") == 2
    assert "vars.OPENROUTER_API_KEY" not in text
    assert text.count("OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}") == 4

    smoke, berkeley = _paid_job_sections(text)
    for section in (smoke, berkeley):
        assert not re.search(r"^    env:", section, flags=re.MULTILINE)
        assert section.index("Install dependencies") < section.index("OPENROUTER_API_KEY:")


def _paid_job_sections(text: str) -> tuple[str, str]:
    smoke_and_after = text.split("  openrouter-smoke:", 1)[1]
    smoke, berkeley = smoke_and_after.split("  berkeley-protocol:", 1)
    return smoke, berkeley


def test_llm_workflow_preflights_before_spending():
    """A missing/invalid key must fail loudly, not silently skip every test
    (conftest's skip markers would otherwise turn a bad credential into a
    green run)."""
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    smoke, berkeley = _paid_job_sections(text)
    assert smoke.count("ci_llm_gate.py preflight") == 1
    assert berkeley.count("ci_llm_gate.py preflight") == 1
    assert smoke.index("ci_llm_gate.py preflight") < smoke.index(
        "uv run --locked pytest tests/test_agent_integration.py"
    )
    assert berkeley.index("ci_llm_gate.py preflight") < berkeley.index(
        "uv run --locked python tests/bench_harness.py"
    )


def test_llm_workflow_guards_against_silent_skips():
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    # pytest reports skip reasons, and the smoke job asserts the agent
    # actually logged run events.
    assert "-rs" in text
    assert 'ASKME_RUN_LIVE_LLM_TESTS: "1"' in text
    assert "-m live_llm" in text
    assert "test -s llm-logs/smoke.jsonl" in text


def test_llm_workflow_gates_bench_results():
    """bench_harness never exits nonzero on test failures; the gate script
    evaluates every cell. A one-trial result is advisory only after merge to
    main; scheduled/manual drift checks and opt-in PR runs stay strict."""
    text = LLM_WORKFLOW.read_text(encoding="utf-8")
    smoke, berkeley = _paid_job_sections(text)
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
    assert text.count("uv sync --locked") == 2
    assert text.count("astral-sh/setup-uv@") == 2
    assert text.count('version: "0.12.1"') == 2
    assert text.count("cache-dependency-glob: uv.lock") == 2


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
    assert text.count("timeout-minutes:") == 2
    assert "concurrency:" in text
    assert "cancel-in-progress: true" in text
    assert "permissions:" in text
    assert "contents: read" in text


# --- macOS / Apple Silicon workflow ---


def _macos_job_sections() -> tuple[str, str, str]:
    """Return the (tests, llama-contract, llama-reference) job bodies."""
    text = MACOS_WORKFLOW.read_text(encoding="utf-8")
    after_tests = text.split("  macos-tests:", 1)[1]
    tests, after_contract = after_tests.split("  llama-contract:", 1)
    contract, reference = after_contract.split("  llama-reference:", 1)
    return tests, contract, reference


def test_macos_workflow_stays_credential_free():
    """No lane here needs a model credential: the deterministic lane runs no
    model at all, and both llama.cpp lanes talk to a server on localhost."""
    text = MACOS_WORKFLOW.read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY" not in text
    assert "environment:" not in text
    assert "secrets." not in text
    assert "pull_request_target" not in text


def test_macos_lanes_target_apple_silicon():
    """macos-*-large is a 30 GB Intel runner: more memory, but no Apple
    Silicon and no Metal, so it cannot stand in for the reference machine."""
    for section in _macos_job_sections()[:2]:
        assert "runs-on: macos-26\n" in section
        # No Intel label may appear in a runs-on line in these lanes.
        for label in re.findall(r"^\s*runs-on:\s*(.+)$", section, flags=re.MULTILINE):
            assert "-large" not in label
            assert "-intel" not in label
            assert "macos-13" not in label  # the last Intel default


def test_macos_deterministic_lane_stays_hermetic():
    """The always-on lane must not enable live-model tests. With the opt-in
    set, conftest would stop skipping the backend suites and every push
    would need a running llama-server."""
    tests, _, _ = _macos_job_sections()
    # The opt-in must not be *set*; the comment explaining its absence is
    # the point of the lane and must survive.
    assert not re.search(r"ASKME_RUN_LIVE_LLM_TESTS\s*[:=]", tests)
    assert "llama-server" not in tests
    assert "uv run --locked pytest tests/ -v -rs" in tests
    assert 'python-version: ["3.10", "3.14"]' in tests


def test_macos_reference_lane_is_opt_in():
    """Larger runners are billed per-minute even on public repositories and
    are unavailable to user-owned repos, so the reference lane must never
    fire on a push or pull request."""
    _, _, reference = _macos_job_sections()
    assert "if: github.event_name == 'workflow_dispatch'" in reference
    assert "runs-on: ${{ inputs.runner" in reference


def test_macos_schedule_drives_only_the_free_lanes():
    """The weekly run exists to catch llama.cpp/Homebrew drift on the free
    runner. It must not be able to start the billed reference lane."""
    text = MACOS_WORKFLOW.read_text(encoding="utf-8")
    assert "schedule:" in text
    _, _, reference = _macos_job_sections()
    assert "github.event_name == 'schedule'" not in reference


def test_macos_reference_lane_gates_size_before_downloading():
    """The hardware gate must precede the multi-gigabyte model pull: an
    undersized runner should fail fast, not get OOM-killed mid-suite in a
    way that looks like an agent-loop bug."""
    _, _, reference = _macos_job_sections()
    assert reference.index("ci_local_gate.py hardware") < reference.index("Download the reference")
    assert "--require-apple-silicon" in reference
    assert '--min-memory-gb "$MIN_MEMORY_GB"' in reference


def test_macos_llama_lanes_preflight_before_asserting_anything():
    """conftest skips local tests when :8080 is absent, so a broken backend
    would otherwise read as a green run. Both llama lanes preflight first."""
    _, contract, reference = _macos_job_sections()
    for section in (contract, reference):
        assert section.count("ci_local_gate.py preflight") == 1
        assert section.index("Start llama-server") < section.index("ci_local_gate.py preflight")
    assert contract.index("ci_local_gate.py preflight") < contract.index("ci_local_gate.py probe")
    assert reference.index("ci_local_gate.py preflight") < reference.index(
        "uv run --locked pytest tests/test_agent_integration.py"
    )


def test_macos_reference_lane_guards_against_silent_skips():
    _, _, reference = _macos_job_sections()
    assert 'ASKME_RUN_LIVE_LLM_TESTS: "1"' in reference
    assert "-m live_llm" in reference
    assert "-rs" in reference
    assert "test -s macos-logs/reference.jsonl" in reference


def test_macos_contract_lane_keeps_model_behavior_advisory():
    """The transport preflight gates; the tiny-model probe does not. A 0.6B
    model on a 3-vCPU runner missing a tool call is evidence about the
    model, not about AskMe."""
    _, contract, _ = _macos_job_sections()
    probe = contract.split("ci_local_gate.py probe", 1)[1]
    assert "--strict" not in probe
    # --jinja is what makes tool calls parse on Qwen-family templates.
    assert "--jinja" in contract


def test_macos_reference_lane_uses_the_documented_reference_flags():
    """docs/gemma4-setup.md's stable flag set. --reasoning off is
    permanently required for Gemma 4; the KV quantization and SWA flags are
    what make the model fit its documented memory envelope."""
    _, _, reference = _macos_job_sections()
    for flag in (
        "--ctx-size 16384",
        "--flash-attn on",
        "--cache-type-k q4_0 --cache-type-v q4_0",
        "--swa-full --cache-reuse 256",
        "--reasoning off",
        "-np 1",
    ):
        assert flag in reference
    assert "LLM_CAPABILITY_PROFILE: legacy-e4b-m1-16k-v1" in reference
    assert "LLM_MODEL: gemma-4-e4b" in reference


def test_macos_workflow_bounds_spend():
    text = MACOS_WORKFLOW.read_text(encoding="utf-8")
    assert text.count("timeout-minutes:") == 3
    assert "concurrency:" in text
    assert "permissions:" in text
    assert "contents: read" in text


def test_macos_manual_runs_are_not_cancelled_by_routine_events():
    """A manual dispatch shares a ref with push and schedule events. Under a
    ref-only concurrency group with cancel-in-progress, a commit landing on
    main would kill an in-flight reference run after it had already spent
    time on a billed runner. Manual runs get their own group and are never
    cancelled; routine runs still coalesce."""
    text = MACOS_WORKFLOW.read_text(encoding="utf-8")
    assert (
        "group: macos-${{ github.ref }}-"
        "${{ github.event_name == 'workflow_dispatch' && github.run_id || 'auto' }}" in text
    )
    assert "cancel-in-progress: ${{ github.event_name != 'workflow_dispatch' }}" in text
    assert "cancel-in-progress: true" not in text


def test_macos_lanes_record_the_model_artifact_they_loaded():
    """'main' is a moving target and Gemma 4 checkpoints have been
    republished under identical filenames, so a run labelled gemma-4-e4b
    does not identify its weights. Every lane that loads a model resolves an
    explicit revision and records the served commit plus the file digest."""
    _, contract, reference = _macos_job_sections()
    for section in (contract, reference):
        assert "resolve/$MODEL_REVISION/$MODEL_FILE" in section
        assert "resolve/main/" not in section
        assert "x-repo-commit" in section
        assert 'shasum -a 256 "models/$MODEL_FILE"' in section
        assert "macos-logs/model-identity.txt" in section


def test_macos_reference_lane_flags_an_unpinned_revision():
    """Unpinned runs stay allowed — they just must not pass silently."""
    _, _, reference = _macos_job_sections()
    assert 'if [ "$MODEL_REVISION" = "main" ]' in reference
    assert "::warning::" in reference
    text = MACOS_WORKFLOW.read_text(encoding="utf-8")
    assert "model_revision:" in text


def test_macos_workflow_states_the_evidence_boundary():
    """CI runners are smaller and slower than the documented reference
    machine. The workflow must say so, so a green run is never cited as a
    local performance result."""
    text = MACOS_WORKFLOW.read_text(encoding="utf-8")
    assert "docs/PERFORMANCE.md" in text
    assert "performance evidence" in text
