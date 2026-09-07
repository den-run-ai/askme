# AskMe release-readiness audit — export bundle

Produced 2026-08-30 against `den-run-ai/askme` `main` @ `fcd5bc0`, for the public blog post /
release now that the Berkeley Agentic AI Summit talk is live
(https://www.youtube.com/watch?v=N1XoiJGyNpM).

## Contents

| file | what it is |
|---|---|
| `askme-release-readiness.md` | the report, Markdown — paste-able into issues, PRs, or a blog draft |
| `askme-release-readiness.html` | the same report, rendered (open in a browser; light/dark aware) |
| `research/01-issue-pr-rollup.json` | merged triage: release blockers, 12 dispositions, recommended order, corrections where code contradicts issue text, blog material, gaps |
| `research/02-issue-pr-unit-reports.json` | the 12 underlying per-issue / per-PR reports (8 issue groups, 4 PR groups), each with `landed_vs_claimed`, evidence, and an honest `not_checked` list |
| `research/03-subsystem-audits.json` | 6 subsystem audits: completion-path, dead-code, evidence, talk-blog, ci-repro, tests-gates — 66 findings with file:line evidence |
| `research/04-dispositions.csv` | the 12 issue/PR dispositions, flat |
| `research/05-subsystem-findings.csv` | all 66 subsystem findings, flat (area, severity, category, title, detail, evidence, next step) |
| `research/06-run-140-trials5.md` | durable record of the `trials=5` OpenRouter run dispatched during the audit — gate table, per-trial detail, and the smoke log showing #95 reproducing on a hosted model. The GitHub artifact expires 2026-09-13. |
| `research/07-first-pass-schema-failure.md` | why the first audit workflow lost 4 agents: valid payloads rejected by a strict output schema — noted because it is the same failure shape as issue #95 |

## Verification basis

Re-verified directly in the audit container (not delegated):

- deterministic suite: `1208 passed, 24 skipped` (Linux, Python 3.11, fresh venv — `uv run`
  fails here because `pyproject.toml` pins `uv ==0.12.1`)
- branch coverage: `96.61%` on `askme.py` + `actions.py` (gate 90%)
- `git diff eee3fb7 fcd5bc0 -- askme.py actions.py` is empty (last-green vs all-red LLM CI)
- the local model identity (`docs/gemma4-setup.md:20-29`): dense E4B; no small Gemma 4 MoE exists
- issue #94's mechanism: no live decode path sets `content_truncated=True` (`askme.py:1117`, `:1125`, `:4498-4502`)
- `SPEAKER_NOTES.md` lines 13/24/32/62 and `slides.md` lines 480/679 as quoted
- `llm.yml` run 135 and run 140 job logs

Everything else came from 19 independent audit agents (13 triage + 6 subsystem), each required
to ground every finding in a file path, issue/PR number, sha, CI run id, or records directory.
Their raw returns are the `research/*.json` files; the report was synthesized from them plus the
direct checks above.

## What was spent

- One OpenRouter dispatch: `llm.yml` run 140 (`33295808202`), `trials=5`, both default pinned
  model cells, provider `auto` — about $0.12 total.
- No local (M1) model runs. No paid runs other than the above.

## Not done

- No source, doc, or test file in the repository was modified.
- Nothing was posted to GitHub issues or PRs.
- The macOS reference lane (PR #96 `llama-reference`) was not dispatched: it requires PR #96
  merged first and a runner this user-owned repo can allocate.
