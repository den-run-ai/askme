#!/usr/bin/env python3
"""Small configuration CLI with explicit timeout-source precedence."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional


DEFAULT_TIMEOUT = 30
TIMEOUT_ERROR = "error: timeout must be a positive integer"
MISSING = object()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--name", default="service")
    parser.add_argument("--timeout")
    return parser.parse_args()


def read_config_timeout(path: Optional[Path]) -> Any:
    if path is None:
        return MISSING
    with path.open(encoding="utf-8") as config_file:
        config = json.load(config_file)
    if not isinstance(config, dict):
        return MISSING
    return config.get("timeout", MISSING)


def selected_timeout(arguments: argparse.Namespace) -> Any:
    if arguments.timeout is not None:
        return arguments.timeout

    environment_timeout = os.environ.get("ASKME_TIMEOUT", MISSING)
    if environment_timeout is not MISSING:
        return environment_timeout

    config_timeout = read_config_timeout(arguments.config)
    if config_timeout is not MISSING:
        return config_timeout
    return DEFAULT_TIMEOUT


def positive_integer(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError
    converted = int(value)
    if converted <= 0:
        raise ValueError
    if isinstance(value, float) and not value.is_integer():
        raise ValueError
    return converted


def main() -> int:
    arguments = parse_arguments()
    try:
        timeout = positive_integer(selected_timeout(arguments))
    except (TypeError, ValueError, OverflowError):
        print(TIMEOUT_ERROR, file=sys.stderr)
        return 2

    print(json.dumps({"name": arguments.name, "timeout": timeout}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
