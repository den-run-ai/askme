#!/usr/bin/env python3
"""Run one AskMe canary through FeatureBench's official inference runner.

FeatureBench is an optional evaluation dependency. Imports stay lazy so AskMe's
normal unit suite does not require it.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Mapping

AGENT_NAME = "askme"
PROMPT_PATH = "/installed-agent/task-prompt.txt"
ASKME_PATH = "/installed-agent/askme.py"
LAUNCHER_PATH = "/installed-agent/askme-launcher.py"
CREDENTIAL_PATH = "/installed-agent/openrouter-api-key"
LOG_DIR = "/agent-logs"
RESULT_PATH = f"{LOG_DIR}/askme-result.json"
RUN_LOG_PATH = f"{LOG_DIR}/askme-run.jsonl"
POLICY_LOG_PATH = f"{LOG_DIR}/askme-policy.jsonl"
STDOUT_LOG_PATH = f"{LOG_DIR}/askme-stdout.log"
ADAPTER_MANIFEST_PATH = f"{LOG_DIR}/askme-adapter.json"
ENDPOINT_CATALOG_PREFLIGHT = "openrouter-endpoint-catalog-preflight.json"

PRESERVED_ARTIFACTS = {
    RESULT_PATH: "askme-result.json",
    RUN_LOG_PATH: "askme-run.jsonl",
    POLICY_LOG_PATH: "askme-policy.jsonl",
    STDOUT_LOG_PATH: "askme-stdout.log",
    ADAPTER_MANIFEST_PATH: "askme-adapter.json",
    PROMPT_PATH: "askme-prompt.txt",
}

INNER_TIMEOUT_MARGIN_SECONDS = 60
INNER_TIMEOUT_KILL_GRACE_SECONDS = 15


def launcher_source() -> str:
    """Return a credential-scrubbing, action-guarded launcher for pinned AskMe."""
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import importlib.util
        import hashlib
        import json
        import os
        import re
        import signal
        import sys
        import time
        from pathlib import Path

        ASKME_PATH = {ASKME_PATH!r}
        PROMPT_PATH = {PROMPT_PATH!r}
        CREDENTIAL_PATH = {CREDENTIAL_PATH!r}
        RESULT_PATH = {RESULT_PATH!r}
        POLICY_LOG_PATH = {POLICY_LOG_PATH!r}
        WORKSPACE = Path("/testbed").resolve()

        _NETWORK_PATTERNS = (
            ("url", re.compile(r"(?:https?|ftp|ssh)://|git@|/dev/(?:tcp|udp)/", re.I)),
            ("network_client", re.compile(
                r"(?:^|[;&|()\\s])(?:curl|wget|aria2c?|ftp|ssh|scp|sftp|nc|ncat|netcat|telnet|ping|dig|nslookup|socat|nmap)(?:$|\\s)",
                re.I,
            )),
            ("remote_git", re.compile(r"\\bgit\\s+(?:clone|fetch|pull|push|ls-remote|submodule)\\b", re.I)),
            ("package_install", re.compile(
                r"(?:\\b(?:pip3?|uv|conda|mamba|poetry|apt|apt-get|yum|dnf|apk|brew|pacman|zypper|npm|pnpm|yarn|bun|gem|cargo|go)\\b[^\\n;&|]*\\b(?:install|download|add|get|update|upgrade)\\b|"
                r"\\bpython3?\\s+-m\\s+pip\\s+(?:install|download)\\b)",
                re.I,
            )),
            ("inline_network_api", re.compile(
                r"\\b(?:requests|urllib3?|http\\.client|aiohttp|socket)\\b",
                re.I,
            )),
            ("environment_disclosure", re.compile(
                r"(?:^|[;&|()]\\s*)(?:env|printenv|set|export\\s+-p)(?:$|\\s)|"
                r"/proc(?:/[^\\s]*)?/environ|OPENROUTER_API_KEY|os\\.environ|(?:getenv|environ)\\s*\\(",
                re.I,
            )),
            ("adapter_path", re.compile(r"/(?:installed-agent|agent-logs)(?:/|\\b)", re.I)),
            ("host_pseudo_fs", re.compile(r"/(?:proc|sys|dev)(?:/|\\b)", re.I)),
            ("parent_traversal", re.compile(
                r"(?<![A-Za-z0-9_.-])\\.\\.(?![A-Za-z0-9_.-])"
            )),
        )
        _action_sequence = 0

        def _policy_event(event):
            Path(POLICY_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
            with open(POLICY_LOG_PATH, "a", encoding="utf-8") as stream:
                stream.write(json.dumps({{"ts": time.time(), **event}}, sort_keys=True) + "\\n")

        def _sha256_path(path):
            digest = hashlib.sha256()
            with open(path, "rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()

        def _terminal_result(reason):
            result_path = Path(RESULT_PATH)
            if result_path.exists():
                return
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps({{
                "status": "adapter_interrupted",
                "state": {{"errors": [reason]}},
                "log": [],
                "adapter_terminal_reason": reason,
            }}, indent=2) + "\\n", encoding="utf-8")

        def _signal_handler(signum, _frame):
            reason = f"launcher received signal {{signum}}"
            _policy_event({{"event": "launcher_terminal", "reason": reason}})
            _terminal_result(reason)
            raise SystemExit(124)

        def _load_askme():
            credential_path = Path(CREDENTIAL_PATH)
            credential = credential_path.read_text(encoding="utf-8").strip()
            credential_path.unlink()
            if not credential:
                raise RuntimeError("empty transient OpenRouter credential")
            os.environ["OPENROUTER_API_KEY"] = credential
            try:
                spec = importlib.util.spec_from_file_location("askme_pinned", ASKME_PATH)
                if spec is None or spec.loader is None:
                    raise RuntimeError("cannot load pinned AskMe source")
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
            finally:
                os.environ.pop("OPENROUTER_API_KEY", None)
            return module, credential

        def _workspace_path(raw, working_dir):
            path = Path(str(raw))
            if not path.is_absolute():
                path = Path(working_dir) / path
            resolved = path.resolve(strict=False)
            if resolved != WORKSPACE and WORKSPACE not in resolved.parents:
                return None
            return resolved

        def _guard_reason(action, working_dir):
            kind = str(action.get("action", ""))
            arg = str(action.get("arg", ""))
            if kind in {{"read", "write", "edit"}}:
                if _workspace_path(arg, working_dir) is None:
                    return "path_outside_workspace"
            if kind in {{"write", "edit"}}:
                mutation = action.get("content", "") if kind == "write" else action.get("replace", "")
                if not isinstance(mutation, str):
                    mutation = json.dumps(mutation, sort_keys=True)
                for reason, pattern in _NETWORK_PATTERNS:
                    if reason in {{"url", "network_client", "remote_git", "inline_network_api"}} and pattern.search(mutation):
                        return "network_shaped_mutation"
            if kind == "shell":
                for reason, pattern in _NETWORK_PATTERNS:
                    if pattern.search(arg):
                        return reason
            return None

        def _install_guard(module, credential):
            original_execute = module.execute

            def guarded_execute(action, working_dir="."):
                global _action_sequence
                _action_sequence += 1
                kind = str(action.get("action", ""))
                arg = str(action.get("arg", ""))
                redacted_arg = arg.replace(credential, "<redacted>")
                reason = _guard_reason(action, working_dir)
                _policy_event({{
                    "event": "action_decision",
                    "sequence": _action_sequence,
                    "action": kind,
                    "arg": redacted_arg,
                    "arg_sha256": hashlib.sha256(redacted_arg.encode("utf-8")).hexdigest(),
                    "decision": "deny" if reason else "allow",
                    "reason": reason,
                }})
                if reason:
                    result = {{
                        "ok": False,
                        "output": f"Blocked by adapter policy: {{reason}}",
                        "error_type": "policy_violation",
                    }}
                else:
                    try:
                        result = original_execute(action, working_dir)
                    except BaseException as error:
                        _policy_event({{
                            "event": "action_result",
                            "sequence": _action_sequence,
                            "action": kind,
                            "decision": "allow",
                            "ok": False,
                            "error_type": f"exception:{{type(error).__name__}}",
                        }})
                        raise
                _policy_event({{
                    "event": "action_result",
                    "sequence": _action_sequence,
                    "action": kind,
                    "decision": "deny" if reason else "allow",
                    "ok": bool(result.get("ok")),
                    "error_type": result.get("error_type"),
                }})
                return result

            module.execute = guarded_execute

        def main():
            Path(POLICY_LOG_PATH).write_text("", encoding="utf-8")
            signal.signal(signal.SIGTERM, _signal_handler)
            signal.signal(signal.SIGINT, _signal_handler)
            module = None
            try:
                module, credential = _load_askme()
                _install_guard(module, credential)
                _policy_event({{
                    "event": "launcher_start",
                    "credential_in_child_environment": False,
                    "guard": "best_effort_command_and_workspace_paths",
                    "container_egress_isolated": False,
                    "askme_sha256": _sha256_path(ASKME_PATH),
                    "prompt_sha256": _sha256_path(PROMPT_PATH),
                }})
                askme_exit_code = module._main()
                structured = json.loads(Path(RESULT_PATH).read_text(encoding="utf-8"))
                complete_statuses = {{"complete", "complete_deterministic_after_exhausted"}}
                exit_code = 0 if structured.get("status") in complete_statuses else askme_exit_code
                _policy_event({{
                    "event": "launcher_end",
                    "askme_exit_code": askme_exit_code,
                    "exit_code": exit_code,
                    "structured_status": structured.get("status"),
                }})
                return exit_code
            except SystemExit:
                raise
            except BaseException as error:
                reason = f"launcher error: {{type(error).__name__}}"
                _policy_event({{"event": "launcher_terminal", "reason": reason}})
                _terminal_result(reason)
                raise
            finally:
                os.environ.pop("OPENROUTER_API_KEY", None)
                if module is not None:
                    module.OPENROUTER_API_KEY = ""

        if __name__ == "__main__":
            raise SystemExit(main())
        """
    )


@dataclass(frozen=True)
class CanarySettings:
    featurebench_root: Path
    featurebench_revision: str
    askme_path: Path
    dataset_path: Path
    dataset_revision: str
    output_dir: Path
    task_id: str
    model: str
    askme_revision: str
    protocol_path: Path
    expected_served_models: tuple[str, ...]
    split: str = "fast"
    provider: str = "siliconflow"
    timeout: int = 3600
    cache_dir: Path | None = None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def strict_openrouter_env(model: str, provider: str) -> dict[str, str]:
    """Build the public, no-fallback route used by the canary container."""
    model = model.strip()
    provider = provider.strip()
    if not model:
        raise ValueError("OpenRouter model is required")
    if not provider:
        raise ValueError("OpenRouter provider is required")
    if provider.lower() == "auto":
        raise ValueError("OpenRouter provider must be pinned, not auto")
    return {
        "LLM_BACKEND": "openrouter",
        "OPENROUTER_MODEL": model,
        "OPENROUTER_PROVIDER": provider,
        "OPENROUTER_ALLOW_FALLBACKS": "0",
        "OPENROUTER_REQUIRE_PARAMETERS": "1",
        "ALLOW_NETWORK": "0",
        "ALLOW_SYSTEM_INSTALLS": "0",
        "CACHE_WORKAROUND": "0",
        "AGENT_REASONING_POLICY": "gated",
        "AGENT_RUN_LOG": RUN_LOG_PATH,
        "PYTHONUNBUFFERED": "1",
    }


def validate_strict_env(env: Mapping[str, str]) -> None:
    required = {
        "LLM_BACKEND": "openrouter",
        "OPENROUTER_ALLOW_FALLBACKS": "0",
        "OPENROUTER_REQUIRE_PARAMETERS": "1",
        "ALLOW_NETWORK": "0",
        "ALLOW_SYSTEM_INSTALLS": "0",
        "AGENT_RUN_LOG": RUN_LOG_PATH,
        "AGENT_REASONING_POLICY": "gated",
    }
    mismatches = [
        f"{key}={env.get(key)!r}, expected {value!r}"
        for key, value in required.items()
        if env.get(key) != value
    ]
    for key in ("OPENROUTER_MODEL", "OPENROUTER_PROVIDER"):
        if not str(env.get(key, "")).strip():
            mismatches.append(f"{key} is required")
    if str(env.get("OPENROUTER_API_KEY", "")).strip():
        mismatches.append("OPENROUTER_API_KEY must not enter the container environment")
    if mismatches:
        raise ValueError("invalid AskMe canary environment: " + "; ".join(mismatches))


def retained_endpoint_catalog_preflight(
    model: str,
    provider: str,
    expected_served_models: tuple[str, ...],
    api_key: str,
    output_path: Path,
    *,
    request_get: Any | None = None,
) -> Mapping[str, Any]:
    """Retain and validate a non-generation endpoint-catalog request.

    The authenticated GET is deliberately separate from the chat-completions
    API. Its credential-free record is written before validation errors are
    raised, and callers must invoke it immediately before starting inference.
    """
    requested_model = model.strip()
    requested_provider = provider.strip()
    credential = api_key.strip()
    expected = tuple(value.strip() for value in expected_served_models)
    if not requested_model or requested_model.count("/") != 1:
        raise ValueError("OpenRouter model must be an author/slug identifier")
    if not requested_provider:
        raise ValueError("OpenRouter provider is required")
    if not credential:
        raise ValueError("OPENROUTER_API_KEY is required for endpoint preflight")
    if not expected or any(not value for value in expected):
        raise ValueError("exact expected served-model IDs are required for endpoint preflight")
    if len(set(expected)) != len(expected):
        raise ValueError("expected served-model IDs must not contain duplicates")

    author, slug = requested_model.split("/", 1)
    url = (
        "https://openrouter.ai/api/v1/models/"
        f"{urllib.parse.quote(author, safe='')}/"
        f"{urllib.parse.quote(slug, safe=':')}/endpoints"
    )
    started_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    record: dict[str, Any] = {
        "schema_version": 1,
        "kind": "openrouter_endpoint_catalog_preflight",
        "method": "GET",
        "url": url,
        "requested_at_utc": started_at,
        "requested_model": requested_model,
        "expected_provider": requested_provider,
        "expected_served_models": list(expected),
        "authenticated": True,
        "outcome_bearing_model_call": False,
        "valid": False,
    }

    def retain() -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(record, indent=2, sort_keys=True) + "\n"
        if credential in rendered:
            raise RuntimeError("endpoint preflight record unexpectedly contains credential")
        output_path.write_text(rendered, encoding="utf-8")

    try:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {credential}",
            },
            method="GET",
        )
        get = request_get or urllib.request.urlopen
        with get(request, timeout=20) as response:
            status = getattr(response, "status", None)
            body = response.read(10 * 1024 * 1024 + 1)
        if status != 200:
            raise RuntimeError(f"endpoint catalog returned HTTP {status!r}")
        if len(body) > 10 * 1024 * 1024:
            raise RuntimeError("endpoint catalog response exceeded 10 MiB")
        payload = json.loads(body)
    except Exception as error:
        record.update(
            {
                "completed_at_utc": dt.datetime.now(dt.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "error_type": type(error).__name__,
            }
        )
        retain()
        raise RuntimeError(
            f"OpenRouter endpoint-catalog preflight failed ({type(error).__name__})"
        ) from error

    data = payload.get("data") if isinstance(payload, dict) else None
    catalog_model = data.get("id") if isinstance(data, dict) else None
    endpoints = data.get("endpoints") if isinstance(data, dict) else None
    errors: list[str] = []
    if catalog_model != requested_model:
        errors.append("catalog model does not match requested model")
    if not isinstance(endpoints, list):
        errors.append("catalog endpoints is not a list")
        endpoints = []

    matches: list[dict[str, Any]] = []
    expected_set = set(expected)
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        provider_name = endpoint.get("provider_name")
        endpoint_name = endpoint.get("name")
        if (
            not isinstance(provider_name, str)
            or provider_name.casefold() != requested_provider.casefold()
            or not isinstance(endpoint_name, str)
            or " | " not in endpoint_name
        ):
            continue
        served_model = endpoint_name.split(" | ", 1)[1]
        if served_model not in expected_set:
            continue
        matches.append(
            {
                "endpoint_name": endpoint_name,
                "model_id": endpoint.get("model_id"),
                "provider_name": provider_name,
                "quantization": endpoint.get("quantization"),
                "served_model": served_model,
                "status": endpoint.get("status"),
                "tag": endpoint.get("tag"),
            }
        )

    for served_model in expected:
        count = sum(match["served_model"] == served_model for match in matches)
        if count != 1:
            errors.append(
                f"expected exactly one {requested_provider} endpoint "
                f"for {served_model}; found {count}"
            )
    if any(match.get("model_id") != requested_model for match in matches):
        errors.append("matching endpoint model_id differs from requested model")

    record.update(
        {
            "catalog_model": catalog_model,
            "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "endpoint_count": len(endpoints),
            "matches": matches,
            "response_sha256": hashlib.sha256(body).hexdigest(),
            "validation_errors": errors,
            "valid": not errors,
        }
    )
    retain()
    if errors:
        raise ValueError("endpoint-catalog preflight rejected route: " + "; ".join(errors))
    return record


def _copy_bytes(cm: Any, container: Any, data: bytes, destination: str) -> None:
    with tempfile.NamedTemporaryFile() as temporary:
        temporary.write(data)
        temporary.flush()
        cm.copy_to_container(container, Path(temporary.name), destination)


def build_askme_agent_class(
    base_agent: Any,
    askme_source: Path,
    api_key: str,
    inner_timeout: int,
) -> type:
    """Create a FeatureBench BaseAgent subclass bound to one AskMe snapshot."""
    source = askme_source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"AskMe source not found: {source}")
    pinned_sha256 = sha256_file(source)
    pinned_size = source.stat().st_size
    credential = api_key.strip()
    if not credential:
        raise ValueError("OPENROUTER_API_KEY is required")
    if inner_timeout < 1:
        raise ValueError("inner timeout must be positive")
    launcher = launcher_source().encode("utf-8")

    class AskMeFeatureBenchAgent(base_agent):
        _source = source
        _source_sha256 = pinned_sha256
        _source_size = pinned_size
        _inner_timeout = inner_timeout

        def __init__(self, *args: Any, **kwargs: Any):
            super().__init__(*args, **kwargs)
            validate_strict_env(self.env_vars)
            self._prompt_chars: int | None = None

        @property
        def name(self) -> str:
            return AGENT_NAME

        @property
        def install_script(self) -> str:
            return """#!/bin/bash
set -euo pipefail
mkdir -p /agent-logs /installed-agent
test -f /opt/miniconda3/etc/profile.d/conda.sh
source /opt/miniconda3/etc/profile.d/conda.sh
conda activate testbed
command -v timeout
python3 -c 'import requests'
"""

        def get_env_setup_script(self) -> str:
            # The API key is deliberately absent from the container environment.
            return """#!/bin/bash
test "${LLM_BACKEND:-}" = "openrouter"
test "${OPENROUTER_ALLOW_FALLBACKS:-}" = "0"
test "${OPENROUTER_REQUIRE_PARAMETERS:-}" = "1"
test "${ALLOW_NETWORK:-}" = "0"
test -z "${OPENROUTER_API_KEY:-}"
"""

        def pre_run_hook(self, container: Any, log_file: Path) -> bool:
            exit_code, _ = self.cm.exec_command(
                container,
                f"mkdir -p {LOG_DIR}",
                log_file=log_file,
            )
            return exit_code == 0

        def pre_run_setup(self, container: Any, instance: Any, log_file: Path) -> bool:
            del instance
            if sha256_file(self._source) != self._source_sha256:
                self.logger.error("Pinned askme.py changed after adapter initialization")
                return False
            try:
                self.cm.copy_to_container(container, self._source, ASKME_PATH)
                _copy_bytes(self.cm, container, launcher, LAUNCHER_PATH)
                _copy_bytes(
                    self.cm,
                    container,
                    (credential + "\n").encode("utf-8"),
                    CREDENTIAL_PATH,
                )
                exit_code, _ = self.cm.exec_command(
                    container,
                    f"chmod 0555 {ASKME_PATH} {LAUNCHER_PATH} && chmod 0400 {CREDENTIAL_PATH}",
                    log_file=log_file,
                )
                return exit_code == 0
            except Exception as error:
                self.logger.error(f"Failed to copy pinned askme.py: {error}")
                return False

        def prepare_run(self, container: Any, instruction: str, log_file: Path) -> bool:
            del log_file
            if not instruction:
                self.logger.error("FeatureBench supplied an empty instruction")
                return False
            self._prompt_chars = len(instruction)
            manifest = {
                "schema_version": 1,
                "agent": AGENT_NAME,
                "askme_sha256": self._source_sha256,
                "askme_size_bytes": self._source_size,
                "prompt_chars": self._prompt_chars,
                "prompt_sha256": hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
                "goal_context_chars": self._prompt_chars,
                "model": self.env_vars["OPENROUTER_MODEL"],
                "provider": self.env_vars["OPENROUTER_PROVIDER"],
                "allow_provider_fallbacks": False,
                "require_provider_parameters": True,
                "network_policy_requested": "deny",
                "network_enforcement": "best_effort_command_and_workspace_path_guard",
                "container_egress_isolated": False,
                "credential_handling": (
                    "transient file; unlinked before AskMe runs; removed from child environment"
                ),
                "reasoning_policy": self.env_vars["AGENT_REASONING_POLICY"],
                "limits": {
                    "max_planning_attempts": 3,
                    "max_tasks_per_plan": 10,
                    "max_steps_per_task_attempt": 10,
                    "max_task_local_replans": 1,
                    "max_task_attempts": 2,
                },
                "timeouts": {
                    "inner_seconds": self._inner_timeout,
                    "kill_grace_seconds": INNER_TIMEOUT_KILL_GRACE_SECONDS,
                },
            }
            try:
                _copy_bytes(self.cm, container, instruction.encode("utf-8"), PROMPT_PATH)
                _copy_bytes(
                    self.cm,
                    container,
                    (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
                    ADAPTER_MANIFEST_PATH,
                )
                return True
            except Exception as error:
                self.logger.error(f"Failed to copy AskMe run inputs: {error}")
                return False

        def get_run_command(self, instruction: str) -> str:
            del instruction
            if self._prompt_chars is None:
                raise RuntimeError("prepare_run must be called before get_run_command")
            reasoning_policy = self.env_vars["AGENT_REASONING_POLICY"]
            if reasoning_policy not in {"gated", "off"}:
                raise ValueError(f"unsupported reasoning policy: {reasoning_policy}")
            return (
                "set -o pipefail; "
                f"timeout --signal=TERM --kill-after={INNER_TIMEOUT_KILL_GRACE_SECONDS}s "
                f"{self._inner_timeout}s python3 {LAUNCHER_PATH} "
                f"--prompt-file {PROMPT_PATH} "
                "--working-dir /testbed "
                f"--result-json {RESULT_PATH} "
                f"--reasoning-policy {reasoning_policy} "
                "--max-replans 3 "
                "--max-tasks 10 "
                "--max-steps 10 "
                f"--goal-context-chars {self._prompt_chars} "
                f"2>&1 | tee {STDOUT_LOG_PATH}"
            )

        def _preserve_artifacts(self, container: Any, log_file: Path) -> dict[str, bool]:
            destination_dir = Path(log_file).parent
            copied = {}
            for source_path, filename in PRESERVED_ARTIFACTS.items():
                try:
                    copied[source_path] = bool(
                        self.cm.copy_from_container(
                            container,
                            source_path,
                            destination_dir / filename,
                        )
                    )
                except Exception:
                    copied[source_path] = False
            return copied

        def post_run_hook(self, container: Any, log_file: Path) -> bool:
            copied = self._preserve_artifacts(container, log_file)
            required_artifacts = (
                RESULT_PATH,
                RUN_LOG_PATH,
                POLICY_LOG_PATH,
                STDOUT_LOG_PATH,
                ADAPTER_MANIFEST_PATH,
                PROMPT_PATH,
            )
            missing = [path for path in required_artifacts if not copied.get(path)]
            if missing:
                self.logger.error("AskMe run artifacts were not preserved: " + ", ".join(missing))
                return False
            try:
                result = json.loads((Path(log_file).parent / "askme-result.json").read_text())
            except (OSError, json.JSONDecodeError) as error:
                self.logger.error(f"Invalid AskMe structured result: {error}")
                return False
            if result.get("status") not in {
                "complete",
                "complete_deterministic_after_exhausted",
            }:
                self.logger.error(f"AskMe status was {result.get('status')!r}")
                return False
            return True

        def failure_hook(self, container: Any, log_file: Path) -> None:
            self._preserve_artifacts(container, log_file)

    AskMeFeatureBenchAgent.__name__ = "AskMeFeatureBenchAgent"
    AskMeFeatureBenchAgent.__qualname__ = "AskMeFeatureBenchAgent"
    return AskMeFeatureBenchAgent


@contextlib.contextmanager
def registered_askme_agent(run_infer_module: Any, agent_class: type) -> Iterator[None]:
    """Temporarily register AskMe at the call site used by InferenceRunner."""
    original_get_agent = run_infer_module.get_agent

    def get_agent(agent_name: str, **kwargs: Any) -> Any:
        if agent_name.lower() == AGENT_NAME:
            return agent_class(**kwargs)
        return original_get_agent(agent_name, **kwargs)

    run_infer_module.get_agent = get_agent
    try:
        yield
    finally:
        run_infer_module.get_agent = original_get_agent


def load_featurebench_api(featurebench_root: Path) -> SimpleNamespace:
    root = featurebench_root.resolve()
    if not (root / "featurebench" / "infer" / "run_infer.py").is_file():
        raise FileNotFoundError(f"FeatureBench checkout not found: {root}")
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    try:
        base_module = importlib.import_module("featurebench.infer.agents.base")
        models_module = importlib.import_module("featurebench.infer.models")
        run_infer_module = importlib.import_module("featurebench.infer.run_infer")
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "FeatureBench dependencies are unavailable; run this script with its uv environment"
        ) from error
    return SimpleNamespace(
        BaseAgent=base_module.BaseAgent,
        InferConfig=models_module.InferConfig,
        InferenceRunner=run_infer_module.InferenceRunner,
        run_infer_module=run_infer_module,
    )


def _load_canary_audit_api() -> Any:
    path = Path(__file__).with_name("canary_audit.py")
    spec = importlib.util.spec_from_file_location("askme_featurebench_canary_audit", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load canary audit module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _toml_string(value: str) -> str:
    return json.dumps(value)


@contextlib.contextmanager
def featurebench_config(cache_dir: Path | None) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="askme-featurebench-") as temp_dir:
        path = Path(temp_dir) / "config.toml"
        lines = ["[env_vars]", "", "[infer]"]
        if cache_dir is not None:
            lines.append(f"download_cache_dir = {_toml_string(str(cache_dir.resolve()))}")
        lines.extend(["", "[infer_config.askme]", ""])
        path.write_text("\n".join(lines), encoding="utf-8")
        yield path


def _git_revision(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _git_is_dirty(repo: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"cannot inspect git state for {repo}: {error}") from error
    return bool(result.stdout.strip())


def _validate_protocol_settings(settings: CanarySettings) -> Mapping[str, Any]:
    """Fail before inference when the executable cell differs from the protocol."""
    try:
        protocol = json.loads(settings.protocol_path.read_text(encoding="utf-8"))
        sources = protocol["sources"]
        askme_source = sources["askme"]
        featurebench_source = sources["featurebench"]
        dataset_source = sources["dataset"]
        container_source = sources["container"]
        cell = protocol["agent_cell"]
        provider = cell["provider"]
        timeouts = cell["timeouts"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError(f"invalid canary protocol: {error}") from error

    checks = {
        "FeatureBench revision": (
            featurebench_source.get("commit"),
            settings.featurebench_revision,
        ),
        "dataset revision": (dataset_source.get("revision"), settings.dataset_revision),
        "dataset split": (dataset_source.get("split"), settings.split),
        "task ID": (dataset_source.get("instance_id"), settings.task_id),
        "requested model": (cell.get("model"), settings.model),
        "provider order": (provider.get("order"), [settings.provider]),
        "provider fallback": (provider.get("allow_fallbacks"), False),
        "required parameters": (provider.get("require_parameters"), True),
        "attempts": (cell.get("attempts"), 1),
        "reasoning policy": (cell.get("reasoning_policy"), "gated"),
        "outer timeout": (timeouts.get("outer_featurebench_seconds"), settings.timeout),
        "inner timeout": (
            timeouts.get("inner_askme_seconds"),
            settings.timeout - INNER_TIMEOUT_MARGIN_SECONDS,
        ),
        "inner kill grace": (
            timeouts.get("inner_kill_grace_seconds"),
            INNER_TIMEOUT_KILL_GRACE_SECONDS,
        ),
        "expected served models": (
            cell.get("expected_served_models"),
            list(settings.expected_served_models),
        ),
    }
    mismatches = [label for label, (observed, expected) in checks.items() if observed != expected]
    if mismatches:
        raise ValueError("canary settings differ from protocol: " + ", ".join(mismatches))

    base_source_sha256 = askme_source.get("base_source_sha256")
    if not isinstance(base_source_sha256, str) or len(base_source_sha256) != 64:
        raise ValueError("protocol must pin sources.askme.base_source_sha256")
    if sha256_file(settings.askme_path) != base_source_sha256:
        raise ValueError("AskMe source hash differs from the protocol")

    code_revision = askme_source.get("adapter_code_revision")
    if not isinstance(code_revision, str) or len(code_revision) != 40:
        raise ValueError("protocol must pin sources.askme.adapter_code_revision")
    repo_root = Path(__file__).resolve().parents[2]
    code_files = askme_source.get("code_files")
    if not isinstance(code_files, dict) or not code_files:
        raise ValueError("protocol must pin sources.askme.code_files")
    for relative, expected_hash in code_files.items():
        if not isinstance(relative, str) or not isinstance(expected_hash, str):
            raise ValueError("protocol code-file pins must be string mappings")
        path = (repo_root / relative).resolve()
        if repo_root != path and repo_root not in path.parents:
            raise ValueError("protocol code-file pin escapes the AskMe repository")
        if not path.is_file() or sha256_file(path) != expected_hash:
            raise ValueError(f"protocol code-file hash mismatch: {relative}")

    try:
        # FeatureBench supplies this optional dependency in its uv environment.
        from datasets import load_dataset  # ty: ignore[unresolved-import]

        rows = [
            row
            for row in load_dataset(str(settings.dataset_path.resolve()), split=settings.split)
            if row.get("instance_id") == settings.task_id
        ]
    except Exception as error:
        raise ValueError(f"cannot load pinned dataset snapshot: {error}") from error
    if len(rows) != 1:
        raise ValueError(f"expected exactly one pinned task, found {len(rows)}")
    row = rows[0]
    prompt = row.get("problem_statement")
    if not isinstance(prompt, str):
        raise ValueError("pinned task has no problem_statement")
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if (
        dataset_source.get("problem_statement_chars") != len(prompt)
        or dataset_source.get("problem_statement_sha256") != prompt_sha256
    ):
        raise ValueError("pinned task prompt differs from the protocol")
    expected_image = str(container_source.get("image", "")).removeprefix("docker.io/")
    if row.get("image_name") != expected_image:
        raise ValueError("pinned task image differs from the protocol")
    return protocol


def _write_run_provenance(
    path: Path,
    settings: CanarySettings,
    askme_sha256: str,
    protocol: Mapping[str, Any],
) -> None:
    askme_protocol = protocol["sources"]["askme"]
    repo_root = Path(__file__).resolve().parents[2]
    observed_code_files = {
        relative: sha256_file(repo_root / relative) for relative in askme_protocol["code_files"]
    }
    data = {
        "schema_version": 1,
        "description": "AskMe-adapted FeatureBench one-task canary",
        "dataset_path": str(settings.dataset_path.resolve()),
        "dataset_revision": settings.dataset_revision,
        "split": settings.split,
        "task_id": settings.task_id,
        "model": settings.model,
        "expected_served_models": list(settings.expected_served_models),
        "provider": settings.provider,
        "allow_provider_fallbacks": False,
        "require_provider_parameters": True,
        "network_policy_requested": "deny",
        "network_enforcement": "auditable best-effort command and workspace-path guard",
        "container_egress_isolated": False,
        "credential_in_container_environment": False,
        "endpoint_catalog_preflight": {
            "required": True,
            "relative_path": ENDPOINT_CATALOG_PREFLIGHT,
            "timing": "immediately_before_inference_runner",
            "outcome_bearing_model_call": False,
        },
        "goal_context": "full FeatureBench problem_statement",
        "limits": {
            "max_planning_attempts": 3,
            "max_tasks_per_plan": 10,
            "max_steps_per_task_attempt": 10,
            "max_task_local_replans": 1,
            "max_task_attempts": 2,
        },
        "timeouts": {
            "outer_seconds": settings.timeout,
            "inner_seconds": settings.timeout - INNER_TIMEOUT_MARGIN_SECONDS,
            "kill_grace_seconds": INNER_TIMEOUT_KILL_GRACE_SECONDS,
        },
        "askme_sha256": askme_sha256,
        "askme_repository_revision": _git_revision(settings.askme_path.parent),
        "expected_askme_repository_revision": settings.askme_revision,
        "adapter_code_revision": askme_protocol["adapter_code_revision"],
        "code_files": observed_code_files,
        "askme_git_dirty": _git_is_dirty(settings.askme_path.parent),
        "featurebench_revision": _git_revision(settings.featurebench_root),
        "featurebench_git_dirty": _git_is_dirty(settings.featurebench_root),
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _audit_retained_run(
    settings: CanarySettings,
    run_dir: Path,
    api_key: str,
    runner_exit: int,
) -> Mapping[str, Any]:
    audit_api = _load_canary_audit_api()
    result = audit_api.audit_canary(
        run_dir,
        protocol_path=settings.protocol_path,
        askme_source=settings.askme_path,
        code_root=Path(__file__).resolve().parents[2],
        api_key=api_key,
        expected_served_models=settings.expected_served_models,
        expected_run_revision=settings.askme_revision,
    )
    result["runner_exit"] = runner_exit
    output_path = run_dir / "askme-canary-audit.json"
    output_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def run_canary(
    settings: CanarySettings,
    api_key: str,
    featurebench_api: Any | None = None,
) -> tuple[int, Path]:
    """Execute and audit one task, returning qualification exit and run path."""
    if settings.timeout <= INNER_TIMEOUT_MARGIN_SECONDS:
        raise ValueError(
            f"timeout must exceed the {INNER_TIMEOUT_MARGIN_SECONDS}s inner-timeout margin"
        )
    if not settings.task_id.strip():
        raise ValueError("task_id is required")
    featurebench_revision = _git_revision(settings.featurebench_root)
    if featurebench_revision != settings.featurebench_revision:
        raise ValueError(
            "FeatureBench revision mismatch: "
            f"expected {settings.featurebench_revision}, got {featurebench_revision}"
        )
    if _git_is_dirty(settings.featurebench_root):
        raise ValueError("FeatureBench checkout must be clean")
    if _git_is_dirty(settings.askme_path.parent):
        raise ValueError("AskMe checkout must be clean")
    askme_revision = _git_revision(settings.askme_path.parent)
    if askme_revision != settings.askme_revision:
        raise ValueError(
            f"AskMe revision mismatch: expected {settings.askme_revision}, got {askme_revision}"
        )
    dataset_path = settings.dataset_path.resolve()
    if not dataset_path.is_dir():
        raise FileNotFoundError(f"FeatureBench dataset snapshot not found: {dataset_path}")
    if not settings.dataset_revision.strip():
        raise ValueError("dataset_revision is required")
    if not settings.protocol_path.is_file():
        raise FileNotFoundError(f"canary protocol not found: {settings.protocol_path}")
    if not settings.expected_served_models:
        raise ValueError("at least one expected served model is required")
    protocol = _validate_protocol_settings(settings)
    env = strict_openrouter_env(settings.model, settings.provider)
    api = featurebench_api or load_featurebench_api(settings.featurebench_root)
    agent_class = build_askme_agent_class(
        api.BaseAgent,
        settings.askme_path,
        api_key,
        settings.timeout - INNER_TIMEOUT_MARGIN_SECONDS,
    )

    settings.output_dir.mkdir(parents=True, exist_ok=True)
    if settings.cache_dir is not None:
        settings.cache_dir.mkdir(parents=True, exist_ok=True)

    with featurebench_config(settings.cache_dir) as config_path:
        config = api.InferConfig(
            agent=AGENT_NAME,
            model=settings.model,
            dataset=str(dataset_path),
            n_concurrent=1,
            n_attempts=1,
            task_ids=[settings.task_id],
            output_dir=settings.output_dir,
            timeout=settings.timeout,
            split=settings.split,
            without_interface_descriptions=False,
            white_box=False,
        )
        runner = api.InferenceRunner(config, config_path=config_path)
        runner.agent_env_vars.update(env)
        validate_strict_env(runner.agent_env_vars)
        _write_run_provenance(
            runner.output_dir / "askme-canary.json",
            settings,
            sha256_file(settings.askme_path),
            protocol,
        )
        retained_endpoint_catalog_preflight(
            settings.model,
            settings.provider,
            settings.expected_served_models,
            api_key,
            runner.output_dir / ENDPOINT_CATALOG_PREFLIGHT,
        )
        with registered_askme_agent(api.run_infer_module, agent_class):
            runner_exit = runner.run()
        audit = _audit_retained_run(
            settings,
            runner.output_dir,
            api_key,
            runner_exit,
        )
        if runner_exit:
            return runner_exit, runner.output_dir
        if not audit.get("infrastructure_valid"):
            return 2, runner.output_dir
        if not audit.get("qualification_valid"):
            return 3, runner.output_dir
        return 0, runner.output_dir


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one AskMe-adapted FeatureBench task through InferenceRunner."
    )
    parser.add_argument("--featurebench-root", type=Path, required=True)
    parser.add_argument("--featurebench-revision", required=True)
    parser.add_argument("--askme-path", type=Path, default=Path(__file__).parents[2] / "askme.py")
    parser.add_argument("--askme-revision", required=True)
    parser.add_argument(
        "--protocol-path",
        type=Path,
        default=Path(__file__).with_name("canary-protocol.json"),
    )
    parser.add_argument("--dataset-path", type=Path, required=True)
    parser.add_argument("--dataset-revision", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--expected-served-model",
        action="append",
        required=True,
        dest="expected_served_models",
    )
    parser.add_argument("--split", default="fast")
    parser.add_argument("--provider", default="siliconflow")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--cache-dir", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = CanarySettings(
        featurebench_root=args.featurebench_root,
        featurebench_revision=args.featurebench_revision,
        askme_path=args.askme_path,
        dataset_path=args.dataset_path,
        dataset_revision=args.dataset_revision,
        output_dir=args.output_dir,
        task_id=args.task_id,
        model=args.model,
        askme_revision=args.askme_revision,
        protocol_path=args.protocol_path,
        expected_served_models=tuple(args.expected_served_models),
        split=args.split,
        provider=args.provider,
        timeout=args.timeout,
        cache_dir=args.cache_dir,
    )
    exit_code, run_dir = run_canary(
        settings,
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
    )
    print(f"FeatureBench run directory: {run_dir}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
