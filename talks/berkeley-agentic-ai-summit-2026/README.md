# Agentic AI Summit 2026 — Lightning Talk

**Title:** Are Small LLMs Ready for Coding Agents?

**Speaker:** Denis Akhiyarov, Sr Staff Research Scientist at ServiceNow · [@den-run-ai](https://github.com/den-run-ai)

**Slot:** Compass (Saturday), Session 2: Frameworks & Dev Platforms, Aug 1, 2026, 1:00 PM PT
**Format:** 5 minutes, 7 main slides + 1 backup

## Contents

- [`DECK_SPEC.md`](DECK_SPEC.md) — reviewer-facing narrative and evidence contract; read this before editing the deck.
- [`slides.md`](slides.md) — corrected Marp source with synchronized notes across seven main slides, plus one backup slide.
- [`SPEAKER_NOTES.md`](SPEAKER_NOTES.md) — canonical delivery script, synchronized with the inline notes in `slides.md`.
- [`slides.pdf`](slides.pdf) — corrected publication deck, regenerated Sep 8, 2026.
- [Recording](https://www.youtube.com/watch?v=N1XoiJGyNpM) — published talk; read the errata below alongside it.
- [`blog.md`](blog.md) — companion argument and citations.
- [`evals/README.md`](evals/README.md) — reproducible protocol and complete measurements.
- [`evals/draft-results.json`](evals/draft-results.json) — per-run provenance and raw summary.
- [`evals/local-repair-evidence.json`](evals/local-repair-evidence.json) — pinned August 4 local repair receipts, with acceptance, exhaustion and diagnostic budgets kept separate.

## Published-talk errata — 2026-09-07

The recording preserves the original delivery. The slide source, canonical
script, deck contract, and visually checked PDF were aligned in the Sep 8
publication correction. The deck labels historical results by date and does
not present them as measurements of the current native-tool interface.
Use the corrected PDF and script for sharing or future delivery. The points
below remain relevant when viewing the recording.

The revised conclusion also adds a separate **August 4 local repair pilot**,
after the original talk date: narrow accepted repairs exist, but dependable
autonomous coding and net time savings remain unproven. This is not a new
inference run or a claim that the historical recording included that result.

- **Local model:** Gemma 4 E4B is dense PLE, not MoE. The hosted Gemma and Qwen
  records changed revisions, serving stacks, quantization, and budgets; they do
  not isolate model size or measure the local deployment.
- **Current interface:** the planner receives a curated summary, not full raw
  state. The executor accepts one native tool call per turn, with six executable
  handlers plus controller-owned `done` and `fail`. The historical sentinel
  write transport was removed in interface revision 6 on Aug 4.
- **Acceptance:** tool feedback reaches the agent; the published held-out
  acceptance check ran after termination and did not guide recovery. The
  rejected wrong-path artifact was the fastest **build trajectory**, not the
  fastest run overall. The frozen 7/8 result is not a current reliability claim.
- **Feature evidence:** two one-attempt cells produced applying but unresolved
  patches, with 11/13 and 7/13 target tests passing under held-out scoring.
  Neither agent ran the target tests; both exhausted their planning attempts. Bundled changes and a
  different serving stack prevent attributing this to one interface change;
  the results do not establish general feature readiness or validate the three
  design bets. Quantitative pi comparisons below remain exploratory archival
  evidence from unmerged [PR #14](https://github.com/den-run-ai/askme/pull/14),
  not a performance ceiling.

## Talk Arc

1. Open with the readiness question and the speaker, not a dense system diagram.
2. Define AskMe as an experimental coding-agent harness, then connect controllable small LLMs to workflows through one structured action, fresh execution feedback, bounded updates, and independent acceptance.
3. Use the retained wrong-output-path miss to separate successful actions, reported completion, and an accepted workflow.
4. Frame reasoning as a trajectory hypothesis: preserve progress, repair locally, and replan broadly only after a broken assumption.
5. Keep the two Gemma 4 and two Qwen3.6 variants visible as four descriptive hosted receipts, then separate the supported harness observation from unsupported family, architecture, size, speed, reasoning, and reliability claims.
6. Show the FeatureBench observations: after bundled revision-3 changes and a changed serving stack, both attempts produced applying but unresolved patches; target-test execution and clean termination remained gaps in those cells.
7. Answer with the separate local E4B repair pilot: four accepted repairs of one seeded health-check bug, but four exhausted agents. Narrow repairs are possible; dependable autonomy, net time savings and causal harness benefit remain unproven.
8. Keep a backup comparison of AskMe, pi, and OpenHands technical boundaries for Q&A.

## Evidence Boundary

The talk keeps six kinds of statements separate:

- **Strategic context.** The motivations for smaller models and broader workflow agents come from current model/deployment capabilities and harness research; they are not findings from this repository's smoke test.
- **Measured result.** Four hosted models each ran two deliberately simple harness checks once. All eight agents reported completion; seven outputs met the exact acceptance contract. In the retained miss, a combined compile-and-run command exited zero at the wrong artifact path, but the required deliverable was absent.
- **External boundary probe.** One qualified FeatureBench-fast task with Gemma 4 31B produced four reads, zero writes, and an empty patch. The 512-token structured-action budget bound that trajectory. This is a negative one-task canary, not a score or readiness result.
- **Local positive, bounded.** The separate August 4 E89 pilot records four independently accepted repairs of one seeded health-check bug with local Gemma 4 E4B. All four shipped-profile heuristic runs exhausted; its two strict passes used a larger diagnostic budget. This is not a real-repository feature result or a reliability estimate.
- **Supported conclusion.** The combined evidence exposes two different harness boundaries: one wrong delivered artifact and one feature-scale action that never reached execution. It does not validate a causal harness benefit.
- **Hypothesis.** Fast feedback should let reasoning correct locally with few repeated errors and little unnecessary replanning. The current runs did not isolate reasoning mode, model size, or model family, so the deck leaves that causal claim open.

For this talk, “small” is an engineering/deployment class rather than a fixed parameter cutoff. The hosted matrix spans 3–4B-active MoE and 27–31B dense models and does not measure local-Mac performance. Its timings are observed trajectory wall times, not model-speed estimates.

Evaluation extension is separate from the talk's critical path. The reproducible
workflow runner and one qualified native fixture are merged, but the native
reasoning-policy A/B is deferred and has zero measured runs. A Qwen-versus-Gemma
or model-size claim would require a separate predeclared, repeated design.

External evaluation now has successful FeatureBench adapter/evaluator
qualification and a valid negative one-task outcome. The registered canary
passed the gold, harmless-control, audit, and evaluator qualification checks;
its only model attempt exhausted without emitting a patch and was unresolved.
This is an actionable interface signal, not a FeatureBench score, reliability
estimate, or readiness result. Vals Vibe
Code Bench remains an access-dependent full-app reference, and ProgramBench is
only a later clean-room stress-test candidate. Slide 6 includes this one-task
boundary diagnosis. [Issue #2](https://github.com/den-run-ai/askme/issues/2) is
the closed protocol/history record; feature-scale interface work is active in
[issue #7](https://github.com/den-run-ai/askme/issues/7). The revision-4
validate-after-write follow-up [PR #21](https://github.com/den-run-ai/askme/pull/21)
merged on Aug 2. No v7 requalification record is checked in as of Sep 7.

On Aug 1, 2026, the revision-3 action interface (sentinel write transport,
backend-aware budgets, write-forcing policy) requalified that same frozen task
under preregistered v6 protocols. Under the bundled interface changes and a
changed serving stack, both cells produced applied but unresolved patches:
Gemma 4 31B reached 11/13 F2P (84.62%), with the same two failures as the
exploratory one-attempt pi reference, while Qwen3.6-27B reached 7/13 (53.85%)
versus the pi reference's 10/13. Both agents exhausted their planning attempts,
but only Gemma entered a rewrite loop (18 writes); Qwen wrote once and
returned to observation. Neither ran the target tests. These remain
one-task adapter canaries — not FeatureBench scores, reliability estimates, or
model comparisons — and carry recorded caveats: a serving-stack confound vs the
SiliconFlow-served v4/pi records (v6 ran on CoreWeave; Gemma bf16, Qwen fp8;
dated served-model IDs identical), a maintainer-waived issue-15
local-neutrality bar (no local-neutrality claim licensed for revision 3), and
three frozen Codex P2 findings on write-forcing mechanics affecting the Qwen
cell's mechanism-level counts. Slide 6 carries this continuation; the dated
records live under `tests/featurebench/results/`.

Those are revision-3 results, not measurements of the current interface.
Interface revision 6 removed the sentinel transport on Aug 4 and uses native
tool calls. A new frozen protocol and requalified controls are needed before
citing the v6 outcomes as current-main behavior.

Provider routing, endpoint metadata, test-runner mechanics, token accounting, costs, and per-cell timings remain in the eval appendix. They are intentionally omitted from the five-minute narrative.

### Local repair evidence on slide 7

The [pinned E89 report](https://github.com/den-run-ai/askme/blob/8d4e1eab8034d2b5e0b6418b6701a351201187ad/tests/bench_records/2026-08-04/e89-web-local/README.md)
describes local Gemma 4 E4B QAT Q4_0 (dense PLE), tools-only runtime `4e528a6`.
In four shipped-profile heuristic runs (one pilot plus three trials), the
health-check repair passed independent acceptance while the agent exhausted.
The original eleven-run pilot's two strict passes used the non-shipping
`generic-feature-scale-v1` profile. Later lifecycle-policy trials are a separate
addendum; they are not pooled into this stage example.

The [local receipt](evals/local-repair-evidence.json) pins the report, protocol,
summary and four JSONL hashes. Those JSONLs retain the successful edits and
exhausted endings; the acceptance claim comes from the historical report's
surviving-workspace check, not a newly replayed acceptance transcript. Protocol
amendments, an early-stopped arm with discarded partial logs, changed budgets
and one seeded task prevent a reliability or causal claim. No human baseline
or net time-savings measurement exists. The September local serving probes did
not attempt coding and are not substituted for this historical positive.

## Render

The checked-in PDF uses Marp CLI 4.4.1 with Node 23. From the repository root:

```bash
npx @marp-team/marp-cli@4.4.1 talks/berkeley-agentic-ai-summit-2026/slides.md \
  --html --pdf --allow-local-files
```

For presenter mode:

```bash
npx @marp-team/marp-cli@4.4.1 talks/berkeley-agentic-ai-summit-2026/slides.md \
  --html --preview
```

## Primary Sources

### Coding-agent benchmark shortlist

- [FeatureBench](https://github.com/LiberCoders/FeatureBench) — primary target
- [Vals Vibe Code Bench](https://www.vals.ai/benchmarks/vibe-code) — access-dependent full-app reference
- [ProgramBench](https://github.com/facebookresearch/programbench) — later clean-room stress test; `gron` canary only

### Other sources

- [Lilian Weng, “Harness Engineering for Self-Improvement”](https://lilianweng.github.io/posts/2026-07-04-harness/)
- [HyperAgents](https://arxiv.org/abs/2603.19461)
- [Pi coding-agent documentation](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md)
- [Oh My Pi, a batteries-included Pi fork](https://github.com/can1357/oh-my-pi)
- [OpenHands tool system](https://docs.openhands.dev/sdk/arch/tool-system)
- [OpenHands conversation architecture](https://docs.openhands.dev/sdk/arch/conversation)
- [OpenHands benchmark harness](https://github.com/OpenHands/benchmarks)
- [Omnigent (Databricks, OSS alpha)](https://github.com/omnigent-ai/omnigent)
- [Omnigent on Databricks (managed beta)](https://docs.databricks.com/aws/en/omnigent/)
- [Databricks’ separate evaluation layer, “Benchmarking Coding Agents on Databricks’ Multi-Million Line Codebase”](https://www.databricks.com/blog/benchmarking-coding-agents-databricks-multi-million-line-codebase)
- [Google, Gemma 4 model overview](https://ai.google.dev/gemma/docs/core)
- [Qwen, Qwen3.6-27B announcement](https://qwen.ai/blog?id=qwen3.6-27b)
- [Qwen, Qwen3.6-35B-A3B announcement](https://qwen.ai/blog?id=qwen3.6-35b-a3b)
- [Google Gemma run and deployment guidance](https://ai.google.dev/gemma/docs/run)
- [Google Gemma tuning guidance](https://ai.google.dev/gemma/docs/tune)
- [Agentic AI Summit 2026 program](https://rdi.berkeley.edu/events/agentic-ai-summit-2026)

The public program confirms the session and start time. The five-minute duration comes from speaker communications rather than the public agenda.
