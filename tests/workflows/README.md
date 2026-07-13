# Native workflow evaluation — Phase 1

The frozen policy and outcome definitions are in [PROTOCOL.md](PROTOCOL.md).

Phase 1 contains one deterministic configuration-precedence workflow. The
manifest points to a syntactically valid seed repository, protected public
tests, a regression command that passes on the seed, a visible feedback command
that fails on the seed, and a behavioral acceptance evaluator. Both the
same-author reference and a separately authored alternative must pass all three
checks before an outcome-bearing run; the alternative's source and provenance
live under `tests/workflow_alternatives/`.

Each manifest freezes the agent budgets (`max_replans`, `max_tasks`,
`max_steps`, `goal_context_chars`, and the outer `agent_timeout_seconds`) and
final-validator policy. A prompt over its declared goal-context cap is rejected
before the callback runs; the harness does not silently truncate task
requirements. The selected `off` or `gated` reasoning policy and all limits are
copied into every structured result.

The runner copies only the seed into a fresh working directory. The held-out
evaluator remains outside that copied workspace and is invoked after the agent
returns, even when the agent reports an incomplete status. This separation
prevents accidental editing and keeps artifact acceptance distinct from agent
completion. It is **not an adversarial security sandbox**: a process with host
filesystem access could still search for the evaluator. Container or namespace
isolation is future work for untrusted agents.

Qualification is completely offline and requires no model call:

```sh
python3 tests/workflow_eval.py \
  tests/workflows/config_precedence/manifest.json \
  --agent noop --reasoning-policy gated
```

The no-op is expected to preserve public regressions while failing visible
feedback and held-out acceptance. The result records infrastructure validity,
protected-test integrity, and their conjunction (`run_valid`) independently
from agent completion and artifact checks. To execute AskMe against a configured
backend, opt in explicitly with `--agent askme`; this path starts `askme.py` as
a fresh CLI subprocess and passes the manifest's workspace, policy, and budgets
explicitly.

`outcome` is one of:

- `clean_success`: a complete agent and all artifact checks passed;
- `false_completion`: a complete agent but at least one artifact check failed;
- `accepted_incomplete`: all artifact checks passed without a complete status;
- `incomplete_failure`: neither completion nor artifact acceptance; or
- `invalid_run`: the harness failed or protected-test integrity was lost.
