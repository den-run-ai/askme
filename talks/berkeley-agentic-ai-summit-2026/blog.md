# Are Small LLMs Ready for Coding Agents?

*Small actions, fresh execution feedback, and full-workflow acceptance.*

Two trends are converging.

First, smaller open models are becoming strategically useful. They give a team more control over execution speed, cost, hardware, data location, deployment, and post-training. Google's current Gemma guidance explicitly spans local, edge, and enterprise deployment, recommends starting with the smallest model that can meet the need, and supports modifying open weights through full or parameter-efficient tuning ([run and deployment guide](https://ai.google.dev/gemma/docs/run), [tuning guide](https://ai.google.dev/gemma/docs/tune)).

In this talk, **small is a deployment class, not a strict parameter threshold**. The hosted receipts include roughly 3–4B-active mixtures and 27–31B dense open-weight models. That range is useful for studying controllability and harness compatibility, but the hosted runs do not establish that every model fits or runs well on a local Mac.

That access can create an organizational advantage too. Engineers can inspect, serve, tune, and evaluate the actual model stack instead of only integrating a remote API. I see that as a learning and talent flywheel, although the small experiment in this repository does not measure it.

Second, coding agents are becoming a practical substrate for broader agents. A coding agent already has to plan, navigate state, call tools, preserve artifacts, run long jobs, test behavior, recover, and hand work across boundaries. Those are the same primitives needed for full-lifecycle enterprise and user workflows. More ambitious hyperagents go one step further by making the agent and its harness editable objects ([HyperAgents](https://arxiv.org/abs/2603.19461)).

The harness ties these trends together.

## The Harness Is the Bridge

[Lilian Weng defines a harness](https://lilianweng.github.io/posts/2026-07-04-harness/) as the deployment system around a model: it controls planning, tool use, context, memory, permissions, workflow, and evaluation. Her central design direction is deliberately simple and generic machinery, with a goal-oriented loop that plans, executes, observes or tests, improves, and executes again.

A harness is not one fixed architecture. Current projects choose different system boundaries:

| Approach | Boundary it emphasizes |
|---|---|
| [Pi](https://github.com/earendil-works/pi) | A minimal terminal core extended through skills, prompts, extensions, and packages; stronger isolation is supplied separately. |
| [Oh My Pi](https://github.com/can1357/oh-my-pi) | A batteries-included fork of Pi with hash-anchored edits, IDE tooling, persistent execution workers, memory, and subagents. |
| [OpenHands](https://github.com/OpenHands/OpenHands) | A composable agent SDK and managed local, cloud, or enterprise runtime. |
| [Omnigent (Databricks, OSS alpha)](https://github.com/omnigent-ai/omnigent) | Databricks' open-source alpha meta-harness for swapping or composing agents under shared policies, sandboxes, and sessions; distinct from its [managed beta](https://docs.databricks.com/aws/en/omnigent/). |
| [Databricks' internal evaluation study](https://www.databricks.com/blog/benchmarking-coding-agents-databricks-multi-million-line-codebase) | An organizational evaluation and selection layer built from recent human pull requests, isolated runs, sealed Git history, and held-out tests. |

These are overlapping layers, not a maturity ladder or a leaderboard. Omnigent and the internal study share Databricks provenance but occupy separate layers: the former composes agent harnesses, while the latter evaluates and selects them. Databricks did not compare every project in this table: its workload-specific study compared Pi with the native Claude Code or Codex harness for the same model and thinking effort. It reported more than a 2× task-cost difference in some cases at similar quality, with Pi sending about 3× less context per turn. That is a private-codebase case study, not a universal ranking.

That is the right level of abstraction for AskMe. A useful agent trace looks like this:

| Step | Plan | Agent action | Evidence | Next move |
|---|---|---|---|---|
| 1 | Establish behavior | Run a focused integration test | Failing behavior is located | Keep the plan |
| 2 | Patch the boundary | Edit the smallest affected surface | Focused test passes | Preserve completed work |
| 3 | Check integration | Run related and full checks | No regression is observed | Advance |
| 4 | Accept the result | Verify the required behavior and artifact | Workflow contract is satisfied | Finish |

The interesting work is not syntax repair. It is whether reasoning keeps the workflow contract in view, interprets fresh execution evidence, and changes only the part of the plan that the evidence invalidated.

### Assumptions and current limits

The AskMe loop is most plausible when a workflow decomposes into bounded actions without losing its contract, tool or test feedback arrives quickly enough to guide the next move, and success can be checked independently. It does not by itself solve ambiguous product intent, missing behavioral oracles, adversarial evaluator leakage, or long-horizon context loss.

The published smoke has one additional limit: independent acceptance scored the artifact after the agent stopped. Its failure was not fed back to AskMe for recovery. A stronger operational loop would run a focused acceptance check at tentative completion, return a failure for one bounded correction, and still reserve a separate held-out evaluator for scoring.

## A Working Program Can Still Miss the Workflow

The most useful result in the hosted smoke was not a compiler error. Qwen3.6-35B-A3B wrote the requested `msg.h` and `main.c`, compiled a working program to `/tmp/test`, ran it successfully, saw `REPLAN_OK`, and reported completion.

The required deliverable was `./main`.

The independent acceptance test therefore failed. Every recorded tool action was successful, yet the workflow contract drifted. This is a more realistic agent failure than a missing include: locally plausible progress did not add up to the requested integration result.

It also clarifies two different roles for feedback:

1. **In-loop feedback** is execution or test output returned to the agent so it can choose the next action.
2. **Independent acceptance** is a separate evaluator check used to decide whether the delivered workflow actually meets its contract.

The published smoke used the independent check for scoring after the run; it did not feed that final failure back into AskMe for recovery. A stronger harness would treat a tentative `done` as the trigger for one focused acceptance pass, return any failure for a bounded local correction, and still retain a held-out evaluator check.

## The Reasoning Hypothesis Is About Trajectory Quality

Reasoning matters between tool calls. It should:

- preserve the current goal and completed work;
- interpret execution, test, and integration evidence;
- choose the smallest useful correction;
- continue when the evidence matches the plan;
- replan broadly only when an assumption has actually broken.

This is not an argument for making every call think longer. The useful hypothesis is behavioral: good reasoning should lead to fewer repeated errors, fewer stuck steps, less completed work redone, and less unnecessary plan churn.

The current data does not isolate that effect: the eight cells were not a controlled reasoning experiment. Across the observed traces there was one full replan and two local replans, but the tasks did not reliably require replanning. Those counts are trajectory receipts, not proof that a reasoning policy caused the outcome.

## Read the Smoke at the Right Level

The hosted study ran four hosted models through two scripted checks once each:

- an artifact probe whose prompt supplied the header, source, build command, and run command;
- an obvious script repair used as a low-ceiling harness sanity check.

These checks test action transport, artifact handling, logging, completion state, and acceptance. They are not representative modern software-engineering tasks.

The model rows are still important descriptive context; aggregate totals should
not make the two variants in each family disappear:

| Family | Requested model | Shape | Build | Repair |
|---|---|---|---:|---:|
| Gemma 4 | [26B A4B](https://huggingface.co/google/gemma-4-26B-A4B) | MoE · 25.2B total / 3.8B active | Pass · 603.64s | Pass · 20.00s |
| Gemma 4 | [31B](https://huggingface.co/google/gemma-4-31B) | Dense · 30.7B | Pass · 66.50s | Pass · 22.36s |
| Qwen3.6 | [27B](https://qwen.ai/blog?id=qwen3.6-27b) | Dense · 27B | Pass · 47.88s | Pass · 23.04s |
| Qwen3.6 | [35B-A3B](https://qwen.ai/blog?id=qwen3.6-35b-a3b) | MoE · 35B total / 3B active | **Fail** · 17.67s | Pass · 11.84s |

These are observed hosted agent-wall times, not controlled speed measurements.
The shapes, sizes, and active compute differ, the runs were sequential, and the
Gemma 31B row was selected after the original six outcomes and declared before
its own model calls.

The descriptive contrasts are still worth preserving. Both dense models—Gemma 31B and Qwen3.6-27B—accepted both cells. Gemma 26B A4B also accepted both; Qwen3.6-35B-A3B accepted one of two. Gemma 31B's build trajectory was 9.1× shorter and used 77.7% fewer tokens than Gemma 26B A4B's, but its repair was slightly slower. The fastest observed build trajectory, Qwen3.6-35B-A3B, was the only rejected artifact. These patterns generate questions about trajectory efficiency; they do not answer them.

The result is simple:

- all eight agents reported completion;
- seven of eight artifacts met the exact acceptance contract;
- the retained failure was working behavior at the wrong artifact path;
- every trajectory and its provenance are auditable.

That is one harness result, not a model ranking. One unseeded run per cell, non-randomized sequential runs, different dense and MoE architectures, and a fourth model selected after the original matrix cannot establish anything about Gemma versus Qwen, parameter count, active compute, reasoning quality, reliability, or local-Mac performance.

The full eight-cell table, billing reconciliation, routes, prompts, and commands remain in [`evals/README.md`](evals/README.md) and [`evals/draft-results.json`](evals/draft-results.json) for provenance rather than as a leaderboard.

## What Changes the Answer Next

The presentation does not need an unfinished policy study to make its bounded
claim. The completed smoke already establishes what happened, what the evaluator
caught, and what remains unsupported. Follow-up work should answer the next
practical question with the shortest valid path.

First, close the visible AskMe harness gap: treat tentative completion as a
focused acceptance checkpoint, return a failure for one bounded correction, and
still reserve an independent held-out evaluator for scoring. The retained
wrong-path miss is the regression case for that improvement.

Second, test realistic feature work. [FeatureBench-fast](https://github.com/LiberCoders/FeatureBench)
is the first external target because it evaluates feature development in
existing repositories and supports task-level evaluation. No AskMe adapter or
FeatureBench result exists today. The smallest defensible start is one pinned
public task on a suitable Linux x86_64 Docker host: verify the official gold
patch, verify a harmless control remains unresolved, run AskMe once, and always
score the resulting patch with the official evaluator. That is adapter
qualification—not a FeatureBench score. A result-bearing subset or full split
needs its own frozen protocol.

[Vals Vibe Code Bench](https://www.vals.ai/benchmarks/vibe-code) remains a useful
full-web-application reference if task and evaluator access becomes available.
[ProgramBench](https://github.com/facebookresearch/programbench) remains only a
later clean-room reconstruction stress test. This is a bounded shortlist, not a
commitment to run all three.

The native `off` versus `gated` reasoning-policy study is deferred. It becomes
useful only if the research question is specifically whether AskMe's explicit
reasoning request changes recovery under otherwise fixed conditions. It is not
a prerequisite for this talk, an external-readiness test, or a Qwen-versus-Gemma
or model-size experiment.

## The Claim That Survives

Smaller models make more of the model stack controllable. General-purpose and lifecycle-spanning agents make more of the workflow executable. A tight harness makes the combination operational.

The action protocol should be simple and general. Easier interfaces and more general standards can be good; specialized skills should earn their complexity from repeated failure traces. Execution and test results should ground the next decision, reasoning should update the smallest necessary part of the plan, and acceptance should remain tied to real behavior.

**Make the interface easier to use. Keep success grounded in the workflow.**
