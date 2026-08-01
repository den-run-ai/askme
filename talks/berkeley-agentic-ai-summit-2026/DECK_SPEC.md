# Berkeley talk deck contract

**Status:** reviewer-facing source of truth for seven main slides plus one backup

**Last updated:** 2026-08-01

**Purpose:** prevent narrative and evidence drift between slide revisions

Review this file before changing `slides.md`, `blog.md`, or the rendered PDF.
When feedback conflicts, the newest explicit instruction wins and the change is
recorded here before the slides are edited.

## Identity and framing

- **Exact title:** Are Small LLMs Ready for Coding Agents?
- **Speaker:** Denis Akhiyarov
- **Role:** Sr Staff Research Scientist at ServiceNow
- **Social:** [@den-run-ai](https://x.com/den-run-ai)
- **Project:** [github.com/den-run-ai/askme](https://github.com/den-run-ai/askme)
- **Venue/date:** Agentic AI Summit 2026, UC Berkeley, Aug 1, 2026
- **Format:** five minutes, seven main slides plus one backup slide

The title is a question. The deck presents a design direction and bounded
evidence; it does not claim that the current smoke test settles the question.
Here, "small" is an engineering and deployment class rather than a fixed
parameter threshold. The hosted receipts span roughly 3–4B-active MoE models
and 27–31B dense models; they are not evidence of local-device performance.

## Central message

Smaller open models are attractive because they offer more control over
execution speed, hardware, deployment, access to post-training, and the builder
ecosystem. Coding agents are expanding toward general-purpose, full-lifecycle
workflow agents. A tight harness connects those trends by giving the model a
small action surface, returning fresh execution or test evidence, preserving
completed work, and checking the delivered workflow independently.

Define AskMe on slide 2 before using its name as shorthand: it is an
experimental coding-agent harness that keeps an explicit plan, asks the model
for one structured action per turn, executes it, and returns focused evidence.
AskMe's specific design seam is:

> keep the contract → choose one small action → execute/test → interpret fresh
> evidence → update locally when possible → accept the real artifact

Reasoning matters when it improves this trajectory: fewer repeated errors,
fewer stuck steps, and less unnecessary broad replanning. Longer hidden or
visible monologues are not themselves the goal.

## Evidence and claim boundaries

Keep one explicit evidence chain visible across the last three slides:

1. **Current experiment:** four hosted models × two deliberately simple harness
   checks × one unseeded, sequential run per cell.
2. **Observed result:** all eight agents reported completion; seven artifacts
   passed independent acceptance; the retained Qwen3.6-35B-A3B action record
   shows a combined compile-and-run command at the wrong path exiting zero.
3. **External boundary probe:** on one qualified FeatureBench-fast task, the
   Gemma 4 31B trajectory made four reads but no write, produced an empty patch,
   and remained unresolved because proposed structured writes exceeded the
   action budget or were malformed.
4. **Supported conclusion:** the harness exposed two distinct boundaries: one
   wrong delivered artifact and one feature-scale action that never reached
   execution.
5. **Unresolved:** readiness, reasoning-policy benefit, model speed or
   reliability, Qwen versus Gemma, dense versus MoE, and larger versus smaller.

Do not collapse these four levels into a single "the smoke validates" claim.

- Keep the four-model comparison visible: Gemma 4 26B A4B, Gemma 4 31B,
  Qwen3.6-27B, and Qwen3.6-35B-A3B. The paired dense/MoE shapes are useful
  descriptive context and must not disappear in aggregate statistics.
- The hosted matrix is four models × two deliberately simple checks × one
  unseeded run per cell. It is an integration smoke, not a modern coding
  benchmark, reliability estimate, leaderboard, controlled scaling study, or
  model-family comparison.
- Report exact observed outcomes, including 8/8 self-reported completions,
  7/8 accepted artifacts, and the retained wrong-output-path miss.
- Do not imply that model size caused trajectory differences. Architecture,
  active parameters, provider conditions, run order, and task trajectories also
  changed; the Gemma 31B follow-up was post-hoc.
- Show total and active parameters consistently for MoE rows. In particular,
  Qwen3.6-35B-A3B is 35B total and 3B active; showing only the active count makes
  the within-family size/architecture confound harder to see.
- Timings are observed hosted trajectory wall times, not model-speed estimates.
  If shown, label them that way and use the inconsistent within-family patterns
  to explain why the smoke cannot support a scaling story.
- Do not use compiler or syntax repair as headline evidence of contemporary
  coding ability. The useful failure is workflow-level: an exit-zero command at
  the wrong required artifact path.
- External acceptance remains important, but do not introduce it as abstract
  jargon on the title slide. Demonstrate it through the retained miss.
- Simpler, more general standards and interfaces can be good. Do not claim that
  models need special lower standards; specialized skills must earn their
  complexity from repeated evidence.
- Provider names, pytest mechanics, routing flags, and similar audit details
  belong in the appendix/eval documentation, not the five-minute narrative.
- The current acceptance check scored the artifact after the run; its failure was
  not returned to AskMe for another correction. Feeding a focused acceptance
  failure back into the loop while retaining a held-out scorer is future harness
  work, not a feature of the published smoke.
- Keep external-benchmark scope narrow. Slide 6 may show the one-task
  FeatureBench-fast canary's progression from the negative July result to the
  revision-3 requalification, never as a score, reliability estimate, or
  readiness result. The companion roadmap may name
  FeatureBench for feature development, Vals Vibe
  Code Bench for complete web applications, and one optional third candidate:
  ProgramBench as a later clean-room reconstruction stress test. Vals is
  proprietary and access-dependent. A one-task `gron` run may qualify an
  adapter but must not become model evidence; any result-bearing subset needs a
  separate preregistration. The full ProgramBench is out of scope. This
  shortlist is not a commitment to run all three.
- Project and vendor landscapes may remain cited in the companion blog. A backup
  slide may compare the technical model-facing boundaries of AskMe, pi, and
  OpenHands without ranking products or discussing vendor positioning.
- The 2026-08-01 revision-3 requalification (v6) may appear on slide 6 as the
  canary's continuation: under the bundled interface changes and a changed
  serving stack, both cells moved from empty patches to applied but unresolved
  patches. Gemma 4 31B reached 11/13 F2P (84.62%), with the same two failures
  as the exploratory one-attempt pi reference; Qwen3.6-27B reached 7/13
  (53.85%) versus the pi reference's 10/13. Wherever those numbers appear they keep
  one-task adapter-canary boundary language: not FeatureBench scores, not
  reliability estimates, not model comparisons. Required caveats travel with
  any v4/pi comparison: the v6 serving stack (CoreWeave; Gemma bf16, Qwen fp8)
  differs from the SiliconFlow-fp8 v4/pi records while dated served-model IDs
  are identical; the issue-15 local-neutrality bar was waived, so no
  local-neutrality claim is licensed for revision 3; and three frozen Codex P2
  findings caveat Qwen mechanism-level write-forcing counts. Both agents
  exhausted without `done`, but their failure modes differed: Gemma rewrote one
  file 18 times without testing, while Qwen wrote once and returned to
  observation. The
  revision-4 counterpart (validate-after-write pressure, rewrite damping, an
  unvalidated-write replan flag) is future work in progress and is never
  presented as done.

## Seven main slides plus one backup

### 1. Author and question

**Must show:** exact title, speaker name, ServiceNow role, Twitter/X handle,
AskMe repository, venue/date, and one short subtitle drawn from the central
message.

**Tone:** calm, spacious, and introductory.

**Do not show:** workflow tables, benchmark results, external-postcondition
jargon, vendor taxonomies, or a dense problem statement.

### 2. Why a tight loop matters for small models

Define AskMe in one sentence before explaining the loop. The audience should
not need prior repository knowledge.

Connect three ideas directly:

1. smaller LLMs provide control and practical deployment choices;
2. AskMe constrains each turn to a small plan/action and returns execution or
   test evidence; and
3. the surrounding harness preserves the contract and independently accepts
   the full workflow.

Use at most three major visual blocks and one concise loop. Do not enumerate
five products, discuss cloud versus enterprise deployment, or name a company as
the point of the slide.

### 3. Retained workflow miss

Show that a combined compile-and-run command can exit zero while still missing
the required deliverable because it targeted the wrong path. Do not claim that
the retained evidence independently proves source contents or command stdout.
Separate the exit-zero action, agent-reported completion, and artifact acceptance.

### 4. Reasoning and bounded plan updates

Explain the desired control flow: continue after expected evidence, repair the
affected step after a local mismatch, and replan broadly only after a broken
assumption. Emphasize preserving progress and avoiding repeated/stuck steps.

### 5. Four-model descriptive comparison

Restore the visible two-variant-per-family comparison. Show the model names and
build and repair acceptance outcomes for all four hosted models. Keep `8/8
complete` and `7/8 accepted` visible, but do not let aggregate totals replace
the model rows. Keep per-cell wall time, steps, tokens, replans, and model-shape
detail in the repository receipts rather than the stage slide. Label the matrix
`n=1/cell`, simple, hosted, and descriptive only, with no model ranking.

### 6. FeatureBench progression and next bottleneck

Give the slide one job: make the revision-3 improvement unmistakable.

1. **Before — July:** the frozen FeatureBench-fast canary produced zero writes,
   an empty patch, and an unresolved task.
2. **After — Aug 1:** under the revision-3 bundle and a changed serving stack,
   both attempts produced patches that applied. Gemma reached 11/13 target
   tests; Qwen reached 7/13.
3. **Next bottleneck:** neither attempt validated and finished cleanly. Gemma
   rewrote 18 times without testing; Qwen wrote once and returned to reading;
   neither emitted `done`.

State the supported observation directly: AskMe now reaches applied, partially
working FeatureBench code on this canary. Keep the one-task, one-attempt/model,
changed-serving-stack caveats visible, so the progression does not read as a
FeatureBench score or transport-only causal claim. The retained wrong-path
smoke example belongs on slide 3 and should not compete with this story.

Keep research sequencing off the stage. The presentation is not blocked on the
unfinished native reasoning-policy A/B. The detailed evaluation roadmap remains
in issue #2 and the companion material.

### 7. Answer posture and takeaway

Return to the title question. The bounded answer is that small LLMs are
promising for bounded coding loops, while realistic feature readiness is not
demonstrated. Treat readiness as a property of the model, harness, task, and
evaluator together. The current evidence shows meaningful progress from empty
to applied patches and exposes validation and termination as the next harness
problems; it does not validate a transport-only causal benefit or settle
general model readiness.

### 8. Backup: AskMe, pi, and OpenHands

Compare only three technical dimensions: model-facing action surface,
state/control, and completion/acceptance boundary. The purpose is to show how a
harness changes the work left to the model. State that this is a trade-off, not
a ranking. Use current primary project documentation and keep company, cloud,
and enterprise positioning off the slide.

## Visual and editorial constraints

- Exactly eight rendered slides: seven main slides with exactly 499 speaker-note
  words in total, followed by one backup slide without a main-talk note block.
- Slide 1 must have substantial whitespace.
- Slide 2 should communicate one relationship, not survey a market.
- Slide 5 keeps a readable acceptance table; trajectory detail stays in the
  repository receipts.
- Use `AskMe` consistently. Do not reintroduce `NanAgent`.
- Prefer execution, test, integration, and artifact language over compiler-error
  framing.
- Keep audit mechanics in sources or the companion documents.

## Feedback ledger and precedence

- The latest instruction makes slide 1 an author/title introduction. This
  supersedes the earlier request to place a structured steps/plan/actions table
  on the first slide; structured evidence now belongs on later slides or in the
  blog.
- The five-harness landscape was too vague and too detailed at the same time.
  Its replacement must explain AskMe's technical bridge to small models.
- AskMe itself must be defined in plain language on slide 2; do not assume the
  audience knows the repository.
- Company-specific positioning is not part of the stage narrative.
- Removing the Gemma/Qwen two-variant comparison was a regression; future edits
  must preserve it unless the reviewer explicitly removes it.
- The latest presentation-first instruction removes the unfinished 24-run
  reasoning-policy study from the stage and from the talk's critical path.
  Phase 1 evaluation machinery is complete; realistic external feature work
  began with a bounded FeatureBench adapter qualification. The adapter qualified;
  the single registered model canary exhausted without emitting a patch and was
  unresolved, which is not a benchmark score.
- The latest instruction permits that negative FeatureBench canary on slide 6
  as a one-task boundary diagnosis. Broader runs under the same known action-cap
  bottleneck would not create a valid benchmark score.
- The latest harness-comparison request adds one backup slide comparing AskMe,
  pi, and OpenHands at technical boundaries only.
- Claims about model size, family, easier standards, compiler repair, and
  reasoning remain bounded by the rules above.
- Four external benchmark references were too broad for this talk. The revised
  three-level shortlist is FeatureBench for existing-repository features, Vals
  Vibe Code Bench for zero-to-one web applications, and an optional bounded
  ProgramBench stress test for clean-room reconstruction. FeatureBench remains
  the first executable adapter; Vals is access-dependent, and a one-task
  ProgramBench canary is infrastructure qualification rather than evidence.
- The 2026-08-01 instruction adds the v6 revision-3 requalification outcome to
  slide 6 and the conclusion: under the bundled interface changes and a changed
  serving stack, Gemma matched the exploratory pi reference at 11/13 while Qwen
  reached 7/13 versus the pi reference's 10/13. This is an observed harness
  outcome, not causal isolation of transport or write forcing. Numbers stay
  bounded as one-task adapter canaries; provider names stay in slide footers
  and companion documents; the
  revision-4 validate-after-write work is named only as in-progress future
  work, never as done.
- The latest slide review makes that progression the primary message on slide
  6: AskMe moved from zero writes to applied, partially working patches. The
  wrong-path smoke example remains on slide 3; slide 6 now names validation and
  termination as the next bottlenecks.

## Pre-render drift check

- [ ] Exact title and complete speaker identity are present on slide 1.
- [ ] `@den-run-ai` links to `https://x.com/den-run-ai`.
- [ ] Slide 1 is visually calm and contains no table.
- [ ] Slide 2 names the small-model → AskMe loop → accepted-workflow connection.
- [ ] Slide 2 defines AskMe as an experimental coding-agent harness.
- [ ] Slide 2 contains no product/vendor taxonomy.
- [ ] The retained wrong-path workflow miss remains visible.
- [ ] Reasoning is framed as trajectory quality and bounded replanning.
- [ ] All four hosted model rows and both acceptance outcomes are visible.
- [ ] The model matrix is labeled descriptive, hosted, simple, and `n=1/cell`.
- [ ] Per-cell timings, tokens, steps, replans, and model-shape detail remain in
      the repository receipts rather than the stage slide.
- [ ] The supported harness conclusion is distinct from unsupported size,
      family, architecture, reasoning, reliability, and local-speed claims.
- [ ] Slide 6 leads with the progression from zero writes and an empty patch to
      applied patches reaching Gemma 11/13 and Qwen 7/13 target tests.
- [ ] Slide 6 separates that progress from the remaining validation and clean
      termination failures.
- [ ] Slide 6 labels the canary one task, one attempt/model, and not a score.
- [ ] The unfinished reasoning-policy pilot is absent from the stage narrative
      and is not presented as a prerequisite for a shareable talk.
- [ ] No FeatureBench score, reliability estimate, or external readiness claim
      is implied.
- [ ] Vals Vibe Code Bench and ProgramBench remain companion-material only.
- [ ] A one-task ProgramBench canary is never presented as model evidence.
- [ ] Companion benchmark scope is capped at those three distinct candidates.
- [ ] The v6 progression keeps one-task adapter-canary boundary language and
      never reads as a FeatureBench score.
- [ ] Any v6 comparison to v4 or the pi ablation carries the serving-stack
      confound, the waived local-neutrality bar, and the Codex P2 caveats on
      the slide footer or in companion documents.
- [ ] Revision-4 validate-after-write work is labeled in progress, not merged.
- [ ] Backup slide 8 compares AskMe, pi, and OpenHands without a product ranking.
- [ ] Eight slides render without clipping; the seven main speaker-note blocks
      total 499 words.
