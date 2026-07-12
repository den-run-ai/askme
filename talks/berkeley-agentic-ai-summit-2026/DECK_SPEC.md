# Berkeley talk deck contract

**Status:** reviewer-facing source of truth for the seven-slide talk

**Last updated:** 2026-07-12

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
- **Format:** five minutes, exactly seven slides

The title is a question. The deck presents a design direction and bounded
evidence; it does not claim that the current smoke test settles the question.

## Central message

Smaller open models are attractive because they offer more control over
execution speed, hardware, deployment, access to post-training, and the builder
ecosystem. Coding agents are expanding toward general-purpose, full-lifecycle
workflow agents. A tight harness connects those trends by giving the model a
small action surface, returning fresh execution or test evidence, preserving
completed work, and checking the delivered workflow independently.

AskMe's specific design seam is:

> keep the contract → choose one small action → execute/test → interpret fresh
> evidence → update locally when possible → accept the real artifact

Reasoning matters when it improves this trajectory: fewer repeated errors,
fewer stuck steps, and less unnecessary broad replanning. Longer hidden or
visible monologues are not themselves the goal.

## Evidence and claim boundaries

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
- Do not use compiler or syntax repair as headline evidence of contemporary
  coding ability. The useful failure is workflow-level: working behavior at the
  wrong required artifact path.
- External acceptance remains important, but do not introduce it as abstract
  jargon on the title slide. Demonstrate it through the retained miss.
- Simpler, more general standards and interfaces can be good. Do not claim that
  models need special lower standards; specialized skills must earn their
  complexity from repeated evidence.
- Provider names, pytest mechanics, routing flags, and similar audit details
  belong in the appendix/eval documentation, not the five-minute narrative.
- Keep external-benchmark scope narrow. The stage narrative may name
  FeatureBench for feature development and Vals Vibe Code Bench for complete
  web applications. Vals is proprietary and access-dependent, so it is external
  evidence rather than a promised AskMe adapter. The companion roadmap may
  retain one optional third candidate: ProgramBench as a later clean-room
  reconstruction stress test. A one-task `gron` run may qualify the adapter but
  must not become model evidence; any result-bearing subset needs a separate
  preregistration. The full ProgramBench is out of scope. This shortlist is not
  a commitment to run all three.
- Project and vendor landscapes may remain cited in the companion blog. The
  stage deck should explain technical boundaries rather than promote or compare
  companies.

## Seven-slide narrative contract

### 1. Author and question

**Must show:** exact title, speaker name, ServiceNow role, Twitter/X handle,
AskMe repository, venue/date, and one short subtitle drawn from the central
message.

**Tone:** calm, spacious, and introductory.

**Do not show:** workflow tables, benchmark results, external-postcondition
jargon, vendor taxonomies, or a dense problem statement.

### 2. Why a tight loop matters for small models

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

Show that correct files, a successful build, and successful execution can still
miss the required deliverable when the artifact is produced at the wrong path.
Separate successful actions, agent-reported completion, and artifact acceptance.

### 4. Reasoning and bounded plan updates

Explain the desired control flow: continue after expected evidence, repair the
affected step after a local mismatch, and replan broadly only after a broken
assumption. Emphasize preserving progress and avoiding repeated/stuck steps.

### 5. Four-model descriptive comparison

Restore the visible two-variant-per-family comparison. Show model shape plus the
build and repair outcomes for all four hosted models. Keep `8/8 complete` and
`7/8 accepted` visible, but do not let aggregate totals replace the model rows.
Label the matrix `n=1/cell`, simple, hosted, and descriptive only.

### 6. Next valid experiment

Show the preregistered direction: four qualified semantic workflows, explicit
reasoning `off` versus frozen `gated`, three randomized repeats, held-out
acceptance and false completion as co-primary outcomes. Do not imply this pilot
has already run.

### 7. Answer posture and takeaway

Return to the title question. The bounded answer is that small LLMs are
promising when the harness keeps actions small, feeds back real execution, uses
reasoning to update the smallest invalidated part of the plan, and ties success
to the delivered workflow. This is a direction to test, not a general readiness
verdict.

## Visual and editorial constraints

- Exactly seven rendered slides and exactly 499 speaker-note words unless the
  reviewer explicitly changes those constraints.
- Slide 1 must have substantial whitespace.
- Slide 2 should communicate one relationship, not survey a market.
- Slide 5 is the one intentionally dense evidence slide; favor a readable table
  over cards full of prose.
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
- Company-specific positioning is not part of the stage narrative.
- Removing the Gemma/Qwen two-variant comparison was a regression; future edits
  must preserve it unless the reviewer explicitly removes it.
- Claims about model size, family, easier standards, compiler repair, and
  reasoning remain bounded by the rules above.
- Four external benchmark references were too broad for this talk. The revised
  three-level shortlist is FeatureBench for existing-repository features, Vals
  Vibe Code Bench for zero-to-one web applications, and an optional bounded
  ProgramBench stress test for clean-room reconstruction. FeatureBench remains
  the first executable adapter; Vals is access-dependent, and a one-task
  ProgramBench canary is infrastructure qualification rather than evidence.

## Pre-render drift check

- [ ] Exact title and complete speaker identity are present on slide 1.
- [ ] `@den-run-ai` links to `https://x.com/den-run-ai`.
- [ ] Slide 1 is visually calm and contains no table.
- [ ] Slide 2 names the small-model → AskMe loop → accepted-workflow connection.
- [ ] Slide 2 contains no product/vendor taxonomy.
- [ ] The retained wrong-path workflow miss remains visible.
- [ ] Reasoning is framed as trajectory quality and bounded replanning.
- [ ] All four model rows and their dense/MoE shapes are visible.
- [ ] The model matrix is labeled descriptive, hosted, simple, and `n=1/cell`.
- [ ] No size, family, reliability, or local-speed conclusion is implied.
- [ ] The next pilot is presented as planned, not completed.
- [ ] The stage narrative names only FeatureBench and Vals Vibe Code Bench;
      ProgramBench remains an optional later roadmap stress test.
- [ ] A one-task ProgramBench canary is never presented as model evidence.
- [ ] Companion benchmark scope is capped at those three distinct candidates.
- [ ] Seven slides render without clipping and speaker notes total 499 words.
