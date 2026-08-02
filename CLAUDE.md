# CLAUDE.md

Repository guidance for Claude Code and other coding agents.

`CLAUDE.md` is the canonical instruction file. `AGENTS.md` must remain a relative
Git symlink to it (`AGENTS.md -> CLAUDE.md`) so Claude Code and Codex receive the
same guidance. Edit this file only; do not replace the symlink with a copy.

If this repository is vendored into another project, follow the parent project's
rules in addition to this file.

## Project

AskMe is an experimental, dependency-light Python coding-agent harness for
constrained local LLMs, with an OpenRouter backend for hosted models. The public
entry point remains `python3 askme.py`; the runtime is currently concentrated in
`askme.py`, but that is not a reason to keep enlarging its longest functions.
Preserve the simple CLI and compatibility surfaces while following the cohesive,
behavior-preserving extraction work tracked in the issue roadmap below.

AskMe executes model-generated shell commands with the launching user's host
permissions. It is **not a sandbox**. A temporary working directory organizes
files but does not confine shell commands, absolute paths, or traversal.
`ALLOW_SYSTEM_INSTALLS` and `ALLOW_NETWORK` are prompt-visible policy signals,
not operating-system enforcement.

Start with:

- [README.md](README.md) — quick start, supported surfaces, and test entry points
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — loop, state, actions, and current constraints
- [docs/configuration.md](docs/configuration.md) — environment variables and evaluation CLI
- [docs/SECURITY.md](docs/SECURITY.md) — execution boundary and safe-use guidance
- [docs/PERFORMANCE.md](docs/PERFORMANCE.md) — dated measurements, not timeless truth
- [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) — experiment conventions and backlog
- [tests/workflows/PROTOCOL.md](tests/workflows/PROTOCOL.md) — frozen native-evaluation contract
- [tests/featurebench/README.md](tests/featurebench/README.md) — qualified FeatureBench canary runbook

## Repository map

- `askme.py` — CLI, provider calls, planner/executor loop, action dispatch, recovery,
  validation, structured results, and the compatibility `run(...) -> bool` API
- `tests/test_agent_*.py` — deterministic unit and action/controller regression tests
- `tests/test_agent_integration.py` — local and OpenRouter integration suites
- `tests/workflow_eval.py`, `tests/test_workflow_*.py`, `tests/workflows/` — native
  semantic workflow evaluator, qualification tests, manifests, and fixtures
- `tests/featurebench/` — adapter, frozen protocols, audits, and historical result records
- `talks/berkeley-agentic-ai-summit-2026/` — slide source, rendered artifacts,
  speaker material, evidence, and deck contracts
- `.github/workflows/ci.yml` — hermetic Python 3.9/3.12 pytest matrix
- `.github/workflows/llm.yml` — credentialed, paid OpenRouter smoke/protocol jobs

## Commands

Run commands from the repository root.

```bash
# Inspect the supported CLI
python3 askme.py --help

# Run against a project that may be edited
python3 askme.py --working-dir /path/to/project "Fix the failing tests"

# Offline deterministic handoff gate; the empty key blocks `.env` key loading
OPENROUTER_API_KEY= python3 -m pytest tests/ -v -k \
  "not Integration and not ServerConfig and not (OpenRouter and not ThinkingRetry and not PlannerReasoning) and not PlannerReasoningOpenRouter"

# Common focused deterministic suites
python3 -m pytest tests/test_agent_actions.py -q
python3 -m pytest tests/test_workflow_eval.py tests/test_workflow_alternatives.py -q
```

The runtime dependency is `requests`; tests also require `pytest`. There is no
repository-wide formatter, linter, type-checker, or coverage gate yet, so do not
claim those checks ran. Keep Python 3.9 compatibility because CI explicitly
tests it.

Backend-dependent tests skip when their server or credential is unavailable.
A skipped integration suite is not evidence that a backend works. Run paid/live
model tests only when the change needs them and the required credentialed
environment is intentionally available.

The unfiltered full suite is safe in hermetic CI, which has neither backend. On
a developer machine it is explicit opt-in because `tests/conftest.py` enables
live integration classes when it finds a local server or valid OpenRouter key:

```bash
# Opt in only when backend calls and possible OpenRouter charges are intended
python3 -m pytest tests/ -v
```

## CI and credentials

- Keep `ci.yml` hermetic. It must never receive an OpenRouter key.
- `llm.yml` spends credits and may expose its key only to its intended jobs.
  Pull-request jobs must remain opt-in via `llm-tests` **and** restricted to
  same-repository branches before credentials or configuration fallbacks enter scope.
- Never print, persist, upload, or copy API keys into fixtures, logs, artifacts,
  issues, or PR text. Treat model responses and JSONL logs as potentially sensitive.
- Do not weaken a credential guard to make a fork or untrusted branch run.

## Engineering invariants

### Compatibility and minimalism

- Prefer the standard library and `requests`. Do not add a framework or runtime
  dependency without an explicit, measured need.
- Preserve the `askme.py` CLI, exit behavior, `run(...) -> bool`, `ask_llm(...)`
  compatibility, environment defaults, and `--result-json` schema unless the
  scoped change explicitly migrates them with contract tests and documentation.
- Token, context, step, output, and timeout bounds are product constraints.
  Do not remove or inflate them casually; local inference is deliberately slow.
- Treat `AGENT_REASONING_POLICY` and `OPENROUTER_REASONING_EFFORT` as separate
  axes. For always-on reasoners, `off` pins the declared baseline effort; it does
  not mean zero reasoning. Freeze and report that baseline in evaluations.
- Keep prompts and model-visible state compact. The planner receives a curated
  state summary—not full raw state—and the executor receives a smaller sliding
  view. Never leak raw write payloads or held-out evaluator evidence into prompts.

### Actions, state, and receipts

- Validate and normalize actions before side effects. In refactors, centralize
  `done` and `fail` as controller concerns rather than expanding executor ownership.
- Do not add workspace mutations—including deterministic recovery—that bypass the
  normal action/recording boundary. The existing C-header exception is tracked in
  #41; do not copy its direct-write or fabricated-receipt pattern.
- Preserve the meanings of **selected**, **executed**, and **skipped** steps.
  A failed or guard-suppressed mutation is not a successful commit.
- Scope task progress (`no_write_executed`, failed steps, validation pressure)
  to the current task. Keep unresolved incomplete-artifact obligations run-wide
  across task-local and full replans.
- Normalize target identities consistently across relative paths, directory
  symlinks, leaf-symlink operations, retries, duplicate detection, and recovery.

### Truncation and observation integrity

- A partial/truncated mutation is `incomplete_write`, never a complete or merely
  unvalidated artifact. Preserve an actionable resume boundary and target; do
  not append to stale content or allow `done` while an obligation remains.
- Preserve every complete byte/line at a token cutoff and test content that can
  resemble transport sentinels. Framing syntax must not silently eat file content.
- A bounded `read`, `search`, or `tree` result must not look complete when any
  line, character, file, match, entry, or depth cap was hit. Carry compact
  truncation reasons through the action result, model history, and JSONL record.
- Continuations must resume at the first unread content. Regression tests must
  reconstruct the original data without gaps or duplication, including long
  lines, wide multi-line windows, repeated continuation to EOF, and Unicode.

### Verification and acceptance

- Keep internal task verification, optional final LLM validation, and external
  held-out acceptance as distinct stages. Never return held-out evaluator evidence
  to the agent as recovery context.
- An explicit validator rejection cannot be erased by a later unavailable or
  malformed validator response. Require new successful mutation or shell evidence
  before a recheck can establish completion.
- Agent-reported completion is a claim, not proof. Record infrastructure validity,
  agent termination, patch production/application, visible tests, and independent
  acceptance separately.

## Testing expectations

- Reproduce a bug with a deterministic regression that fails before the fix.
  Assert the user-visible contract or reconstructed artifact, not only an internal
  flag, header, offset, or helper call.
- Compare the whole disposable workspace when claiming edits were limited to an
  intended file; extra files and modified tests must remain visible.
- Mock the provider/HTTP boundary for deterministic suites. Keep ordinary tests
  network-free, credential-free, and independent of a running model server.
- Run the narrowest relevant suite while iterating, then the offline deterministic
  handoff gate above. Treat the unfiltered full suite as explicit opt-in. Do not
  make a failure green by weakening assertions, swallowing errors, broadening
  skips, or deleting historical evidence.
- Update action docs, workflow contracts, structured schemas, and tests together
  when a public or model-visible contract changes.

## Evaluation and evidence discipline

- Register the protocol, execution revision/hash, model and provider route,
  budgets, controls, trial count, and decision rule **before** outcome-bearing
  model calls. A changed interface requires a new protocol, not a rewritten result.
- Requalify gold and harmless non-empty controls before inference. Preserve
  historical negative runs, malformed actions, exhaustion, timeouts, wrong
  artifacts, evaluator errors, and false completion as distinct evidence.
- Verify that a runbook is executable at the exact registered revision; do not
  point new protocols at setup instructions that still pin an older cell.
- Keep summaries arithmetically consistent with retained records and distinguish
  typed errors from related telemetry such as `finish_reason=length`.
- Use matched providers/configuration for causal comparisons and report variance
  for repeated live claims. A one-task or one-attempt canary is not an official
  benchmark score, reliability estimate, model-size/family conclusion, local
  performance result, or proof of general feature readiness.
- Treat [docs/PERFORMANCE.md](docs/PERFORMANCE.md) entries as dated evidence.
  Re-run before citing a number whose code, model, provider, or protocol changed.

## Architecture roadmap

Issue text describes intended work, not necessarily landed behavior. Check the
current issue and code state before implementing:

- [#30](https://github.com/den-run-ai/askme/issues/30) and
  [#42](https://github.com/den-run-ai/askme/issues/42) — repair observation
  integrity before freezing it behind a refactor
- [#31](https://github.com/den-run-ai/askme/issues/31) — evaluate an explicit
  inspect → modify → verify → finish lifecycle; do not assume it is already adopted
- [#36](https://github.com/den-run-ai/askme/issues/36) — cohesive action handlers,
  typed results/receipts, and one recorder
- [#37](https://github.com/den-run-ai/askme/issues/37) — separate provider
  transport, retry policy, and pure response decoding behind `ask_llm`
- [#38](https://github.com/den-run-ai/askme/issues/38) — remove, rather than
  extend or advertise, the obsolete manual slot-cache workaround
- [#40](https://github.com/den-run-ai/askme/issues/40) — add a reusable structured
  run API after the executor/client seams while retaining compatibility wrappers
- [#41](https://github.com/den-run-ai/askme/issues/41) — preregister an ablation
  before removing or retaining the benchmark-shaped C-header repair path

For architecture work, make a behavior-preserving extraction first. Avoid adding
more disconnected top-level helpers; group related state and behavior around a
clear seam, add characterization tests, and keep the dependency-free entry point.

## Talks and generated artifacts

- Use one canonical source for the spoken script. If full prose is duplicated,
  enforce synchronization rather than relying on manual edits.
- Keep claims observational and traceable to frozen records. Distinguish local
  Gemma 4 E4B (dense PLE) from hosted variants, and distinguish tool feedback
  visible to AskMe from post-run held-out scoring.
- When slide source changes, update the deck contract/spec deliberately, run the
  relevant contract tests, regenerate `slides.pdf` with the pinned Marp command,
  and visually inspect every changed page. Contract tests do not catch clipping,
  overlap, or unreadable density.

```bash
python3 -m pytest tests/test_talk_deck_contract.py -q
npx @marp-team/marp-cli@4.4.1 \
  talks/berkeley-agentic-ai-summit-2026/slides.md \
  --html --pdf --allow-local-files
```

## Change hygiene

- Read the relevant issue, nearby tests, and recent review threads before editing.
- Keep diffs focused. Preserve unrelated user changes and generated/frozen records.
- Update documentation when commands, defaults, schemas, or evidence boundaries change.
- Before merge, inspect every earlier review thread against the exact candidate
  head. Reply with the fixing commit and regression evidence, then resolve the
  thread; a later clean review does not implicitly close an older finding.
- In the PR description, state what changed, why, user/developer impact, and the
  exact checks run. Say explicitly when paid/live tests or visual checks were not run.
