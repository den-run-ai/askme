# Agentic AI Summit 2026 — Lightning Talk

**Title:** Smaller Open Models, Full Workflows, Tight Harnesses

**Slot:** Compass (Saturday), Session 2: Frameworks & Dev Platforms, Aug 1, 2026, 1:00 PM PT
**Format:** 5 minutes, 7 slides

## Contents

- [`slides.md`](slides.md) — Marp source with 499-word speaker notes.
- `slides.pdf` — rendered deck.
- [`blog.md`](blog.md) — companion argument and citations.
- [`evals/README.md`](evals/README.md) — reproducible protocol and complete measurements.
- [`evals/draft-results.json`](evals/draft-results.json) — per-run provenance and raw summary.

## Talk Arc

1. Smaller open models offer more control over execution, hardware, deployment, post-training, and the builder ecosystem.
2. Harnesses choose different boundaries: minimal extensible loops, tool-rich terminals, managed runtimes, meta-orchestration, and enterprise evaluation.
3. AskMe isolates one seam connecting controllable models to broader workflows: execution evidence, bounded plan updates, and independent acceptance.
4. The retained failure—working code at the wrong artifact path—shows why successful actions and self-reported completion are not the same as a completed workflow.
5. Reasoning should preserve progress and turn fresh evidence into the smallest useful plan update.
6. The published smoke validates the measurement path, not a model hierarchy.
7. The next experiment should freeze four native semantic workflows, compare explicit-reasoning off with the current gated policy, and use repeated held-out acceptance and false completion as primary outcomes.

## Evidence Boundary

The talk keeps three kinds of statements separate:

- **Strategic context.** The motivations for smaller models and broader workflow agents come from current model/deployment capabilities and harness research; they are not findings from this repository's smoke test.
- **Measured result.** Four hosted models each ran two deliberately simple harness checks once. All eight agents reported completion; seven outputs met the exact acceptance contract. The retained miss produced working behavior at the wrong path.
- **Hypothesis.** Fast feedback should let reasoning correct locally with few repeated errors and little unnecessary replanning. The current runs did not isolate reasoning mode, model size, or model family, so the deck proposes the experiment needed to test that claim.

Provider routing, endpoint metadata, test-runner mechanics, token accounting, costs, and per-cell timings remain in the eval appendix. They are intentionally omitted from the five-minute narrative.

## Render

From the repository root:

```bash
npx @marp-team/marp-cli talks/berkeley-agentic-ai-summit-2026/slides.md \
  --html --pdf --allow-local-files
```

For presenter mode:

```bash
npx @marp-team/marp-cli talks/berkeley-agentic-ai-summit-2026/slides.md \
  --html --preview
```

## Primary Sources

- [Lilian Weng, “Harness Engineering for Self-Improvement”](https://lilianweng.github.io/posts/2026-07-04-harness/)
- [HyperAgents](https://arxiv.org/abs/2603.19461)
- [Pi documentation](https://pi.dev/docs/latest)
- [Oh My Pi](https://github.com/can1357/oh-my-pi)
- [OpenHands](https://github.com/OpenHands/OpenHands)
- [Omnigent](https://github.com/omnigent-ai/omnigent)
- [Databricks, “Benchmarking Coding Agents on Databricks’ Multi-Million Line Codebase”](https://www.databricks.com/blog/benchmarking-coding-agents-databricks-multi-million-line-codebase)
- [Claw-SWE-Bench](https://arxiv.org/abs/2606.12344)
- [FeatureBench](https://github.com/LiberCoders/FeatureBench)
- [Datacurve's deep-swe benchmark](https://github.com/datacurve-ai/deep-swe)
- [Terminal-Bench 2.1](https://www.tbench.ai/news/terminal-bench-2-1)
- [Google Gemma run and deployment guidance](https://ai.google.dev/gemma/docs/run)
- [Google Gemma tuning guidance](https://ai.google.dev/gemma/docs/tune)
- [Agentic AI Summit 2026 program](https://rdi.berkeley.edu/events/agentic-ai-summit-2026)

The public program confirms the session and start time. The five-minute duration comes from speaker communications rather than the public agenda.
