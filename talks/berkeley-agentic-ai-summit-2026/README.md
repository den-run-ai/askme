# Berkeley Agentic AI Summit 2026 — Lightning Talk

**Title:** Are Small LLMs Ready for Coding Agents?
**Slot:** Session 2 — Agentic AI Frameworks & Developer Platforms, Sat Aug 1, 1:00 PM PT
**Format:** 5 minutes, 7 slides

## Contents

- [`slides.md`](slides.md) — the deck, in [Marp](https://marp.app/) markdown. Speaker notes are inline as HTML comments under each slide (~40s each ≈ 4:50 total, leaving buffer for the walk-on).
- `slides.pdf` — rendered deck (upload this to the speaker form).

## Rendering

```bash
npx @marp-team/marp-cli talks/berkeley-agentic-ai-summit-2026/slides.md --pdf --allow-local-files
# or for presenting with notes:
npx @marp-team/marp-cli slides.md --preview
```

## Sources for the numbers

All harness/agent numbers come from this repo:

- [PERFORMANCE.md](../../PERFORMANCE.md) — E01 baseline (27/27 OpenRouter, easy/medium local), 2026-05-03 hard suite (9/9 local), 4–24× local-vs-hosted gap, the ~60% scaffold-addressable time breakdown of `fix_missing_include`, and the Qwen 3.5 → Gemma 4 switch rationale.
- [ARCHITECTURE.md](../../ARCHITECTURE.md) — the harness mechanisms named on slide 2 (edit action, JSON repair, error-class retry policy, task-local replan, slim executor state).

External references:

- Qwen3.6 sizes and coding focus: [Qwen3.6-27B blog](https://qwen.ai/blog?id=qwen3.6-27b), [Qwen3.6-35B-A3B on Hugging Face](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)
- [WebDev Arena](https://web.lmarena.ai/) — human-preference Elo on generated web apps
- [WebCoderBench](https://arxiv.org/html/2601.02430v1) — 1,572 real user requirements for web app generation
- [LiveCodeBench](https://livecodebench.github.io/) — contamination-free code generation

Note: Qwen3.6 (27B / 35B-A3B) appears on the models slide as the current open-coder size class; NanAgent's measured results in PERFORMANCE.md cover Gemma 4 E4B and Gemma 4 26B-A4B. Qwen3.6 harness runs are future work.
