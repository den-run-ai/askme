# Testing and CI

Run these commands from the repository root after following the
[quick start](../README.md#quick-start). Ordinary tests do not call a model.

## Offline checks

```bash
uv sync --locked
uv run --locked ruff check *.py tests
uv run --locked ruff format --check *.py tests
uv run --locked ty check
uv run --locked pytest tests/ -q
```

For the CI-equivalent, branch-aware coverage gate:

```bash
uv run --locked pytest tests/ --cov --cov-report=term-missing --cov-report=xml:coverage.xml
```

Native semantic-workflow qualification also runs without a model:

```bash
uv run --locked pytest \
  tests/test_workflow_eval.py tests/test_workflow_alternatives.py -q
uv run --locked python tests/workflow_eval.py \
  tests/workflows/config_precedence/manifest.json --agent noop
```

## Live integration tests

Live tests require `ASKME_RUN_LIVE_LLM_TESTS=1` and skip when their server or
credential is unavailable. A skipped suite does not verify the backend.
OpenRouter calls spend credits; ordinary test and coverage commands do not
opt in. Run only the suite you need.

Local suites require `llama-server` on port 8080:

```bash
# Easy
ASKME_RUN_LIVE_LLM_TESTS=1 uv run --locked pytest tests/test_agent_integration.py -s -v -m live_llm -k "TestIntegration and not Medium and not Hard"
# Medium
ASKME_RUN_LIVE_LLM_TESTS=1 uv run --locked pytest tests/test_agent_integration.py -s -v -m live_llm -k "IntegrationMedium"
# Hard
ASKME_RUN_LIVE_LLM_TESTS=1 uv run --locked pytest tests/test_agent_integration.py -s -v -m live_llm -k "IntegrationHard"
```

OpenRouter suites require `OPENROUTER_API_KEY` in the environment or `.env`;
see [backend setup](configuration.md#openrouter):

```bash
ASKME_RUN_LIVE_LLM_TESTS=1 uv run --locked pytest tests/test_agent_integration.py -s -v -m live_llm -k "TestOpenRouterEasy or TestOpenRouterMedium or TestOpenRouterHard"
```

[Showcase web-app tasks](showcase-tasks.md) have separate suites:

```bash
ASKME_RUN_LIVE_LLM_TESTS=1 uv run --locked pytest tests/test_agent_integration.py -s -v -m live_llm -k "TestWebLocal"
ASKME_RUN_LIVE_LLM_TESTS=1 uv run --locked pytest tests/test_agent_integration.py -s -v -m live_llm -k "TestOpenRouterWeb"
```

## Repeated benchmarks

The benchmark harness reports the median and range across trials. Listing
available tests does not call a model:

```bash
uv run --locked python tests/bench_harness.py --list
```

To run the local Gemma reference after serving qualification:

```bash
uv run --locked python tests/bench_harness.py \
  --model gemma-4-e4b \
  --capability-profile legacy-e4b-m1-16k-v1 \
  --expected-served-model gemma-4-e4b
```

Register the model, provider, profile, budgets, trial count, and decision rule
before collecting outcomes; see [contributor guidance](../CLAUDE.md#evaluation-and-evidence-discipline).
Dated run matrices and suite-size snapshots live in [PERFORMANCE.md](PERFORMANCE.md).

## Coverage reports

The README coverage badge shows Codecov's latest `main` percentage to two
decimal places and opens the detailed report. The Python 3.14 CI run also writes
coverage.py's exact branch-aware table to the GitHub job summary and stores
browsable HTML plus JSON/XML reports in the `coverage-python-3.14` artifact for
14 days. Codecov [counts partially covered lines as
misses](https://docs.codecov.com/docs/frequently-asked-questions#how-is-coverage-calculated),
so its badge can differ from coverage.py's execution-opportunity percentage
that enforces the 90% CI gate.

## LLM tests in CI

Separate GitHub Actions workflows handle hermetic and hosted-model testing;
local Apple Silicon lanes are described below:

- [`ci.yml`](../.github/workflows/ci.yml) — locked uv environments, Ruff lint and
  formatting, ty type checking, Python 3.10–3.14 tests, and a 90% branch-aware
  coverage floor on every push/PR, with the Python 3.14 report published to
  GitHub and Codecov. It deliberately has no OpenRouter credential, so
  backend-gated suites auto-skip (guarded by `tests/test_ci_workflows_contract.py`).
- [`llm.yml`](../.github/workflows/llm.yml) — OpenRouter-backed tests, using the
  repository's `Openrouter` deployment environment for `OPENROUTER_API_KEY`
  as an environment secret. The key is scoped only to preflight and live-model
  execution steps. Runs on push to `main` touching agent/test/dependency code,
  weekly on schedule, on manual dispatch (choose suite, smoke-model matrix,
  Berkeley models, provider, trials), and on pull requests only when labeled
  `llm-tests` — the job guard
  also requires the PR head branch to live in this repository, so labeled fork
  PRs are rejected before any credential is in scope.

The default CI model is **Qwen 3.6 27B** (`qwen/qwen3.6-27b`). Benchmark
cells pin the expected served identity to `qwen/qwen3.6-27b-20260422`.
This CI selection does not change AskMe's runtime model default. Manual
`smoke_models`, `models`, and `web_models` inputs can still select Gemma or
other models; [the CI investigation](llm-ci-investigation.md) explains the
change and records how to rerun the earlier matrix.

`llm.yml` has three jobs. The smoke job runs an OpenRouter pytest suite (easy
by default) with automatic provider routing, once per model in the
`smoke_models` matrix. The dispatch-only `web-bench-trials` job (opt-in via a
nonzero `web_trials` input) benches every `web_models` × web-task cell that
many times through `tests/bench_harness.py` for median+range evidence. Web
cells use `requested=expected-served@effort` syntax and the immutable
`generic-feature-scale-v1` capability profile; an empty `web_models` input
inherits the `models` matrix. The legacy-named Berkeley job runs the same
hard-build and medium-repair selectors used by
[the talk's frozen eval protocol](../talks/berkeley-agentic-ai-summit-2026/evals/README.md)
on current code and current model cells; it is not a replay of that historical
four-model, strict-SiliconFlow matrix. `tests/ci_llm_gate.py report` then
evaluates the smoke pass rule (every
trial: pytest pass and agent completion) and publishes a summary table. Because
a cell separately pins its requested model, immutable capability profile, and
exact expected served identity, requested/served-model/profile drift and
missing configuration hashes are evidence-integrity failures rather than model
outcomes. Provider drift is checked only when a provider is explicitly pinned. Since a
single unseeded live-model trial is not a reliability estimate, valid
Berkeley-cell outcome failures are advisory on post-merge `main` pushes;
malformed or missing evidence remains blocking, and scheduled, manual, and
opt-in PR runs remain strict. JSONL run logs, bounded pytest failure diagnostics,
and `summary.json` files are uploaded as artifacts. A
preflight step fails loudly when the key is missing or rejected, so a bad
credential can never produce a silently green (all-skipped) run. Live-call cost
depends on the chosen models, tasks, trial count, and current provider pricing.

### Diagnosing a red LLM badge

Open the failed job from the README badge and start with the first failing
step. Authentication, provider availability, and task success are different
checks:

| Failure | What to inspect |
|---|---|
| Missing or rejected key during preflight | The `OPENROUTER_API_KEY` secret in the job's `Openrouter` environment; never print the value |
| Provider request fails | The HTTP status and bounded diagnostics; an authenticated key alone does not guarantee model access or a successful generation |
| Smoke pytest fails | The failed assertion and that model's JSONL log; repeated actions or exhausted steps can be genuine model task failures |
| Benchmark report rejects a cell | `summary.json`, the expected served identity/profile, and configuration hashes; missing or mismatched evidence is not a model outcome |
| All live tests skip | Check the explicit live-test opt-in and backend availability; CI additionally requires nonempty run logs |

Retain failed records when comparing revisions. A rerun can reveal model
variability, but a passing rerun alone does not identify or fix a regression.

## macOS Apple Silicon CI

[`macos.yml`](../.github/workflows/macos.yml) covers the platform the local
llama.cpp backend actually targets. It needs no credential — every lane talks
either to nothing at all or to a `llama-server` on localhost — and has three
lanes because they buy different things:

| Lane | Runner | Trigger | Gates? |
|---|---|---|---|
| `macos-tests` | `macos-26` (free) | push, PR | yes |
| `llama-contract` | `macos-26` (free) | push, PR, weekly | server preflight gates; model probe advisory |
| `llama-reference` | `${{ inputs.runner }}` | manual only | yes, including a runner-size floor |

`macos-tests` runs the deterministic suite on arm64 for Python 3.10 and 3.14 —
the ends of the supported range, since `ci.yml` already covers the middle on
Linux. This is the lane that catches genuine platform bugs: macOS resolves
`/tmp` and `/var` through symlinks into `/private`, and its default filesystem
is case-insensitive, both of which bear directly on workspace-path and
target-identity normalization.

`llama-contract` installs llama.cpp from Homebrew, serves a deliberately tiny
tool-capable model (Qwen3-0.6B), and checks the seam through the real
`LLMClient`. It exists to catch upstream llama.cpp drift breaking the local
backend. `tests/ci_local_gate.py preflight` gates: `tests/conftest.py` *skips*
local suites when `:8080` is absent, so without it a broken backend would read
as a green run. The `probe` step — one `expect="action"` round trip asserting
the tools payload is accepted and the envelope decodes — stays advisory,
because a 0.6B model on a 3-vCPU runner missing a tool call is evidence about
the model, not about AskMe.

`llama-reference` runs the documented reference model (Gemma 4 E4B QAT Q4_0,
5.15 GB) with the exact stable flags from [docs/gemma4-setup.md](gemma4-setup.md).
It is manual-only and takes a runner label, because **no GitHub-hosted runner
matches the 16 GB reference machine**:

| Label | Chip | vCPU | RAM | Cost |
|---|---|---|---|---|
| `macos-26` | M1 | 3 | 7 GB | free/unlimited on public repos |
| `macos-26-xlarge` | M2 Pro | 5 | 14 GB | billed per-minute, org-owned repos only |
| `macos-26-large` | Intel | 12 | 30 GB | not Apple Silicon — no Metal |

14 GB is short of the reference 16 GB but still holds the model plus its 16K
q4_0 KV cache, so `macos-26-xlarge` is the default. For true 16 GB+ parity,
point `runner` at a third-party arm64 label (Depot, WarpBuild, Blaze, Bitrise)
or a self-hosted Tart/Tartelet runner — all are one-line swaps — and set
`min_memory_gb` to `16`. `tests/ci_local_gate.py hardware` then verifies the
machine really is that size *before* the multi-gigabyte download, so an
undersized runner fails fast instead of being OOM-killed mid-suite in a way
that looks like an agent-loop bug.

**These lanes are not performance evidence.** CI runners are smaller and slower
than the documented M1/16 GB reference deployment, and a run that clears a
lane's floor while sitting below 16 GB says so explicitly in its job summary.
Timings from this workflow must never be written into
[docs/PERFORMANCE.md](PERFORMANCE.md) or cited as a local baseline; use the
reference machine for that.
