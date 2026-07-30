"""Best-effort multi-file transaction for no-database agent authoring."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class AgentFileTransaction:
    """Stage several files and apply/restore them as one best-effort unit.

    This helper is used only when database persistence is disabled and files
    are the sole source of truth. If a normal file operation raises,
    :meth:`rollback` restores every pre-existing target; :meth:`finish`
    removes backups after all replacements succeed. It is not a cross-resource
    transaction or a crash-recovery journal.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._directory_existed = directory.exists()
        self._staged: list[tuple[Path, Path]] = []
        self._backups: dict[Path, Path | None] = {}
        self._applied = False

    def stage_text(self, target: Path, text: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            dir=target.parent,
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        )
        try:
            handle.write(text)
            handle.flush()
            handle.close()
            self._staged.append((Path(handle.name), target))
        except BaseException:
            handle.close()
            Path(handle.name).unlink(missing_ok=True)
            raise

    def apply(self) -> None:
        if self._applied:
            return
        try:
            for staged, target in self._staged:
                backup: Path | None = None
                if target.exists():
                    fd = tempfile.NamedTemporaryFile(dir=target.parent, suffix=".bak", delete=False)
                    fd.close()
                    backup = Path(fd.name)
                    backup.unlink(missing_ok=True)
                    target.replace(backup)
                self._backups[target] = backup
                staged.replace(target)
            self._applied = True
        except BaseException:
            self.rollback()
            raise

    def rollback(self) -> None:
        for _staged, target in reversed(self._staged):
            backup = self._backups.get(target)
            try:
                if target in self._backups:
                    target.unlink(missing_ok=True)
                if backup is not None and backup.exists():
                    # Use the primitive directly so recovery does not depend on
                    # the same Path.replace wrapper that may have just failed.
                    os.replace(backup, target)
            except OSError:
                logger.exception("Failed to restore agent compatibility file %s", target)
        self._cleanup_artifacts()
        if not self._directory_existed:
            try:
                self.directory.rmdir()
            except OSError:
                pass
        self._applied = False
        self._staged.clear()
        self._backups.clear()

    def finish(self) -> None:
        self._cleanup_artifacts()
        self._staged.clear()
        self._backups.clear()

    def _cleanup_artifacts(self) -> None:
        for staged, _target in self._staged:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                logger.debug("Failed to remove staged agent file %s", staged, exc_info=True)
        for backup in self._backups.values():
            if backup is None:
                continue
            try:
                backup.unlink(missing_ok=True)
            except OSError:
                logger.debug("Failed to remove agent file backup %s", backup, exc_info=True)
