# Speaker Script — Are Small LLMs Ready for Coding Agents?

Agentic AI Summit 2026 · UC Berkeley · Aug 1, 2026 · 5-minute lightning talk

Corrected delivery script, updated 2026-09-07. This is the canonical spoken
source; the published deck and its inline notes remain historical artifacts.
See the [published-talk errata](README.md#published-talk-errata--2026-09-07)
when viewing the recording or slides. The backup slide has no script — Q&A only.

Key framing: AskMe began with local Gemma 4 E4B, a dense PLE model that fits
in 16GB of MacBook RAM. The hosted Gemma 4 and Qwen3.6 evaluations span later
AskMe revisions, serving stacks, quantization, and budgets; they are not a
controlled model-size comparison or evidence of local performance.

## Slide 1 — Title: Are Small LLMs Ready for Coding Agents? (~40s)

This started on a plane. No wifi, no coding agent, and the experiments I
wanted would take weeks alone. So, the dream: small open models on my own
MacBook, through llama.cpp, doing real coding work anywhere. This talk is a
progress report on that dream. Small means a deployment class, not a parameter
count. One caveat up front: AskMe began with Gemma 4 E4B, a dense PLE model
on a sixteen-gigabyte Mac. These evaluations use hosted Gemma and Qwen models
across later revisions and different serving configurations. They do not
measure local performance or isolate model size.

## Slide 2 — AskMe gives a small model one structured move at a time (~40s)

AskMe is an experimental coding-agent harness, small enough to read in one
sitting. Three bets for small models. One: pass as little context as possible
— the planner gets a curated summary, the executor a smaller sliding view.
Two: small granular actions, an edit instead of a rewrite. Today's interface
accepts one native tool call per turn: six executable actions plus `done` and
`fail`. Three: reasoning tokens only where they pay, mostly in recovery.
Tool feedback guides AskMe; held-out acceptance scores the artifact after the
run and is not returned to the agent for recovery.

## Slide 3 — A command passed. The workflow still failed. (~35s)

First lesson: success signals lie. This Qwen run compiled and ran its program
at slash tmp slash test, saw exit zero, reported complete. Inside the loop,
all green. The contract asked for dot slash main. The acceptance test found
nothing. Passing command plus confident completion — still a missing
deliverable. That run set the design rule: judge the delivered artifact, not
the agent's self-report.

## Slide 4 — Hypothesis: update only what fresh evidence invalidates (~35s)

The third bet is still a hypothesis. The control flow is boring on purpose.
Evidence matches the plan: continue. One step misses: repair that step, rerun
its check. Replan broadly only when an assumption broke. The target is
trajectory quality — fewer repeated failures, fewer stuck steps, less plan
churn — not longer monologues. Not measured yet; it's the bet I most want to
be right about.

## Slide 5 — Acceptance caught the one bad deliverable (~40s)

Does the loop hold? Four hosted variants — two Gemma 4s, two Qwen3.6s — two
simple tasks each, one run each. All eight reported complete; the independent
check accepted seven. The one rejection was the fastest build trajectory —
that wrong-path build; an accepted repair was faster overall. This is a frozen
July record, one run per cell, with no rankings or current reliability claim.
Acceptance caught what the completion signal missed.

## Slide 6 — Applying but unresolved patches on one feature task (~40s)

Next: FeatureBench, one frozen feature task. Revision 2 left both cells with
empty patches, for different reasons. After bundled revision-3 changes and a
changed serving stack, both left applying but unresolved patches. Held-out
scoring found eleven of thirteen target tests passing for Gemma, seven for
Qwen. Neither ran the target tests or emitted `done`; Gemma rewrote one file
eighteen times, Qwen returned to reading. One task, one attempt each, no causal
attribution. These historical results do not validate today's native-tool
transport.

## Slide 7 — Promising for bounded loops. Feature readiness is still open. (~35s)

So: ready? Not yet. Bounded loops with independent acceptance — promising.
The feature canary exposed gaps in target-test execution and clean completion.
It did not establish general feature readiness or isolate any of the three
design bets. The takeaway: judge delivered behavior — evaluate the model,
harness, task, and evaluator as one system. The reliable plane version remains
the goal. I'm building it.

## Slide 8 — Backup: AskMe, pi, and OpenHands

No script. Backup slide for Q&A on how harness boundaries differ; the slide
itself carries the comparison.
