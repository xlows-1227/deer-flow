"""Async repository and slow-hash lifecycle for Agent API Keys."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.agent_api_key.model import AgentAPIKeyRow
from deerflow.persistence.published_agent.model import PublishedAgentRow

_KEY_RE = re.compile(r"^dfa_([0-9a-f]{32})_([A-Za-z0-9_-]{40,})$")
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _public_dict(row: AgentAPIKeyRow) -> dict[str, Any]:
    data = row.to_dict()
    data.pop("secret_hash", None)
    data["quota_overrides"] = dict(data.pop("quota_overrides_json") or {})
    return data


class AgentAPIKeyRepository:
    """Store many independently managed active credentials per stable Agent."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        pepper: str,
        now_fn: Callable[[], datetime] = _now,
    ) -> None:
        if len(pepper) < 32:
            raise ValueError("Agent API Key pepper must be at least 32 characters")
        self._sf = session_factory
        self._pepper = pepper
        self._now = now_fn

    @staticmethod
    def parse(api_key: str) -> tuple[str, str] | None:
        """Parse a plaintext credential into key id and secret components."""
        match = _KEY_RE.fullmatch(api_key)
        return (match.group(1), match.group(2)) if match else None

    def _hash_secret(self, secret: str, *, salt: bytes | None = None) -> str:
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.scrypt(
            (secret + self._pepper).encode("utf-8"),
            salt=salt,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=32,
            maxmem=_SCRYPT_MAXMEM,
        )
        return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64encode(salt)}${_b64encode(digest)}"

    def _verify_hash(self, secret: str, encoded: str) -> bool:
        try:
            algorithm, raw_n, raw_r, raw_p, raw_salt, raw_digest = encoded.split("$", 5)
            if algorithm != "scrypt":
                return False
            digest = hashlib.scrypt(
                (secret + self._pepper).encode("utf-8"),
                salt=_b64decode(raw_salt),
                n=int(raw_n),
                r=int(raw_r),
                p=int(raw_p),
                dklen=32,
                maxmem=_SCRYPT_MAXMEM,
            )
            return hmac.compare_digest(digest, _b64decode(raw_digest))
        except (ValueError, TypeError):
            return False

    def _new_row(
        self,
        *,
        agent_id: str,
        name: str,
        quota_overrides: Mapping[str, Any] | None,
        expires_at: datetime | None = None,
        rotation_of: str | None = None,
    ) -> tuple[AgentAPIKeyRow, str]:
        key_id = secrets.token_hex(16)
        secret = secrets.token_urlsafe(32)
        plaintext = f"dfa_{key_id}_{secret}"
        return (
            AgentAPIKeyRow(
                id=key_id,
                agent_id=agent_id,
                name=name,
                secret_hash=self._hash_secret(secret),
                key_prefix=f"dfa_{key_id[:8]}",
                last_four=secret[-4:],
                quota_overrides_json=dict(quota_overrides or {}),
                expires_at=expires_at,
                rotation_of=rotation_of,
            ),
            plaintext,
        )

    async def create(
        self,
        *,
        agent_id: str,
        owner_user_id: str,
        name: str,
        quota_overrides: Mapping[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create a named key and return its one-time plaintext value."""
        row, plaintext = self._new_row(
            agent_id=agent_id,
            name=name,
            quota_overrides=quota_overrides,
            expires_at=expires_at,
        )
        async with self._sf() as session:
            owned = await session.scalar(
                select(PublishedAgentRow.id).where(
                    PublishedAgentRow.id == agent_id,
                    PublishedAgentRow.owner_user_id == owner_user_id,
                )
            )
            if owned is None:
                raise PermissionError("Agent is not owned by caller")
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return {**_public_dict(row), "api_key": plaintext}

    async def list_by_agent(self, agent_id: str, *, owner_user_id: str) -> list[dict[str, Any]]:
        """List safe key metadata for one Agent, expiring stale rows first."""
        now = self._now()
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentAPIKeyRow)
                        .join(PublishedAgentRow, PublishedAgentRow.id == AgentAPIKeyRow.agent_id)
                        .where(
                            AgentAPIKeyRow.agent_id == agent_id,
                            PublishedAgentRow.owner_user_id == owner_user_id,
                        )
                        .order_by(AgentAPIKeyRow.created_at.desc(), AgentAPIKeyRow.id)
                    )
                )
                .scalars()
                .all()
            )
            changed = False
            for row in rows:
                if row.status == "active" and row.expires_at is not None and _as_utc(row.expires_at) <= now:
                    row.status = "expired"
                    changed = True
            if changed:
                await session.commit()
            return [_public_dict(row) for row in rows]

    async def get(self, agent_id: str, key_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        """Return safe key metadata within an Agent scope."""
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(AgentAPIKeyRow)
                    .join(PublishedAgentRow, PublishedAgentRow.id == AgentAPIKeyRow.agent_id)
                    .where(
                        AgentAPIKeyRow.id == key_id,
                        AgentAPIKeyRow.agent_id == agent_id,
                        PublishedAgentRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            return _public_dict(row) if row else None

    async def verify(self, api_key: str) -> dict[str, Any] | None:
        """Verify an active plaintext key using its slow salted hash."""
        parsed = self.parse(api_key)
        if parsed is None:
            return None
        key_id, secret = parsed
        now = self._now()
        async with self._sf() as session:
            row = await session.get(AgentAPIKeyRow, key_id)
            if row is None or row.key_prefix != f"dfa_{key_id[:8]}" or row.status != "active" or row.revoked_at is not None:
                return None
            if row.expires_at is not None and _as_utc(row.expires_at) <= now:
                row.status = "expired"
                await session.commit()
                return None
            if not self._verify_hash(secret, row.secret_hash):
                return None
            return _public_dict(row)

    async def rotate(
        self,
        agent_id: str,
        key_id: str,
        *,
        owner_user_id: str,
        overlap_seconds: int = 24 * 60 * 60,
    ) -> dict[str, Any] | None:
        """Issue a successor key and bound the predecessor overlap period."""
        now = self._now()
        async with self._sf() as session:
            old = (
                await session.execute(
                    select(AgentAPIKeyRow)
                    .join(PublishedAgentRow, PublishedAgentRow.id == AgentAPIKeyRow.agent_id)
                    .where(
                        AgentAPIKeyRow.id == key_id,
                        AgentAPIKeyRow.agent_id == agent_id,
                        PublishedAgentRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            if old is None or old.status != "active" or old.revoked_at is not None:
                return None
            if old.expires_at is not None and _as_utc(old.expires_at) <= now:
                old.status = "expired"
                await session.commit()
                return None
            overlap_end = now + timedelta(seconds=max(0, overlap_seconds))
            if old.expires_at is None or _as_utc(old.expires_at) > overlap_end:
                old.expires_at = overlap_end
            row, plaintext = self._new_row(
                agent_id=agent_id,
                name=old.name,
                quota_overrides=old.quota_overrides_json,
                rotation_of=old.id,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return {**_public_dict(row), "api_key": plaintext}

    async def revoke(self, agent_id: str, key_id: str, *, owner_user_id: str) -> bool:
        """Immediately revoke an Agent-scoped key idempotently."""
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(AgentAPIKeyRow)
                    .join(PublishedAgentRow, PublishedAgentRow.id == AgentAPIKeyRow.agent_id)
                    .where(
                        AgentAPIKeyRow.id == key_id,
                        AgentAPIKeyRow.agent_id == agent_id,
                        PublishedAgentRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            if row.status == "active":
                row.status = "revoked"
                row.revoked_at = self._now()
                await session.commit()
            return True

    async def update(
        self,
        agent_id: str,
        key_id: str,
        *,
        owner_user_id: str,
        name: str | None = None,
        quota_overrides: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update safe metadata and quota overrides within an Agent scope."""
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(AgentAPIKeyRow)
                    .join(PublishedAgentRow, PublishedAgentRow.id == AgentAPIKeyRow.agent_id)
                    .where(
                        AgentAPIKeyRow.id == key_id,
                        AgentAPIKeyRow.agent_id == agent_id,
                        PublishedAgentRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            if name is not None:
                row.name = name
            if quota_overrides is not None:
                row.quota_overrides_json = dict(quota_overrides)
            await session.commit()
            await session.refresh(row)
            return _public_dict(row)

    async def touch_last_used(self, key_id: str, *, min_interval_seconds: int = 60) -> None:
        """Throttle writes while recording recent successful authentication."""
        now = self._now()
        async with self._sf() as session:
            row = await session.get(AgentAPIKeyRow, key_id)
            if row is None:
                return
            if row.last_used_at is not None and (now - _as_utc(row.last_used_at)).total_seconds() < min_interval_seconds:
                return
            row.last_used_at = now
            await session.commit()
