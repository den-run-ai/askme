# Speaker Script — Are Small LLMs Ready for Coding Agents?

Agentic AI Summit 2026 · UC Berkeley · Aug 1, 2026 · 5-minute lightning talk

This is the full spoken script, one section per main slide. It is extracted
verbatim from the presenter-note comments in [`slides.md`](slides.md), which
remain the canonical source (Marp presenter mode reads them from there). Seven
sections, 511 words, ~5:00 total. The backup slide has no script — it exists
for Q&A only.

## Slide 1 — Title: Are Small LLMs Ready for Coding Agents? (~40s)

The title is a question, not a verdict. Here, small is a deployment class, not
one parameter cutoff. The hosted receipts span roughly
three-to-four-billion-active mixtures and twenty-seven-to-thirty-one-billion
dense models; they do not measure local performance. Teams want control over
speed, hardware, deployment, and post-training on their chosen hardware and
stack. The question is whether tight execution feedback can turn structured
actions into accepted workflows.

## Slide 2 — AskMe gives a small model one structured move at a time (~45s)

AskMe is an experimental coding-agent harness. It keeps an explicit plan, asks
the model for one structured action, executes it, and returns fresh test or
runtime evidence. AskMe keeps the current task and recent completed work in
view; it can continue, repair, or replan. External acceptance retains the full
contract. This approach assumes work decomposes into scoped actions, feedback
is informative, and success is independently testable. Acceptance checks the
required behavior and artifact.

## Slide 3 — A command passed. The workflow still failed. (~45s)

This Qwen build run shows the agent problem: a successful command can still
miss the workflow contract. The retained evidence shows a combined
compile-and-run command targeting slash tmp slash test returning zero, followed
by reported completion. It does not preserve stdout or prove the source
contents. Acceptance expected dot slash main and found none. AskMe did not
receive that failure for recovery. The workflow contract remained unmet
afterward.

## Slide 4 — Hypothesis: update only what fresh evidence invalidates (~45s)

This is a design hypothesis, not a result from the smoke. Reasoning should keep
the contract in view, interpret execution feedback, and decide how much of the
plan changed. A local mismatch should produce a local correction while
completed work stays completed. Broad replanning belongs to broken assumptions,
not every red command. The target is trajectory quality across these fast
execution-feedback loops: fewer repeated failures, stuck steps, and unnecessary
plan churn—not longer monologues.

## Slide 5 — Acceptance caught the one bad deliverable (~45s)

Four hosted variants each ran two simple checks once. Every agent reported
completion; independent checks accepted seven artifacts. The shortest build
trajectory was the rejected one, which is why completion and speed alone are
insufficient. The rows preserve Gemma and Qwen acceptance status; steps,
tokens, and replans remain in the records. No pair isolates size, architecture,
active compute, family, run order, reasoning, or reliability. These are
one-shot receipts, not rankings.

## Slide 6 — Both models build app features — but fail on testing (~45s)

FeatureBench asks the agent to build a real app feature. In July, the same task
produced no code edits at all: the agents read files and returned an empty
patch. On August first, both models produced patches that applied and passed
most target tests: Gemma eleven of thirteen, Qwen seven of thirteen. Both
models can now build partially working app features. Neither validated its
work: Gemma rewrote the same file without running tests; Qwen stopped editing
and went back to reading. This is one task and one attempt per model —
progress, not a benchmark score.

## Slide 7 — Promising for bounded loops. Feature readiness is still open. (~35s)

The bounded checks are promising, but feature readiness remains unproven. Both
models moved from empty patches to working partial features, yet neither tested
its work or finished cleanly. Gemma rewrote without testing; Qwen wrote once
and returned to reading. Testing and clean completion are the next harness
problems, and one task cannot settle general readiness. Judge delivered
behavior; evaluate the model, harness, and task as one system.

## Slide 8 — Backup: AskMe, pi, and OpenHands

No script. Backup slide for Q&A on how harness boundaries differ; the slide
itself carries the comparison.
