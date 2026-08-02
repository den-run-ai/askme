"""Deterministic tests for the app-development action surface (issues #7, #30).

No LLM calls: these pin the navigable read window, bounded search/tree,
chunked-write transport, truncation detection, and curated replan state.
"""
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import askme
from askme import (
    OBSERVE_STATE_CHARS,
    READ_CHARS,
    READ_LIMIT_MAX,
    SEARCH_MAX_FILES,
    SEARCH_MAX_MATCHES,
    STEP_WRITE_TOKENS,
    TREE_MAX_ENTRIES,
    _run_loop,
    _validate_action_contract,
    execute,
)


def _big_file(work_dir, name="big.py", lines=200):
    p = Path(work_dir, name)
    p.write_text("".join(f"def f{i}(): return {i}\n" for i in range(lines)))
    return p


def _walk_read(work_dir, name, **initial):
    """Follow action-ready continuations and return exact content plus pages."""
    action = {"action": "read", "arg": name, **initial}
    pages = []
    while True:
        result = execute(action, work_dir)
        assert result["ok"] is True
        pages.append(result)
        continuation = result["continuation"]
        if continuation is None:
            return "".join(page["content"] for page in pages), pages
        action = {"action": "read", "arg": name, **continuation}
        assert len(pages) < 100, "continuation walk is not converging"


def _symlinked_target_dirs(tmp_path):
    work = tmp_path / "work"
    real = tmp_path / "real"
    work.mkdir()
    real.mkdir()
    alias = work / "alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")
    return work, real, alias


# --- Ranged reads with continuation metadata ---

class TestRangedRead:
    def test_window_header_and_continuation(self, work_dir):
        _big_file(work_dir)
        result = execute({"action": "read", "arg": "big.py"}, work_dir)
        assert result["ok"] is True
        assert result["output"].startswith("[big.py: lines 1-")
        assert " of 200" in result["output"]
        assert result["truncated"] is True
        assert result["continuation"]["cursor"] == READ_CHARS
        assert "continue: cursor=1200" in result["output"]
        assert result["continuation"]["sha256"] == result["sha256"]

    def test_continuation_navigation_covers_whole_file(self, work_dir):
        """Walking continuation cursors must tile the whole file contiguously."""
        p = _big_file(work_dir, lines=200)
        reconstructed, pages = _walk_read(work_dir, "big.py")
        assert reconstructed == p.read_text()
        assert len(pages) > 1
        assert pages[-1]["continuation"] is None

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
        assert result["continuation"]["offset"] == 11
        assert result["continuation"]["limit"] == 10

    def test_limit_capped(self, work_dir):
        Path(work_dir, "short.txt").write_text("x\n" * (READ_LIMIT_MAX + 50))
        result = execute({"action": "read", "arg": "short.txt", "limit": 99999}, work_dir)
        assert f"lines 1-{READ_LIMIT_MAX}" in result["output"]
        assert result["continuation"]["offset"] == READ_LIMIT_MAX + 1
        assert result["continuation"]["limit"] == READ_LIMIT_MAX

    def test_invalid_offset_and_limit_clamped(self, work_dir):
        _big_file(work_dir)
        for bad in ("abc", -5, 0, None):
            result = execute({"action": "read", "arg": "big.py", "offset": bad,
                              "limit": bad}, work_dir)
            assert result["ok"] is True
            assert result["output"].startswith("[big.py: lines 1-")

    def test_long_single_line_reconstructs_exactly(self, work_dir):
        text = "x" * (READ_CHARS * 2 + 17)
        Path(work_dir, "long.txt").write_text(text)
        reconstructed, pages = _walk_read(work_dir, "long.txt")
        assert reconstructed == text
        assert len(pages) == 3
        assert pages[0]["continuation"]["cursor"] == READ_CHARS
        assert all(len(page["content"]) <= READ_CHARS for page in pages)
        assert f"cut at {READ_CHARS} chars" in pages[0]["output"]

    def test_wide_multiline_pages_reconstruct_to_eof(self, work_dir):
        text = "".join(f"line {i:03}: {'x' * 40}\n" for i in range(180))
        Path(work_dir, "wide.txt").write_text(text)
        reconstructed, pages = _walk_read(work_dir, "wide.txt")
        assert reconstructed == text
        assert len(pages) >= 3
        assert pages[-1]["continuation"] is None
        cursors = [p["continuation"]["cursor"] for p in pages[:-1]]
        assert cursors == sorted(set(cursors))

    def test_nondefault_limit_is_preserved_through_reconstruction(self,
                                                                  work_dir):
        lines = [f"line {i}\n" for i in range(25)]
        text = "".join(lines)
        Path(work_dir, "limited.txt").write_text(text)
        reconstructed, pages = _walk_read(work_dir, "limited.txt",
                                          offset=3, limit=1)
        assert reconstructed == "".join(lines[2:])
        assert len(pages) == 23
        assert all(page["continuation"]["limit"] == 1
                   for page in pages[:-1])

    def test_unicode_cursor_counts_code_points_and_preserves_newlines(self, work_dir):
        text = ("🙂é漢" * 500) + "\r\n" + ("é🙂" * 700) + "\r\ntail🙂\r\n"
        raw = text.encode("utf-8")
        Path(work_dir, "unicode.txt").write_bytes(raw)
        reconstructed, pages = _walk_read(work_dir, "unicode.txt")
        assert reconstructed == text
        assert reconstructed.encode("utf-8") == raw
        assert pages[0]["continuation"]["cursor"] == READ_CHARS
        assert pages[0]["total_chars"] == len(text)
        assert pages[0]["total_bytes"] == len(raw)
        assert pages[0]["continuation"]["cursor"] != len(
            pages[0]["content"].encode("utf-8"))

    def test_stale_hash_rejects_continuation(self, work_dir):
        Path(work_dir, "changing.txt").write_text("x" * (READ_CHARS + 10))
        first = execute({"action": "read", "arg": "changing.txt"}, work_dir)
        Path(work_dir, "changing.txt").write_text("y" * (READ_CHARS + 10))
        result = execute({"action": "read", "arg": "changing.txt",
                          **first["continuation"]}, work_dir)
        assert result["ok"] is False
        assert result["error_type"] == "stale_read_cursor"

    def test_cursor_requires_source_hash(self, work_dir):
        Path(work_dir, "data.txt").write_text("abc")
        result = execute({"action": "read", "arg": "data.txt", "cursor": 1,
                          "limit": 60}, work_dir)
        assert result["ok"] is False
        assert result["error_type"] == "read_cursor_hash_required"

    @pytest.mark.parametrize("limit", [None, 0, READ_LIMIT_MAX + 1,
                                        True, 1.5, "1.0"])
    def test_cursor_requires_valid_continuation_limit(self, work_dir, limit):
        Path(work_dir, "data.txt").write_text("abc")
        action = {"action": "read", "arg": "data.txt", "cursor": 1,
                  "sha256": hashlib.sha256(b"abc").hexdigest()[:12]}
        if limit is not None:
            action["limit"] = limit
        result = execute(action, work_dir)
        assert result["ok"] is False
        assert result["error_type"] == "invalid_read_limit"

    @pytest.mark.parametrize("cursor", [None, "", "oops", -1,
                                         True, 1.9, "1.0"])
    def test_invalid_cursor_is_rejected(self, work_dir, cursor):
        Path(work_dir, "data.txt").write_text("abc")
        result = execute({"action": "read", "arg": "data.txt",
                          "cursor": cursor, "sha256": "deadbeef"}, work_dir)
        assert result["ok"] is False
        assert result["error_type"] == "invalid_read_cursor"

    @pytest.mark.parametrize("cursor", [3, 4])
    def test_cursor_at_or_beyond_eof_is_rejected(self, work_dir, cursor):
        Path(work_dir, "data.txt").write_text("abc")
        result = execute({
            "action": "read", "arg": "data.txt", "cursor": cursor,
            "limit": 60,
            "sha256": hashlib.sha256(b"abc").hexdigest()[:12],
        }, work_dir)
        assert result["ok"] is False
        assert result["error_type"] == "invalid_read_cursor"
        assert result["content"] == ""
        assert result["continuation"] is None

    def test_small_file_read_fully(self, work_dir):
        Path(work_dir, "small.txt").write_text("a\nb\nc\n")
        result = execute({"action": "read", "arg": "small.txt"}, work_dir)
        assert result["ok"] is True
        assert result["truncated"] is False
        assert result["continuation"] is None
        assert result["content"] == "a\nb\nc\n"
        assert result["output"].endswith("a\nb\nc\n")

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

    def test_long_filename_pages_fit_history_without_data_loss(self, work_dir):
        name = "n" * 240 + ".txt"
        text = "x" * (READ_CHARS * 2)
        Path(work_dir, name).write_text(text)
        reconstructed, pages = _walk_read(work_dir, name)
        assert reconstructed == text
        assert all(len(page["output"]) <= OBSERVE_STATE_CHARS for page in pages)
        assert pages[0]["continuation"]["cursor"] < READ_CHARS


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
        assert "narrow the pattern/path" in result["output"]
        assert "matches" in result["truncation_reasons"]
        assert len(result["output"]) <= OBSERVE_STATE_CHARS
        body_lines = [line for line in result["output"].splitlines()
                      if line.startswith("many.txt:")]
        assert len(body_lines) == SEARCH_MAX_MATCHES

    def test_exact_match_cap_is_complete(self, work_dir):
        Path(work_dir, "exact.txt").write_text(
            "".join(f"needle {i}\n" for i in range(SEARCH_MAX_MATCHES)))
        result = execute({"action": "search", "arg": "needle"}, work_dir)
        assert result["truncated"] is False
        assert result["truncation_reasons"] == []

    def test_long_match_snippet_is_marked_incomplete(self, work_dir):
        Path(work_dir, "long.txt").write_text("needle " + "x" * 500 + "\n")
        result = execute({"action": "search", "arg": "needle"}, work_dir)
        assert result["truncated"] is True
        assert "snippets" in result["truncation_reasons"]
        assert "…" in result["output"]

    def test_file_scan_cap_is_marked_incomplete(self, work_dir):
        for i in range(SEARCH_MAX_FILES + 1):
            content = "needle\n" if i == SEARCH_MAX_FILES else "hay\n"
            Path(work_dir, f"f{i:03}.txt").write_text(content)
        result = execute({"action": "search", "arg": "needle"}, work_dir)
        assert result["truncated"] is True
        assert "files" in result["truncation_reasons"]
        assert "[0+ matches" in result["output"]

    def test_search_packs_only_complete_records(self, work_dir):
        for i in range(SEARCH_MAX_MATCHES):
            Path(work_dir, f"{'p' * 80}{i:02}.txt").write_text(
                f"needle {'x' * 90}\n")
        result = execute({"action": "search", "arg": "needle"}, work_dir)
        assert len(result["output"]) <= OBSERVE_STATE_CHARS
        assert "chars" in result["truncation_reasons"]
        for line in result["output"].splitlines()[1:]:
            assert line.endswith("x" * 90)

    def test_near_boundary_record_is_not_dropped_by_reason_reserve(self,
                                                                   work_dir):
        for i in range(7):
            Path(work_dir, f"{'n' * 100}{i}.txt").write_text(
                f"needle {'x' * 90}\n")
        result = execute({"action": "search", "arg": "needle"}, work_dir)
        assert result["truncated"] is False
        assert result["truncation_reasons"] == []
        assert len(result["output"].splitlines()[1:]) == 7
        assert len(result["output"]) <= OBSERVE_STATE_CHARS

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

    def test_unreadable_candidate_is_marked_incomplete(self, work_dir):
        broken = Path(work_dir, "broken.txt")
        try:
            broken.symlink_to(Path(work_dir, "missing.txt"))
        except (OSError, NotImplementedError):
            pytest.skip("file symlinks are unavailable")
        result = execute({"action": "search", "arg": "needle"}, work_dir)
        assert result["truncated"] is True
        assert "unreadable" in result["truncation_reasons"]

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
        assert "entries" in result["truncation_reasons"]
        assert len(result["output"]) <= OBSERVE_STATE_CHARS

    def test_exact_entry_cap_is_complete(self, work_dir):
        for i in range(TREE_MAX_ENTRIES):
            Path(work_dir, f"f{i:03}.txt").write_text("x")
        result = execute({"action": "tree", "arg": "."}, work_dir)
        assert result["truncated"] is False
        assert result["truncation_reasons"] == []

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
        assert result["truncated"] is True
        assert "depth" in result["truncation_reasons"]

    def test_tree_packs_only_complete_paths(self, work_dir):
        names = []
        for i in range(20):
            name = f"{'n' * 120}{i:02}.txt"
            names.append(name)
            Path(work_dir, name).write_text("x")
        result = execute({"action": "tree", "arg": "."}, work_dir)
        assert len(result["output"]) <= OBSERVE_STATE_CHARS
        assert "chars" in result["truncation_reasons"]
        shown = result["output"].splitlines()[1:]
        assert shown
        assert all(line in names for line in shown)

    def test_missing_directory(self, work_dir):
        result = execute({"action": "tree", "arg": "nope/"}, work_dir)
        assert result["ok"] is False
        assert result["error_type"] == "missing_file"

    def test_default_arg_is_cwd(self, work_dir):
        Path(work_dir, "here.txt").write_text("x")
        result = execute({"action": "tree"}, work_dir)
        assert result["ok"] is True
        assert "here.txt" in result["output"]

    def test_walk_error_is_marked_incomplete(self, work_dir):
        def failing_walk(root, onerror=None):
            if onerror:
                onerror(PermissionError("denied"))
            return iter(())

        with patch("askme.os.walk", side_effect=failing_walk):
            result = execute({"action": "tree", "arg": "."}, work_dir)
        assert result["truncated"] is True
        assert "walk_errors" in result["truncation_reasons"]


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

    def test_read_cursor_contract_requires_valid_cursor_and_hash(self):
        assert _validate_action_contract(
            {"action": "read", "arg": "f.py", "cursor": 10,
             "limit": 60, "sha256": "abc123"}) is True
        assert _validate_action_contract(
            {"action": "read", "arg": "f.py", "cursor": "10",
             "limit": "60", "sha256": "abc123"}) is True
        assert _validate_action_contract(
            {"action": "read", "arg": "f.py", "cursor": "oops",
             "limit": 60, "sha256": "abc123"}) is False
        assert _validate_action_contract(
            {"action": "read", "arg": "f.py", "cursor": -1,
             "limit": 60, "sha256": "abc123"}) is False
        assert _validate_action_contract(
            {"action": "read", "arg": "f.py", "cursor": True,
             "limit": 60, "sha256": "abc123"}) is False
        assert _validate_action_contract(
            {"action": "read", "arg": "f.py", "cursor": 1.9,
             "limit": 60, "sha256": "abc123"}) is False
        assert _validate_action_contract(
            {"action": "read", "arg": "f.py", "cursor": 10,
             "limit": 60}) is False
        assert _validate_action_contract(
            {"action": "read", "arg": "f.py", "cursor": 10,
             "sha256": "abc123"}) is False
        for invalid_limit in (0, READ_LIMIT_MAX + 1, True, 1.5, "1.0"):
            assert _validate_action_contract(
                {"action": "read", "arg": "f.py", "cursor": 10,
                 "limit": invalid_limit, "sha256": "abc123"}) is False

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
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
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
        assert reads[0]["_read_key"] == ("big.py", "lines", 1, 60)
        assert reads[1]["_read_key"] == ("big.py", "lines", 61, 60)

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_different_limits_are_not_duplicates(self, mock_llm, mock_replan,
                                                  capsys, tmp_path):
        _big_file(str(tmp_path), lines=200)
        mock_llm.side_effect = [
            {"tasks": ["inspect big.py"]},
            {"action": "read", "arg": "big.py", "offset": 1, "limit": 5},
            {"action": "read", "arg": "big.py", "offset": 1, "limit": 10},
            {"action": "done"},
        ]
        result = _run_loop("inspect big.py", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=5)
        assert result["status"] == "complete"
        assert "skip (duplicate read)" not in capsys.readouterr().out

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
        assert any("Continue with cursor=1200, limit=60, sha256=" in o
                   for o in captured)

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_cursor_pages_in_same_line_are_not_duplicates(self, mock_llm,
                                                           mock_replan,
                                                           capsys, tmp_path):
        Path(tmp_path, "long.txt").write_text("x" * (READ_CHARS * 3))
        first = execute({"action": "read", "arg": "long.txt"}, tmp_path)
        continuation = first["continuation"]
        mock_llm.side_effect = [
            {"tasks": ["inspect long.txt"]},
            {"action": "read", "arg": "long.txt"},
            {"action": "read", "arg": "long.txt", **continuation},
            {"action": "done"},
        ]
        result = _run_loop("inspect long.txt", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=5)
        assert result["status"] == "complete"
        assert "skip (duplicate read)" not in capsys.readouterr().out

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_cursor_reads_with_different_limits_are_not_duplicates(
            self, mock_llm, mock_replan, capsys, tmp_path):
        p = _big_file(str(tmp_path), lines=200)
        source_hash = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
        mock_llm.side_effect = [
            {"tasks": ["inspect big.py"]},
            {"action": "read", "arg": "big.py", "cursor": 0,
             "limit": 1, "sha256": source_hash},
            {"action": "read", "arg": "big.py", "cursor": 0,
             "limit": 10, "sha256": source_hash},
            {"action": "done"},
        ]
        result = _run_loop("inspect big.py", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=5)
        assert result["status"] == "complete"
        assert "skip (duplicate read)" not in capsys.readouterr().out


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
    def test_same_chunk_via_path_alias_auto_fails(self, mock_llm, mock_replan,
                                                  capsys, tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["build big.py in chunks"]},
            {"action": "write", "arg": "big.py", "content": "x = 1\n",
             "append": True},
            {"action": "write", "arg": "./big.py", "content": "x = 1\n",
             "append": True},
            {"action": "done"},
        ]
        result = _run_loop("build big.py in chunks", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=5)
        out = capsys.readouterr().out
        assert result["status"] == "exhausted"
        assert "auto-fail (same chunk appended twice" in out
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
        assert r["total_chars"] == len(text)
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
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        reads = [e for e in events if e["event"] == "step" and e["action"] == "read"]
        assert reads
        assert reads[0]["sha256"]
        assert reads[0]["total_lines"] == 200
        assert reads[0]["total_chars"] > READ_CHARS
        assert reads[0]["continuation"]["cursor"] == READ_CHARS


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

    @pytest.mark.parametrize("error_type", [
        "invalid_read_cursor", "invalid_read_limit",
        "read_cursor_hash_required", "stale_read_cursor",
    ])
    def test_read_cursor_errors_survive_summary_without_thinking(self,
                                                                 error_type):
        error = f"[{error_type}] read app.py: bad continuation"
        assert askme._extract_error_type(error)[0] == error_type
        assert askme.summarize_errors([error])[0].startswith(f"[{error_type}]")
        assert error_type in askme._NO_THINK_ERRORS
        assert error_type in askme._RECOVERY_HINTS


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
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
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
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
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


# --- Revision 3 (issue #15): sentinel-framed content transport ---

class TestSentinelSplit:
    def test_no_block(self):
        assert askme._split_content_block('{"action":"done"}') == (
            '{"action":"done"}', None, False)

    def test_closed_block(self):
        text = 'HDR\n<<<CONTENT\na\nb\nCONTENT>>>'
        assert askme._split_content_block(text) == ("HDR", "a\nb", True)

    def test_unclosed_block(self):
        text = 'HDR\n<<<CONTENT\na\nb'
        assert askme._split_content_block(text) == ("HDR", "a\nb", False)

    def test_prompt_advertises_sentinels(self):
        assert askme.CONTENT_OPEN in askme.SYSTEM_STEP
        assert askme.CONTENT_CLOSE in askme.SYSTEM_STEP


class TestSentinelTransport:
    def test_sentinel_write_parses_without_escaping(self):
        code = 'import numpy as np\n\ndef seed(n):\n    return {"x": n}'
        text = ('{"action":"write","arg":"impl.py","reasoning":"impl"}\n'
                '<<<CONTENT\n' + code + '\nCONTENT>>>')
        with patch("askme.requests.post", return_value=_stop_response(text)):
            result = askme.ask_llm([{"role": "user", "content": "hi"}],
                                   max_retries=0, reasoning_policy="off")
        assert result["action"] == "write"
        assert result["content"] == code
        assert "content_truncated" not in result

    def test_block_overrides_header_content(self):
        text = ('{"action":"write","arg":"a.py","content":"stub"}\n'
                '<<<CONTENT\nreal\nCONTENT>>>')
        with patch("askme.requests.post", return_value=_stop_response(text)):
            result = askme.ask_llm([{"role": "user", "content": "hi"}],
                                   max_retries=0, reasoning_policy="off")
        assert result["content"] == "real"

    def test_unclosed_block_at_length_marks_truncated(self):
        text = ('{"action":"write","arg":"a.py"}\n'
                '<<<CONTENT\nline1\nline2\nline3 par')
        with patch("askme.requests.post", return_value=_length_response(text)):
            result = askme.ask_llm([{"role": "user", "content": "hi"}],
                                   max_retries=0, reasoning_policy="off")
        assert result["content"] == "line1\nline2\nline3 par"
        assert result["content_truncated"] is True

    def test_unclosed_block_at_stop_is_complete(self):
        # A model that forgot the closing sentinel but stopped on its own
        # emitted everything it meant to — accept the content as complete.
        text = '{"action":"write","arg":"a.py"}\n<<<CONTENT\nline1\nline2'
        with patch("askme.requests.post", return_value=_stop_response(text)):
            result = askme.ask_llm([{"role": "user", "content": "hi"}],
                                   max_retries=0, reasoning_policy="off")
        assert result["content"] == "line1\nline2"
        assert "content_truncated" not in result

    def test_unclosed_block_at_length_keeps_last_complete_line(self):
        # Cutoff on a line boundary: the response's trailing newline is the
        # proof the last line is complete — it must survive the strip chain
        # so the run loop's partial-line trim does not drop the line.
        text = '{"action":"write","arg":"a.py"}\n<<<CONTENT\nline1\nline2\n'
        with patch("askme.requests.post", return_value=_length_response(text)):
            result = askme.ask_llm([{"role": "user", "content": "hi"}],
                                   max_retries=0, reasoning_policy="off")
        assert result["content"] == "line1\nline2\n"
        assert result["content_truncated"] is True

    def test_embedded_close_line_stays_content(self):
        # Content lines that resemble the terminator (docs/fixtures about this
        # protocol) must not end the block when a real terminator follows.
        code = 'print("demo")\nCONTENT>>>\nprint("after")'
        text = ('{"action":"write","arg":"a.py"}\n'
                '<<<CONTENT\n' + code + '\nCONTENT>>>')
        with patch("askme.requests.post", return_value=_stop_response(text)):
            result = askme.ask_llm([{"role": "user", "content": "hi"}],
                                   max_retries=0, reasoning_policy="off")
        assert result["content"] == code

    def test_indented_close_is_content_not_terminator(self):
        text = ('{"action":"write","arg":"a.py"}\n'
                '<<<CONTENT\nexample:\n    CONTENT>>>')
        with patch("askme.requests.post", return_value=_stop_response(text)):
            result = askme.ask_llm([{"role": "user", "content": "hi"}],
                                   max_retries=0, reasoning_policy="off")
        assert result["content"] == "example:\n    CONTENT>>>"

    def test_empty_block_at_length_retries_with_payload_budget(self):
        cut = '{"action":"write","arg":"a.py"}\n<<<CONTENT\n'
        good = ('{"action":"write","arg":"a.py"}\n'
                '<<<CONTENT\nx = 1\nCONTENT>>>')
        responses = [_length_response(cut), _stop_response(good)]
        with patch("askme.requests.post", side_effect=responses) as mock_post:
            result = askme.ask_llm([{"role": "user", "content": "hi"}],
                                   max_tokens=256, max_retries=1,
                                   reasoning_policy="off")
        assert result["content"] == "x = 1"
        bodies = [c.kwargs["json"] for c in mock_post.call_args_list]
        assert bodies[1]["max_tokens"] == STEP_WRITE_TOKENS


class TestTruncatedWriteContinuation:
    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_partial_lines_written_then_append_completes(self, mock_llm,
                                                         mock_replan, tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["create impl.py with the implementation"]},
            {"action": "write", "arg": "impl.py",
             "content": "line1\nline2\nline3 par", "content_truncated": True},
            {"action": "write", "arg": "impl.py",
             "content": "line3 full\nline4\n", "append": True},
            {"action": "done"},
        ]
        result = _run_loop("create impl.py with the implementation",
                           str(tmp_path), max_replans=1, max_tasks=1,
                           max_steps=5)
        assert result["status"] == "complete"
        # The trailing partial line was dropped; append resumed from there.
        assert Path(tmp_path, "impl.py").read_text() == "line1\nline2\nline3 full\nline4\n"
        writes = [s for s in result["state"]["all_steps"] if s["action"] == "write"]
        assert "continue with append:true" in writes[0]["output"]
        assert writes[0]["_truncated_write"] is True

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_truncated_write_output_carries_resume_anchor(self, mock_llm,
                                                          mock_replan,
                                                          tmp_path):
        """The executor is stateless per step: the observation after a
        truncated write must say where the write stopped, or the append
        continuation can duplicate or skip lines."""
        seen = []

        def llm(messages, **kwargs):
            seen.append(messages[-1]["content"])
            n = len(seen)
            if n == 1:
                return {"tasks": ["create impl.py with the implementation"]}
            if n == 2:
                return {"action": "write", "arg": "impl.py",
                        "content": "line1\nline2\nline3 par",
                        "content_truncated": True}
            if n == 3:
                return {"action": "write", "arg": "impl.py",
                        "content": "line3\n", "append": True}
            return {"action": "done"}

        mock_llm.side_effect = llm
        result = _run_loop("create impl.py with the implementation",
                           str(tmp_path), max_replans=1, max_tasks=1,
                           max_steps=5)
        assert result["status"] == "complete"
        writes = [s for s in result["state"]["all_steps"] if s["action"] == "write"]
        assert "truncated after 2 lines" in writes[0]["output"]
        assert "'line2'" in writes[0]["output"]
        # The anchor must reach the next executor call, not just the log.
        assert "'line2'" in seen[2]

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_empty_truncated_write_not_told_to_append(self, mock_llm,
                                                      mock_replan, tmp_path):
        """No chunk was written: appending would land on the stale existing
        file, so the retry instruction must stay a non-append write."""
        Path(tmp_path, "impl.py").write_text("OLD CONTENT\n")
        seen = []

        def llm(messages, **kwargs):
            seen.append(messages[-1]["content"])
            n = len(seen)
            if n == 1:
                return {"tasks": ["create impl.py with the implementation"]}
            if n == 2:
                return {"action": "write", "arg": "impl.py",
                        "content": "nonewline", "content_truncated": True}
            if n == 3:
                return {"action": "write", "arg": "impl.py", "content": "new\n"}
            return {"action": "done"}

        mock_llm.side_effect = llm
        result = _run_loop("create impl.py with the implementation",
                           str(tmp_path), max_replans=1, max_tasks=1,
                           max_steps=5)
        assert result["status"] == "complete"
        assert "Resend the write (no append)" in seen[2]
        assert Path(tmp_path, "impl.py").read_text() == "new\n"

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_empty_truncated_append_chunk_stays_append(self, mock_llm,
                                                       mock_replan, tmp_path):
        seen = []

        def llm(messages, **kwargs):
            seen.append(messages[-1]["content"])
            n = len(seen)
            if n == 1:
                return {"tasks": ["create impl.py with the implementation"]}
            if n == 2:
                return {"action": "write", "arg": "impl.py",
                        "content": "line1\n"}
            if n == 3:
                return {"action": "write", "arg": "impl.py", "append": True,
                        "content": "nonewline", "content_truncated": True}
            if n == 4:
                return {"action": "write", "arg": "impl.py", "append": True,
                        "content": "line2\n"}
            return {"action": "done"}

        mock_llm.side_effect = llm
        result = _run_loop("create impl.py with the implementation",
                           str(tmp_path), max_replans=1, max_tasks=1,
                           max_steps=6)
        assert result["status"] == "complete"
        assert "Resend a smaller append:true chunk" in seen[3]
        assert Path(tmp_path, "impl.py").read_text() == "line1\nline2\n"

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_line_boundary_truncation_drops_nothing(self, mock_llm,
                                                    mock_replan, tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["create impl.py with the implementation"]},
            {"action": "write", "arg": "impl.py",
             "content": "line1\nline2\n", "content_truncated": True},
            {"action": "write", "arg": "impl.py", "content": "line3\n",
             "append": True},
            {"action": "done"},
        ]
        result = _run_loop("create impl.py with the implementation",
                           str(tmp_path), max_replans=1, max_tasks=1,
                           max_steps=5)
        assert result["status"] == "complete"
        assert Path(tmp_path, "impl.py").read_text() == "line1\nline2\nline3\n"

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_no_complete_line_skips_dispatch(self, mock_llm, mock_replan,
                                             tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["create impl.py with the implementation"]},
            {"action": "write", "arg": "impl.py",
             "content": "nonewline", "content_truncated": True},
            {"action": "write", "arg": "impl.py", "content": "ok\n"},
            {"action": "done"},
        ]
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("create impl.py with the implementation",
                               str(tmp_path), max_replans=1, max_tasks=1,
                               max_steps=5)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "complete"
        assert Path(tmp_path, "impl.py").read_text() == "ok\n"
        assert result["state"]["skipped_steps"] == 1
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        reasons = [e["reason"] for e in events if e["event"] == "step_skipped"]
        assert reasons == ["truncated_write_empty"]

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_reemitted_truncated_prefix_is_clean_restart(self, mock_llm,
                                                         mock_replan, tmp_path):
        """A complete resend of a retained prefix is dispatched, not 'done'."""
        mock_llm.side_effect = [
            {"tasks": ["create impl.py with the implementation"]},
            {"action": "write", "arg": "impl.py",
             "content": "a\nb par", "content_truncated": True},
            {"action": "write", "arg": "impl.py", "content": "a\n"},
            {"action": "write", "arg": "impl.py", "content": "b\n",
             "append": True},
            {"action": "done"},
        ]
        captured = []
        original_get_step = askme.get_step

        def spy_get_step(task, state, **kwargs):
            captured.extend(s.get("output", "") for s in state.get("last_steps", []))
            return original_get_step(task, state, **kwargs)

        with patch("askme.get_step", side_effect=spy_get_step):
            result = _run_loop("create impl.py with the implementation",
                               str(tmp_path), max_replans=1, max_tasks=1,
                               max_steps=6)
        assert result["status"] == "complete"
        assert Path(tmp_path, "impl.py").read_text() == "a\nb\n"
        assert result["state"]["executed_steps"] == 3
        assert not any("Already done" in o for o in captured)
        assert not any("Already done" in o for o in captured)


# --- Revision 3 (issue #15): backend-aware output budgets ---

class TestBackendAwareBudgets:
    def test_budgets_keyed_by_backend(self):
        if askme.LLM_BACKEND == "openrouter":
            assert askme.STEP_TOKENS == 4096
            assert askme.STEP_WRITE_TOKENS == 8192
        else:
            assert askme.STEP_TOKENS == 256
            assert askme.STEP_WRITE_TOKENS == 512

    def test_get_step_requests_backend_budget(self):
        with patch("askme.ask_llm", return_value={"action": "done"}) as m:
            askme.get_step("do a thing", {})
        assert m.call_args.kwargs["max_tokens"] == askme.STEP_TOKENS


# --- Revision 3 (issue #15): write-forcing executor policy ---

class TestWriteForcingPolicy:
    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_pressure_note_after_three_observations(self, mock_llm,
                                                    mock_replan, tmp_path):
        _big_file(str(tmp_path), lines=200)
        seen = []

        def llm(messages, **kwargs):
            seen.append(messages[-1]["content"])
            n = len(seen)
            if n == 1:
                return {"tasks": ["fix big.py bug"]}
            if n == 2:
                return {"action": "read", "arg": "big.py"}
            if n == 3:
                return {"action": "tree", "arg": "."}
            if n == 4:
                return {"action": "search", "arg": "def f1"}
            if n == 5:
                return {"action": "write", "arg": "big.py", "content": "fixed\n"}
            return {"action": "done"}

        mock_llm.side_effect = llm
        result = _run_loop("fix big.py bug", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=10)
        assert result["status"] == "complete"
        assert all("MUST be write" not in s for s in seen[:4])
        assert "MUST be write" in seen[4]
        assert "MUST be write" not in seen[5]  # write executed — pressure off

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_no_pressure_on_observe_shaped_task(self, mock_llm, mock_replan,
                                                tmp_path):
        _big_file(str(tmp_path), lines=200)
        seen = []

        def llm(messages, **kwargs):
            seen.append(messages[-1]["content"])
            n = len(seen)
            if n == 1:
                return {"tasks": ["inspect big.py structure"]}
            if n <= 5:
                return {"action": "read", "arg": "big.py",
                        "offset": 1 + 60 * (n - 2)}
            return {"action": "done"}

        mock_llm.side_effect = llm
        result = _run_loop("inspect big.py structure", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=10)
        assert result["status"] == "complete"
        assert result["state"]["skipped_steps"] == 0
        assert all("MUST be write" not in s for s in seen)

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_tail_reserve_blocks_observation_then_auto_fails(self, mock_llm,
                                                             mock_replan,
                                                             tmp_path):
        _big_file(str(tmp_path), lines=200)
        mock_llm.side_effect = [
            {"tasks": ["fix big.py bug"]},
            {"action": "read", "arg": "big.py"},
            {"action": "tree", "arg": "."},
            {"action": "read", "arg": "big.py", "offset": 61},   # tail: blocked
            {"action": "search", "arg": "def f1"},               # tail: auto-fail
        ]
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("fix big.py bug", str(tmp_path),
                               max_replans=1, max_tasks=1, max_steps=5)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "exhausted"
        assert any("observation steps exhausted without a write" in e
                   for e in result["state"]["errors"])
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        reasons = [e["reason"] for e in events if e["event"] == "step_skipped"]
        assert reasons == ["observe_tail_reserved", "observe_tail_exhausted"]

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_tail_allows_observation_after_commit(self, mock_llm, mock_replan,
                                                  tmp_path):
        _big_file(str(tmp_path), lines=200)
        mock_llm.side_effect = [
            {"tasks": ["fix big.py bug"]},
            {"action": "write", "arg": "big.py", "content": "fixed\n"},
            {"action": "read", "arg": "big.py"},
            {"action": "read", "arg": "big.py", "offset": 2},  # tail, but committed
            {"action": "done"},
        ]
        result = _run_loop("fix big.py bug", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=5)
        assert result["status"] == "complete"
        assert result["state"]["skipped_steps"] == 0

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_replan_sees_no_write_executed(self, mock_llm, mock_replan,
                                           tmp_path):
        _big_file(str(tmp_path), lines=200)
        plan_msgs = []
        call_idx = {"n": 0}

        def tracking_llm(messages, **kwargs):
            call_idx["n"] += 1
            n = call_idx["n"]
            if n == 1:
                return {"tasks": ["implement feature in big.py"]}
            if n == 2:
                return {"action": "read", "arg": "big.py"}
            if n == 3:
                return {"action": "fail", "reasoning": "cannot"}
            if n == 4:
                plan_msgs.append(messages[-1]["content"])
                return {"tasks": ["finish"]}
            return {"action": "done"}

        mock_llm.side_effect = tracking_llm
        result = _run_loop("implement feature in big.py", str(tmp_path),
                           max_replans=2, max_tasks=1, max_steps=5)
        assert result["status"] == "complete"
        assert len(plan_msgs) == 1
        assert '"no_write_executed": true' in plan_msgs[0]

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_no_flag_after_successful_write(self, mock_llm, mock_replan,
                                            tmp_path):
        plan_msgs = []
        call_idx = {"n": 0}

        def tracking_llm(messages, **kwargs):
            call_idx["n"] += 1
            n = call_idx["n"]
            if n == 1:
                return {"tasks": ["implement feature in app.py"]}
            if n == 2:
                return {"action": "write", "arg": "app.py", "content": "x = 1\n"}
            if n == 3:
                return {"action": "fail", "reasoning": "tests missing"}
            if n == 4:
                plan_msgs.append(messages[-1]["content"])
                return {"tasks": ["finish"]}
            return {"action": "done"}

        mock_llm.side_effect = tracking_llm
        result = _run_loop("implement feature in app.py", str(tmp_path),
                           max_replans=2, max_tasks=1, max_steps=5)
        assert result["status"] == "complete"
        assert len(plan_msgs) == 1
        assert "no_write_executed" not in plan_msgs[0]

    def test_task_replan_sees_no_write_flag(self):
        state = {"all_steps": [{"action": "read", "arg": "app.py", "ok": True}]}
        with patch("askme.ask_llm",
                   return_value={"task": "create the feature code in app.py"}) as m:
            askme.replan_task("implement feature in app.py",
                              ["[unknown] stalled"], [], state, "goal")
        msg = m.call_args.args[0][-1]["content"]
        assert '"no_write_executed": true' in msg

    def test_task_replan_flag_scoped_to_current_task(self):
        # A write from an earlier task must not mask this task's stall.
        state = {
            "all_steps": [
                {"action": "write", "arg": "a.py", "ok": True},
                {"action": "read", "arg": "b.py", "ok": True},
            ],
            "task_start_step_count": 1,
        }
        with patch("askme.ask_llm",
                   return_value={"task": "create the logic in b.py"}) as m:
            askme.replan_task("fix b.py logic",
                              ["[unknown] stalled"], [], state, "goal")
        msg = m.call_args.args[0][-1]["content"]
        assert '"no_write_executed": true' in msg
        replan_state = json.loads(msg.split("STATE:\n", 1)[1])
        assert [s["arg"] for s in replan_state["failed_steps"]] == ["b.py"]

    def test_full_replan_zero_step_reports_no_write(self):
        state = {
            "all_steps": [],
            "task_start_step_count": 0,
            "current_task": "implement feature in app.py",
            "errors": ["[unknown] transport failed"],
        }
        with patch("askme.ask_llm", return_value={"tasks": ["retry"]}) as m:
            askme.get_plan("implement feature in app.py", state)
        msg = m.call_args.args[0][-1]["content"]
        plan_state = json.loads(msg.split("STATE:\n", 1)[1])
        assert plan_state["no_write_executed"] is True
        assert "recent_steps" not in plan_state

    def test_task_replan_zero_step_does_not_leak_prior_task(self):
        state = {
            "all_steps": [{"action": "write", "arg": "prior.py", "ok": True}],
            "task_start_step_count": 1,
        }
        with patch("askme.ask_llm",
                   return_value={"task": "create the feature in app.py"}) as m:
            askme.replan_task("implement feature in app.py",
                              ["[unknown] transport failed"], [], state, "goal")
        msg = m.call_args.args[0][-1]["content"]
        replan_state = json.loads(msg.split("STATE:\n", 1)[1])
        assert replan_state["no_write_executed"] is True
        assert "failed_steps" not in replan_state
        assert "prior.py" not in msg

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_replan_flag_scoped_across_tasks(self, mock_llm, mock_replan,
                                             tmp_path):
        """Task 1's successful write must not mask task 2's stall."""
        Path(tmp_path, "b.py").write_text("x = 0\n")
        plan_msgs = []
        call_idx = {"n": 0}

        def tracking_llm(messages, **kwargs):
            call_idx["n"] += 1
            n = call_idx["n"]
            if n == 1:
                return {"tasks": ["create a.py", "fix b.py logic"]}
            if n == 2:
                return {"action": "write", "arg": "a.py", "content": "x = 1\n"}
            if n == 3:
                return {"action": "done"}
            if n == 4:
                return {"action": "read", "arg": "b.py"}
            if n == 5:
                return {"action": "fail", "reasoning": "cannot"}
            if n == 6:
                plan_msgs.append(messages[-1]["content"])
                return {"tasks": ["finish"]}
            return {"action": "done"}

        mock_llm.side_effect = tracking_llm
        result = _run_loop("create a.py then fix b.py logic", str(tmp_path),
                           max_replans=2, max_tasks=2, max_steps=5)
        assert result["status"] == "complete"
        assert len(plan_msgs) == 1
        assert '"no_write_executed": true' in plan_msgs[0]


# --- Validate-after-write policy (revision 4) ---

class TestWriteShapedClassification:
    def test_mutation_verbs_are_write_shaped(self):
        assert askme._is_write_shaped("implement bootstrap in algorithms.py")
        assert askme._is_write_shaped("update the config defaults")
        assert askme._is_write_shaped("fix the parser bug")

    def test_passive_include_is_not_write_shaped(self):
        # Codex P2 (PR #16): "include" matched passive phrasing.
        assert not askme._is_write_shaped("find files that include deprecated.h")
        assert not askme._is_write_shaped("list modules that include the header")

    def test_leading_observation_verb_wins(self):
        assert not askme._is_write_shaped("find where to add the import")
        assert not askme._is_write_shaped("locate the file to update")
        assert not askme._is_write_shaped("check whether main.c needs a fix")

    def test_empty_task_is_not_write_shaped(self):
        assert not askme._is_write_shaped("")


class TestValidateAfterWrite:
    def test_truncated_mutation_is_incomplete_even_after_shell(self):
        steps = [
            {"action": "write", "arg": "app.py", "ok": True,
             "_truncated_write": True},
            {"action": "shell", "arg": "python app.py", "ok": True},
        ]
        assert askme._write_visibility_flag(steps) == {
            "incomplete_write": "app.py"
        }

    def test_nonempty_append_visibility_exposes_frozen_recovery_target(self):
        steps = [
            {"action": "write", "arg": "app.py", "ok": True,
             "_append": True, "_truncated_write": True,
             "_target": "/real/old.py"},
        ]
        assert askme._incomplete_write_visibility(steps) == {
            "incomplete_write": "app.py",
            "incomplete_write_target": "/real/old.py",
            "incomplete_write_append_allowed": True,
        }

    def test_truncated_overwrite_supersedes_older_complete_write(self):
        steps = [
            {"action": "write", "arg": "app.py", "ok": True},
            {"action": "write", "arg": "app.py", "ok": True,
             "_truncated_write": True},
        ]
        assert askme._write_visibility_flag(steps) == {
            "incomplete_write": "app.py"
        }

    def test_complete_append_after_truncation_needs_validation(self):
        steps = [
            {"action": "write", "arg": "app.py", "ok": True,
             "_truncated_write": True},
            {"action": "write", "arg": "app.py", "ok": True,
             "_append": True},
        ]
        assert askme._write_visibility_flag(steps) == {
            "unvalidated_write": "app.py"
        }
        steps.append({"action": "shell", "arg": "pytest", "ok": True})
        assert askme._write_visibility_flag(steps) is None

    def test_full_and_task_replans_surface_incomplete_write(self):
        state = {
            "all_steps": [
                {"action": "write", "arg": "app.py", "ok": True,
                 "_truncated_write": True},
                {"action": "read", "arg": "app.py", "ok": True},
            ],
            # The next plan already started a non-write-shaped replacement;
            # its task-scoped slice excludes the earlier partial artifact.
            "task_start_step_count": 1,
            "current_task": "inspect app.py",
            "errors": ["[unknown] stalled"],
        }
        with patch("askme.ask_llm", return_value={"tasks": ["retry"]}) as full:
            askme.get_plan("finish app.py", state)
        full_msg = full.call_args.args[0][-1]["content"]
        full_state = json.loads(full_msg.split("STATE:\n", 1)[1])
        assert full_state["incomplete_write"] == "app.py"
        assert "unvalidated_write" not in full_state
        assert "no_write_executed" not in full_state

        with patch("askme.ask_llm",
                   return_value={"task": "finish app.py"}) as local:
            askme.replan_task("inspect app.py",
                              ["[unknown] stalled"], [], state, "goal")
        local_msg = local.call_args.args[0][-1]["content"]
        local_state = json.loads(local_msg.split("STATE:\n", 1)[1])
        assert local_state["incomplete_write"] == "app.py"
        assert "unvalidated_write" not in local_state
        assert "no_write_executed" not in local_state
        assert [step["action"] for step in local_state["failed_steps"]] == ["read"]

    def test_pending_empty_write_is_visible_to_both_replanners(self):
        state = {
            "all_steps": [],
            "pending_empty_writes": {
                "/internal/normalized/app.py": {
                    "name": "app.py", "append_allowed": False,
                }
            },
            "task_start_step_count": 0,
            "current_task": "inspect app.py",
            "errors": ["[unknown] stalled"],
        }
        with patch("askme.ask_llm", return_value={"tasks": ["retry"]}) as full:
            askme.get_plan("finish app.py", state)
        full_state = json.loads(
            full.call_args.args[0][-1]["content"].split("STATE:\n", 1)[1])
        assert full_state["incomplete_write"] == "app.py"
        assert (full_state["incomplete_write_target"]
                == "/internal/normalized/app.py")
        assert full_state["incomplete_write_append_allowed"] is False
        assert "no_write_executed" not in full_state

        with patch("askme.ask_llm", return_value={"task": "finish app.py"}) as local:
            askme.replan_task("inspect app.py", ["[unknown] stalled"], [],
                              state, "goal")
        local_state = json.loads(
            local.call_args.args[0][-1]["content"].split("STATE:\n", 1)[1])
        assert local_state["incomplete_write"] == "app.py"
        assert (local_state["incomplete_write_target"]
                == "/internal/normalized/app.py")
        assert local_state["incomplete_write_append_allowed"] is False
        assert "no_write_executed" not in local_state

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_empty_truncated_write_replans_as_incomplete(self, mock_llm,
                                                         mock_replan,
                                                         tmp_path):
        plan_msgs = []

        def llm(messages, **kwargs):
            llm.n = getattr(llm, "n", 0) + 1
            n = llm.n
            if n == 1:
                return {"tasks": ["implement feature in app.py"]}
            if n == 2:
                return {"action": "write", "arg": "app.py",
                        "content": "no complete line",
                        "content_truncated": True}
            if n == 3:
                return {"action": "fail", "reasoning": "need a new plan"}
            if n == 4:
                plan_msgs.append(messages[-1]["content"])
                return {"tasks": ["finish"]}
            return {"action": "done"}

        mock_llm.side_effect = llm
        result = _run_loop("implement feature in app.py", str(tmp_path),
                           max_replans=2, max_tasks=1, max_steps=3)
        assert result["status"] == "exhausted"
        assert len(plan_msgs) == 1
        plan_state = json.loads(plan_msgs[0].split("STATE:\n", 1)[1])
        assert plan_state["incomplete_write"] == "app.py"
        assert "no_write_executed" not in plan_state
        assert not (tmp_path / "app.py").exists()

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_validate_note_after_two_rewrites(self, mock_llm, mock_replan,
                                              tmp_path):
        seen = []

        def llm(messages, **kwargs):
            seen.append(messages[-1]["content"])
            n = len(seen)
            if n == 1:
                return {"tasks": ["implement feature in big.py"]}
            if n == 2:
                return {"action": "write", "arg": "big.py", "content": "v1\n"}
            if n == 3:
                return {"action": "write", "arg": "big.py", "content": "v2\n"}
            if n == 4:
                return {"action": "shell", "arg": "echo verified"}
            return {"action": "done"}

        mock_llm.side_effect = llm
        result = _run_loop("implement feature in big.py", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=10)
        assert result["status"] == "complete"
        assert all("Do NOT write the whole file again" not in s
                   for s in seen[:3])
        assert "Do NOT write the whole file again" in seen[3]
        assert "big.py is already written" in seen[3]
        # A successful shell verification clears the pressure.
        assert "Do NOT write the whole file again" not in seen[4]

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_rewrite_loop_skip_after_three(self, mock_llm, mock_replan,
                                           tmp_path):
        def llm(messages, **kwargs):
            llm.n = getattr(llm, "n", 0) + 1
            n = llm.n
            if n == 1:
                return {"tasks": ["implement feature in big.py"]}
            if n <= 4:
                return {"action": "write", "arg": "big.py",
                        "content": f"v{n - 1}\n"}
            if n == 5:
                return {"action": "write", "arg": "big.py", "content": "v4\n"}
            return {"action": "done"}

        mock_llm.side_effect = llm
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("implement feature in big.py", str(tmp_path),
                               max_replans=1, max_tasks=1, max_steps=10)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "complete"
        # The fourth consecutive full write is damped, not executed.
        assert (tmp_path / "big.py").read_text() == "v3\n"
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        reasons = [e["reason"] for e in events if e["event"] == "step_skipped"]
        assert "rewrite_loop" in reasons

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_rewrite_streak_normalizes_alias_paths(self, mock_llm, mock_replan,
                                                   tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["create big.py"]},
            {"action": "write", "arg": "big.py", "content": "v1\n"},
            {"action": "write", "arg": "sub/../big.py", "content": "v2\n"},
            {"action": "write", "arg": "big.py", "content": "v3\n"},
            {"action": "write", "arg": "sub/../big.py", "content": "v4\n"},
            {"action": "done"},
        ]
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("create big.py", str(tmp_path),
                               max_replans=1, max_tasks=1, max_steps=6)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "complete"
        assert (tmp_path / "big.py").read_text() == "v3\n"
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert any(e.get("reason") == "rewrite_loop" for e in events)

    def test_target_key_resolves_parent_symlinks_but_not_leaf(self, tmp_path):
        work, real, alias = _symlinked_target_dirs(tmp_path)
        # The immediate parent and file do not exist yet; strict=False must
        # still collapse the existing directory alias.
        alias_key = askme._mutation_target_key(
            {"arg": "alias/new/app.py"}, str(work))
        real_key = askme._mutation_target_key(
            {"arg": str(real / "new" / "app.py")}, str(work))
        assert alias_key == real_key
        # Preserve OS traversal order: alias is followed before `..` applies.
        assert askme._mutation_target_key(
            {"arg": "alias/../app.py"}, str(work)) == \
            askme._mutation_target_key(
                {"arg": str(tmp_path / "app.py")}, str(work))

        referent = real / "referent.py"
        referent.write_text("real\n")
        leaf = work / "leaf.py"
        try:
            leaf.symlink_to(referent)
        except (OSError, NotImplementedError):
            pytest.skip("file symlinks are unavailable")
        # Atomic full writes replace the leaf symlink itself, so it is a
        # different mutation destination from the referent.
        assert askme._mutation_target_key(
            {"arg": "leaf.py"}, str(work)) != askme._mutation_target_key(
                {"arg": str(referent)}, str(work))
        assert askme._mutation_target_key(
            {"arg": "leaf.py", "append": True}, str(work)) == \
            askme._mutation_target_key(
                {"arg": str(referent), "append": True}, str(work))

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_rewrite_streak_collapses_symlinked_parent_aliases(
            self, mock_llm, mock_replan, tmp_path):
        work, real, _ = _symlinked_target_dirs(tmp_path)
        real_target = str(real / "big.py")
        mock_llm.side_effect = [
            {"tasks": ["create big.py"]},
            {"action": "write", "arg": "alias/big.py", "content": "v1\n"},
            {"action": "write", "arg": real_target, "content": "v2\n"},
            {"action": "write", "arg": "alias/big.py", "content": "v3\n"},
            {"action": "write", "arg": real_target, "content": "v4\n"},
            {"action": "done"},
        ]
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("create big.py", str(work),
                               max_replans=1, max_tasks=1, max_steps=6)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "complete"
        assert (real / "big.py").read_text() == "v3\n"
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert any(e.get("reason") == "rewrite_loop" for e in events)

    @pytest.mark.parametrize("truncated_content", [
        "partial line\nmissing tail",
        "no complete line",
    ])
    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_complete_symlink_alias_clears_incomplete_write(
            self, mock_llm, mock_replan, tmp_path, truncated_content):
        work, real, _ = _symlinked_target_dirs(tmp_path)
        real_target = str(real / "app.py")
        mock_llm.side_effect = [
            {"tasks": ["create app.py"]},
            {"action": "write", "arg": "alias/app.py",
             "content": truncated_content, "content_truncated": True},
            {"action": "write", "arg": real_target, "content": "final\n"},
            {"action": "done"},
        ]
        result = _run_loop("create app.py", str(work),
                           max_replans=1, max_tasks=1, max_steps=4)
        assert result["status"] == "complete"
        assert (real / "app.py").read_text() == "final\n"
        assert result["state"]["pending_empty_writes"] == {}
        assert not askme._unresolved_incomplete_writes(
            result["state"]["all_steps"], str(work))

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_append_through_leaf_symlink_resolves_partial_referent(
            self, mock_llm, mock_replan, tmp_path):
        work = tmp_path / "work"
        real = tmp_path / "real"
        work.mkdir()
        real.mkdir()
        referent = real / "app.py"
        leaf = work / "app.py"
        try:
            leaf.symlink_to(referent)
        except (OSError, NotImplementedError):
            pytest.skip("file symlinks are unavailable")
        mock_llm.side_effect = [
            {"tasks": ["create app.py"]},
            {"action": "write", "arg": str(referent),
             "content": "prefix\nmissing tail", "content_truncated": True},
            {"action": "write", "arg": "app.py", "content": "tail\n",
             "append": True},
            {"action": "done"},
        ]
        result = _run_loop("create app.py", str(work),
                           max_replans=1, max_tasks=1, max_steps=4)
        assert result["status"] == "complete"
        assert referent.read_text() == "prefix\ntail\n"
        assert not askme._unresolved_incomplete_writes(
            result["state"]["all_steps"], str(work))

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_empty_overwrite_blocks_append_through_leaf_alias(
            self, mock_llm, mock_replan, tmp_path):
        work = tmp_path / "work"
        real = tmp_path / "real"
        work.mkdir()
        real.mkdir()
        referent = real / "app.py"
        referent.write_text("OLD\n")
        leaf = work / "app.py"
        try:
            leaf.symlink_to(referent)
        except (OSError, NotImplementedError):
            pytest.skip("file symlinks are unavailable")
        mock_llm.side_effect = [
            {"tasks": ["replace app.py"]},
            {"action": "write", "arg": "app.py",
             "content": "no complete line", "content_truncated": True},
            {"action": "write", "arg": str(referent), "content": "BAD\n",
             "append": True},
            {"action": "write", "arg": "app.py", "content": "GOOD\n"},
            {"action": "done"},
        ]
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("replace app.py", str(work),
                               max_replans=1, max_tasks=1, max_steps=5)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "complete"
        assert referent.read_text() == "OLD\n"
        assert not leaf.is_symlink()
        assert leaf.read_text() == "GOOD\n"
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert any(e.get("reason") == "append_after_empty_overwrite"
                   for e in events)

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_pending_overwrite_wins_across_append_aliases(
            self, mock_llm, mock_replan, tmp_path):
        work = tmp_path / "work"
        real = tmp_path / "real"
        work.mkdir()
        real.mkdir()
        referent = real / "app.py"
        referent.write_text("OLD\n")
        leaf = work / "app.py"
        try:
            leaf.symlink_to(referent)
        except (OSError, NotImplementedError):
            pytest.skip("file symlinks are unavailable")
        mock_llm.side_effect = [
            {"tasks": ["replace app.py"]},
            {"action": "write", "arg": "app.py",
             "content": "no complete line", "append": True,
             "content_truncated": True},
            {"action": "write", "arg": "app.py",
             "content": "still no complete line", "content_truncated": True},
            {"action": "write", "arg": "app.py", "content": "BAD\n",
             "append": True},
            {"action": "write", "arg": "app.py", "content": "GOOD\n"},
            {"action": "write", "arg": str(referent), "content": "TAIL\n",
             "append": True},
            {"action": "done"},
        ]
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("replace app.py", str(work),
                               max_replans=1, max_tasks=1, max_steps=6)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "complete"
        assert not leaf.is_symlink()
        assert leaf.read_text() == "GOOD\n"
        assert referent.read_text() == "OLD\nTAIL\n"
        assert result["state"]["pending_empty_writes"] == {}
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert any(e.get("reason") == "append_after_empty_overwrite"
                   for e in events)

    @pytest.mark.parametrize(("first_content", "expected_old"), [
        ("no complete line", "OLD\nTAIL\n"),
        ("PART\nmissing tail", "OLD\nPART\nTAIL\n"),
    ])
    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_recovery_prioritizes_restrictive_overwrite_obligation(
            self, mock_llm, mock_replan, tmp_path, first_content,
            expected_old):
        work = tmp_path / "work"
        real = tmp_path / "real"
        work.mkdir()
        real.mkdir()
        old_target = real / "old.py"
        old_target.write_text("OLD\n")
        leaf = work / "app.py"
        try:
            leaf.symlink_to(old_target)
        except (OSError, NotImplementedError):
            pytest.skip("file symlinks are unavailable")

        def llm(messages, **kwargs):
            llm.n = getattr(llm, "n", 0) + 1
            if llm.n == 1:
                return {"tasks": ["replace app.py"]}
            if llm.n == 2:
                return {"action": "write", "arg": "app.py",
                        "content": first_content, "append": True,
                        "content_truncated": True}
            if llm.n == 3:
                return {"action": "write", "arg": "app.py",
                        "content": "still no complete line",
                        "content_truncated": True}
            if llm.n in (4, 6, 8):
                return {"action": "done"}
            prompt = messages[-1]["content"]
            if llm.n == 5:
                assert f'"incomplete_write_target": "{leaf}"' in prompt
                assert '"incomplete_write_append_allowed": false' in prompt
                return {"action": "write", "arg": str(leaf),
                        "content": "GOOD\n"}
            assert f'"incomplete_write_target": "{old_target}"' in prompt
            assert '"incomplete_write_append_allowed": true' in prompt
            return {"action": "write", "arg": str(old_target),
                    "content": "TAIL\n", "append": True}

        mock_llm.side_effect = llm
        result = _run_loop("replace app.py", str(work),
                           max_replans=1, max_tasks=1, max_steps=7)
        assert result["status"] == "complete"
        assert not leaf.is_symlink()
        assert leaf.read_text() == "GOOD\n"
        assert old_target.read_text() == expected_old
        assert result["state"]["pending_empty_writes"] == {}
        assert not askme._unresolved_incomplete_writes(
            result["state"]["all_steps"], str(work))

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_empty_append_can_resume_through_physical_alias(
            self, mock_llm, mock_replan, tmp_path):
        work = tmp_path / "work"
        real = tmp_path / "real"
        work.mkdir()
        real.mkdir()
        referent = real / "app.py"
        referent.write_text("OLD\n")
        leaf = work / "app.py"
        try:
            leaf.symlink_to(referent)
        except (OSError, NotImplementedError):
            pytest.skip("file symlinks are unavailable")
        mock_llm.side_effect = [
            {"tasks": ["update app.py"]},
            {"action": "write", "arg": "app.py",
             "content": "no complete line", "append": True,
             "content_truncated": True},
            {"action": "write", "arg": str(referent), "content": "NEW\n",
             "append": True},
            {"action": "done"},
        ]
        result = _run_loop("update app.py", str(work),
                           max_replans=1, max_tasks=1, max_steps=4)
        assert result["status"] == "complete"
        assert referent.read_text() == "OLD\nNEW\n"
        assert result["state"]["pending_empty_writes"] == {}

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_empty_append_obligations_survive_symlink_retarget(
            self, mock_llm, mock_replan, tmp_path):
        work = tmp_path / "work"
        real = tmp_path / "real"
        work.mkdir()
        real.mkdir()
        old_target = real / "old.py"
        new_target = real / "new.py"
        old_target.write_text("OLD\n")
        new_target.write_text("NEW\n")
        leaf = work / "app.py"
        try:
            leaf.symlink_to(old_target)
        except (OSError, NotImplementedError):
            pytest.skip("file symlinks are unavailable")
        recovery_args = {
            str(old_target): "OLDTAIL\n",
            str(new_target): "NEWTAIL\n",
        }
        surfaced = []

        def llm(messages, **kwargs):
            llm.n = getattr(llm, "n", 0) + 1
            if llm.n == 1:
                return {"tasks": ["update app.py"]}
            if llm.n == 2:
                return {"action": "write", "arg": "app.py",
                        "content": "no complete line", "append": True,
                        "content_truncated": True}
            if llm.n == 3:
                return {"action": "shell",
                        "arg": f"ln -sfn {new_target} app.py"}
            if llm.n == 4:
                return {"action": "write", "arg": "app.py",
                        "content": "still no complete line", "append": True,
                        "content_truncated": True}
            if llm.n in (5, 7, 9):
                return {"action": "done"}
            prompt = messages[-1]["content"]
            positions = {
                target: prompt.rfind(f" at {target}. Retry")
                for target in recovery_args
            }
            target = max(positions, key=positions.get)
            assert positions[target] >= 0
            surfaced.append(target)
            return {"action": "write", "arg": target,
                    "content": recovery_args[target], "append": True}

        mock_llm.side_effect = llm
        log_path = tmp_path / "run.jsonl"
        old_log = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("update app.py", str(work),
                               max_replans=1, max_tasks=1, max_steps=8)
        finally:
            askme.RUN_LOG_PATH = old_log
        assert result["status"] == "complete"
        assert old_target.read_text() == "OLD\nOLDTAIL\n"
        assert new_target.read_text() == "NEW\nNEWTAIL\n"
        assert result["state"]["pending_empty_writes"] == {}
        assert surfaced == [str(old_target), str(new_target)]
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert any(e.get("reason") == "incomplete_write_done"
                   for e in events)

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_nonempty_append_recovery_hint_survives_symlink_retarget(
            self, mock_llm, mock_replan, tmp_path):
        work = tmp_path / "work"
        real = tmp_path / "real"
        work.mkdir()
        real.mkdir()
        old_target = real / "old.py"
        new_target = real / "new.py"
        old_target.write_text("OLD\n")
        new_target.write_text("NEW\n")
        leaf = work / "app.py"
        try:
            leaf.symlink_to(old_target)
        except (OSError, NotImplementedError):
            pytest.skip("file symlinks are unavailable")
        expected_target = str(old_target)

        def llm(messages, **kwargs):
            llm.n = getattr(llm, "n", 0) + 1
            if llm.n == 1:
                return {"tasks": ["update app.py"]}
            if llm.n == 2:
                return {"action": "write", "arg": "app.py",
                        "content": "PART\nmissing tail", "append": True,
                        "content_truncated": True}
            if llm.n == 3:
                return {"action": "shell",
                        "arg": f"ln -sfn {new_target} app.py"}
            if llm.n in (4, 6):
                return {"action": "done"}
            prompt = messages[-1]["content"]
            assert f" at {expected_target}. Retry" in prompt
            return {"action": "write", "arg": expected_target,
                    "content": "TAIL\n", "append": True}

        mock_llm.side_effect = llm
        result = _run_loop("update app.py", str(work),
                           max_replans=1, max_tasks=1, max_steps=5)
        assert result["status"] == "complete"
        assert old_target.read_text() == "OLD\nPART\nTAIL\n"
        assert new_target.read_text() == "NEW\n"
        assert not askme._unresolved_incomplete_writes(
            result["state"]["all_steps"], str(work))

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_recovery_target_survives_working_dir_symlink_retarget(
            self, mock_llm, mock_replan, tmp_path):
        old_root = tmp_path / "old-root"
        new_root = tmp_path / "new-root"
        old_root.mkdir()
        new_root.mkdir()
        old_target = old_root / "app.py"
        new_target = new_root / "app.py"
        old_target.write_text("OLD\n")
        new_target.write_text("NEW\n")
        work_link = tmp_path / "work"
        try:
            work_link.symlink_to(old_root, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("directory symlinks are unavailable")

        def llm(messages, **kwargs):
            llm.n = getattr(llm, "n", 0) + 1
            if llm.n == 1:
                return {"tasks": ["update app.py"]}
            if llm.n == 2:
                return {"action": "write", "arg": "app.py",
                        "content": "no complete line", "append": True,
                        "content_truncated": True}
            if llm.n == 3:
                return {"action": "shell",
                        "arg": f"ln -sfn {new_root} {work_link}"}
            if llm.n in (4, 6):
                return {"action": "done"}
            prompt = messages[-1]["content"]
            assert f'"incomplete_write_target": "{old_target}"' in prompt
            assert '"incomplete_write_append_allowed": true' in prompt
            return {"action": "write", "arg": str(old_target),
                    "content": "TAIL\n", "append": True}

        mock_llm.side_effect = llm
        result = _run_loop("update app.py", str(work_link),
                           max_replans=1, max_tasks=1, max_steps=5)
        assert result["status"] == "complete"
        assert old_target.read_text() == "OLD\nTAIL\n"
        assert new_target.read_text() == "NEW\n"
        assert result["state"]["pending_empty_writes"] == {}

    @patch("askme.replan_task", return_value="finish app.py")
    @patch("askme.ask_llm")
    def test_task_local_retry_keeps_structured_recovery_target(
            self, mock_llm, mock_replan, tmp_path):
        work = tmp_path / "work"
        real = tmp_path / "real"
        work.mkdir()
        real.mkdir()
        old_target = real / "old.py"
        new_target = real / "new.py"
        old_target.write_text("OLD\n")
        new_target.write_text("NEW\n")
        leaf = work / "app.py"
        try:
            leaf.symlink_to(old_target)
        except (OSError, NotImplementedError):
            pytest.skip("file symlinks are unavailable")

        def llm(messages, **kwargs):
            llm.n = getattr(llm, "n", 0) + 1
            if llm.n == 1:
                return {"tasks": ["update app.py"]}
            if llm.n == 2:
                return {"action": "write", "arg": "app.py",
                        "content": "no complete line", "append": True,
                        "content_truncated": True}
            if llm.n == 3:
                return {"action": "shell",
                        "arg": f"ln -sfn {new_target} app.py"}
            if llm.n == 4:
                return {"action": "fail", "reasoning": "need retry"}
            if llm.n == 5:
                prompt = messages[-1]["content"]
                assert f'"incomplete_write_target": "{old_target}"' in prompt
                assert '"incomplete_write_append_allowed": true' in prompt
                return {"action": "write", "arg": str(old_target),
                        "content": "TAIL\n", "append": True}
            return {"action": "done"}

        mock_llm.side_effect = llm
        result = _run_loop("update app.py", str(work),
                           max_replans=1, max_tasks=1, max_steps=4)
        assert result["status"] == "complete"
        assert mock_replan.called
        assert old_target.read_text() == "OLD\nTAIL\n"
        assert new_target.read_text() == "NEW\n"
        assert result["state"]["pending_empty_writes"] == {}

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_empty_overwrite_referent_guards_survive_symlink_retarget(
            self, mock_llm, mock_replan, tmp_path):
        work = tmp_path / "work"
        real = tmp_path / "real"
        work.mkdir()
        real.mkdir()
        old_target = real / "old.py"
        new_target = real / "new.py"
        old_target.write_text("OLD\n")
        new_target.write_text("NEW\n")
        leaf = work / "app.py"
        try:
            leaf.symlink_to(old_target)
        except (OSError, NotImplementedError):
            pytest.skip("file symlinks are unavailable")
        mock_llm.side_effect = [
            {"tasks": ["replace app.py"]},
            {"action": "write", "arg": "app.py",
             "content": "no complete line", "content_truncated": True},
            {"action": "shell", "arg": f"ln -sfn {new_target} app.py"},
            {"action": "write", "arg": "app.py",
             "content": "still no complete line", "content_truncated": True},
            {"action": "write", "arg": str(old_target),
             "content": "BAD-OLD\n", "append": True},
            {"action": "write", "arg": str(new_target),
             "content": "BAD-NEW\n", "append": True},
            {"action": "write", "arg": "app.py", "content": "GOOD\n"},
            {"action": "done"},
        ]
        log_path = tmp_path / "run.jsonl"
        old_log = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("replace app.py", str(work),
                               max_replans=1, max_tasks=1, max_steps=7)
        finally:
            askme.RUN_LOG_PATH = old_log
        assert result["status"] == "complete"
        assert old_target.read_text() == "OLD\n"
        assert new_target.read_text() == "NEW\n"
        assert not leaf.is_symlink()
        assert leaf.read_text() == "GOOD\n"
        assert result["state"]["pending_empty_writes"] == {}
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert sum(e.get("reason") == "append_after_empty_overwrite"
                   for e in events) == 2

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_empty_append_regular_path_survives_retarget_to_symlink(
            self, mock_llm, mock_replan, tmp_path):
        work = tmp_path / "work"
        real = tmp_path / "real"
        work.mkdir()
        real.mkdir()
        original = work / "app.py"
        moved = work / "old.py"
        new_target = real / "new.py"
        original.write_text("OLD\n")
        new_target.write_text("NEW\n")
        mock_llm.side_effect = [
            {"tasks": ["update app.py"]},
            {"action": "write", "arg": "app.py",
             "content": "no complete line", "append": True,
             "content_truncated": True},
            {"action": "shell",
             "arg": f"mv app.py old.py && ln -s {new_target} app.py"},
            {"action": "write", "arg": "app.py", "content": "TAIL\n",
             "append": True},
            {"action": "done"},
        ]
        log_path = tmp_path / "run.jsonl"
        old_log = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("update app.py", str(work),
                               max_replans=1, max_tasks=1, max_steps=4)
        finally:
            askme.RUN_LOG_PATH = old_log
        assert result["status"] == "exhausted"
        assert moved.read_text() == "OLD\n"
        assert new_target.read_text() == "NEW\nTAIL\n"
        assert result["state"]["pending_empty_writes"]
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert any(e.get("reason") == "incomplete_write_done"
                   for e in events)

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_rewrite_streak_ignores_model_internal_target(self, mock_llm,
                                                          mock_replan,
                                                          tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["create big.py"]},
            {"action": "write", "arg": "big.py", "content": "v1\n",
             "_target": "spoof-1"},
            {"action": "write", "arg": "big.py", "content": "v2\n",
             "_target": "spoof-2"},
            {"action": "write", "arg": "big.py", "content": "v3\n",
             "_target": "spoof-3"},
            {"action": "write", "arg": "big.py", "content": "v4\n",
             "_target": "spoof-4"},
            {"action": "done"},
        ]
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("create big.py", str(tmp_path),
                               max_replans=1, max_tasks=1, max_steps=6)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "complete"
        assert (tmp_path / "big.py").read_text() == "v3\n"
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert any(e.get("reason") == "rewrite_loop" for e in events)

    @patch("askme.replan_task", return_value="finish big.py")
    @patch("askme.ask_llm")
    def test_rewrite_streak_survives_task_local_replan(self, mock_llm,
                                                       mock_replan, tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["create big.py"]},
            {"action": "write", "arg": "big.py", "content": "v1\n"},
            {"action": "write", "arg": "big.py", "content": "v2\n"},
            {"action": "write", "arg": "big.py", "content": "v3\n"},
            {"action": "write", "arg": "big.py", "content": "v4\n"},
            {"action": "done"},
        ]
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("create big.py", str(tmp_path),
                               max_replans=1, max_tasks=1, max_steps=3)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "complete"
        assert mock_replan.call_count == 1
        assert (tmp_path / "big.py").read_text() == "v3\n"
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert any(e.get("reason") == "rewrite_loop" for e in events)

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_rewrite_streak_survives_full_replan(self, mock_llm, mock_replan,
                                                 tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["create big.py"]},
            {"action": "write", "arg": "big.py", "content": "v1\n"},
            {"action": "write", "arg": "big.py", "content": "v2\n"},
            {"action": "write", "arg": "big.py", "content": "v3\n"},
            {"tasks": ["finish big.py"]},
            {"action": "write", "arg": "big.py", "content": "v4\n"},
            {"action": "done"},
        ]
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            with patch("askme._validate_completion",
                       return_value={"valid": True}):
                result = _run_loop("create big.py", str(tmp_path),
                                   max_replans=2, max_tasks=1, max_steps=3)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "complete"
        assert mock_replan.call_count == 1
        assert (tmp_path / "big.py").read_text() == "v3\n"
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert any(e.get("reason") == "rewrite_loop" for e in events)

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_deterministic_repair_clears_rewrite_streak(self, mock_llm,
                                                        mock_replan, tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["create main.c"]},
            {"action": "write", "arg": "main.c",
             "content": 'int main(void){ printf("v1"); return 0; }\n'},
            {"action": "write", "arg": "main.c",
             "content": 'int main(void){ printf("v2"); return 0; }\n'},
            {"action": "write", "arg": "main.c",
             "content": 'int main(void){ printf("v3"); return 0; }\n'},
            {"action": "shell", "arg": "cc -o main main.c"},
            {"action": "write", "arg": "main.c",
             "content": "int main(void){ return 4; }\n"},
            {"action": "done"},
        ]
        shell_calls = {"count": 0}

        def fake_execute(action, working_dir):
            if action.get("action") == "shell":
                shell_calls["count"] += 1
                if shell_calls["count"] == 1:
                    return {
                        "ok": False,
                        "output": ("main.c:1:13: error: implicit declaration "
                                   "of function 'printf'"),
                        "error_type": "compile_error",
                    }
                return {"ok": True, "output": "(no output)"}
            return execute(action, working_dir)

        with patch("askme.execute", side_effect=fake_execute), \
                patch("askme._validate_completion",
                      return_value={"valid": True}):
            result = _run_loop("create main.c", str(tmp_path),
                               max_replans=1, max_tasks=1, max_steps=6)
        assert result["status"] == "complete"
        assert shell_calls["count"] == 2
        assert (tmp_path / "main.c").read_text() == \
            "int main(void){ return 4; }\n"

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_append_chunks_do_not_count_as_rewrites(self, mock_llm,
                                                    mock_replan, tmp_path):
        seen = []

        def llm(messages, **kwargs):
            seen.append(messages[-1]["content"])
            n = len(seen)
            if n == 1:
                return {"tasks": ["create big.py from chunks"]}
            if n == 2:
                return {"action": "write", "arg": "big.py", "content": "a\n"}
            if n == 3:
                return {"action": "write", "arg": "big.py", "content": "b\n",
                        "append": True}
            if n == 4:
                return {"action": "write", "arg": "big.py", "content": "c\n",
                        "append": True}
            return {"action": "done"}

        mock_llm.side_effect = llm
        result = _run_loop("create big.py from chunks", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=10)
        assert result["status"] == "complete"
        assert (tmp_path / "big.py").read_text() == "a\nb\nc\n"
        assert all("Do NOT write the whole file again" not in s for s in seen)

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_failed_mutation_keeps_write_pressure(self, mock_llm, mock_replan,
                                                  tmp_path):
        # Codex P2 (PR #16): a failed edit must not disarm the pressure note.
        _big_file(str(tmp_path), lines=200)
        seen = []

        def llm(messages, **kwargs):
            seen.append(messages[-1]["content"])
            n = len(seen)
            if n == 1:
                return {"tasks": ["fix big.py bug"]}
            if n == 2:
                return {"action": "read", "arg": "big.py"}
            if n == 3:
                return {"action": "tree", "arg": "."}
            if n == 4:
                return {"action": "search", "arg": "def f1"}
            if n == 5:
                return {"action": "edit", "arg": "big.py",
                        "find": "no such text anywhere", "replace": "x"}
            if n == 6:
                return {"action": "write", "arg": "big.py", "content": "ok\n"}
            return {"action": "done"}

        mock_llm.side_effect = llm
        result = _run_loop("fix big.py bug", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=10)
        assert result["status"] == "complete"
        assert "MUST be write" in seen[4]
        # Pre-fix, the failed edit incremented commit_executed and the
        # pressure vanished here.
        assert "MUST be write" in seen[5]
        assert "MUST be write" not in seen[6]

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_replan_sees_unvalidated_write(self, mock_llm, mock_replan,
                                           tmp_path):
        plan_msgs = []

        def llm(messages, **kwargs):
            llm.n = getattr(llm, "n", 0) + 1
            n = llm.n
            if n == 1:
                return {"tasks": ["implement feature in app.py"]}
            if n == 2:
                return {"action": "write", "arg": "app.py", "content": "x = 1\n"}
            if n == 3:
                return {"action": "fail", "reasoning": "unsure"}
            if n == 4:
                plan_msgs.append(messages[-1]["content"])
                return {"tasks": ["finish"]}
            return {"action": "done"}

        mock_llm.side_effect = llm
        result = _run_loop("implement feature in app.py", str(tmp_path),
                           max_replans=2, max_tasks=1, max_steps=5)
        assert result["status"] == "complete"
        assert len(plan_msgs) == 1
        assert '"unvalidated_write": "app.py"' in plan_msgs[0]
        assert "no_write_executed" not in plan_msgs[0]

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_failed_inspection_task_is_not_a_write_stall(self, mock_llm,
                                                         mock_replan,
                                                         tmp_path):
        # Codex P2 (PR #16): "create a.py, then inspect b.py" — a failure in
        # the inspection task must not flag no_write_executed.
        plan_msgs = []

        def llm(messages, **kwargs):
            llm.n = getattr(llm, "n", 0) + 1
            n = llm.n
            if n == 1:
                return {"tasks": ["create a.py with hello",
                                  "inspect b.py structure"]}
            if n == 2:
                return {"action": "write", "arg": "a.py",
                        "content": "print('hello')\n"}
            if n == 3:
                return {"action": "done"}
            if n == 4:
                return {"action": "fail", "reasoning": "b.py is missing"}
            if n == 5:
                plan_msgs.append(messages[-1]["content"])
                return {"tasks": ["finish"]}
            return {"action": "done"}

        mock_llm.side_effect = llm
        result = _run_loop("create a.py with hello, then inspect b.py",
                           str(tmp_path),
                           max_replans=2, max_tasks=2, max_steps=5)
        assert result["status"] == "complete"
        assert len(plan_msgs) == 1
        assert "no_write_executed" not in plan_msgs[0]
        assert "unvalidated_write" not in plan_msgs[0]

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_truncated_writes_do_not_advance_rewrite_streak(self, mock_llm,
                                                            mock_replan,
                                                            tmp_path):
        # Codex P1 (PR #21): a partial write from a truncated sentinel block
        # is not a completed rewrite — neither the validate note nor the
        # rewrite_loop skip may fire while the file is incomplete.
        seen = []

        def llm(messages, **kwargs):
            seen.append(messages[-1]["content"])
            n = len(seen)
            if n == 1:
                return {"tasks": ["implement feature in big.py"]}
            if n in (2, 3, 4):
                return {"action": "write", "arg": "big.py",
                        "content": f"try{n} line1\ntry{n} par",
                        "content_truncated": True}
            if n == 5:
                return {"action": "write", "arg": "big.py",
                        "content": "final\nversion\n"}
            return {"action": "done"}

        mock_llm.side_effect = llm
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("implement feature in big.py", str(tmp_path),
                               max_replans=1, max_tasks=1, max_steps=10)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "complete"
        # The clean restart after repeated truncations must execute.
        assert (tmp_path / "big.py").read_text() == "final\nversion\n"
        assert all("Do NOT write the whole file again" not in s for s in seen)
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        reasons = [e["reason"] for e in events if e["event"] == "step_skipped"]
        assert "rewrite_loop" not in reasons

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_truncated_append_clears_armed_rewrite_streak(self, mock_llm,
                                                          mock_replan,
                                                          tmp_path):
        def llm(messages, **kwargs):
            llm.n = getattr(llm, "n", 0) + 1
            n = llm.n
            if n == 1:
                return {"tasks": ["implement feature in big.py"]}
            if n in (2, 3, 4):
                return {"action": "write", "arg": "big.py",
                        "content": f"v{n - 1}\n"}
            if n == 5:
                return {"action": "write", "arg": "big.py",
                        "content": "partial\ncut", "append": True,
                        "content_truncated": True}
            if n == 6:
                return {"action": "write", "arg": "big.py",
                        "content": "final\nversion\n"}
            return {"action": "done"}

        mock_llm.side_effect = llm
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("implement feature in big.py", str(tmp_path),
                               max_replans=1, max_tasks=1, max_steps=10)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "complete"
        assert (tmp_path / "big.py").read_text() == "final\nversion\n"
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        reasons = [e["reason"] for e in events if e["event"] == "step_skipped"]
        assert "rewrite_loop" not in reasons

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_empty_truncation_clears_armed_rewrite_streak(self, mock_llm,
                                                          mock_replan,
                                                          tmp_path):
        def llm(messages, **kwargs):
            llm.n = getattr(llm, "n", 0) + 1
            n = llm.n
            if n == 1:
                return {"tasks": ["implement feature in big.py"]}
            if n in (2, 3, 4):
                return {"action": "write", "arg": "big.py",
                        "content": f"v{n - 1}\n"}
            if n == 5:
                return {"action": "write", "arg": "big.py",
                        "content": "no complete line",
                        "content_truncated": True}
            if n == 6:
                return {"action": "write", "arg": "big.py",
                        "content": "final\nversion\n"}
            return {"action": "done"}

        mock_llm.side_effect = llm
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("implement feature in big.py", str(tmp_path),
                               max_replans=1, max_tasks=1, max_steps=10)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "complete"
        assert (tmp_path / "big.py").read_text() == "final\nversion\n"
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        reasons = [e["reason"] for e in events if e["event"] == "step_skipped"]
        assert "truncated_write_empty" in reasons
        assert "rewrite_loop" not in reasons

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_exhaustion_never_accepts_incomplete_write(self, mock_llm,
                                                       mock_replan,
                                                       tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["fix app.py"]},
            {"action": "write", "arg": "app.py",
             "content": "x = 1\nmissing tail",
             "content_truncated": True},
            {"action": "shell", "arg": "true"},
        ]
        result = _run_loop("fix app.py", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=2)
        assert result["status"] == "exhausted"
        assert (tmp_path / "app.py").read_text() == "x = 1\n"
        assert askme._deterministic_check("fix app.py", result["state"],
                                          str(tmp_path)) is False

    def test_edit_and_unrelated_write_do_not_clear_incomplete_target(self,
                                                                     tmp_path):
        steps = [
            {"action": "write", "arg": "app.py", "ok": True,
             "_truncated_write": True},
            {"action": "edit", "arg": "app.py", "ok": True},
            {"action": "write", "arg": "notes.txt", "ok": True},
            {"action": "shell", "arg": "true", "ok": True},
        ]
        assert askme._write_visibility_flag(steps) == {
            "incomplete_write": "app.py"
        }
        state = {"all_steps": steps, "errors": []}
        assert askme._deterministic_check("fix app.py", state,
                                          str(tmp_path)) is False

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_done_is_rejected_after_truncated_write(self, mock_llm,
                                                    mock_replan, tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["create app.py"]},
            {"action": "write", "arg": "app.py",
             "content": "partial\nmissing tail",
             "content_truncated": True},
            {"action": "done"},
        ]
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("create app.py", str(tmp_path),
                               max_replans=1, max_tasks=1, max_steps=2)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "exhausted"
        events = [json.loads(line)
                  for line in log_path.read_text().splitlines()]
        assert any(e.get("reason") == "incomplete_write_done"
                   for e in events)

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_done_is_rejected_after_empty_truncation(self, mock_llm,
                                                     mock_replan, tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["create app.py"]},
            {"action": "write", "arg": "app.py",
             "content": "no complete line", "content_truncated": True},
            {"action": "done"},
        ]
        result = _run_loop("create app.py", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=2)
        assert result["status"] == "exhausted"
        assert result["state"]["pending_empty_writes"]
        assert not (tmp_path / "app.py").exists()

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_edit_cannot_clear_empty_truncation(self, mock_llm,
                                                mock_replan, tmp_path):
        (tmp_path / "app.py").write_text("OLD\n")
        mock_llm.side_effect = [
            {"tasks": ["replace app.py"]},
            {"action": "write", "arg": "app.py",
             "content": "no complete line", "content_truncated": True},
            {"action": "edit", "arg": "app.py",
             "find": "OLD", "replace": "CHANGED"},
            {"action": "done"},
        ]
        result = _run_loop("replace app.py", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=3)
        assert result["status"] == "exhausted"
        assert result["state"]["pending_empty_writes"]
        assert (tmp_path / "app.py").read_text() == "CHANGED\n"

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_deterministic_reconciliation_rejects_empty_truncation(
            self, mock_llm, mock_replan, tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["fix app.py"]},
            {"action": "shell", "arg": "true"},
            {"action": "write", "arg": "app.py",
             "content": "no complete line", "content_truncated": True},
        ]
        result = _run_loop("fix app.py", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=2)
        assert result["status"] == "exhausted"
        assert result["state"]["pending_empty_writes"]
        assert askme._deterministic_check("fix app.py", result["state"],
                                          str(tmp_path)) is False

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_armed_streak_allows_truncated_overwrite_then_restart(
            self, mock_llm, mock_replan, tmp_path):
        def llm(messages, **kwargs):
            llm.n = getattr(llm, "n", 0) + 1
            n = llm.n
            if n == 1:
                return {"tasks": ["implement feature in big.py"]}
            if n in (2, 3, 4):
                return {"action": "write", "arg": "big.py",
                        "content": f"v{n - 1}\n"}
            if n == 5:
                return {"action": "write", "arg": "big.py",
                        "content": "partial\nmissing tail",
                        "content_truncated": True}
            if n == 6:
                return {"action": "write", "arg": "big.py",
                        "content": "final\nversion\n"}
            return {"action": "done"}

        mock_llm.side_effect = llm
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("implement feature in big.py", str(tmp_path),
                               max_replans=1, max_tasks=1, max_steps=10)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "complete"
        assert (tmp_path / "big.py").read_text() == "final\nversion\n"
        events = [json.loads(line)
                  for line in log_path.read_text().splitlines()]
        assert not any(e.get("reason") == "rewrite_loop" for e in events)

    @patch("askme.ask_llm", return_value={"tasks": []})
    def test_empty_plan_is_not_completion(self, mock_llm, tmp_path):
        result = _run_loop("create app.py", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=2)
        assert result["status"] == "exhausted"
        assert any("malformed_plan" in error
                   for error in result["state"]["errors"])
        assert result["state"]["completed_tasks"] == []

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_failed_validation_requires_new_evidence(self, mock_llm,
                                                     mock_replan, tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["fix app.py"]},
            {"action": "write", "arg": "app.py", "content": "x = 1\n"},
            {"action": "done"},
            {"valid": False, "reason": "tests not run", "missing": []},
            {"tasks": ["finish app.py"]},
            {"action": "done"},
        ]
        with patch.object(askme, "FINAL_VALIDATE", "always"):
            result = _run_loop("fix app.py", str(tmp_path),
                               max_replans=2, max_tasks=1, max_steps=2)
        assert result["status"] == "exhausted"
        assert result["state"]["validation_recheck_needed"] is True
        assert any("requires new write, edit, or shell evidence" in error
                   for error in result["state"]["errors"])

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_empty_overwrite_rejects_append_to_stale_file(
            self, mock_llm, mock_replan, tmp_path):
        (tmp_path / "app.py").write_text("OLD\n")
        mock_llm.side_effect = [
            {"tasks": ["replace app.py"]},
            {"action": "write", "arg": "app.py",
             "content": "no complete line", "content_truncated": True},
            {"action": "write", "arg": "app.py", "append": True,
             "content": "NEW\n"},
            {"action": "done"},
        ]
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("replace app.py", str(tmp_path),
                               max_replans=1, max_tasks=1, max_steps=3)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "exhausted"
        assert (tmp_path / "app.py").read_text() == "OLD\n"
        assert "append:false" in result["state"]["last_steps"][-1]["output"]
        events = [json.loads(line)
                  for line in log_path.read_text().splitlines()]
        assert any(e.get("reason") == "append_after_empty_overwrite"
                   for e in events)

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_empty_overwrite_recovers_via_partial_then_append(
            self, mock_llm, mock_replan, tmp_path):
        (tmp_path / "app.py").write_text("OLD\n")
        mock_llm.side_effect = [
            {"tasks": ["replace app.py"]},
            {"action": "write", "arg": "app.py",
             "content": "no complete line", "content_truncated": True},
            {"action": "write", "arg": "app.py",
             "content": "NEW\nmissing tail", "content_truncated": True},
            {"action": "write", "arg": "app.py", "append": True,
             "content": "TAIL\n"},
            {"action": "done"},
        ]
        result = _run_loop("replace app.py", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=4)
        assert result["status"] == "complete"
        assert (tmp_path / "app.py").read_text() == "NEW\nTAIL\n"
        assert result["state"]["pending_empty_writes"] == {}
        assert not askme._unresolved_incomplete_writes(
            result["state"]["all_steps"], str(tmp_path))

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_truncated_prefix_matching_prior_write_is_dispatched(
            self, mock_llm, mock_replan, tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["create app.py"]},
            {"action": "write", "arg": "app.py", "content": "prefix\n"},
            {"action": "write", "arg": "app.py",
             "content": "prefix\nmissing tail", "content_truncated": True},
            {"action": "write", "arg": "app.py", "append": True,
             "content": "tail\n"},
            {"action": "done"},
        ]
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("create app.py", str(tmp_path),
                               max_replans=1, max_tasks=1, max_steps=4)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "complete"
        assert (tmp_path / "app.py").read_text() == "prefix\ntail\n"
        events = [json.loads(line)
                  for line in log_path.read_text().splitlines()]
        assert not any(e.get("reason") == "duplicate_write" for e in events)
        assert sum(e.get("truncated_write") is True for e in events) == 1

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_complete_restart_matching_truncated_prefix_is_dispatched(
            self, mock_llm, mock_replan, tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["create app.py"]},
            {"action": "write", "arg": "app.py",
             "content": "complete\n", "content_truncated": True},
            {"action": "write", "arg": "app.py", "content": "complete\n"},
            {"action": "done"},
        ]
        log_path = tmp_path / "run.jsonl"
        old = askme.RUN_LOG_PATH
        askme.RUN_LOG_PATH = str(log_path)
        try:
            result = _run_loop("create app.py", str(tmp_path),
                               max_replans=1, max_tasks=1, max_steps=3)
        finally:
            askme.RUN_LOG_PATH = old
        assert result["status"] == "complete"
        assert (tmp_path / "app.py").read_text() == "complete\n"
        assert not askme._unresolved_incomplete_writes(
            result["state"]["all_steps"], str(tmp_path))
        events = [json.loads(line)
                  for line in log_path.read_text().splitlines()]
        assert not any(e.get("reason") == "duplicate_write" for e in events)

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_failed_action_is_not_new_validation_evidence(
            self, mock_llm, mock_replan, tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["fix app.py"]},
            {"action": "write", "arg": "app.py", "content": "x = 1\n"},
            {"action": "done"},
            {"valid": False, "reason": "tests not run", "missing": []},
            {"tasks": ["repair app.py"]},
            {"action": "edit", "arg": "app.py",
             "find": "not present", "replace": "x = 2"},
            {"action": "done"},
        ]
        with patch.object(askme, "FINAL_VALIDATE", "always"):
            result = _run_loop("fix app.py", str(tmp_path),
                               max_replans=2, max_tasks=1, max_steps=2)
        assert result["status"] == "exhausted"
        assert result["state"]["validation_attempts"] == 1
        assert result["state"]["validation_recheck_needed"] is True
        assert askme._has_new_validation_evidence(result["state"]) is False

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_duplicate_shell_auto_done_cannot_hide_incomplete_write(
            self, mock_llm, mock_replan, tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["create app.py"]},
            {"action": "write", "arg": "app.py",
             "content": "partial\nmissing tail", "content_truncated": True},
            {"action": "shell", "arg": "true"},
            {"action": "shell", "arg": "true"},
        ]
        result = _run_loop("create app.py", str(tmp_path),
                           max_replans=1, max_tasks=1, max_steps=3)
        assert result["status"] == "exhausted"
        assert result["state"]["completed_tasks"] == []
        assert any("incomplete_write" in error
                   for error in result["state"]["errors"])

    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_exhaustion_reconciliation_cannot_bypass_failed_validation(
            self, mock_llm, mock_replan, tmp_path):
        mock_llm.side_effect = [
            {"tasks": ["fix app.py"]},
            {"action": "shell", "arg": "true"},
            {"action": "write", "arg": "app.py", "content": "x = 1\n"},
            {"action": "done"},
            {"valid": False, "reason": "needs verification", "missing": []},
            {"tasks": ["verify app.py"]},
            {"action": "shell", "arg": "true"},
            {"action": "tree", "arg": "."},
            {"action": "read", "arg": "app.py"},
        ]
        with patch.object(askme, "FINAL_VALIDATE", "always"):
            result = _run_loop("fix app.py", str(tmp_path),
                               max_replans=2, max_tasks=1, max_steps=3)
        assert askme._deterministic_check("fix app.py", result["state"],
                                          str(tmp_path)) is True
        assert result["status"] == "exhausted"
        assert result["state"]["validation_recheck_needed"] is True

    @pytest.mark.parametrize("invalid_verdict", [None, "true"])
    @patch("askme.replan_task", return_value=None)
    @patch("askme.ask_llm")
    def test_invalid_validation_recheck_cannot_erase_known_failure(
            self, mock_llm, mock_replan, tmp_path, invalid_verdict):
        mock_llm.side_effect = [
            {"tasks": ["fix app.py"]},
            {"action": "write", "arg": "app.py", "content": "x = 1\n"},
            {"action": "done"},
            {"valid": False, "reason": "needs repair", "missing": []},
            {"tasks": ["repair app.py"]},
            {"action": "edit", "arg": "app.py",
             "find": "1", "replace": "2"},
            {"action": "done"},
            {"valid": invalid_verdict},
        ]
        with patch.object(askme, "FINAL_VALIDATE", "always"):
            result = _run_loop("fix app.py", str(tmp_path),
                               max_replans=2, max_tasks=1, max_steps=2)
        assert result["status"] == "exhausted"
        assert result["state"]["validation_attempts"] == 2
        assert result["state"]["validation_recheck_needed"] is True
