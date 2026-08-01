# Speaker Script — Are Small LLMs Ready for Coding Agents?

Agentic AI Summit 2026 · UC Berkeley · Aug 1, 2026 · 5-minute lightning talk

This is the personal delivery script — the story to speak on stage: the dream
is a fully local coding agent on a 16GB MacBook, and this talk is a progress
report toward that dream. Speak from this document. The slides and the inline
presenter-note comments in [`slides.md`](slides.md) are intentionally
untouched; for delivery, this script supersedes the inline notes. About 690
words — roughly 5:00 at a brisk conversational pace, so rehearse once with a
timer. If you are over time, the flex cuts are the last sentence of slide 4
and the last sentence of slide 5. The backup slide has no script — it exists
for Q&A only.

## Slide 1 — Title: Are Small LLMs Ready for Coding Agents? (~45s)

A few months ago I was on a plane with no wifi, and I caught myself completely
stuck. I had ideas I wanted to try — and without a live coding agent, the
honest truth was it would take me ages alone. I felt genuinely helpless, and it
bothered me the whole flight. So I made myself a promise — call it the dream:
small open models, running on my own MacBook through llama.cpp, doing real
coding work with me, anywhere. This talk is that dream, in progress. Small here
means a deployment class, not a parameter count. One warning up front: the
hosted numbers you'll see today do not measure local performance.

## Slide 2 — AskMe gives a small model one structured move at a time (~45s)

To chase the dream I wrote AskMe — an experimental coding-agent harness small
enough to read in one sitting. I made three bets for small models. One: pass as
little context as possible — the planner sees full state, the executor gets a
slim, curated view. Two: keep every action small and granular — one JSON action
per turn, an edit instead of a rewrite. Three: spend reasoning tokens only
where they earn their cost, mostly in recovery. The loop executes each action
and feeds fresh evidence back, and an independent check accepts the delivered
workflow.

## Slide 3 — A command passed. The workflow still failed. (~40s)

The first lesson small models taught me: success signals lie. In this Qwen
build run, the model compiled and ran its program at slash tmp slash test, saw
exit code zero, and reported the task complete. From inside the loop everything
looked green. The contract asked for dot slash main. The acceptance test looked
and found nothing. A passing command plus a confident completion still added up
to a missing deliverable. That run shaped the whole design: judge the delivered
artifact, not the agent's self-report.

## Slide 4 — Hypothesis: update only what fresh evidence invalidates (~40s)

My third bet is still a hypothesis — let me say that plainly. The control
flow I want is boring on purpose. When evidence matches the plan, continue.
When one step misses, repair that step and rerun its check — don't melt the
whole plan. Replan broadly only when an assumption actually broke. I care about
trajectory quality — fewer repeated failures, fewer stuck steps, less plan
churn — not longer monologues. I haven't measured this benefit yet. It's the
bet I'd most like to be right about.

## Slide 5 — Acceptance caught the one bad deliverable (~45s)

First waypoint: does the loop hold together at all? Four hosted variants — two
Gemmas, two Qwens — each ran two deliberately simple tasks once. Hosted cousins
of my local setup, because hosted is where I could benchmark — they say nothing
about MacBook speed. All eight runs reported complete; the independent check
accepted seven. The one rejection was the fastest run — that wrong-path build.
One run per cell, so no rankings. But acceptance caught exactly what the
completion signal missed. And the small mixture-of-experts Gemma runs this same
loop on my sixteen-gigabyte MacBook — slowly.

## Slide 6 — Both models build app features — but fail on testing (~45s)

Then I raised the ambition: can the harness build a real app feature?
FeatureBench gave me the wall I needed. In July, on one frozen task, both
models produced nothing — zero writes, an empty patch. That failure was mine;
my action interface blocked their edits. I rebuilt the write path — changing
more than one thing at once — and on August first both models delivered
applied patches: Gemma passed eleven of thirteen target tests, Qwen seven.
Real partial features. Then neither ran a single test or finished cleanly —
Gemma rewrote one file eighteen times, Qwen drifted back to reading. One task,
one attempt each: progress, not a score.

## Slide 7 — Promising for bounded loops. Feature readiness is still open. (~40s)

So — is the dream real? Not yet. This is a progress report from the middle.
Bounded loops with independent acceptance: genuinely promising. Feature-scale
work: the models can now build, but they don't test their own work and don't
know when to stop. Those are my next harness problems.
Two of my three bets held up in bounded checks; the reasoning bet is still
unmeasured. If you take one thing from me: judge delivered behavior — evaluate
the model, the harness, and the task as one system. Somewhere over the ocean
there's still a version of me waiting for this to work mid-flight. That's who
I'm building it for.

## Slide 8 — Backup: AskMe, pi, and OpenHands

No script. Backup slide for Q&A on how harness boundaries differ; the slide
itself carries the comparison.
