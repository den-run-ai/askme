"""Artifact audit regressions: no provider requests or credentials are used."""

import hashlib
import json
import os

import pytest

from tests.featurebench import pi_artifact_audit as audit

SECRET = b"dummy-only-artifact-audit-credential"


def test_ordinary_and_hidden_artifacts_have_exact_inventory(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "nested").mkdir()
    (root / ".internal").mkdir()
    files = {
        "trajectory.jsonl": b'{"type":"done"}\n',
        "nested/patch.diff": b"+implementation\n",
        ".hidden": b"hidden metadata",
        ".internal/receipt": b"verified",
        "empty": b"",
    }
    for relative, content in files.items():
        (root / relative).write_bytes(content)
    result = audit.audit_artifacts(root, SECRET)
    assert result == {
        "schema_version": 1,
        "file_count": len(files),
        "total_bytes": sum(map(len, files.values())),
        "files": [
            {
                "path": relative,
                "size_bytes": len(files[relative]),
                "sha256": hashlib.sha256(files[relative]).hexdigest(),
            }
            for relative in sorted(files)
        ],
    }
    assert str(root) not in json.dumps(result)


@pytest.mark.parametrize("link_kind", ["file", "directory", "dangling"])
def test_any_symlink_rejects_entire_tree_before_any_content_read(tmp_path, monkeypatch, link_kind):
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "a-ordinary.log").write_bytes(b"must not be read before full validation")
    outside = tmp_path / "outside"
    if link_kind == "directory":
        outside.mkdir()
        (outside / "protected").write_bytes(SECRET)
    elif link_kind == "file":
        outside.write_bytes(SECRET)
    # The unsafe entry sorts after the normal file and is nested, so checking
    # each file immediately before reading would fail this regression.
    nested = root / "z-nested"
    nested.mkdir()
    (nested / "link").symlink_to(outside, target_is_directory=link_kind == "directory")
    reads = []

    def unexpected_read(*args):
        reads.append(args)
        raise AssertionError("Content was opened before tree validation finished")

    monkeypatch.setattr(audit, "_hash_file", unexpected_read)
    with pytest.raises(audit.ArtifactAuditError, match="symbolic links"):
        audit.audit_artifacts(root, SECRET)
    assert reads == []


def test_symlink_directory_is_not_traversed(tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "protected").write_bytes(SECRET)
    (root / "linked-directory").symlink_to(outside, target_is_directory=True)
    scanned = []
    real_scandir = os.scandir

    def tracked_scandir(path):
        scanned.append(os.fspath(path))
        return real_scandir(path)

    monkeypatch.setattr(audit.os, "scandir", tracked_scandir)
    with pytest.raises(audit.ArtifactAuditError, match="symbolic links"):
        audit.audit_artifacts(root, SECRET)
    assert scanned == [str(root)]


@pytest.mark.parametrize("root_kind", ["link", "file", "missing"])
def test_root_must_be_a_real_directory(tmp_path, root_kind):
    root = tmp_path / "root"
    if root_kind == "link":
        actual = tmp_path / "actual"
        actual.mkdir()
        root.symlink_to(actual, target_is_directory=True)
    elif root_kind == "file":
        root.write_bytes(b"not a directory")
    with pytest.raises(audit.ArtifactAuditError):
        audit.audit_artifacts(root, SECRET)


def test_special_file_is_rejected_without_opening_it(tmp_path, monkeypatch):
    os.mkfifo(tmp_path / "pipe")

    def unexpected_read(*_args):
        raise AssertionError("A special file must never be opened")

    monkeypatch.setattr(audit, "_hash_file", unexpected_read)
    with pytest.raises(audit.ArtifactAuditError, match="regular files"):
        audit.audit_artifacts(tmp_path, SECRET)


def test_credential_detection_is_generic_and_no_partial_inventory_is_printed(
    tmp_path, capsys, monkeypatch
):
    (tmp_path / "safe.json").write_bytes(b"{}")
    (tmp_path / "secret.log").write_bytes(b"prefix " + SECRET + b" suffix")
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET.decode())
    assert audit.main([str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Artifact audit detected credential material.\n"
    assert SECRET.decode() not in captured.err
    assert str(tmp_path) not in captured.err


def test_credential_match_across_read_boundaries_is_not_missed(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "CHUNK_BYTES", 8)
    (tmp_path / "response.bin").write_bytes(b"prefix" + SECRET + b"suffix")
    with pytest.raises(audit.ArtifactAuditError, match="credential material"):
        audit.audit_artifacts(tmp_path, SECRET)


def test_credential_in_filename_is_rejected_before_inventory_output(tmp_path):
    (tmp_path / SECRET.decode()).write_bytes(b"ordinary contents")
    with pytest.raises(audit.ArtifactAuditError, match="credential material") as caught:
        audit.audit_artifacts(tmp_path, SECRET)
    assert SECRET.decode() not in str(caught.value)


@pytest.mark.parametrize("secret", [b"", "not-bytes", None])
def test_missing_credential_fails_closed(tmp_path, secret):
    with pytest.raises(audit.ArtifactAuditError, match="nonempty credential"):
        audit.audit_artifacts(tmp_path, secret)


def test_successful_cli_prints_only_inventory(tmp_path, capsys, monkeypatch):
    (tmp_path / "result.json").write_bytes(b"{}")
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET.decode())
    assert audit.main([str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == audit.audit_artifacts(tmp_path, SECRET)


def test_read_failure_is_generic_and_does_not_return_partial_inventory(tmp_path, monkeypatch):
    (tmp_path / "first").write_bytes(b"ordinary")
    (tmp_path / "second").write_bytes(b"ordinary")
    original = audit._hash_file

    def failed_read(path, expected, secret):
        if path.name == "second":
            raise PermissionError("credential " + SECRET.decode())
        return original(path, expected, secret)

    monkeypatch.setattr(audit, "_hash_file", failed_read)
    with pytest.raises(audit.ArtifactAuditError) as caught:
        audit.audit_artifacts(tmp_path, SECRET)
    assert SECRET.decode() not in str(caught.value)
    assert "complete tree" in str(caught.value)
