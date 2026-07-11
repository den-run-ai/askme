# Smaller Open Models, Full Workflows, Tight Harnesses

Two trends are converging.

First, smaller open models are becoming strategically useful. They give a team more control over execution speed, cost, hardware, data location, deployment, and post-training. Google's current Gemma guidance explicitly spans local, edge, and enterprise deployment, recommends starting with the smallest model that can meet the need, and supports modifying open weights through full or parameter-efficient tuning ([run and deployment guide](https://ai.google.dev/gemma/docs/run), [tuning guide](https://ai.google.dev/gemma/docs/tune)).

That access can create an organizational advantage too. Engineers can inspect, serve, tune, and evaluate the actual model stack instead of only integrating a remote API. I see that as a learning and talent flywheel, although the small experiment in this repository does not measure it.

Second, coding agents are becoming a practical substrate for broader agents. A coding agent already has to plan, navigate state, call tools, preserve artifacts, run long jobs, test behavior, recover, and hand work across boundaries. Those are the same primitives needed for full-lifecycle enterprise and user workflows. More ambitious hyperagents go one step further by making the agent and its harness editable objects ([HyperAgents](https://arxiv.org/abs/2603.19461)).

The harness ties these trends together.

## The Harness Is the Bridge

[Lilian Weng defines a harness](https://lilianweng.github.io/posts/2026-07-04-harness/) as the deployment system around a model: it controls planning, tool use, context, memory, permissions, workflow, and evaluation. Her central design direction is deliberately simple and generic machinery, with a goal-oriented loop that plans, executes, observes or tests, improves, and executes again.

A harness is not one fixed architecture. Current projects choose different system boundaries:

| Approach | Boundary it emphasizes |
|---|---|
| [Pi](https://pi.dev/docs/latest) | A minimal terminal core extended through skills, prompts, extensions, and packages; stronger isolation is supplied separately. |
| [Oh My Pi](https://github.com/can1357/oh-my-pi) | A batteries-included Pi fork with hash-anchored edits, IDE tooling, persistent execution workers, memory, and subagents. |
| [OpenHands](https://github.com/OpenHands/OpenHands) | A composable agent SDK and managed local, cloud, or enterprise runtime. |
| [Omnigent](https://github.com/omnigent-ai/omnigent) | An open-source alpha meta-harness for swapping or composing agents under shared policies, sandboxes, and sessions. |
| [Databricks' internal benchmark](https://www.databricks.com/blog/benchmarking-coding-agents-databricks-multi-million-line-codebase) | An organizational evaluation and selection layer built from recent human pull requests, isolated runs, sealed Git history, and held-out tests. |

These are overlapping layers, not a maturity ladder or a leaderboard. Databricks did not compare every project in this table: its workload-specific study compared Pi with the native Claude Code or Codex harness for the same model and thinking effort. It reported more than a 2× task-cost difference in some cases at similar quality, with Pi sending about 3× less context per turn. That is a private-codebase case study, not a universal ranking.

A second, benchmark-specific signal comes from [Claw-SWE-Bench](https://arxiv.org/abs/2606.12344). Across five harnesses with Qwen 3.6-flash fixed, its reported Pass@1 spread was 27.4 percentage points, from 38.6% to 66.0%; the fixed-GLM 5.1 spread was 12.5 points. Those numbers support treating the harness as an experimental variable, not treating either spread as a general causal effect.

That is the right level of abstraction for AskMe. A useful agent trace looks like this:

| Step | Plan | Agent action | Evidence | Next move |
|---|---|---|---|---|
| 1 | Establish behavior | Run a focused integration test | Failing behavior is located | Keep the plan |
| 2 | Patch the boundary | Edit the smallest affected surface | Focused test passes | Preserve completed work |
| 3 | Check integration | Run related and full checks | No regression is observed | Advance |
| 4 | Accept the result | Verify the required behavior and artifact | Workflow contract is satisfied | Finish |

The interesting work is not syntax repair. It is whether reasoning keeps the workflow contract in view, interprets fresh execution evidence, and changes only the part of the plan that the evidence invalidated.

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

The result is simple:

- all eight agents reported completion;
- seven of eight artifacts met the exact acceptance contract;
- the retained failure was working behavior at the wrong artifact path;
- every trajectory and its provenance are auditable.

That is one harness result, not a model ranking. One unseeded run per cell, non-randomized sequential runs, different dense and MoE architectures, and a post-hoc fourth model cannot establish anything about Gemma versus Qwen, parameter count, active compute, reasoning quality, reliability, or local-Mac performance.

The full eight-cell table, billing reconciliation, routes, prompts, and commands remain in [`evals/README.md`](evals/README.md) and [`evals/draft-results.json`](evals/draft-results.json) for provenance rather than as a leaderboard.

## Test the Real Hypothesis Next

A useful follow-up should start with four native, syntactically valid workflow tasks and semantic integration failures: for example, a CLI whose configuration precedence or stdout/stderr contract is wrong across several modules. Starting natively keeps a new external-benchmark adapter from dominating the first policy comparison.

Before any outcome-bearing call, the pilot should be predeclared and then registered at an immutable public commit or archive. It should:

1. freeze the four tasks, prompts, feedback, held-out checks, budgets, gate predicate, and exclusions before any measured run;
2. compare a system-wide reasoning-off policy with the current composite gated policy under matched conditions;
3. repeat each task three times in randomized policy order: 24 runs per model;
4. use held-out acceptance and false completion—reported `complete` plus failed acceptance—over all valid scheduled runs as the two primary outcomes;
5. report regressions, repeated actions, recovery turns, work redone, local corrections, full replans, latency, and tokens descriptively.

Here, `off` must disable the explicit reasoning channel on planner, executor and retry, local and full replan, and validator calls. `Gated` must freeze the current composite decision table across all of those sites, not only one error classifier. These are explicit-reasoning request policies, not direct measurements of internal cognition.

At this scale, the pilot can reveal only large effects. A null result would still be informative if the deterministic harness guards, rather than extra reasoning, carried the outcome. External suites should follow as generalization checks after the policy path is stable: [FeatureBench-fast](https://github.com/LiberCoders/FeatureBench) first, then [Datacurve's deep-swe benchmark](https://github.com/datacurve-ai/deep-swe), with [Terminal-Bench 2.1](https://www.tbench.ai/news/terminal-bench-2-1) later because it adds the largest harness-inside-harness boundary.

This pilot can support a conclusion only about the tested policy on the frozen tasks. Model-size or model-family claims require a separate, appropriately powered design.

## The Claim That Survives

Smaller models make more of the model stack controllable. General-purpose and lifecycle-spanning agents make more of the workflow executable. A tight harness makes the combination operational.

The action protocol should be simple and general. Easier interfaces and more general standards can be good; specialized skills should earn their complexity from repeated failure traces. Execution and test results should ground the next decision, reasoning should update the smallest necessary part of the plan, and acceptance should remain tied to real behavior.

**Make the interface easier to use. Keep success grounded in the workflow.**
