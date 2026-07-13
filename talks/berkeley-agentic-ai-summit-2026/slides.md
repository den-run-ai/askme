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
    font-size: 15px;
    margin-top: 12px;
    padding: 9px 15px;
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

<p class="title-message">Can tight execution feedback turn bounded small-model actions into accepted workflows?</p>

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
turn bounded actions into accepted workflows.
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

<div class="bridge-caption"><strong>The bridge:</strong> small action surface + fresh execution feedback + independent workflow acceptance.<span>Assumes bounded actions, informative feedback, and independently testable success.</span></div>

<!--
Speaker notes (~45s):
AskMe narrows each turn to one small action, executes it, and returns fresh test or
runtime evidence. The harness keeps the full contract and completed work in view, so the
model can continue, repair locally, or replan when an assumption breaks. This approach
assumes the work decomposes into bounded actions, feedback is informative, and success is
independently testable. The point is a simpler interface, not a lower standard.
Independent acceptance checks the required behavior and artifact.
-->

---

<div class="eyebrow">Observed result · Qwen3.6-35B-A3B build cell</div>

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
    <div class="callout"><strong>Successful actions + reported completion ≠ accepted artifact.</strong></div>
    <p class="result-limit"><strong>Current limit:</strong> acceptance scored this run after completion; the failure was not returned for recovery.</p>
  </div>
</div>

<div class="source">One hosted build cell · retained under the predeclared failure rule · evals/draft-results.json</div>

<!--
Speaker notes (~45s):
This Qwen build run shows the agent problem: correct local execution can still miss the
workflow contract. It wrote correct files, compiled slash tmp slash test, ran it
successfully, and declared completion. Acceptance expected dot slash main. Every recorded
action succeeded, but the artifact failed. That check scored the finished run; AskMe did
not receive the failure for recovery. Successful steps and completion are insufficient
without artifact acceptance.
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

# Current result: 7 of 8 simple artifacts accepted

<p class="subtitle">4 hosted model variants × 2 simple checks × 1 unseeded run/cell</p>

<div class="matrix-summary">
  <div><strong>8 / 8</strong><span>agent complete</span></div>
  <div><strong>7 / 8</strong><span>artifact accepted</span></div>
  <div class="boundary">Compatibility smoke · n=1/cell · descriptive only</div>
</div>

<div class="matrix-grid">
  <div class="matrix-cell head">Model</div><div class="matrix-cell head">Shape</div><div class="matrix-cell head">Artifact build</div><div class="matrix-cell head">Script repair</div>
  <div class="matrix-cell model-name">Gemma 4 26B A4B</div><div class="matrix-cell"><span class="shape moe">◆ MoE</span><small>25.2B total · 3.8B active</small></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span></div>
  <div class="matrix-cell model-name">Gemma 4 31B</div><div class="matrix-cell"><span class="shape dense">● Dense</span><small>30.7B parameters</small></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span></div>
  <div class="matrix-cell model-name">Qwen3.6-27B</div><div class="matrix-cell"><span class="shape dense">● Dense</span><small>27B parameters</small></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span></div>
  <div class="matrix-cell model-name">Qwen3.6-35B-A3B</div><div class="matrix-cell"><span class="shape moe">◆ MoE</span><small>35B total · 3B active</small></div><div class="matrix-cell"><span class="matrix-status fail">NOT ACCEPTED</span><small>wrong artifact path</small></div><div class="matrix-cell"><span class="matrix-status pass">ACCEPTED</span></div>
</div>

<div class="matrix-interpretation">
  <div class="supported"><strong>Supported</strong>AskMe ran all four variants; acceptance caught one false completion.</div>
  <div class="unsupported"><strong>Not a clean model comparison</strong>No pair isolates size: dense/MoE shape, active compute, run order, and trajectories changed. No Qwen-vs-Gemma, larger-vs-smaller, speed, reasoning, or reliability inference.</div>
</div>

<div class="source">Four hosted models × two deliberately simple checks × one unseeded run/cell · evals/README.md</div>

<!--
Speaker notes (~45s):
Four hosted variants each ran two simple checks once. Both dense models and the Gemma
mixture accepted both cells; the Qwen mixture accepted one. All eight reported complete.
Descriptively, Gemma thirty-one's build trace was nine times shorter than Gemma
twenty-six's, but its repair was slower; the fastest build trace was rejected. No pair
isolates size, architecture, active compute, family, provider timing, or reliability.
These are trajectory observations, not rankings. The thirty-one model was added
post-hoc.
-->

---

<div class="eyebrow">Next evidence · two separate steps</div>

# Does selective extra reasoning improve delivery?

<p class="subtitle">An AskMe setting A/B test—not an external benchmark or model comparison.</p>

<div class="experiment-grid">
  <div class="experiment"><div class="num">01</div><strong>Build the AskMe test</strong><p>Four semantic workflows; one is qualified today.</p></div>
  <div class="experiment"><div class="num">02</div><strong>Hold the system fixed</strong><p>One model route; identical tasks, tools, feedback, and budgets.</p></div>
  <div class="experiment"><div class="num">03</div><strong>Change one switch</strong><p><strong>Off:</strong> never request extra reasoning. <strong>Gated:</strong> request it only at predefined recovery points.</p></div>
  <div class="experiment"><div class="num">04</div><strong>Judge the delivery</strong><p>Three repeats per task and setting; held-out acceptance and false completion.</p></div>
</div>

<div class="pilot-status">Native A/B not ready: 3 workflows, model route, and schedule still pending · 0 measured runs</div>

<div class="metrics"><strong>24-run scope:</strong> only an obvious difference on these four tasks—not reliability, model size, or Qwen vs Gemma.</div>

<div class="external-status"><strong>External evaluation is also not ready:</strong> no adapter or result yet. FeatureBench first; Vals only if access permits.</div>

<!--
Speaker notes (~45s):
This is an AskMe-owned A/B test, not an external benchmark. Both arms use one model,
workflows, tools, feedback, and budgets; both may reason internally. Off never requests
the API's explicit-reasoning mode. Gated requests it only at retry, recovery, replan, and
final-check points. Today one of four workflows is qualified, and zero runs exist.
FeatureBench adaptation comes later; Vals requires access. Twenty-four runs could reveal
a task-local difference, not a family or size effect.
-->

---

<div class="eyebrow">Conclusion + limits</div>

# Promising—with a tight harness.

<div class="conclusion-grid">
  <div class="conclusion-card observed"><strong>Observed</strong><p>8 / 8 reported completion. 7 / 8 simple artifacts were accepted. One completed run missed its deliverable.</p></div>
  <div class="conclusion-card supported"><strong>Supported</strong><p>Harness compatibility across four hosted variants; execution evidence plus independent acceptance is a credible direction.</p></div>
  <div class="conclusion-card open"><strong>Still open</strong><p>Feature/app readiness, reasoning impact, Qwen vs Gemma, model-size effects, reliability, and local performance.</p></div>
</div>

<p class="tagline">Make the interface easier to use.<br>Keep success tied to real behavior.</p>

<p class="closing">The smoke exercises the measurement path; readiness needs repeated held-out workflows.</p>

<p class="tiny" style="text-align:center; margin-top:20px;">github.com/den-run-ai/askme · slides, blog, protocol, and raw summary data</p>

<!--
Speaker notes (~35s):
Today we observed harness compatibility and one caught false completion. That supports
execution feedback plus independent acceptance as a credible design direction, not a
readiness verdict. We have not established realistic feature or application performance,
a reasoning-policy benefit, Qwen versus Gemma, scaling, reliability, or local performance.
Keep the action protocol simple and general, update only invalidated work, and tie success
to behavior. Repeated held-out workflows must earn the stronger claim.
-->
