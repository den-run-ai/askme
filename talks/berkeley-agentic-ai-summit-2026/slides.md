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
    --amber: #e58a00;
    --coral: #e45649;
    --terminal: #171a21;
  }
  section {
    background: var(--paper);
    color: var(--ink);
    font-family: Inter, Aptos, "Helvetica Neue", Arial, sans-serif;
    font-size: 25px;
    letter-spacing: 0;
    padding: 42px 54px 46px;
  }
  section::after {
    color: #8792a2;
    font-size: 15px;
  }
  section.title {
    background: #eef2f7;
  }
  h1, h2, h3, p { letter-spacing: 0; }
  h1 {
    color: var(--ink);
    font-size: 48px;
    line-height: 1.05;
    margin: 0 0 20px;
  }
  h2 {
    color: var(--ink);
    font-size: 30px;
    line-height: 1.15;
    margin: 0 0 16px;
  }
  p { line-height: 1.32; }
  code { font-family: "SFMono-Regular", Consolas, monospace; }
  .eyebrow {
    color: var(--blue);
    font-size: 16px;
    font-weight: 800;
    margin-bottom: 14px;
    text-transform: uppercase;
  }
  .hero {
    display: grid;
    grid-template-columns: 1.03fr 0.97fr;
    gap: 34px;
    align-items: center;
    height: 88%;
  }
  .hero h1 { font-size: 66px; }
  .subtitle {
    color: var(--muted);
    font-size: 25px;
    margin: 0 0 22px;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 9px;
  }
  .chip {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 5px;
    color: var(--ink);
    font-size: 16px;
    font-weight: 700;
    padding: 7px 10px;
  }
  .terminal {
    background: var(--terminal);
    border-radius: 8px;
    box-shadow: 0 18px 45px rgba(17, 24, 39, 0.16);
    color: #f7f9fc;
    font-family: "SFMono-Regular", Consolas, monospace;
    font-size: 18px;
    line-height: 1.55;
    min-height: 315px;
    padding: 22px 24px;
  }
  .terminal .bar { color: #8f9bad; margin-bottom: 18px; }
  .terminal .prompt { color: #63d8c2; }
  .terminal .ok { color: #ffd166; font-weight: 800; }
  .terminal .dim { color: #aeb7c5; }
  .two-col {
    display: grid;
    grid-template-columns: 0.94fr 1.06fr;
    gap: 28px;
    align-items: stretch;
  }
  .code-pane {
    background: var(--terminal);
    border-radius: 8px;
    color: #f7f9fc;
    font-family: "SFMono-Regular", Consolas, monospace;
    font-size: 19px;
    line-height: 1.45;
    padding: 22px;
  }
  .code-pane .bad { color: #ff8a80; }
  .code-pane .good { color: #63d8c2; }
  .timeline { padding: 2px 0; }
  .event {
    display: grid;
    grid-template-columns: 112px 1fr;
    gap: 12px;
    margin: 0 0 13px;
  }
  .event .time {
    color: var(--muted);
    font-size: 17px;
    font-weight: 800;
    padding-top: 7px;
    text-align: right;
  }
  .event .bar {
    border-left: 8px solid var(--blue);
    border-radius: 4px;
    background: #fff;
    padding: 7px 12px;
  }
  .event.warn .bar { border-left-color: var(--coral); }
  .event.slow .bar { border-left-color: var(--amber); }
  .event strong { display: block; font-size: 19px; }
  .event span { color: var(--muted); font-size: 16px; }
  .callout {
    background: #fff4dc;
    border-left: 6px solid var(--amber);
    border-radius: 4px;
    font-size: 20px;
    margin-top: 18px;
    padding: 12px 15px;
  }
  .flow {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 12px;
    margin-top: 24px;
  }
  .node {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 7px;
    min-height: 145px;
    padding: 15px;
    position: relative;
  }
  .node:not(:last-child)::after {
    color: var(--blue);
    content: "→";
    font-size: 30px;
    font-weight: 800;
    position: absolute;
    right: -22px;
    top: 50px;
    z-index: 2;
  }
  .node .num {
    color: var(--blue);
    font-size: 15px;
    font-weight: 900;
  }
  .node strong { display: block; font-size: 20px; margin: 6px 0; }
  .node p { color: var(--muted); font-size: 16px; margin: 0; }
  .example-strip {
    background: var(--terminal);
    border-radius: 8px;
    color: #f7f9fc;
    font-family: "SFMono-Regular", Consolas, monospace;
    font-size: 18px;
    margin-top: 24px;
    padding: 16px 20px;
  }
  .example-strip .typed { color: #ffb74d; }
  .example-strip .pass { color: #63d8c2; }
  .task-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 18px;
    margin: 20px 0;
  }
  .task {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 7px;
    min-height: 148px;
    padding: 17px 19px;
  }
  .task .label { color: var(--teal); font-size: 16px; font-weight: 900; }
  .task strong { display: block; font-size: 23px; margin: 6px 0 8px; }
  .task code { color: var(--muted); font-size: 16px; }
  .model-lane {
    align-items: center;
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    margin-bottom: 14px;
  }
  .model {
    background: #edf4ff;
    border-bottom: 5px solid var(--blue);
    border-radius: 6px;
    font-size: 18px;
    padding: 12px;
    text-align: center;
  }
  .model:nth-child(2) { background: #e9f7f3; border-color: var(--teal); }
  .model:nth-child(3) { background: #fff3e5; border-color: var(--amber); }
  .model strong { display: block; }
  .model span { color: var(--muted); font-size: 14px; }
  table.results {
    border-collapse: separate;
    border-spacing: 8px;
    font-size: 18px;
    margin-top: 15px;
    table-layout: fixed;
    width: calc(100% - 16px) !important;
  }
  table.results th {
    color: var(--muted);
    font-size: 15px;
    padding: 8px;
    text-align: left;
  }
  table.results td {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 13px 12px;
  }
  table.results td:first-child { font-weight: 800; }
  .pending { color: var(--amber); font-weight: 900; }
  .pass { color: var(--teal); font-weight: 900; }
  .fail { color: var(--coral); font-weight: 900; }
  .tiny { color: var(--muted); font-size: 15px; }
  .proof-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    margin-top: 22px;
  }
  .proof {
    border-radius: 7px;
    min-height: 265px;
    padding: 20px 22px;
  }
  .proof.yes { background: #e9f7f3; border-top: 7px solid var(--teal); }
  .proof.no { background: #fff0ed; border-top: 7px solid var(--coral); }
  .proof h2 { font-size: 25px; }
  .proof ul { font-size: 19px; line-height: 1.45; margin: 12px 0 0; padding-left: 24px; }
  .loop {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 10px;
    margin: 34px 0 30px;
  }
  .loop .box {
    border-radius: 7px;
    color: #fff;
    font-size: 20px;
    font-weight: 800;
    min-height: 86px;
    padding: 19px 12px;
    text-align: center;
  }
  .loop .box:nth-child(1) { background: var(--blue); }
  .loop .box:nth-child(2) { background: #405670; }
  .loop .box:nth-child(3) { background: var(--amber); }
  .loop .box:nth-child(4) { background: var(--teal); }
  .loop .box:nth-child(5) { background: var(--coral); }
  .tagline {
    font-size: 37px;
    font-weight: 850;
    line-height: 1.15;
    margin: 0;
    text-align: center;
  }
  .source {
    bottom: 18px;
    color: #8792a2;
    font-size: 12px;
    left: 54px;
    position: absolute;
  }
---

<!-- _class: title -->

<div class="hero">
  <div>
    <div class="eyebrow">Agentic AI Summit 2026 · UC Berkeley</div>
    <h1>Small models.<br>Tight loops.</h1>
    <p class="subtitle">What a coding-agent harness is actually doing for you</p>
    <div class="chips">
      <span class="chip">one Python file</span>
      <span class="chip">real compiler</span>
      <span class="chip">external postconditions</span>
    </div>
    <p class="tiny"><strong>Denis Akhiyarov</strong> · Aug 1, 2026</p>
  </div>
  <div class="terminal">
    <div class="bar">nanagent / run 01</div>
    <span class="prompt">$</span> askme "build a two-file C program"<br><br>
    <span class="dim">plan</span> &nbsp; write .h → write .c → compile → run<br>
    <span class="dim">tool</span> &nbsp; cc -o main main.c<br>
    <span class="dim">check</span> &nbsp; ./main<br><br>
    <span class="ok">AGENT_OK</span>
  </div>
</div>

<!--
Speaker notes (~45s):
I wanted to know whether small models are useful inside coding agents, not whether they
can win a one-shot code benchmark. So I built the smallest harness I could: one Python
file, real shell tools, and a plan-execute-recover loop. The surprising lesson was not
"the tiny model is secretly frontier." It was that most of the useful reliability came
from the loop around it. Let me show you one very ordinary bug that made that obvious.
-->

---

<div class="eyebrow">A real failure trace</div>

# The bug that ate minutes

<div class="two-col">
  <div class="code-pane">
    <span class="bad">// missing: #include &lt;stdio.h&gt;</span><br>
    int main() {<br>
    &nbsp;&nbsp;printf("FIXED\n");<br>
    &nbsp;&nbsp;return 0;<br>
    }<br><br>
    <span class="bad">error: implicit declaration of printf</span>
  </div>
  <div class="timeline">
    <div class="event"><div class="time">step 1</div><div class="bar"><strong>Compile</strong><span>Useful failure: the compiler names the bug.</span></div></div>
    <div class="event warn"><div class="time">next</div><div class="bar"><strong>Bad edit</strong><span>Malformed JSON or the wrong exact-match string.</span></div></div>
    <div class="event slow"><div class="time">140–253s</div><div class="bar"><strong>Think, retry, re-read</strong><span>The model spends tokens rediscovering known state.</span></div></div>
    <div class="callout"><strong>Wrong move:</strong> buy more thinking. <strong>Better move:</strong> turn the compiler error into control flow.</div>
  </div>
</div>

<div class="source">Three traces of the slowest local microtask, Apr 26, 2026 · PERFORMANCE.md. The 140–253s range is not a suite-wide result.</div>

<!--
Speaker notes (~45s):
This is the most boring C bug possible: call printf without including stdio. The compiler
already tells us exactly what happened. But the early agent treated every failed step as
"the model needs to think harder." On our slowest local microtask, a bad edit could trigger
a thinking retry and then a huge re-read. In three traces that recovery call took 140 to
253 seconds. This is narrow evidence from one task, but it exposed the design error: we
were paying the model to rediscover a fact the compiler had already given us.
-->

---

<div class="eyebrow">Harness design</div>

# Move reliability out of the model

<div class="flow">
  <div class="node"><div class="num">01</div><strong>Typed error</strong><p><code>compile_error</code>, not an undifferentiated failure string.</p></div>
  <div class="node"><div class="num">02</div><strong>Exact context</strong><p>Read the file before retrying an edit.</p></div>
  <div class="node"><div class="num">03</div><strong>Cheap repair</strong><p>Use a narrow deterministic fix when the diagnostic is unambiguous.</p></div>
  <div class="node"><div class="num">04</div><strong>Small replan</strong><p>Replace one failed task, not the whole plan.</p></div>
  <div class="node"><div class="num">05</div><strong>Real check</strong><p>Run the artifact outside the LLM's own judgment.</p></div>
</div>

<div class="example-strip">
compiler stderr → <span class="typed">[compile_error]</span> → repair / retry → <span class="pass">./main == "AGENT_OK"</span>
</div>

<div class="source">Implementation: askme.py · architecture and historical timings: ARCHITECTURE.md, PERFORMANCE.md</div>

<!--
Speaker notes (~45s):
The fix is a sequence of small, explicit contracts. Classify the failure. Give the model
the exact file content instead of asking it to guess. Apply deterministic repairs only
when the compiler diagnostic is unambiguous. If a task still fails, replace that task
instead of regenerating the whole plan. And finally, run the program. None of this makes
the model smarter. It makes the system less dependent on intelligence for mechanical
work. That is the part of agent engineering I expect to survive every model upgrade.
-->

---

<div class="eyebrow">Draft OpenRouter smoke · July 10, 2026</div>

# Six runs, two concrete jobs

<div class="task-grid">
  <div class="task"><div class="label">MULTI-FILE BUILD</div><strong>Header + source → compile → run</strong><code>msg.h + main.c · ./main == REPLAN_OK</code></div>
  <div class="task"><div class="label">REPAIR</div><strong>Fix a Python syntax error</strong><code>python3 greet.py exits 0 · stdout contains hello</code></div>
</div>

<div class="model-lane">
  <div class="model"><strong>Gemma 4 26B A4B</strong><span>MoE · 3.8B active</span></div>
  <div class="model"><strong>Qwen3.6-27B</strong><span>dense · 27B</span></div>
  <div class="model"><strong>Qwen3.6-35B-A3B</strong><span>MoE · 3B active</span></div>
</div>

<p class="tiny"><strong>Held fixed:</strong> NanAgent commit, prompts, SiliconFlow routing with FP8 endpoints, reasoning off initially with one retry policy, strict external checks. <strong>n=1 per cell.</strong></p>

<div class="source">Exact protocol and commands: evals/README.md · Model architecture: official Google and Qwen model cards</div>

<!--
Speaker notes (~40s):
For this talk I added a deliberately tiny hosted smoke test: three models, two jobs, one
run per cell. Job one creates a header and source file, compiles them, and runs the binary.
Job two repairs a broken Python file, which we execute independently. Provider, prompts,
harness commit, and reasoning policy are pinned. Six runs are not a leaderboard or a
reliability estimate. They are a receipt: can each model drive this exact tool loop and
leave behind an artifact that really runs?
-->

---

<div class="eyebrow">Draft result · replace after authenticated run</div>

# What happened?

<table class="results">
  <thead><tr><th>Model</th><th>Build + run</th><th>Repair + run</th><th>LLM calls</th><th>Billed credits</th></tr></thead>
  <tbody>
    <tr><td>Gemma 4 26B A4B</td><td><span class="pending">PENDING</span></td><td><span class="pending">PENDING</span></td><td>—</td><td>—</td></tr>
    <tr><td>Qwen3.6-27B</td><td><span class="pending">PENDING</span></td><td><span class="pending">PENDING</span></td><td>—</td><td>—</td></tr>
    <tr><td>Qwen3.6-35B-A3B</td><td><span class="pending">PENDING</span></td><td><span class="pending">PENDING</span></td><td>—</td><td>—</td></tr>
  </tbody>
</table>

<p class="tiny">Pass means both <strong>agent complete</strong> and the deterministic postcondition passed. Latency is shown per task because it is provider- and network-dependent.</p>

<div class="callout"><strong>Read this as a smoke test.</strong> One run can find an integration failure; it cannot estimate a success rate.</div>

<div class="source">Result data will be committed as evals/draft-results.json. Current .env key returned 401 before billing.</div>

<!--
Speaker notes (~35s):
This is where the six results go. I am keeping the definition of pass strict: the agent
must report completion and the program must run under a deterministic postcondition.
Calls and billed credits matter because they capture loop efficiency better than a single
response score. One run can expose a parser, routing, or recovery incompatibility. It
cannot tell us a model is 100 percent reliable. Treat these cells as a draft integration
smoke, nothing more.
-->

---

<div class="eyebrow">Scope check</div>

# Useful evidence, small claim

<div class="proof-grid">
  <div class="proof yes">
    <h2>What we actually verify</h2>
    <ul>
      <li>Structured actions survive the model/provider path</li>
      <li>The loop can build or repair these exact programs</li>
      <li>The resulting artifact executes correctly</li>
      <li>Calls, tokens, route, and cost are auditable</li>
    </ul>
  </div>
  <div class="proof no">
    <h2>What we do not claim</h2>
    <ul>
      <li>Full-app or long-horizon reliability</li>
      <li>Local-laptop speed from hosted runs</li>
      <li>UX, architecture, or maintainability quality</li>
      <li>Contest-code scores predict agent performance</li>
    </ul>
  </div>
</div>

<div class="source">No LiveCodeBench claim: short contest problems answer a different question from multi-step, multi-file agent work.</div>

<!--
Speaker notes (~40s):
The claim has to stay the size of the evidence. We verify that structured actions survive
the provider path, that the loop completes these two jobs, and that the resulting programs
run. We do not verify full-app reliability, long-horizon refactors, code quality, or local
speed. And I am intentionally not using LiveCodeBench here. Short contest problems are a
useful raw-coding measure, but they do not exercise stateful tool use across multiple files
and steps. For agent work, executable postconditions are closer to the question.
-->

---

<div class="eyebrow">Takeaway</div>

# The loop is the product

<div class="loop">
  <div class="box">Model</div>
  <div class="box">Typed action</div>
  <div class="box">Guardrail</div>
  <div class="box">Real tool</div>
  <div class="box">Postcondition</div>
</div>

<p class="tagline">Small models do not need easier standards.<br>They need tighter feedback loops.</p>

<p class="tiny" style="text-align:center; margin-top:22px;">github.com/den-run-ai/askme · slides, blog, protocol, and raw summary data</p>

<!--
Speaker notes (~35s):
My conclusion is not that small models are ready for every coding job. It is that the
agent loop is the durable product: typed actions, narrow guardrails, real tools, and
external checks. Small models make weak loops painfully visible; frontier models often
hide the same debt. Keep the standards high and shorten the feedback path. Then choose
the smallest model that closes your real loop at the latency, privacy, and cost you need.
The repo includes the deck, blog, exact protocol, and result data. Thank you.
-->
