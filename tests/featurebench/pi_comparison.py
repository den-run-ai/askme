"""Fresh, bounded whole-scaffold qualification for the known Berkeley task.

No historical runner or result is reused. Setup and controls have no API key;
the paid phase requires a separate claim for this exact GitHub run and source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = Path(__file__).with_name("pi-comparison-sep14.json")
RUNTIME = ("askme.py", "loop.py", "state.py", "policies.py", "actions.py", "llm.py")


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stage_runtime(harness, destination):
    """Exclude repository evidence, fixtures and history from the episode."""
    destination.mkdir()
    for file in RUNTIME:
        if (harness / file).is_symlink():
            raise ValueError("Runtime source cannot be a symlink")
        shutil.copyfile(harness / file, destination / file)


def command(args, *, cwd=None, output=None, check=True, timeout=1800, env=None):
    env = dict(os.environ if env is None else env)
    env.pop("OPENROUTER_API_KEY", None)
    if output:
        with Path(output).open("wb") as stream:
            return subprocess.run(
                args,
                cwd=cwd,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=check,
                timeout=timeout,
            )
    return subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True, check=check, timeout=timeout
    )


def validate_claim(claim, protocol_path, environment, workflow_revision):
    """A label or Actions rerun cannot mint another paid attempt."""
    expected = {
        "protocol": read(protocol_path)["protocol"],
        "protocol_sha256": digest(protocol_path),
        "run_id": int(environment.get("GITHUB_RUN_ID", "0")),
        "workflow_revision": workflow_revision,
    }
    if environment.get("GITHUB_REPOSITORY") != "den-run-ai/askme":
        raise ValueError("Wrong claim repository")
    if expected["run_id"] <= 0 or environment.get("GITHUB_RUN_ATTEMPT") != "1":
        raise ValueError("Only the claimed first Actions attempt may run")
    if any(claim.get(k) != v for k, v in expected.items()):
        raise ValueError("This run/revision does not own the paid claim")
    return expected


def episode_completion(harness, exit_code, artifact):
    """Exit zero alone never means a valid pi session completed."""
    if harness == "askme":
        return exit_code == 0 and artifact.get("status") in {"complete", "complete_unverified"}
    ends = [e for e in artifact if isinstance(e, dict) and e.get("type") == "agent_end"]
    if exit_code != 0 or not ends:
        return False
    messages = [
        e.get("message") for e in artifact if isinstance(e, dict) and e.get("type") == "message_end"
    ]
    messages.extend(ends[-1].get("messages", []))
    assistants = [m for m in messages if isinstance(m, dict) and m.get("role") == "assistant"]
    errors = any(isinstance(e, dict) and e.get("type") == "error" for e in artifact)
    if not assistants or errors:
        return False
    terminal = assistants[-1]
    return terminal.get("stopReason") == "stop" and not terminal.get("errorMessage")


def parse_events(path):
    events, malformed = [], 0
    if not Path(path).exists():
        return events, 1
    for line in Path(path).read_text(errors="replace").splitlines():
        if line.strip():
            try:
                event = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            if not isinstance(event, dict):
                malformed += 1
            else:
                events.append(event)
    return events, malformed


def normalize_acceptance(report, task):
    """Evaluator errors are unknown acceptance, never failed model scores."""
    item = report.get(task, {})
    valid = item.get("featurebench_eval_completed") is True and not any(
        key in item for key in ("error", "traceback")
    )
    return {
        "evaluator_valid": valid,
        "resolved": item.get("resolved") if valid else None,
        "patch_applied": item.get("patch_successfully_applied") if valid else None,
        "official_report": item,
    }


def endpoint_preflight(p, records):
    """Metadata GETs are free; reject stale route/price/precision before inference."""
    from decimal import Decimal

    routes = []
    for cell in p["cells"]:
        if any(r["model"] == cell["model"] for r in routes):
            continue
        with urllib.request.urlopen(
            "https://openrouter.ai/api/v1/models/" + cell["model"] + "/endpoints", timeout=30
        ) as response:
            catalog = json.load(response)
        candidates = [e for e in catalog["data"]["endpoints"] if e.get("tag") == cell["provider"]]
        if len(candidates) != 1:
            raise ValueError("Pinned provider endpoint is unavailable")
        e = candidates[0]
        if e.get("status") != 0 or e.get("quantization") != cell["quantization"]:
            raise ValueError("Pinned endpoint status/precision changed")
        if e.get("name") != cell["served_provider"] + " | " + cell["served_model"]:
            raise ValueError("Exact served model identity changed")
        for key, limit in (
            ("prompt", "input_price_per_million"),
            ("completion", "output_price_per_million"),
        ):
            if Decimal(str(e["pricing"][key])) * 1000000 > Decimal(str(cell[limit])):
                raise ValueError("Provider price exceeds the preregistered reservation rate")
        if not {"tools", "tool_choice", "max_tokens", "reasoning"}.issubset(
            e["supported_parameters"]
        ):
            raise ValueError("Route cannot honor the common parameter contract")
        routes.append({"model": cell["model"], "endpoint": e})
    save(records / "endpoint-preflight.json", routes)


def eval_prediction(p, records, fb, dataset, prediction, name):
    target = records / name
    target.mkdir(exist_ok=True)
    if prediction != "gold":
        save(target / "output.jsonl", prediction)
        # A JSONL prediction is exactly one line, independent of pretty JSON helpers.
        (target / "output.jsonl").write_text(json.dumps(prediction) + "\n")
        prediction = str(target / "output.jsonl")
    result = command(
        [
            str(fb),
            "eval",
            "--predictions-path",
            prediction,
            "--dataset",
            dataset,
            "--split",
            p["split"],
            "--task-id",
            p["task_id"],
            "--n-concurrent",
            "1",
            "--include-failed",
        ],
        cwd=target,
        output=target / "evaluator.log",
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Official evaluator command failed: {name}")
    if prediction == "gold":
        report = read(target / "runs/gold/report.json")["attempt_1"]
        if any(
            report.get(k) != v
            for k, v in {
                "total_instances": 1,
                "completed_instances": 1,
                "resolved_instances": 1,
                "error_instances": 0,
            }.items()
        ):
            raise RuntimeError("Gold control did not resolve")
        return report
    report_path = target / "eval_outputs" / p["task_id"] / "attempt-1/report.json"
    result = normalize_acceptance(read(report_path), p["task_id"])
    save(target / "acceptance.json", result)
    return result


def prepare(p, records, fb_root, harness):
    """Finish all setup and independent controls before the paid phase."""
    from datasets import load_dataset
    from huggingface_hub import snapshot_download

    if "OPENROUTER_API_KEY" in os.environ:
        raise ValueError("The setup phase must be credential-free")
    records.mkdir(parents=True, exist_ok=False)
    save(records / "protocol.json", p)
    endpoint_preflight(p, records)
    for checkout, revision in (
        (fb_root, p["featurebench_revision"]),
        (harness, p["askme_revision"]),
    ):
        got = command(["git", "-C", str(checkout), "rev-parse", "HEAD"]).stdout.strip()
        if got != revision:
            raise ValueError("Source revision differs from the preregistration")
    dataset = str(
        Path(
            snapshot_download(
                repo_id=p["dataset_repository"], repo_type="dataset", revision=p["dataset_revision"]
            )
        ).resolve()
    )
    if Path(dataset).name != p["dataset_revision"]:
        raise ValueError("Dataset snapshot is not immutable")
    rows = [r for r in load_dataset(dataset, split=p["split"]) if r["instance_id"] == p["task_id"]]
    if len(rows) != 1:
        raise ValueError("Expected exactly the frozen task")
    row = rows[0]
    if hashlib.sha256(row["problem_statement"].encode()).hexdigest() != p["prompt_sha256"]:
        raise ValueError("Frozen prompt changed")
    (records / "prompt.txt").write_text(row["problem_statement"])
    (records / "mask.patch").write_text(row["patch"])
    f2p = row["FAIL_TO_PASS"]
    f2p = f2p if isinstance(f2p, list) else [f2p]
    if any(Path(path).is_absolute() or ".." in Path(path).parts for path in f2p):
        raise ValueError("Unexpected masked test path")
    save(records / "hidden-files.json", f2p)
    pinned = p["image"] + "@" + p["image_digest"]
    command(
        ["docker", "pull", "--platform", "linux/amd64", pinned], output=records / "image-pull.log"
    )
    command(["docker", "tag", pinned, p["image"]])
    image_id = command(["docker", "image", "inspect", pinned, "--format", "{{.Id}}"]).stdout.strip()
    tagged = command(
        ["docker", "image", "inspect", p["image"], "--format", "{{.Id}}"]
    ).stdout.strip()
    if image_id != tagged:
        raise ValueError("Evaluator image tag does not match the frozen digest")
    fb = fb_root / ".venv/bin/fb"
    gold = eval_prediction(p, records, fb, dataset, "gold", "gold-control")
    negative = {
        "instance_id": p["task_id"],
        "agent": "harmless-control",
        "model": "none",
        "n_attempt": 1,
        "success": True,
        "error": None,
        "model_patch": "diff --git a/ASKME_CONTROL.txt b/ASKME_CONTROL.txt\nnew file mode 100644\n--- /dev/null\n+++ b/ASKME_CONTROL.txt\n@@ -0,0 +1 @@\n+harmless control\n",
    }
    control = eval_prediction(p, records, fb, dataset, negative, "negative-control")
    if (
        not control["evaluator_valid"]
        or control["resolved"] is not False
        or control["patch_applied"] is not True
    ):
        raise ValueError("Harmless control must apply and remain unresolved")
    save(records / "controls.json", {"qualified": True, "gold": gold, "negative": control})
    save(
        records / "setup.json",
        {
            "dataset": dataset,
            "image_id": image_id,
            "runtime_sha256": {f: digest(harness / f) for f in RUNTIME},
            "featurebench_lock_sha256": digest(fb_root / "uv.lock"),
            "askme_lock_sha256": digest(harness / "uv.lock"),
        },
    )
    stage_runtime(harness, records / "runtime")


def inside_askme():
    """Normal immutable composition path, fresh process, proxy-only credential."""
    from dataclasses import replace

    sys.path.insert(0, "/harness")
    import askme

    p = read("/study/protocol.json")
    cfg = p["askme_config"]
    cell = next(c for c in p["cells"] if c["id"] == os.environ["STUDY_CELL"])
    settings = askme.LLMSettings.from_env(
        {
            "LLM_BACKEND": "openrouter",
            "OPENROUTER_MODEL": cell["model"],
            "OPENROUTER_API_KEY": os.environ["STUDY_PROXY_BEARER"],
            "OPENROUTER_PROVIDER": cell["provider"],
            "OPENROUTER_ALLOW_FALLBACKS": "0",
            "OPENROUTER_REQUIRE_PARAMETERS": "1",
            "LLM_CAPABILITY_PROFILE": cfg["capability_profile"],
        }
    )
    settings = replace(
        settings, api=os.environ["STUDY_PROXY_URL"] + "/chat/completions", max_retries=0
    )
    config = askme.RunConfig(
        llm=settings,
        reasoning_policy=cfg["reasoning_policy"],
        max_replans=cfg["max_replans"],
        max_tasks=cfg["max_tasks"],
        max_steps=cfg["max_steps"],
        goal_context_chars=cfg["goal_context_chars"],
    )
    result = askme.run_result(
        Path("/study/prompt.txt").read_text(), working_dir="/testbed", config=config
    )
    save("/evidence/result.json", result)
    return 0 if result["status"] in askme.COMPLETE_STATUSES else 1


def initialize_container(p, records, cell, image, network, harness):
    name = "berkeley-" + cell["id"] + "-" + str(os.getpid())
    target = records / cell["id"]
    target.mkdir()
    command(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network,
            "--mount",
            f"type=bind,src={harness},dst=/harness,readonly",
            "--mount",
            f"type=bind,src={target},dst=/evidence",
            image,
            "tail",
            "-f",
            "/dev/null",
        ]
    )
    # Clean all prior contents, then follow official Level-1 masking.
    command(
        [
            "docker",
            "exec",
            name,
            "bash",
            "-lc",
            "rm -rf /testbed && mkdir /testbed && cp -r /root/my_repo/* /testbed/ && rm -rf /root/my_repo",
        ]
    )
    command(["docker", "cp", str(records / "mask.patch"), name + ":/tmp/mask.patch"])
    command(
        [
            "docker",
            "exec",
            name,
            "bash",
            "-lc",
            "cd /testbed && git apply --whitespace=fix /tmp/mask.patch && rm /tmp/mask.patch",
        ]
    )
    hidden = read(records / "hidden-files.json")
    for path in hidden:
        command(["docker", "exec", name, "rm", "-f", "/testbed/" + path])
    command(
        [
            "docker",
            "exec",
            name,
            "bash",
            "-lc",
            "cd /testbed && rm -rf .git && git init -q && git config user.email fb@bench.com && git config user.name FeatureBench && git add -A && git commit -qm baseline --allow-empty",
        ]
    )
    tree = command(
        ["docker", "exec", name, "git", "-C", "/testbed", "rev-parse", "HEAD^{tree}"]
    ).stdout.strip()
    save(target / "workspace-preflight.json", {"masked_tree": tree, "hidden_files": hidden})
    command(["docker", "exec", name, "mkdir", "-p", "/study"])
    for file in ("prompt.txt", "protocol.json"):
        command(["docker", "cp", str(records / file), name + ":/study/" + file])
    command(["docker", "cp", str(Path(__file__)), name + ":/study/runner.py"])
    return name, target, tree


def collect_patch(name, target):
    # Match official extraction including new text files and excluding binaries.
    script = """cd /testbed && git add -A
git diff --cached --numstat --no-renames --diff-filter=ACMRTD | awk -F '\\t' '$1=="-" || $2=="-" {print $3}' | while IFS= read -r f; do git reset HEAD -- "$f" >/dev/null; done
git diff --no-color --cached "$(git rev-list --max-parents=0 HEAD)"
"""
    patch = command(["docker", "exec", name, "bash", "-lc", script]).stdout
    (target / "patch.diff").write_text(patch)
    return patch


def run_cells(p, records, fb_root, harness, claim):
    if not read(records / "controls.json")["qualified"]:
        raise ValueError("Unqualified controls")
    revision = command(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).stdout.strip()
    checked_claim = validate_claim(read(claim), PROTOCOL, os.environ, revision)
    save(records / "claim.json", checked_claim)
    setup = read(records / "setup.json")
    tooling = read(records / "tooling.json")
    if read(records / "protocol.json") != p:
        raise ValueError("Staged protocol changed after qualification")
    staged = records / "runtime"
    if {f.name for f in staged.iterdir()} != set(RUNTIME):
        raise ValueError("Runtime stage contains missing or unexpected files")
    for file in RUNTIME:
        if (
            staged.joinpath(file).is_symlink()
            or digest(staged / file) != setup["runtime_sha256"][file]
        ):
            raise ValueError("Staged runtime changed after qualification")
        if digest(harness / file) != setup["runtime_sha256"][file]:
            raise ValueError("Pinned runtime changed after qualification")
    for checkout, expected in (
        (harness, p["askme_revision"]),
        (fb_root, p["featurebench_revision"]),
    ):
        if command(["git", "-C", str(checkout), "rev-parse", "HEAD"]).stdout.strip() != expected:
            raise ValueError("Source revision changed after qualification")
        command(["git", "-C", str(checkout), "diff", "--exit-code", "HEAD"])
    if (
        command(
            ["docker", "image", "inspect", tooling["image_id"], "--format", "{{.Id}}"]
        ).stdout.strip()
        != tooling["image_id"]
    ):
        raise ValueError("Qualified tooling image disappeared")
    endpoint_preflight(p, records)
    bearer = secrets.token_hex(24)
    network = "berkeley-study-" + str(os.getpid())
    command(["docker", "network", "create", "--internal", network])
    gateway = command(
        ["docker", "network", "inspect", network, "--format", "{{(index .IPAM.Config 0).Gateway}}"]
    ).stdout.strip()
    proxy_env = dict(os.environ, PI_PROXY_SECRET=bearer)
    os.environ.pop("OPENROUTER_API_KEY", None)
    proxy = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("pi_comparison_proxy.py")),
            "--manifest",
            str(PROTOCOL),
            "--ledger",
            str(records / "proxy"),
            "--port",
            "8787",
        ],
        env=proxy_env,
        stdout=(records / "proxy.log").open("wb"),
        stderr=subprocess.STDOUT,
    )
    outcomes, names, baseline = [], [], None
    try:
        for _ in range(100):
            if proxy.poll() is not None:
                raise RuntimeError("Budget proxy failed before inference")
            try:
                urllib.request.urlopen("http://127.0.0.1:8787/health", timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Budget proxy startup timeout")
        for cell in p["cells"]:
            name, target, tree = initialize_container(
                p, records, cell, tooling["image_id"], network, records / "runtime"
            )
            names.append(name)
            if baseline is not None and tree != baseline:
                raise RuntimeError("Paired workspace initialization differs")
            baseline = tree
            endpoint = "http://" + gateway + ":8787/v1"
            cell_env = dict(
                os.environ,
                STUDY_CELL=cell["id"],
                STUDY_PROXY_URL=endpoint,
                STUDY_PROXY_BEARER=bearer + ":" + cell["id"],
            )
            # Environment names on Docker CLI avoid putting credentials in argv/logs.
            env_args = ["-e", "STUDY_CELL", "-e", "STUDY_PROXY_URL", "-e", "STUDY_PROXY_BEARER"]
            if cell["harness"] == "askme":
                invocation = ["python3", "/study/runner.py", "inside"]
                env_args += ["-e", "AGENT_RUN_LOG=/evidence/trajectory.jsonl"]
            else:
                models = {
                    "providers": {
                        "study": {
                            "baseUrl": endpoint,
                            "api": "openai-completions",
                            "apiKey": "$STUDY_PROXY_BEARER",
                            "compat": {
                                "supportsDeveloperRole": False,
                                "supportsReasoningEffort": False,
                            },
                            "models": [
                                {
                                    "id": cell["model"],
                                    "name": cell["model"],
                                    "reasoning": False,
                                    "input": ["text"],
                                    "contextWindow": 131072,
                                    "maxTokens": 8192,
                                    "cost": {
                                        "input": 0,
                                        "output": 0,
                                        "cacheRead": 0,
                                        "cacheWrite": 0,
                                    },
                                }
                            ],
                        }
                    }
                }
                save(target / "models.json", models)
                command(["docker", "exec", name, "mkdir", "-p", "/pi-config"])
                command(
                    ["docker", "cp", str(target / "models.json"), name + ":/pi-config/models.json"]
                )
                env_args += [
                    "-e",
                    "PI_CODING_AGENT_DIR=/pi-config",
                    "-e",
                    "PI_CODING_AGENT_SESSION_DIR=/evidence/sessions",
                ]
                invocation = [
                    "bash",
                    "-lc",
                    "exec /opt/pi/node_modules/.bin/pi --offline --provider study --model "
                    + cell["model"]
                    + ' --mode json -p "$(cat /study/prompt.txt)"',
                ]
            start = time.monotonic()
            run = command(
                [
                    "docker",
                    "exec",
                    "-w",
                    "/testbed",
                    *env_args,
                    name,
                    "timeout",
                    "--signal=TERM",
                    "--kill-after=15s",
                    str(p["wall_seconds_per_cell"]),
                    *invocation,
                ],
                env=cell_env,
                output=target / "agent-output.jsonl",
                check=False,
                timeout=p["wall_seconds_per_cell"] + 60,
            )
            wall = time.monotonic() - start
            patch = collect_patch(name, target)
            command(["docker", "exec", name, "chmod", "-R", "a+rX", "/evidence"])
            # Stop every model-spawned descendant before host-only scoring. A
            # background process must never observe or modify evaluator reports.
            command(["docker", "rm", "-f", name])
            names.remove(name)
            if cell["harness"] == "askme":
                artifact = read(target / "result.json") if (target / "result.json").exists() else {}
                malformed = 0
            else:
                artifact, malformed = parse_events(target / "agent-output.jsonl")
            complete = (
                episode_completion(cell["harness"], run.returncode, artifact) and not malformed
            )
            prediction = {
                "instance_id": p["task_id"],
                "model_patch": patch,
                "agent": cell["harness"],
                "model": cell["model"],
                "n_attempt": 1,
                "success": complete,
                "error": None,
            }
            outcome = {
                "cell": cell,
                "exit_code": run.returncode,
                "wall_seconds": wall,
                "agent_completion": complete,
                "malformed_event_lines": malformed,
                "patch_bytes": len(patch.encode()),
                "terminal_status": "timeout"
                if run.returncode == 124
                else "complete"
                if complete
                else "incomplete",
            }
            try:
                acceptance = eval_prediction(
                    p, target, fb_root / ".venv/bin/fb", setup["dataset"], prediction, "evaluation"
                )
                outcome.update(acceptance)
            except (OSError, ValueError, subprocess.SubprocessError, RuntimeError) as exc:
                outcome.update(
                    evaluator_valid=False,
                    resolved=None,
                    patch_applied=None,
                    evaluator_error=type(exc).__name__,
                )
            save(target / "outcome.json", outcome)
            outcomes.append(outcome)
            with urllib.request.urlopen(
                urllib.request.Request(
                    "http://127.0.0.1:8787/ledger",
                    headers={"Authorization": "Bearer " + bearer + ":" + cell["id"]},
                ),
                timeout=30,
            ) as response:
                ledger = json.load(response)
            calls = [c for c in ledger["calls"] if c["cell"] == cell["id"]]
            outcome["budget"] = ledger["cells"][cell["id"]]
            outcome["route_audit_valid"] = bool(calls) and all(
                c.get("route_valid") is True for c in calls
            )
            outcome["infrastructure_valid"] = (
                outcome["evaluator_valid"] and outcome["route_audit_valid"]
            )
            save(target / "outcome.json", outcome)
            save(
                records / "summary.json",
                {"protocol": p["protocol"], "label": p["label"], "cells": outcomes},
            )
    finally:
        proxy.terminate()
        proxy.wait(timeout=10)
        for name in names:
            command(["docker", "rm", "-f", name], check=False)
        command(["docker", "network", "rm", network], check=False)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "run", "inside"))
    parser.add_argument("--records", type=Path)
    parser.add_argument("--featurebench", type=Path)
    parser.add_argument("--harness", type=Path)
    parser.add_argument("--claim", type=Path)
    args = parser.parse_args(argv)
    if args.mode == "inside":
        return inside_askme()
    p = read(PROTOCOL)
    if args.mode == "prepare":
        prepare(p, args.records.resolve(), args.featurebench.resolve(), args.harness.resolve())
    else:
        run_cells(
            p,
            args.records.resolve(),
            args.featurebench.resolve(),
            args.harness.resolve(),
            args.claim,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
