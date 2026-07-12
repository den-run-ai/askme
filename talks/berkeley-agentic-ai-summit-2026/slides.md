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
  section.title {
    background: #eef2f7;
    padding: 62px 76px 54px;
  }
  section.title::after { display: none; }
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
  .title-kicker {
    color: var(--blue);
    font-size: 16px;
    font-weight: 850;
    text-transform: uppercase;
  }
  .title-question { margin-top: 68px; max-width: 980px; }
  .title-question h1 {
    font-size: 64px;
    line-height: 1.04;
    margin-bottom: 24px;
  }
  .title-message {
    color: var(--muted);
    font-size: 27px;
    line-height: 1.3;
    margin: 0;
    max-width: 850px;
  }
  .title-speaker {
    align-items: end;
    border-top: 1px solid var(--line);
    display: flex;
    justify-content: space-between;
    margin-top: 92px;
    padding-top: 19px;
  }
  .title-speaker .name { display: block; font-size: 22px; }
  .title-speaker .role { color: var(--muted); display: block; font-size: 16px; margin-top: 4px; }
  .title-links { font-size: 15px; text-align: right; }
  .title-links a { color: var(--blue); text-decoration: none; }
  .title-links span { color: #9aa4b2; margin: 0 8px; }
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
  .bridge-grid {
    display: grid;
    gap: 24px;
    grid-template-columns: 0.9fr 1.2fr 0.9fr;
    margin-top: 26px;
  }
  .bridge-stage {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 8px;
    min-height: 265px;
    padding: 19px 20px;
    position: relative;
  }
  .bridge-stage:not(:last-child)::after {
    color: var(--blue);
    content: "→";
    font-size: 30px;
    font-weight: 900;
    position: absolute;
    right: -23px;
    top: 108px;
    z-index: 2;
  }
  .bridge-stage.model-stage { border-top: 7px solid var(--blue); }
  .bridge-stage.askme-stage {
    background: var(--terminal);
    border-color: var(--terminal);
    color: #f8fafc;
  }
  .bridge-stage.workflow-stage { border-top: 7px solid var(--teal); }
  .bridge-stage .num { color: var(--blue); font-size: 13px; font-weight: 900; }
  .bridge-stage.askme-stage .num { color: #63d8c2; }
  .bridge-stage .label { color: var(--muted); font-size: 13px; font-weight: 900; text-transform: uppercase; }
  .bridge-stage.askme-stage .label { color: #9fb0c5; }
  .bridge-stage h2 { font-size: 22px; margin: 10px 0 14px; }
  .bridge-stage.askme-stage h2 { color: #fff; }
  .bridge-stage ul { font-size: 16px; line-height: 1.5; margin: 0; padding-left: 20px; }
  .bridge-stage p { color: var(--muted); font-size: 15px; line-height: 1.35; margin: 14px 0 0; }
  .bridge-stage.askme-stage p { color: #bcc6d3; }
  .micro-loop {
    background: #242a35;
    border-left: 5px solid #63d8c2;
    border-radius: 4px;
    color: #f8fafc;
    font-family: "SFMono-Regular", Consolas, monospace;
    font-size: 15px;
    line-height: 1.55;
    padding: 13px 14px;
  }
  .bridge-caption {
    background: #e9f7f3;
    border-left: 6px solid var(--teal);
    border-radius: 4px;
    color: #285d53;
    font-size: 18px;
    margin-top: 18px;
    padding: 11px 15px;
  }
  .matrix-summary {
    display: grid;
    gap: 12px;
    grid-template-columns: 0.72fr 0.72fr 1.56fr;
    margin: 13px 0 12px;
  }
  .matrix-summary > div {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 6px;
    min-height: 50px;
    padding: 8px 13px;
  }
  .matrix-summary strong { color: var(--blue); font-size: 24px; margin-right: 7px; }
  .matrix-summary span { color: var(--muted); font-size: 14px; }
  .matrix-summary .boundary {
    align-items: center;
    background: #fff7e9;
    border-color: #f0d5a6;
    color: #8a5700;
    display: flex;
    font-size: 14px;
    font-weight: 800;
  }
  .matrix-grid {
    display: grid;
    gap: 5px;
    grid-template-columns: 1.32fr 1.13fr 1fr 1fr;
  }
  .matrix-cell {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 5px;
    font-size: 15px;
    min-height: 39px;
    padding: 8px 10px;
  }
  .matrix-cell.head {
    background: transparent;
    border-color: transparent;
    color: var(--muted);
    font-size: 13px;
    font-weight: 850;
    min-height: 20px;
    padding-bottom: 3px;
    text-transform: uppercase;
  }
  .matrix-cell.model-name { font-weight: 850; }
  .matrix-cell small { color: var(--muted); display: block; font-size: 12px; margin-top: 2px; }
  .shape { font-weight: 850; }
  .shape.dense { color: var(--blue); }
  .shape.moe { color: #7256c9; }
  .matrix-status { font-size: 13px; font-weight: 900; }
  .matrix-status.pass { color: var(--teal); }
  .matrix-status.fail { color: var(--coral); }
  .matrix-caveat {
    color: var(--muted);
    font-size: 13px;
    line-height: 1.3;
    margin: 10px 0 0;
  }
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

<div class="title-kicker">Agentic AI Summit 2026 · UC Berkeley · Aug 1, 2026</div>

<div class="title-question">

# Are Small LLMs Ready for Coding Agents?

<p class="title-message">A tight harness connects controllable models to accepted full workflows.</p>

</div>

<div class="title-speaker">
  <div><span class="name"><strong>Denis Akhiyarov</strong></span><span class="role">Sr Staff Research Scientist at ServiceNow</span></div>
  <div class="title-links"><a href="https://x.com/den-run-ai">@den-run-ai</a><span>·</span><a href="https://github.com/den-run-ai/askme">github.com/den-run-ai/askme</a></div>
</div>

<!--
Speaker notes (~40s):
The title is a question, not a verdict. Smaller open models are attractive because they
offer control over execution speed, hardware, deployment, post-training, and the builder
ecosystem. Coding agents are also moving beyond code edits toward full-lifecycle
workflows. My question is whether a tight harness can connect those trends: keep each
action small, return real execution evidence, preserve progress, and judge the delivered
workflow.
-->

---

<div class="eyebrow">Why this matters now</div>

# A tight loop connects model control to full workflows

<p class="subtitle">The model gets one bounded move; the harness keeps the end-to-end contract.</p>

<div class="bridge-grid">
  <div class="bridge-stage model-stage"><div class="num">01</div><div class="label">Controllable small LLMs</div><h2>Choose the model boundary</h2><ul><li>Execution speed</li><li>Hardware and deployment</li><li>Post-training access</li></ul></div>
  <div class="bridge-stage askme-stage"><div class="num">02</div><div class="label">AskMe loop</div><h2>One small action at a time</h2><div class="micro-loop">plan → act → execute / test<br>→ fresh evidence ↺</div><p>Interpret the result. Update locally when possible.</p></div>
  <div class="bridge-stage workflow-stage"><div class="num">03</div><div class="label">Accepted full workflow</div><h2>Keep and check the contract</h2><ul><li>Preserve completed work</li><li>Check required behavior</li><li>Accept the real artifact independently</li></ul></div>
</div>

<div class="bridge-caption"><strong>The bridge:</strong> small action surface + fresh execution feedback + independent workflow acceptance.</div>

<!--
Speaker notes (~45s):
The technical bridge: start with a controllable small model. AskMe narrows each turn to
one small action, executes it, and returns fresh test or runtime evidence. The surrounding
harness keeps the full contract and completed work in view. The model can continue,
repair locally, or replan only when an assumption breaks. Independent acceptance then
checks the required behavior and artifact. The point is not a smaller standard; it is a
simpler interface that makes feedback useful.
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

# Four models, eight descriptive receipts

<p class="subtitle">Two variants per family; architecture and active parameters changed.</p>

<div class="matrix-summary">
  <div><strong>8 / 8</strong><span>agent complete</span></div>
  <div><strong>7 / 8</strong><span>artifact accepted</span></div>
  <div class="boundary">Hosted · simple checks · n=1/cell · descriptive only</div>
</div>

<div class="matrix-grid">
  <div class="matrix-cell head">Model</div><div class="matrix-cell head">Shape</div><div class="matrix-cell head">Build observation</div><div class="matrix-cell head">Repair observation</div>
  <div class="matrix-cell model-name">Gemma 4 26B A4B</div><div class="matrix-cell"><span class="shape moe">◆ MoE</span><small>25.2B total · 3.8B active</small></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span><small>603.6s</small></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span><small>20.0s</small></div>
  <div class="matrix-cell model-name">Gemma 4 31B</div><div class="matrix-cell"><span class="shape dense">● Dense</span><small>30.7B parameters</small></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span><small>66.5s</small></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span><small>22.4s</small></div>
  <div class="matrix-cell model-name">Qwen3.6-27B</div><div class="matrix-cell"><span class="shape dense">● Dense</span><small>27B parameters</small></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span><small>47.9s</small></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span><small>23.0s</small></div>
  <div class="matrix-cell model-name">Qwen3.6-35B-A3B</div><div class="matrix-cell"><span class="shape moe">◆ MoE</span><small>3B active</small></div><div class="matrix-cell"><span class="matrix-status fail">NOT ACCEPTED</span><small>wrong artifact path · 17.7s</small></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span><small>11.8s</small></div>
</div>

<p class="matrix-caveat"><strong>Boundary:</strong> architecture, active parameters, run order, and trajectories changed; the 31B follow-up was post-hoc. No size, family, reasoning, or reliability inference.</p>

<div class="source">Four hosted models × two deliberately simple checks × one unseeded run/cell · evals/README.md</div>

<!--
Speaker notes (~45s):
This is the hosted matrix: two Gemma 4 variants and two Qwen3.6 variants, including dense
and mixture-of-experts shapes. Every agent reported completion. Seven artifacts were
accepted. The Qwen thirty-five B A3B build is the retained wrong-path miss; every repair
cell passed. Timings describe these observed trajectories only. Each cell is one unseeded
run on simple checks, and the Gemma thirty-one B addition was post-hoc. Nothing here
supports a size, architecture, family, reasoning, or reliability conclusion.
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

<div class="eyebrow">Answer to the title</div>

# Promising—with a tight harness.

<div class="loop">
  <div class="box">Model choice</div>
  <div class="box">Simple contract</div>
  <div class="box">Execution / test</div>
  <div class="box">Local reasoning</div>
  <div class="box">Accepted workflow</div>
</div>

<p class="tagline">Make the interface easier to use.<br>Keep success tied to real behavior.</p>

<p class="closing">The smoke validates this interface; readiness needs repeated held-out workflows.</p>

<p class="tiny" style="text-align:center; margin-top:20px;">github.com/den-run-ai/askme · slides, blog, protocol, and raw summary data</p>

<!--
Speaker notes (~35s):
The claim that survives today is a design direction, not a model verdict. Smaller models offer
control; broader agents offer leverage; the harness makes the combination operational.
Prefer a simple, general action protocol. Return execution and test evidence.
Let reasoning update the smallest part of the plan, with acceptance tied to the
real workflow. Easier standards and interfaces can be good. Specialized skills should
earn their complexity from repeated traces.
-->
