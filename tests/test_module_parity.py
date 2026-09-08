"""Frozen pre-extraction API and whole-run transcript parity, without inference."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.runtime_characterization import capture, cases

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "fixtures" / "runtime-module-baseline.json"


@pytest.fixture(scope="module")
def captured():
    # No inherited provider/model policy, credential, or live-test opt-in.
    env = {"PATH": os.defpath, "PYTHONHASHSEED": "0", "COLUMNS": "80"}
    for name in ("SYSTEMROOT", "WINDIR"):
        if name in os.environ:
            env[name] = os.environ[name]
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tests" / "runtime_characterization.py"),
            "--runtime-root",
            str(ROOT),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


@pytest.fixture(scope="module")
def baseline():
    return json.loads(BASELINE.read_text(encoding="utf-8"))["capture"]


def test_public_namespace_signatures_and_cli_help_unchanged(captured, baseline):
    assert captured["contract"] == baseline["contract"]


@pytest.mark.parametrize(
    "name",
    [
        "write_edit_verify",
        "lifecycle_rewrite",
        "duplicate_write",
        "observation_stall",
        "accepted_fail",
        "empty_write",
        "partial_write",
        "validation_recheck",
        "validator_unavailable",
    ],
)
def test_complete_scripted_transcripts_and_workspace_unchanged(name, captured, baseline):
    assert captured["cases"][name] == baseline["cases"][name]


@pytest.mark.parametrize("name", list(cases()))
def test_complete_transcripts_ignore_workspace_root_symlink(name, tmp_path):
    import askme

    real_root = tmp_path / "real"
    real_root.mkdir()
    alias_root = tmp_path / "link"
    alias_root.symlink_to(real_root, target_is_directory=True)
    spec = cases()[name]

    # Equal-length roots preserve the runtime's real character-budget behavior.
    canonical = capture(askme, spec, real_root / "physical")
    aliased = capture(askme, spec, alias_root / "symbolic")

    # Mutation identities resolve symlinks; model-visible workspace paths do not.
    # Both must normalize to the same complete transcript, not a host prefix.
    assert aliased == canonical
