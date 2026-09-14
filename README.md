# AskMe

[![CI](https://github.com/den-run-ai/askme/actions/workflows/ci.yml/badge.svg?branch=main&event=push)](https://github.com/den-run-ai/askme/actions/workflows/ci.yml?query=branch%3Amain)
[![Coverage](https://codecov.io/gh/den-run-ai/askme/branch/main/graph/badge.svg?precision=2)](https://app.codecov.io/gh/den-run-ai/askme)
[![LLM Tests](https://github.com/den-run-ai/askme/actions/workflows/llm.yml/badge.svg?branch=main)](https://github.com/den-run-ai/askme/actions/workflows/llm.yml?query=branch%3Amain)

A small Python coding agent for local models and OpenRouter. Give it a task;
it makes a plan, edits files, runs commands, and checks its work.

AskMe started with a simple goal: useful coding help from a small open model
on a MacBook, even without Wi-Fi. It keeps the harness small, the context lean,
and the actions explicit. The only runtime dependency is `requests`.

**Experimental:** small models can still get stuck or claim success too early.
Review changes and run your project's tests. See the
[dated results and limitations](docs/PERFORMANCE.md).

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
- [Workflow evaluation protocol](tests/workflows/PROTOCOL.md) and
  [FeatureBench runbook](tests/featurebench/README.md)
- [Contributor and coding-agent guidance](CLAUDE.md)

For the motivation, early evidence, and open questions, see
[*Are Small LLMs Ready for Coding Agents?*](talks/berkeley-agentic-ai-summit-2026/README.md),
my five-minute talk at the 2026 Agentic AI Summit at UC Berkeley
([slides](talks/berkeley-agentic-ai-summit-2026/slides.pdf),
[recording](https://www.youtube.com/watch?v=N1XoiJGyNpM)).
