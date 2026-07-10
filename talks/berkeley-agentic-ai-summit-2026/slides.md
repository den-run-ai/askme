---
marp: true
theme: default
paginate: true
size: 16:9
style: |
  section {
    font-size: 26px;
  }
  section h1 {
    font-size: 44px;
  }
  section.lead h1 {
    font-size: 56px;
  }
  table {
    font-size: 21px;
  }
  .small {
    font-size: 19px;
    color: #666;
  }
---

<!-- _class: lead -->

# Are Small LLMs Ready for Coding Agents?

## Spoiler: yes — if the harness does its share of the work

**Denis Akhiyarov** · Agentic AI Summit, UC Berkeley · Aug 1, 2026

<span class="small">One agent. One file. One laptop. ~7 tokens/sec. Let's see how far that gets us.</span>

<!--
Speaker notes (~40s):
Hi, I'm Denis. Everyone here is building agents on frontier models. I did the opposite:
I built a coding agent that runs on a 4B-active model on a 16GB M1 laptop, at 7 tokens
per second. Not because it's a good idea — because it's the fastest way to learn what
the HARNESS is actually doing for you. When the model is weak, every flaw in your agent
loop is exposed immediately. This talk is 5 minutes of what I learned.
-->

---

# The harness is half the model

**NanAgent**: single Python file, no frameworks. Preflight → Plan → Execute → Replan.

What it took to make a 4B-active model finish real coding tasks:

| Harness trick | Why the small model needs it |
|---|---|
| `edit` action (find/replace) | Full-file `write` blows the 256-token budget |
| Mechanical JSON repair | ~60% of first edit attempts emit broken JSON |
| Error-**class** retry policy | "File not found" needs a read, not more thinking |
| Task-local replan | Fix one failed task in ~5s instead of a ~110s full replan |
| Slim executor state (~200 tok) | 16K context, 7 tok/s — every token is wall-clock |

**Each of these was worth more than a model-size upgrade on our tasks.**

<!--
Speaker notes (~45s):
The agent itself is boring on purpose: plan, execute steps, replan on failure. The
interesting part is what a small model FORCES you to build. Example: the model can't
reliably write a whole file in 256 tokens, so you add an edit action — 40 tokens instead
of 200+. It fails JSON on 60% of first edit attempts, so you repair JSON mechanically
before burning a retry. A failed edit doesn't need "more thinking" — it needs to read
the file first, so retries branch on error CLASS. Every row in this table moved our
numbers more than swapping in a bigger model would have. The harness is half the model.
-->

---

# The contestants: three sizes of "small"

| Model | Shape | Runs on | Vibe |
|---|---|---|---|
| **Gemma 4 E4B** | MoE 12B / 4B active | 16GB M1 laptop, ~7 tok/s | The scrappy one |
| **Gemma 4 26B-A4B** | MoE 26B / 4B active | Hosted, ~1–2s per step | The sweet spot |
| **Qwen3.6-27B / 35B-A3B** | Dense 27B / MoE 3B active | Single 24GB GPU | The new open coder |

- Same harness, same tests, three size classes — size is the only variable
- Qwen3.6-27B: arguably the best open coder on a single consumer GPU right now
- (RIP Qwen 3.5 9B — always-on `<think>` blocks fought the harness and lost)

<!--
Speaker notes (~40s):
Three size classes. Gemma 4 E4B — 4B active parameters, runs on my laptop. Gemma 4
26B — same 4B-active MoE recipe, hosted, basically interactive speed. And the new
Qwen3.6 pair — the 27B dense is probably the best open-weight coder you can run on
one consumer GPU today. Same harness across all of them, so model size is the only
variable. And a moment of silence for Qwen 3.5 9B, which we dropped: its always-on
thinking blocks leaked into outputs and broke JSON parsing more than it helped.
-->

---

# Full app generation mode: the receipts

Agent harness suite, easy → hard (build/fix/replan tasks), 3 trials each:

| Suite | E4B local (median) | 26B hosted (median) | Pass |
|---|---|---|---|
| Easy | 20–119s | 2.5–5s | Both 9/9 |
| Medium | 29–609s | 2–39s | Both 9/9 |
| Hard | 80–904s | 9.5–43s | Both 9/9 |

- **Same completion rate.** The small model gets there — it just takes the scenic route (4–24× slower)
- Where the time goes: failed edits → thinking retries → re-reads. **~60% was harness-fixable**
- Full app generation ("build me a working X, compiled and running") is the same loop, more tasks — final validation catches "all tasks passed but the app doesn't run"

<!--
Speaker notes (~45s):
Here's the punchline table. After the harness work, the laptop model passes everything
the hosted model passes — 9 out of 9 on the hard suite, 100% completion. It's just 4 to
24 times slower. And when we profiled WHERE the time went, about 60% was harness-fixable
waste: failed edits triggering thinking escalation, thinking eating the token budget,
re-reads. Not model stupidity — harness debt. Full app generation is the same loop
scaled up, plus a final validation pass, because "every task succeeded" and "the app
actually runs" are very different claims.
-->

---

# Benchmarks that actually smell like app coding

(SWE-bench is great — it's also patch-fixing in mature repos, not *building apps*)

- **WebDev Arena** (LMArena) — humans blind-vote on generated web apps. Elo, not unit tests. Frontier models lead; open ~30B models are climbing into the same bracket
- **WebCoderBench** — 1,572 *real user requests* for web apps, interpretable metrics. Messy prompts included, which is exactly how users talk
- **LiveCodeBench** — continuously refreshed problems = contamination-free. The honest "can it code at all" floor

**Pattern across all three: small open models trail frontier by one league, not one era.**

<!--
Speaker notes (~40s):
When people ask "is it ready", they usually cite SWE-bench. But SWE-bench is patching
mature repos — app generation is a different job. Three benchmarks that fit better:
WebDev Arena, where humans blind-vote on actual generated apps; WebCoderBench, built
from over 1,500 real user requests, messy phrasing and all; and LiveCodeBench as the
contamination-free floor for raw coding. The consistent pattern: current small open
models are one league below frontier — not one era. That gap is closable, and harnesses
close part of it.
-->

---

# Benchmarks lie a little. Run the app.

Our spot-check ritual for generated apps — 10 minutes, no leaderboard needed:

1. **Does it run?** Not "does it compile" — `npm start` / `python app.py` and click around
2. **The second feature.** First feature is always fine; feature #2 is where small models quietly drop state, break routes, forget the DB schema
3. **Error paths.** Empty input, missing file, wrong port — small models write the happy path and *only* the happy path
4. **Read the diff like a reviewer**, not a fan

Small-model failures are **boring and mechanical** (truncation, path typos, lost context) → exactly what harnesses catch. Frontier failures are subtle design mistakes → much harder to guard.

<!--
Speaker notes (~40s):
And whatever the leaderboard says — run the app. Our ritual: actually launch it and
click around. Then check the SECOND feature, because the first one always works and the
second is where small models drop state or forget the schema. Then the error paths,
because small models write only the happy path. The encouraging part: small-model
failures are boring — truncation, path typos, lost context. Boring failures are exactly
what a harness can catch mechanically. Frontier model failures are subtle design
mistakes, and those are much harder to guard against.
-->

---

# So... are they ready?

**Ready today —** bounded coding-agent work: build-fix-test loops, scaffolding, file surgery, CI bots, private/offline/on-device agents. *If* your harness has typed errors, cheap recovery, and real validation.

**Not yet —** one-shot full apps at frontier quality, long-horizon refactors, >16K-context reasoning. Frontier models still win the *leap*; small models win the *loop*.

**The bet:** capability that needed a frontier model 18 months ago runs on a laptop today. Harness work is the *durable* asset — every trick transfers upward, and models only get smaller-better.

<span class="small">Code, benchmarks & the full pain journal: github.com/den-run-ai/askme — thanks!</span>

<!--
Speaker notes (~40s):
So, are small LLMs ready for coding agents? For bounded agentic work — build-fix-test
loops, scaffolding, offline and private deployments — yes, today, if your harness pulls
its weight. For one-shot full apps at frontier quality — not yet. Frontier wins the
leap; small models win the loop. But here's the bet: what needed a frontier model 18
months ago runs on my laptop today, and every hour of harness engineering transfers
upward to every future model. Repo's on GitHub, including the full pain journal.
Thank you!
-->
