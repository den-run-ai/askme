# Agentic AI Summit 2026 — Lightning Talk

**Title:** Are Small LLMs Ready for Coding Agents?

**Speaker:** Denis Akhiyarov, Sr Staff Research Scientist at ServiceNow · [@den-run-ai](https://x.com/den-run-ai)

**Slot:** Compass (Saturday), Session 2: Frameworks & Dev Platforms, Aug 1, 2026, 1:00 PM PT
**Format:** 5 minutes, 7 slides

## Contents

- [`DECK_SPEC.md`](DECK_SPEC.md) — reviewer-facing narrative and evidence contract; read this before editing the deck.
- [`slides.md`](slides.md) — Marp source with 499-word speaker notes.
- `slides.pdf` — rendered deck.
- [`blog.md`](blog.md) — companion argument and citations.
- [`evals/README.md`](evals/README.md) — reproducible protocol and complete measurements.
- [`evals/draft-results.json`](evals/draft-results.json) — per-run provenance and raw summary.

## Talk Arc

1. Open with the readiness question and the speaker, not a dense system diagram.
2. Connect controllable small LLMs to coding workflows through AskMe's small actions, fresh execution feedback, bounded updates, and independent acceptance.
3. Use the retained wrong-output-path miss to separate successful actions, reported completion, and an accepted workflow.
4. Frame reasoning as a trajectory hypothesis: preserve progress, repair locally, and replan broadly only after a broken assumption.
5. Keep the two Gemma 4 and two Qwen3.6 variants visible as four descriptive hosted receipts, including 8/8 reported completions and 7/8 accepted artifacts.
6. Show the repeated native pilot needed to test reasoning policy and false completion properly.
7. Answer cautiously: small LLM coding agents are promising with tight execution-grounded harnesses, but the current n=1 smoke does not establish general readiness.

## Evidence Boundary

The talk keeps three kinds of statements separate:

- **Strategic context.** The motivations for smaller models and broader workflow agents come from current model/deployment capabilities and harness research; they are not findings from this repository's smoke test.
- **Measured result.** Four hosted models each ran two deliberately simple harness checks once. All eight agents reported completion; seven outputs met the exact acceptance contract. The retained miss produced working behavior at the wrong path.
- **Hypothesis.** Fast feedback should let reasoning correct locally with few repeated errors and little unnecessary replanning. The current runs did not isolate reasoning mode, model size, or model family, so the deck proposes the experiment needed to test that claim.

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
- [Pi](https://github.com/earendil-works/pi)
- [Oh My Pi, a batteries-included Pi fork](https://github.com/can1357/oh-my-pi)
- [OpenHands](https://github.com/OpenHands/OpenHands)
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
