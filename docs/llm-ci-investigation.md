# LLM CI failure investigation — 2026-09-14

The red LLM badge on `main` was caused by a live Gemma task failing, not a
missing OpenRouter key or a broken GitHub runner. Ordinary CI and macOS checks
passed at the inspected main revision, `7ef018e4561ed7b2cfb7d0006a1e1c2968ae37a9`.

## What failed and when

| Evidence | Result |
| --- | --- |
| [Last green main LLM run](https://github.com/den-run-ai/askme/actions/runs/30887026678), August 4, `eee3fb71` | Easy smoke passed, with replanning already needed. |
| [First persistent failure](https://github.com/den-run-ai/askme/actions/runs/30925437531), August 4, `fcd5bc07` | Gemma repeated successful writes instead of completing the task. |
| [Latest inspected main run](https://github.com/den-run-ai/askme/actions/runs/34234968980), September 8, `7ef018e` | Easy smoke: one failed, two passed. Qwen benchmark cells: two passed. Gemma benchmark cells: two failed. |

The failing easy test asked for `hello.txt` containing `hello world`, followed
by reading it back. Gemma wrote the file, repeated the same write (suppressed
by the duplicate-action guard), and issued redundant directory commands. It
never selected `read` or `done`, then exhausted the existing step/replan budget.
Successful individual actions explain the empty action-error list; they do not
establish task completion.

The change between the last green revision and the first persistent failure
contains only `docs/PERFORMANCE.md` and retained benchmark records:

```bash
git diff --name-only eee3fb71 fcd5bc07
```

Weekly failures continued on the same `fcd5bc07` revision, before the September
runtime refactors. The preceding material change was the
[native-tools-only migration](https://github.com/den-run-ai/askme/commit/5e59dd6b0f3d19a03b1925ff0ca7f9c91e7b197c)
in PR #92. That is a possible sensitivity point, but the first main run after
it passed. These observations do not prove a causal runtime regression or a
provider change. The
[retained September 8 audit](../tests/bench_records/2026-09-08-main-smoke-461fdf0/README.md)
also shows valid native responses without an HTTP, parsing, or runtime error.

## Changes

- Use `qwen/qwen3.6-27b` as the default CI model, with the exact expected served
  identity `qwen/qwen3.6-27b-20260422` in benchmark cells. This is a CI model
  selection change, not a fix for Gemma's completion behavior. The normal
  application model defaults remain unchanged.
- Keep the same task assertions, budgets, completion requirements, evidence
  checks, and strict/advisory rules. There are no pass-on-retry changes or new
  skips. Historical Gemma failures remain available and unchanged.
- Authenticate the preflight through OpenRouter's `/api/v1/key` endpoint.
  The old public `/models` endpoint could not prove that a key was valid.
  Actual successful paid calls establish that the key was available in the
  inspected runs; this preflight defect was separate from the red badge.
- Keep credentials in the existing `Openrouter` environment secret and omit
  response bodies and exception details from preflight messages.

Qwen passed both benchmark cells at the inspected main revision, making it a
candidate for the CI default. Earlier runs also contain Qwen task failures;
this change does not claim deterministic model behavior or a reliability score.
The PR's live checks qualify the selected model on the changed revision.

## Re-run Gemma or the previous matrix

From **Actions → LLM Tests → Run workflow**, use these inputs:

| Input | Value |
| --- | --- |
| `suite` | `easy` |
| `smoke_models` | `google/gemma-4-26b-a4b-it` |
| `models` | `google/gemma-4-26b-a4b-it=google/gemma-4-26b-a4b-it-20260403,qwen/qwen3.6-27b=qwen/qwen3.6-27b-20260422` |
| `provider` | `auto` |
| `trials` | `1` |
| `web_trials` | `0` |

Manual runs remain strict: a valid failed task still fails the gate. Increase
`trials` explicitly for repeated evaluation; a single run is not a reliability
estimate. See [Testing and CI](testing.md) for local commands and artifacts.
