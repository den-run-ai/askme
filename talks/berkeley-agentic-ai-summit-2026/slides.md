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
  .bridge-caption span { display: block; font-size: 14px; margin-top: 3px; }
  .result-limit {
    color: var(--muted);
    font-size: 14px;
    margin: 8px 0 0;
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
    grid-template-columns: 1.05fr 1.32fr 1.32fr;
  }
  .matrix-cell {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 5px;
    font-size: 14px;
    min-height: 52px;
    padding: 7px 10px;
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
  .matrix-cell small { color: var(--muted); display: block; font-size: 11px; line-height: 1.28; margin-top: 2px; }
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
  .matrix-interpretation {
    display: grid;
    gap: 10px;
    grid-template-columns: 0.9fr 1.4fr;
    margin-top: 10px;
  }
  .matrix-interpretation > div {
    border-radius: 5px;
    font-size: 13px;
    line-height: 1.3;
    padding: 8px 11px;
  }
  .matrix-interpretation strong { display: block; font-size: 14px; margin-bottom: 2px; }
  .matrix-interpretation .supported { background: #e9f7f3; color: #285d53; }
  .matrix-interpretation .unsupported { background: #fff2df; color: #7a5008; }
  .experiment-grid { grid-template-columns: repeat(4, 1fr); margin-top: 18px; }
  .experiment { min-height: 145px; }
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
  .pilot-status {
    background: #fff2df;
    border-left: 6px solid var(--amber);
    border-radius: 4px;
    color: #7a5008;
    font-size: 17px;
    font-weight: 850;
    margin-top: 13px;
    padding: 10px 15px;
  }
  .external-status {
    background: #edf4ff;
    border-left: 6px solid var(--blue);
    border-radius: 4px;
    color: #27476f;
    font-size: 14px;
    margin-top: 8px;
    padding: 7px 13px;
  }
  .boundary-grid {
    display: grid;
    gap: 16px;
    grid-template-columns: repeat(3, 1fr);
    margin-top: 15px;
  }
  .boundary-step {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 8px;
    min-height: 190px;
    padding: 18px 19px;
    position: relative;
  }
  .boundary-step:not(:last-child)::after {
    color: var(--blue);
    content: "→";
    font-size: 30px;
    font-weight: 900;
    position: absolute;
    right: -25px;
    top: 80px;
    z-index: 2;
  }
  .boundary-step.run { border-top: 7px solid var(--blue); }
  .boundary-step.entry { border-top: 7px solid var(--coral); }
  .boundary-step.delivery { border-top: 7px solid var(--teal); }
  .boundary-step.gap { border-top: 7px solid var(--coral); }
  .boundary-step .num { color: var(--blue); font-size: 14px; font-weight: 900; }
  .boundary-step strong { display: block; font-size: 20px; margin: 8px 0 12px; }
  .boundary-step p { color: var(--muted); font-size: 16px; margin: 0; }
  .boundary-step code { color: var(--ink); font-size: 14px; }
  .boundary-observation {
    background: #fff2df;
    border-left: 6px solid var(--amber);
    border-radius: 4px;
    color: #7a5008;
    font-size: 17px;
    margin-top: 12px;
    padding: 9px 13px;
  }
  .harness-grid {
    display: grid;
    gap: 5px;
    grid-template-columns: 0.72fr repeat(3, 1fr);
    margin-top: 18px;
  }
  .harness-cell {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 5px;
    font-size: 14px;
    line-height: 1.28;
    min-height: 83px;
    padding: 11px 12px;
  }
  .harness-cell.head {
    background: var(--terminal);
    border-color: var(--terminal);
    color: #fff;
    font-size: 17px;
    font-weight: 850;
    min-height: 24px;
  }
  .harness-cell.row-head {
    align-items: center;
    color: var(--blue);
    display: flex;
    font-size: 13px;
    font-weight: 900;
    text-transform: uppercase;
  }
  .harness-caption {
    background: #e9f7f3;
    border-left: 6px solid var(--teal);
    border-radius: 4px;
    color: #285d53;
    font-size: 16px;
    line-height: 1.3;
    margin-top: 14px;
    padding: 10px 14px;
  }
  .conclusion-grid {
    display: grid;
    gap: 16px;
    grid-template-columns: repeat(3, 1fr);
    margin: 30px 0 26px;
  }
  .conclusion-card {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 7px;
    min-height: 190px;
    padding: 18px 19px;
  }
  .conclusion-card.observed { border-top: 7px solid var(--blue); }
  .conclusion-card.supported { border-top: 7px solid var(--teal); }
  .conclusion-card.open { border-top: 7px solid var(--amber); }
  .conclusion-card strong { display: block; font-size: 20px; margin-bottom: 10px; }
  .conclusion-card p { color: var(--muted); font-size: 16px; margin: 0; }
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

<p class="title-message">Can tight execution feedback turn structured small-model actions into accepted workflows?</p>

</div>

<div class="title-speaker">
  <div><span class="name"><strong>Denis Akhiyarov</strong></span><span class="role">Sr Staff Research Scientist at ServiceNow</span></div>
  <div class="title-links"><a href="https://x.com/den-run-ai">@den-run-ai</a><span>·</span><a href="https://github.com/den-run-ai/askme">github.com/den-run-ai/askme</a></div>
</div>

<!--
Speaker notes (~40s):
The title is a question, not a verdict. Here, small is a deployment class, not one
parameter cutoff. The hosted receipts span roughly three-to-four-billion-active mixtures
and twenty-seven-to-thirty-one-billion dense models; they do not measure local
performance. Teams want control over speed, hardware, deployment, and post-training on
their chosen hardware and stack. The question is whether tight execution feedback can
turn structured actions into accepted workflows.
-->

---

<div class="eyebrow">Why this matters now</div>

# AskMe gives a small model one structured move at a time

<p class="subtitle"><strong>AskMe</strong> is an experimental coding-agent harness: explicit plan → one JSON action → execution evidence ↺</p>

<div class="bridge-grid">
  <div class="bridge-stage model-stage"><div class="num">01</div><div class="label">Controllable small LLMs</div><h2>Choose the model boundary</h2><ul><li>Execution speed</li><li>Hardware and deployment</li><li>Post-training access</li></ul></div>
  <div class="bridge-stage askme-stage"><div class="num">02</div><div class="label">AskMe loop</div><h2>One small action at a time</h2><div class="micro-loop">plan → act → execute / test<br>→ fresh evidence ↺</div><p>Interpret the result. Update locally when possible.</p></div>
  <div class="bridge-stage workflow-stage"><div class="num">03</div><div class="label">External acceptance layer</div><h2>Check the delivered workflow</h2><ul><li>Preserve the full contract</li><li>Check required behavior</li><li>Accept the real artifact independently</li></ul></div>
</div>

<div class="bridge-caption"><strong>The bridge:</strong> fixed action vocabulary + fresh execution feedback + external workflow acceptance.<span>Assumes scoped actions, informative feedback, and independently testable success.</span></div>

<!--
Speaker notes (~45s):
AskMe is an experimental coding-agent harness. It keeps an explicit plan, asks the model
for one structured action, executes it, and returns fresh test or runtime evidence. AskMe
keeps the current task and recent completed work in view; it can continue, repair, or
replan. External acceptance retains the full contract. This approach assumes work decomposes into
scoped actions, feedback is informative, and success is independently testable.
Acceptance checks the required behavior and artifact.
-->

---

<div class="eyebrow">Observed result · Qwen3.6-35B-A3B build cell</div>

# A command passed. The workflow still failed.

<div class="two-col">
  <div class="contract">
    <div class="label">Required contract</div>
    <div class="pipeline">write <span class="required">msg.h + main.c</span><br>build <span class="required">./main</span><br>run <span class="required">./main</span><br>observe <span class="required">REPLAN_OK</span></div>
  </div>
  <div class="events">
    <div class="event ok"><strong>A different target was chosen</strong><span><code>cc -o /tmp/test main.c &amp;&amp; /tmp/test</code></span></div>
    <div class="event ok"><strong>The combined command exited 0</strong><span>The retained record does not independently prove stdout or source contents.</span></div>
    <div class="event fail"><strong>Agent reported completion</strong><span>The independent acceptance test found no <code>./main</code>.</span></div>
    <div class="callout"><strong>Exit-zero command + reported completion ≠ accepted artifact.</strong></div>
    <p class="result-limit"><strong>Current limit:</strong> acceptance scored this run after completion; the failure was not returned for recovery.</p>
  </div>
</div>

<div class="source">One hosted build cell · retained under the predeclared failure rule · evals/draft-results.json</div>

<!--
Speaker notes (~45s):
This Qwen build run shows the agent problem: a successful command can still miss the
workflow contract. The retained evidence shows a combined compile-and-run command
targeting slash tmp slash test returning zero, followed by reported completion. It does
not preserve stdout or prove the source contents. Acceptance expected dot slash main and
found none. AskMe did not receive that failure for recovery. The workflow contract
remained unmet afterward.
-->

---

<div class="eyebrow">Design hypothesis</div>

# Hypothesis: update only what fresh evidence invalidates

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

<div class="callout"><strong>Trajectory goal—not yet isolated:</strong> fewer repeated failures, fewer stuck steps, and less unnecessary plan churn.</div>

<!--
Speaker notes (~45s):
This is a design hypothesis, not a result from the smoke. Reasoning should keep the
contract in view, interpret execution feedback, and decide how much of the plan changed.
A local mismatch should produce a local correction while completed work stays completed.
Broad replanning belongs to broken assumptions, not every red command. The target is
trajectory quality across these fast execution-feedback loops: fewer repeated failures,
stuck steps, and unnecessary plan churn—not longer monologues.
-->

---

<div class="eyebrow">Experiment 0 · completed hosted smoke · July 10, 2026</div>

# All four variants ran; one deliverable was rejected

<p class="subtitle">4 hosted variants × 2 deliberately simple checks × 1 unseeded trajectory per cell</p>

<div class="matrix-summary">
  <div><strong>8 / 8</strong><span>reported complete</span></div>
  <div><strong>7 / 8</strong><span>artifact accepted</span></div>
  <div class="boundary">Compatibility smoke · n=1/cell · descriptive only</div>
</div>

<div class="matrix-grid">
  <div class="matrix-cell head">Model · shape</div><div class="matrix-cell head">Artifact build · observed trajectory</div><div class="matrix-cell head">Sanity repair · observed trajectory</div>
  <div class="matrix-cell model-name">Gemma 4 26B A4B<small><span class="shape moe">◆ MoE</span> · 25.2B total / 3.8B active</small></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span><small>603.6s · 19 steps · 19.5k tok<br>1 full / 1 local replan</small></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span><small>20.0s · 7 steps · 4.5k tok<br>0 full / 0 local replans</small></div>
  <div class="matrix-cell model-name">Gemma 4 31B<small><span class="shape dense">● Dense</span> · 30.7B parameters</small></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span><small>66.5s · 4 steps · 4.4k tok<br>0 full / 0 local replans</small></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span><small>22.4s · 5 steps · 4.2k tok<br>0 full / 1 local replan</small></div>
  <div class="matrix-cell model-name">Qwen3.6-27B<small><span class="shape dense">● Dense</span> · 27B parameters</small></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span><small>47.9s · 6 steps · 5.3k tok<br>0 full / 0 local replans</small></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span><small>23.0s · 6 steps · 4.1k tok<br>0 full / 0 local replans</small></div>
  <div class="matrix-cell model-name">Qwen3.6-35B-A3B<small><span class="shape moe">◆ MoE</span> · 35B total / 3B active</small></div><div class="matrix-cell"><span class="matrix-status fail">NOT ACCEPTED</span><small>17.7s · 3 steps · 3.5k tok<br>wrong artifact path</small></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span><small>11.8s · 4 steps · 2.5k tok<br>0 full / 0 local replans</small></div>
</div>

<div class="matrix-interpretation">
  <div class="supported"><strong>Supported</strong>All four reported completion on both checks; acceptance rejected one wrong deliverable.</div>
  <div class="unsupported"><strong>Not a clean model comparison</strong>No pair isolates size: dense/MoE shape, active compute, run order, and trajectories changed. No Qwen-vs-Gemma, larger-vs-smaller, speed, reasoning, or reliability inference.</div>
</div>

<div class="source">Four hosted models × two deliberately simple checks × one unseeded run/cell · evals/README.md</div>

<!--
Speaker notes (~45s):
Four hosted variants each ran two simple checks once. Every agent reported completion;
independent checks accepted seven artifacts. The shortest build trajectory was the rejected
one, which is why completion and speed alone are insufficient. The rows preserve Gemma and
Qwen size, shape, steps, tokens, and replans as descriptive trajectory context. No pair
isolates size, architecture, active compute, family, run order, reasoning, or reliability.
These are one-shot receipts, not rankings.
-->

---

<div class="eyebrow">Two observed harness boundaries</div>

# Feedback works only when actions enter and failures return

<p class="subtitle">One limit appeared before execution; another after reported completion.</p>

<div class="boundary-grid">
  <div class="boundary-step entry"><div class="num">01 · ENTER THE LOOP</div><strong>The model must emit a valid action</strong><p><code>model → valid structured action</code><br><br><b>FeatureBench-fast · 1 task · Gemma 4 31B:</b><br>3 plans → 4 reads → 0 writes → empty patch → unresolved.</p></div>
  <div class="boundary-step run"><div class="num">02 · INSIDE THE LOOP</div><strong>Execution feedback can guide repair</strong><p><code>action → execute / test → evidence ↺</code><br><br>AskMe can continue, update locally, or replan.</p></div>
  <div class="boundary-step delivery"><div class="num">03 · AFTER COMPLETION</div><strong>Acceptance checks the deliverable</strong><p><code>artifact → independent acceptance</code><br><br>The Qwen wrong-path result was rejected, but not returned for recovery.</p></div>
</div>

<div class="boundary-observation"><strong>External boundary probe—not a score:</strong> the FeatureBench canary exposed a 512-token structured-action bottleneck before a patch existed. One task cannot separate model capability from this model–harness interface.</div>

<div class="external-status"><strong>Requalified Aug 1 · one task, one attempt/model:</strong> under the revision-3 bundle and a changed serving stack, both patches applied but remained unresolved — Gemma 11/13 target tests (same two failures as the exploratory pi run); Qwen 7/13 (pi: 10/13). Gemma rewrote 18 times without testing; Qwen wrote once, then resumed reading; neither emitted <code>done</code>.</div>

<div class="source">One-task canaries, not scores · v6: CoreWeave (Gemma bf16, Qwen fp8); v4/pi: SiliconFlow fp8 · no local-neutrality claim · records: featurebench/results/ + featurebench/pi-ablation/results/</div>

<!--
Speaker notes (~45s):
Feedback helps only after a valid action enters the loop. In July, Gemma's attempted writes
overflowed AskMe's structured-action budget, leaving an empty patch. Under the revision-three
bundle and a changed serving stack, both models produced applying but unresolved patches.
Gemma matched the exploratory pi run at eleven of thirteen target tests, then rewrote
eighteen times without testing. Qwen passed seven of thirteen, wrote once, and returned to
reading. Neither emitted done. Harness changes moved failure downstream.
-->

---

<div class="eyebrow">Conclusion + limits</div>

# Promising for bounded loops. Feature readiness is still open.

<div class="conclusion-grid">
  <div class="conclusion-card observed"><strong>Observed</strong><p>Simple smoke: 7 / 8 artifacts accepted. On one feature task, revision 3 moved both models from empty to applied but unresolved patches: Gemma 11/13 target tests; Qwen 7/13.</p></div>
  <div class="conclusion-card supported"><strong>Supported</strong><p>Harness design was consequential on this task. The bundled changes moved failures into execution: Gemma rewrote without testing; Qwen resumed observation after one write.</p></div>
  <div class="conclusion-card open"><strong>Still open</strong><p>Reliability beyond one task; clean validation and termination; transport versus serving/configuration effects; reasoning, family, size, architecture, and local performance. Revision 4 is implemented in open PR #21; v7 requalification is pending.</p></div>
</div>

<p class="tagline">Evaluate the model, harness, and task as one system.</p>

<p class="closing">Current evidence supports boundary diagnosis—not a general readiness verdict.</p>

<p class="tiny" style="text-align:center; margin-top:20px;">github.com/den-run-ai/askme · slides, blog, protocol, and raw summary data</p>

<!--
Speaker notes (~35s):
The bounded checks are promising, but feature readiness remains unproven. Under revision
three, both models moved from empty to applied patches, yet neither resolved the task or
finished cleanly. Gemma rewrote without testing; Qwen wrote once and returned to observation.
The serving stack also changed, so this shows that harness design matters, not that transport
alone caused the improvement. Revision four is implemented in open PR #21; matched v7
requalification remains pending. Judge delivered behavior.
-->

---

<div class="eyebrow">Backup · harness boundaries</div>

# A small model's workload depends on the harness

<p class="subtitle">Three technical boundaries—not a leaderboard.</p>

<div class="harness-grid">
  <div class="harness-cell head"></div><div class="harness-cell head">AskMe</div><div class="harness-cell head">pi</div><div class="harness-cell head">OpenHands</div>
  <div class="harness-cell row-head">Action surface</div><div class="harness-cell">6 fixed JSON actions; exactly one action per turn.</div><div class="harness-cell">4 default tools; extensions can add or replace tools.</div><div class="harness-cell">Typed, extensible <code>Action → Observation</code> tools.</div>
  <div class="harness-cell row-head">State + control</div><div class="harness-cell">Explicit plan, curated slim state, bounded local or full replanning.</div><div class="harness-cell">Model-led session tree with branching and lossy compaction; no built-in plan mode.</div><div class="harness-cell">Conversation state + append-only event log; optional persistence and configurable condenser.</div>
  <div class="harness-cell row-head">Completion boundary</div><div class="harness-cell"><code>done</code> → conditional fail-open validation; held-out acceptance external.</div><div class="harness-cell">Loop ends when tool calls stop; checks come from the workflow or extensions.</div><div class="harness-cell"><code>finish</code> signals completion; benchmark evaluation remains a separate harness.</div>
</div>

<div class="harness-caption"><strong>Trade-off, not ranking:</strong> AskMe spends more structure to reduce each turn's decision burden; pi keeps a minimal model-led core; OpenHands supplies a richer lifecycle runtime. All still need independent behavioral acceptance.</div>

<div class="source">Sources: AskMe architecture · <a href="https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md">pi coding-agent docs</a> · <a href="https://docs.openhands.dev/sdk/arch/tool-system">OpenHands tool system</a> and <a href="https://docs.openhands.dev/sdk/arch/conversation">conversation architecture</a></div>
