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
  .smoke-layout {
    align-items: start;
    display: grid;
    gap: 36px;
    grid-template-columns: 0.7fr 1.55fr;
    margin-top: 24px;
  }
  .smoke-scores {
    border-top: 6px solid var(--blue);
    padding-top: 16px;
  }
  .smoke-score { margin-bottom: 22px; }
  .smoke-score strong { color: var(--blue); display: block; font-size: 50px; line-height: 1; }
  .smoke-score span { color: var(--muted); display: block; font-size: 19px; margin-top: 5px; }
  .smoke-insight {
    border-left: 5px solid var(--coral);
    color: var(--ink);
    font-size: 17px;
    line-height: 1.3;
    margin-top: 28px;
    padding-left: 12px;
  }
  .smoke-table {
    display: grid;
    grid-template-columns: 1.45fr 1fr 1fr;
  }
  .smoke-table > div {
    border-bottom: 1px solid var(--line);
    font-size: 17px;
    min-height: 54px;
    padding: 14px 10px;
  }
  .smoke-table .head {
    color: var(--muted);
    font-size: 14px;
    font-weight: 850;
    min-height: 36px;
    padding-top: 0;
    text-transform: uppercase;
  }
  .smoke-model { font-weight: 800; }
  .smoke-status { font-weight: 900; }
  .smoke-status.pass { color: var(--teal); }
  .smoke-status.fail { color: var(--coral); }
  .smoke-limit {
    background: #fff2df;
    border-left: 6px solid var(--amber);
    border-radius: 4px;
    color: #7a5008;
    font-size: 17px;
    line-height: 1.3;
    margin-top: 24px;
    padding: 11px 15px;
  }
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
  .feature-progress {
    display: grid;
    gap: 38px;
    grid-template-columns: repeat(3, 1fr);
    margin-top: 30px;
  }
  .feature-stage {
    border-top: 7px solid var(--blue);
    min-height: 215px;
    padding: 17px 10px 0 0;
    position: relative;
  }
  .feature-stage.before { border-color: var(--coral); }
  .feature-stage.after { border-color: var(--teal); }
  .feature-stage.next { border-color: var(--amber); }
  .feature-stage:not(:last-child)::after {
    color: var(--blue);
    content: "→";
    font-size: 34px;
    font-weight: 900;
    position: absolute;
    right: -31px;
    top: 86px;
    z-index: 2;
  }
  .feature-label {
    color: var(--blue);
    font-size: 14px;
    font-weight: 900;
    text-transform: uppercase;
  }
  .feature-stage.before .feature-label { color: var(--coral); }
  .feature-stage.after .feature-label { color: var(--teal); }
  .feature-stage.next .feature-label { color: var(--amber); }
  .feature-stage h2 { font-size: 28px; margin: 10px 0 10px; }
  .feature-stage p { color: var(--muted); font-size: 18px; line-height: 1.35; margin: 0; }
  .feature-stage .big-result { color: var(--coral); font-size: 48px; font-weight: 900; line-height: 1; }
  .feature-results { display: flex; gap: 28px; margin-top: 12px; }
  .feature-results strong { color: var(--teal); display: block; font-size: 34px; line-height: 1; }
  .feature-results span { color: var(--muted); font-size: 15px; }
  .feature-takeaway {
    background: #e9f7f3;
    border-left: 6px solid var(--teal);
    border-radius: 4px;
    color: #285d53;
    font-size: 20px;
    margin-top: 22px;
    padding: 12px 15px;
  }
  .feature-caveat {
    color: var(--muted);
    font-size: 14px;
    margin-top: 9px;
  }
  .harness-grid {
    display: grid;
    gap: 5px;
    grid-template-columns: 0.72fr repeat(3, 1fr);
    margin-top: 12px;
  }
  .harness-cell {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 5px;
    font-size: 14px;
    line-height: 1.28;
    min-height: 76px;
    padding: 9px 10px;
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
    font-size: 15px;
    line-height: 1.3;
    margin-top: 10px;
    padding: 8px 12px;
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

<div class="title-kicker">Agentic AI Summit 2026 · UC Berkeley · Aug 1, 2026 · Corrected Sep 8</div>

<div class="title-question">

# Are Small LLMs Ready for Coding Agents?

<p class="title-message">Can tight execution feedback turn structured small-model actions into accepted workflows?</p>

</div>

<div class="title-speaker">
  <div><span class="name"><strong>Denis Akhiyarov</strong></span><span class="role">Sr Staff Research Scientist at ServiceNow</span></div>
  <div class="title-links"><a href="https://github.com/den-run-ai">@den-run-ai</a><span>·</span><a href="https://github.com/den-run-ai/askme">github.com/den-run-ai/askme</a></div>
</div>

<!--
Speaker notes:
This started on a plane. No wifi, no coding agent, and the experiments I
wanted would take weeks alone. So, the dream: small open models on my own
MacBook, through llama.cpp, doing real coding work anywhere. This talk is a
progress report on that dream. Small means a deployment class, not a parameter
count. One caveat up front: AskMe began with Gemma 4 E4B, a dense PLE model
on a sixteen-gigabyte Mac. The smoke and feature evaluations use hosted Gemma
and Qwen across different revisions and serving configurations. A separate
local repair pilot closes the talk. None isolates model size.

[Sources]
- [Local deployment and scope](../../README.md)
- [Hosted experiment provenance](evals/draft-results.json)
-->

---

<div class="eyebrow">Why this matters now</div>

# AskMe gives a small model one structured move at a time

<p class="subtitle"><strong>AskMe</strong> is an experimental coding-agent harness: explicit plan → one native tool call → execution evidence ↺</p>

<div class="bridge-grid">
  <div class="bridge-stage model-stage"><div class="num">01</div><div class="label">Controllable small LLMs</div><h2>Choose the model boundary</h2><ul><li>Execution speed</li><li>Hardware and deployment</li><li>Post-training access</li></ul></div>
  <div class="bridge-stage askme-stage"><div class="num">02</div><div class="label">AskMe loop</div><h2>One small action at a time</h2><div class="micro-loop">plan → act → execute / test<br>→ fresh evidence ↺</div><p>Interpret the result. Update locally when possible.</p></div>
  <div class="bridge-stage workflow-stage"><div class="num">03</div><div class="label">External acceptance layer</div><h2>Check the delivered workflow</h2><ul><li>Preserve the full contract</li><li>Check required behavior</li><li>Accept the real artifact independently</li></ul></div>
</div>

<div class="bridge-caption"><strong>The bridge:</strong> eight native tools + fresh execution feedback + external workflow acceptance.<span>Assumes scoped actions, informative feedback, and independently testable success.</span></div>

<!--
Speaker notes:
AskMe is an experimental coding-agent harness, small enough to read in one
sitting. Three bets for small models. One: pass as little context as possible
— the planner gets a curated summary, the executor a smaller sliding view.
Two: small granular actions, an edit instead of a rewrite. Today's interface
accepts one native tool call per turn: six executable actions plus `done` and
`fail`. Three: reasoning tokens only where they pay, mostly in recovery.
Tool feedback guides AskMe; held-out acceptance scores the artifact after the
run and is not returned to the agent for recovery.

[Sources]
- [Runtime protocol and control boundaries](../../docs/ARCHITECTURE.md)
- [Current native tool schemas](../../actions.py)
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
    <div class="event ok"><strong>The combined command exited 0</strong><span>From the agent's side, the task looked done.</span></div>
    <div class="event fail"><strong>Agent reported completion</strong><span>The independent acceptance test found no <code>./main</code>.</span></div>
    <div class="callout"><strong>Exit-zero command + reported completion ≠ accepted artifact.</strong></div>
  </div>
</div>

<!--
Speaker notes:
First lesson: success signals can mislead. This Qwen run issued a combined
compile-and-run command at slash tmp slash test, saw exit zero, and reported
complete. The record does not preserve stdout or prove source contents. The
contract asked for dot slash main; the post-run check found none. AskMe did
not receive that failure for recovery. Judge the delivered artifact, not
the agent's self-report.

[Sources]
- [Frozen July 10 per-cell outcomes and retained command](evals/draft-results.json)
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

<div class="callout"><strong>Design goal:</strong> fewer repeated failures, fewer stuck steps, and less unnecessary plan churn.</div>

<!--
Speaker notes:
The third bet is still a hypothesis. The control flow is boring on purpose.
Evidence matches the plan: continue. One step misses: repair that step, rerun
its check. Replan broadly only when an assumption broke. The target is
trajectory quality — fewer repeated failures, fewer stuck steps, less plan
churn — not longer monologues. Not measured yet; it's the bet I most want to
be right about.

[Sources]
- [Harness hypotheses and evaluation limits](../../docs/EXPERIMENTS.md)
-->

---

<div class="eyebrow">Hosted smoke test · July 10, 2026</div>

# Acceptance caught the one bad deliverable

<p class="subtitle">Four hosted models · two simple tasks · one run each</p>

<div class="smoke-layout">
  <div class="smoke-scores">
    <div class="smoke-score"><strong>8 / 8</strong><span>reported complete</span></div>
    <div class="smoke-score"><strong>7 / 8</strong><span>artifacts accepted</span></div>
    <div class="smoke-insight"><strong>The fastest build was the rejected one.</strong><br>Completion and speed were not enough.</div>
  </div>
  <div class="smoke-table">
    <div class="head">Hosted variant</div><div class="head">Artifact build</div><div class="head">Sanity repair</div>
    <div class="smoke-model">Gemma 4 26B A4B</div><div class="smoke-status pass">Accepted</div><div class="smoke-status pass">Accepted</div>
    <div class="smoke-model">Gemma 4 31B</div><div class="smoke-status pass">Accepted</div><div class="smoke-status pass">Accepted</div>
    <div class="smoke-model">Qwen3.6-27B</div><div class="smoke-status pass">Accepted</div><div class="smoke-status pass">Accepted</div>
    <div class="smoke-model">Qwen3.6-35B-A3B</div><div class="smoke-status fail">Wrong path</div><div class="smoke-status pass">Accepted</div>
  </div>
</div>

<div class="smoke-limit"><strong>Compatibility smoke, not a ranking:</strong> single runs on simple tasks — no model comparisons.</div>

<!--
Speaker notes:
Does the loop hold? Four hosted variants — two Gemma 4s, two Qwen3.6s — two
simple tasks each, one run each. All eight reported complete; the independent
check accepted seven. The one rejection was the fastest build trajectory —
that wrong-path build; an accepted repair was faster overall. This is a frozen
July record, one run per cell, with no rankings or current reliability claim.
Acceptance caught what the completion signal missed.

[Sources]
- [Frozen July 10 hosted matrix](evals/draft-results.json)
- [Protocol and acceptance boundary](evals/README.md)
-->

---

<div class="eyebrow">Historical FeatureBench canary · one feature task</div>

# Applied patches, unresolved feature task

<p class="subtitle">After bundled harness changes and a changed serving stack.</p>

<div class="feature-progress">
  <div class="feature-stage before">
    <div class="feature-label">Before · July</div>
    <div class="big-result">0 writes</div>
    <h2>Empty patch</h2>
    <p>Revision 2 left both cells empty. Their failure modes differed.</p>
  </div>
  <div class="feature-stage after">
    <div class="feature-label">After · Aug 1</div>
    <h2>Patches applied</h2>
    <div class="feature-results">
      <div><strong>11 / 13</strong><span>Gemma target tests</span></div>
      <div><strong>7 / 13</strong><span>Qwen target tests</span></div>
    </div>
    <p>Held-out target tests passed in part; both patches remained unresolved.</p>
  </div>
  <div class="feature-stage next">
    <div class="feature-label">Gaps in both attempts</div>
    <h2>No target-test run</h2>
    <p>Gemma rewrote one file 18 times. Qwen returned to reading. Both agents exhausted their planning attempts.</p>
  </div>
</div>

<div class="feature-takeaway"><strong>Observed:</strong> partial target-test acceptance, without agent completion.</div>

<div class="feature-caveat">One task, one attempt per model · historical results · no causal attribution or benchmark score.</div>

<!--
Speaker notes:
Next: FeatureBench, one frozen feature task. Revision 2 left both cells with
empty patches, for different reasons. After bundled revision-3 changes and a
changed serving stack, both left applying but unresolved patches. Held-out
scoring found eleven of thirteen target tests passing for Gemma, seven for
Qwen. Neither ran the target tests; both agents exhausted their planning attempts.
Gemma rewrote one file
eighteen times, Qwen returned to reading. One task, one attempt each, no causal
attribution. These historical results do not validate today's native-tool
transport.

[Sources]
- [FeatureBench protocols and historical outcomes](../../tests/featurebench/README.md)
- [Gemma August 1 result](../../tests/featurebench/results/2026-08-01-gemma-4-31b-canary-v6.json)
- [Qwen August 1 result](../../tests/featurebench/results/2026-08-01-qwen36-27b-canary-v6.json)
-->

---

<div class="eyebrow">Conclusion + limits</div>

# Local repairs are possible. Reliable autonomy is unproven.

<div class="conclusion-grid">
  <div class="conclusion-card observed"><strong>Observed locally</strong><p>Gemma 4 E4B (dense PLE), Aug 4: four repairs of one seeded health-check bug passed independent acceptance. All four agents exhausted.</p></div>
  <div class="conclusion-card supported"><strong>Supported</strong><p>Narrow, independently accepted small repairs exist. A correct artifact is not a clean agent finish.</p></div>
  <div class="conclusion-card open"><strong>Still open</strong><p>Dependable autonomous coding, net time savings, and causal harness benefit. The historical pilots do not isolate a harness effect.</p></div>
</div>

<p class="tagline">Evaluate the model, harness, and task as one system.</p>

<p class="closing">One seeded task—not a general readiness verdict or time-savings claim.</p>

<p class="tiny" style="text-align:center; margin-top:20px;">github.com/den-run-ai/askme · slides, blog, protocol, and raw summary data</p>

<!--
Speaker notes:
So: can small local models do useful coding work? A narrow yes. In an August
pilot, local Gemma E4B repaired one seeded health-check bug in four
shipped-profile runs. Independent acceptance passed; all four agents exhausted.
The two clean finishes used a larger, diagnostic budget.
That is evidence of small accepted repairs, not dependable autonomy or measured
net time savings. The feature canary remains unresolved. Judge delivered
behavior, and evaluate the model, harness, task, and evaluator together.
A causal harness benefit and the reliable plane version remain goals.

[Sources]
- [Frozen August 4 local repair receipt and evidence limits](evals/local-repair-evidence.json)
- [Evidence and unresolved claims](README.md#evidence-boundary)
- [Dated measurements and limits](../../docs/PERFORMANCE.md)
-->

---

<div class="eyebrow">Backup · harness boundaries</div>

# A small model's workload depends on the harness

<p class="subtitle">Three technical boundaries · documentation checked Sep 8, 2026.</p>

<div class="harness-grid">
  <div class="harness-cell head"></div><div class="harness-cell head">AskMe</div><div class="harness-cell head">pi</div><div class="harness-cell head">OpenHands</div>
  <div class="harness-cell row-head">Action surface</div><div class="harness-cell">8 native tools: 6 executable actions + <code>done</code>/<code>fail</code>; one call per turn.</div><div class="harness-cell">4 default tools; extensions can add or replace tools.</div><div class="harness-cell">Typed, extensible <code>Action → Observation</code> tools.</div>
  <div class="harness-cell row-head">State + control</div><div class="harness-cell">Explicit plan, curated slim state, bounded local or full replanning.</div><div class="harness-cell">Model-led session tree with branching and lossy compaction; no built-in plan mode.</div><div class="harness-cell">Conversation state + append-only event log; optional persistence and configurable condenser.</div>
  <div class="harness-cell row-head">Completion boundary</div><div class="harness-cell"><code>done</code> + conditional validation. Unavailable check: <code>complete_unverified</code>. Held-out acceptance external.</div><div class="harness-cell">Loop ends when tool calls stop; checks come from the workflow or extensions.</div><div class="harness-cell"><code>finish</code> signals completion; benchmark evaluation remains a separate harness.</div>
</div>

<div class="harness-caption"><strong>Trade-off, not ranking:</strong> AskMe spends more structure to reduce each turn's decision burden; pi keeps a minimal model-led core; OpenHands supplies a richer lifecycle runtime. All still need independent behavioral acceptance.</div>

<div class="source">Sources: AskMe architecture · <a href="https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md">pi coding-agent docs</a> · <a href="https://docs.openhands.dev/sdk/arch/tool-system">OpenHands tool system</a> and <a href="https://docs.openhands.dev/sdk/arch/conversation">conversation architecture</a></div>
