"""Offline tests for benchmark-harness evidence retention."""

import json
import subprocess

import bench_harness


def test_failure_diagnostic_retains_bounded_stream_tails(tmp_path):
    prefix = "discard-me-" * 400
    stdout_tail = "stdout assertion: expected executable main"
    stderr_tail = "stderr traceback tail"

    name = bench_harness.write_failure_diagnostic(
        tmp_path,
        "test_build",
        2,
        prefix + stdout_tail,
        prefix + stderr_tail,
    )

    assert name == "test_build_trial2_pytest.txt"
    diagnostic = (tmp_path / name).read_text(encoding="utf-8")
    assert stdout_tail in diagnostic
    assert stderr_tail in diagnostic
    assert "earlier characters omitted" in diagnostic
    assert prefix not in diagnostic
    assert len(diagnostic) < 2 * bench_harness.PYTEST_DIAGNOSTIC_STREAM_CHARS + 500


def test_failure_diagnostic_marks_empty_streams(tmp_path):
    name = bench_harness.write_failure_diagnostic(tmp_path, "test_empty", 1, "", "")

    diagnostic = (tmp_path / name).read_text(encoding="utf-8")
    assert diagnostic.count("(empty)") == 2


def test_timeout_retains_partial_pytest_output_in_summary(tmp_path, monkeypatch):
    def time_out(*args, **kwargs):
        args[3].write_text('{"event":', encoding="utf-8")
        raise subprocess.TimeoutExpired(
            cmd=["pytest"],
            timeout=1200,
            output=b"partial stdout",
            stderr=b"partial stderr",
        )

    monkeypatch.setattr(bench_harness, "run_single_test", time_out)
    monkeypatch.setattr(bench_harness, "git_state", lambda: ("0" * 40, False))

    bench_harness.main(
        [
            "--suite",
            "hard",
            "--test",
            "test_timeout",
            "--trials",
            "1",
            "--log-dir",
            str(tmp_path),
        ]
    )

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    diagnostic_name = "test_timeout_trial1_pytest.txt"
    assert summary["tests"]["test_timeout"]["pytest_diagnostics"] == [diagnostic_name]
    assert summary["tests"]["test_timeout"]["timed_out"] == [True]
    assert "line 1 column 10" in summary["tests"]["test_timeout"]["log_parse_errors"][0]
    diagnostic = (tmp_path / diagnostic_name).read_text(encoding="utf-8")
    assert diagnostic.startswith("Pytest timeout diagnostics")
    assert "stdout:\npartial stdout" in diagnostic
    assert "stderr:\npartial stderr" in diagnostic
    assert "b'partial" not in diagnostic
