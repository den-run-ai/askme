# Speaker Script — Are Small LLMs Ready for Coding Agents?

Agentic AI Summit 2026 · UC Berkeley · Aug 1, 2026 · 5-minute lightning talk

Corrected delivery script, updated 2026-09-08. This is the canonical spoken
source; the inline notes in `slides.md` are synchronized with it. Source blocks
are reference material, not spoken copy. The [published-talk errata](README.md#published-talk-errata--2026-09-07)
apply to the historical recording. The backup slide has no script — Q&A only.

Key framing: AskMe began with local Gemma 4 E4B, a dense PLE model that fits
in 16GB of MacBook RAM. The hosted Gemma 4 and Qwen3.6 evaluations span later
AskMe revisions, serving stacks, quantization, and budgets. The separate August
local repair pilot on slide 7 is narrow positive evidence, not a controlled
model-size comparison, current-runtime result or proof of net time savings.

## Slide 1 — Title: Are Small LLMs Ready for Coding Agents? (~40s)

This started on a plane. No wifi, no coding agent, and the experiments I
wanted would take weeks alone. So, the dream: small open models on my own
MacBook, through llama.cpp, doing real coding work anywhere. This talk is a
progress report on that dream. Small means a deployment class, not a parameter
count. One caveat up front: AskMe began with Gemma 4 E4B, a dense PLE model
on a sixteen-gigabyte Mac. The smoke and feature evaluations use hosted Gemma
and Qwen across different revisions and serving configurations. A separate
local repair pilot closes the talk. None isolates model size.

[Sources]
- [Local deployment and scope](../../README.md)
- [Hosted experiment provenance](evals/draft-results.json)

## Slide 2 — AskMe gives a small model one structured move at a time (~40s)

AskMe is an experimental coding-agent harness, small enough to read in one
sitting. Three bets for small models. One: pass as little context as possible
— the planner gets a curated summary, the executor a smaller sliding view.
Two: small granular actions, an edit instead of a rewrite. Today's interface
accepts one native tool call per turn: six executable actions plus `done` and
`fail`. Three: reasoning tokens only where they pay, mostly in recovery.
Tool feedback guides AskMe; held-out acceptance scores the artifact after the
run and is not returned to the agent for recovery.

[Sources]
- [Runtime protocol and control boundaries](../../docs/ARCHITECTURE.md)
- [Current native tool schemas](../../actions.py)

## Slide 3 — A command passed. The workflow still failed. (~35s)

First lesson: success signals can mislead. This Qwen run issued a combined
compile-and-run command at slash tmp slash test, saw exit zero, and reported
complete. The record does not preserve stdout or prove source contents. The
contract asked for dot slash main; the post-run check found none. AskMe did
not receive that failure for recovery. Judge the delivered artifact, not
the agent's self-report.

[Sources]
- [Frozen July 10 per-cell outcomes and retained command](evals/draft-results.json)

## Slide 4 — Hypothesis: update only what fresh evidence invalidates (~35s)

The third bet is still a hypothesis. The control flow is boring on purpose.
Evidence matches the plan: continue. One step misses: repair that step, rerun
its check. Replan broadly only when an assumption broke. The target is
trajectory quality — fewer repeated failures, fewer stuck steps, less plan
churn — not longer monologues. Not measured yet; it's the bet I most want to
be right about.

[Sources]
- [Harness hypotheses and evaluation limits](../../docs/EXPERIMENTS.md)

## Slide 5 — Acceptance caught the one bad deliverable (~40s)

Does the loop hold? Four hosted variants — two Gemma 4s, two Qwen3.6s — two
simple tasks each, one run each. All eight reported complete; the independent
check accepted seven. The one rejection was the fastest build trajectory —
that wrong-path build; an accepted repair was faster overall. This is a frozen
July record, one run per cell, with no rankings or current reliability claim.
Acceptance caught what the completion signal missed.

[Sources]
- [Frozen July 10 hosted matrix](evals/draft-results.json)
- [Protocol and acceptance boundary](evals/README.md)

## Slide 6 — Applied patches, unresolved feature task (~40s)

Next: FeatureBench, one frozen feature task. Revision 2 left both cells with
empty patches, for different reasons. After bundled revision-3 changes and a
changed serving stack, both left applying but unresolved patches. Held-out
scoring found eleven of thirteen target tests passing for Gemma, seven for
Qwen. Neither ran the target tests; both agents exhausted their planning attempts.
Gemma rewrote one file
eighteen times, Qwen returned to reading. One task, one attempt each, no causal
attribution. These historical results do not validate today's native-tool
transport.

[Sources]
- [FeatureBench protocols and historical outcomes](../../tests/featurebench/README.md)
- [Gemma August 1 result](../../tests/featurebench/results/2026-08-01-gemma-4-31b-canary-v6.json)
- [Qwen August 1 result](../../tests/featurebench/results/2026-08-01-qwen36-27b-canary-v6.json)

## Slide 7 — Local repairs are possible. Reliable autonomy is unproven. (~45s)

So: can small local models do useful coding work? A narrow yes. In an August
pilot, local Gemma E4B repaired one seeded health-check bug in four
shipped-profile runs. Independent acceptance passed; all four agents exhausted.
The two clean finishes used a larger, diagnostic budget.
That is evidence of small accepted repairs, not dependable autonomy or measured
net time savings. The feature canary remains unresolved. Judge delivered
behavior, and evaluate the model, harness, task, and evaluator together.
A causal harness benefit and the reliable plane version remain goals.

[Sources]
- [Frozen August 4 local repair receipt and evidence limits](evals/local-repair-evidence.json)
- [Evidence and unresolved claims](README.md#evidence-boundary)
- [Dated measurements and limits](../../docs/PERFORMANCE.md)

## Slide 8 — Backup: AskMe, pi, and OpenHands

No script. Backup slide for Q&A on how harness boundaries differ; the slide
itself carries the comparison.

[Sources]
- [AskMe completion semantics](../../README.md)
- [pi coding-agent documentation](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md)
- [OpenHands tool system](https://docs.openhands.dev/sdk/arch/tool-system)
- [OpenHands conversation](https://docs.openhands.dev/sdk/arch/conversation)
- External documentation checked 2026-09-08.
