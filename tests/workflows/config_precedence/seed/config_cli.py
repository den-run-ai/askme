#!/usr/bin/env python3
"""Print the effective service configuration as JSON."""

import argparse
import json
import os
import sys


DEFAULT_TIMEOUT = 30
TIMEOUT_ERROR = "error: timeout must be a positive integer"


def load_config(path):
    if path is None:
        return {}
    with open(path, encoding="utf-8") as source:
        return json.load(source)


def resolve_timeout(cli_timeout, environment, config):
    """Resolve timeout from the available sources.

    This implementation is intentionally seeded with a semantic precedence
    bug for the workflow task.
    """
    raw = DEFAULT_TIMEOUT
    if cli_timeout is not None:
        raw = cli_timeout
    if "ASKME_TIMEOUT" in environment:
        raw = environment["ASKME_TIMEOUT"]
    if "timeout" in config:
        raw = config["timeout"]
    return int(raw)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    parser.add_argument("--timeout")
    parser.add_argument("--name", default="service")
    args = parser.parse_args(argv)

    try:
        timeout = resolve_timeout(args.timeout, os.environ, load_config(args.config))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        print(TIMEOUT_ERROR)
        return 0

    print(json.dumps({"name": args.name, "timeout": timeout}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
