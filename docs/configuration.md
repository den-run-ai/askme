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
| `LLM_API_URL` | `http://localhost:8080/v1/chat/completions` | Custom API URL (local only) |
| `LLM_MODEL` | `gemma-4-e4b` | Model name (local only) |
| `CACHE_WORKAROUND` | `0` | Obsolete manual slot save/restore bypass (local only). Measured 40% slower than baseline; superseded by `--swa-full --cache-reuse 256`. Kept in code only for retesting after server rebuilds — leave at `0`. See [gemma4-setup.md](gemma4-setup.md). |
| `ALLOW_SYSTEM_INSTALLS` | `0` | Prompt-visible install policy; does not enforce host isolation |
| `ALLOW_NETWORK` | `1` | Reserved prompt-visible policy; currently does not enforce network isolation |
| `AGENT_FINAL_VALIDATE` | `auto` | Final validation: `auto`, `always`, or `0` (disabled) |
| `AGENT_REASONING_POLICY` | `gated` | Explicit-reasoning requests: `gated` preserves the recovery policy; `off` suppresses them at every call site |
| `AGENT_GOAL_CONTEXT_CHARS` | `300` | Goal characters retained for executor and task-local replan context; independent of result/history truncation |
| `AGENT_RUN_LOG` | (unset) | Path to append JSONL events (`run_start`, `reasoning_decision`, `plan`, `tokens`, `step`, `task_complete`, `task_failed`, `validation`, `run_end`). Disabled when unset. |

`off` controls explicit reasoning requests sent by the harness; it is not a claim
that the model performs no internal reasoning. Each request attempt logs the
requested policy, trigger, and effective reasoning level when `AGENT_RUN_LOG` is
enabled. Per-call-site `gated`/`off` semantics are specified in
[ARCHITECTURE.md](ARCHITECTURE.md#explicit-reasoning-policy).

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

See `python3 askme.py --help` for the full flag list, and
[tests/workflows/PROTOCOL.md](../tests/workflows/PROTOCOL.md) for the frozen
evaluation contract that consumes this interface.
