#!/usr/bin/env python3
"""Held-out behavioral evaluator for the configuration-precedence task."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path.cwd()
PROGRAM = ROOT / "config_cli.py"
EXPECTED_ERROR = "error: timeout must be a positive integer\n"


def run_cli(arguments=(), *, environment_timeout=None):
    environment = os.environ.copy()
    environment.pop("ASKME_TIMEOUT", None)
    if environment_timeout is not None:
        environment["ASKME_TIMEOUT"] = environment_timeout
    return subprocess.run(
        [sys.executable, str(PROGRAM), *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )


def decoded(completed):
    return json.loads(completed.stdout)


def main():
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "config.json"
        config.write_text('{"timeout": 40}', encoding="utf-8")

        env_over_config = run_cli(("--config", str(config)), environment_timeout="50")
        check(env_over_config.returncode == 0, "environment-over-config exited nonzero")
        try:
            check(decoded(env_over_config)["timeout"] == 50,
                  "environment must override JSON config")
        except (json.JSONDecodeError, KeyError):
            failures.append("environment-over-config output was not valid configuration JSON")

        cli_over_all = run_cli(
            ("--config", str(config), "--timeout", "60", "--name", "worker"),
            environment_timeout="50",
        )
        check(cli_over_all.returncode == 0, "CLI-over-all exited nonzero")
        try:
            check(decoded(cli_over_all) == {"name": "worker", "timeout": 60},
                  "CLI must override environment and config without changing name/output")
        except json.JSONDecodeError:
            failures.append("CLI-over-all output was not valid JSON")

        invalid_config = Path(directory) / "invalid-config.json"
        invalid_config.write_text('{"timeout": "bad"}', encoding="utf-8")
        ignored_invalid_lower_sources = run_cli(
            ("--config", str(invalid_config), "--timeout", "70"),
            environment_timeout="also-bad",
        )
        check(ignored_invalid_lower_sources.returncode == 0,
              "valid CLI value must ignore invalid lower-precedence sources")
        try:
            check(decoded(ignored_invalid_lower_sources)["timeout"] == 70,
                  "valid CLI value did not win over invalid lower-precedence sources")
        except (json.JSONDecodeError, KeyError):
            failures.append("lower-precedence case output was not valid JSON")

    for label, completed in (
        ("invalid CLI", run_cli(("--timeout", "0"))),
        ("invalid environment", run_cli(environment_timeout="not-an-integer")),
    ):
        check(completed.returncode == 2, f"{label} must exit 2")
        check(completed.stdout == "", f"{label} must not write stdout")
        check(completed.stderr == EXPECTED_ERROR,
              f"{label} must write the exact error to stderr")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("held-out acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
