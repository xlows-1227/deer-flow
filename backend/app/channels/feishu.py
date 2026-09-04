"""Feishu/Lark channel — connects to Feishu via WebSocket (no public IP needed)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import hmac
import json
import logging
import multiprocessing
import re
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from email.message import Message
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import quote

import httpx
from filelock import FileLock

from app.channels.base import Channel
from app.channels.commands import KNOWN_CHANNEL_COMMANDS
from app.channels.contracts import EventDeduplicator as FeishuEventDeduplicator
from app.channels.message_bus import InboundMessage, InboundMessageType, MessageBus, OutboundMessage, ResolvedAttachment
from deerflow.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from deerflow.persistence.channel_mapping import SYSTEM_CHANNEL_MAPPING_SCOPE
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sandbox.sandbox_provider import SandboxAcquisition, get_sandbox_provider

logger = logging.getLogger(__name__)

FEISHU_INBOUND_FILE_MAX_BYTES = 50 * 1024 * 1024
FEISHU_PUBLISHED_INBOUND_MAX_FILES = 10
FEISHU_WEBSOCKET_CONNECT_TIMEOUT_SECONDS = 15.0
FEISHU_ENDPOINT_CONNECT_TIMEOUT_SECONDS = 5.0
FEISHU_ENDPOINT_READ_TIMEOUT_SECONDS = 10.0
FEISHU_PUBLISHED_DOWNLOAD_TIMEOUT_SECONDS = 60.0
FEISHU_SANDBOX_ACQUIRE_TIMEOUT_SECONDS = 15.0
FEISHU_SANDBOX_LATE_ACQUIRE_TIMEOUT_SECONDS = 30.0
FEISHU_SANDBOX_LATE_ACQUIRE_CANCEL_DRAIN_SECONDS = 0.5
FEISHU_SANDBOX_SYNC_FILE_TIMEOUT_SECONDS = 60.0
FEISHU_SANDBOX_SYNC_BATCH_TIMEOUT_SECONDS = 120.0
FEISHU_SANDBOX_SYNC_CLEANUP_TIMEOUT_SECONDS = 2.0
FEISHU_ATTACHMENT_CLEANUP_DRAIN_TIMEOUT_SECONDS = 2.0
FEISHU_ATTACHMENT_DELETE_MAX_ATTEMPTS = 3
FEISHU_ATTACHMENT_DELETE_RETRY_SECONDS = 0.05
FEISHU_ATTACHMENT_CLEANUP_RETRY_INTERVAL_SECONDS = 30.0
FEISHU_ATTACHMENT_PRODUCER_LEASE_SECONDS = 30.0
FEISHU_ATTACHMENT_PRODUCER_HEARTBEAT_SECONDS = 5.0
FEISHU_ATTACHMENT_RECOVERY_MAX_JOBS = 25
FEISHU_ATTACHMENT_RECOVERY_MAX_CONCURRENCY = 4
FEISHU_ATTACHMENT_RECOVERY_TIMEOUT_SECONDS = 10.0
FEISHU_ATTACHMENT_DELETE_TIMEOUT_SECONDS = 2.0
FEISHU_ATTACHMENT_CLAIM_LEASE_SECONDS = 15.0
FEISHU_ATTACHMENT_CLEANUP_JOB_MAX_BYTES = 1024 * 1024
FEISHU_ATTACHMENT_BACKLOG_SCAN_TIMEOUT_SECONDS = 2.0
FEISHU_ATTACHMENT_BACKLOG_SCANNER_WORKERS = 2
FEISHU_ATTACHMENT_BACKLOG_SCANNER_MAINTENANCE_SECONDS = 1.0
FEISHU_ATTACHMENT_BACKLOG_SCANNER_RETRY_SECONDS = 0.1
FEISHU_ATTACHMENT_BACKLOG_SCANNER_MAX_RETRY_SECONDS = 5.0
FEISHU_ATTACHMENT_BACKLOG_SCANNER_READY_POLL_SECONDS = 0.05
FEISHU_ATTACHMENT_BACKLOG_SCANNER_SHUTDOWN_SECONDS = 2.0
FEISHU_ATTACHMENT_CURSOR_REPLACE_RETRY_SECONDS = 0.01

_ACTIVE_ATTACHMENT_PRODUCERS: set[str] = set()
_ACTIVE_ATTACHMENT_CLAIMS: set[str] = set()
_ATTACHMENT_CLEANUP_SCAN_LOCK = threading.Lock()
_ATTACHMENT_CLEANUP_READ_SLOTS = threading.BoundedSemaphore(2)
_ATTACHMENT_CLEANUP_READ_STATE_LOCK = threading.Lock()
_ATTACHMENT_CLEANUP_QUARANTINED_READS: dict[str, concurrent.futures.Future[_PublishedAttachmentCleanupJob]] = {}
_ATTACHMENT_CLEANUP_MAX_QUARANTINED_READERS = 8
_ATTACHMENT_CLEANUP_THREAD_READ_RESERVATIONS = 0


def _replace_cleanup_state_with_deadline(
    source: Path,
    target: Path,
    *,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Retry transient Windows sharing violations without exceeding the caller's budget."""
    initial_remaining = deadline - clock()
    if initial_remaining <= 0:
        raise TimeoutError("Cleanup cursor replacement deadline expired")
    wall_deadline = time.monotonic() + initial_remaining
    last_replace_error: PermissionError | None = None
    while True:
        remaining = min(
            deadline - clock(),
            wall_deadline - time.monotonic(),
        )
        if remaining <= 0:
            if last_replace_error is not None:
                raise last_replace_error
            raise TimeoutError("Cleanup cursor replacement deadline expired")
        try:
            source.replace(target)
            return
        except PermissionError as exc:
            last_replace_error = exc
            remaining = min(
                deadline - clock(),
                wall_deadline - time.monotonic(),
            )
            if remaining <= 0:
                raise
            time.sleep(
                min(
                    FEISHU_ATTACHMENT_CURSOR_REPLACE_RETRY_SECONDS,
                    remaining,
                )
            )


def _cleanup_binding_generation_path(outbox_dir: Path, binding_id: str) -> Path:
    binding_key = hashlib.sha256(binding_id.encode("utf-8")).hexdigest()[:24]
    return outbox_dir / f".store-generation-{binding_key}"


def _cleanup_binding_index_dir(outbox_dir: Path, binding_id: str) -> Path:
    binding_key = hashlib.sha256(binding_id.encode("utf-8")).hexdigest()[:24]
    return outbox_dir / ".binding-index" / binding_key


def _write_cleanup_binding_index(outbox_dir: Path, job: _PublishedAttachmentCleanupJob) -> None:
    index_dir = _cleanup_binding_index_dir(outbox_dir, job.binding_id)
    index_dir.mkdir(parents=True, exist_ok=True)
    target = index_dir / f"{job.job_id}.ref"
    temp_path = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(f"{job.job_id}.json", encoding="utf-8")
        temp_path.replace(target)
    finally:
        temp_path.unlink(missing_ok=True)


def _remove_cleanup_binding_index(outbox_dir: Path, binding_id: str, job_id: str) -> None:
    (_cleanup_binding_index_dir(outbox_dir, binding_id) / f"{job_id}.ref").unlink(missing_ok=True)


def _binding_cleanup_index_has_backlog(outbox_dir: Path, binding_id: str) -> tuple[bool, bool]:
    """Read only one binding's durable index; unknown global corruption stays global."""
    index_dir = _cleanup_binding_index_dir(outbox_dir, binding_id)
    if not index_dir.exists():
        return False, False
    invalid = False
    for index_path in index_dir.glob("*.ref"):
        try:
            filename = index_path.read_text(encoding="utf-8").strip()
            if not filename or Path(filename).name != filename or not filename.endswith(".json"):
                invalid = True
                continue
            if (outbox_dir / filename).exists():
                return True, invalid
            index_path.unlink(missing_ok=True)
        except OSError:
            invalid = True
    return False, invalid


def _read_cleanup_binding_generation(outbox_dir: Path, binding_id: str) -> str:
    if not outbox_dir.exists():
        return ""
    generation_path = _cleanup_binding_generation_path(outbox_dir, binding_id)
    with FileLock(str(generation_path.with_suffix(".lock")), timeout=2.0):
        try:
            return generation_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""


def _bump_cleanup_binding_generation_locked(outbox_dir: Path, binding_id: str) -> None:
    generation_path = _cleanup_binding_generation_path(outbox_dir, binding_id)
    temp_path = generation_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(uuid.uuid4().hex, encoding="utf-8")
        temp_path.replace(generation_path)
    finally:
        temp_path.unlink(missing_ok=True)


def _read_inbound_resource(stream: Any) -> bytes:
    """Read one provider resource without buffering beyond the input limit."""
    content = stream.read(FEISHU_INBOUND_FILE_MAX_BYTES + 1)
    if not isinstance(content, (bytes, bytearray)):
        raise ValueError("Feishu inbound resource did not contain bytes")
    if len(content) > FEISHU_INBOUND_FILE_MAX_BYTES:
        raise ValueError("Feishu inbound resource exceeds size limit")
    return bytes(content)


@dataclass(frozen=True)
class _MaterializedInboundFile:
    virtual_path: str
    actual_path: Path
    size: int


@dataclass(frozen=True)
class _PublishedAttachmentCleanupJob:
    """Durable instructions for removing one rejected sandbox file batch."""

    job_id: str
    binding_id: str
    thread_id: str
    owner_user_id: str
    virtual_paths: tuple[str, ...]
    phase: Literal["producer_pending", "ready_to_delete", "deleting"] = "ready_to_delete"
    producer_token: str | None = None
    producer_lease_expires_at: float | None = None
    claim_token: str | None = None
    claim_lease_expires_at: float | None = None
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "binding_id": self.binding_id,
            "thread_id": self.thread_id,
            "owner_user_id": self.owner_user_id,
            "virtual_paths": list(self.virtual_paths),
            "phase": self.phase,
            "producer_token": self.producer_token,
            "producer_lease_expires_at": self.producer_lease_expires_at,
            "claim_token": self.claim_token,
            "claim_lease_expires_at": self.claim_lease_expires_at,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> _PublishedAttachmentCleanupJob:
        job_id = payload.get("job_id")
        binding_id = payload.get("binding_id")
        thread_id = payload.get("thread_id")
        owner_user_id = payload.get("owner_user_id")
        virtual_paths = payload.get("virtual_paths")
        phase = payload.get("phase", "ready_to_delete")
        producer_token = payload.get("producer_token")
        producer_lease_expires_at = payload.get("producer_lease_expires_at")
        claim_token = payload.get("claim_token")
        claim_lease_expires_at = payload.get("claim_lease_expires_at")
        version = payload.get("version", 1)
        if not all(isinstance(value, str) for value in (job_id, binding_id, thread_id, owner_user_id)):
            raise ValueError("attachment cleanup job identifiers are invalid")
        if not job_id or not thread_id or not owner_user_id:
            raise ValueError("attachment cleanup job identifiers are empty")
        if not isinstance(virtual_paths, list) or not virtual_paths:
            raise ValueError("attachment cleanup job has no files")
        if phase not in {"producer_pending", "ready_to_delete", "deleting"}:
            raise ValueError("attachment cleanup job phase is invalid")
        if producer_token is not None and not isinstance(producer_token, str):
            raise ValueError("attachment cleanup producer token is invalid")
        if producer_lease_expires_at is not None and not isinstance(producer_lease_expires_at, (int, float)):
            raise ValueError("attachment cleanup producer lease is invalid")
        if claim_token is not None and not isinstance(claim_token, str):
            raise ValueError("attachment cleanup claim token is invalid")
        if claim_lease_expires_at is not None and not isinstance(claim_lease_expires_at, (int, float)):
            raise ValueError("attachment cleanup claim lease is invalid")
        if not isinstance(version, int) or version < 1:
            raise ValueError("attachment cleanup job version is invalid")
        if phase == "producer_pending" and (not producer_token or producer_lease_expires_at is None):
            raise ValueError("pending attachment cleanup job has no producer lease")
        if phase == "deleting" and (not claim_token or claim_lease_expires_at is None):
            raise ValueError("deleting attachment cleanup job has no claim lease")
        normalized_paths: list[str] = []
        for virtual_path in virtual_paths:
            if not isinstance(virtual_path, str) or not virtual_path.startswith(f"{VIRTUAL_PATH_PREFIX}/uploads/"):
                raise ValueError("attachment cleanup job path is outside uploads")
            normalized_paths.append(virtual_path)
        return cls(
            job_id=job_id,
            binding_id=binding_id,
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            virtual_paths=tuple(normalized_paths),
            phase=phase,
            producer_token=producer_token,
            producer_lease_expires_at=float(producer_lease_expires_at) if producer_lease_expires_at is not None else None,
            claim_token=claim_token,
            claim_lease_expires_at=float(claim_lease_expires_at) if claim_lease_expires_at is not None else None,
            version=version,
        )


def _scan_published_attachment_cleanup_backlog(outbox_value: str, binding_id: str) -> bool:
    """Enumerate, stat, read and parse entirely inside a disposable worker."""
    outbox_dir = Path(outbox_value)
    if not outbox_dir.exists():
        return False
    try:
        paths = outbox_dir.glob("*.json")
        for path in paths:
            try:
                job = FeishuChannel._read_attachment_cleanup_job(path)
            except FileNotFoundError:
                continue
            except BaseException:
                return True
            if path.stem != job.job_id or job.binding_id == binding_id:
                return True
    except BaseException:
        return True
    return False


def _attachment_backlog_scanner_process(connection: Any) -> None:
    """Serve whole-scan requests in a worker the parent may terminate."""
    try:
        connection.send(("ready",))
        while True:
            request = connection.recv()
            if request == ("stop",):
                return
            if not isinstance(request, tuple) or len(request) != 4 or request[0] != "scan":
                continue
            _, request_id, outbox_value, binding_id = request
            result = _scan_published_attachment_cleanup_backlog(str(outbox_value), str(binding_id))
            connection.send(("ok", request_id, result))
    except (EOFError, BrokenPipeError, OSError):
        return
    finally:
        connection.close()


@dataclass
class _AttachmentBacklogScannerSlot:
    process: Any
    connection: Any


class _AttachmentBacklogScannerStopping(RuntimeError):
    """Internal signal used to unwind an in-flight replenish during shutdown."""


class _PublishedAttachmentBacklogScanner:
    """A prestarted pool keeping all filesystem work outside DELETE threads."""

    def __init__(self, *, worker_count: int = FEISHU_ATTACHMENT_BACKLOG_SCANNER_WORKERS) -> None:
        self._worker_count = worker_count
        self._slots: list[_AttachmentBacklogScannerSlot] = []
        self._lock = threading.Lock()
        self._spawning = False
        self._stopping = False
        self._maintenance_stop = threading.Event()
        self._replenish_needed = threading.Event()
        self._maintenance_thread: threading.Thread | None = None

    def start(self) -> None:
        """Prestart workers and their non-request-path pool manager."""
        with self._lock:
            if self._maintenance_thread is not None and self._maintenance_thread.is_alive():
                if self._stopping:
                    raise RuntimeError("attachment backlog scanner is still stopping")
                return
            if self._stopping and any(self._process_liveness(slot) is not False for slot in self._slots):
                raise RuntimeError("attachment backlog scanner is still stopping")
            self._stopping = False
            self._maintenance_stop.clear()
        self._replenish()
        with self._lock:
            if self._maintenance_thread is not None and self._maintenance_thread.is_alive():
                return
            maintenance_thread = threading.Thread(
                target=self._maintain,
                name="feishu-attachment-backlog-scanner-manager",
                daemon=True,
            )
            self._maintenance_thread = maintenance_thread
            maintenance_thread.start()

    def _replenish(self) -> None:
        """Restore pool capacity without holding the request-facing lock while spawning."""
        dead_slots: list[_AttachmentBacklogScannerSlot] = []
        with self._lock:
            if self._stopping or self._spawning:
                return
            live_slots: list[_AttachmentBacklogScannerSlot] = []
            for slot in self._slots:
                if self._process_liveness(slot) is True:
                    live_slots.append(slot)
                else:
                    dead_slots.append(slot)
            self._slots = live_slots
            missing = self._worker_count - len(self._slots)
            if missing <= 0:
                return
            self._spawning = True
            start_index = len(self._slots)

        new_slots: list[_AttachmentBacklogScannerSlot] = []
        failure: BaseException | None = None
        unconfirmed_slots: list[_AttachmentBacklogScannerSlot] = []
        try:
            for slot in dead_slots:
                if not self._terminate_slot(slot):
                    unconfirmed_slots.append(slot)
            if unconfirmed_slots:
                raise RuntimeError("attachment backlog scanner child worker did not stop")
            context = multiprocessing.get_context("spawn")
            for offset in range(missing):
                parent, child = context.Pipe(duplex=True)
                process = context.Process(
                    target=_attachment_backlog_scanner_process,
                    args=(child,),
                    name=f"feishu-attachment-backlog-scanner-{start_index + offset}",
                    daemon=True,
                )
                slot = _AttachmentBacklogScannerSlot(process=process, connection=parent)
                try:
                    process.start()
                except BaseException:
                    child.close()
                    if self._process_liveness(slot) is not False:
                        new_slots.append(slot)
                    else:
                        parent.close()
                    raise
                child.close()
                new_slots.append(slot)
                if self._maintenance_stop.is_set():
                    raise _AttachmentBacklogScannerStopping
                if not self._wait_for_ready(parent):
                    raise RuntimeError("attachment backlog scanner failed to become ready")
        except BaseException as exc:
            failure = exc

        with self._lock:
            publish_slots = failure is None and not self._stopping
            if publish_slots:
                self._slots.extend(new_slots)
                new_slots = []
            self._spawning = False
        stubborn_slots = list(unconfirmed_slots)
        for slot in new_slots:
            if not self._terminate_slot(slot):
                stubborn_slots.append(slot)
        if stubborn_slots:
            with self._lock:
                self._retain_unconfirmed_slots(stubborn_slots)
            if failure is None:
                failure = RuntimeError("attachment backlog scanner child worker did not stop")
        if failure is not None:
            raise failure

    def _wait_for_ready(self, connection: Any) -> bool:
        """Wait in stop-aware slices so shutdown can drain unpublished children."""
        deadline = time.monotonic() + FEISHU_WEBSOCKET_CONNECT_TIMEOUT_SECONDS
        while not self._maintenance_stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if connection.poll(min(FEISHU_ATTACHMENT_BACKLOG_SCANNER_READY_POLL_SECONDS, remaining)):
                return connection.recv() == ("ready",)
        raise _AttachmentBacklogScannerStopping

    def _maintain(self) -> None:
        """Replenish failed slots with bounded backoff outside request threads."""
        retry_delay = FEISHU_ATTACHMENT_BACKLOG_SCANNER_RETRY_SECONDS
        try:
            while not self._maintenance_stop.is_set():
                self._replenish_needed.wait(FEISHU_ATTACHMENT_BACKLOG_SCANNER_MAINTENANCE_SECONDS)
                self._replenish_needed.clear()
                if self._maintenance_stop.is_set():
                    return
                try:
                    self._replenish()
                except _AttachmentBacklogScannerStopping:
                    return
                except Exception:
                    logger.exception("Published attachment backlog scanner replenishment failed")
                    if self._maintenance_stop.wait(retry_delay):
                        return
                    retry_delay = min(
                        retry_delay * 2,
                        FEISHU_ATTACHMENT_BACKLOG_SCANNER_MAX_RETRY_SECONDS,
                    )
                    self._replenish_needed.set()
                else:
                    retry_delay = FEISHU_ATTACHMENT_BACKLOG_SCANNER_RETRY_SECONDS
        finally:
            with self._lock:
                if self._maintenance_thread is threading.current_thread():
                    self._maintenance_thread = None

    @staticmethod
    def _process_liveness(slot: _AttachmentBacklogScannerSlot) -> bool | None:
        """Return process liveness, treating API failure as unknown."""
        try:
            return bool(slot.process.is_alive())
        except Exception:
            logger.warning("Published attachment scanner child liveness check failed", exc_info=True)
            return None

    def _retain_unconfirmed_slots(self, slots: list[_AttachmentBacklogScannerSlot]) -> None:
        """Retain ownership and fence restart for every unconfirmed child."""
        for slot in slots:
            if all(existing is not slot for existing in self._slots):
                self._slots.append(slot)
        self._stopping = True
        self._maintenance_stop.set()

    @classmethod
    def _terminate_slot(cls, slot: _AttachmentBacklogScannerSlot) -> bool:
        """Best-effort every process API and return only confirmed exit."""
        try:
            slot.connection.close()
        except Exception:
            logger.warning("Published attachment scanner connection close failed", exc_info=True)
        if cls._process_liveness(slot) is not False:
            try:
                slot.process.terminate()
            except Exception:
                logger.warning("Published attachment scanner child terminate failed", exc_info=True)
        try:
            slot.process.join(timeout=0.5)
        except Exception:
            logger.warning("Published attachment scanner child join failed", exc_info=True)
        if cls._process_liveness(slot) is not False:
            try:
                slot.process.kill()
            except Exception:
                logger.warning("Published attachment scanner child kill failed", exc_info=True)
            try:
                slot.process.join(timeout=0.5)
            except Exception:
                logger.warning("Published attachment scanner child post-kill join failed", exc_info=True)
        return cls._process_liveness(slot) is False

    def _fail_scan_slot(self, slot: _AttachmentBacklogScannerSlot) -> bool:
        """Fail closed and retain ownership unless child exit is confirmed."""
        if not self._terminate_slot(slot):
            self._retain_unconfirmed_slots([slot])
        return True

    def scan(self, binding_id: str, *, timeout: float = FEISHU_ATTACHMENT_BACKLOG_SCAN_TIMEOUT_SECONDS) -> bool:
        """Return fail-closed by a deadline; never start a worker in this call."""
        if timeout <= 0 or self._stopping or not self._lock.acquire(blocking=False):
            return True
        try:
            while self._slots:
                slot = self._slots.pop(0)
                liveness = self._process_liveness(slot)
                if liveness is True:
                    break
                return self._fail_scan_slot(slot)
            else:
                return True
            request_id = uuid.uuid4().hex
            try:
                slot.connection.send(
                    (
                        "scan",
                        request_id,
                        str(get_paths().base_dir / "published-attachment-cleanup"),
                        binding_id,
                    )
                )
                if not slot.connection.poll(timeout):
                    return self._fail_scan_slot(slot)
                response = slot.connection.recv()
            except Exception:
                logger.warning("Published attachment scanner request failed", exc_info=True)
                return self._fail_scan_slot(slot)
            if not isinstance(response, tuple) or response[:2] != ("ok", request_id) or len(response) != 3:
                return self._fail_scan_slot(slot)
            self._slots.append(slot)
            return bool(response[2])
        finally:
            if len(self._slots) < self._worker_count:
                self._replenish_needed.set()
            self._lock.release()

    def stop(self) -> None:
        """Terminate all prestarted scanners during gateway shutdown."""
        self._maintenance_stop.set()
        self._replenish_needed.set()
        with self._lock:
            self._stopping = True
            maintenance_thread = self._maintenance_thread
        if maintenance_thread is not None and maintenance_thread is not threading.current_thread():
            maintenance_thread.join(timeout=FEISHU_ATTACHMENT_BACKLOG_SCANNER_SHUTDOWN_SECONDS)
        with self._lock:
            slots = self._slots
            stubborn_slots: list[_AttachmentBacklogScannerSlot] = []
            for slot in slots:
                try:
                    slot.connection.send(("stop",))
                except Exception:
                    logger.warning("Published attachment scanner stop request failed", exc_info=True)
                if not self._terminate_slot(slot):
                    stubborn_slots.append(slot)
            self._slots = stubborn_slots
            if stubborn_slots:
                self._retain_unconfirmed_slots(stubborn_slots)
        if maintenance_thread is not None and maintenance_thread.is_alive():
            raise RuntimeError("attachment backlog scanner manager did not stop")
        if stubborn_slots:
            raise RuntimeError("attachment backlog scanner child worker did not stop")


_published_attachment_backlog_scanner = _PublishedAttachmentBacklogScanner()


def start_published_attachment_backlog_scanner() -> None:
    """Prepare bounded DELETE scanning before request admission."""
    _published_attachment_backlog_scanner.start()


def stop_published_attachment_backlog_scanner() -> None:
    """Stop the process-owned DELETE scanning pool."""
    _published_attachment_backlog_scanner.stop()


def has_published_attachment_cleanup_backlog(binding_id: str) -> bool:
    """Fail closed after one deadline-bound request to a prestarted worker."""
    return _published_attachment_backlog_scanner.scan(binding_id)


def _published_attachment_cleanup_binding_ids() -> set[str]:
    outbox_dir = get_paths().base_dir / "published-attachment-cleanup"
    if not outbox_dir.exists():
        return set()
    binding_ids: set[str] = set()
    for path in outbox_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            binding_id = payload.get("binding_id")
            if isinstance(binding_id, str) and binding_id:
                binding_ids.add(binding_id)
        except Exception:
            logger.error("[Feishu] invalid cleanup job cannot be assigned to the global janitor: %s", path, exc_info=True)
    return binding_ids


def _cleanup_job_priority(
    job: _PublishedAttachmentCleanupJob,
    *,
    now: float,
) -> int | None:
    if job.phase == "ready_to_delete":
        return 0
    if job.phase == "deleting":
        if job.claim_token in _ACTIVE_ATTACHMENT_CLAIMS:
            return None
        if job.claim_lease_expires_at is not None and job.claim_lease_expires_at > now:
            return None
        return 1
    if job.producer_token in _ACTIVE_ATTACHMENT_PRODUCERS:
        return None
    if job.producer_lease_expires_at is not None and job.producer_lease_expires_at > now:
        return None
    return 2


def _select_cleanup_jobs(
    jobs: list[_PublishedAttachmentCleanupJob],
    *,
    cursor_scope: str,
    limit: int,
) -> list[_PublishedAttachmentCleanupJob]:
    """Choose claimable work fairly without letting live leases occupy slots."""
    now = time.time()
    claimable = [(priority, job) for job in jobs if (priority := _cleanup_job_priority(job, now=now)) is not None]
    claimable.sort(key=lambda item: (item[0], item[1].job_id))
    if not claimable:
        return []
    outbox_dir = get_paths().base_dir / "published-attachment-cleanup"
    cursor_name = hashlib.sha256(cursor_scope.encode("utf-8")).hexdigest()[:24]
    cursor_path = outbox_dir / f".recovery-cursor-{cursor_name}"
    cursor_lock = FileLock(str(cursor_path.with_suffix(".lock")), timeout=2.0)
    with cursor_lock:
        cursor = cursor_path.read_text(encoding="utf-8").strip() if cursor_path.exists() else ""
        start_index = next(
            (index + 1 for index, (_priority, job) in enumerate(claimable) if job.job_id == cursor),
            0,
        )
        rotated = claimable[start_index:] + claimable[:start_index]
        selected = [job for _priority, job in rotated[:limit]]
        if selected:
            cursor_path.parent.mkdir(parents=True, exist_ok=True)
            cursor_path.write_text(selected[-1].job_id, encoding="utf-8")
        return selected


def _scan_all_cleanup_jobs(
    *,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[_PublishedAttachmentCleanupJob], bool, bool]:
    """Parse every candidate at most once until the global pass deadline."""
    outbox_dir = get_paths().base_dir / "published-attachment-cleanup"
    if not outbox_dir.exists():
        return [], False, False
    if not _ATTACHMENT_CLEANUP_SCAN_LOCK.acquire(blocking=False):
        return [], False, True
    try:
        paths = sorted(outbox_dir.glob("*.json"), key=lambda path: path.name)
        if not paths:
            return [], False, False
        cursor_path = outbox_dir / ".discovery-cursor-global"
        cursor_lock = FileLock(str(cursor_path.with_suffix(".lock")), timeout=2.0)
        with cursor_lock:
            cursor = cursor_path.read_text(encoding="utf-8").strip() if cursor_path.exists() else ""
            start_index = next((index for index, path in enumerate(paths) if path.name > cursor), 0)
            ordered_paths = paths[start_index:] + paths[:start_index]
            jobs: list[_PublishedAttachmentCleanupJob] = []
            invalid = False
            last_scanned = ""
            timed_out = False
            for path in ordered_paths:
                if clock() >= deadline:
                    timed_out = True
                    break
                last_scanned = path.name
                temp_path = cursor_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
                try:
                    # Persist discovery progress before parsing. A bounded pair
                    # of daemon readers isolates uninterruptible filesystem I/O
                    # without accumulating an unbounded worker backlog.
                    temp_path.write_text(last_scanned, encoding="utf-8")
                    _replace_cleanup_state_with_deadline(
                        temp_path,
                        cursor_path,
                        deadline=deadline,
                        clock=clock,
                    )
                finally:
                    temp_path.unlink(missing_ok=True)
                try:
                    job = _read_cleanup_job_with_deadline(
                        path,
                        timeout=max(0.0, deadline - clock()),
                    )
                    jobs.append(job)
                    _write_cleanup_binding_index(outbox_dir, job)
                except TimeoutError:
                    timed_out = True
                    continue
                except FileNotFoundError:
                    continue
                except Exception:
                    invalid = True
                    logger.error("[Feishu] invalid cleanup job cannot be recovered by the global janitor: %s", path, exc_info=True)
            return jobs, invalid, timed_out
    finally:
        _ATTACHMENT_CLEANUP_SCAN_LOCK.release()


def _attachment_cleanup_reader_process(path_value: str, connection: Any) -> None:
    """Read one untrusted job in a process the parent can terminate."""
    try:
        job = FeishuChannel._read_attachment_cleanup_job(Path(path_value))
        connection.send(("ok", job.to_dict()))
    except BaseException as exc:
        connection.send(("error", type(exc).__name__, str(exc)))
    finally:
        connection.close()


def _read_attachment_cleanup_job_isolated(
    path: Path,
    *,
    timeout: float,
) -> _PublishedAttachmentCleanupJob:
    """Parse one job in a killable child so a bad filesystem read cannot linger."""
    if timeout <= 0:
        raise TimeoutError("attachment cleanup job read deadline exceeded")
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(
        target=_attachment_cleanup_reader_process,
        args=(str(path), send),
        name="feishu-cleanup-job-read-isolated",
        daemon=True,
    )
    started = False
    try:
        process.start()
        started = True
        send.close()
        if not receive.poll(timeout):
            raise TimeoutError("attachment cleanup job isolated read deadline exceeded")
        payload = receive.recv()
        process.join(timeout=0.5)
        if not isinstance(payload, tuple) or not payload:
            raise ValueError("attachment cleanup job reader returned invalid state")
        if payload[0] == "ok" and len(payload) == 2 and isinstance(payload[1], dict):
            return _PublishedAttachmentCleanupJob.from_dict(payload[1])
        if payload[0] == "error" and len(payload) == 3:
            if payload[1] == "FileNotFoundError":
                raise FileNotFoundError(payload[2])
            raise ValueError(f"attachment cleanup job reader failed: {payload[1]}: {payload[2]}")
        raise ValueError("attachment cleanup job reader returned invalid state")
    finally:
        receive.close()
        send.close()
        if started and process.is_alive():
            process.terminate()
        if started:
            process.join(timeout=0.5)
        if started and process.is_alive():
            process.kill()
            process.join(timeout=0.5)


def _read_cleanup_job_with_deadline(
    path: Path,
    *,
    timeout: float,
) -> _PublishedAttachmentCleanupJob:
    """Bound one parse with eight thread quarantines, then killable isolation."""
    global _ATTACHMENT_CLEANUP_THREAD_READ_RESERVATIONS

    path_key = str(path.resolve(strict=False))
    with _ATTACHMENT_CLEANUP_READ_STATE_LOCK:
        for stale_key, future in list(_ATTACHMENT_CLEANUP_QUARANTINED_READS.items()):
            if future.done():
                _ATTACHMENT_CLEANUP_QUARANTINED_READS.pop(stale_key, None)
        quarantined = _ATTACHMENT_CLEANUP_QUARANTINED_READS.get(path_key)
        if quarantined is not None:
            if not quarantined.done():
                raise TimeoutError("attachment cleanup job read is quarantined")
            _ATTACHMENT_CLEANUP_QUARANTINED_READS.pop(path_key, None)
            return quarantined.result()
        use_isolated_reader = len(_ATTACHMENT_CLEANUP_QUARANTINED_READS) + _ATTACHMENT_CLEANUP_THREAD_READ_RESERVATIONS >= _ATTACHMENT_CLEANUP_MAX_QUARANTINED_READERS
        if not use_isolated_reader:
            _ATTACHMENT_CLEANUP_THREAD_READ_RESERVATIONS += 1

    if timeout <= 0 or not _ATTACHMENT_CLEANUP_READ_SLOTS.acquire(blocking=False):
        if not use_isolated_reader:
            with _ATTACHMENT_CLEANUP_READ_STATE_LOCK:
                _ATTACHMENT_CLEANUP_THREAD_READ_RESERVATIONS -= 1
        raise TimeoutError("attachment cleanup job read deadline exceeded")

    if use_isolated_reader:
        try:
            return _read_attachment_cleanup_job_isolated(path, timeout=timeout)
        finally:
            _ATTACHMENT_CLEANUP_READ_SLOTS.release()

    result: concurrent.futures.Future[_PublishedAttachmentCleanupJob] = concurrent.futures.Future()
    permit_lock = threading.Lock()
    permit_held = True

    def release_permit() -> None:
        nonlocal permit_held
        with permit_lock:
            if not permit_held:
                return
            permit_held = False
            _ATTACHMENT_CLEANUP_READ_SLOTS.release()

    def read() -> None:
        try:
            result.set_result(FeishuChannel._read_attachment_cleanup_job(path))
        except BaseException as exc:
            result.set_exception(exc)
        finally:
            release_permit()

    threading.Thread(
        target=read,
        name="feishu-cleanup-job-read",
        daemon=True,
    ).start()
    try:
        job = result.result(timeout=timeout)
    except concurrent.futures.TimeoutError as exc:
        with _ATTACHMENT_CLEANUP_READ_STATE_LOCK:
            _ATTACHMENT_CLEANUP_THREAD_READ_RESERVATIONS -= 1
            _ATTACHMENT_CLEANUP_QUARANTINED_READS[path_key] = result
        release_permit()
        raise TimeoutError("attachment cleanup job read deadline exceeded") from exc
    except BaseException:
        with _ATTACHMENT_CLEANUP_READ_STATE_LOCK:
            _ATTACHMENT_CLEANUP_THREAD_READ_RESERVATIONS -= 1
        raise
    with _ATTACHMENT_CLEANUP_READ_STATE_LOCK:
        _ATTACHMENT_CLEANUP_THREAD_READ_RESERVATIONS -= 1
    return job


async def recover_all_published_attachment_cleanups() -> int:
    """Recover one globally bounded, fair batch without rows or secrets."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + FEISHU_ATTACHMENT_RECOVERY_TIMEOUT_SECONDS
    try:
        jobs, invalid, scan_timed_out = await asyncio.wait_for(
            asyncio.to_thread(
                _scan_all_cleanup_jobs,
                deadline=time.monotonic() + FEISHU_ATTACHMENT_RECOVERY_TIMEOUT_SECONDS,
            ),
            timeout=FEISHU_ATTACHMENT_RECOVERY_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.error("[Feishu] global attachment cleanup discovery exceeded its total deadline")
        return 0
    if invalid or scan_timed_out:
        logger.warning("[Feishu] global attachment cleanup scan was incomplete or contained invalid state")
    remaining_seconds = deadline - loop.time()
    if remaining_seconds <= 0 or not jobs:
        return 0
    try:
        selected = await asyncio.wait_for(
            asyncio.to_thread(
                _select_cleanup_jobs,
                jobs,
                cursor_scope="global",
                limit=FEISHU_ATTACHMENT_RECOVERY_MAX_JOBS,
            ),
            timeout=remaining_seconds,
        )
    except TimeoutError:
        logger.error("[Feishu] global attachment cleanup scheduling exceeded its total deadline")
        return 0
    if not selected:
        return 0
    try:
        sandbox_provider = get_sandbox_provider()
    except Exception:
        logger.error("[Feishu] global attachment cleanup could not load the sandbox provider", exc_info=True)
        return 0
    semaphore = asyncio.Semaphore(FEISHU_ATTACHMENT_RECOVERY_MAX_CONCURRENCY)

    async def recover_job(job: _PublishedAttachmentCleanupJob) -> bool:
        async with semaphore:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            coordinator = FeishuChannel(MessageBus(), binding_id=job.binding_id)
            try:
                return await asyncio.wait_for(
                    coordinator._recover_attachment_cleanup_job(
                        job,
                        sandbox_provider,
                        acquire_timeout_seconds=min(
                            FEISHU_SANDBOX_ACQUIRE_TIMEOUT_SECONDS,
                            remaining,
                        ),
                        refresh_health=False,
                    ),
                    timeout=remaining,
                )
            except Exception:
                logger.error(
                    "[Feishu] global attachment cleanup failed for job %s",
                    job.job_id,
                    exc_info=True,
                )
                return False

    results = await asyncio.gather(*(recover_job(job) for job in selected))
    return sum(results)


class FeishuEventVerifier:
    """Validate the authenticated SDK event header and replay window.

    Feishu long-connection mode authenticates the WebSocket with app
    credentials, but lark-oapi deliberately dispatches WebSocket frames through
    ``do_without_validation``. Dynamic bindings therefore compare the event
    header token here and additionally require a stable ID and fresh timestamp
    before durable deduplication. HTTP callback signatures are not present on
    this transport; ``encrypt_key`` remains part of the encrypted credential
    bundle and dispatcher construction for provider configuration parity.
    """

    def __init__(
        self,
        *,
        verification_token: str = "",
        max_age_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        self._verification_token = verification_token
        self._max_age_seconds = max_age_seconds
        self._clock = clock

    @staticmethod
    def _timestamp(event: Any) -> float | None:
        header = getattr(event, "header", None)
        raw_timestamp = getattr(header, "create_time", None)
        try:
            timestamp = float(raw_timestamp)
        except (TypeError, ValueError):
            return None
        if timestamp >= 1_000_000_000_000:
            timestamp /= 1000
        return timestamp

    def __call__(self, event: Any) -> bool:
        header = getattr(event, "header", None)
        event_id = getattr(header, "event_id", None)
        if not isinstance(event_id, str) or not event_id.strip():
            return False

        timestamp = self._timestamp(event)
        if timestamp is None or abs(self._clock() - timestamp) > self._max_age_seconds:
            return False

        if self._verification_token:
            token = getattr(header, "token", None)
            if not isinstance(token, str) or not hmac.compare_digest(token, self._verification_token):
                return False
        return True


class FeishuWebSocketSession(Protocol):
    """Blocking SDK connection owned by one Feishu channel worker thread."""

    def run(
        self,
        *,
        on_ready: Callable[[], None],
        on_error: Callable[[str], None],
    ) -> None: ...

    def stop(self, *, timeout_seconds: float) -> bool: ...


class WebSocketSessionFactory(Protocol):
    """Construct a terminable SDK session for one isolated binding."""

    def __call__(
        self,
        *,
        app_id: str,
        app_secret: str,
        domain: str,
        message_handler: Callable[[Any], None],
        encrypt_key: str,
        verification_token: str,
    ) -> FeishuWebSocketSession: ...


RuntimeErrorCallback = Callable[[str], Awaitable[None]]
RuntimeHealthCallback = Callable[[bool, str | None], Awaitable[None]]
LarkEndpointResolver = Callable[[object], Awaitable[str]]


async def _resolve_lark_endpoint(client: object) -> str:
    """Fetch one SDK endpoint asynchronously with bounded network timeouts."""
    from lark_oapi.ws.const import GEN_ENDPOINT_URI, OK

    app_id = getattr(client, "_app_id", "")
    app_secret = getattr(client, "_app_secret", "")
    domain = getattr(client, "_domain", "")
    if not app_id or not app_secret:
        raise RuntimeError("Feishu app_id or app_secret is empty")

    timeout = httpx.Timeout(
        FEISHU_ENDPOINT_READ_TIMEOUT_SECONDS,
        connect=FEISHU_ENDPOINT_CONNECT_TIMEOUT_SECONDS,
    )
    async with httpx.AsyncClient(timeout=timeout) as http_client:
        response = await http_client.post(
            f"{domain}{GEN_ENDPOINT_URI}",
            headers={"locale": "zh"},
            json={"AppID": app_id, "AppSecret": app_secret},
        )
    if response.status_code != 200:
        raise RuntimeError(f"Feishu endpoint request failed with HTTP {response.status_code}")
    payload = response.json()
    code = payload.get("code")
    if code != OK:
        raise RuntimeError(f"Feishu endpoint request failed: code={code}, msg={payload.get('msg', '')}")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("URL"), str) or not data["URL"]:
        raise RuntimeError("Feishu endpoint response did not contain a WebSocket URL")

    client_config = data.get("ClientConfig")
    configure = getattr(client, "_configure", None)
    if isinstance(client_config, dict) and callable(configure):
        from lark_oapi.ws.model import ClientConfig

        configure(ClientConfig(client_config))
    return data["URL"]


class _LarkSdkRuntime:
    """Own the single event loop referenced by lark-oapi's module global."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = threading.Event()
        self._stopped = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="feishu-lark-sdk-loop",
            daemon=True,
        )
        self._thread.start()
        if not self._started.wait(5.0):
            raise RuntimeError("Feishu SDK event loop failed to start")

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        loop = self._loop
        if loop is None:
            raise RuntimeError("Feishu SDK event loop is unavailable")
        return loop

    def _run(self) -> None:
        import lark_oapi.ws.client as ws_client_module

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        # lark-oapi 1.x schedules receive work through this module global.
        # Assign it exactly once to a process-owned loop shared by all clients.
        ws_client_module.loop = loop
        self._started.set()
        try:
            loop.run_forever()
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            self._stopped.set()

    def submit(self, coroutine: Awaitable[Any]) -> concurrent.futures.Future[Any]:
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def shutdown(self, *, timeout_seconds: float = 5.0) -> bool:
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout_seconds)
        return self._stopped.is_set()


_lark_sdk_runtime: _LarkSdkRuntime | None = None
_lark_sdk_runtime_lock = threading.Lock()


def _get_lark_sdk_runtime() -> _LarkSdkRuntime:
    global _lark_sdk_runtime
    with _lark_sdk_runtime_lock:
        if _lark_sdk_runtime is None:
            _lark_sdk_runtime = _LarkSdkRuntime()
        return _lark_sdk_runtime


def shutdown_lark_sdk_runtime(*, timeout_seconds: float = 5.0) -> bool:
    """Stop the process-owned lark-oapi loop after all bindings have stopped."""
    global _lark_sdk_runtime
    with _lark_sdk_runtime_lock:
        runtime = _lark_sdk_runtime
        if runtime is None:
            return True
        stopped = runtime.shutdown(timeout_seconds=timeout_seconds)
        if stopped:
            _lark_sdk_runtime = None
        return stopped


class _LarkWebSocketSession:
    """One binding connection scheduled on the process-owned SDK loop."""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        domain: str,
        event_handler: object,
        endpoint_resolver: LarkEndpointResolver = _resolve_lark_endpoint,
        connect_timeout_seconds: float = FEISHU_WEBSOCKET_CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        if connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be positive")
        self._app_id = app_id
        self._app_secret = app_secret
        self._domain = domain
        self._event_handler = event_handler
        self._endpoint_resolver = endpoint_resolver
        self._connect_timeout_seconds = connect_timeout_seconds
        self._runtime: _LarkSdkRuntime | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: object | None = None
        self._run_future: concurrent.futures.Future[Any] | None = None
        self._stop_waiter: asyncio.Future[None] | None = None
        self._owned_tasks: set[asyncio.Task[Any]] = set()
        self._stopping = threading.Event()
        self._exited = threading.Event()

    @staticmethod
    def _belongs_to_client(task: asyncio.Task[Any], client: object) -> bool:
        coroutine = task.get_coro()
        frame = getattr(coroutine, "cr_frame", None)
        return frame is not None and frame.f_locals.get("self") is client

    async def _run_client(
        self,
        *,
        on_ready: Callable[[], None],
        on_error: Callable[[str], None],
    ) -> None:
        import lark_oapi as lark

        loop = asyncio.get_running_loop()
        self._loop = loop
        client = lark.ws.Client(
            app_id=self._app_id,
            app_secret=self._app_secret,
            event_handler=self._event_handler,
            log_level=lark.LogLevel.INFO,
            domain=self._domain,
            auto_reconnect=True,
        )
        self._client = client

        def handle_sdk_task_done(task: asyncio.Task[Any]) -> None:
            if self._stopping.is_set() or task.cancelled():
                return
            try:
                task.exception()
            except asyncio.CancelledError:
                return
            on_error("connection lost")
            waiter = self._stop_waiter
            if waiter is not None and not waiter.done():
                waiter.set_result(None)

        try:
            async with asyncio.timeout(self._connect_timeout_seconds):
                conn_url = await self._endpoint_resolver(client)
                # lark-oapi 1.5.x performs a synchronous, unbounded
                # ``requests.post`` inside ``_connect``. Resolve it above with
                # async bounded I/O, then let the SDK consume the cached URL.
                client._get_conn_url = lambda: conn_url
                await client._connect()
            self._owned_tasks.update(task for task in asyncio.all_tasks(loop) if task is not asyncio.current_task() and self._belongs_to_client(task, client))
            if client._conn is None:
                raise RuntimeError("Feishu WebSocket connection was not established")
            ping_task = loop.create_task(client._ping_loop())
            self._owned_tasks.add(ping_task)
            for task in self._owned_tasks:
                task.add_done_callback(handle_sdk_task_done)
            if self._stopping.is_set():
                return
            self._stop_waiter = loop.create_future()
            on_ready()
            await self._stop_waiter
        finally:
            self._owned_tasks.update(task for task in asyncio.all_tasks(loop) if task is not asyncio.current_task() and self._belongs_to_client(task, client))
            try:
                if client._conn is not None:
                    await client._disconnect()
            except Exception:
                logger.warning("Feishu WebSocket disconnect failed", exc_info=True)
            owned_tasks = [task for task in self._owned_tasks if not task.done()]
            for task in owned_tasks:
                task.cancel()
            if owned_tasks:
                await asyncio.gather(*owned_tasks, return_exceptions=True)
            self._owned_tasks.clear()
            self._stop_waiter = None
            self._client = None

    def run(
        self,
        *,
        on_ready: Callable[[], None],
        on_error: Callable[[str], None],
    ) -> None:
        self._runtime = _get_lark_sdk_runtime()
        try:
            future = self._runtime.submit(self._run_client(on_ready=on_ready, on_error=on_error))
            self._run_future = future
            future.result()
        except concurrent.futures.CancelledError:
            pass
        except Exception:
            if not self._stopping.is_set():
                on_error("connection failed")
        finally:
            self._run_future = None
            self._exited.set()

    def stop(self, *, timeout_seconds: float) -> bool:
        """Close the SDK connection and confirm its worker loop has exited."""
        self._stopping.set()
        runtime = self._runtime
        if runtime is not None:

            async def request_stop() -> bool:
                waiter = self._stop_waiter
                if waiter is not None and not waiter.done():
                    waiter.set_result(None)
                    return True
                return False

            try:
                future = runtime.submit(request_stop())
                signaled = future.result(timeout=timeout_seconds)
                if not signaled:
                    run_future = self._run_future
                    if run_future is not None:
                        run_future.cancel()
            except Exception:
                run_future = self._run_future
                if run_future is not None:
                    run_future.cancel()
        return self._exited.wait(timeout_seconds)


# 已处理卡片的 message_id 集合，防止重复点击
_processed_card_messages: set[str] = set()
# 集合最大容量，避免内存泄漏
_MAX_PROCESSED_CARDS = 500


def _handle_card_action(data, app_id=None, app_secret=None) -> None:
    """处理飞书卡片按钮回调。

    审批人点击卡片上的 ✅同意/❌拒绝 按钮后：
    1. 立刻 patch 卡片，把按钮换成已处理状态（防止重复点击）
    2. approve → 触发智能体（含禅道同步）
    3. reject → 触发智能体（跳过禅道同步）
    """
    import json
    import logging
    import os

    import httpx

    logger = logging.getLogger(__name__)

    try:
        event = getattr(data, "event", None)
        if event is None:
            return

        action = getattr(event, "action", None)
        if action is None:
            return

        value = getattr(action, "value", None)
        if value is None:
            return

        # value 可能是 dict 或 JSON 字符串
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                return

        if not isinstance(value, dict):
            return

        action_type = value.get("action", "")
        callback = value.get("callback") or {}

        if not callback:
            return  # 非 approval skill 卡片，忽略

        agent_id = callback.get("agent_id", "")
        conversation_id = callback.get("conversation_id", "")
        message = callback.get("message", "开始分析")
        # 从 callback 中读取后端 API 配置（auth_token 不走环境变量，直接从卡片回调配置取）
        base_url = callback.get("base_url", "") or os.environ.get("DEER_FLOW_API_URL", "http://localhost:8000")
        auth_token = callback.get("auth_token", "") or os.environ.get("DEER_FLOW_AUTH_TOKEN", "")

        if not agent_id or not conversation_id:
            return

        record_id = value.get("record_id", "")

        # 提取表单数据（拒绝原因）
        form_value = getattr(action, "form_value", None) or {}
        if isinstance(form_value, str):
            try:
                form_value = json.loads(form_value)
            except (json.JSONDecodeError, ValueError):
                form_value = {}
        reject_reason = ""
        if isinstance(form_value, dict):
            reject_reason = form_value.get("reject_reason", "")

        # 获取操作人
        operator = getattr(event, "operator", None)
        operator_open_id = ""
        if operator is not None:
            operator_open_id = getattr(operator, "open_id", "") or ""

        # 获取消息 ID（用于 patch 卡片 + 去重）
        message_id = ""
        context = getattr(event, "context", None)
        if context is not None:
            message_id = getattr(context, "open_message_id", "") or ""
        if not message_id and operator is not None:
            message_id = getattr(operator, "open_message_id", "") or ""

        # 去重：同一卡片只处理一次（防止重复点击）
        if message_id:
            if message_id in _processed_card_messages:
                logger.warning("Card already processed, skipping: msg_id=%s, action=%s", message_id, action_type)
                return
            _processed_card_messages.add(message_id)
            # 控制集合大小，避免内存泄漏
            if len(_processed_card_messages) > _MAX_PROCESSED_CARDS:
                _processed_card_messages.clear()

        logger.info(
            "Card action: action=%s, agent=%s, record=%s, operator=%s, msg_id=%s",
            action_type, agent_id, record_id, operator_open_id, message_id,
        )

        # 1. Patch 卡片：移除按钮，显示已处理状态（防止重复点击）
        if app_id and app_secret and message_id:
            _patch_card_after_action(
                app_id, app_secret, message_id,
                action_type, reject_reason,
            )

        # 2. 触发智能体（带上 action_type 和 record_id，智能体自行处理后续逻辑）
        if action_type == "approve":
            full_message = f"{message}（审批回调：action=approve, record_id={record_id}）"
        elif action_type == "reject":
            reason_part = f", reject_reason={reject_reason}" if reject_reason else ""
            full_message = f"{message}（审批回调：action=reject, record_id={record_id}{reason_part}）"
        else:
            full_message = f"{message}（审批回调：action={action_type}, record_id={record_id}）"

        url = f"{base_url}/api/v1/agents/{agent_id}/conversations/{conversation_id}/runs"
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        }
        payload = {"message": full_message}

        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            logger.info("Agent triggered: action=%s, status=%s", action_type, resp.status_code)
        except Exception as e:
            logger.error("Failed to trigger agent: %s", e)

    except Exception as e:
        logger.error("Error handling card action: %s", e, exc_info=True)


def _patch_card_after_action(app_id, app_secret, message_id, action_type, reject_reason=""):
    """Patch 飞书卡片消息，移除按钮，显示已处理状态。"""
    import json
    import logging
    from datetime import datetime, timezone, timedelta

    import lark_oapi as lark
    from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

    logger = logging.getLogger(__name__)
    CST = timezone(timedelta(hours=8))
    now_str = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")

    if action_type == "approve":
        title = "审批已通过"
        tag_text = "已同意"
        tag_color = "green"
        template = "green"
        body_content = f"✅ 审批人已同意\n\n已提交禅道同步。\n\n操作时间：{now_str}"
    elif action_type == "reject":
        title = "审批已拒绝"
        tag_text = "已拒绝"
        tag_color = "red"
        template = "red"
        reason_text = f"\n\n拒绝原因：{reject_reason}" if reject_reason else ""
        body_content = f"❌ 审批人已拒绝{reason_text}\n\n不同步禅道。\n\n操作时间：{now_str}"
    else:
        title = "审批已处理"
        tag_text = "已处理"
        tag_color = "grey"
        template = "grey"
        body_content = f"审批已处理\n\n操作时间：{now_str}"

    new_card = {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "text_tag_list": [
                {"tag": "text_tag", "text": {"tag": "plain_text", "content": tag_text}, "color": tag_color}
            ],
            "template": template,
            "padding": "12px 8px 12px 8px",
        },
        "body": {
            "elements": [
                {"tag": "markdown", "content": body_content},
            ],
        },
    }

    try:
        client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .build()
        )
        req = (
            PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                PatchMessageRequestBody.builder()
                .content_type("interactive")
                .content(json.dumps(new_card))
                .build()
            )
            .build()
        )
        resp = client.im.v1.message.patch(req)
        if resp.success():
            logger.info("Card patched: message_id=%s, action=%s", message_id, action_type)
        else:
            logger.warning("Card patch failed: code=%s, msg=%s", resp.code, resp.msg)
    except Exception as e:
        logger.error("Error patching card: %s", e)


def _default_websocket_session_factory(
    *,
    app_id: str,
    app_secret: str,
    domain: str,
    message_handler: Callable[[Any], None],
    encrypt_key: str,
    verification_token: str,
) -> FeishuWebSocketSession:
    import lark_oapi as lark
    from functools import partial

    card_action_handler = partial(_handle_card_action, app_id=app_id, app_secret=app_secret)

    event_handler = (
        lark.EventDispatcherHandler.builder(encrypt_key, verification_token)
        .register_p2_im_message_receive_v1(message_handler)
        .register_p2_card_action_trigger(card_action_handler)
        .build()
    )
    return _LarkWebSocketSession(
        app_id=app_id,
        app_secret=app_secret,
        domain=domain,
        event_handler=event_handler,
    )


def _is_feishu_command(text: str) -> bool:
    if not text.startswith("/"):
        return False
    return text.split(maxsplit=1)[0].lower() in KNOWN_CHANNEL_COMMANDS


class FeishuChannel(Channel):
    """Feishu/Lark IM channel using the ``lark-oapi`` WebSocket client.

    Configuration keys (in ``config.yaml`` under ``channels.feishu``):
        - ``app_id``: Feishu app ID.
        - ``app_secret``: Feishu app secret.
        - ``verification_token``: (optional) Event verification token.

    The channel uses WebSocket long-connection mode so no public IP is required.

    Message flow:
        1. User sends a message → bot adds "OK" emoji reaction
        2. Bot replies in thread: "Working on it......"
        3. Agent processes the message and returns a result
        4. Bot replies in thread with the result
        5. Bot adds "DONE" emoji reaction to the original message
    """

    def __init__(
        self,
        bus: MessageBus,
        config: dict[str, Any] | None = None,
        *,
        app_id: str | None = None,
        app_secret: str | None = None,
        verification_token: str | None = None,
        encrypt_key: str | None = None,
        binding_id: str | None = None,
        agent_id: str | None = None,
        event_deduplicator: FeishuEventDeduplicator | None = None,
        event_verifier: Callable[[Any], bool] | None = None,
        websocket_session_factory: WebSocketSessionFactory | None = None,
        startup_timeout_seconds: float = 15.0,
        runtime_error_callback: RuntimeErrorCallback | None = None,
        runtime_health_callback: RuntimeHealthCallback | None = None,
        published_http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        resolved_config = dict(config or {})
        if app_id is not None:
            resolved_config["app_id"] = app_id
        if app_secret is not None:
            resolved_config["app_secret"] = app_secret
        if verification_token is not None:
            resolved_config["verification_token"] = verification_token
        if encrypt_key is not None:
            resolved_config["encrypt_key"] = encrypt_key
        channel_name = f"feishu:{binding_id}" if binding_id else "feishu"
        super().__init__(name=channel_name, bus=bus, config=resolved_config)
        self.binding_id = binding_id
        self.agent_id = agent_id
        self._event_deduplicator = event_deduplicator
        self._event_verifier = event_verifier or (FeishuEventVerifier(verification_token=str(resolved_config.get("verification_token", ""))) if binding_id else None)
        if startup_timeout_seconds <= 0:
            raise ValueError("startup_timeout_seconds must be positive")
        self._websocket_session_factory = websocket_session_factory or _default_websocket_session_factory
        self._startup_timeout_seconds = startup_timeout_seconds
        self._runtime_error_callback = runtime_error_callback
        self._runtime_health_callback = runtime_health_callback
        self._published_http_client_factory = published_http_client_factory or self._new_published_http_client
        self._ws_session: FeishuWebSocketSession | None = None
        self._startup_event = threading.Event()
        self._startup_error: RuntimeError | None = None
        self._startup_acknowledged = False
        self._stop_requested = False
        self._thread: threading.Thread | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._api_client = None
        self._CreateMessageReactionRequest = None
        self._CreateMessageReactionRequestBody = None
        self._Emoji = None
        self._PatchMessageRequest = None
        self._PatchMessageRequestBody = None
        self._background_tasks: set[asyncio.Task] = set()
        self._running_card_ids: dict[str, str] = {}
        self._running_card_tasks: dict[str, asyncio.Task] = {}
        self._CreateFileRequest = None
        self._CreateFileRequestBody = None
        self._CreateImageRequest = None
        self._CreateImageRequestBody = None
        self._GetMessageResourceRequest = None
        self._thread_lock = threading.Lock()
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._attachment_cleanup_state_lock = threading.Lock()
        self._attachment_cleanup_generation = 0
        self._attachment_cleanup_unhealthy = False
        self._attachment_cleanup_store_generation: str | None = None

    def _new_published_http_client(self) -> httpx.AsyncClient:
        """Build the bounded client used for authenticated resource streaming."""
        return httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=FEISHU_ENDPOINT_CONNECT_TIMEOUT_SECONDS,
                read=FEISHU_ENDPOINT_READ_TIMEOUT_SECONDS,
                write=FEISHU_ENDPOINT_READ_TIMEOUT_SECONDS,
                pool=FEISHU_ENDPOINT_CONNECT_TIMEOUT_SECONDS,
            )
        )

    @property
    def supports_streaming(self) -> bool:
        return True

    @property
    def websocket_thread_alive(self) -> bool:
        """Return whether this binding still owns a live SDK worker thread."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def attachment_cleanup_healthy(self) -> bool:
        """Return the last asynchronously projected durable cleanup health."""
        with self._attachment_cleanup_state_lock:
            locally_healthy = not self._attachment_cleanup_unhealthy
            projected = self._attachment_cleanup_store_generation is not None
        return locally_healthy and (projected or not self.binding_id)

    def _mark_attachment_cleanup_unhealthy(self) -> int:
        """Record a new dirty generation and return it."""
        with self._attachment_cleanup_state_lock:
            self._attachment_cleanup_generation += 1
            self._attachment_cleanup_unhealthy = True
            return self._attachment_cleanup_generation

    def _attachment_cleanup_generation_snapshot(self) -> int:
        with self._attachment_cleanup_state_lock:
            return self._attachment_cleanup_generation

    def _commit_attachment_cleanup_health(
        self,
        generation: int,
        *,
        healthy: bool,
    ) -> bool:
        """Publish healthy only if no newer durable producer dirtied the state."""
        with self._attachment_cleanup_state_lock:
            if healthy and self._attachment_cleanup_generation == generation:
                self._attachment_cleanup_unhealthy = False
            else:
                self._attachment_cleanup_unhealthy = True
            return not self._attachment_cleanup_unhealthy

    async def start(self) -> None:
        if self._running:
            return

        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import (
                CreateFileRequest,
                CreateFileRequestBody,
                CreateImageRequest,
                CreateImageRequestBody,
                CreateMessageReactionRequest,
                CreateMessageReactionRequestBody,
                CreateMessageRequest,
                CreateMessageRequestBody,
                Emoji,
                GetMessageResourceRequest,
                PatchMessageRequest,
                PatchMessageRequestBody,
                ReplyMessageRequest,
                ReplyMessageRequestBody,
            )
        except ImportError:
            logger.error("lark-oapi is not installed. Install it with: uv add lark-oapi")
            return

        self._lark = lark
        self._CreateMessageRequest = CreateMessageRequest
        self._CreateMessageRequestBody = CreateMessageRequestBody
        self._ReplyMessageRequest = ReplyMessageRequest
        self._ReplyMessageRequestBody = ReplyMessageRequestBody
        self._CreateMessageReactionRequest = CreateMessageReactionRequest
        self._CreateMessageReactionRequestBody = CreateMessageReactionRequestBody
        self._Emoji = Emoji
        self._PatchMessageRequest = PatchMessageRequest
        self._PatchMessageRequestBody = PatchMessageRequestBody
        self._CreateFileRequest = CreateFileRequest
        self._CreateFileRequestBody = CreateFileRequestBody
        self._CreateImageRequest = CreateImageRequest
        self._CreateImageRequestBody = CreateImageRequestBody
        self._GetMessageResourceRequest = GetMessageResourceRequest

        app_id = self.config.get("app_id", "")
        app_secret = self.config.get("app_secret", "")
        domain = self.config.get("domain", "https://open.feishu.cn")

        if not app_id or not app_secret:
            raise RuntimeError("Feishu channel requires app_id and app_secret")
        if self.binding_id and not str(self.config.get("verification_token", "")).strip():
            raise RuntimeError("Dynamic Feishu binding requires a verification token")
        if self.binding_id and self._event_deduplicator is None:
            raise RuntimeError("Dynamic Feishu binding requires durable event deduplication")

        if self.binding_id:
            # Startup projects cleanup health through the prestarted killable
            # scanner.  Direct binding-index I/O remains available to runtime
            # refreshes, but cannot hold request admission indefinitely.
            if not await self._refresh_attachment_cleanup_health_for_startup():
                self._mark_attachment_cleanup_unhealthy()

        self._api_client = lark.Client.builder().app_id(app_id).app_secret(app_secret).domain(domain).build()
        logger.info("[Feishu] using domain: %s", domain)
        self._main_loop = asyncio.get_event_loop()
        self._startup_event.clear()
        self._startup_error = None
        self._startup_acknowledged = False
        self._stop_requested = False

        # Both ws.Client construction and start() must happen in a dedicated
        # thread with its own event loop.  lark-oapi caches the running loop
        # at construction time and later calls loop.run_until_complete(),
        # which conflicts with an already-running uvloop.
        self._thread = threading.Thread(
            target=self._run_ws,
            args=(app_id, app_secret, domain),
            daemon=True,
        )
        self._thread.start()
        signalled = await asyncio.to_thread(self._startup_event.wait, self._startup_timeout_seconds)
        if not signalled or self._startup_error is not None or not self._running:
            await self.stop()
            raise self._startup_error or RuntimeError("Feishu WebSocket failed to connect")
        self._startup_acknowledged = True
        self.bus.subscribe_outbound(self._on_outbound)
        if self.binding_id:
            retry_task = asyncio.create_task(self._retry_published_attachment_cleanups())
            self._track_background_task(
                retry_task,
                name="published_attachment_cleanup_recovery",
                msg_id=self.binding_id,
            )
        logger.info("Feishu channel started")

    def _run_ws(self, app_id: str, app_secret: str, domain: str) -> None:
        """Construct and run the lark WS client in a thread with a fresh event loop.

        The lark-oapi SDK captures a module-level event loop at import time
        (``lark_oapi.ws.client.loop``).  When uvicorn uses uvloop, that
        captured loop is the *main* thread's uvloop — which is already
        running, so ``loop.run_until_complete()`` inside ``Client.start()``
        raises ``RuntimeError``.

        We work around this by creating a plain asyncio event loop for this
        thread and patching the SDK's module-level reference before calling
        ``start()``.
        """
        try:
            session = self._websocket_session_factory(
                app_id=app_id,
                app_secret=app_secret,
                domain=domain,
                message_handler=self._on_message,
                encrypt_key=str(self.config.get("encrypt_key", "")),
                verification_token=str(self.config.get("verification_token", "")),
            )
            self._ws_session = session
            session.run(on_ready=self._on_ws_ready, on_error=self._on_ws_error)
            if not self._stop_requested and self._running:
                self._on_ws_error("connection lost")
        except Exception:
            if not self._stop_requested:
                logger.exception("Feishu WebSocket error")
                self._on_ws_error("connection failed")

    def _on_ws_ready(self) -> None:
        self._running = True
        self._startup_event.set()

    def _on_ws_error(self, _detail: str) -> None:
        if self._stop_requested:
            return
        was_ready = self._running and self._startup_acknowledged
        self._running = False
        if not self._startup_acknowledged:
            self._startup_error = RuntimeError("Feishu WebSocket failed to connect")
        self._startup_event.set()
        if was_ready and self._runtime_error_callback is not None and self._main_loop is not None:
            future = asyncio.run_coroutine_threadsafe(
                self._runtime_error_callback("Feishu WebSocket connection lost"),
                self._main_loop,
            )
            future.add_done_callback(lambda done: self._log_future_error(done, "runtime_error_callback", "runtime"))

    async def stop(self) -> None:
        self._stop_requested = True
        self._running = False
        self.bus.unsubscribe_outbound(self._on_outbound)
        background_tasks = list(self._background_tasks)
        for task in background_tasks:
            task.cancel()
        self._background_tasks.clear()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        cleanup_tasks = {task for task in self._cleanup_tasks if not task.done()}
        if cleanup_tasks:
            done, pending = await asyncio.wait(
                cleanup_tasks,
                timeout=FEISHU_ATTACHMENT_CLEANUP_DRAIN_TIMEOUT_SECONDS,
            )
            if done:
                await asyncio.gather(*done, return_exceptions=True)
            if pending:
                self._mark_attachment_cleanup_unhealthy()
                logger.warning(
                    "[Feishu] %d durable attachment cleanup task(s) still pending during stop",
                    len(pending),
                )
        for task in list(self._running_card_tasks.values()):
            task.cancel()
        self._running_card_tasks.clear()
        session = self._ws_session
        if session is not None:
            stopped = await asyncio.to_thread(session.stop, timeout_seconds=5.0)
            if not stopped:
                raise RuntimeError("Feishu WebSocket client did not stop")
        thread = self._thread
        if thread is not None:
            await asyncio.to_thread(thread.join, 5.0)
            if thread.is_alive():
                raise RuntimeError("Feishu WebSocket worker thread did not exit")
        self._thread = None
        self._ws_session = None
        logger.info("Feishu channel stopped")

    async def send(self, msg: OutboundMessage, *, _max_retries: int = 3) -> None:
        if not self._api_client:
            logger.warning("[Feishu] send called but no api_client available")
            return

        logger.info(
            "[Feishu] sending reply: chat_id=%s, thread_ts=%s, text_len=%d",
            msg.chat_id,
            msg.thread_ts,
            len(msg.text),
        )

        last_exc: Exception | None = None
        for attempt in range(_max_retries):
            try:
                await self._send_card_message(msg)
                return  # success
            except Exception as exc:
                last_exc = exc
                if attempt < _max_retries - 1:
                    delay = 2**attempt  # 1s, 2s
                    logger.warning(
                        "[Feishu] send failed (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1,
                        _max_retries,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)

        logger.error("[Feishu] send failed after %d attempts: %s", _max_retries, last_exc)
        if last_exc is None:
            raise RuntimeError("Feishu send failed without an exception from any attempt")
        raise last_exc

    async def send_file(self, msg: OutboundMessage, attachment: ResolvedAttachment) -> bool:
        if not self._api_client:
            return False

        # Check size limits (image: 10MB, file: 30MB)
        if attachment.is_image and attachment.size > 10 * 1024 * 1024:
            logger.warning("[Feishu] image too large (%d bytes), skipping: %s", attachment.size, attachment.filename)
            return False
        if not attachment.is_image and attachment.size > 30 * 1024 * 1024:
            logger.warning("[Feishu] file too large (%d bytes), skipping: %s", attachment.size, attachment.filename)
            return False

        try:
            if attachment.is_image:
                file_key = await self._upload_image(attachment.actual_path)
                msg_type = "image"
                content = json.dumps({"image_key": file_key})
            else:
                file_key = await self._upload_file(attachment.actual_path, attachment.filename)
                msg_type = "file"
                content = json.dumps({"file_key": file_key})

            if msg.thread_ts:
                request = self._ReplyMessageRequest.builder().message_id(msg.thread_ts).request_body(self._ReplyMessageRequestBody.builder().msg_type(msg_type).content(content).reply_in_thread(True).build()).build()
                await asyncio.to_thread(self._api_client.im.v1.message.reply, request)
            else:
                request = self._CreateMessageRequest.builder().receive_id_type("chat_id").request_body(self._CreateMessageRequestBody.builder().receive_id(msg.chat_id).msg_type(msg_type).content(content).build()).build()
                await asyncio.to_thread(self._api_client.im.v1.message.create, request)

            logger.info("[Feishu] file sent: %s (type=%s)", attachment.filename, msg_type)
            return True
        except Exception:
            logger.exception("[Feishu] failed to upload/send file: %s", attachment.filename)
            return False

    async def _upload_image(self, path) -> str:
        """Upload an image to Feishu and return the image_key."""
        with open(str(path), "rb") as f:
            request = self._CreateImageRequest.builder().request_body(self._CreateImageRequestBody.builder().image_type("message").image(f).build()).build()
            response = await asyncio.to_thread(self._api_client.im.v1.image.create, request)
        if not response.success():
            raise RuntimeError(f"Feishu image upload failed: code={response.code}, msg={response.msg}")
        return response.data.image_key

    async def _upload_file(self, path, filename: str) -> str:
        """Upload a file to Feishu and return the file_key."""
        suffix = path.suffix.lower() if hasattr(path, "suffix") else ""
        if suffix in (".xls", ".xlsx", ".csv"):
            file_type = "xls"
        elif suffix in (".ppt", ".pptx"):
            file_type = "ppt"
        elif suffix == ".pdf":
            file_type = "pdf"
        elif suffix in (".doc", ".docx"):
            file_type = "doc"
        else:
            file_type = "stream"

        with open(str(path), "rb") as f:
            request = self._CreateFileRequest.builder().request_body(self._CreateFileRequestBody.builder().file_type(file_type).file_name(filename).file(f).build()).build()
            response = await asyncio.to_thread(self._api_client.im.v1.file.create, request)
        if not response.success():
            raise RuntimeError(f"Feishu file upload failed: code={response.code}, msg={response.msg}")
        return response.data.file_key

    async def receive_file(self, msg: InboundMessage, thread_id: str) -> InboundMessage:
        """Download a Feishu file into the thread uploads directory.

        Returns the sandbox virtual path when the image is persisted successfully.
        """
        if not msg.thread_ts:
            logger.warning("[Feishu] received file message without thread_ts, cannot associate with conversation: %s", msg)
            return msg
        files = msg.files
        if not files:
            logger.warning("[Feishu] received message with no files: %s", msg)
            return msg
        text = msg.text
        for file in files:
            if file.get("image_key"):
                virtual_path = await self._receive_single_file(msg.thread_ts, file["image_key"], "image", thread_id)
                text = text.replace("[image]", virtual_path, 1)
            elif file.get("file_key"):
                virtual_path = await self._receive_single_file(msg.thread_ts, file["file_key"], "file", thread_id)
                text = text.replace("[file]", virtual_path, 1)
        msg.text = text
        return msg

    async def materialize_published_files(
        self,
        msg: InboundMessage,
        thread_id: str,
        *,
        owner_user_id: str,
        max_input_bytes: int,
    ) -> tuple[InboundMessage, int]:
        """Stream published attachments into one trusted owner scope.

        The caller must resolve ``owner_user_id`` from the published Agent; it
        must never come from Feishu message metadata. Downloads use
        authenticated streaming HTTP, enforce count, per-file, and aggregate
        actual-byte limits, then expose the fully admitted set to the same
        owner-scoped sandbox. Cancellation closes the network response and
        removes partial host files. A blocked non-mounted sandbox upload is
        handed to a tracked cleanup task that removes both host and sandbox
        residues when the worker exits.

        Args:
            msg: Verified inbound message containing Feishu resource keys and
                matching ``[image]`` or ``[file]`` placeholders.
            thread_id: Trusted runtime thread receiving the files.
            owner_user_id: Trusted published-Agent owner used for host paths,
                sandbox acquisition, and cache ownership checks.
            max_input_bytes: Maximum combined UTF-8 text and actual attachment
                bytes admitted for the request.

        Returns:
            The message with placeholders replaced by sandbox virtual paths,
            plus the total actual attachment byte count.

        Raises:
            ValueError: Attachment metadata, count, filename, empty content,
                per-file size, or aggregate input admission is invalid.
            RuntimeError: Authentication, download, or sandbox synchronization
                fails.
            PermissionError: The thread sandbox is bound to another owner.
            asyncio.CancelledError: The caller is cancelled after bounded or
                recoverable cleanup has been arranged.
        """
        if not msg.thread_ts:
            raise ValueError("Feishu attachment message is missing its message ID")
        if len(msg.files) > FEISHU_PUBLISHED_INBOUND_MAX_FILES:
            raise ValueError("Feishu attachment count exceeds the published input limit")

        text = msg.text
        total_bytes = 0
        created_paths: list[Path] = []
        materialized_files: list[_MaterializedInboundFile] = []
        try:
            for file_info in msg.files:
                if not isinstance(file_info, dict):
                    raise ValueError("Feishu attachment metadata is invalid")
                if file_info.get("image_key"):
                    file_key = file_info["image_key"]
                    resource_type: Literal["image", "file"] = "image"
                    placeholder = "[image]"
                elif file_info.get("file_key"):
                    file_key = file_info["file_key"]
                    resource_type = "file"
                    placeholder = "[file]"
                else:
                    raise ValueError("Feishu attachment metadata has no resource key")

                remaining_bytes = max_input_bytes - total_bytes - len(text.encode("utf-8"))
                if remaining_bytes <= 0:
                    raise ValueError("Feishu attachments exceed the published input quota")
                materialized = await self._materialize_published_file(
                    msg.thread_ts,
                    str(file_key),
                    resource_type,
                    thread_id,
                    owner_user_id=owner_user_id,
                    max_bytes=min(FEISHU_INBOUND_FILE_MAX_BYTES, remaining_bytes),
                )
                materialized_files.append(materialized)
                created_paths.append(materialized.actual_path)
                total_bytes += materialized.size
                text = text.replace(placeholder, materialized.virtual_path, 1)
                if total_bytes + len(text.encode("utf-8")) > max_input_bytes:
                    raise ValueError("Feishu attachments exceed the published input quota")
            await self._sync_published_files(
                materialized_files,
                thread_id=thread_id,
                owner_user_id=owner_user_id,
                message_id=msg.thread_ts,
            )
        except BaseException:
            for created_path in created_paths:
                try:
                    created_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("[Feishu] failed to clean rejected inbound file: %s", created_path)
            raise

        msg.text = text
        return msg, total_bytes

    async def _materialize_published_file(
        self,
        message_id: str,
        file_key: str,
        resource_type: Literal["image", "file"],
        thread_id: str,
        *,
        owner_user_id: str,
        max_bytes: int,
    ) -> _MaterializedInboundFile:
        """Download one resource with bounded memory and remove partial files."""
        from deerflow.uploads.manager import (
            claim_unique_filename,
            ensure_uploads_dir,
            normalize_filename,
            open_upload_file_no_symlink,
        )

        paths = get_paths()
        paths.ensure_thread_dirs(thread_id, user_id=owner_user_id)
        uploads_dir = ensure_uploads_dir(thread_id, user_id=owner_user_id).resolve()
        extension = "png" if resource_type == "image" else "bin"
        fallback_filename = f"feishu_{file_key[-12:]}.{extension}"
        domain = str(self.config.get("domain", "https://open.feishu.cn")).rstrip("/")
        app_id = str(self.config.get("app_id", ""))
        app_secret = str(self.config.get("app_secret", ""))
        if not app_id or not app_secret:
            raise RuntimeError("Feishu channel requires app_id and app_secret")
        resource_url = f"{domain}/open-apis/im/v1/messages/{quote(message_id, safe='')}/resources/{quote(file_key, safe='')}"
        resolved_target: Path | None = None
        total_bytes = 0
        try:
            async with asyncio.timeout(FEISHU_PUBLISHED_DOWNLOAD_TIMEOUT_SECONDS):
                async with self._published_http_client_factory() as client:
                    token_response = await client.post(
                        f"{domain}/open-apis/auth/v3/tenant_access_token/internal",
                        json={"app_id": app_id, "app_secret": app_secret},
                    )
                    token_response.raise_for_status()
                    token_payload = token_response.json()
                    if token_payload.get("code") not in {0, None}:
                        raise RuntimeError(f"Feishu tenant token request failed: code={token_payload.get('code')}, msg={token_payload.get('msg', '')}")
                    tenant_token = token_payload.get("tenant_access_token")
                    if not isinstance(tenant_token, str) or not tenant_token:
                        raise RuntimeError("Feishu tenant token response is missing a token")

                    async with client.stream(
                        "GET",
                        resource_url,
                        params={"type": resource_type},
                        headers={"Authorization": f"Bearer {tenant_token}"},
                    ) as response:
                        response.raise_for_status()
                        content_length_header = response.headers.get("content-length")
                        if content_length_header is not None:
                            try:
                                content_length = int(content_length_header)
                            except ValueError as exc:
                                raise ValueError("Feishu resource Content-Length is invalid") from exc
                            if content_length < 0 or content_length > max_bytes:
                                raise ValueError("Feishu inbound resource exceeds size limit")

                        disposition = Message()
                        disposition["content-disposition"] = response.headers.get(
                            "content-disposition",
                            "",
                        )
                        raw_filename = disposition.get_filename() or fallback_filename
                        with self._thread_lock:
                            seen_names = {entry.name for entry in uploads_dir.iterdir() if entry.is_file()}
                            safe_name = claim_unique_filename(normalize_filename(raw_filename), seen_names)
                            resolved_target, file_handle = open_upload_file_no_symlink(
                                uploads_dir,
                                safe_name,
                            )
                        with file_handle:
                            async for chunk in response.aiter_raw():
                                total_bytes += len(chunk)
                                if total_bytes > max_bytes:
                                    raise ValueError("Feishu inbound resource exceeds size limit")
                                file_handle.write(chunk)
            if total_bytes == 0:
                raise ValueError("Feishu inbound resource is empty")
        except BaseException:
            if resolved_target is not None:
                resolved_target.unlink(missing_ok=True)
            raise

        assert resolved_target is not None
        return _MaterializedInboundFile(
            virtual_path=f"{VIRTUAL_PATH_PREFIX}/uploads/{resolved_target.name}",
            actual_path=resolved_target,
            size=total_bytes,
        )

    def _attachment_cleanup_outbox_dir(self) -> Path:
        return get_paths().base_dir / "published-attachment-cleanup"

    def _attachment_cleanup_job_path(self, job_id: str) -> Path:
        return self._attachment_cleanup_outbox_dir() / f"{job_id}.json"

    @staticmethod
    def _write_attachment_cleanup_job(path: Path, job: _PublishedAttachmentCleanupJob) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        generation_path = _cleanup_binding_generation_path(path.parent, job.binding_id)
        with FileLock(str(generation_path.with_suffix(".lock")), timeout=2.0):
            try:
                temp_path.write_text(json.dumps(job.to_dict(), ensure_ascii=False, sort_keys=True), encoding="utf-8")
                for attempt in range(5):
                    try:
                        temp_path.replace(path)
                        break
                    except PermissionError:
                        if attempt == 4:
                            raise
                        # Windows denies replacement while a concurrent
                        # bounded discovery reader still owns the file handle.
                        time.sleep(0.01)
                _bump_cleanup_binding_generation_locked(path.parent, job.binding_id)
                _write_cleanup_binding_index(path.parent, job)
            finally:
                temp_path.unlink(missing_ok=True)

    @staticmethod
    def _read_attachment_cleanup_job(path: Path) -> _PublishedAttachmentCleanupJob:
        if path.stat().st_size > FEISHU_ATTACHMENT_CLEANUP_JOB_MAX_BYTES:
            raise ValueError("attachment cleanup job exceeds size limit")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("attachment cleanup job is not an object")
        job = _PublishedAttachmentCleanupJob.from_dict(payload)
        if path.stem != job.job_id:
            raise ValueError("attachment cleanup job id does not match its filename")
        return job

    @classmethod
    def _renew_attachment_cleanup_producer_lease(
        cls,
        path: Path,
        producer_token: str,
    ) -> _PublishedAttachmentCleanupJob | None:
        with FileLock(str(path.with_suffix(".lock")), timeout=2.0):
            if not path.exists():
                return None
            current = cls._read_attachment_cleanup_job(path)
            if current.phase != "producer_pending" or current.producer_token != producer_token:
                return None
            updated = replace(
                current,
                producer_lease_expires_at=time.time() + FEISHU_ATTACHMENT_PRODUCER_LEASE_SECONDS,
                version=current.version + 1,
            )
            cls._write_attachment_cleanup_job(path, updated)
            return updated

    @classmethod
    def _mark_attachment_cleanup_ready(
        cls,
        path: Path,
        *,
        producer_token: str | None,
        now: float | None = None,
    ) -> _PublishedAttachmentCleanupJob | None:
        with FileLock(str(path.with_suffix(".lock")), timeout=2.0):
            if not path.exists():
                return None
            current = cls._read_attachment_cleanup_job(path)
            if current.phase != "producer_pending":
                return current
            if producer_token is not None:
                if current.producer_token != producer_token:
                    return None
            elif current.producer_lease_expires_at is None or current.producer_lease_expires_at > (now or time.time()):
                return None
            updated = replace(
                current,
                phase="ready_to_delete",
                producer_token=None,
                producer_lease_expires_at=None,
                version=current.version + 1,
            )
            cls._write_attachment_cleanup_job(path, updated)
            return updated

    @classmethod
    def _claim_attachment_cleanup_job(
        cls,
        path: Path,
        claim_token: str,
        *,
        now: float | None = None,
    ) -> _PublishedAttachmentCleanupJob | None:
        current_time = now or time.time()
        with FileLock(str(path.with_suffix(".lock")), timeout=2.0):
            if not path.exists():
                return None
            current = cls._read_attachment_cleanup_job(path)
            claimable = current.phase == "ready_to_delete" or (current.phase == "deleting" and current.claim_lease_expires_at is not None and current.claim_lease_expires_at <= current_time)
            if not claimable:
                return None
            updated = replace(
                current,
                phase="deleting",
                claim_token=claim_token,
                claim_lease_expires_at=current_time + FEISHU_ATTACHMENT_CLAIM_LEASE_SECONDS,
                version=current.version + 1,
            )
            cls._write_attachment_cleanup_job(path, updated)
            return updated

    @classmethod
    def _release_attachment_cleanup_claim(
        cls,
        path: Path,
        claim_token: str,
    ) -> _PublishedAttachmentCleanupJob | None:
        with FileLock(str(path.with_suffix(".lock")), timeout=2.0):
            if not path.exists():
                return None
            current = cls._read_attachment_cleanup_job(path)
            if current.phase != "deleting" or current.claim_token != claim_token:
                return current
            updated = replace(
                current,
                phase="ready_to_delete",
                claim_token=None,
                claim_lease_expires_at=None,
                version=current.version + 1,
            )
            cls._write_attachment_cleanup_job(path, updated)
            return updated

    async def _persist_attachment_cleanup_job(
        self,
        *,
        thread_id: str,
        owner_user_id: str,
        files: list[_MaterializedInboundFile],
        producer_pending: bool = False,
    ) -> _PublishedAttachmentCleanupJob:
        virtual_paths = tuple(dict.fromkeys(materialized.virtual_path for materialized in files))
        producer_token = uuid.uuid4().hex if producer_pending else None
        job = _PublishedAttachmentCleanupJob(
            job_id=uuid.uuid4().hex,
            binding_id=self.binding_id or "",
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            virtual_paths=virtual_paths,
            phase="producer_pending" if producer_pending else "ready_to_delete",
            producer_token=producer_token,
            producer_lease_expires_at=(time.time() + FEISHU_ATTACHMENT_PRODUCER_LEASE_SECONDS if producer_pending else None),
        )
        if producer_token is not None:
            _ACTIVE_ATTACHMENT_PRODUCERS.add(producer_token)
        self._mark_attachment_cleanup_unhealthy()
        try:
            await asyncio.to_thread(
                self._write_attachment_cleanup_job,
                self._attachment_cleanup_job_path(job.job_id),
                job,
            )
        except Exception:
            # Keep the in-process cleanup alive even when the durable recovery
            # medium is unavailable. The unhealthy flag and critical log make
            # the loss of restart recovery visible to operators.
            logger.critical(
                "[Feishu] attachment cleanup outbox write failed: job=%s",
                job.job_id,
                exc_info=True,
            )
            self._schedule_attachment_cleanup_health_update(False)
        return job

    async def _complete_attachment_cleanup_job(self, job: _PublishedAttachmentCleanupJob) -> bool:
        path = self._attachment_cleanup_job_path(job.job_id)

        def remove() -> bool:
            with FileLock(str(path.with_suffix(".lock")), timeout=2.0):
                if not path.exists():
                    return True
                current = self._read_attachment_cleanup_job(path)
                if current.phase != "deleting" or current.claim_token != job.claim_token:
                    return False
                generation_path = _cleanup_binding_generation_path(path.parent, current.binding_id)
                with FileLock(str(generation_path.with_suffix(".lock")), timeout=2.0):
                    path.unlink(missing_ok=True)
                    _remove_cleanup_binding_index(path.parent, current.binding_id, current.job_id)
                    _bump_cleanup_binding_generation_locked(path.parent, current.binding_id)
            return True

        completed = await asyncio.to_thread(remove)
        if completed and job.claim_token is not None:
            _ACTIVE_ATTACHMENT_CLAIMS.discard(job.claim_token)
        return completed

    async def _refresh_attachment_cleanup_health(self) -> bool:
        generation = self._attachment_cleanup_generation_snapshot()
        outbox_dir = self._attachment_cleanup_outbox_dir()
        backlog, invalid = await asyncio.to_thread(
            _binding_cleanup_index_has_backlog,
            outbox_dir,
            self.binding_id or "",
        )
        healthy = not backlog and not invalid
        projected = self._commit_attachment_cleanup_health(generation, healthy=healthy)
        if projected:
            stable_generation = await asyncio.to_thread(
                _read_cleanup_binding_generation,
                outbox_dir,
                self.binding_id or "",
            )
            with self._attachment_cleanup_state_lock:
                if self._attachment_cleanup_generation == generation:
                    self._attachment_cleanup_store_generation = stable_generation
                else:
                    self._attachment_cleanup_unhealthy = True
                    projected = False
        return projected

    async def _refresh_attachment_cleanup_health_for_startup(self) -> bool:
        """Project startup cleanup health through the deadline-bound scanner pool."""
        generation = self._attachment_cleanup_generation_snapshot()
        backlog = await asyncio.to_thread(
            has_published_attachment_cleanup_backlog,
            self.binding_id or "",
        )
        return self._commit_attachment_cleanup_health(generation, healthy=not backlog)

    async def _acquire_published_sandbox(
        self,
        sandbox_provider: Any,
        thread_id: str,
        *,
        owner_user_id: str,
        timeout_seconds: float | None = None,
    ) -> str:
        managed_acquire = getattr(sandbox_provider, "acquire_with_lease_async", None)
        acquire_async = getattr(sandbox_provider, "acquire_async", None)
        if callable(managed_acquire):
            acquire_task = asyncio.create_task(managed_acquire(thread_id, user_id=owner_user_id))
        elif callable(acquire_async):
            acquire_task = asyncio.create_task(acquire_async(thread_id, user_id=owner_user_id))
        else:
            acquire_task = asyncio.create_task(
                asyncio.to_thread(
                    sandbox_provider.acquire,
                    thread_id,
                    user_id=owner_user_id,
                )
            )
        try:
            result = await asyncio.wait_for(
                asyncio.shield(acquire_task),
                timeout=timeout_seconds or FEISHU_SANDBOX_ACQUIRE_TIMEOUT_SECONDS,
            )
            if isinstance(result, SandboxAcquisition):
                try:
                    await asyncio.to_thread(sandbox_provider.accept_acquisition, result)
                except BaseException:
                    await asyncio.to_thread(sandbox_provider.abandon_acquisition, result)
                    raise
                return result.sandbox_id
            if isinstance(result, str):
                return result
            raise TypeError("sandbox provider returned an invalid acquisition result")
        except asyncio.CancelledError:
            self._track_late_sandbox_acquisition(
                acquire_task,
                sandbox_provider,
                thread_id=thread_id,
            )
            raise
        except TimeoutError as exc:
            self._track_late_sandbox_acquisition(
                acquire_task,
                sandbox_provider,
                thread_id=thread_id,
            )
            raise TimeoutError("Feishu sandbox acquisition exceeded the admission deadline") from exc

    def _track_late_sandbox_acquisition(
        self,
        acquire_task: asyncio.Task[Any],
        sandbox_provider: Any,
        *,
        thread_id: str,
    ) -> None:
        cleanup_task = asyncio.create_task(self._release_late_sandbox_acquisition(acquire_task, sandbox_provider))
        self._track_cleanup_task(
            cleanup_task,
            name="published_sandbox_acquisition_cleanup",
            msg_id=thread_id,
        )

    async def _release_late_sandbox_acquisition(
        self,
        acquire_task: asyncio.Task[Any],
        sandbox_provider: Any,
    ) -> None:
        """Bound late waiting and let the provider compensate owned capacity."""
        try:
            acquisition = await asyncio.wait_for(
                asyncio.shield(acquire_task),
                timeout=FEISHU_SANDBOX_LATE_ACQUIRE_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            acquire_task.cancel()
            raise
        except TimeoutError:
            acquire_task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(acquire_task, return_exceptions=True),
                    timeout=FEISHU_SANDBOX_LATE_ACQUIRE_CANCEL_DRAIN_SECONDS,
                )
            except TimeoutError:
                pass
            self._mark_attachment_cleanup_unhealthy()
            self._schedule_attachment_cleanup_health_update(False)
            logger.error("[Feishu] sandbox acquisition did not terminate after its compensation deadline")
            return
        except Exception:
            return
        if not isinstance(acquisition, SandboxAcquisition):
            logger.warning("[Feishu] legacy sandbox provider returned a late naked ID; skipping unsafe release")
            return
        try:
            await asyncio.to_thread(sandbox_provider.abandon_acquisition, acquisition)
        except Exception:
            logger.warning(
                "[Feishu] failed to abandon sandbox acquisition: %s",
                acquisition.acquisition_token,
                exc_info=True,
            )

    @staticmethod
    async def _delete_published_sandbox_files(
        sandbox: Any,
        files: list[_MaterializedInboundFile],
    ) -> list[_MaterializedInboundFile]:
        """Delete remote files with bounded retry and return unconfirmed files."""
        pending = list(dict.fromkeys(files))
        for attempt in range(1, FEISHU_ATTACHMENT_DELETE_MAX_ATTEMPTS + 1):
            failed: list[_MaterializedInboundFile] = []
            for materialized in pending:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(sandbox.delete_file, materialized.virtual_path),
                        timeout=FEISHU_ATTACHMENT_DELETE_TIMEOUT_SECONDS,
                    )
                except Exception:
                    failed.append(materialized)
                    logger.warning(
                        "[Feishu] failed to clean sandbox attachment %s (attempt %d/%d)",
                        materialized.virtual_path,
                        attempt,
                        FEISHU_ATTACHMENT_DELETE_MAX_ATTEMPTS,
                        exc_info=True,
                    )
            if not failed:
                return []
            pending = failed
            if attempt < FEISHU_ATTACHMENT_DELETE_MAX_ATTEMPTS:
                await asyncio.sleep(FEISHU_ATTACHMENT_DELETE_RETRY_SECONDS * (2 ** (attempt - 1)))
        return pending

    @staticmethod
    async def _delete_published_host_files(files: list[_MaterializedInboundFile]) -> bool:
        success = True
        for materialized in files:
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(materialized.actual_path.unlink, missing_ok=True),
                    timeout=FEISHU_ATTACHMENT_DELETE_TIMEOUT_SECONDS,
                )
            except (OSError, TimeoutError):
                success = False
                logger.warning(
                    "[Feishu] failed to clean host attachment: %s",
                    materialized.actual_path,
                    exc_info=True,
                )
        return success

    async def _claim_cleanup_for_execution(
        self,
        job: _PublishedAttachmentCleanupJob,
    ) -> _PublishedAttachmentCleanupJob | None:
        if job.phase == "deleting" and job.claim_token in _ACTIVE_ATTACHMENT_CLAIMS:
            return job
        claim_token = uuid.uuid4().hex
        path = self._attachment_cleanup_job_path(job.job_id)
        claimed = await asyncio.to_thread(
            self._claim_attachment_cleanup_job,
            path,
            claim_token,
        )
        if claimed is not None:
            _ACTIVE_ATTACHMENT_CLAIMS.add(claim_token)
            return claimed
        if not path.exists():
            # Preserve best-effort in-memory cleanup if writing the durable
            # outbox failed before execution began.
            claimed = replace(
                job,
                phase="deleting",
                claim_token=claim_token,
                claim_lease_expires_at=time.time() + FEISHU_ATTACHMENT_CLAIM_LEASE_SECONDS,
            )
            _ACTIVE_ATTACHMENT_CLAIMS.add(claim_token)
            return claimed
        return None

    async def _release_cleanup_claim(self, job: _PublishedAttachmentCleanupJob) -> None:
        if job.claim_token is None:
            return
        await asyncio.to_thread(
            self._release_attachment_cleanup_claim,
            self._attachment_cleanup_job_path(job.job_id),
            job.claim_token,
        )
        _ACTIVE_ATTACHMENT_CLAIMS.discard(job.claim_token)

    async def _execute_attachment_cleanup_job(
        self,
        job: _PublishedAttachmentCleanupJob,
        sandbox: Any,
        files: list[_MaterializedInboundFile],
        *,
        refresh_health: bool = True,
    ) -> bool:
        claimed_job = await self._claim_cleanup_for_execution(job)
        if claimed_job is None:
            self._mark_attachment_cleanup_unhealthy()
            return False
        completed = False
        try:
            unconfirmed = await self._delete_published_sandbox_files(sandbox, files)
            if unconfirmed:
                self._mark_attachment_cleanup_unhealthy()
                logger.error(
                    "[Feishu] durable attachment cleanup remains pending after retries: job=%s files=%s",
                    job.job_id,
                    [item.virtual_path for item in unconfirmed],
                )
                self._schedule_attachment_cleanup_health_update(False)
                return False
            if not await self._delete_published_host_files(files):
                self._mark_attachment_cleanup_unhealthy()
                self._schedule_attachment_cleanup_health_update(False)
                return False
            completed = await self._complete_attachment_cleanup_job(claimed_job)
            if not completed:
                self._mark_attachment_cleanup_unhealthy()
                return False
            if refresh_health:
                await self._refresh_attachment_cleanup_health()
            return True
        finally:
            if not completed:
                await self._release_cleanup_claim(claimed_job)

    def _materialized_files_from_cleanup_job(
        self,
        job: _PublishedAttachmentCleanupJob,
    ) -> list[_MaterializedInboundFile]:
        paths = get_paths()
        return [
            _MaterializedInboundFile(
                virtual_path=virtual_path,
                actual_path=paths.resolve_virtual_path(
                    job.thread_id,
                    virtual_path,
                    user_id=job.owner_user_id,
                ),
                size=0,
            )
            for virtual_path in job.virtual_paths
        ]

    async def _recover_attachment_cleanup_job(
        self,
        job: _PublishedAttachmentCleanupJob,
        sandbox_provider: Any,
        *,
        acquire_timeout_seconds: float,
        refresh_health: bool = True,
    ) -> bool:
        if job.phase == "producer_pending":
            producer_active = job.producer_token in _ACTIVE_ATTACHMENT_PRODUCERS
            lease_active = job.producer_lease_expires_at is not None and job.producer_lease_expires_at > time.time()
            if producer_active or lease_active:
                self._mark_attachment_cleanup_unhealthy()
                return False
            promoted = await asyncio.to_thread(
                self._mark_attachment_cleanup_ready,
                self._attachment_cleanup_job_path(job.job_id),
                producer_token=None,
            )
            if promoted is None:
                self._mark_attachment_cleanup_unhealthy()
                return False
            job = promoted
        if job.phase == "deleting" and job.claim_token not in _ACTIVE_ATTACHMENT_CLAIMS and job.claim_lease_expires_at is not None and job.claim_lease_expires_at > time.time():
            self._mark_attachment_cleanup_unhealthy()
            return False
        files = self._materialized_files_from_cleanup_job(job)
        sandbox_id = await self._acquire_published_sandbox(
            sandbox_provider,
            job.thread_id,
            owner_user_id=job.owner_user_id,
            timeout_seconds=acquire_timeout_seconds,
        )
        if sandbox_id == "local" or sandbox_provider.uses_thread_data_mounts:
            claimed_job = await self._claim_cleanup_for_execution(job)
            if claimed_job is None:
                self._mark_attachment_cleanup_unhealthy()
                return False
            mounted_completed = False
            try:
                if await self._delete_published_host_files(files):
                    mounted_completed = await self._complete_attachment_cleanup_job(claimed_job)
                if not mounted_completed:
                    self._mark_attachment_cleanup_unhealthy()
                elif refresh_health:
                    await self._refresh_attachment_cleanup_health()
                return mounted_completed
            finally:
                if not mounted_completed:
                    await self._release_cleanup_claim(claimed_job)
        sandbox = sandbox_provider.get(sandbox_id)
        if sandbox is None:
            raise RuntimeError(f"Sandbox not found for attachment cleanup thread {job.thread_id}")
        return await self._execute_attachment_cleanup_job(
            job,
            sandbox,
            files,
            refresh_health=refresh_health,
        )

    async def recover_published_attachment_cleanups(self) -> int:
        """Retry durable attachment cleanups owned by this Feishu binding."""
        loop = asyncio.get_running_loop()
        recovery_deadline = loop.time() + FEISHU_ATTACHMENT_RECOVERY_TIMEOUT_SECONDS
        generation = self._attachment_cleanup_generation_snapshot()
        outbox_dir = self._attachment_cleanup_outbox_dir()
        try:
            store_generation = await asyncio.wait_for(
                asyncio.to_thread(
                    _read_cleanup_binding_generation,
                    outbox_dir,
                    self.binding_id or "",
                ),
                timeout=max(0.0, recovery_deadline - loop.time()),
            )
            scanned_jobs, invalid, discovery_timed_out = await asyncio.wait_for(
                asyncio.to_thread(
                    _scan_all_cleanup_jobs,
                    deadline=time.monotonic() + max(0.0, recovery_deadline - loop.time()),
                ),
                timeout=max(0.0, recovery_deadline - loop.time()),
            )
            jobs = [job for job in scanned_jobs if job.binding_id == (self.binding_id or "")]
        except TimeoutError:
            self._mark_attachment_cleanup_unhealthy()
            logger.error("[Feishu] attachment cleanup discovery exceeded the recovery deadline")
            return 0
        if invalid or discovery_timed_out:
            self._mark_attachment_cleanup_unhealthy()
        try:
            store_stable = store_generation == await asyncio.wait_for(
                asyncio.to_thread(
                    _read_cleanup_binding_generation,
                    outbox_dir,
                    self.binding_id or "",
                ),
                timeout=max(0.0, recovery_deadline - loop.time()),
            )
        except TimeoutError:
            self._mark_attachment_cleanup_unhealthy()
            logger.error("[Feishu] attachment cleanup generation check exceeded the recovery deadline")
            return 0
        if not jobs:
            await self._refresh_attachment_cleanup_health()
            return 0

        try:
            selected_jobs = await asyncio.wait_for(
                asyncio.to_thread(
                    _select_cleanup_jobs,
                    jobs,
                    cursor_scope=self.binding_id or "unbound",
                    limit=FEISHU_ATTACHMENT_RECOVERY_MAX_JOBS,
                ),
                timeout=max(0.0, recovery_deadline - loop.time()),
            )
        except TimeoutError:
            self._mark_attachment_cleanup_unhealthy()
            logger.error("[Feishu] attachment cleanup scheduling exceeded the recovery deadline")
            return 0
        if not selected_jobs:
            self._commit_attachment_cleanup_health(generation, healthy=False)
            return 0

        completed = 0
        try:
            sandbox_provider = get_sandbox_provider()
        except Exception:
            self._mark_attachment_cleanup_unhealthy()
            logger.error("[Feishu] attachment cleanup recovery could not load the sandbox provider", exc_info=True)
            return 0
        for job in selected_jobs:
            try:
                remaining_seconds = recovery_deadline - loop.time()
                if remaining_seconds <= 0:
                    self._mark_attachment_cleanup_unhealthy()
                    break
                if await asyncio.wait_for(
                    self._recover_attachment_cleanup_job(
                        job,
                        sandbox_provider,
                        acquire_timeout_seconds=min(
                            FEISHU_SANDBOX_ACQUIRE_TIMEOUT_SECONDS,
                            remaining_seconds,
                        ),
                        refresh_health=False,
                    ),
                    timeout=remaining_seconds,
                ):
                    completed += 1
            except Exception:
                self._mark_attachment_cleanup_unhealthy()
                logger.error(
                    "[Feishu] durable attachment cleanup recovery failed: job=%s",
                    job.job_id,
                    exc_info=True,
                )
        if completed == len(jobs) and store_stable:
            try:
                await asyncio.wait_for(
                    self._refresh_attachment_cleanup_health(),
                    timeout=max(0.0, recovery_deadline - loop.time()),
                )
            except TimeoutError:
                self._mark_attachment_cleanup_unhealthy()
                logger.error("[Feishu] attachment cleanup health refresh exceeded the recovery deadline")
        else:
            self._commit_attachment_cleanup_health(generation, healthy=False)
        return completed

    async def _retry_published_attachment_cleanups(self) -> None:
        """Periodically retry the durable cleanup outbox while the binding runs."""
        while not self._stop_requested:
            try:
                await self.recover_published_attachment_cleanups()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._mark_attachment_cleanup_unhealthy()
                logger.exception("[Feishu] periodic attachment cleanup recovery failed")
            if self._runtime_health_callback is not None:
                try:
                    await self._runtime_health_callback(
                        self.attachment_cleanup_healthy,
                        None if self.attachment_cleanup_healthy else "Attachment cleanup recovery is pending",
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Health is a projection. A transient repository failure
                    # must not terminate the cleanup coordinator; the next
                    # pass retries both cleanup and the dirty projection.
                    logger.exception("[Feishu] attachment cleanup health projection failed")
            await asyncio.sleep(FEISHU_ATTACHMENT_CLEANUP_RETRY_INTERVAL_SECONDS)

    def _schedule_attachment_cleanup_health_update(self, healthy: bool) -> None:
        """Report cleanup health without making critical cleanup wait on Supervisor."""
        if self._runtime_health_callback is None:
            return
        task = asyncio.create_task(
            self._runtime_health_callback(
                healthy,
                None if healthy else "Attachment cleanup recovery is pending",
            )
        )
        self._track_background_task(
            task,
            name="published_attachment_cleanup_health",
            msg_id=self.binding_id or "unknown",
        )

    async def _finish_cancelled_sandbox_sync(
        self,
        sync_task: asyncio.Task[object],
        sandbox: Any,
        materialized_files: list[_MaterializedInboundFile],
        cleanup_job: _PublishedAttachmentCleanupJob,
    ) -> None:
        """Finish an uncancellable worker and remove every possible residue."""
        cleanup_job = await self._wait_for_attachment_producer(sync_task, cleanup_job)
        await self._execute_attachment_cleanup_job(
            cleanup_job,
            sandbox,
            materialized_files,
        )

    async def _wait_for_attachment_producer(
        self,
        sync_task: asyncio.Task[object],
        cleanup_job: _PublishedAttachmentCleanupJob,
    ) -> _PublishedAttachmentCleanupJob:
        """Keep the durable producer lease alive until the worker really exits."""
        producer_token = cleanup_job.producer_token
        if cleanup_job.phase != "producer_pending" or producer_token is None:
            await asyncio.gather(sync_task, return_exceptions=True)
            return cleanup_job
        path = self._attachment_cleanup_job_path(cleanup_job.job_id)
        current = cleanup_job
        try:
            while not sync_task.done():
                done, _pending = await asyncio.wait(
                    {sync_task},
                    timeout=FEISHU_ATTACHMENT_PRODUCER_HEARTBEAT_SECONDS,
                )
                if done:
                    break
                renewed = await asyncio.to_thread(
                    self._renew_attachment_cleanup_producer_lease,
                    path,
                    producer_token,
                )
                if renewed is not None:
                    current = renewed
            await asyncio.gather(sync_task, return_exceptions=True)
            ready = await asyncio.to_thread(
                self._mark_attachment_cleanup_ready,
                path,
                producer_token=producer_token,
            )
            return ready or replace(
                current,
                phase="ready_to_delete",
                producer_token=None,
                producer_lease_expires_at=None,
            )
        finally:
            _ACTIVE_ATTACHMENT_PRODUCERS.discard(producer_token)

    async def _arrange_published_sandbox_cleanup(
        self,
        sync_task: asyncio.Task[object],
        sandbox: Any,
        materialized_files: list[_MaterializedInboundFile],
        *,
        thread_id: str,
        owner_user_id: str,
        message_id: str,
    ) -> None:
        cleanup_job = await self._persist_attachment_cleanup_job(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            files=materialized_files,
            producer_pending=True,
        )
        done, _pending = await asyncio.wait(
            {sync_task},
            timeout=FEISHU_SANDBOX_SYNC_CLEANUP_TIMEOUT_SECONDS,
        )
        if done:
            cleanup_job = await self._wait_for_attachment_producer(sync_task, cleanup_job)
            await self._execute_attachment_cleanup_job(
                cleanup_job,
                sandbox,
                materialized_files,
            )
            return
        cleanup_task = asyncio.create_task(
            self._finish_cancelled_sandbox_sync(
                sync_task,
                sandbox,
                materialized_files,
                cleanup_job,
            )
        )
        self._track_cleanup_task(
            cleanup_task,
            name="published_attachment_cleanup",
            msg_id=message_id,
        )

    async def _cleanup_published_sandbox_files(
        self,
        sandbox: Any,
        materialized_files: list[_MaterializedInboundFile],
        *,
        thread_id: str,
        owner_user_id: str,
    ) -> None:
        if not materialized_files:
            return
        cleanup_job = await self._persist_attachment_cleanup_job(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            files=materialized_files,
        )
        await self._execute_attachment_cleanup_job(
            cleanup_job,
            sandbox,
            materialized_files,
        )

    async def _sync_published_files(
        self,
        materialized_files: list[_MaterializedInboundFile],
        *,
        thread_id: str,
        owner_user_id: str,
        message_id: str,
    ) -> None:
        """Expose admitted files to the explicitly owner-scoped sandbox."""
        if not materialized_files:
            return
        sandbox_provider = get_sandbox_provider()
        sandbox_id = await self._acquire_published_sandbox(
            sandbox_provider,
            thread_id,
            owner_user_id=owner_user_id,
        )
        if sandbox_id == "local" or sandbox_provider.uses_thread_data_mounts:
            return
        sandbox = sandbox_provider.get(sandbox_id)
        if sandbox is None:
            raise RuntimeError(f"Sandbox not found for thread {thread_id}")

        synced_files: list[_MaterializedInboundFile] = []
        batch_deadline = asyncio.get_running_loop().time() + FEISHU_SANDBOX_SYNC_BATCH_TIMEOUT_SECONDS
        for materialized in materialized_files:
            remaining_batch_seconds = batch_deadline - asyncio.get_running_loop().time()
            if remaining_batch_seconds <= 0:
                await self._cleanup_published_sandbox_files(
                    sandbox,
                    synced_files,
                    thread_id=thread_id,
                    owner_user_id=owner_user_id,
                )
                raise TimeoutError("Feishu sandbox sync exceeded the admission deadline")
            sync_task = asyncio.create_task(
                asyncio.to_thread(
                    sandbox.update_file_from_path,
                    materialized.virtual_path,
                    str(materialized.actual_path),
                )
            )
            try:
                await asyncio.wait_for(
                    asyncio.shield(sync_task),
                    timeout=min(
                        FEISHU_SANDBOX_SYNC_FILE_TIMEOUT_SECONDS,
                        remaining_batch_seconds,
                    ),
                )
                synced_files.append(materialized)
            except asyncio.CancelledError:
                await self._arrange_published_sandbox_cleanup(
                    sync_task,
                    sandbox,
                    [*synced_files, materialized],
                    thread_id=thread_id,
                    owner_user_id=owner_user_id,
                    message_id=message_id,
                )
                raise
            except TimeoutError as exc:
                await self._arrange_published_sandbox_cleanup(
                    sync_task,
                    sandbox,
                    [*synced_files, materialized],
                    thread_id=thread_id,
                    owner_user_id=owner_user_id,
                    message_id=message_id,
                )
                raise TimeoutError("Feishu sandbox sync exceeded the admission deadline") from exc
            except BaseException:
                await self._cleanup_published_sandbox_files(
                    sandbox,
                    [*synced_files, materialized],
                    thread_id=thread_id,
                    owner_user_id=owner_user_id,
                )
                raise

    async def _receive_single_file(self, message_id: str, file_key: str, type: Literal["image", "file"], thread_id: str) -> str:
        request = self._GetMessageResourceRequest.builder().message_id(message_id).file_key(file_key).type(type).build()

        def inner():
            return self._api_client.im.v1.message_resource.get(request)

        try:
            response = await asyncio.to_thread(inner)
        except Exception:
            logger.exception("[Feishu] resource get request failed for resource_key=%s type=%s", file_key, type)
            return f"Failed to obtain the [{type}]"

        if not response.success():
            logger.warning(
                "[Feishu] resource get failed: resource_key=%s, type=%s, code=%s, msg=%s, log_id=%s ",
                file_key,
                type,
                response.code,
                response.msg,
                response.get_log_id(),
            )
            return f"Failed to obtain the [{type}]"

        image_stream = getattr(response, "file", None)
        if image_stream is None:
            logger.warning("[Feishu] resource get returned no file stream: resource_key=%s, type=%s", file_key, type)
            return f"Failed to obtain the [{type}]"

        try:
            content = await asyncio.to_thread(_read_inbound_resource, image_stream)
        except ValueError as exc:
            logger.warning(
                "[Feishu] rejected inbound resource: resource_key=%s, type=%s, reason=%s",
                file_key,
                type,
                exc,
            )
            return f"Failed to obtain the [{type}]"
        except Exception:
            logger.exception("[Feishu] failed to read resource stream: resource_key=%s, type=%s", file_key, type)
            return f"Failed to obtain the [{type}]"

        if not content:
            logger.warning("[Feishu] empty resource content: resource_key=%s, type=%s", file_key, type)
            return f"Failed to obtain the [{type}]"

        paths = get_paths()
        user_id = get_effective_user_id()
        paths.ensure_thread_dirs(thread_id, user_id=user_id)
        uploads_dir = paths.sandbox_uploads_dir(thread_id, user_id=user_id).resolve()

        ext = "png" if type == "image" else "bin"
        raw_filename = getattr(response, "file_name", "") or f"feishu_{file_key[-12:]}.{ext}"

        # Sanitize filename: preserve extension, replace path chars in name part
        if "." in raw_filename:
            name_part, ext = raw_filename.rsplit(".", 1)
            name_part = re.sub(r"[./\\]", "_", name_part)
            filename = f"{name_part}.{ext}"
        else:
            filename = re.sub(r"[./\\]", "_", raw_filename)
        resolved_target = uploads_dir / filename

        def down_load():
            # use thread_lock to avoid filename conflicts when writing
            with self._thread_lock:
                resolved_target.write_bytes(content)

        try:
            await asyncio.to_thread(down_load)
        except Exception:
            logger.exception("[Feishu] failed to persist downloaded resource: %s, type=%s", resolved_target, type)
            return f"Failed to obtain the [{type}]"

        virtual_path = f"{VIRTUAL_PATH_PREFIX}/uploads/{resolved_target.name}"

        try:
            sandbox_provider = get_sandbox_provider()
            sandbox_id = sandbox_provider.acquire(thread_id)
            if sandbox_id != "local":
                sandbox = sandbox_provider.get(sandbox_id)
                if sandbox is None:
                    logger.warning("[Feishu] sandbox not found for thread_id=%s", thread_id)
                    return f"Failed to obtain the [{type}]"
                sandbox.update_file(virtual_path, content)
        except Exception:
            logger.exception("[Feishu] failed to sync resource into non-local sandbox: %s", virtual_path)
            return f"Failed to obtain the [{type}]"

        logger.info("[Feishu] downloaded resource mapped: file_key=%s -> %s", file_key, virtual_path)
        return virtual_path

    # -- message formatting ------------------------------------------------

    @staticmethod
    def _build_card_content(text: str) -> str:
        """Build a Feishu interactive card with markdown content.

        Feishu's interactive card format natively renders markdown, including
        headers, bold/italic, code blocks, lists, and links.
        """
        card = {
            "config": {"wide_screen_mode": True, "update_multi": True},
            "elements": [{"tag": "markdown", "content": text}],
        }
        return json.dumps(card)

    # -- reaction helpers --------------------------------------------------

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        """Add an emoji reaction to a message."""
        if not self._api_client or not self._CreateMessageReactionRequest:
            return
        try:
            request = self._CreateMessageReactionRequest.builder().message_id(message_id).request_body(self._CreateMessageReactionRequestBody.builder().reaction_type(self._Emoji.builder().emoji_type(emoji_type).build()).build()).build()
            await asyncio.to_thread(self._api_client.im.v1.message_reaction.create, request)
            logger.info("[Feishu] reaction '%s' added to message %s", emoji_type, message_id)
        except Exception:
            logger.exception("[Feishu] failed to add reaction '%s' to message %s", emoji_type, message_id)

    async def _reply_card(self, message_id: str, text: str) -> str | None:
        """Reply with an interactive card and return the created card message ID."""
        if not self._api_client:
            return None

        content = self._build_card_content(text)
        request = self._ReplyMessageRequest.builder().message_id(message_id).request_body(self._ReplyMessageRequestBody.builder().msg_type("interactive").content(content).reply_in_thread(True).build()).build()
        response = await asyncio.to_thread(self._api_client.im.v1.message.reply, request)
        response_data = getattr(response, "data", None)
        return getattr(response_data, "message_id", None)

    async def _create_card(self, chat_id: str, text: str) -> None:
        """Create a new card message in the target chat."""
        if not self._api_client:
            return

        content = self._build_card_content(text)
        request = self._CreateMessageRequest.builder().receive_id_type("chat_id").request_body(self._CreateMessageRequestBody.builder().receive_id(chat_id).msg_type("interactive").content(content).build()).build()
        await asyncio.to_thread(self._api_client.im.v1.message.create, request)

    async def _update_card(self, message_id: str, text: str) -> None:
        """Patch an existing card message in place."""
        if not self._api_client or not self._PatchMessageRequest:
            return

        content = self._build_card_content(text)
        request = self._PatchMessageRequest.builder().message_id(message_id).request_body(self._PatchMessageRequestBody.builder().content(content).build()).build()
        await asyncio.to_thread(self._api_client.im.v1.message.patch, request)

    def _track_background_task(self, task: asyncio.Task, *, name: str, msg_id: str) -> None:
        """Keep a strong reference to fire-and-forget tasks and surface errors."""
        self._background_tasks.add(task)
        task.add_done_callback(lambda done_task, task_name=name, mid=msg_id: self._finalize_background_task(done_task, task_name, mid))

    def _track_cleanup_task(self, task: asyncio.Task, *, name: str, msg_id: str) -> None:
        """Track durable cleanup separately so channel stop cannot cancel it."""
        self._cleanup_tasks.add(task)
        task.add_done_callback(lambda done_task, task_name=name, mid=msg_id: self._finalize_cleanup_task(done_task, task_name, mid))

    def _finalize_background_task(self, task: asyncio.Task, name: str, msg_id: str) -> None:
        self._background_tasks.discard(task)
        self._log_task_error(task, name, msg_id)

    def _finalize_cleanup_task(self, task: asyncio.Task, name: str, msg_id: str) -> None:
        self._cleanup_tasks.discard(task)
        self._log_task_error(task, name, msg_id)

    async def _create_running_card(self, source_message_id: str, text: str) -> str | None:
        """Create the running card and cache its message ID when available."""
        running_card_id = await self._reply_card(source_message_id, text)
        if running_card_id:
            self._running_card_ids[source_message_id] = running_card_id
            logger.info("[Feishu] running card created: source=%s card=%s", source_message_id, running_card_id)
        else:
            logger.warning("[Feishu] running card creation returned no message_id for source=%s, subsequent updates will fall back to new replies", source_message_id)
        return running_card_id

    def _ensure_running_card_started(self, source_message_id: str, text: str = "Working on it...") -> asyncio.Task | None:
        """Start running-card creation once per source message."""
        running_card_id = self._running_card_ids.get(source_message_id)
        if running_card_id:
            return None

        running_card_task = self._running_card_tasks.get(source_message_id)
        if running_card_task:
            return running_card_task

        running_card_task = asyncio.create_task(self._create_running_card(source_message_id, text))
        self._running_card_tasks[source_message_id] = running_card_task
        running_card_task.add_done_callback(lambda done_task, mid=source_message_id: self._finalize_running_card_task(mid, done_task))
        return running_card_task

    def _finalize_running_card_task(self, source_message_id: str, task: asyncio.Task) -> None:
        if self._running_card_tasks.get(source_message_id) is task:
            self._running_card_tasks.pop(source_message_id, None)
        self._log_task_error(task, "create_running_card", source_message_id)

    async def _ensure_running_card(self, source_message_id: str, text: str = "Working on it...") -> str | None:
        """Ensure the in-thread running card exists and track its message ID."""
        running_card_id = self._running_card_ids.get(source_message_id)
        if running_card_id:
            return running_card_id

        running_card_task = self._ensure_running_card_started(source_message_id, text)
        if running_card_task is None:
            return self._running_card_ids.get(source_message_id)
        return await running_card_task

    async def _send_running_reply(self, message_id: str) -> None:
        """Reply to a message in-thread with a running card."""
        try:
            await self._ensure_running_card(message_id)
        except Exception:
            logger.exception("[Feishu] failed to send running reply for message %s", message_id)

    async def _send_card_message(self, msg: OutboundMessage) -> None:
        """Send or update the Feishu card tied to the current request."""
        source_message_id = msg.thread_ts
        if source_message_id:
            running_card_id = self._running_card_ids.get(source_message_id)
            awaited_running_card_task = False

            if not running_card_id:
                running_card_task = self._running_card_tasks.get(source_message_id)
                if running_card_task:
                    awaited_running_card_task = True
                    running_card_id = await running_card_task

            if running_card_id:
                try:
                    await self._update_card(running_card_id, msg.text)
                except Exception:
                    if not msg.is_final:
                        raise
                    logger.exception(
                        "[Feishu] failed to patch running card %s, falling back to final reply",
                        running_card_id,
                    )
                    await self._reply_card(source_message_id, msg.text)
                else:
                    logger.info("[Feishu] running card updated: source=%s card=%s", source_message_id, running_card_id)
            elif msg.is_final:
                await self._reply_card(source_message_id, msg.text)
            elif awaited_running_card_task:
                logger.warning(
                    "[Feishu] running card task finished without message_id for source=%s, skipping duplicate non-final creation",
                    source_message_id,
                )
            else:
                await self._ensure_running_card(source_message_id, msg.text)

            if msg.is_final:
                self._running_card_ids.pop(source_message_id, None)
                await self._add_reaction(source_message_id, "DONE")
            return

        await self._create_card(msg.chat_id, msg.text)

    # -- internal ----------------------------------------------------------

    @staticmethod
    def _log_future_error(fut, name: str, msg_id: str) -> None:
        """Callback for run_coroutine_threadsafe futures to surface errors."""
        try:
            exc = fut.exception()
            if exc:
                logger.error("[Feishu] %s failed for msg_id=%s: %s", name, msg_id, exc)
        except Exception:
            pass

    @staticmethod
    def _log_task_error(task: asyncio.Task, name: str, msg_id: str) -> None:
        """Callback for background asyncio tasks to surface errors."""
        try:
            exc = task.exception()
            if exc:
                logger.error("[Feishu] %s failed for msg_id=%s: %s", name, msg_id, exc)
        except asyncio.CancelledError:
            logger.info("[Feishu] %s cancelled for msg_id=%s", name, msg_id)
        except Exception:
            pass

    async def _prepare_inbound(
        self,
        msg_id: str,
        inbound: InboundMessage,
        event_id: str | None = None,
    ) -> None:
        """Claim a trusted event before reactions or MessageBus dispatch."""
        if self.binding_id:
            if not event_id or self._event_deduplicator is None:
                logger.error(
                    "[Feishu] rejecting binding event without durable deduplication",
                    extra={"binding_id": self.binding_id},
                )
                return
            if not await self._event_deduplicator.claim(
                self.binding_id,
                event_id,
                system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE,
            ):
                logger.info(
                    "[Feishu] duplicate event dropped",
                    extra={"binding_id": self.binding_id, "event_id": event_id},
                )
                return
        reaction_task = asyncio.create_task(self._add_reaction(msg_id, "OK"))
        self._track_background_task(reaction_task, name="add_reaction", msg_id=msg_id)
        self._ensure_running_card_started(msg_id)
        await self.bus.publish_inbound(inbound)

    def _on_message(self, event: Any) -> None:
        """Validate and enqueue one SDK message callback on the main loop."""
        if self.binding_id and self._stop_requested:
            logger.info("[Feishu] ignored event received while binding is stopping")
            return
        try:
            logger.info("[Feishu] raw event received: type=%s", type(event).__name__)
            if self.binding_id and (self._event_verifier is None or not self._event_verifier(event)):
                logger.warning(
                    "[Feishu] rejected unauthenticated or stale event",
                    extra={"binding_id": self.binding_id},
                )
                return

            event_header = getattr(event, "header", None)
            event_id = getattr(event_header, "event_id", None)
            event_created_at = FeishuEventVerifier._timestamp(event)
            message = event.event.message
            chat_id = message.chat_id
            msg_id = message.message_id
            sender_id = event.event.sender.sender_id.open_id

            # root_id is set when the message is a reply within a Feishu thread.
            # Use it as topic_id so all replies share the same DeerFlow thread.
            root_id = getattr(message, "root_id", None) or None
            thread_id = getattr(message, "thread_id", None) or None
            raw_chat_type = getattr(message, "chat_type", None)
            chat_type = raw_chat_type if raw_chat_type in ("p2p", "group") else "p2p"

            # Parse message content
            content = json.loads(message.content)

            # files_list store the any-file-key in feishu messages, which can be used to download the file content later
            # In Feishu channel, image_keys are independent of file_keys.
            # The file_key includes files, videos, and audio, but does not include stickers.
            files_list = []

            if "text" in content:
                # Handle plain text messages
                text = content["text"]
            elif "file_key" in content:
                file_key = content.get("file_key")
                if isinstance(file_key, str) and file_key:
                    files_list.append({"file_key": file_key})
                    text = "[file]"
                else:
                    text = ""
            elif "image_key" in content:
                image_key = content.get("image_key")
                if isinstance(image_key, str) and image_key:
                    files_list.append({"image_key": image_key})
                    text = "[image]"
                else:
                    text = ""
            elif "content" in content and isinstance(content["content"], list):
                # Handle rich-text messages with a top-level "content" list (e.g., topic groups/posts)
                text_paragraphs: list[str] = []
                for paragraph in content["content"]:
                    if isinstance(paragraph, list):
                        paragraph_text_parts: list[str] = []
                        for element in paragraph:
                            if isinstance(element, dict):
                                # Include both normal text and @ mentions
                                if element.get("tag") in ("text", "at"):
                                    text_value = element.get("text", "")
                                    if text_value:
                                        paragraph_text_parts.append(text_value)
                                elif element.get("tag") == "img":
                                    image_key = element.get("image_key")
                                    if isinstance(image_key, str) and image_key:
                                        files_list.append({"image_key": image_key})
                                        paragraph_text_parts.append("[image]")
                                elif element.get("tag") in ("file", "media"):
                                    file_key = element.get("file_key")
                                    if isinstance(file_key, str) and file_key:
                                        files_list.append({"file_key": file_key})
                                        paragraph_text_parts.append("[file]")
                        if paragraph_text_parts:
                            # Join text segments within a paragraph with spaces to avoid "helloworld"
                            text_paragraphs.append(" ".join(paragraph_text_parts))

                # Join paragraphs with blank lines to preserve paragraph boundaries
                text = "\n\n".join(text_paragraphs)
            else:
                text = ""
            text = text.strip()

            logger.info(
                "[Feishu] parsed message: chat_id=%s, msg_id=%s, root_id=%s, sender=%s, text=%r",
                chat_id,
                msg_id,
                root_id,
                sender_id,
                text[:100] if text else "",
            )

            if not (text or files_list):
                logger.info("[Feishu] empty text, ignoring message")
                return

            # Only treat known slash commands as commands; absolute paths and
            # other slash-prefixed text should be handled as normal chat.
            if _is_feishu_command(text):
                msg_type = InboundMessageType.COMMAND
            else:
                msg_type = InboundMessageType.CHAT

            # DB-driven bindings keep direct chats stable per user and groups
            # stable per chat/topic. Legacy channels preserve per-message topics.
            topic_id = (root_id or thread_id or None) if self.binding_id else (root_id or msg_id)

            inbound = self._make_inbound(
                chat_id=chat_id,
                user_id=sender_id,
                text=text,
                msg_type=msg_type,
                thread_ts=msg_id,
                files=files_list,
                metadata={
                    "message_id": msg_id,
                    "root_id": root_id,
                    "chat_type": chat_type,
                    **({"event_id": event_id} if isinstance(event_id, str) else {}),
                    **({"binding_id": self.binding_id, "agent_id": self.agent_id} if self.binding_id else {}),
                },
                created_at=event_created_at,
            )
            inbound.topic_id = topic_id

            # Schedule on the async event loop
            if self._main_loop and self._main_loop.is_running():
                logger.info("[Feishu] publishing inbound message to bus (type=%s, msg_id=%s)", msg_type.value, msg_id)
                fut = asyncio.run_coroutine_threadsafe(
                    self._prepare_inbound(msg_id, inbound, event_id if isinstance(event_id, str) else None),
                    self._main_loop,
                )
                fut.add_done_callback(lambda f, mid=msg_id: self._log_future_error(f, "prepare_inbound", mid))
            else:
                logger.warning("[Feishu] main loop not running, cannot publish inbound message")
        except Exception:
            logger.exception("[Feishu] error processing message")
