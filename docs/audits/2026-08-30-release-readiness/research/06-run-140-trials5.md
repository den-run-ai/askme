# LLM Tests run 140 — `trials=5` dispatch, 2026-08-30

Dispatched during the release-readiness audit to convert the four consecutive scheduled reds
(runs 132–135) into per-cell rates. Retained here because the GitHub artifacts expire after 14 days.

| field | value |
|---|---|
| workflow | `.github/workflows/llm.yml` ("LLM Tests") |
| run | https://github.com/den-run-ai/askme/actions/runs/33295808202 (run number 140) |
| event | `workflow_dispatch` |
| ref / sha | `main` @ `fcd5bc07acdd6309ae0e1bb170b745e64ba1fbaa` |
| inputs | `suite=easy`, `trials=5`, `provider=auto`, `models=google/gemma-4-26b-a4b-it=google/gemma-4-26b-a4b-it-20260403,qwen/qwen3.6-27b=qwen/qwen3.6-27b-20260422` |
| duration | 05:57:28Z → 06:28:27Z (31 min) |
| conclusion | `failure` (expected: `tests/ci_llm_gate.py:336` requires every trial to pass) |
| artifacts | `berkeley-protocol-logs` id 9727713336 (35 files, 77 KB); `openrouter-smoke-logs` id 9727384509 |

## Berkeley-protocol job (99215144132) — gate table, verbatim

| Cell | Model | Provider | Pytest | Agent complete | Median wall (s) | Cost ($) |
|---|---|---|---|---|---|---|
| ❌ hard/test_replan_build_with_dependency | google/gemma-4-26b-a4b-it | auto → Cloudflare, Darkbloom, DeepInfra, Google, NextBit, Parasail, SiliconFlow, Venice | 0/5 | 2/5 | 127.7 | 0.03117 |
| ❌ medium/test_fix_python_syntax_error | google/gemma-4-26b-a4b-it | auto → Cloudflare, Darkbloom, DeepInfra, Google, NextBit, Novita, Parasail, Venice | 1/5 | 1/5 | 95.8 | 0.01083 |
| ✅ hard/test_replan_build_with_dependency | qwen/qwen3.6-27b | auto → Alibaba, Chutes, CoreWeave, DeepInfra, Phala, SiliconFlow, Venice | 5/5 | 5/5 | 34.8 | 0.01548 |
| ❌ medium/test_fix_python_syntax_error | qwen/qwen3.6-27b | auto → Alibaba, Chutes, CoreWeave, DeepInfra, Phala, SiliconFlow, Venice | 3/5 | 3/5 | 105.2 | 0.05556 |

Protocol-job cost: $0.113. `LLM GATE: FAIL` (3 of 4 cells).

### Qwen medium cell, per-trial (from the job log)

```
test_fix_python_syntax_error  [pytest 3/5, agent complete 3/5, contract valid 5/5]
  Wall time (s)          105.2 (20.2–136.9)
  Replans (full)         1 (0–1)      Local replans 1 (0–2)      Thinking retries 1 (0–3)
  Steps                  7 (4–9)      Steps failed  0 (0–0)
  LLM calls              18 (9–20)    Prompt tokens 18893        Completion tokens 4033
  trial 1: PYTEST_PASS   20.2s  (agent=complete,  steps=4, replans=0, retries=0)
  trial 2: PYTEST_FAIL  107.0s  (agent=exhausted, steps=6, replans=0, lr=1/1, retries=2)
  trial 3: PYTEST_PASS   85.9s  (agent=complete,  steps=9, replans=1, lr=1/1, retries=3)
  trial 4: PYTEST_FAIL  105.2s  (agent=exhausted, steps=7, replans=1, lr=1/2, retries=1)
  trial 5: PYTEST_PASS  136.9s  (agent=complete,  steps=7, replans=1, lr=1/1, retries=1)
```

Note `Steps failed 0 (0–0)` on every trial, including the two exhausted ones: no step
failed; the runs exhausted on suppression, not on errors.

## OpenRouter-smoke job (99215144065) — `TestOpenRouterEasy`, 2 failed / 1 passed

`test_multi_step_build` — the artifact was correct, the run was recorded `exhausted`:

```
[05:59:11]   [5] shell: cd .../test_multi_step_build0 && cc -o main main.c
[05:59:11]   -> OK (2.1s): (no output)
[05:59:12]   Task-local replan (1.2s): 'Run ./main in .../test_multi_step'
[05:59:12] --- Task 2/3: Run ./main in .../test_multi_step_build0 ---
[05:59:13]   [1] shell: cd .../test_multi_step_build0 && ./main
[05:59:13]   -> OK (1.1s): AGENT_OK
[05:59:15]   [2] shell: cd .../test_multi_step_build0 && ./main
[05:59:15]   [2] skip (duplicate successful shell)
[05:59:17]   [3] done: The task is completed as ./main was successfully run and produced 'AGENT_OK'.
[05:59:17]   Task complete. (13.9s)
[05:59:17] --- Task 3/3: Run ./main ---
[05:59:18]   [1] shell: cd .../test_multi_step_build0 && ./main
[05:59:18]   [1] skip (duplicate successful shell)
[05:59:19]   [2] shell: cd .../test_multi_step_build0 && ./main
[05:59:19]   [2] auto-fail (same successful shell repeated on cd .../test_m)
[05:59:35]   Task-local replan failed (15.1s), will full replan.
[05:59:35]   Task failed, will replan. (17.9s)
[05:59:35] Exhausted 2 replan attempts. (73.5s total)
[05:59:35] Errors: ['[stuck_loop] shell cd .../test_multi_step_build0 && : same successful command repeated']
```

In the pytest body, `assert_file(tmp_path / "main.c", "AGENT_OK")` (line 205) passed; the
failing assertion is `assert result["status"] == "complete"` at line 206 (`'exhausted' == 'complete'`).

`test_create_and_read_file` — `exhausted` with
`[stuck_loop] read .../hello.txt: same file read repeatedly` (twice). Its first assertion
is the status assert (line 192), so this log does not establish whether `hello.txt` was written.

## Reading the result

1. Issue #95's mechanism reproduces on hosted models: the planner emits a verify-shaped task
   ("Run ./main") whose only satisfying action is a command that already succeeded; the
   duplicate guard skips it, the stuck guard auto-fails it, the task cannot be satisfied, and
   the run exhausts with the work finished. This is the same chain as the local 11/20.
2. Claim-failure rates on the medium cell: gemma-4-26b-a4b-it 4/5 exhausted, qwen3.6-27b 2/5
   exhausted — the same regime as local E4B's 11/20 (55%).
3. The inverse failure is present too: gemma-4-26b-a4b-it on the hard cell claimed `complete`
   2/5 while passing 0/5 — false completion caught by the test, not by the agent.
4. These cells are "Berkeley-derived" and are explicitly not a replay of the talk's frozen
   four-model matrix (`llm.yml` comment, lines 118–127). They bound how the 7/8 figure may be
   read; they do not overturn it.
5. Provider routing was `auto`; each Gemma cell was served by 7–8 providers across 5 trials.
   Under CLAUDE.md's matched-provider rule this is a variance measurement, not a causal
   model comparison. Do not present it as Gemma vs Qwen.

To make any of these numbers citable: port `berkeley-protocol-logs` (the 35 per-trial files)
into `tests/bench_records/2026-08-30-run140-trials5/` with this note as its README before the
artifact expires on 2026-09-13.
