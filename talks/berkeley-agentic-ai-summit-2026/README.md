# Agentic AI Summit 2026 — Lightning Talk

**Title:** Are Small LLMs Ready for Coding Agents?

**Speaker:** Denis Akhiyarov, Sr Staff Research Scientist at ServiceNow · [@den-run-ai](https://x.com/den-run-ai)

**Slot:** Compass (Saturday), Session 2: Frameworks & Dev Platforms, Aug 1, 2026, 1:00 PM PT
**Format:** 5 minutes, 7 main slides + 1 backup

## Contents

- [`DECK_SPEC.md`](DECK_SPEC.md) — reviewer-facing narrative and evidence contract; read this before editing the deck.
- [`slides.md`](slides.md) — Marp source with 499 words of notes across the seven main slides, plus one backup slide.
- `slides.pdf` — rendered deck.
- [`blog.md`](blog.md) — companion argument and citations.
- [`evals/README.md`](evals/README.md) — reproducible protocol and complete measurements.
- [`evals/draft-results.json`](evals/draft-results.json) — per-run provenance and raw summary.

## Talk Arc

1. Open with the readiness question and the speaker, not a dense system diagram.
2. Define AskMe as an experimental coding-agent harness, then connect controllable small LLMs to workflows through one structured action, fresh execution feedback, bounded updates, and independent acceptance.
3. Use the retained wrong-output-path miss to separate successful actions, reported completion, and an accepted workflow.
4. Frame reasoning as a trajectory hypothesis: preserve progress, repair locally, and replan broadly only after a broken assumption.
5. Keep the two Gemma 4 and two Qwen3.6 variants visible as four descriptive hosted receipts, then separate the supported harness observation from unsupported family, architecture, size, speed, reasoning, and reliability claims.
6. Show two observed boundaries: a FeatureBench-fast canary that never emitted a write, and a wrong deliverable that independent acceptance caught only after the agent stopped — then the Aug 1 revision-3 requalification that moved both cells to applied but unresolved patches and exposed distinct downstream failures: Gemma rewrote without testing; Qwen returned to observation after one write.
7. Answer cautiously: bounded loops look promising, but realistic feature readiness remains open and belongs to the model–harness–task combination.
8. Keep a backup comparison of AskMe, pi, and OpenHands technical boundaries for Q&A.

## Evidence Boundary

The talk keeps five kinds of statements separate:

- **Strategic context.** The motivations for smaller models and broader workflow agents come from current model/deployment capabilities and harness research; they are not findings from this repository's smoke test.
- **Measured result.** Four hosted models each ran two deliberately simple harness checks once. All eight agents reported completion; seven outputs met the exact acceptance contract. In the retained miss, a combined compile-and-run command exited zero at the wrong artifact path, but the required deliverable was absent.
- **External boundary probe.** One qualified FeatureBench-fast task with Gemma 4 31B produced four reads, zero writes, and an empty patch. The 512-token structured-action budget bound that trajectory. This is a negative one-task canary, not a score or readiness result.
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
[issue #7](https://github.com/den-run-ai/askme/issues/7) and continues in the
revision-4 validate-after-write follow-up (#21; implemented, v7 requalification
pending).

On Aug 1, 2026, the revision-3 action interface (sentinel write transport,
backend-aware budgets, write-forcing policy) requalified that same frozen task
under preregistered v6 protocols. Under the bundled interface changes and a
changed serving stack, both cells produced applied but unresolved patches:
Gemma 4 31B reached 11/13 F2P (84.62%), with the same two failures as the
exploratory one-attempt pi reference, while Qwen3.6-27B reached 7/13 (53.85%)
versus the pi reference's 10/13. Both agents exhausted without emitting `done`,
but only Gemma entered a rewrite loop (18 writes, zero tests); Qwen wrote once
and returned to observation. These remain
one-task adapter canaries — not FeatureBench scores, reliability estimates, or
model comparisons — and carry recorded caveats: a serving-stack confound vs the
SiliconFlow-served v4/pi records (v6 ran on CoreWeave; Gemma bf16, Qwen fp8;
dated served-model IDs identical), a maintainer-waived issue-15
local-neutrality bar (no local-neutrality claim licensed for revision 3), and
three frozen Codex P2 findings on write-forcing mechanics affecting the Qwen
cell's mechanism-level counts. Slide 6 carries this continuation; the dated
records live under `tests/featurebench/results/`.

Provider routing, endpoint metadata, test-runner mechanics, token accounting, costs, and per-cell timings remain in the eval appendix. They are intentionally omitted from the five-minute narrative.

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
