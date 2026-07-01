from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.invite_code.model import InviteCodeRow


def _now() -> datetime:
    return datetime.now(UTC)


class InviteCodeRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def claim(self, code: str) -> bool:
        """Atomically mark an invite code as used. Returns True if claimed."""
        async with self._sf() as session:
            result = await session.execute(update(InviteCodeRow).where(InviteCodeRow.code == code, InviteCodeRow.used.is_(False)).values(used=True))
            if result.rowcount == 0:
                await session.rollback()
                return False
            await session.commit()
            return True

    async def complete(self, code: str, user_id: str) -> None:
        """Record which user consumed the invite code."""
        async with self._sf() as session:
            await session.execute(update(InviteCodeRow).where(InviteCodeRow.code == code).values(used_by_user_id=user_id, used_at=_now()))
            await session.commit()

    async def release(self, code: str) -> None:
        """Return a claimed invite code to the unused pool."""
        async with self._sf() as session:
            await session.execute(update(InviteCodeRow).where(InviteCodeRow.code == code, InviteCodeRow.used.is_(True)).values(used=False, used_by_user_id=None, used_at=None))
            await session.commit()

    async def get_unused_code(self) -> str | None:
        """Return the first unused invite code, or None."""
        async with self._sf() as session:
            row = (await session.execute(select(InviteCodeRow.code).where(InviteCodeRow.used.is_(False)).limit(1))).scalar_one_or_none()
            return row

    async def count_all(self) -> int:
        async with self._sf() as session:
            rows = (await session.execute(select(InviteCodeRow.code))).scalars().all()
            return len(rows)
