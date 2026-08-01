# Security Policy

## Execution boundary

AskMe is an experimental coding-agent harness, not an isolation boundary. The
agent can execute model-generated shell commands through the host shell with the
permissions of the user who launched it. Its temporary working directory is for
output organization; it does not confine shell commands, absolute paths, or path
traversal.

`ALLOW_SYSTEM_INSTALLS` and `ALLOW_NETWORK` are exposed to the model as policy
signals. They are not an operating-system sandbox. In particular,
`ALLOW_NETWORK` is currently reserved and does not block network access.

## Safe use

- Treat prompts, repositories, issue text, generated commands, and tool output as
  potentially untrusted.
- Run AskMe in a disposable container or VM with restricted mounts, least-privilege
  credentials, and explicit network policy.
- Do not mount SSH keys, cloud credentials, browser profiles, password stores, or
  production data into the execution environment.
- Review model-generated changes before merging or deploying them.
- Remember that remote LLM backends receive selected prompt and workspace context.

The FeatureBench adapter adds task pins, traversal checks, and audit controls for
that evaluation path. Those controls are defense in depth for a frozen benchmark;
they do not turn the core agent into a general-purpose sandbox.

## Reporting a vulnerability

Do not include exploit details or secrets in a public issue. Use GitHub's private
vulnerability reporting for this repository when available. If it is unavailable,
contact the repository owner through their GitHub profile and request a private
channel without disclosing sensitive details publicly.
