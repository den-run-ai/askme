"""Offline tests for benchmark-harness evidence retention."""

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
