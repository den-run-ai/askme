# Configuration

Full environment-variable and CLI reference for `askme.py`. The everyday knobs
are summarized in the [README](../README.md#configuration); for what these
settings do inside the loop, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Environment variables

| Env var | Default | Purpose |
|---|---|---|
| `LLM_BACKEND` | `local` | `local` or `openrouter` |
| `OPENROUTER_API_KEY` | (from `.env`) | API key for OpenRouter |
| `OPENROUTER_MODEL` | `google/gemma-4-26b-a4b-it` | OpenRouter model |
| `OPENROUTER_PROVIDER` | `Parasail` | Preferred OpenRouter provider; empty means automatic routing |
| `OPENROUTER_ALLOW_FALLBACKS` | `1` | Whether OpenRouter may leave the preferred provider |
| `OPENROUTER_REQUIRE_PARAMETERS` | `0` | Require the provider to advertise support for all request parameters |
| `OPENROUTER_REASONING_EFFORT` | (unset) | Baseline reasoning effort (`low`/`medium`/`high`) for always-on reasoners like `openai/gpt-oss-20b`. Leave unset for hybrid models like Gemma 4 |
| `LLM_API_URL` | `http://localhost:8080/v1/chat/completions` | Custom API URL (local only) |
| `LLM_MODEL` | `gemma-4-e4b` | Model name (local only) |
| `ALLOW_SYSTEM_INSTALLS` | `0` | Prompt-visible install policy; does not enforce host isolation |
| `ALLOW_NETWORK` | `1` | Reserved prompt-visible policy; currently does not enforce network isolation |
| `AGENT_FINAL_VALIDATE` | `auto` | Final validation: `auto`, `always`, or `0` (disabled). An unavailable or malformed verdict never counts as a pass — the run completes with status `complete_unverified` |
| `AGENT_COMPILE_REPAIR` | `1` | Deterministic C-header compile repair (tracked in issue #41). `0` disables it — the preregistered ablation's off arm ([ablation-compile-repair.md](ablation-compile-repair.md)). The repair rule proposes a normal write action that is dispatched through the action executor |
| `AGENT_STEP_POLICY` | `heuristic` | Step/completion-pressure arm (issue #31): `heuristic` is the guard/counter baseline; `lifecycle` is the explicit inspect → modify → verify → finish alternative |
| `AGENT_REASONING_POLICY` | `gated` | Explicit-reasoning requests: `gated` preserves the recovery policy; `off` suppresses them at every call site |
| `AGENT_GOAL_CONTEXT_CHARS` | `300` | Goal characters retained for executor and task-local replan context; independent of result/history truncation |
| `AGENT_RUN_LOG` | (unset) | Path to append JSONL events (`run_start`, `reasoning_decision`, `plan`, `tokens`, `step`, `task_complete`, `task_failed`, `validation`, `run_end`). Disabled when unset. |

`off` controls explicit reasoning requests sent by the harness; it is not a claim
that the model performs no internal reasoning. Each request attempt logs the
requested policy, trigger, and effective reasoning level when `AGENT_RUN_LOG` is
enabled. Per-call-site `gated`/`off` semantics are specified in
[ARCHITECTURE.md](ARCHITECTURE.md#explicit-reasoning-policy).

`OPENROUTER_REASONING_EFFORT` exists because harmony-format models
(gpt-oss-20b/120b) expose `low`/`medium`/`high` effort but no off switch, so the
default reasoning-disabled request leaves their effort at the provider default
on every call. When set, every OpenRouter request carries at least the baseline
effort; `gated` escalation raises it but never lowers it, and
`AGENT_REASONING_POLICY=off` pins requests to exactly the baseline. The outer
`max_tokens` is floored at 1024/1536/2048 for low/medium/high because reasoning
tokens share the completion budget on Parasail-class providers. Example:

```bash
LLM_BACKEND=openrouter OPENROUTER_MODEL=openai/gpt-oss-20b \
OPENROUTER_REASONING_EFFORT=low python3 askme.py "your request here"
```

## Automation and evaluation CLI

`askme.py` accepts flags for a fixed workspace, a prompt file, a structured JSON
result, and frozen policy/budget overrides — the interface used by the
evaluation runners:

```bash
mkdir -p /tmp/task-workspace
python3 askme.py --prompt-file task.md --working-dir /tmp/task-workspace \
  --result-json /tmp/run.json --reasoning-policy gated \
  --max-replans 3 --max-tasks 4 --max-steps 10 --goal-context-chars 1200
```

The `--result-json` file is one JSON object whose contract is: a non-empty
string `status` — `complete`, `complete_unverified` (all tasks finished but
the wanted final validation produced no verdict; never reported as a
verified pass), or `exhausted` on failure; the process exit code is `0`
exactly when the status is `complete` or `complete_unverified` — the
structured `state` dict, and the `log` history list. Issue #40 added two
credential-free metadata keys: `config` (the resolved immutable run
configuration — backend, endpoint `api` and `timeout_s`, model, provider
routing, reasoning effort/policy, execution policy, validation mode, the
#41 compile-repair arm, the #31 `step_policy` arm, guard thresholds, token
budgets (including final validation), per-call `timeouts_s` and
`retry_budgets`, limits, an `llm_provenance` marker naming where the LLM
identity came from (`module_snapshot`, `pinned_config`,
`injected_client_settings`, or `injected_opaque` for a duck-typed client
without settings), and a `config_hash` over that whole canonical payload;
never the API key) and
`workspace` (`path` plus a `created` flag that is true only when AskMe made
the temporary directory, so callers can clean it up intentionally). Issue
#68 added `outcome`, the typed terminal record: `status`, the final
`validation` disposition (`passed`, `deterministic`, `unavailable`,
`failed`, or `skipped`), `replans`, `wall_s`, `completed_tasks`, and the
selected/executed/skipped step counters — the same record the `run_end`
JSONL event is projected from.

The same structured result is available in-process from
`askme.run_result(prompt, working_dir=None, config=None, dependencies=None)`,
with `askme.RunConfig` pinning per-run settings and `askme.RunDependencies`
injecting the LLM client, action executor, clock, and log/event sinks;
`run(...) -> bool` remains the compatibility wrapper. See
`python3 askme.py --help` for the full flag list, and
[tests/workflows/PROTOCOL.md](../tests/workflows/PROTOCOL.md) for the frozen
evaluation contract that consumes this interface.

Run composition snapshots the module compatibility settings exactly once.
Later changes to model, endpoint, provider routing, request deadlines, retry
limits, or token budgets cannot alter requests in that run. Direct
`ask_llm(...)` callers keep the historical per-call module snapshot behavior.
The `budgets.reasoning_token_floors` map records the low/medium/high floors
that request construction applies when reasoning shares the HTTP completion
allowance, so the effective `max_tokens` remains derivable from the requested
call-site budget and selected effort.
