---
marp: true
theme: default
paginate: true
size: 16:9
style: |
  :root {
    --ink: #111827;
    --muted: #5f6b7a;
    --paper: #f7f8fb;
    --line: #d8dee8;
    --blue: #276ef1;
    --teal: #008f7a;
    --amber: #d97706;
    --coral: #d94c42;
    --terminal: #171a21;
  }
  section {
    background: var(--paper);
    color: var(--ink);
    font-family: Inter, Aptos, "Helvetica Neue", Arial, sans-serif;
    font-size: 24px;
    letter-spacing: 0;
    padding: 40px 54px 46px;
  }
  section::after { color: #8792a2; font-size: 15px; }
  section.title { background: #eef2f7; }
  h1, h2, h3, p { letter-spacing: 0; }
  h1 {
    color: var(--ink);
    font-size: 46px;
    line-height: 1.06;
    margin: 0 0 18px;
  }
  h2 { font-size: 25px; line-height: 1.15; margin: 0 0 12px; }
  p { line-height: 1.32; }
  code { font-family: "SFMono-Regular", Consolas, monospace; }
  .eyebrow {
    color: var(--blue);
    font-size: 15px;
    font-weight: 850;
    margin-bottom: 12px;
    text-transform: uppercase;
  }
  .subtitle { color: var(--muted); font-size: 24px; margin: -8px 0 20px; }
  .tiny { color: var(--muted); font-size: 15px; }
  .source {
    bottom: 18px;
    color: #8792a2;
    font-size: 12px;
    left: 54px;
    position: absolute;
  }
  .trace-table {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 8px;
    box-shadow: 0 16px 40px rgba(17, 24, 39, 0.08);
    overflow: hidden;
  }
  .trace-row {
    align-items: center;
    border-top: 1px solid var(--line);
    display: grid;
    grid-template-columns: 72px 1.15fr 1.35fr 1.15fr;
    min-height: 61px;
  }
  .trace-row.head {
    background: var(--terminal);
    border-top: 0;
    color: #f8fafc;
    font-size: 14px;
    font-weight: 850;
    min-height: 38px;
    text-transform: uppercase;
  }
  .trace-cell { padding: 10px 14px; }
  .trace-cell + .trace-cell { border-left: 1px solid var(--line); }
  .trace-row.head .trace-cell + .trace-cell { border-left-color: #394150; }
  .step { color: var(--blue); font-size: 17px; font-weight: 900; }
  .action { font-family: "SFMono-Regular", Consolas, monospace; font-size: 17px; }
  .evidence { color: var(--teal); font-size: 17px; font-weight: 750; }
  .intro-foot { display: flex; justify-content: space-between; margin-top: 15px; }
  .two-col {
    display: grid;
    gap: 24px;
    grid-template-columns: 0.88fr 1.12fr;
  }
  .contract {
    background: var(--terminal);
    border-radius: 8px;
    color: #f8fafc;
    min-height: 320px;
    padding: 22px 24px;
  }
  .contract .label { color: #8fa2ba; font-size: 14px; font-weight: 850; text-transform: uppercase; }
  .contract .pipeline { font-family: "SFMono-Regular", Consolas, monospace; font-size: 20px; line-height: 1.7; margin-top: 16px; }
  .contract .required { color: #63d8c2; }
  .events { padding-top: 2px; }
  .event {
    background: #fff;
    border-left: 7px solid var(--blue);
    border-radius: 5px;
    margin-bottom: 10px;
    padding: 10px 14px;
  }
  .event strong { display: block; font-size: 18px; }
  .event span { color: var(--muted); font-size: 15px; }
  .event.ok { border-left-color: var(--teal); }
  .event.fail { border-left-color: var(--coral); }
  .callout {
    background: #fff2df;
    border-left: 6px solid var(--amber);
    border-radius: 4px;
    font-size: 19px;
    margin-top: 12px;
    padding: 10px 14px;
  }
  .flow {
    display: grid;
    gap: 12px;
    grid-template-columns: repeat(5, 1fr);
    margin: 22px 0;
  }
  .node {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 7px;
    min-height: 119px;
    padding: 14px;
    position: relative;
  }
  .node:not(:last-child)::after {
    color: var(--blue);
    content: "→";
    font-size: 28px;
    font-weight: 850;
    position: absolute;
    right: -22px;
    top: 40px;
    z-index: 2;
  }
  .node .num { color: var(--blue); font-size: 13px; font-weight: 900; }
  .node strong { display: block; font-size: 18px; margin: 5px 0; }
  .node p { color: var(--muted); font-size: 14px; margin: 0; }
  .decision-grid, .probe-grid, .experiment-grid {
    display: grid;
    gap: 16px;
    grid-template-columns: repeat(3, 1fr);
  }
  .decision, .probe, .experiment {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 7px;
    min-height: 112px;
    padding: 15px 17px;
  }
  .decision strong, .probe strong, .experiment strong { display: block; font-size: 19px; margin-bottom: 6px; }
  .decision p, .probe p, .experiment p { color: var(--muted); font-size: 15px; margin: 0; }
  .decision.local { border-top: 6px solid var(--teal); }
  .decision.replan { border-top: 6px solid var(--coral); }
  .decision.continue { border-top: 6px solid var(--blue); }
  .harness-grid {
    display: grid;
    gap: 10px;
    grid-template-columns: repeat(5, 1fr);
    margin-top: 14px;
  }
  .harness-card {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 8px;
    min-height: 205px;
    padding: 15px 14px;
  }
  .harness-card:nth-child(1) { border-top: 7px solid var(--blue); }
  .harness-card:nth-child(2) { border-top: 7px solid #6b5bd2; }
  .harness-card:nth-child(3) { border-top: 7px solid var(--teal); }
  .harness-card:nth-child(4) { border-top: 7px solid var(--amber); }
  .harness-card:nth-child(5) { border-top: 7px solid var(--coral); }
  .harness-card .kind {
    color: var(--muted);
    font-size: 12px;
    font-weight: 900;
    min-height: 30px;
    text-transform: uppercase;
  }
  .harness-card strong { display: block; font-size: 19px; margin: 8px 0 10px; }
  .harness-card .qualifier {
    color: var(--muted);
    display: block;
    font-size: 12px;
    line-height: 1.2;
    margin-top: 2px;
  }
  .harness-card p { color: var(--muted); font-size: 15px; line-height: 1.3; margin: 0; }
  .harness-seam {
    background: #e9f7f3;
    border-left: 6px solid var(--teal);
    border-radius: 4px;
    color: #285d53;
    font-size: 18px;
    margin-top: 16px;
    padding: 12px 15px;
  }
  .trend-grid {
    align-items: stretch;
    display: grid;
    gap: 16px;
    grid-template-columns: 1fr 0.72fr 1fr;
    margin-top: 20px;
  }
  .trend, .bridge {
    border-radius: 8px;
    min-height: 290px;
    padding: 18px 20px;
  }
  .trend { background: #fff; border: 1px solid var(--line); }
  .trend.model-side { border-top: 7px solid var(--blue); }
  .trend.workflow-side { border-top: 7px solid var(--teal); }
  .trend .label, .bridge .label { font-size: 13px; font-weight: 900; text-transform: uppercase; }
  .trend .label { color: var(--blue); }
  .trend.workflow-side .label { color: var(--teal); }
  .trend h2 { font-size: 23px; margin-top: 6px; }
  .trend ul { font-size: 17px; line-height: 1.45; margin: 13px 0 0; padding-left: 21px; }
  .bridge {
    align-items: center;
    background: var(--terminal);
    color: #f8fafc;
    display: flex;
    flex-direction: column;
    justify-content: center;
    text-align: center;
  }
  .bridge .label { color: #63d8c2; }
  .bridge h2 { color: #fff; font-size: 27px; margin: 12px 0; }
  .bridge p { color: #bcc6d3; font-size: 15px; margin: 0; }
  .formula {
    align-items: center;
    background: var(--terminal);
    border-radius: 8px;
    color: #f8fafc;
    display: flex;
    font-size: 24px;
    font-weight: 800;
    justify-content: center;
    margin: 17px 0;
    padding: 13px;
  }
  .formula span { color: #63d8c2; margin: 0 10px; }
  .probe-grid { grid-template-columns: 1fr 1fr; }
  .probe { min-height: 118px; }
  .probe .label { color: var(--blue); font-size: 13px; font-weight: 900; text-transform: uppercase; }
  .model-lane {
    display: grid;
    gap: 8px;
    grid-template-columns: repeat(4, 1fr);
    margin-top: 14px;
  }
  .model {
    background: #edf4ff;
    border-bottom: 4px solid var(--blue);
    border-radius: 5px;
    font-size: 14px;
    font-weight: 800;
    padding: 8px;
    text-align: center;
  }
  .stats { display: grid; gap: 16px; grid-template-columns: repeat(3, 1fr); margin: 20px 0; }
  .stat {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 8px;
    min-height: 108px;
    padding: 14px 18px;
  }
  .stat .big { color: var(--blue); font-size: 39px; font-weight: 900; line-height: 1; }
  .stat.fail .big { color: var(--coral); }
  .stat p { color: var(--muted); font-size: 15px; margin: 8px 0 0; }
  .claim-table { background: #fff; border: 1px solid var(--line); border-radius: 7px; overflow: hidden; }
  .claim-row { display: grid; grid-template-columns: 0.82fr 1.18fr; }
  .claim-row + .claim-row { border-top: 1px solid var(--line); }
  .claim-row div { font-size: 16px; padding: 10px 14px; }
  .claim-row div + div { border-left: 1px solid var(--line); color: var(--muted); }
  .claim-row strong { color: var(--ink); }
  .experiment-grid { grid-template-columns: repeat(4, 1fr); margin-top: 18px; }
  .experiment { min-height: 164px; }
  .experiment .num { color: var(--blue); font-size: 14px; font-weight: 900; }
  .metrics {
    background: #e9f7f3;
    border-left: 6px solid var(--teal);
    border-radius: 4px;
    color: #285d53;
    font-size: 17px;
    margin-top: 18px;
    padding: 12px 15px;
  }
  .loop {
    display: grid;
    gap: 10px;
    grid-template-columns: repeat(5, 1fr);
    margin: 38px 0 30px;
  }
  .loop .box {
    border-radius: 7px;
    color: #fff;
    font-size: 18px;
    font-weight: 850;
    min-height: 82px;
    padding: 18px 10px;
    text-align: center;
  }
  .loop .box:nth-child(1) { background: var(--blue); }
  .loop .box:nth-child(2) { background: #405670; }
  .loop .box:nth-child(3) { background: var(--amber); }
  .loop .box:nth-child(4) { background: var(--teal); }
  .loop .box:nth-child(5) { background: var(--coral); }
  .tagline { font-size: 36px; font-weight: 850; line-height: 1.2; margin: 0; text-align: center; }
  .closing { color: var(--muted); font-size: 19px; margin-top: 18px; text-align: center; }
---

<!-- _class: title -->

<div class="eyebrow">AskMe · Agentic AI Summit 2026 · UC Berkeley</div>

# Smaller open models.<br>Full workflows. Tight harnesses.

<p class="subtitle">The system layer connecting model control to agent execution</p>

<p class="tiny" style="font-weight:850; margin:0 0 6px; text-transform:uppercase;">Illustrative workflow</p>

<div class="trace-table">
  <div class="trace-row head"><div class="trace-cell">Step</div><div class="trace-cell">Plan</div><div class="trace-cell">AskMe action</div><div class="trace-cell">Evidence</div></div>
  <div class="trace-row"><div class="trace-cell step">01</div><div class="trace-cell">Establish behavior</div><div class="trace-cell action">run focused integration test</div><div class="trace-cell evidence">failure is located</div></div>
  <div class="trace-row"><div class="trace-cell step">02</div><div class="trace-cell">Patch the boundary</div><div class="trace-cell action">edit the smallest surface</div><div class="trace-cell evidence">focused test passes</div></div>
  <div class="trace-row"><div class="trace-cell step">03</div><div class="trace-cell">Check integration</div><div class="trace-cell action">run related + full suite</div><div class="trace-cell evidence">no regression observed</div></div>
  <div class="trace-row"><div class="trace-cell step">04</div><div class="trace-cell">Accept the result</div><div class="trace-cell action">verify required behavior</div><div class="trace-cell evidence">contract satisfied</div></div>
</div>

<div class="intro-foot"><span class="tiny"><strong>Denis Akhiyarov</strong> · Aug 1, 2026</span><span class="tiny">github.com/den-run-ai/askme</span></div>

<!--
Speaker notes (~45s):
Smaller open models give teams more control over latency, hardware, deployment, and
post-training, and may help attract and grow talent. Meanwhile coding agents are
expanding from code edits toward full workflows. The harness connects those trends. An
agent is not one answer; it is this sequence of plans, actions, and fresh evidence. AskMe
makes the sequence visible so we can ask how reasoning turns each result into the next
move.
-->

---

<div class="eyebrow">Why this matters now</div>

# Five approaches, five harness boundaries

<p class="subtitle">Same model; different context, tools, runtime, governance, and evaluation.</p>

<div class="harness-grid">
  <div class="harness-card"><div class="kind">Minimal · extensible</div><strong>Pi</strong><p>Small terminal core. Add skills, prompts, extensions, and packages.</p></div>
  <div class="harness-card"><div class="kind">Tool-rich terminal</div><strong>Oh My Pi</strong><p>Batteries-included Pi fork: IDE tools, workers, memory, and subagents.</p></div>
  <div class="harness-card"><div class="kind">Agent platform</div><strong>OpenHands</strong><p>Composable SDK plus local, cloud, and enterprise runtimes.</p></div>
  <div class="harness-card"><div class="kind">Meta-harness</div><strong>Omnigent <span class="qualifier">(Databricks, OSS alpha)</span></strong><p>Swap or compose agents under policies, sandboxes, and shared sessions.</p></div>
  <div class="harness-card"><div class="kind">Evaluation · selection</div><strong>Databricks</strong><p>Real PR tasks, isolated runs, sealed history, and held-out tests.</p></div>
</div>

<div class="harness-seam"><strong>AskMe isolates one seam:</strong> execution evidence → bounded plan update → independent acceptance.</div>

<div class="source">Sources: Pi → Oh My Pi · OpenHands · Omnigent (Databricks, OSS alpha) · Databricks internal coding benchmark, July 8, 2026</div>

<!--
Speaker notes (~45s):
These are not interchangeable competitors; they choose harness boundaries. Pi keeps a
minimal loop and invites extensions. Oh My Pi is its batteries-included, tool-rich
fork. OpenHands provides an agent SDK and managed runtime. Omnigent is Databricks'
open-source alpha meta-harness; its separate managed counterpart is beta. Databricks'
benchmark adds an evaluation layer: real pull requests, isolated runs, and held-out tests.
AskMe is narrower; it studies how execution evidence becomes a bounded plan update and
an accepted result.
-->

---

<div class="eyebrow">A retained integration miss</div>

# The code ran. The workflow still failed.

<div class="two-col">
  <div class="contract">
    <div class="label">Required contract</div>
    <div class="pipeline">write <span class="required">msg.h + main.c</span><br>build <span class="required">./main</span><br>run <span class="required">./main</span><br>observe <span class="required">REPLAN_OK</span></div>
  </div>
  <div class="events">
    <div class="event ok"><strong>Source and header written</strong><span>The requested program was correct.</span></div>
    <div class="event ok"><strong><code>/tmp/test</code> compiled</strong><span>A different output path was chosen.</span></div>
    <div class="event ok"><strong><code>/tmp/test</code> ran successfully</strong><span>It printed <code>REPLAN_OK</code>.</span></div>
    <div class="event fail"><strong>Agent reported completion</strong><span>The independent acceptance test found no <code>./main</code>.</span></div>
    <div class="callout"><strong>Every recorded action succeeded.</strong> The deliverable contract still drifted.</div>
  </div>
</div>

<div class="source">One hosted build cell · retained under the predeclared failure rule · evals/draft-results.json</div>

<!--
Speaker notes (~45s):
This retained run exposes the agent problem more clearly: correct local execution can
still miss the requested workflow contract across multiple otherwise successful steps
and tools. The agent wrote correct files, compiled slash tmp slash test, ran
it successfully, and declared completion. The acceptance check expected dot slash main.
Every tool action looked green, but the workflow contract was missed. Working code is not
automatically an accepted change.
-->

---

<div class="eyebrow">Design hypothesis</div>

# Reason over fresh evidence. Preserve progress.

<div class="flow">
  <div class="node"><div class="num">01</div><strong>Keep the contract</strong><p>Carry the required behavior and artifact forward.</p></div>
  <div class="node"><div class="num">02</div><strong>Choose one action</strong><p>Advance the current plan with bounded scope.</p></div>
  <div class="node"><div class="num">03</div><strong>Execute</strong><p>Run the tool, focused test, or integration check.</p></div>
  <div class="node"><div class="num">04</div><strong>Interpret</strong><p>Use the new evidence, not reconstructed state.</p></div>
  <div class="node"><div class="num">05</div><strong>Update locally</strong><p>Change only what the evidence invalidated.</p></div>
</div>

<div class="decision-grid">
  <div class="decision continue"><strong>Expected result</strong><p>Continue to the next planned action.</p></div>
  <div class="decision local"><strong>Local mismatch</strong><p>Repair the affected step and rerun its check.</p></div>
  <div class="decision replan"><strong>Broken assumption</strong><p>Replan broadly only when the plan is no longer valid.</p></div>
</div>

<div class="callout"><strong>Trajectory goal:</strong> fewer repeated failures, fewer stuck steps, and less unnecessary plan churn—not longer monologues.</div>

<!--
Speaker notes (~45s):
Reasoning matters between calls. It keeps the current contract in view, interprets
execution or test feedback, and decides how much of the plan changed. A local
failure should produce a local correction while completed work stays completed.
Broad replanning belongs to broken assumptions, not every red command. This is not an
argument for longer monologues. It is a hypothesis about trajectory quality: fewer
repeated errors, fewer stuck steps, and less needless plan churn.
-->

---

<div class="eyebrow">Measured harness smoke · July 10, 2026</div>

# A smoke test—not a model verdict

<div class="formula">4 hosted models <span>×</span> 2 simple checks <span>×</span> 1 run / cell</div>

<div class="stats">
  <div class="stat"><div class="big">8 / 8</div><p>agents reported completion</p></div>
  <div class="stat"><div class="big">7 / 8</div><p>artifacts passed exact acceptance</p></div>
  <div class="stat fail"><div class="big">1</div><p>integration-path miss was retained</p></div>
</div>

<div class="claim-table">
  <div class="claim-row"><div><strong>What it exercises</strong></div><div>Action protocol, trace visibility, completion state, and separate acceptance.</div></div>
  <div class="claim-row"><div><strong>What it does not test</strong></div><div>Modern coding ability, model family or size, reasoning impact, or reliability.</div></div>
</div>

<div class="callout"><strong>The useful receipt:</strong> self-reported completion and accepted workflow behavior are different measurements.</div>

<div class="source">Both checks were deliberately simple. Full prompts, protocol, and outcomes: evals/README.md</div>

<!--
Speaker notes (~40s):
The hosted study is an interface smoke, not a modern coding benchmark. Four hosted models
ran two scripted checks once. All eight agents reported completion; seven artifacts met
the exact acceptance contract. The traces validate logging and expose the path miss, but
they are receipts. One unseeded run per cell, non-randomized sequential runs, different
architectures, and a post hoc fourth model support no family, size, reasoning,
reliability, or local-hardware conclusion.
-->

---

<div class="eyebrow">Pre-August native pilot</div>

# Freeze the smallest clean test

<div class="experiment-grid">
  <div class="experiment"><div class="num">01</div><strong>4 frozen workflows</strong><p>Valid multi-file code, semantic failures, visible feedback, held-out scoring.</p></div>
  <div class="experiment"><div class="num">02</div><strong>2 frozen policies</strong><p>Explicit-reasoning off versus the current composite gated policy.</p></div>
  <div class="experiment"><div class="num">03</div><strong>3 randomized repeats</strong><p>4 × 2 × 3 = 24 scheduled runs per model.</p></div>
  <div class="experiment"><div class="num">04</div><strong>2 primary outcomes</strong><p>Held-out acceptance and false completion across all valid runs.</p></div>
</div>

<div class="metrics"><strong>Secondary:</strong> repeated actions · work redone · local/full replans · latency · tokens</div>

<div class="source">Publish tasks, policies, budgets, gate table, randomization, and exclusions before the first outcome-bearing call.</div>

<!--
Speaker notes (~45s):
Before the summit, freeze the smallest clean pilot rather than rush external adapters.
Use four native semantic workflows, explicit-reasoning off and current-gated policies,
and three randomized repeats: twenty-four scheduled runs per model. Score every valid
run with held-out acceptance and false completion as the two primary outcomes. Treat
recovery and plan stability as descriptive. This is a feasibility study that can reveal
only large effects. FeatureBench and other external suites test generalization later.
-->

---

<div class="eyebrow">Takeaway</div>

# Control the model. Ground the workflow.

<div class="loop">
  <div class="box">Model choice</div>
  <div class="box">Simple contract</div>
  <div class="box">Execution / test</div>
  <div class="box">Local reasoning</div>
  <div class="box">Accepted workflow</div>
</div>

<p class="tagline">Make the interface easier to use.<br>Keep success tied to real behavior.</p>

<p class="closing">Tight harnesses connect controllable models to increasingly capable, full-lifecycle agents.</p>

<p class="tiny" style="text-align:center; margin-top:20px;">github.com/den-run-ai/askme · slides, blog, protocol, and raw summary data</p>

<!--
Speaker notes (~35s):
The claim that survives is a design direction, not a model verdict. Smaller models offer
control; broader agents offer leverage; the harness makes the combination operational.
Prefer a simple, general action protocol. Return execution and test evidence.
Let reasoning update the smallest part of the plan, with acceptance tied to the
real workflow. Easier standards and interfaces can be good. Specialized skills should
earn their complexity from repeated traces.
-->
