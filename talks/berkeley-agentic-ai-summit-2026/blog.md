# Small Models, Tight Loops

I started with a broad question: **are small LLMs ready for coding agents?**

That question is too vague to benchmark honestly. "Coding" can mean filling in one function, resolving an issue in a mature repository, or spending twenty minutes creating files, calling tools, recovering from errors, and checking whether the result actually runs. A coding agent is the last one. Its model matters, but so does everything wrapped around the model.

NanAgent is my deliberately small way to study that wrapper: one Python file, no agent framework, and a loop that plans tasks, emits structured actions, runs real tools, and replans when something fails.

## One Boring Bug

The most useful trace came from an unglamorous C program:

```c
int main() {
    printf("FIXED\n");
    return 0;
}
```

The missing `#include <stdio.h>` is not a reasoning puzzle. The compiler names the problem. Yet an early version of the agent handled every failed action the same way: ask the model to think harder and try again.

On three traces of this one slow local microtask, a failed edit could lead to a thinking retry and then a long re-read. That recovery call took 140-253 seconds. This is narrow evidence, not a suite-wide statistic, but it exposed a systems bug: the harness was paying the model to rediscover state that a deterministic tool had already supplied.

The better loop looks like this:

```text
compiler stderr
    -> [compile_error]
    -> exact file context or a narrowly matched repair
    -> rerun the command
    -> execute an external postcondition
```

Five changes matter in practice:

1. **Typed failures.** A missing file, failed exact-match edit, timeout, and compiler error should not share one generic retry path.
2. **Exact context before edits.** Read the file, then ask for a small replacement. Do not make the model reconstruct old text from memory.
3. **Deterministic work for deterministic facts.** If a compiler diagnostic and source file uniquely identify a safe repair, code can do it more cheaply and predictably.
4. **Task-local replanning.** Replace the failed task instead of regenerating a whole plan that mostly worked.
5. **External validation.** "The model said done" is not a postcondition. Run the binary or script outside the model's own judgment.

These are engineering responses to observed failure modes. They are not five independent ablations, and I do not claim each one beats a model upgrade.

## An Eight-Cell Hosted Smoke

For the Berkeley talk, I added a deliberately small OpenRouter comparison. The original six-cell matrix ran three hosted models; after those outcomes were frozen, I added dense Gemma 4 31B as a two-cell exploratory extension. Every model used the same NanAgent implementation with SiliconFlow-only routing and fallbacks disabled. In authenticated endpoint-catalog snapshots, each model had one SiliconFlow match and each match was FP8:

- Google Gemma 4 26B A4B
- Google Gemma 4 31B
- Qwen3.6-27B
- Qwen3.6-35B-A3B

Each model gets one task run for each of two jobs:

- **Multi-file build:** write `msg.h` and `main.c`, compile them, and leave an executable whose output contains `REPLAN_OK`.
- **Repair:** fix a syntax error in `greet.py`, then leave a script that exits zero and prints `hello`.

The first/no-retry call explicitly disables reasoning for every model; retries use the same harness policy. A pass requires pytest, `agent_complete`, and an independently repeated deterministic postcondition. Requested and served model, provider, usage-bearing responses, tokens, billed credits, commit, and dirty state are recorded.

<!-- EVAL_RESULTS_START -->
| Model | Build (P/A/X) | Repair (P/A/X) | Usage responses | Billed credits |
|---|---:|---:|---:|---:|
| Gemma 4 26B A4B | **PASS** (✓/✓/✓; 603.6s) | **PASS** (✓/✓/✓; 20.0s) | 39 | $0.00414692 |
| Gemma 4 31B | **PASS** (✓/✓/✓; 66.5s) | **PASS** (✓/✓/✓; 22.4s) | 19 | $0.00132133 |
| Qwen3.6-27B | **PASS** (✓/✓/✓; 47.9s) | **PASS** (✓/✓/✓; 23.0s) | 22 | $0.00496840 |
| Qwen3.6-35B-A3B | **FAIL** (✗/✓/✗; 17.7s) | **PASS** (✓/✓/✓; 11.8s) | 14 | $0.00187520 |
<!-- EVAL_RESULTS_END -->

`P/A/X` means pytest, agent completion, and the external check. “Usage responses” counts responses with token-usage events; raw chat-completions HTTP attempts were not instrumented. The per-model counts and credits sum both tasks. Across all eight cells, seven passed all three criteria and all eight agents reported completion. The 94 usage responses reported $0.01231185 in cost; the original matrix, 31B extension, and combined total each reconciled exactly to the API key usage delta.

The failure is the interesting receipt. Qwen3.6-35B-A3B created correct source and header files, compiled and ran `/tmp/test`, then reported completion. It did not leave the required `main` executable, so pytest and the independent probe failed it. The result was retained rather than replaced.

The added Gemma pair gives an exploratory size-and-architecture contrast. Both variants passed both cells. On the build, dense 31B finished in 66.5 seconds and 8 responses versus 603.6 seconds and 29 responses for 26B A4B. On repair, however, 31B was slightly slower and used one more response: 22.4 seconds and 11 responses versus 20.0 seconds and 10. It still used fewer tokens and cost slightly less.

That is not a clean estimate of the impact of size. Gemma 4 31B is a dense 30.7B model; 26B A4B is a 25.2B-total MoE with 3.8B active per token. Architecture, active compute, stochastic trajectory, and provider load all change or remain uncontrolled. The build difference is a useful observation, while the repair result warns against turning it into “larger is always faster.”

This is `n=1` per cell. It can reveal that a model/provider path missed one action contract. It cannot estimate a success rate, rank the models, isolate model size, or prove that any of them can build a full application reliably.

The exact commands, per-cell metrics, routing provenance, and repeat policy live in [`evals/README.md`](evals/README.md) and [`evals/draft-results.json`](evals/draft-results.json). Before the recorded matrix, an earlier setup attempt with the old key returned `401 User not found`. It produced no model response, token event, or billed usage and is retained only in the infrastructure audit.

## Why Not LiveCodeBench?

LiveCodeBench asks a useful raw-coding question: can a model solve date-stamped contest problems? It does not exercise the question in this project: can a model hold state across multiple tool calls, modify several artifacts, react to real command output, and satisfy an executable end condition?

For agent work, I would rather have a tiny task with a real compiler and a strict postcondition than a large score from a benchmark aimed at a different unit of work.

## The Claim That Survives

The evidence here is promising but small. It covers a few scripted build-and-repair loops, not application architecture, UX, maintainability, or long-horizon refactoring. The older local runs and the new hosted smoke also answer different questions and should not be collapsed into a controlled model-size comparison.

The useful conclusion is narrower: **the harness is a durable part of the capability.** Typed actions, error-aware recovery, real tools, and external checks help a small model today and a larger model tomorrow. Small models do not need easier standards. They need tighter feedback loops.
