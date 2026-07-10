# Agentic AI Summit 2026 - Lightning Talk

**Title:** Small Models, Tight Loops: What a Coding-Agent Harness Is Actually Doing for You

**Slot:** Compass (Saturday), Session 2: Frameworks & Dev Platforms, Aug 1, 2026, 1:00 PM PT
**Format:** 5 minutes, 7 slides

## Contents

- [`slides.md`](slides.md) - Marp source with 499-word speaker notes (about 4:00-4:10 read straight, leaving pause and transition buffer).
- `slides.pdf` - rendered deck.
- [`blog.md`](blog.md) - short companion post.
- [`evals/README.md`](evals/README.md) - exact draft eval protocol and commands.
- `evals/draft-results.json` - compact result data once the authenticated run completes.

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

## Evidence Policy

The deck distinguishes three kinds of evidence:

1. **Historical local traces.** The missing-header timeline comes from three traces of one slow Gemma 4 E4B microtask in [`PERFORMANCE.md`](../../PERFORMANCE.md). It is not presented as a suite-wide percentage or an ablation.
2. **Current hosted smoke.** Three models run two tasks once each through the same NanAgent commit and strictly pinned SiliconFlow routing with FP8 endpoints. A pass requires agent completion and an independently executed postcondition. This is an integration smoke (`n=1`), not a reliability estimate.
3. **Architecture.** Typed errors, JSON repair, task-local replanning, deterministic repair, and completion checks are implementation features documented in [`ARCHITECTURE.md`](../../ARCHITECTURE.md). The talk does not claim each feature has a matched causal ablation.

These tasks are small C/Python build-and-repair jobs. They are not full-app generation. No LiveCodeBench result is used: contest-style single-problem coding does not test the stateful, multi-step tool loop discussed here.

## Primary Sources

- [Agentic AI Summit 2026 program](https://rdi.berkeley.edu/events/agentic-ai-summit-2026)
- [Google Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4) - E4B is dense (4.5B effective / 8B including embeddings); 26B A4B is MoE (25.2B total / 3.8B active).
- [Qwen3.6-27B release](https://qwen.ai/blog?id=qwen3.6-27b) and [model card](https://huggingface.co/Qwen/Qwen3.6-27B)
- [Qwen3.6-35B-A3B release](https://qwen.ai/blog?id=qwen3.6-35b-a3b) and [model card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)
- [OpenRouter models API](https://openrouter.ai/api/v1/models) - exact model IDs and current endpoint metadata

The public program confirms the session and start time. The five-minute duration comes from speaker communications rather than the public agenda.
