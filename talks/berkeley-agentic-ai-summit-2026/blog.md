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

## A Six-Run Draft Smoke

For the Berkeley talk, I added a deliberately small OpenRouter comparison. It runs three hosted models through the same NanAgent commit and strictly pinned SiliconFlow routing with FP8 endpoints:

- Google Gemma 4 26B A4B
- Qwen3.6-27B
- Qwen3.6-35B-A3B

Each model gets one task run for each of two jobs:

- **Multi-file build:** write `msg.h` and `main.c`, compile them, and leave an executable whose output contains `REPLAN_OK`.
- **Repair:** fix a syntax error in `greet.py`, then leave a script that exits zero and prints `hello`.

The first/no-retry call explicitly disables reasoning for every model; retries enable the same harness reasoning policy. A pass requires both `agent_complete` and the deterministic postcondition. Model ID, selected endpoint, calls, tokens, billed credits, commit, and dirty state are recorded.

<!-- EVAL_RESULTS_START -->
| Model | Build + run | Repair + run | LLM calls | Billed credits |
|---|---:|---:|---:|---:|
| Gemma 4 26B A4B | Pending authenticated run | Pending authenticated run | - | - |
| Qwen3.6-27B | Pending authenticated run | Pending authenticated run | - | - |
| Qwen3.6-35B-A3B | Pending authenticated run | Pending authenticated run | - | - |
<!-- EVAL_RESULTS_END -->

This is `n=1` per cell. It can reveal that a model/provider path cannot follow the action contract or recover on one task. It cannot estimate a success rate, rank the models, or prove that any of them can build a full application reliably.

The exact commands and repeat policy live in [`evals/README.md`](evals/README.md). The initial run was not billed because the repository's `.env` key returned `401 User not found`; the table will be filled only from a successful authenticated run.

## Why Not LiveCodeBench?

LiveCodeBench asks a useful raw-coding question: can a model solve date-stamped contest problems? It does not exercise the question in this project: can a model hold state across multiple tool calls, modify several artifacts, react to real command output, and satisfy an executable end condition?

For agent work, I would rather have a tiny task with a real compiler and a strict postcondition than a large score from a benchmark aimed at a different unit of work.

## The Claim That Survives

The evidence here is promising but small. It covers a few scripted build-and-repair loops, not application architecture, UX, maintainability, or long-horizon refactoring. The older local runs and the new hosted smoke also answer different questions and should not be collapsed into a controlled model-size comparison.

The useful conclusion is narrower: **the harness is a durable part of the capability.** Typed actions, error-aware recovery, real tools, and external checks help a small model today and a larger model tomorrow. Small models do not need easier standards. They need tighter feedback loops.
