"""Immutable content store for skill-revision file snapshots.

A content store holds a set of files keyed by a content checksum. Writes are
idempotent — putting the same checksum twice returns the same opaque reference
and never duplicates storage. The reference (``content_ref``) is an opaque
string of the form ``cs://<namespace>/<checksum>`` with no local-path
semantics, so the backing implementation can be swapped to an object store
later without touching call sites.

The local implementation writes atomically: bytes are staged to a sibling
temporary directory and then ``os.replace``-d into place, so a crashed or
interrupted write never leaves a half-written snapshot visible to ``get``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Protocol


class ImmutableContentStore(Protocol):
    """Versioned, write-once store for file snapshots keyed by checksum."""

    def put(self, namespace: str, checksum: str, files: dict[str, bytes]) -> str:
        """Persist ``files`` under ``(namespace, checksum)``; return an opaque content_ref.

        Idempotent: putting the same checksum again returns the same ref and
        does not overwrite or duplicate.
        """
        ...

    def get(self, content_ref: str) -> dict[str, bytes]:
        """Read back the files for ``content_ref``. Raises ``KeyError`` if absent."""
        ...

    def exists(self, content_ref: str) -> bool:
        """True if ``content_ref`` has been written and is still readable."""
        ...


def _ref(namespace: str, checksum: str) -> str:
    return f"cs://{namespace}/{checksum}"


class LocalContentStore:
    """Filesystem-backed implementation of :class:`ImmutableContentStore`.

    Layout::

        {base_dir}/content-store/{namespace}/{checksum[:2]}/{checksum}/<files>

    The first two hex chars of the checksum are sharded into the path to avoid
    a single flat directory. Once written, a snapshot directory is treated as
    read-only; ``put`` never mutates an existing one.
    """

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir) / "content-store"

    def _snapshot_dir(self, namespace: str, checksum: str) -> Path:
        return self._base / namespace / checksum[:2] / checksum

    def put(self, namespace: str, checksum: str, files: dict[str, bytes]) -> str:
        target = self._snapshot_dir(namespace, checksum)
        if target.exists():
            # Idempotent reuse: do not touch an existing snapshot.
            return _ref(namespace, checksum)
        # Guard early against path-unsafe characters so callers get a clear
        # error rather than a platform-specific OSError deep in pathlib.
        if not namespace or not checksum or "/" in namespace or "/" in checksum or ":" in checksum:
            raise ValueError(f"unsafe content-store key: namespace={namespace!r} checksum={checksum!r}")
        # Atomic write: stage into a sibling temp dir, then rename into place.
        # We never pre-create ``target`` so the final rename is always
        # "directory does not exist" -> "directory exists", which is the only
        # flavour of os.replace(dir, dir) that is reliable on Windows.
        staging = target.with_name(target.name + ".tmp")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        try:
            for name, data in files.items():
                # Guard against path traversal in the stored file names.
                rel = Path(name)
                if rel.is_absolute() or ".." in rel.parts:
                    raise ValueError(f"unsafe content path: {name!r}")
                dest = staging / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            os.replace(staging, target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return _ref(namespace, checksum)

    def get(self, content_ref: str) -> dict[str, bytes]:
        namespace, checksum = self._parse_ref(content_ref)
        snapshot = self._snapshot_dir(namespace, checksum)
        if not snapshot.exists():
            raise KeyError(content_ref)
        result: dict[str, bytes] = {}
        for path in snapshot.rglob("*"):
            if path.is_file():
                result[str(path.relative_to(snapshot)).replace(os.sep, "/")] = path.read_bytes()
        return result

    def exists(self, content_ref: str) -> bool:
        try:
            namespace, checksum = self._parse_ref(content_ref)
        except ValueError:
            return False
        return self._snapshot_dir(namespace, checksum).exists()

    @staticmethod
    def _parse_ref(content_ref: str) -> tuple[str, str]:
        if not content_ref.startswith("cs://"):
            raise ValueError(f"invalid content_ref: {content_ref!r}")
        rest = content_ref[len("cs://") :]
        parts = rest.split("/", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"invalid content_ref: {content_ref!r}")
        return parts[0], parts[1]


_DEFAULT_STORE: LocalContentStore | None = None


def get_content_store(base_dir: str | Path | None = None) -> ImmutableContentStore:
    """Factory returning the process-wide content store.

    Reads no configuration in the first version: callers may pass an explicit
    ``base_dir`` (tests do). A future revision can consult the
    ``publishing.content_store`` config block to pick a different backend
    without changing call sites.
    """
    global _DEFAULT_STORE
    if base_dir is not None:
        return LocalContentStore(base_dir=base_dir)
    if _DEFAULT_STORE is None:
        from deerflow.config.paths import get_paths

        _DEFAULT_STORE = LocalContentStore(base_dir=get_paths().base_dir)
    return _DEFAULT_STORE
