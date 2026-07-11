# Smaller Open Models, Full Workflows, Tight Harnesses

Two trends are converging.

First, smaller open models are becoming strategically useful. They give a team more control over execution speed, cost, hardware, data location, deployment, and post-training. Google's current Gemma guidance explicitly spans local, edge, and enterprise deployment, recommends starting with the smallest model that can meet the need, and supports modifying open weights through full or parameter-efficient tuning ([run and deployment guide](https://ai.google.dev/gemma/docs/run), [tuning guide](https://ai.google.dev/gemma/docs/tune)).

That access can create an organizational advantage too. Engineers can inspect, serve, tune, and evaluate the actual model stack instead of only integrating a remote API. I see that as a learning and talent flywheel, although the small experiment in this repository does not measure it.

Second, coding agents are becoming a practical substrate for broader agents. A coding agent already has to plan, navigate state, call tools, preserve artifacts, run long jobs, test behavior, recover, and hand work across boundaries. Those are the same primitives needed for full-lifecycle enterprise and user workflows. More ambitious hyperagents go one step further by making the agent and its harness editable objects ([HyperAgents](https://arxiv.org/abs/2603.19461)).

The harness ties these trends together.

## The Harness Is the Bridge

[Lilian Weng defines a harness](https://lilianweng.github.io/posts/2026-07-04-harness/) as the deployment system around a model: it controls planning, tool use, context, memory, permissions, workflow, and evaluation. Her central design direction is deliberately simple and generic machinery, with a goal-oriented loop that plans, executes, observes or tests, improves, and executes again.

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

## Reasoning Should Shorten the Trajectory

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

A useful follow-up should start with syntactically valid multi-file code and a semantic integration failure: for example, a CLI whose configuration precedence or stdout/stderr contract is wrong across several modules.

The experiment should:

1. expose focused unit, execution, and integration feedback inside the loop;
2. retain a separate held-out acceptance check;
3. compare reasoning off, gated on semantic uncertainty, and always on;
4. repeat and randomize runs under matched conditions;
5. measure accepted outcomes, regressions, repeated actions, recovery turns, completed work redone, local corrections, full replans, latency, and tokens.

Only then would a comparison of reasoning policies, model sizes, or model families support a conclusion.

## The Claim That Survives

Smaller models make more of the model stack controllable. General-purpose and lifecycle-spanning agents make more of the workflow executable. A tight harness makes the combination operational.

The action protocol should be simple and general. Easier interfaces and more general standards can be good; specialized skills should earn their complexity from repeated failure traces. Execution and test results should ground the next decision, reasoning should update the smallest necessary part of the plan, and acceptance should remain tied to real behavior.

**Make the interface easier to use. Keep success grounded in the workflow.**
