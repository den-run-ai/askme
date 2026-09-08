"""Offline wall-clock and descendant regressions for captured commands."""

import errno
import io
import os
import shlex
import subprocess
import sys
import time
from unittest.mock import Mock

import bench_harness
import pytest

import actions


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group lifecycle")
@pytest.mark.parametrize("denied_signal", [actions.signal.SIGTERM, actions.signal.SIGKILL])
def test_timeout_reaps_zombie_group_before_retrying_darwin_permission_error(
    tmp_path, monkeypatch, denied_signal
):
    # Darwin excludes zombies from killpg's eligible recipients and returns
    # EPERM until the only remaining member (our direct child) is reaped.
    command = ["timed-out-command"]
    process = Mock(pid=12345, returncode=None, stdout=io.BytesIO(), stderr=io.BytesIO())
    process.communicate.side_effect = subprocess.TimeoutExpired(
        command, 0.2, output=b"partial stdout", stderr=b"partial stderr"
    )

    def reap():
        process.returncode = -actions.signal.SIGTERM
        return process.returncode

    process.poll.side_effect = reap

    def signal_group(_pid, signum):
        if signum == denied_signal:
            if process.returncode is None:
                raise PermissionError(errno.EPERM, "Operation not permitted")
            raise ProcessLookupError(errno.ESRCH, "No such process")

    monkeypatch.setattr(actions.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(actions.os, "killpg", signal_group)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        actions.CapturedProcess.run(command, timeout=0.2, cwd=tmp_path)
    assert caught.value.output == b"partial stdout"
    assert caught.value.stderr == b"partial stderr"
    assert process.stdout.closed and process.stderr.closed
    process.wait.assert_called_once_with(timeout=actions.PROCESS_TERMINATION_GRACE)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group lifecycle")
@pytest.mark.parametrize("returncode", [None, -actions.signal.SIGTERM])
def test_timeout_does_not_hide_permission_denial_for_existing_group(
    tmp_path, monkeypatch, returncode
):
    command = ["timed-out-command"]
    process = Mock(pid=12345, stdout=io.BytesIO(), stderr=io.BytesIO())
    process.poll.return_value = returncode
    process.communicate.side_effect = subprocess.TimeoutExpired(command, 0.2)
    denied = PermissionError(errno.EPERM, "Operation not permitted")
    monkeypatch.setattr(actions.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(actions.os, "killpg", Mock(side_effect=denied))
    with pytest.raises(PermissionError) as caught:
        actions.CapturedProcess.run(command, timeout=0.2, cwd=tmp_path)
    assert caught.value is denied
    assert process.stdout.closed and process.stderr.closed
    process.wait.assert_called_once_with(timeout=actions.PROCESS_TERMINATION_GRACE)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group lifecycle")
@pytest.mark.parametrize("close_output", [False, True])
def test_shell_timeout_stops_descendants_without_waiting_for_inherited_output(
    tmp_path, monkeypatch, close_output
):
    # The child ignores graceful termination and inherits both output pipes.
    # Every process expires by itself within 1.5 seconds even on broken code.
    marker = tmp_path / "descendant-survived"
    child = (
        "import os, pathlib, signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('child ready', flush=True); "
        + ("os.close(1); os.close(2); " if close_output else "")
        + "time.sleep(1); "
        f"pathlib.Path({str(marker)!r}).write_text('survived')"
    )
    parent = tmp_path / "spawn.py"
    parent.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        "time.sleep(1.5)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(actions, "_get_shell_timeout", lambda *_args: 0.2)

    started = time.monotonic()
    result = actions.ActionExecutor(tmp_path).dispatch(
        {"action": "shell", "arg": shlex.join([sys.executable, str(parent)])}
    )
    elapsed = time.monotonic() - started

    assert result.ok is False
    assert result.output == "TIMEOUT"
    assert result.error_type == "timeout"
    assert elapsed < 0.9, f"0.2-second timeout took {elapsed:.2f}s"
    time.sleep(max(0, 1.2 - elapsed))
    assert not marker.exists(), "timed-out descendant continued modifying the workspace"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group lifecycle")
def test_trial_timeout_retains_output_and_stops_grace_resistant_children(tmp_path, monkeypatch):
    marker = tmp_path / "descendant-survived"
    child = (
        "import pathlib, signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('trial child ready', flush=True); "
        "time.sleep(1); "
        f"pathlib.Path({str(marker)!r}).write_text('survived')"
    )
    original_popen = subprocess.Popen

    def trial_process(command, **kwargs):
        # Replace only the paid pytest program, retaining the harness's real
        # subprocess setup and deadline. There is no provider/network call.
        assert command[1:3] == ["-m", "pytest"]
        program = (
            "import subprocess, sys, time; "
            "print('partial trial stdout', flush=True); "
            "print('partial trial stderr', file=sys.stderr, flush=True); "
            f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
            "time.sleep(1.5)"
        )
        return original_popen([sys.executable, "-c", program], **kwargs)

    monkeypatch.setattr(actions.subprocess, "Popen", trial_process)
    monkeypatch.setattr(bench_harness, "TRIAL_TIMEOUT", 0.2)
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        bench_harness.run_single_test("test_example", "easy", "local", tmp_path / "trial.jsonl")
    elapsed = time.monotonic() - started
    assert elapsed < 0.9, f"0.2-second trial timeout took {elapsed:.2f}s"
    assert b"partial trial stdout" in caught.value.output
    assert b"partial trial stderr" in caught.value.stderr
    time.sleep(max(0, 1.2 - elapsed))
    assert not marker.exists(), "timed-out trial descendant still wrote to the workspace"


@pytest.mark.skipif(os.name != "posix", reason="POSIX escaped-session regression")
def test_escaped_descendant_cannot_make_timeout_output_drain_unbounded(tmp_path):
    child = "import time; print('detached child ready', flush=True); time.sleep(1.5)"
    program = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}], start_new_session=True); "
        "time.sleep(1.5)"
    )
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        actions.CapturedProcess.run([sys.executable, "-c", program], timeout=0.2, cwd=tmp_path)
    elapsed = time.monotonic() - started
    assert elapsed < 0.9, f"escaped inherited pipe delayed timeout to {elapsed:.2f}s"
    assert b"detached child ready" in caught.value.output


def test_windows_file_capture_keeps_timeout_bounded_and_preserves_diagnostics(tmp_path):
    # Exercise the non-POSIX capture path with real files/subprocess I/O even
    # on Linux. Missing taskkill must still allow bounded direct-child cleanup.
    program = (
        "import sys, time; print('partial stdout', flush=True); "
        "print('partial stderr', file=sys.stderr, flush=True); time.sleep(1.5)"
    )
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        actions.CapturedProcess._run_windows(
            [sys.executable, "-c", program], timeout=0.2, cwd=tmp_path, shell=False, env=None
        )
    assert time.monotonic() - started < 0.9
    assert b"partial stdout" in caught.value.output
    assert b"partial stderr" in caught.value.stderr


@pytest.mark.parametrize(
    "runner", [actions.CapturedProcess.run, actions.CapturedProcess._run_windows]
)
def test_timeout_diagnostics_preserve_invalid_or_incomplete_text_bytes(tmp_path, runner):
    program = (
        "import os, time; os.write(1, b'partial \\xe2'); os.write(2, b'\\xff'); time.sleep(1.5)"
    )
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        runner([sys.executable, "-c", program], timeout=0.2, cwd=tmp_path, shell=False, env=None)
    assert caught.value.output == b"partial \xe2"
    assert caught.value.stderr == b"\xff"


def test_windows_capture_preserves_successful_text_and_exit_status(tmp_path):
    result = actions.CapturedProcess._run_windows(
        [
            sys.executable,
            "-c",
            "import os; os.write(1, b'first\\r\\nsecond\\r'); os.write(2, b'error')",
        ],
        timeout=1,
        cwd=tmp_path,
        shell=False,
        env=None,
    )
    assert result.returncode == 0
    assert result.stdout == "first\nsecond\n"
    assert result.stderr == "error"
