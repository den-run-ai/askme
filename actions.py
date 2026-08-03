"""Action layer for the AskMe agent (issue #36).

One registry defines every action's name, category, fields, and handler, while
``parse_action_envelope`` is the pure semantic boundary shared by decode,
controller intake, and dispatch. ActionExecutor groups the handlers behind a
single dispatch seam with shared workspace-path resolution, error
classification, atomic writes, and bounded observation packing. ActionResult
and StepReceipt are the typed result and receipt structures the controller
consumes. done/fail stay controller concerns in askme.py, which also keeps the
execute() compatibility facade and re-exports this module's public names.

This layer is not a sandbox: handlers run with the launching user's host
permissions, and workspace-relative paths organize files without confining
shell commands, absolute paths, or traversal.
"""

import bisect
import copy
import hashlib
import json
import os
import re
import shlex
import subprocess
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_RESULT = 300  # chars kept from command output

READ_LINES = 60  # max lines per read window
READ_CHARS = 1200  # max Unicode code points per read page
READ_LIMIT_MAX = 200  # hard cap on model-specified read limit
SEARCH_MAX_MATCHES = 15  # bounded literal search results
SEARCH_MAX_CHARS = 1500  # bounded search output
SEARCH_MAX_FILES = 500  # bounded search scan
TREE_MAX_ENTRIES = 60  # bounded repository-tree listing
TREE_MAX_CHARS = 1500  # bounded tree output
TREE_MAX_DEPTH = 3  # bounded tree walk depth
OBSERVE_STATE_CHARS = 1500  # executor-state budget for observation step output

# Command patterns that need longer timeouts
_LONG_TIMEOUT_PATTERNS = [
    "install",
    "update",
    "upgrade",  # package managers
    "cmake",
    "make",
    "cargo build",
    "go build",  # build tools
    "npm install",
    "pip install",
    "brew ",  # specific installers
]
SHELL_TIMEOUT = 30  # default
SHELL_TIMEOUT_LONG = 120  # for install/build commands
SHELL_TIMEOUT_MAX = 300  # hard cap for model-specified timeout

# Model-controlled read positions are bounded even though Python integers are
# unbounded.  The cap is intentionally much larger than an executor-visible
# workspace is expected to need; its purpose is to make the wire contract
# finite and deterministic rather than to constrain ordinary navigation.
READ_POSITION_MAX = 2_147_483_647


def _get_shell_timeout(cmd, hint=None):
    """Return timeout for a shell command. Uses longer timeout for install/build patterns."""
    if hint is not None:
        return min(max(int(hint), 5), SHELL_TIMEOUT_MAX)
    cmd_lower = cmd.lower()
    for pattern in _LONG_TIMEOUT_PATTERNS:
        if pattern in cmd_lower:
            return SHELL_TIMEOUT_LONG
    return SHELL_TIMEOUT


# VCS / dependency / build directories excluded from search and tree walks.
_REPO_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "target",
        "dist",
        "build",
    }
)


def _valid_nonempty_str(value):
    return isinstance(value, str) and bool(value.strip())


def _step_path(arg, working_dir):
    p = Path(arg)
    if not p.is_absolute():
        p = Path(working_dir) / p
    return p


def _mutation_target_key(step, working_dir=None):
    """Stable operation-aware identity for write/edit transition tracking."""
    target = step.get("_target")
    if _valid_nonempty_str(target):
        return target
    arg = step.get("arg", "")
    if not _valid_nonempty_str(arg):
        return None
    if working_dir is not None:
        path = _step_path(arg, working_dir)
        lexical = os.path.abspath(os.fspath(path))
        try:
            if step.get("append") or step.get("_append"):
                # Append opens the target directly and follows a leaf symlink.
                path = path.resolve(strict=False)
            else:
                # Atomic overwrite/edit replaces a leaf symlink rather than
                # mutating its referent. Resolve directory aliases only.
                path = path.parent.resolve(strict=False) / path.name
        except (OSError, RuntimeError, ValueError):
            path = Path(lexical)
        return os.path.normcase(os.path.normpath(os.fspath(path)))
    return os.path.normcase(os.path.normpath(arg))


def _target_recovery_arg(target, working_dir):
    """Action-ready spelling for a frozen mutation target."""
    if not _valid_nonempty_str(target):
        return None
    # Keep the canonical absolute identity. A relative spelling can silently
    # retarget if the working-directory path (or one of its parents) is itself
    # a symlink changed by a shell step during recovery.
    return target


_COMPILER_EXES = frozenset(
    {
        "cc",
        "gcc",
        "g++",
        "clang",
        "clang++",
        "c++",
        "rustc",
        "javac",
        "make",
        "cmake",
        "cargo",
        "go",
        "tsc",
        "swiftc",
    }
)


def _is_compiler_command(cmd):
    """Check if a shell command invokes a compiler or build tool."""
    try:
        parts = shlex.split(cmd)
    except ValueError:
        parts = cmd.split()
    if not parts:
        return False
    exe = os.path.basename(parts[0])
    return exe in _COMPILER_EXES


def classify_error(output, action="shell", cmd=""):
    """Classify an error output into a typed category for structured diagnostics."""
    out = output.lower()
    if "timeout" in out or output == "TIMEOUT":
        return "timeout"
    if "command not found" in out:
        return "missing_tool"
    if "permission denied" in out:
        return "permission_denied"
    if "no such file" in out or "no such file or directory" in out:
        if action == "shell" and cmd and _is_compiler_command(cmd):
            return "compile_error"
        return "missing_file"
    if action == "shell" and cmd and _is_compiler_command(cmd):
        if re.search(
            r"error generated|implicit function declaration|undeclared (?:library )?function|"
            r"include the header <[^>]+>|undefined reference|undefined symbols",
            out,
        ):
            return "compile_error"
    if "syntax error" in out or "error:" in out:
        return "compile_error"
    return "unknown"


def _read_offset_limit(action):
    """Read window from an action already normalized by the wire parser."""
    return action.get("offset", 1), action.get("limit", READ_LINES)


def _read_cursor(action):
    """Parsed 0-based Unicode-code-point cursor, or None for a line read."""
    return action.get("cursor")


def _read_continuation_limit(action):
    """Parsed line limit echoed by an action-ready cursor continuation."""
    return action["limit"]


def _read_key(action):
    """Identity for duplicate-read detection, including every range field."""
    arg = action.get("arg", "")
    cursor = _read_cursor(action)
    if cursor is not None:
        limit = _read_continuation_limit(action)
        return (arg, "cursor", cursor, limit, action.get("sha256") or "")
    offset, limit = _read_offset_limit(action)
    return (arg, "lines", offset, limit)


@dataclass(frozen=True)
class ActionProtocolError:
    """A precise, non-throwing rejection at the model-action boundary."""

    error_type: str
    message: str
    field: str | None = None


@dataclass(frozen=True, eq=False)
class ActionEnvelope(Mapping[str, Any]):
    """Immutable normalized model action.

    Values are copied while parsing, and the outer action cannot be mutated.
    ``to_dict`` is the explicit compatibility projection for legacy callers
    and JSON records.  Controller transformations create a new parsed
    envelope with :meth:`with_updates` instead of changing the selected
    action in place.
    """

    _items: tuple[tuple[str, Any], ...]

    @property
    def name(self):
        return self["action"]

    def __getitem__(self, key):
        for item_key, value in self._items:
            if item_key == key:
                # Never leak a nested dict/list retained by the frozen
                # envelope.  Callers may mutate the compatibility value they
                # receive, but cannot thereby mutate the parsed action.
                return copy.deepcopy(value)
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _value in self._items)

    def __len__(self):
        return len(self._items)

    def __eq__(self, other):
        if isinstance(other, Mapping):
            return self.to_dict() == dict(other)
        return NotImplemented

    def to_dict(self):
        return {key: copy.deepcopy(value) for key, value in self._items}

    def with_updates(self, **updates):
        candidate = self.to_dict()
        candidate.update(updates)
        parsed = parse_action_envelope(candidate)
        if isinstance(parsed, ActionProtocolError):
            raise ValueError(parsed.message)
        return parsed


@dataclass(frozen=True)
class ActionTransport:
    """Provider/decoder facts that a model action cannot set."""

    content_truncated: bool = False


@dataclass(frozen=True, eq=False)
class DecodedAction(Mapping[str, Any]):
    """A normalized action plus transport metadata.

    The mapping view preserves the historical ``ask_llm`` result shape.  In
    particular, a recovered partial sentinel write still exposes the legacy
    ``content_truncated`` key to direct compatibility callers, while the key
    is not part of :attr:`envelope` and therefore cannot be model-spoofed.
    """

    envelope: ActionEnvelope
    transport: ActionTransport = ActionTransport()

    def __getitem__(self, key):
        if key == "content_truncated" and self.transport.content_truncated:
            return True
        return self.envelope[key]

    def __iter__(self) -> Iterator[str]:
        yield from self.envelope
        if self.transport.content_truncated:
            yield "content_truncated"

    def __len__(self):
        return len(self.envelope) + int(self.transport.content_truncated)

    def __eq__(self, other):
        if isinstance(other, Mapping):
            return self.to_dict() == dict(other)
        return NotImplemented

    def to_dict(self):
        projected = self.envelope.to_dict()
        if self.transport.content_truncated:
            projected["content_truncated"] = True
        return projected


@dataclass
class ActionResult:
    """Normalized result of one dispatched action (issue #36).

    ``ok``/``output``/``error_type`` are the controller-facing core. Every
    action-specific field — truncation flags and reasons, read content and
    continuation cursors, source metadata — travels in ``details`` exactly as
    the legacy ``execute`` dict carried it.
    """

    ok: bool
    output: str
    error_type: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw):
        """Normalize a legacy execute() result dict once, at the seam."""
        details = {k: v for k, v in raw.items() if k not in ("ok", "output", "error_type")}
        return cls(
            ok=bool(raw.get("ok")),
            output=raw.get("output", ""),
            error_type=raw.get("error_type"),
            details=details,
        )

    def get(self, key, default=None):
        """Legacy dict-style access covering core fields and details."""
        if key in ("ok", "output", "error_type"):
            return getattr(self, key)
        return self.details.get(key, default)

    def to_dict(self):
        """Project to the legacy execute() result dict."""
        raw: dict[str, Any] = {"ok": self.ok, "output": self.output}
        if self.error_type is not None:
            raw["error_type"] = self.error_type
        raw.update(self.details)
        return raw


@dataclass(frozen=True)
class ActionSpec:
    """One action's registry entry.

    ``allowed`` and ``requires`` feed :func:`parse_action_envelope`, the one
    semantic contract used by decode, controller intake, and dispatch.
    Control actions carry no handler because the run controller resolves
    them before dispatch.
    """

    name: str
    category: str  # "observe" | "mutate" | "control"
    handler: Callable[["ActionExecutor", Mapping[str, Any]], ActionResult] | None = None
    requires: tuple[str, ...] = ()
    allowed: tuple[str, ...] = ()


class ActionExecutor:
    """Filesystem/shell action handlers behind one dispatch seam (issue #36).

    One instance per dispatch. Workspace path resolution, exception-to-result
    shaping, atomic writes, and bounded observation packing are shared here
    instead of being copied per action branch. ``done``/``fail`` never
    execute: they are controller decisions, and dispatching one is a typed
    error.
    """

    def __init__(self, working_dir="."):
        self.working_dir = working_dir

    def dispatch(self, action):
        parsed = parse_action_envelope(action)
        if isinstance(parsed, ActionProtocolError):
            error_type = parsed.error_type
            try:
                attempted = action.get("action") if isinstance(action, Mapping) else None
            except Exception:
                # A hostile or structurally forged Mapping may fail both
                # copying and diagnostic lookup; dispatch must still return
                # the parser's typed error rather than raise.
                attempted = None
            if attempted == "read":
                error_type = {
                    "cursor": "invalid_read_cursor",
                    "limit": "invalid_read_limit",
                    "offset": "invalid_read_offset",
                    "sha256": "read_cursor_hash_required",
                }.get(parsed.field, error_type)
            elif attempted == "shell" and parsed.field == "timeout":
                error_type = "invalid_timeout"
            return ActionResult(False, parsed.message, error_type)
        act = parsed.name
        spec = ACTION_SPECS[act]
        if spec.handler is None:
            return ActionResult(
                False,
                f"control action '{act}' is resolved by the run controller",
                "control_action",
            )
        return spec.handler(self, parsed)

    def _resolve(self, arg):
        """Resolve a model-supplied path against the working directory."""
        p = Path(arg)
        if not p.is_absolute():
            p = Path(self.working_dir) / p
        return p

    def _exception_result(self, e, act):
        out = str(e)[:MAX_RESULT]
        return ActionResult(False, out, classify_error(out, act))

    @staticmethod
    def _atomic_write_text(path, content):
        """Write via temp file + rename so a crashed/interrupted write never
        leaves a partial file behind."""
        tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
        tmp.write_text(content)
        os.replace(tmp, path)

    @staticmethod
    def _iter_repo_files(root, max_files=SEARCH_MAX_FILES, on_error=None):
        """Yield visible repo files under root, bounded and deterministic."""
        count = 0
        for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
            dirnames[:] = sorted(
                d for d in dirnames if d not in _REPO_SKIP_DIRS and not d.startswith(".")
            )
            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                yield Path(dirpath) / name
                count += 1
                if count >= max_files:
                    return

    @staticmethod
    def _pack_observation_lines(make_header, records, reasons, max_chars):
        """Pack complete discovery records, including the header, within a cap."""

        def pack(active_reasons):
            header = make_header(active_reasons)
            kept = []
            used = len(header)
            for record in records:
                extra = 1 + len(record)
                if used + extra > max_chars:
                    break
                kept.append(record)
                used += extra
            output = header + ("\n" + "\n".join(kept) if kept else "")
            return output, kept

        output, kept = pack(reasons)
        if len(kept) == len(records):
            return output, reasons
        final_reasons = [*reasons, "chars"]
        output, _ = pack(final_reasons)
        return output, final_reasons

    def _shell(self, action):
        try:
            timeout = _get_shell_timeout(action["arg"], action.get("timeout"))
            r = subprocess.run(
                action["arg"],
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.working_dir,
            )
            out = r.stdout[:MAX_RESULT] + r.stderr[-MAX_RESULT:]
            output = out.strip() or "(no output)"
            if r.returncode == 0:
                return ActionResult(True, output)
            return ActionResult(False, output, classify_error(output, "shell", cmd=action["arg"]))
        except subprocess.TimeoutExpired:
            return ActionResult(False, "TIMEOUT", "timeout")

    def _write(self, action):
        try:
            p = self._resolve(action["arg"])
            p.parent.mkdir(parents=True, exist_ok=True)
            content = action.get("content", "")
            # Auto-serialize dict/list content — models often output JSON as objects
            # instead of escaped strings (e.g. "content": {"key": "val"} not "content": "{\"key\": \"val\"}")
            if isinstance(content, (dict, list)):
                content = json.dumps(content, indent=2)
            if action.get("append"):
                # Chunked-write transport: append one chunk at a time so large
                # files fit within the executor token budget.
                existed = p.exists()
                with open(p, "a") as f:
                    f.write(content)
                size = p.stat().st_size
                verb = "Appended to" if existed else "Wrote"
                return ActionResult(True, f"{verb} {p.name} (+{len(content)} chars, total {size})")
            self._atomic_write_text(p, content)
            return ActionResult(True, f"Wrote {p.name}")
        except Exception as e:
            return self._exception_result(e, "write")

    def _edit(self, action):
        try:
            p = self._resolve(action["arg"])
            if not p.exists():
                return ActionResult(False, f"File not found: {p.name}", "missing_file")
            text = p.read_text()
            find = action.get("find", "")
            replace = action.get("replace", "")
            count = text.count(find)
            if count == 0:
                return ActionResult(False, f"No match for find string in {p.name}", "edit_failed")
            if count > 1:
                return ActionResult(
                    False,
                    f"Ambiguous: find string matches {count} times in {p.name}",
                    "edit_failed",
                )
            self._atomic_write_text(p, text.replace(find, replace, 1))
            return ActionResult(True, f"Edited {p.name}")
        except Exception as e:
            return self._exception_result(e, "edit")

    def _read(self, action):
        try:
            p = self._resolve(action["arg"])
            raw = p.read_bytes()
            text = raw.decode("utf-8")
            lines = text.splitlines(keepends=True)
            total = len(lines)
            offset, limit = _read_offset_limit(action)
            # Hash the exact bytes whose decoded text is paged. Continuation
            # cursors count Python Unicode code points, not UTF-8 bytes or
            # grapheme clusters, and are valid only for this source hash.
            meta = {
                "total_lines": total,
                "total_bytes": len(raw),
                "total_chars": len(text),
                "sha256": hashlib.sha256(raw).hexdigest()[:12],
            }

            def empty_page(ok, output, error_type=None):
                details = {"truncated": False, "content": "", "continuation": None, **meta}
                return ActionResult(ok, output, error_type, details)

            cursor = _read_cursor(action)
            if cursor is not None:
                limit = _read_continuation_limit(action)
            expected_hash = action.get("sha256")
            if cursor is not None and expected_hash and expected_hash != meta["sha256"]:
                return empty_page(
                    False,
                    (
                        f"[{p.name}: stale read cursor; source changed "
                        f"({expected_hash} -> {meta['sha256']})]"
                    ),
                    "stale_read_cursor",
                )
            if cursor is None and offset > total:
                return empty_page(
                    True,
                    f"[{p.name}: offset {offset} past end of file ({total} lines)]",
                )
            if cursor is not None and cursor >= len(text):
                return empty_page(
                    False,
                    (
                        f"[{p.name}: read cursor {cursor} must point "
                        f"before end of file ({len(text)} chars)]"
                    ),
                    "invalid_read_cursor",
                )

            starts = []
            pos = 0
            for line in lines:
                starts.append(pos)
                pos += len(line)
            if cursor is None:
                start = starts[offset - 1]
                start_line_index = offset - 1
            else:
                start = cursor
                start_line_index = max(0, bisect.bisect_right(starts, start) - 1)
            window_end_index = min(total, start_line_index + limit)
            window_end = starts[window_end_index] if window_end_index < total else len(text)
            page_cap = READ_CHARS
            while True:
                page_end = min(start + page_cap, window_end)
                body = text[start:page_end]
                last_line_index = max(
                    start_line_index,
                    bisect.bisect_right(starts, max(start, page_end - 1)) - 1,
                )
                end = last_line_index + 1
                continuation = None
                if page_end < len(text):
                    next_line_index = max(0, bisect.bisect_right(starts, page_end) - 1)
                    continuation = {
                        "cursor": page_end,
                        "offset": next_line_index + 1,
                        "limit": limit,
                        "sha256": meta["sha256"],
                    }
                header = f"[{p.name}: lines {start_line_index + 1}-{end} of {total}"
                if page_end < window_end:
                    header += f", cut at {len(body)} chars"
                if continuation:
                    header += (
                        f"; continue: cursor={continuation['cursor']}, "
                        f"limit={continuation['limit']}, "
                        f"sha256={continuation['sha256']}"
                    )
                header += "]"
                output = f"{header}\n{body}"
                overflow = len(output) - OBSERVE_STATE_CHARS
                if overflow <= 0 or page_cap == 1:
                    break
                page_cap = max(1, page_cap - overflow)
            truncated = continuation is not None
            return ActionResult(
                True,
                output,
                details={
                    "truncated": truncated,
                    "content": body,
                    "continuation": continuation,
                    **meta,
                },
            )
        except Exception as e:
            return self._exception_result(e, "read")

    def _search(self, action):
        pattern = action.get("arg", "")
        try:
            base = self._resolve(action.get("path") or ".")
            if not base.is_dir():
                return ActionResult(False, f"Directory not found: {base.name}", "missing_file")
            walk_errors: list[OSError] = []
            files = list(
                self._iter_repo_files(base, SEARCH_MAX_FILES + 1, on_error=walk_errors.append)
            )
            file_limited = len(files) > SEARCH_MAX_FILES
            matches = []
            snippets_limited = False
            unreadable = 0
            for f in files[:SEARCH_MAX_FILES]:
                try:
                    text = f.read_text(errors="replace")
                except OSError:
                    unreadable += 1
                    continue
                if "\0" in text[:2048]:
                    continue  # skip binary files
                for lineno, line in enumerate(text.splitlines(), 1):
                    if pattern in line:
                        snippet = line.strip()
                        if len(snippet) > 100:
                            snippet = snippet[:99] + "…"
                            snippets_limited = True
                        matches.append(f"{f.relative_to(base)}:{lineno}: {snippet}")
                        if len(matches) > SEARCH_MAX_MATCHES:
                            break
                if len(matches) > SEARCH_MAX_MATCHES:
                    break
            match_limited = len(matches) > SEARCH_MAX_MATCHES
            shown = matches[:SEARCH_MAX_MATCHES]
            reasons = []
            if match_limited:
                reasons.append("matches")
            if file_limited:
                reasons.append("files")
            if snippets_limited:
                reasons.append("snippets")
            if unreadable:
                reasons.append("unreadable")
            if walk_errors:
                reasons.append("walk_errors")

            def search_header(active_reasons):
                marker = (
                    "+" if any(r in active_reasons for r in ("matches", "files", "chars")) else ""
                )
                header = f"[{len(shown)}{marker} matches for '{pattern[:40]}'"
                if active_reasons:
                    header += (
                        " — incomplete: "
                        + ", ".join(active_reasons)
                        + "; narrow the pattern/path or read the file"
                    )
                return header + "]"

            output, reasons = self._pack_observation_lines(
                search_header, shown, reasons, min(SEARCH_MAX_CHARS, OBSERVE_STATE_CHARS)
            )
            truncated = bool(reasons)
            return ActionResult(
                True, output, details={"truncated": truncated, "truncation_reasons": reasons}
            )
        except Exception as e:
            return self._exception_result(e, "search")

    def _tree(self, action):
        try:
            base = self._resolve(action.get("arg") or ".")
            if not base.is_dir():
                return ActionResult(False, f"Directory not found: {base.name}", "missing_file")
            entries: list[str] = []
            depth_limited = False
            walk_errors = []
            root_depth = len(base.parts)
            for dirpath, dirnames, filenames in os.walk(base, onerror=walk_errors.append):
                depth = len(Path(dirpath).parts) - root_depth
                dirnames[:] = sorted(
                    d for d in dirnames if d not in _REPO_SKIP_DIRS and not d.startswith(".")
                )
                if depth >= TREE_MAX_DEPTH:
                    if dirnames:
                        depth_limited = True
                    dirnames[:] = []
                rel_dir = Path(dirpath).relative_to(base)
                prefix = "" if str(rel_dir) == "." else f"{rel_dir}/"
                entries.extend(f"{prefix}{d}/" for d in dirnames)
                entries.extend(
                    f"{prefix}{name}" for name in sorted(filenames) if not name.startswith(".")
                )
                if len(entries) > TREE_MAX_ENTRIES:
                    break
            entry_limited = len(entries) > TREE_MAX_ENTRIES
            shown = entries[:TREE_MAX_ENTRIES]
            reasons = []
            if entry_limited:
                reasons.append("entries")
            if depth_limited:
                reasons.append("depth")
            if walk_errors:
                reasons.append("walk_errors")

            def tree_header(active_reasons):
                header = f"[tree of {base.name}: {len(shown)} entries"
                if "entries" in active_reasons:
                    header += f", capped at {TREE_MAX_ENTRIES}"
                if active_reasons:
                    header += " — incomplete: " + ", ".join(active_reasons) + "; narrow the path"
                return header + "]"

            output, reasons = self._pack_observation_lines(
                tree_header, shown, reasons, min(TREE_MAX_CHARS, OBSERVE_STATE_CHARS)
            )
            truncated = bool(reasons)
            return ActionResult(
                True, output, details={"truncated": truncated, "truncation_reasons": reasons}
            )
        except Exception as e:
            return self._exception_result(e, "tree")


# One source of truth for action names, categories, allowed/required fields,
# and handlers (issues #36/#79). Control actions have no handler: the run
# controller owns done/fail, and the executor refuses to dispatch them.
ACTION_SPECS: dict[str, ActionSpec] = {
    spec.name: spec
    for spec in (
        ActionSpec(
            "shell",
            "mutate",
            ActionExecutor._shell,
            requires=("arg",),
            allowed=("action", "arg", "timeout", "reasoning"),
        ),
        ActionSpec(
            "write",
            "mutate",
            ActionExecutor._write,
            requires=("arg", "content"),
            allowed=("action", "arg", "content", "append", "reasoning"),
        ),
        ActionSpec(
            "edit",
            "mutate",
            ActionExecutor._edit,
            requires=("arg", "find", "replace"),
            allowed=("action", "arg", "find", "replace", "reasoning"),
        ),
        ActionSpec(
            "read",
            "observe",
            ActionExecutor._read,
            requires=("arg",),
            allowed=("action", "arg", "offset", "limit", "cursor", "sha256", "reasoning"),
        ),
        ActionSpec(
            "search",
            "observe",
            ActionExecutor._search,
            requires=("arg",),
            allowed=("action", "arg", "path", "reasoning"),
        ),
        ActionSpec(
            "tree",
            "observe",
            ActionExecutor._tree,
            allowed=("action", "arg", "reasoning"),
        ),
        ActionSpec(
            "done",
            "control",
            allowed=("action", "arg", "reasoning"),
        ),
        ActionSpec(
            "fail",
            "control",
            allowed=("action", "arg", "reasoning"),
        ),
    )
}


_RESERVED_ACTION_FIELDS = frozenset(
    {
        "content_truncated",
        "finish_reason",
        "transport",
        "truncated_write",
    }
)


def _action_error(message, field=None, error_type="malformed_action"):
    return ActionProtocolError(error_type=error_type, message=message, field=field)


def _require_string(obj, field, *, nonempty=True):
    value = obj[field]
    if not isinstance(value, str):
        return _action_error(f"field '{field}' must be a string", field)
    if nonempty and not value.strip():
        return _action_error(f"field '{field}' must be a non-empty string", field)
    return None


def _require_int(obj, field, minimum, maximum):
    value = obj[field]
    if isinstance(value, bool) or not isinstance(value, int):
        return _action_error(f"field '{field}' must be an integer", field)
    if not minimum <= value <= maximum:
        return _action_error(f"field '{field}' must be between {minimum} and {maximum}", field)
    return None


def parse_action_envelope(obj):
    """Parse one model action without mutation or side effects.

    This is the executable action contract for reply decode, controller
    intake, and defensive dispatch.  It owns allowed names, field types and
    ranges, reserved metadata, and cross-field continuation rules.  Every
    malformed value returns :class:`ActionProtocolError`; it never raises.
    """
    if isinstance(obj, DecodedAction):
        obj = obj.envelope
    if not isinstance(obj, Mapping):
        return _action_error("action envelope must be an object")

    try:
        raw = {key: copy.deepcopy(value) for key, value in obj.items()}
    except Exception:
        return _action_error("action envelope could not be copied safely")
    action_name = raw.get("action")
    if not isinstance(action_name, str) or not action_name.strip():
        return _action_error("field 'action' must be a known non-empty string", "action")
    spec = ACTION_SPECS.get(action_name)
    if spec is None:
        return _action_error(
            f"unknown action: {action_name}", "action", error_type="unknown_action"
        )

    # Deliberate legacy normalization: optional JSON null means the field was
    # omitted for free-form reasoning and for the optional arg on tree/control
    # actions.  Required command/path/search args remain strictly typed.
    if "reasoning" in raw and raw["reasoning"] is None:
        raw.pop("reasoning")
    if action_name in ("tree", "done", "fail") and "arg" in raw and raw["arg"] is None:
        raw.pop("arg")

    reserved = sorted(
        key
        for key in raw
        if isinstance(key, str) and (key.startswith("_") or key in _RESERVED_ACTION_FIELDS)
    )
    if reserved:
        field = reserved[0]
        return _action_error(f"field '{field}' is reserved for controller metadata", field)
    non_string_keys = [key for key in raw if not isinstance(key, str)]
    if non_string_keys:
        return _action_error("action field names must be strings")
    extras = sorted(set(raw) - set(spec.allowed))
    if extras:
        field = extras[0]
        return _action_error(f"field '{field}' is not allowed for action '{action_name}'", field)
    for field in spec.requires:
        if field not in raw:
            return _action_error(f"action '{action_name}' requires field '{field}'", field)

    string_fields = ("arg", "find", "replace", "path", "sha256", "reasoning")
    for field in string_fields:
        if field not in raw:
            continue
        optional_arg = field == "arg" and action_name in ("tree", "done", "fail")
        nonempty = field not in ("replace", "reasoning") and not optional_arg
        error = _require_string(raw, field, nonempty=nonempty)
        if error is not None:
            return error

    if action_name == "write":
        content = raw["content"]
        if not isinstance(content, (str, dict, list)):
            return _action_error("field 'content' must be a string, object, or list", "content")
        if "append" in raw and not isinstance(raw["append"], bool):
            return _action_error("field 'append' must be a boolean", "append")
    if "timeout" in raw:
        error = _require_int(raw, "timeout", 5, SHELL_TIMEOUT_MAX)
        if error is not None:
            return error
    if "offset" in raw:
        error = _require_int(raw, "offset", 1, READ_POSITION_MAX)
        if error is not None:
            return error
    if "limit" in raw:
        error = _require_int(raw, "limit", 1, READ_LIMIT_MAX)
        if error is not None:
            return error
    if "cursor" in raw:
        error = _require_int(raw, "cursor", 0, READ_POSITION_MAX)
        if error is not None:
            return error

    if action_name == "read":
        has_cursor = "cursor" in raw
        if has_cursor:
            for field in ("limit", "sha256"):
                if field not in raw:
                    return _action_error(f"read cursor requires field '{field}'", field)
        elif "sha256" in raw:
            return _action_error("field 'sha256' requires a read cursor", "sha256")

    # Registry order gives stable compatibility projections regardless of
    # the model's object-key order.  Values were deep-copied above, so the
    # returned envelope does not alias the raw response object.
    normalized = tuple((field, raw[field]) for field in spec.allowed if field in raw)
    return ActionEnvelope(normalized)


OBSERVE_ACTIONS = frozenset(
    name for name, spec in ACTION_SPECS.items() if spec.category == "observe"
)


@dataclass(frozen=True)
class SkippedStep:
    """Typed record of a selected action a guard suppressed (issue #69).

    Skips never dispatch and never enter the model-visible window or the
    run-wide structured record; their one projection is the ``step_skipped``
    JSONL event, produced here so the reason taxonomy has a single owner.
    """

    task_index: Any
    step: int
    action: str
    arg: str
    reason: str

    def jsonl_event(self):
        return {
            "event": "step_skipped",
            "task_index": self.task_index,
            "step": self.step,
            "action": self.action,
            "arg": self.arg[:120],
            "reason": self.reason,
        }


class StepReceipt:
    """Internal record of one recorded step (issue #36).

    Owns the state entry — the model-visible sliding window and run-wide
    structured record, including underscore-prefixed guard metadata — plus the
    explicit projections for model history and the JSONL run log.
    """

    def __init__(
        self,
        entry,
        history_action,
        action=None,
        result=None,
        deterministic=None,
        truncated_write=False,
    ):
        self.entry = entry
        self.history_action = history_action
        self.action = action
        self.result = result
        self.deterministic = deterministic  # None | "repair" | "retry"
        self.truncated_write = truncated_write

    @classmethod
    def executed(cls, action, result, working_dir, truncated_write=False):
        """Receipt for a model action that reached the dispatcher."""
        act = action.get("action", "")
        # Truncated-write outputs carry the resume anchor the next step
        # navigates by — observation-class budget, not 100.
        out_cap = OBSERVE_STATE_CHARS if act in OBSERVE_ACTIONS or truncated_write else 100
        entry = {
            "action": act,
            "arg": action.get("arg", ""),
            "ok": result.ok,
            "output": result.output[:out_cap],
        }
        if not result.ok and result.error_type is not None:
            entry["error_type"] = result.error_type
        if act == "shell" and "timeout" in action:
            entry["_timeout"] = action["timeout"]
        if act == "write":
            entry["_content"] = action.get("content", "")
            if action.get("append"):
                entry["_append"] = True
            if truncated_write:
                entry["_truncated_write"] = True
        if act == "edit":
            entry["_find"] = action.get("find", "")
            entry["_replace"] = action.get("replace", "")
        if act in ("write", "edit"):
            target_step = {"arg": action.get("arg", "")}
            if act == "write" and action.get("append"):
                target_step["append"] = True
            target = _mutation_target_key(target_step, working_dir)
            if target is not None:
                entry["_target"] = target
                if act == "write" and truncated_write:
                    entry["_recovery_arg"] = _target_recovery_arg(target, working_dir)
        if act == "read":
            entry["_read_key"] = _read_key(action)
            if result.get("continuation"):
                entry["_continuation"] = result.get("continuation")
        history_action = action.to_dict() if isinstance(action, ActionEnvelope) else dict(action)
        return cls(
            entry,
            history_action,
            action=action,
            result=result,
            truncated_write=truncated_write,
        )

    @classmethod
    def deterministic_repair(cls, action, result):
        """Receipt for the #41 compile-repair rule's dispatched action.

        The rule proposes a normal action and the controller dispatches it
        through the action executor, so ``ok`` reflects the real dispatch
        result. A successful receipt keeps the human description (the
        action's ``reasoning``) that downstream task matching reads; a
        refused dispatch keeps its actionable error output instead."""
        if result.ok:
            output = action.get("reasoning", "") or result.output[:100]
        else:
            output = result.output[:100]
        entry = {
            "action": action.get("action", ""),
            "arg": action.get("arg", ""),
            "ok": result.ok,
            "output": output,
            "deterministic_repair": True,
        }
        # No model action produced this step: history shows the entry itself.
        return cls(entry, entry, action=action, result=result, deterministic="repair")

    @classmethod
    def deterministic_retry(cls, action, result):
        """Receipt for the scaffold-initiated shell retry after a repair."""
        entry = {
            "action": "shell",
            "arg": action.get("arg", ""),
            "ok": result.ok,
            "output": result.output[:100],
            "deterministic_retry": True,
        }
        if not result.ok and result.error_type is not None:
            entry["error_type"] = result.error_type
        history_action = {"action": "shell", "arg": action.get("arg", "")}
        return cls(entry, history_action, action=action, result=result, deterministic="retry")

    def history_event(self, task_index, step):
        event = {
            "event": "step",
            "task": task_index,
            "step": step,
            "action": self.history_action,
            "result": {"ok": self.entry["ok"], "output": self.entry["output"][:100]},
        }
        if self.deterministic == "repair":
            event["deterministic_repair"] = True
        elif self.deterministic == "retry":
            event["deterministic_retry"] = True
        return event

    def jsonl_event(self, task_index, step, wall_s):
        if self.deterministic == "repair":
            return {
                "event": "deterministic_repair",
                "kind": "compile_include",
                "file": self.entry["arg"],
                "description": self.entry["output"],
                "ok": self.entry["ok"],
            }
        record = {
            "event": "step",
            "task_index": task_index,
            "step": step,
            "action": self.entry["action"],
            "arg": self.entry["arg"][:120],
            "ok": self.entry["ok"],
            "error_type": self.result.error_type if self.result else None,
        }
        if self.deterministic == "retry":
            record["deterministic_retry"] = True
        record["wall_s"] = wall_s
        if self.result and self.result.get("truncated"):
            record["truncated"] = True
        # Bounded discovery is only auditable when the record says why it
        # stopped (issue #42): keep the compact cap reasons.
        if self.result and self.result.get("truncation_reasons"):
            record["truncation_reasons"] = self.result.get("truncation_reasons")
        if self.truncated_write:
            record["truncated_write"] = True
        # Hash-linked read audit: which content, how much of it, and where the
        # window ended.
        if self.entry["action"] == "read" and self.result and self.result.get("sha256"):
            record["sha256"] = self.result.get("sha256")
            record["total_lines"] = self.result.get("total_lines")
            record["total_bytes"] = self.result.get("total_bytes")
            record["total_chars"] = self.result.get("total_chars")
            record["continuation"] = self.result.get("continuation")
        return record
