# E89 — PR #89 web showcase suite on local Gemma 4 E4B (2026-08-04)

Raw records for the first local measurement of the T1 web-app family
(`TestWebLocal`). PR #89's own PERFORMANCE entry states "Local Gemma 4 E4B
remains unmeasured on this suite"; this closes that gap. Protocol as
registered before the first model call is in `PROTOCOL.md` (including both
mid-run amendments).

## Execution revision

Merge commit `4e528a6` on scratch branch `eval/e89-web-local`: PR #89 head
`56608b1` merged into tools-only `main` `fcd5bc0` (post #91/#92/#93). Only
`README.md` and `docs/PERFORMANCE.md` conflicted; runtime code and test code
merged clean. **PR #89 was validated on the pre-#92 JSON transport — this
evaluation is on tools-only, which is what the PR would land into.**

`git_dirty: true` in every `summary.json` is the one eval-only change:
`tests/bench_harness.py`'s hard-coded 1200 s per-trial cap became a
`BENCH_TRIAL_TIMEOUT` env override, because the stock cap cut runs off
mid-plan so the wall clock, not the agent's budgets, decided outcomes.
Not proposed upstream.

## Model and server

Local `llama-server`, `models/gemma4-e4b-qat/gemma-4-E4B_q4_0-it.gguf`
(alias `gemma-4-e4b`, served identity pinned and verified in every run):
`-ngl 99 --ctx-size 16384 --flash-attn on --cache-type-k q4_0
--cache-type-v q4_0 --swa-full --cache-reuse 256 --reasoning off -np 1`.
Reasoning policy `gated`, default heuristic step policy, tools transport.
Generation measured at ~12.8 tok/s.

## Results — 11 runs, 2 passes

### `shipped-profile/` — `legacy-e4b-m1-16k-v1` (step 256 / write 512)

The shipped local contract, as in E23/E25.

| Task | Trials | pytest | agent complete | Wall | Failure class |
|---|---|---|---|---|---|
| T1a status service | 1 | 0/1 | 0/1 | TIMEOUT >1200 s | write-truncation loop; 8 `app.py` rewrites, `test_app.py` never written |
| T1b notes service | 1 | 0/1 | 0/1 | 839 s | 6 of 7 executor steps `response_truncated`; one write ever landed |
| T1c repair | 4 (1+3) | 0/4 | 0/4 | 191–254 s | **done-emission loop** — see below |

T1a/T1b are one trial each: after the pilot established the truncation bind
on all three tasks, the planned 3-trial arm was stopped by owner decision
rather than spend ~2 h of the single-slot server re-confirming it
(PROTOCOL.md amendment 2). One trial is a health check, not a rate.

**T1c is the notable cell and it is consistent 4/4.** Every run landed
exactly one correct `edit` to `app.py`, ran `python3 test_app.py`
successfully (`HEALTH_OK`), and then re-ran that same passing command until
exhaustion — `[stuck_loop] same successful command repeated`. The
deliverable is correct in every run: held-out acceptance
(`assert_status_service`, fresh ephemeral port, real loopback HTTP) **passes
on every surviving workspace**, with the protected `test_app.py` unmodified.
The suite records these as failures solely because `result["status"] !=
"complete"`. This is the **inverse of a false completion** — a true
completion the agent could not claim — and it is the E23 done-emission loop
(EXPERIMENTS.md E20/E07) reproducing on a new task family.

### `raised-budget/` — `generic-feature-scale-v1` (step 4096 / write 8192)

**Non-shipping contract.** Diagnostic arm: same model, server, and prompts;
capability profile is the only changed axis. Answers "is the write budget
what fails these tasks?"

| Task | Trials | pytest | agent complete | Wall | Outcome |
|---|---|---|---|---|---|
| T1a status service | 1 | 0/1 | 0/1 | 2017 s | 163-line `test_app.py`; 2 hung smoke runs (254.8 s, 255.4 s); contract inversion |
| T1b notes service | 2 | 0/2 | 0/2 | 1841 s, 2039 s | 180-line `app.py`; `import requests` violates stdlib-only; first write escaped the workdir |
| T1c repair | 2 | **2/2** | **2/2** | 164 s, 150 s | clean: 0 replans, one `edit`, verified, `done` |

Truncation disappeared entirely (zero `finish_reason=length`), and each write
step dropped from 3 LLM calls (256 → 512 → retry ladder) to 1, and from
77–102 s to 28–45 s. The build tasks still failed — differently.

### `shipped-profile/t1c-lifecycle-3trials/` — step-policy arm (2026-08-06)

Follow-up on finding 2. `AGENT_STEP_POLICY=lifecycle` is the lever CLAUDE.md
names for the done-emission loop and the subject of open issue #31. Matched
against the `t1c-3trials` control: same revision, model, server, capability
profile, reasoning policy, budgets, and prompt — **step policy is the only
changed axis**, and `step_policy=lifecycle` is verified in all three
`run_start` records.

| Arm | pytest | agent complete | Held-out acceptance | Wall median (range) |
|---|---|---|---|---|
| `heuristic` (control, default) | 0/4 | 0/4 | 4/4 correct | 197.6 s (191–254) |
| `lifecycle` | **2/3** | **2/3** | **3/3 correct** | 335.5 s (288–528) |

Reading this honestly:

- **The direction is right but the sample is not decisive.** 2/3 vs 0/4 is
  Fisher one-sided p ≈ 0.14. Suggestive, not established. A decision on #31
  needs a preregistered protocol and more trials, not this cell.
- **The failure class did not disappear.** Trial 3 still ended
  `[stuck_loop] same successful command repeated` — the identical signature
  as the control. Lifecycle appears to reduce the loop's frequency, not
  remove it.
- **It costs wall time**: median 335.5 s vs 197.6 s, ~70% slower on a cell
  where the underlying repair takes ~90 s. Worth pricing before adoption.
- **Artifact correctness was never the variable.** All 7 runs across both
  arms produced a repaired service passing held-out acceptance with the
  protected test unmodified. The arms differ only in whether the agent
  managed to *claim* the completion.

## Findings

1. **The tasks are feasible within the shipped 512-token write budget; E4B's
   verbosity is not.** Tokenized on the serving model, the gold
   implementations cost 310 (T1a `app.py`), 369 (T1b `app.py`), and 408
   (a `test_app.py` of the required shape) tokens as tool-call arguments —
   all under 512. E4B instead writes 163–180 line files against the doc's
   ~35-line target. The budget is the first symptom, not the root cause;
   raising it removed the truncation and let the verbosity run unchecked,
   making the build failures worse rather than better.

2. **On tools transport a token-cut write is lost, not resumed.**
   `_decode_tool_call_reply` raises on unparseable tool-call arguments and
   always returns a default `ActionTransport()`; no live decode path sets
   `content_truncated`. So `_truncated_write` → `incomplete_write` →
   resume-anchor append recovery is unreachable from real model truncation —
   the only remaining producer is the injection seam at `askme.py:4502`,
   whose own comment says the real decoder rejects that key. The showcase
   doc anticipated that an oversize `app.py` would "exercise the resume-anchor
   append path"; under tools-only it cannot. Worth its own issue: this is an
   observation-integrity invariant with no live producer.

3. **Recurring contract violations under the larger budget**, both of which
   the visible smoke test catches but the agent does not recover from:
   `import requests` despite "using only the Python standard library", and a
   `test_app.py` that demands a port argument (`Usage: python3 test_app.py
   <port>`) instead of starting `app.py` itself on the fixed port.

4. **Hung smoke scripts are real, not hypothetical.** Two T1a shell steps ran
   254.8 s and 255.4 s — the generated script started the server and never
   terminated it, hitting the timeout escalation. This is the hazard
   `showcase-tasks.md` designs against and the same hang that hit gemma in
   PR #89's own round 2.

5. **Long absolute paths get mangled.** T1b's first write went to
   `<parent>/app.py` instead of `<workdir>/app.py` — the model copied the deep
   pytest tmp path from the prompt and dropped its final component, leaving a
   stray 64-line file outside the workspace. The same path confusion appears
   in the T1c pilot (`missing_file` on a parent-dir `cat`). Not a sandbox
   escape — AskMe honors absolute paths by design and documents that it is not
   a sandbox — but the T1 prompts embed a deep workdir path, which invites it.

## Limitations

- **Single-trial cells.** T1a shipped (1), T1b shipped (1), T1a raised (1).
  Only T1c is multi-trial (4 shipped, 2 raised). No cell here supports a
  reliability rate; E23 documented this task class flipping 13 s ↔ 226 s.
- The raised-budget arm is **not** the shipped local contract. Any claim
  about AskMe-as-shipped must cite `shipped-profile/`.
- T1c shipped (0/4) vs raised (2/2) is a clean split but confounds two
  changed values (`step_tokens` and `step_write_tokens`) in one named
  profile, and T1c never truncated — so the mechanism is not established.
- No matched control on another model or on the pre-#92 JSON transport, so
  none of this decomposes across model / transport / profile.
- Wall times share one machine with the persistent llama-server (1 slot) and
  are not isolated from host load.
