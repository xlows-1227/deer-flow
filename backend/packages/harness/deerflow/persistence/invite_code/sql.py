from __future__ import annotations

import secrets
import string
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.invite_code.model import InviteCodeRow
from deerflow.persistence.user.model import UserRow

_CODE_ALPHABET = string.ascii_uppercase + string.digits
_CODE_LENGTH = 8
_MAX_COLLISION_RETRIES = 3


def _now() -> datetime:
    return datetime.now(UTC)


def _generate_code() -> str:
    """Generate a random 8-char uppercase alphanumeric invite code."""
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


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

    async def batch_create(self, count: int) -> list[str]:
        """Generate *count* unique invite codes and persist them.

        Uses the database UNIQUE constraint on ``code`` as the collision
        guard. Each code is flushed individually in a nested savepoint so a
        collision only rolls back that one row, not the whole batch. On
        collision a new code is generated and retried up to
        ``_MAX_COLLISION_RETRIES`` times.
        """
        codes: list[str] = []
        async with self._sf() as session:
            produced = 0
            while produced < count:
                code = _generate_code()
                inserted = False
                for _retry in range(_MAX_COLLISION_RETRIES):
                    try:
                        async with session.begin_nested():
                            session.add(InviteCodeRow(code=code, used=False))
                        codes.append(code)
                        produced += 1
                        inserted = True
                        break
                    except IntegrityError:
                        code = _generate_code()
                        continue
                if not inserted:
                    raise ValueError(
                        f"Failed to generate unique invite code after "
                        f"{_MAX_COLLISION_RETRIES} retries"
                    )
            await session.commit()
        return codes

    async def list_paginated(
        self,
        page: int = 1,
        page_size: int = 20,
        sort_by: str | None = None,
        sort_order: str | None = None,
    ) -> tuple[list[dict], int]:
        """Paginated list with optional user-chosen ordering.

        Default order: unused first, then earliest created. When ``sort_by``
        is ``created_at`` or ``used_at`` and ``sort_order`` is ``asc`` or
        ``desc``, sort by that column instead; rows with NULL ``used_at``
        (unused codes) always sort last in ``used_at`` ordering. Ties break
        by created_at then code for a stable order.

        Returns (rows, total) where each row is a dict with keys:
        code, used, used_by_email, created_at, used_at.
        """
        async with self._sf() as session:
            # Total count
            total = (await session.execute(select(func.count()).select_from(InviteCodeRow))).scalar_one()

            if sort_by in ("created_at", "used_at") and sort_order in ("asc", "desc"):
                column = InviteCodeRow.created_at if sort_by == "created_at" else InviteCodeRow.used_at
                order = column.asc() if sort_order == "asc" else column.desc()
                if sort_by == "used_at":
                    # Unused codes (NULL used_at) sink to the bottom either way.
                    order = order.nulls_last()
                order_by = [order, InviteCodeRow.created_at.asc(), InviteCodeRow.code.asc()]
            else:
                order_by = [InviteCodeRow.used.asc(), InviteCodeRow.created_at.asc()]

            # Join with users to get email of the consumer
            stmt = (
                select(InviteCodeRow, UserRow.email.label("used_by_email"))
                .outerjoin(UserRow, InviteCodeRow.used_by_user_id == UserRow.id)
                .order_by(*order_by)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            result = await session.execute(stmt)
            rows = []
            for row in result.all():
                ic = row[0]  # InviteCodeRow
                email = row[1]  # used_by_email or None
                rows.append({
                    "code": ic.code,
                    "used": ic.used,
                    "used_by_email": email,
                    "created_at": ic.created_at,
                    "used_at": ic.used_at,
                })
            return rows, total
