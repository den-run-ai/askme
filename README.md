# AskMe

[![CI](https://github.com/den-run-ai/askme/actions/workflows/ci.yml/badge.svg?branch=main&event=push)](https://github.com/den-run-ai/askme/actions/workflows/ci.yml?query=branch%3Amain)
[![Coverage](https://codecov.io/gh/den-run-ai/askme/branch/main/graph/badge.svg?precision=2)](https://app.codecov.io/gh/den-run-ai/askme)
[![LLM Tests](https://github.com/den-run-ai/askme/actions/workflows/llm.yml/badge.svg?branch=main)](https://github.com/den-run-ai/askme/actions/workflows/llm.yml?query=branch%3Amain)

A small Python coding agent for local models and OpenRouter. Give it a task;
it makes a plan, edits files, runs commands, and checks its work.

AskMe began with a simple dream: a small open model on my MacBook, through
`llama.cpp`, helping with real coding work anywhere—even on a plane without
Wi-Fi. This repository is a progress report toward that fully local coding
agent. It keeps the context lean and actions explicit, with `requests` as its
only runtime dependency.

I shared the motivation and early results in
[*Are Small LLMs Ready for Coding Agents?*](talks/berkeley-agentic-ai-summit-2026/README.md),
my five-minute talk at the 2026 Agentic AI Summit at UC Berkeley
([slides](talks/berkeley-agentic-ai-summit-2026/slides.pdf),
[recording](https://www.youtube.com/watch?v=N1XoiJGyNpM)).

## How Ready Is It?

**Useful for experiments and small supervised repairs. Dependable autonomous
coding is still unproven.** The Berkeley results make the gap visible:

| Hosted model | Simple build + repair | Complex feature task |
|---|---|---|
| Gemma 4 26B A4B (MoE) | 2/2 accepted | Not evaluated |
| Gemma 4 31B (dense) | 2/2 accepted | Unresolved; 11/13 target tests passed |
| Qwen3.6-27B (dense) | 2/2 accepted | Unresolved; 7/13 target tests passed |
| Qwen3.6-35B-A3B (MoE) | 1/2 accepted; build used the wrong output path | Not evaluated |

These are historical hosted observations: [two simple checks on July 10](talks/berkeley-agentic-ai-summit-2026/evals/README.md)
and [one FeatureBench task on August 1](talks/berkeley-agentic-ai-summit-2026/README.md#evidence-boundary),
one attempt per model/task. All eight simple runs reported completion, but only
seven artifacts passed independent checks. Both feature attempts exhausted.
They used earlier harness revisions and do not measure current reliability or
local performance. Review changes and run your project's tests.

A separately dated [September 14 AskMe/pi comparison](talks/berkeley-agentic-ai-summit-2026/README.md#september-14-askmepi-follow-up)
reached 8/13 target tests with the frozen AskMe snapshot and 11/13 with pi for
both dense models. All four patches remained unresolved; the receipt records
the tool-schema defect and budget limit that qualify those observations.

## Quick Start

Requires Python 3.10+ with pip. Clone and run from source:

```bash
python3 -m pip install uv==0.12.1
git clone https://github.com/den-run-ai/askme.git
cd askme
uv run --locked --no-dev askme.py --help
```

`uv` creates the environment from the committed lockfile. `--help` lists the
options without calling a model. AskMe is not an installable pip CLI package.

Choose a backend:

- **Local:** start a compatible `llama-server` on port 8080, then run the
  command below. See [local setup](docs/configuration.md#local-models) and the
  [Gemma 4 E4B reference](docs/gemma4-setup.md) for model and server settings.
- **OpenRouter:** follow the short [API key setup](docs/configuration.md#openrouter),
  then use the same command. Hosted calls spend OpenRouter credits.

```bash
uv run --locked --no-dev askme.py --working-dir /path/to/project "Fix the failing tests"
```

Replace `/path/to/project` with a project you can safely edit. Without
`--working-dir`, AskMe creates a temporary directory for the task.

## Usage

Describe the change and how to check it:

```bash
uv run --locked --no-dev askme.py --working-dir /path/to/project \
  "Add CSV export to the report command and run the relevant tests"
```

For a longer prompt or a machine-readable result:

```bash
uv run --locked --no-dev askme.py --prompt-file task.md \
  --working-dir /path/to/project --result-json result.json
```

Model selection, provider routing, budgets, reasoning, logging, and the Python
API are covered in [configuration](docs/configuration.md).

## How It Works

AskMe inspects the environment, plans tasks, then asks the model for one action
at a time: `shell`, `write`, `edit`, `read`, `search`, or `tree`. It returns the
result to the model and replans when needed, with up to three planning attempts
by default.

A conditional LLM validator can review completion. If that requested review
produces no verdict, the result is `complete_unverified`. Exit code `0` means
the agent completed its tasks; it does not prove your project's tests pass.
The [architecture guide](docs/ARCHITECTURE.md) explains the loop and its limits.

## Security

AskMe is experimental automation, **not a sandbox**. Model-generated shell
commands run with your user account's permissions. Install and network settings
are instructions to the model, not enforced restrictions. Use a disposable
container or VM for untrusted prompts or repositories, and review
[the security guide](docs/SECURITY.md).

## Tests

```bash
uv sync --locked
uv run --locked pytest tests/ -q
```

This runs the deterministic suite; live model tests skip by default.
[Testing and CI](docs/testing.md) covers linting, coverage, explicit live-test
opt-in, OpenRouter credentials, CI failure diagnostics, and macOS lanes.
The badges above show ordinary CI, coverage, and the separate live LLM workflow
on `main`.

## More

- [Configuration and Python API](docs/configuration.md)
- [Local Gemma 4 setup](docs/gemma4-setup.md)
- [Architecture and source map](docs/ARCHITECTURE.md)
- [Benchmark results](docs/PERFORMANCE.md) and [experiments](docs/EXPERIMENTS.md)
- [Public release readiness](docs/public-release-readiness.md)
- [Workflow evaluation protocol](tests/workflows/PROTOCOL.md) and
  [FeatureBench runbook](tests/featurebench/README.md)
- [Contributor and coding-agent guidance](CLAUDE.md)
