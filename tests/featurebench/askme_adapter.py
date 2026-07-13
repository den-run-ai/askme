#!/usr/bin/env python3
"""Run one AskMe canary through FeatureBench's official inference runner.

FeatureBench is an optional evaluation dependency. Imports stay lazy so AskMe's
normal unit suite does not require it.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Mapping


AGENT_NAME = "askme"
PROMPT_PATH = "/installed-agent/task-prompt.txt"
ASKME_PATH = "/installed-agent/askme.py"
LOG_DIR = "/agent-logs"
RESULT_PATH = f"{LOG_DIR}/askme-result.json"
RUN_LOG_PATH = f"{LOG_DIR}/askme-run.jsonl"
STDOUT_LOG_PATH = f"{LOG_DIR}/askme-stdout.log"
ADAPTER_MANIFEST_PATH = f"{LOG_DIR}/askme-adapter.json"

PRESERVED_ARTIFACTS = {
    RESULT_PATH: "askme-result.json",
    RUN_LOG_PATH: "askme-run.jsonl",
    STDOUT_LOG_PATH: "askme-stdout.log",
    ADAPTER_MANIFEST_PATH: "askme-adapter.json",
    PROMPT_PATH: "askme-prompt.txt",
}


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


def strict_openrouter_env(model: str, provider: str, api_key: str) -> dict[str, str]:
    """Build the fixed, no-fallback route used by the canary."""
    model = model.strip()
    provider = provider.strip()
    if not model:
        raise ValueError("OpenRouter model is required")
    if not provider:
        raise ValueError("OpenRouter provider is required")
    if provider.lower() == "auto":
        raise ValueError("OpenRouter provider must be pinned, not auto")
    if not api_key.strip():
        raise ValueError("OPENROUTER_API_KEY is required")
    return {
        "LLM_BACKEND": "openrouter",
        "OPENROUTER_API_KEY": api_key,
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
    for key in ("OPENROUTER_API_KEY", "OPENROUTER_MODEL", "OPENROUTER_PROVIDER"):
        if not str(env.get(key, "")).strip():
            mismatches.append(f"{key} is required")
    if mismatches:
        raise ValueError("invalid AskMe canary environment: " + "; ".join(mismatches))


def _copy_bytes(cm: Any, container: Any, data: bytes, destination: str) -> None:
    with tempfile.NamedTemporaryFile() as temporary:
        temporary.write(data)
        temporary.flush()
        cm.copy_to_container(container, Path(temporary.name), destination)


def build_askme_agent_class(base_agent: type, askme_source: Path) -> type:
    """Create a FeatureBench BaseAgent subclass bound to one AskMe snapshot."""
    source = askme_source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"AskMe source not found: {source}")
    pinned_sha256 = sha256_file(source)
    pinned_size = source.stat().st_size

    class AskMeFeatureBenchAgent(base_agent):
        _source = source
        _source_sha256 = pinned_sha256
        _source_size = pinned_size

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
python3 -c 'import requests'
"""

        def get_env_setup_script(self) -> str:
            # The values are already in the container environment. Keeping the
            # API key out of this generated script prevents it entering infer.log.
            return """#!/bin/bash
test "${LLM_BACKEND:-}" = "openrouter"
test "${OPENROUTER_ALLOW_FALLBACKS:-}" = "0"
test "${OPENROUTER_REQUIRE_PARAMETERS:-}" = "1"
test "${ALLOW_NETWORK:-}" = "0"
test -n "${OPENROUTER_API_KEY:-}"
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
                exit_code, _ = self.cm.exec_command(
                    container,
                    f"chmod 0555 {ASKME_PATH}",
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
                "goal_context_chars": self._prompt_chars,
                "model": self.env_vars["OPENROUTER_MODEL"],
                "provider": self.env_vars["OPENROUTER_PROVIDER"],
                "allow_provider_fallbacks": False,
                "require_provider_parameters": True,
                "allow_agent_network_actions": False,
                "reasoning_policy": self.env_vars["AGENT_REASONING_POLICY"],
                "limits": {"max_replans": 3, "max_tasks": 10, "max_steps": 10},
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
                f"python3 {ASKME_PATH} "
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
                STDOUT_LOG_PATH,
                ADAPTER_MANIFEST_PATH,
                PROMPT_PATH,
            )
            missing = [path for path in required_artifacts if not copied.get(path)]
            if missing:
                self.logger.error(
                    "AskMe run artifacts were not preserved: " + ", ".join(missing)
                )
                return False
            try:
                result = json.loads((Path(log_file).parent / "askme-result.json").read_text())
            except (OSError, json.JSONDecodeError) as error:
                self.logger.error(f"Invalid AskMe structured result: {error}")
                return False
            if result.get("status") != "complete":
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


def _write_run_provenance(
    path: Path,
    settings: CanarySettings,
    askme_sha256: str,
) -> None:
    data = {
        "schema_version": 1,
        "description": "AskMe-adapted FeatureBench one-task canary",
        "dataset_path": str(settings.dataset_path.resolve()),
        "dataset_revision": settings.dataset_revision,
        "split": settings.split,
        "task_id": settings.task_id,
        "model": settings.model,
        "provider": settings.provider,
        "allow_provider_fallbacks": False,
        "require_provider_parameters": True,
        "allow_agent_network_actions": False,
        "goal_context": "full FeatureBench problem_statement",
        "limits": {"max_replans": 3, "max_tasks": 10, "max_steps": 10},
        "askme_sha256": askme_sha256,
        "askme_repository_revision": _git_revision(settings.askme_path.parent),
        "askme_git_dirty": _git_is_dirty(settings.askme_path.parent),
        "featurebench_revision": _git_revision(settings.featurebench_root),
        "featurebench_git_dirty": _git_is_dirty(settings.featurebench_root),
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_canary(
    settings: CanarySettings,
    api_key: str,
    featurebench_api: Any | None = None,
) -> tuple[int, Path]:
    """Execute one task with FeatureBench InferenceRunner and return its run path."""
    if settings.timeout < 1:
        raise ValueError("timeout must be positive")
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
    dataset_path = settings.dataset_path.resolve()
    if not dataset_path.is_dir():
        raise FileNotFoundError(f"FeatureBench dataset snapshot not found: {dataset_path}")
    if not settings.dataset_revision.strip():
        raise ValueError("dataset_revision is required")
    env = strict_openrouter_env(settings.model, settings.provider, api_key)
    api = featurebench_api or load_featurebench_api(settings.featurebench_root)
    agent_class = build_askme_agent_class(api.BaseAgent, settings.askme_path)

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
        )
        with registered_askme_agent(api.run_infer_module, agent_class):
            exit_code = runner.run()
        return exit_code, runner.output_dir


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one AskMe-adapted FeatureBench task through InferenceRunner."
    )
    parser.add_argument("--featurebench-root", type=Path, required=True)
    parser.add_argument("--featurebench-revision", required=True)
    parser.add_argument("--askme-path", type=Path, default=Path(__file__).parents[2] / "askme.py")
    parser.add_argument("--dataset-path", type=Path, required=True)
    parser.add_argument("--dataset-revision", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--model", required=True)
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
