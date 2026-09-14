# Public release readiness — September 14, 2026

The audited source at [`3ec477f`](https://github.com/den-run-ai/askme/commit/3ec477fc06cfd48bf6920c58974a60788847f5bb)
is suitable for an **experimental, source-only research preview**. This pass
found no new reproducible runtime blocker. It does not establish reliable
autonomous coding or approve a later, unreviewed release commit.

## Verified baseline

| Check | Result |
|---|---|
| Clean source quick start | Installed the documented uv 0.12.1 in a separate location; `uv run --locked --no-dev askme.py --help` created a fresh environment and exited successfully without a model call. |
| Locked development setup | `uv sync --locked` succeeded on Linux / Python 3.12.14. |
| Static checks | Ruff lint, Ruff format (106 files), and ty passed. |
| Deterministic suite | 1,813 passed; 30 expected live-model skips; 96.89% branch-aware coverage locally. |
| Main Linux CI | [All five Python versions, 3.10–3.14, passed](https://github.com/den-run-ai/askme/actions/runs/34808716539). Python 3.14 reported 96.77% branch-aware coverage, above the 90% gate. |
| Main macOS CI | [Passed](https://github.com/den-run-ai/askme/actions/runs/34808716525), including the local-server contract. |
| Hosted smoke | [Three easy cases passed](https://github.com/den-run-ai/askme/actions/runs/34808716557/job/103865605030) with the current Qwen 3.6 27B CI selection. |
| Hosted build/repair checks | [Both cells passed pytest, agent completion, and the evidence contract](https://github.com/den-run-ai/askme/actions/runs/34808716557/job/103865605171); the green job was not merely an advisory outcome failure. |
| Secrets scan | Gitleaks 8.30.1: zero findings in all fetched Git history and an exact tracked export with archive traversal enabled. See the [machine-readable record](releases/2026-09-14-readiness.json). |
| Public surface | GitHub reports a public, active repository, enabled issues, and an MIT license. Source-only distribution and the host execution boundary are explicit in the README and security guide. |

The hosted build took 35.4 seconds and repair took 67.2 seconds, each in one
attempt, with automatic routing to Chutes. Their reported API costs were
$0.00232 and $0.00557. These are current integration observations, not a
replacement for the frozen four-model Berkeley matrix or a reliability estimate.
No additional paid calls were made for this release audit.

The documented uv pin matters: this environment initially had uv 0.12.11,
which correctly rejected the project requirement. Installing 0.12.1 as the
quick start instructs resolved it; no dependency or version constraint was
weakened. Local and CI coverage percentages are reported separately because
Python versions produce different measured statement counts.

## Before formal publication

- Review and verify the actual final candidate after the README, slide, and
  evaluation changes. This baseline audit does not scan or test future edits;
  scan any new inference artifacts before committing them.
- [Release tracker #85](https://github.com/den-run-ai/askme/issues/85) still asks
  for a signed-in non-collaborator to reach the **New issue** flow. Public issues
  are enabled, but an independent account was unavailable for that permission
  check. The prior [logged-out audience-link audit](releases/2026-09-08-public-access.json)
  remains dated evidence; it was not repeated in this pass.
- Select the reviewed commit and deliberately create the `v0.1.0` tag and
  GitHub Release using the [prepared notes](releases/v0.1.0.md), updated for
  the selected candidate. No tag or GitHub Release was created by this audit.

## Disclosed limitations, not hidden launch gates

AskMe executes generated shell commands with the launching user's permissions;
it is [not a sandbox](SECURITY.md). Whole-run resource budgets/cancellation
([#77](https://github.com/den-run-ai/askme/issues/77)) and live truncated-write
recovery ([#94](https://github.com/den-run-ai/askme/issues/94)) remain open.
Completion, a useful patch, and independent task acceptance remain separate
results. Historical negative evidence stays published in [PERFORMANCE.md](PERFORMANCE.md).

The architecture, dependency-injection migration, broader ablation panel, and
PyPI packaging backlog do not block a source-only research preview. Claims of
reliability, general feature readiness, or a causal advantage over another
harness require the corresponding evaluations; a green test badge is not that
evidence.

## Qwen model choice

Keep Qwen3.6-27B in the Berkeley matrix and current documented configuration.
[Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B) is a newer dense model,
and its [OpenRouter route](https://openrouter.ai/qwen/qwen3.8-27b) supports tools.
Its listed CoreWeave price on September 14 was $0.40 per million input tokens
and $3.00 per million output tokens. A bounded follow-up is financially
practical, but no AskMe result yet establishes a performance improvement.

Evaluate it as a separately dated model row before changing defaults. Preserve
the original four-model results, qualify the provider and reasoning settings,
and prioritize completing the matched AskMe/pi comparison within the available
$10 budget. A model upgrade is optional for the research preview.
