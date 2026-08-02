# Speaker Script — Are Small LLMs Ready for Coding Agents?

Agentic AI Summit 2026 · UC Berkeley · Aug 1, 2026 · 5-minute lightning talk

Personal delivery script — terse, fast transitions, no filler. Speak from this
document; the slides and the inline presenter-note comments in
[`slides.md`](slides.md) are intentionally untouched, and this script
supersedes the inline notes for delivery. About 520 words — roughly 4:15–4:45
spoken, leaving room for slide changes. The backup slide has no script — Q&A
only.

Key framing carried on slides 1 and 5: AskMe was developed for the small local
Gemma 4 MoE that fits in 16GB of MacBook RAM; the evaluations use larger
hosted Gemma 4 and Qwen3.6 variants because the Mac cannot hold them — same
harness, bigger models, so hosted numbers are not local performance.

## Slide 1 — Title: Are Small LLMs Ready for Coding Agents? (~40s)

This started on a plane. No wifi, no coding agent, and the experiments I
wanted would take weeks alone. So, the dream: small open models on my own
MacBook, through llama.cpp, doing real coding work anywhere. This talk is a
progress report on that dream. Small means a deployment class, not a parameter
count. One caveat up front: AskMe is built for a small Gemma 4
mixture-of-experts that fits in sixteen gigabytes of MacBook RAM. The
evaluations you'll see use its larger hosted siblings — my Mac can't hold
them. Same harness, bigger models: hosted numbers, not local performance.

## Slide 2 — AskMe gives a small model one structured move at a time (~40s)

AskMe is an experimental coding-agent harness, small enough to read in one
sitting. Three bets for small models. One: pass as little context as possible
— the planner sees full state, the executor gets a slim curated view. Two:
small granular actions — one JSON action per turn, an edit instead of a
rewrite. Three: reasoning tokens only where they pay, mostly in recovery.
Execute, feed the evidence back, and an independent check accepts the
delivered workflow.

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
check accepted seven. The one rejection was the fastest run — that wrong-path
build. One run per cell: no rankings. But acceptance caught exactly what the
completion signal missed. Locally, the small MoE Gemma runs this same loop on
my MacBook at about seven tokens a second.

## Slide 6 — Both models build app features — but fail on testing (~40s)

Next: a real app feature. FeatureBench, one frozen task. July: zero writes,
empty patch — my action interface blocked the edits. I rebuilt the write path
— several changes at once, so no clean causal story. August first: both models
delivered applied patches. Gemma passed eleven of thirteen target tests, Qwen
seven. Real partial features. Then neither ran a single test or finished
cleanly — Gemma rewrote one file eighteen times, Qwen drifted back to reading.
One task, one attempt each: progress, not a score.

## Slide 7 — Promising for bounded loops. Feature readiness is still open. (~35s)

So: ready? Not yet. Bounded loops with independent acceptance — promising.
Feature scale — the models build, but they don't test their own work and don't
know when to stop. Those are the next harness problems. Two of three bets held
up in bounded checks; the reasoning bet is unmeasured. The takeaway: judge
delivered behavior — evaluate the model, harness, and task as one system. The
plane version of this still doesn't exist. I'm building it.

## Slide 8 — Backup: AskMe, pi, and OpenHands

No script. Backup slide for Q&A on how harness boundaries differ; the slide
itself carries the comparison.
