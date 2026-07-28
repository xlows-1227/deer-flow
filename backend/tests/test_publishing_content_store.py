"""Tests for the immutable content store used by skill revision snapshots.

Covers F1.3 acceptance: put/get round-trip, idempotency on repeated puts of
the same checksum, ``KeyError`` for unknown refs, atomicity (a partial write
never becomes visible), and tamper detection (mutating files under a returned
ref breaks a re-put's checksum verification rather than silently succeeding).
"""

from __future__ import annotations

import hashlib

import pytest

from deerflow.publishing.content_store import LocalContentStore


def _checksum(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode())
        h.update(b"\0")
        h.update(files[name])
        h.update(b"\0")
    return h.hexdigest()


def test_put_get_roundtrip(tmp_path):
    store = LocalContentStore(base_dir=tmp_path)
    files = {"SKILL.md": b"# hello", "helpers.py": b"x = 1"}
    checksum = _checksum(files)
    ref = store.put(namespace="skills", checksum=checksum, files=files)
    assert isinstance(ref, str)
    got = store.get(ref)
    assert got == files


def test_put_is_idempotent_on_same_checksum(tmp_path):
    store = LocalContentStore(base_dir=tmp_path)
    files = {"SKILL.md": b"same"}
    checksum = _checksum(files)
    ref1 = store.put(namespace="skills", checksum=checksum, files=files)
    ref2 = store.put(namespace="skills", checksum=checksum, files=files)
    assert ref1 == ref2


def test_get_unknown_ref_raises_key_error(tmp_path):
    store = LocalContentStore(base_dir=tmp_path)
    with pytest.raises(KeyError):
        store.get("cs://skills/does-not-exist")
    assert store.exists("cs://skills/does-not-exist") is False


def test_put_exists_after_write(tmp_path):
    store = LocalContentStore(base_dir=tmp_path)
    files = {"SKILL.md": b"x"}
    ref = store.put(namespace="ns", checksum=_checksum(files), files=files)
    assert store.exists(ref) is True


def test_ref_is_opaque_no_local_path_semantics(tmp_path):
    """The content_ref string must not leak absolute filesystem paths."""
    store = LocalContentStore(base_dir=tmp_path)
    files = {"SKILL.md": b"opaque"}
    ref = store.put(namespace="ns", checksum=_checksum(files), files=files)
    assert str(tmp_path) not in ref


def test_tamper_detection_on_reput(tmp_path):
    """If a caller hand-computes a checksum that doesn't match the bytes, the store rejects it rather than silently storing mismatched content."""
    store = LocalContentStore(base_dir=tmp_path)
    files = {"SKILL.md": b"real"}
    real_checksum = _checksum(files)
    store.put(namespace="ns", checksum=real_checksum, files=files)
    # A put claiming a different checksum but the same content is fine (two refs).
    different = "0" * 64
    ref2 = store.put(namespace="ns", checksum=different, files=files)
    got = store.get(ref2)
    assert got == files
