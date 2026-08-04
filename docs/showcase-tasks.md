# Showcase tasks for small local LLMs — proposal

Status: T1 implemented as the opt-in `web` suite (2026-08-04, issue #85
release preparation) — offline fixture qualification in
`tests/test_webapp_showcase.py`, live tests in `TestWebLocal` /
`TestOpenRouterWeb` (`tests/test_agent_integration.py`), suite wiring in
`tests/bench_harness.py` and the `llm.yml` smoke job. T2–T4 remain
proposals. This document records no model outcomes; a single CI run of the
suite is a health check, not a result.

## Why a new task family

Every current integration task ends at "a program printed a string once":
create/read files, compile a C file, repair a syntax error or missing
include, recover from a missing dependency. Nothing makes the model deliver
a *running system* and then prove that system's behavior over a protocol.
For the v0.1.0 research-preview story — a small model on a laptop doing real
coding work, even offline — the most convincing minimal demo is:

> **Build a tiny service, then test it end-to-end. All loopback, all
> stdlib, all local.**

Loopback HTTP needs no external network, so the demo is consistent with the
offline ("on a plane") motivation and with `ALLOW_NETWORK` remaining a
prompt-visible signal. The deliverable is behavior, not text: held-out
acceptance can drive the service itself instead of grepping a file.

Target models are the ones the project already measures: local
**Gemma 4 E4B QAT Q4_0** on `llama-server` (the E23 primary; fits 16–18 GB
unified memory), with hosted OpenRouter routes for CI. Among the hosted
routes, `openai/gpt-oss-20b` is the closest stand-in for the local model —
same ~4B-active-parameter class as E4B, ~2.4× cheaper per token than the
current `google/gemma-4-26b-a4b-it` control, and already wired for
effort-pinned `model@effort` cells in `llm.yml` by PR #43 (experiment E21).
E21's live adoption cells are still pending its predeclared decision rule,
so until they run, treat gpt-oss-20b as the preferred *representativeness*
pick and Gemma 26B-A4B as the incumbent control; the FeatureBench Qwen
3.6-class cells remain the third comparison point.

## Harness constraints that shape every design

These are current-code constraints, not preferences:

- **A server must never run in the foreground of a `shell` step.** Shell
  actions time out at 30 s (default) / 120 s (build-family) / 300 s (hard
  cap, `actions.py`). Every end-to-end check is therefore a *self-
  terminating script*: it starts the app itself (subprocess or thread),
  probes it over loopback, prints a sentinel, stops the server, and exits
  0/1 — one bounded `shell` action.
- **Local write budget is small.** `STEP_WRITE_TOKENS` is 512 on the local
  backend (8192 on OpenRouter), so each file the agent writes should target
  roughly ≤ 35 lines. Prefer two small files (`app.py`, `test_app.py`) over
  one large one; anything larger deliberately exercises the
  `incomplete_write`/append-resume machinery instead of the happy path.
- **Port 8080 is taken.** The reference local setup runs `llama-server` on
  `:8080`; tasks must pin a different loopback port (e.g. 8765) and the app
  must accept the port as its first CLI argument so held-out evaluation can
  rebind it.
- **Budgets follow the existing ladder.** Easy 1/3/5, medium 1/3/8, hard
  2/5/8 (`max_replans`/`max_tasks`/`max_steps`, `tests/_test_support.py`);
  runtime defaults are 3/10/10.
- **Known small-model failure modes to watch** (E23 evidence in
  `EXPERIMENTS.md`): done-emission loops (work correct, `done` never
  emitted) and content drift on whole-file rewrites. Mitigations below:
  every test prints a unique sentinel so "finish when it prints X" gives
  crisp completion evidence, and acceptance pins exact sentinel strings so
  drift fails deterministically.

## T1 — flagship: loopback micro-service, built and proven end-to-end

Shared contract: stdlib only (`http.server`, `urllib`, `json`,
`subprocess`); `app.py` takes the port as `argv[1]`; the visible smoke test
is agent-visible feedback, while held-out acceptance launches `app.py` on a
*fresh* port with its own client and never enters the workspace.

### T1a (easy-medium) — build a status service

Prompt sketch:

> In {dir}: create `app.py`, an HTTP server using only the Python standard
> library. It takes a port as its first argument and serves `GET /` with
> plain text `MICRO_OK` and `GET /health` with JSON `{"status": "ok"}`.
> Then create `test_app.py`: it starts `app.py` on port 8765 as a
> subprocess, requests both routes with urllib, prints `WEBAPP_OK` and
> exits 0 if both match, and always stops the server. Run
> `python3 test_app.py` and finish when it prints `WEBAPP_OK`.

Budgets: medium shape (1/3/8). Real actions: write, write, shell — the
same arity as `multi_step_build`, so the step budget is known-feasible
territory; the novelty is coordinating two files against one runtime
contract.

Held-out acceptance (behavioral, deterministic): launch `app.py <port>` on
a fresh port; `GET /` returns 200 with body containing `MICRO_OK`;
`GET /health` returns 200 with parseable JSON whose `status` equals `ok`;
process terminates cleanly on kill. Nothing beyond what the prompt
declares.

### T1b (medium-hard) — state round-trip

Prompt sketch: same contract, but the service stores notes in memory:
`POST /notes` with a text body appends a note and returns 201; `GET /notes`
returns all notes, one per line, in insertion order. The smoke test POSTs
`alpha` and `beta`, GETs `/notes`, verifies order, prints `NOTES_OK`.

This is the smallest task in the suite where the deliverable has *state
over time* — a property no current integration test exercises. Budgets:
hard shape (2/5/8). `app.py` will flirt with the 512-token local write
budget by design; if it overflows, the run becomes a live exercise of the
resume-anchor append path rather than a failure.

### T1c (repair) — fix the failing service test

The seeded-defect variant, shaped exactly like a Phase-2 native workflow
manifest (`tests/workflows/PROTOCOL.md`): seed a *working* notes app plus
smoke test with one planted semantic defect — e.g. `GET /health` reports
`{"status": "down"}`, or the notes listing drops the first entry via an
off-by-one slice. Protected visible regression (server starts; `GET /`
returns 200) passes on the seed; protected visible feedback
(`python3 test_app.py`) fails on it. Prompt: run the test, fix `app.py`
only, do not edit `test_app.py`, finish when the test prints its sentinel.

This is the strongest evaluation candidate of the family because it slots
into the existing outcome taxonomy (`clean_success` … `invalid_run`)
without inventing any new machinery, and repair-on-feedback is the loop the
harness was built around.

## Companion families

- **T2 — CLI tool with persistence across invocations.** Build `todo.py`
  (stdlib argparse or plain `sys.argv`): `add TEXT` appends to
  `todo.json`, `list` prints numbered entries. The smoke check runs three
  *separate* process invocations. Novelty: no current task requires state
  to survive across process boundaries. Easy budgets; a natural first rung
  below T1a.
- **T3 — long-file surgery.** Seed a ~300-line `config.py` (mostly inert
  constants) plus a protected `check_config.py` that names exactly one
  wrong constant and prints `CONFIG_OK` when fixed. The file exceeds one
  bounded read window, so the run must use ranged reads/continuations or
  `search` — the first integration-grade exercise of the protocol
  revision-5 observation machinery, which today is only covered offline.
- **T4 — search-driven rename.** Seed three ~15-line modules and a
  protected runner; renaming one function consistently across files is the
  goal. This finally produces the integration-side signal the E04
  `search`/`tree` entry lists as "still pending."

## What these tasks are not (claim hygiene)

- They are **synthetic fixtures**. They do not satisfy the issue #85
  must-have of a predeclared *external real-repository* run, and the
  announcement must not present them as one.
- A passing demo is a **health check**, not a reliability estimate. Any
  cited number requires the E01 3-trial harness, a registered
  protocol/revision/model/route *before* outcome-bearing calls, and
  publication of every trial including failures.
- "Loopback micro-service" is the honest name. Not "web app users can
  deploy"; the security posture is unchanged (`SECURITY.md` — AskMe is not
  a sandbox, and the smoke tests execute model-written code on the host).

## Landing path

1. **Offline qualification first (no model calls).** Landed:
   `tests/test_webapp_showcase.py` runs the gold and no-op controls
   deterministically on every CI Python — the reference implementations
   pass held-out acceptance, the T1c seed fails both visible feedback and
   acceptance, and the intended one-value fix flips it to passing.
2. **T1 as opt-in live integration tests.** Landed: `TestWebLocal` and
   `TestOpenRouterWeb` in `tests/test_agent_integration.py` (marker
   `live_llm`, skip-by-default), selectable as the `web` suite in
   `tests/bench_harness.py` for 3-trial medians — in CI via the
   dispatch-only `web-bench-trials` job (`web_trials` ≥ 1 in `llm.yml`).
3. **T1c as a new workflow fixture** (e.g. `tests/workflows/notes_health/`)
   registered additively under the frozen protocol's versioning rules — a
   new phase and manifest, not a rewrite of Phase 1. Still proposed.
4. **Hosted smoke on demand.** Landed as a dispatch choice: `llm.yml`'s
   smoke job accepts `suite: web` plus a `smoke_models` matrix — one full
   suite per model per dispatch (never hermetic `ci.yml`). Following the
   E21 matrix pattern, pair the `google/gemma-4-26b-a4b-it` control with
   small-active-parameter peers (`openai/gpt-oss-20b@low`,
   `qwen/qwen3.6-35b-a3b`) so the CI signal stays in the same
   active-parameter class as the local E4B target.

Pre-registration checklist before the first outcome-bearing run: budgets
and prompts frozen (prompt within the goal-context cap), port ≠ 8080
verified, model/provider route and revision hash recorded, trial count and
decision rule declared, gold and no-op controls requalified.
