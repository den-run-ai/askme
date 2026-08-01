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

A harness is not one fixed architecture. AskMe is an experimental coding-agent
harness: it keeps an explicit plan, asks the model for one structured action per
turn, executes it, and returns focused evidence. Its model-facing boundary is
deliberately narrower than two prominent alternatives:

| Boundary | AskMe | [pi](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md) | [OpenHands](https://docs.openhands.dev/sdk/arch/tool-system) |
|---|---|---|---|
| Action surface | Six fixed JSON actions; one action per turn | Four default tools; extensions can add or replace tools | Typed, extensible `Action → Observation` tools |
| State and control | Explicit plan, curated slim state, bounded local or full replanning | Model-led JSONL session tree, branching, and lossy compaction; no built-in plan mode | Conversation state and append-only event log; optional persistence and configurable condenser |
| Completion boundary | `done`; conditional fail-open validation; held-out acceptance remains external | The loop ends when tool calls stop; checks come from the workflow or extensions | `finish` signals completion; benchmark evaluation remains a separate harness |

This is a trade-off, not a ranking. AskMe spends more harness structure to reduce
each turn's decision burden. Pi keeps a minimal, extensible, model-led core.
OpenHands supplies a richer lifecycle runtime. All three still need independent
behavioral acceptance. Other projects explore adjacent layers: [Oh My
Pi](https://github.com/can1357/oh-my-pi) packages more capabilities around pi,
while [Omnigent](https://github.com/omnigent-ai/omnigent) composes agents behind
shared policies and sessions. A separate [Databricks private-codebase
study](https://www.databricks.com/blog/benchmarking-coding-agents-databricks-multi-million-line-codebase)
illustrates why harness choice itself needs evaluation, but it is not a universal
ranking of these projects.

A useful AskMe trace looks like this:

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

## An Exit-Zero Command Can Still Miss the Workflow

The most useful result in the hosted smoke was not a compiler error. The retained Qwen3.6-35B-A3B action record shows `cc -o /tmp/test main.c && /tmp/test` returning zero, followed by the agent reporting completion. It does not independently preserve that command's stdout or prove the source contents.

The required deliverable was `./main`.

The independent acceptance test therefore failed. The recorded combined command succeeded, yet the required artifact was absent. This is a more realistic agent failure than a missing include: locally plausible progress did not add up to the requested integration result.

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
- the retained failure was an exit-zero compile-and-run command at the wrong artifact path;
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
existing repositories and supports task-level evaluation. The AskMe adapter is
now implemented and qualified on one pinned public task: the official gold patch
resolved, a harmless nonempty patch applied but remained unresolved, the audit
passed, and the official evaluator returned a categorical outcome. The single
registered model attempt exhausted without emitting a patch, so the task was
unresolved. That is a successful diagnostic run with a negative task outcome—not
a FeatureBench score, reliability estimate, or readiness result. Repeating more
tasks under the same known action bottleneck would add little. A result-bearing
subset should follow an action-interface fix and its own frozen protocol.

*Update (Aug 1, 2026).* That action-interface fix landed as revision 3—a
sentinel-framed write transport, backend-aware budgets (4096/8192 on
OpenRouter), and a write-forcing policy—and the same frozen task was
requalified under preregistered v6 protocols. Under the bundled interface
changes and a changed serving stack, both cells moved from empty patches to
applied but unresolved patches: Gemma 4 31B reached 11/13 F2P (84.62%), with
the same two failures as the exploratory one-attempt pi reference, and
Qwen3.6-27B reached 7/13 (53.85%) versus the pi reference's 10/13. This shows
that harness design was consequential on this task; it does not isolate
sentinel transport or write forcing from the simultaneous serving changes.
Both agents exhausted their planning attempts without emitting `done`, but
their downstream failures differed: Gemma rewrote one file 18 times and ran
zero tests, while Qwen wrote once and returned to observation. These remain one-task adapter
canaries, not FeatureBench scores, reliability estimates, or model
comparisons, and three caveats travel with any v4/pi comparison: the v6 runs
were served by CoreWeave (Gemma bf16, Qwen fp8) while the v4 and pi-ablation
records were served by SiliconFlow fp8—a serving-stack confound, with
identical dated served-model IDs; the issue-15 local-bench neutrality bar was
waived by the maintainer, so no local-neutrality claim is licensed for
revision 3; and three frozen Codex P2 findings on write-forcing mechanics
caveat the Qwen cell's mechanism-level counts. A validate-after-write
counterpart—acceptance pressure after writes, rewrite damping, an
unvalidated-write replan flag—is implemented in open PR #21; v7
requalification is pending.

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

**Evaluate the model, harness, task, and evaluator as one system.**
