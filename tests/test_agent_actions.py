"""Deterministic tests for the app-development action surface (issue #7).

No LLM calls: these pin the navigable read window, bounded search/tree,
chunked-write transport, truncation detection, and curated replan state.
"""
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import askme
from askme import (execute, _run_loop, _validate_action_contract,
                   READ_CHARS, READ_LIMIT_MAX,
                   SEARCH_MAX_MATCHES, SEARCH_MAX_CHARS,
                   TREE_MAX_ENTRIES, TREE_MAX_CHARS,
                   OBSERVE_STATE_CHARS, STEP_WRITE_TOKENS)


def _big_file(work_dir, name="big.py", lines=200):
    p = Path(work_dir, name)
    p.write_text("".join(f"def f{i}(): return {i}\n" for i in range(lines)))
    return p


# --- Ranged reads with continuation metadata ---

class TestRangedRead:
    def test_window_header_and_continuation(self, work_dir):
        _big_file(work_dir)
        result = execute({"action": "read", "arg": "big.py"}, work_dir)
        assert result["ok"] is True
        assert result["output"].startswith("[big.py: lines 1-")
        assert " of 200" in result["output"]
        assert result["truncated"] is True
        assert result["continuation"] > 1
        assert f"continue: offset={result['continuation']}" in result["output"]

    def test_continuation_navigation_covers_whole_file(self, work_dir):
        """Walking continuation offsets must tile the whole file contiguously."""
        _big_file(work_dir, lines=200)
        offset = 1
        windows = 0
        while True:
            r = execute({"action": "read", "arg": "big.py", "offset": offset}, work_dir)
            assert r["ok"] is True
            assert r["output"].startswith(f"[big.py: lines {offset}-")
            windows += 1
            assert windows < 20, "continuation walk is not converging"
            if not r["continuation"]:
                assert f"-200 of 200" in r["output"].splitlines()[0]
                break
            offset = r["continuation"]

    def test_last_window_has_no_continuation(self, work_dir):
        _big_file(work_dir, lines=200)
        result = execute({"action": "read", "arg": "big.py", "offset": 199}, work_dir)
        assert "lines 199-200 of 200" in result["output"]
        assert result["truncated"] is False
        assert result["continuation"] is None

    def test_offset_past_eof(self, work_dir):
        _big_file(work_dir)
        result = execute({"action": "read", "arg": "big.py", "offset": 9999}, work_dir)
        assert result["ok"] is True
        assert result["truncated"] is False
        assert "past end of file (200 lines)" in result["output"]

    def test_limit_honored(self, work_dir):
        _big_file(work_dir)
        result = execute({"action": "read", "arg": "big.py", "limit": 10}, work_dir)
        assert "lines 1-10 of 200" in result["output"]
        assert result["continuation"] == 11

    def test_limit_capped(self, work_dir):
        _big_file(work_dir, lines=READ_LIMIT_MAX + 50)
        result = execute({"action": "read", "arg": "big.py", "limit": 99999}, work_dir)
        assert f"lines 1-{READ_LIMIT_MAX}" in result["output"]

    def test_invalid_offset_and_limit_clamped(self, work_dir):
        _big_file(work_dir)
        for bad in ("abc", -5, 0, None):
            result = execute({"action": "read", "arg": "big.py", "offset": bad,
                              "limit": bad}, work_dir)
            assert result["ok"] is True
            assert result["output"].startswith("[big.py: lines 1-")

    def test_char_cut_on_long_lines(self, work_dir):
        Path(work_dir, "long.txt").write_text("x" * (READ_CHARS * 2))
        result = execute({"action": "read", "arg": "long.txt"}, work_dir)
        assert result["truncated"] is True
        assert f"cut at {READ_CHARS} chars" in result["output"]
        assert len(result["output"]) <= READ_CHARS + 120

    def test_small_file_read_fully(self, work_dir):
        Path(work_dir, "small.txt").write_text("a\nb\nc\n")
        result = execute({"action": "read", "arg": "small.txt"}, work_dir)
        assert result["ok"] is True
        assert result["truncated"] is False
        assert result["continuation"] is None
        assert result["output"].endswith("a\nb\nc")

    def test_empty_file(self, work_dir):
        Path(work_dir, "empty.txt").write_text("")
        result = execute({"action": "read", "arg": "empty.txt"}, work_dir)
        assert result["ok"] is True
        assert "past end of file (0 lines)" in result["output"]

    def test_read_window_fits_observe_budget(self, work_dir):
        """Read output must survive the observation-class history cap."""
        _big_file(work_dir)
        result = execute({"action": "read", "arg": "big.py"}, work_dir)
        assert len(result["output"]) <= OBSERVE_STATE_CHARS


# --- Bounded search ---

class TestSearchAction:
    def test_finds_matches_with_line_numbers(self, work_dir):
        Path(work_dir, "app.py").write_text("import os\n\ndef main():\n    pass\n")
        result = execute({"action": "search", "arg": "def main"}, work_dir)
        assert result["ok"] is True
        assert "[1 matches for 'def main']" in result["output"]
        assert "app.py:3: def main():" in result["output"]

    def test_bounded_matches(self, work_dir):
        Path(work_dir, "many.txt").write_text(
            "".join(f"hit line {i}\n" for i in range(SEARCH_MAX_MATCHES * 2)))
        result = execute({"action": "search", "arg": "hit line"}, work_dir)
        assert result["truncated"] is True
        assert "narrow the pattern" in result["output"]
        assert len(result["output"]) <= SEARCH_MAX_CHARS + 120
        body_lines = [l for l in result["output"].splitlines()
                      if l.startswith("many.txt:")]
        assert len(body_lines) == SEARCH_MAX_MATCHES

    def test_skips_vcs_deps_and_hidden(self, work_dir):
        for d in (".git", "node_modules", "__pycache__"):
            sub = Path(work_dir, d)
            sub.mkdir()
            (sub / "x.py").write_text("needle\n")
        Path(work_dir, ".hidden.py").write_text("needle\n")
        Path(work_dir, "visible.py").write_text("needle\n")
        result = execute({"action": "search", "arg": "needle"}, work_dir)
        assert "visible.py" in result["output"]
        assert ".git" not in result["output"]
        assert "node_modules" not in result["output"]
        assert "__pycache__" not in result["output"]
        assert "hidden" not in result["output"]

    def test_skips_binary_files(self, work_dir):
        Path(work_dir, "bin.dat").write_bytes(b"needle\x00binary")
        result = execute({"action": "search", "arg": "needle"}, work_dir)
        assert "[0 matches" in result["output"]

    def test_path_field_scopes_search(self, work_dir):
        sub = Path(work_dir, "pkg")
        sub.mkdir()
        (sub / "mod.py").write_text("needle\n")
        Path(work_dir, "top.py").write_text("needle\n")
        result = execute({"action": "search", "arg": "needle", "path": "pkg"}, work_dir)
        assert "mod.py:1" in result["output"]
        assert "top.py" not in result["output"]

    def test_missing_directory(self, work_dir):
        result = execute({"action": "search", "arg": "x", "path": "nope/"}, work_dir)
        assert result["ok"] is False
        assert result["error_type"] == "missing_file"

    def test_empty_pattern_rejected(self, work_dir):
        result = execute({"action": "search", "arg": ""}, work_dir)
        assert result["ok"] is False

    def test_no_matches_ok(self, work_dir):
        Path(work_dir, "a.txt").write_text("hello\n")
        result = execute({"action": "search", "arg": "zzz"}, work_dir)
        assert result["ok"] is True
        assert result["truncated"] is False


# --- Bounded repository tree ---

class TestTreeAction:
    def test_lists_entries_with_dir_markers(self, work_dir):
        sub = Path(work_dir, "pkg")
        sub.mkdir()
        (sub / "mod.py").write_text("x")
        Path(work_dir, "main.py").write_text("x")
        result = execute({"action": "tree", "arg": "."}, work_dir)
        assert result["ok"] is True
        assert "pkg/" in result["output"]
        assert "main.py" in result["output"]
        assert "pkg/mod.py" in result["output"]

    def test_skips_hidden_and_vcs(self, work_dir):
        git = Path(work_dir, ".git")
        git.mkdir()
        (git / "config").write_text("x")
        Path(work_dir, ".envrc").write_text("x")
        Path(work_dir, "visible.py").write_text("x")
        result = execute({"action": "tree", "arg": "."}, work_dir)
        assert "visible.py" in result["output"]
        assert ".git" not in result["output"]
        assert ".envrc" not in result["output"]

    def test_bounded_entries(self, work_dir):
        for i in range(TREE_MAX_ENTRIES + 20):
            Path(work_dir, f"f{i:03}.txt").write_text("x")
        result = execute({"action": "tree", "arg": "."}, work_dir)
        assert result["truncated"] is True
        assert f"capped at {TREE_MAX_ENTRIES}" in result["output"]
        assert len(result["output"]) <= TREE_MAX_CHARS + 120

    def test_depth_cap(self, work_dir):
        deep = Path(work_dir)
        for part in ("a", "b", "c", "d", "e"):
            deep = deep / part
            deep.mkdir()
        (deep / "too_deep.txt").write_text("x")
        result = execute({"action": "tree", "arg": "."}, work_dir)
        assert "a/b/c/" in result["output"]
        assert "a/b/c/d/" not in result["output"]
        assert "too_deep.txt" not in result["output"]

    def test_missing_directory(self, work_dir):
        result = execute({"action": "tree", "arg": "nope/"}, work_dir)
        assert result["ok"] is False
        assert result["error_type"] == "missing_file"

    def test_default_arg_is_cwd(self, work_dir):
        Path(work_dir, "here.txt").write_text("x")
        result = execute({"action": "tree"}, work_dir)
        assert result["ok"] is True
        assert "here.txt" in result["output"]


# --- Chunked-write transport and atomic writes ---

class TestChunkedWrite:
    def test_append_creates_file(self, work_dir):
        result = execute({"action": "write", "arg": "app.py",
                          "content": "chunk0\n", "append": True}, work_dir)
        assert result["ok"] is True
        assert result["output"].startswith("Wrote app.py")
        assert Path(work_dir, "app.py").read_text() == "chunk0\n"

    def test_append_assembles_large_file(self, work_dir):
        chunks = [f"# chunk {i}\n" + "x = 1\n" * 20 for i in range(10)]
        for i, chunk in enumerate(chunks):
            result = execute({"action": "write", "arg": "big.py",
                              "content": chunk, "append": i > 0}, work_dir)
            assert result["ok"] is True
        assert Path(work_dir, "big.py").read_text() == "".join(chunks)
        assert "Appended to big.py" in result["output"]
        assert f"total {len(''.join(chunks))}" in result["output"]

    def test_append_dict_content_serialized(self, work_dir):
        execute({"action": "write", "arg": "d.json", "content": {"a": 1}}, work_dir)
        result = execute({"action": "write", "arg": "d.json",
                          "content": [1, 2], "append": True}, work_dir)
        assert result["ok"] is True
        text = Path(work_dir, "d.json").read_text()
        assert text == json.dumps({"a": 1}, indent=2) + json.dumps([1, 2], indent=2)


class TestAtomicWrite:
    def test_write_leaves_no_tmp_files(self, work_dir):
        execute({"action": "write", "arg": "f.txt", "content": "data"}, work_dir)
        assert [f for f in os.listdir(work_dir) if ".tmp" in f] == []

    def test_write_replaces_content(self, work_dir):
        Path(work_dir, "f.txt").write_text("old")
        execute({"action": "write", "arg": "f.txt", "content": "new"}, work_dir)
        assert Path(work_dir, "f.txt").read_text() == "new"

    def test_edit_leaves_no_tmp_files(self, work_dir):
        Path(work_dir, "f.txt").write_text("hello world")
        result = execute({"action": "edit", "arg": "f.txt",
                          "find": "world", "replace": "there"}, work_dir)
        assert result["ok"] is True
        assert Path(work_dir, "f.txt").read_text() == "hello there"
        assert [f for f in os.listdir(work_dir) if ".tmp" in f] == []


# --- Action contract ---

class TestActionContract:
    def test_search_requires_pattern(self):
        assert _validate_action_contract({"action": "search", "arg": "x"}) is True
        assert _validate_action_contract({"action": "search"}) is False
        assert _validate_action_contract({"action": "search", "arg": "  "}) is False

    def test_tree_arg_optional(self):
        assert _validate_action_contract({"action": "tree"}) is True
        assert _validate_action_contract({"action": "tree", "arg": "pkg"}) is True

    def test_read_with_window_fields(self):
        assert _validate_action_contract(
            {"action": "read", "arg": "f.py", "offset": 10, "limit": 20}) is True
        assert _validate_action_contract({"action": "read", "offset": 10}) is False

    def test_write_append_still_requires_content(self):
        assert _validate_action_contract(
            {"action": "write", "arg": "f", "content": "x", "append": True}) is True
        assert _validate_action_contract(
            {"action": "write", "arg": "f", "append": True}) is False


# --- Truncation detection and write-payload retry budget ---

def _length_response(text):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"finish_reason": "length", "message": {"content": text}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    return resp


def _ok_response(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"finish_reason": "stop",
                     "message": {"content": json.dumps(payload)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    return resp


class TestTruncationBudget:
    def test_truncated_write_attempt_bumps_retry_budget(self):
        # reasoning_policy="off" isolates the payload budget from thinking bumps.
        truncated_write = '{"action": "write", "arg": "a.py", "content": "aaaa'
        responses = [_length_response(truncated_write),
                     _ok_response({"action": "write", "arg": "a.py", "content": "x"})]
        with patch("askme.requests.post", side_effect=responses) as mock_post:
            result = askme.ask_llm([{"role": "user", "content": "hi"}],
                                   max_tokens=256, max_retries=1,
                                   reasoning_policy="off")
        assert result["action"] == "write"
        bodies = [c.kwargs["json"] for c in mock_post.call_args_list]
        assert bodies[0]["max_tokens"] == 256
        assert bodies[1]["max_tokens"] == STEP_WRITE_TOKENS

    def test_truncated_non_write_keeps_budget(self):
        # Repair strips the truncated "arg" value, leaving a contract-incomplete
        # shell action — so the retry happens without a payload budget bump.
        truncated_shell = '{"action": "shell", "arg": "gcc main.c -o '
        responses = [_length_response(truncated_shell),
                     _ok_response({"action": "done"})]
        with patch("askme.requests.post", side_effect=responses) as mock_post:
            askme.ask_llm([{"role": "user", "content": "hi"}],
                          max_tokens=256, max_retries=1,
                          reasoning_policy="off")
        bodies = [c.kwargs["json"] for c in mock_post.call_args_list]
        assert bodies[1]["max_tokens"] == 256

    def test_finish_reason_recorded_in_tokens_event(self, tmp_path):
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            with patch("askme.requests.post",
                       return_value=_ok_response({"action": "done"})):
                askme.ask_llm([{"role": "user", "content": "hi"}], max_retries=0)
        finally:
            askme.RUN_LOG_PATH = old
        events = [json.loads(l) for l in log_path.read_text().splitlines()]
        tokens = [e for e in events if e["event"] == "tokens"]
        assert tokens and tokens[0]["finish_reason"] == "stop"


# --- Loop-level: typed repeated-read recovery and append stuck guard ---

class TestReadDupGuardOffsets:
    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_different_offsets_are_not_duplicates(self, mock_llm, mock_replan,
                                                  capsys, tmp_path):
        """Ranged navigation of one file must not trip the duplicate-read guard."""
        _big_file(str(tmp_path), lines=200)
        mock_llm.side_effect = [
            {"tasks": ["inspect big.py"]},
            {"action": "read", "arg": "big.py", "offset": 1},
            {"action": "read", "arg": "big.py", "offset": 61},
            {"action": "done"},
        ]
        result = _run_loop("inspect big.py", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=5)
        out = capsys.readouterr().out
        assert result["status"] == "complete"
        assert "skip (duplicate read)" not in out
        reads = [s for s in result["state"]["all_steps"] if s["action"] == "read"]
        assert len(reads) == 2
        assert reads[0]["_read_key"] == ("big.py", 1)
        assert reads[1]["_read_key"] == ("big.py", 61)

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_same_range_skip_points_at_continuation(self, mock_llm, mock_replan,
                                                    capsys, tmp_path):
        """Re-reading the same truncated range gets a typed continuation hint."""
        _big_file(str(tmp_path), lines=200)
        mock_llm.side_effect = [
            {"tasks": ["inspect big.py"]},
            {"action": "read", "arg": "big.py", "offset": 1},
            {"action": "read", "arg": "big.py", "offset": 1},
            {"action": "done"},
        ]
        captured = []
        original_get_step = askme.get_step

        def spy_get_step(task, state, **kwargs):
            captured.extend(s.get("output", "") for s in state.get("last_steps", []))
            return original_get_step(task, state, **kwargs)

        with patch("askme.get_step", side_effect=spy_get_step):
            result = _run_loop("inspect big.py", str(tmp_path),
                               max_replans=1, max_tasks=1, max_steps=5)
        out = capsys.readouterr().out
        assert result["status"] == "complete"
        assert "skip (duplicate read)" in out
        assert any("Continue with offset=61" in o for o in captured)


class TestAppendStuckGuard:
    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_same_chunk_appended_twice_auto_fails(self, mock_llm, mock_replan,
                                                  capsys, tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["build big.py in chunks"]},
            {"action": "write", "arg": "big.py", "content": "x = 1\n", "append": True},
            {"action": "write", "arg": "big.py", "content": "x = 1\n", "append": True},
        ]
        result = _run_loop("build big.py in chunks", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=5)
        out = capsys.readouterr().out
        assert result["status"] == "exhausted"
        assert "auto-fail (same chunk appended twice" in out
        # The chunk must not have been applied twice
        assert Path(tmp_path, "big.py").read_text() == "x = 1\n"

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_distinct_chunks_not_stuck(self, mock_llm, mock_replan, capsys, tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["build big.py in chunks"]},
            {"action": "write", "arg": "big.py", "content": "a = 1\n", "append": True},
            {"action": "write", "arg": "big.py", "content": "b = 2\n", "append": True},
            {"action": "done"},
        ]
        result = _run_loop("build big.py in chunks", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=5)
        assert result["status"] == "complete"
        assert Path(tmp_path, "big.py").read_text() == "a = 1\nb = 2\n"


# --- Curated stateful replan state ---

class TestPlannerStateCurated:
    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_replan_sees_digest_not_file_contents(self, mock_llm, mock_replan,
                                                  tmp_path):
        """Full replan gets a step digest; raw write payloads never reach it."""
        marker = "SECRET_PAYLOAD_" + "z" * 500
        plan_msgs = []
        call_idx = {"n": 0}

        def tracking_llm(messages, **kwargs):
            call_idx["n"] += 1
            n = call_idx["n"]
            if n == 1:
                return {"tasks": ["write and fail"]}
            if n == 2:
                return {"action": "write", "arg": "big.py", "content": marker}
            if n == 3:
                return {"action": "fail", "reasoning": "boom"}
            if n == 4:
                plan_msgs.append(messages[-1]["content"])
                return {"tasks": ["finish"]}
            return {"action": "done"}

        mock_llm.side_effect = tracking_llm
        result = _run_loop("write file", str(tmp_path),
                           max_replans=2, max_tasks=1, max_steps=5)
        assert result["status"] == "complete"
        assert len(plan_msgs) == 1
        msg = plan_msgs[0]
        assert marker not in msg, "raw write content leaked into planner state"
        assert "recent_steps" in msg, "planner should see a step digest"
        assert "boom" in msg
        for key in ("completed_tasks", "environment", "policy"):
            assert key in msg


# --- Read totals + content-hash metadata (issue #7 continuation data) ---

class TestReadMetadata:
    def test_read_returns_totals_and_hash(self, work_dir):
        p = _big_file(work_dir, lines=100)
        text = p.read_text()
        r = execute({"action": "read", "arg": "big.py"}, work_dir)
        assert r["total_lines"] == 100
        assert r["total_bytes"] == len(text.encode())
        assert r["sha256"] == hashlib.sha256(text.encode()).hexdigest()[:12]

    def test_hash_stable_across_windows(self, work_dir):
        _big_file(work_dir, lines=200)
        r1 = execute({"action": "read", "arg": "big.py", "offset": 1}, work_dir)
        r2 = execute({"action": "read", "arg": "big.py", "offset": 61}, work_dir)
        assert r1["sha256"] == r2["sha256"]
        assert r1["total_lines"] == r2["total_lines"] == 200

    def test_past_eof_still_reports_totals(self, work_dir):
        _big_file(work_dir, lines=10)
        r = execute({"action": "read", "arg": "big.py", "offset": 99}, work_dir)
        assert r["ok"] is True
        assert r["total_lines"] == 10
        assert "sha256" in r

    def test_metadata_not_in_model_output(self, work_dir):
        # Header stays compact: hash/bytes are structured fields for the
        # harness, not prompt text the executor pays tokens for.
        _big_file(work_dir, lines=10)
        r = execute({"action": "read", "arg": "big.py"}, work_dir)
        assert r["sha256"] not in r["output"]

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_read_step_log_is_hash_linked(self, mock_llm, mock_replan, tmp_path):
        _big_file(str(tmp_path), lines=200)
        mock_llm.side_effect = [
            {"tasks": ["inspect big.py"]},
            {"action": "read", "arg": "big.py"},
            {"action": "done"},
        ]
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("inspect big.py", str(tmp_path),
                               max_replans=1, max_tasks=1, max_steps=5)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "complete"
        events = [json.loads(l) for l in log_path.read_text().splitlines()]
        reads = [e for e in events if e["event"] == "step" and e["action"] == "read"]
        assert reads
        assert reads[0]["sha256"]
        assert reads[0]["total_lines"] == 200
        assert reads[0]["continuation"] == 61


# --- Typed parse failures: malformed_action / response_truncated ---

def _stop_response(text):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"finish_reason": "stop", "message": {"content": text}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    return resp


class TestTypedParseFailures:
    def test_truncated_unrepairable_output_is_typed(self):
        # Cut mid-key so mechanical repair cannot rebuild a valid contract.
        bad = _length_response('{"action": "write", "ar')
        with patch("askme.requests.post", return_value=bad):
            with pytest.raises(json.JSONDecodeError) as exc:
                askme.ask_llm([{"role": "user", "content": "hi"}],
                              max_retries=0, reasoning_policy="off")
        assert exc.value.malformed_action is True
        assert exc.value.response_truncated is True

    def test_malformed_stop_output_not_marked_truncated(self):
        bad = _stop_response("garbage output, no json here")
        with patch("askme.requests.post", return_value=bad):
            with pytest.raises(json.JSONDecodeError) as exc:
                askme.ask_llm([{"role": "user", "content": "hi"}],
                              max_retries=0, reasoning_policy="off")
        assert exc.value.malformed_action is True
        assert exc.value.response_truncated is False

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_loop_records_typed_parse_error(self, mock_llm, mock_replan, tmp_path):
        err = json.JSONDecodeError("boom", "{", 0)
        err.malformed_action = True
        err.response_truncated = True
        mock_llm.side_effect = [{"tasks": ["do a thing"]}, err]
        result = _run_loop("do a thing", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=3)
        assert result["status"] == "exhausted"
        errors = result["state"]["errors"]
        assert errors and errors[0].startswith("[response_truncated]")
        # The typed prefix must survive the planner-facing summary.
        summary = askme.summarize_errors(errors)
        assert summary[0].startswith("[response_truncated]")

    def test_extract_error_type_knows_new_types(self):
        assert askme._extract_error_type("[malformed_action] x")[0] == "malformed_action"
        assert askme._extract_error_type("[response_truncated] x")[0] == "response_truncated"


# --- Selected vs executed step accounting (step_skipped) ---

class TestStepAccounting:
    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_selected_executed_skipped_counters(self, mock_llm, mock_replan,
                                                tmp_path):
        _big_file(str(tmp_path), lines=200)
        mock_llm.side_effect = [
            {"tasks": ["inspect big.py"]},
            {"action": "read", "arg": "big.py"},
            {"action": "read", "arg": "big.py"},   # duplicate -> skipped
            {"action": "done"},
        ]
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("inspect big.py", str(tmp_path),
                               max_replans=1, max_tasks=1, max_steps=5)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "complete"
        st = result["state"]
        assert st["selected_steps"] == 3
        assert st["executed_steps"] == 1
        assert st["skipped_steps"] == 1
        events = [json.loads(l) for l in log_path.read_text().splitlines()]
        skips = [e for e in events if e["event"] == "step_skipped"]
        assert len(skips) == 1
        assert skips[0]["reason"] == "duplicate_read"
        run_end = [e for e in events if e["event"] == "run_end"][0]
        assert run_end["steps"] == {"selected": 3, "executed": 1, "skipped": 1}

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_stuck_read_recorded_as_skipped(self, mock_llm, mock_replan,
                                            tmp_path):
        _big_file(str(tmp_path), lines=200)
        mock_llm.side_effect = [
            {"tasks": ["inspect big.py"]},
            {"action": "read", "arg": "big.py"},
            {"action": "read", "arg": "big.py"},
            {"action": "read", "arg": "big.py"},
        ]
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("inspect big.py", str(tmp_path),
                               max_replans=1, max_tasks=1, max_steps=5)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "exhausted"
        st = result["state"]
        assert st["selected_steps"] == 3
        assert st["executed_steps"] == 1
        assert st["skipped_steps"] == 2
        events = [json.loads(l) for l in log_path.read_text().splitlines()]
        reasons = [e["reason"] for e in events if e["event"] == "step_skipped"]
        assert reasons == ["duplicate_read", "stuck_read"]


# --- End-to-end synthetic repo: symbol beyond first chunk, patch > 512 tokens ---

class TestEndToEndFeatureScale:
    """Issue #7 acceptance boundary: the agent must be able to navigate to a
    symbol beyond the first read window and land a patch larger than one
    512-token action payload, with the patch reaching real execution."""

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_symbol_beyond_first_chunk_and_multi_kb_patch(self, mock_llm,
                                                          mock_replan,
                                                          tmp_path):
        filler = "".join(f"def f{i}():\n    return {i}\n\n" for i in range(120))
        original = filler + "def target_symbol():\n    return 'original'\n"
        Path(tmp_path, "module.py").write_text(original)
        symbol_line = len(filler.splitlines()) + 1
        assert symbol_line > 60  # beyond the first read window

        patched_tail = ("def target_symbol():\n    return 'patched'\n\n"
                        + "".join(f"def g{i}():\n    return {i * 2}\n\n"
                                  for i in range(40)))
        new_content = filler + patched_tail
        assert len(new_content) > 512 * 4  # patch exceeds a 512-token payload
        third = len(new_content) // 3
        chunks = [new_content[:third], new_content[third:2 * third],
                  new_content[2 * third:]]

        mock_llm.side_effect = [
            {"tasks": ["patch target_symbol in module.py and verify it"]},
            {"action": "read", "arg": "module.py"},
            {"action": "search", "arg": "def target_symbol"},
            {"action": "read", "arg": "module.py", "offset": symbol_line},
            {"action": "write", "arg": "module.py", "content": chunks[0]},
            {"action": "write", "arg": "module.py", "content": chunks[1],
             "append": True},
            {"action": "write", "arg": "module.py", "content": chunks[2],
             "append": True},
            {"action": "shell",
             "arg": "python3 -c \"import module; assert module.target_symbol() == 'patched'\""},
            {"action": "done"},
        ]
        result = _run_loop("patch target_symbol in module.py and verify it",
                           str(tmp_path), max_replans=1, max_tasks=1,
                           max_steps=10)
        assert result["status"] == "complete"
        # Byte-exact round trip: no truncation, no partial application.
        assert Path(tmp_path, "module.py").read_text() == new_content

        st = result["state"]
        steps = st["all_steps"]
        reads = [s for s in steps if s["action"] == "read"]
        # The symbol was beyond the first window and reachable via navigation.
        assert "target_symbol" not in reads[0]["output"]
        assert "def target_symbol" in reads[1]["output"]
        search_out = next(s for s in steps if s["action"] == "search")["output"]
        assert f"module.py:{symbol_line}" in search_out
        # Every dispatched action succeeded, including the real subprocess
        # that imported the patched module and checked its behavior.
        assert all(s["ok"] for s in steps)
        assert st["selected_steps"] == 8
        assert st["executed_steps"] == 7
        assert st["skipped_steps"] == 0
        assert not st["errors"]
