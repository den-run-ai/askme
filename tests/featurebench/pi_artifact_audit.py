#!/usr/bin/env python3
"""Validate a quiescent artifact tree before reading it, then hash and scan it.

Destroy model containers before calling this helper. The two-pass check rejects
symlinks and special files throughout the tree before any content read. It does
not claim protection against concurrent, adversarial directory replacement.
The inventory has relative paths only; no artifact contents or credentials are
returned. Keep CLI output outside the audited tree to avoid changing that tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

CHUNK_BYTES = 64 * 1024


class ArtifactAuditError(ValueError):
    """A generic audit failure that never exposes paths or credential values."""


def _validate_tree(root: Path, secret: bytes) -> list[tuple[Path, os.stat_result]]:
    """First pass: reject unsafe entries everywhere, without opening any file."""
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ArtifactAuditError("Artifact audit requires a real directory root.")
    files: list[tuple[Path, os.stat_result]] = []

    def walk_error(_error):
        raise ArtifactAuditError("Artifact audit could not inspect the complete tree.")

    for parent, directories, filenames in os.walk(root, followlinks=False, onerror=walk_error):
        directories.sort()
        filenames.sort()
        for name in (*directories, *filenames):
            path = Path(parent) / name
            relative = path.relative_to(root).as_posix()
            if secret in os.fsencode(relative):
                raise ArtifactAuditError("Artifact audit detected credential material.")
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise ArtifactAuditError("Artifact audit rejects symbolic links.")
            if stat.S_ISDIR(metadata.st_mode):
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ArtifactAuditError(
                    "Artifact audit accepts only directories and regular files."
                )
            files.append((path, metadata))
    return sorted(files, key=lambda entry: entry[0].relative_to(root).as_posix())


def _hash_file(path: Path, expected: os.stat_result, secret: bytes) -> tuple[int, str]:
    """Second pass: stream one already-validated file; match across chunk edges."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        ) != (expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns):
            raise ArtifactAuditError("Artifact tree changed during the audit.")
        digest = hashlib.sha256()
        total = 0
        tail = b""
        while chunk := stream.read(CHUNK_BYTES):
            window = tail + chunk
            if secret in window:
                raise ArtifactAuditError("Artifact audit detected credential material.")
            tail = window[-(len(secret) - 1) :] if len(secret) > 1 else b""
            digest.update(chunk)
            total += len(chunk)
        after = os.fstat(stream.fileno())
        if total != expected.st_size or after.st_mtime_ns != expected.st_mtime_ns:
            raise ArtifactAuditError("Artifact tree changed during the audit.")
    return total, digest.hexdigest()


def audit_artifacts(root: Path | str, secret: bytes) -> dict:
    """Return a SHA-256 inventory, or fail without returning a partial inventory.

    All entries, including hidden entries and dangling links, are inspected in
    pass one. Pass two reads only regular files. ``secret`` must be nonempty;
    matches in filenames or file bytes fail with a generic message.
    """
    if not isinstance(secret, bytes) or not secret:
        raise ArtifactAuditError("Artifact audit requires a nonempty credential.")
    try:
        root = Path(root)
        candidates = _validate_tree(root, secret)
        inventory = []
        total = 0
        for path, metadata in candidates:
            size, digest = _hash_file(path, metadata, secret)
            inventory.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size_bytes": size,
                    "sha256": digest,
                }
            )
            total += size
        result = {
            "schema_version": 1,
            "file_count": len(inventory),
            "total_bytes": total,
            "files": inventory,
        }
        if secret in json.dumps(result, ensure_ascii=True).encode():
            raise ArtifactAuditError("Artifact audit detected credential material.")
        return result
    except OSError:
        raise ArtifactAuditError(
            "Artifact audit could not inspect or read the complete tree."
        ) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    secret = os.environ.get("OPENROUTER_API_KEY", "").encode()
    try:
        inventory = audit_artifacts(args.root, secret)
    except ArtifactAuditError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(inventory, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
