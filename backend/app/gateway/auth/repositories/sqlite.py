"""SQLAlchemy-backed UserRepository implementation.

Uses the shared async session factory from
``deerflow.persistence.engine`` — the ``users`` table lives in the
same database as ``threads_meta``, ``runs``, ``run_events``, and
``feedback``.

Constructor takes the session factory directly (same pattern as the
other four repositories in ``deerflow.persistence.*``). Callers
construct this after ``init_engine_from_config()`` has run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.auth.models import User
from app.gateway.auth.repositories.base import UserNotFoundError, UserRepository
from deerflow.persistence.user.model import UserRow


class SQLiteUserRepository(UserRepository):
    """Async user repository backed by the shared SQLAlchemy engine."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # ── Converters ────────────────────────────────────────────────────

    @staticmethod
    def _row_to_user(row: UserRow) -> User:
        return User(
            id=UUID(row.id),
            email=row.email,
            password_hash=row.password_hash,
            system_role=row.system_role,  # type: ignore[arg-type]
            # SQLite loses tzinfo on read; reattach UTC so downstream
            # code can compare timestamps reliably.
            created_at=row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC),
            oauth_provider=row.oauth_provider,
            oauth_id=row.oauth_id,
            needs_setup=row.needs_setup,
            token_version=row.token_version,
            deleted=row.deleted,
            deleted_at=(
                row.deleted_at
                if row.deleted_at is None or row.deleted_at.tzinfo
                else row.deleted_at.replace(tzinfo=UTC)
            ),
        )

    @staticmethod
    def _user_to_row(user: User) -> UserRow:
        return UserRow(
            id=str(user.id),
            email=user.email,
            password_hash=user.password_hash,
            system_role=user.system_role,
            created_at=user.created_at,
            oauth_provider=user.oauth_provider,
            oauth_id=user.oauth_id,
            needs_setup=user.needs_setup,
            token_version=user.token_version,
            deleted=user.deleted,
            deleted_at=user.deleted_at,
        )

    # ── CRUD ──────────────────────────────────────────────────────────

    async def create_user(self, user: User) -> User:
        """Insert a new user.

        Raises ``ValueError`` with a short reason on unique-constraint violations
        (duplicate email or duplicate ``oauth_provider`` + ``oauth_id`` pair).
        """
        row = self._user_to_row(user)
        async with self._sf() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                detail = str(exc.orig) if exc.orig else str(exc)
                if user.oauth_provider and user.oauth_id and "idx_users_oauth_identity" in detail:
                    raise ValueError(f"Domain account already registered: {user.oauth_id}") from exc
                raise ValueError(f"Email already registered: {user.email}") from exc
        return user

    async def get_user_by_id(self, user_id: str) -> User | None:
        async with self._sf() as session:
            row = await session.get(UserRow, user_id)
            return self._row_to_user(row) if row is not None else None

    async def get_user_by_email(self, email: str) -> User | None:
        stmt = select(UserRow).where(func.lower(UserRow.email) == email.lower())
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._row_to_user(row) if row is not None else None

    async def update_user(self, user: User) -> User:
        async with self._sf() as session:
            row = await session.get(UserRow, str(user.id))
            if row is None:
                # Hard fail on concurrent delete: callers (reset_admin,
                # password change handlers, _ensure_admin_user) all
                # fetched the user just before this call, so a missing
                # row here means the row vanished underneath us. Silent
                # success would let the caller log "password reset" for
                # a row that no longer exists.
                raise UserNotFoundError(f"User {user.id} no longer exists")
            row.email = user.email
            row.password_hash = user.password_hash
            row.system_role = user.system_role
            row.oauth_provider = user.oauth_provider
            row.oauth_id = user.oauth_id
            row.needs_setup = user.needs_setup
            row.token_version = user.token_version
            row.deleted = user.deleted
            row.deleted_at = user.deleted_at
            await session.commit()
        return user

    async def count_users(self) -> int:
        stmt = select(func.count()).select_from(UserRow).where(UserRow.deleted.is_(False))
        async with self._sf() as session:
            return await session.scalar(stmt) or 0

    async def count_admin_users(self) -> int:
        stmt = (
            select(func.count())
            .select_from(UserRow)
            .where(UserRow.system_role == "admin", UserRow.deleted.is_(False))
        )
        async with self._sf() as session:
            return await session.scalar(stmt) or 0

    async def get_user_by_oauth(self, provider: str, oauth_id: str) -> User | None:
        # AD sAMAccountName matching is case-insensitive; normalise for lookup.
        normalized = oauth_id.strip().lower()
        stmt = select(UserRow).where(
            UserRow.oauth_provider == provider,
            func.lower(UserRow.oauth_id) == normalized,
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._row_to_user(row) if row is not None else None

    async def list_users(self) -> list[User]:
        stmt = select(UserRow).where(UserRow.deleted.is_(False)).order_by(UserRow.email.asc())
        async with self._sf() as session:
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [self._row_to_user(row) for row in rows]

    async def list_users_paginated(
        self,
        *,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[User], int]:
        """Return a page of active users matching ``search`` (email ilike).

        Returns ``(users, total)`` so callers can render pagination metadata.
        ``page`` is 1-indexed; ``page_size`` is clamped to [1, 200].
        """
        page = max(1, page)
        page_size = max(1, min(200, page_size))
        conditions = [UserRow.deleted.is_(False)]
        if search:
            conditions.append(UserRow.email.ilike(f"%{search}%"))
        base = select(UserRow).where(*conditions)
        count_stmt = select(func.count()).select_from(base.subquery())
        list_stmt = base.order_by(UserRow.email.asc()).limit(page_size).offset((page - 1) * page_size)
        async with self._sf() as session:
            total = await session.scalar(count_stmt) or 0
            result = await session.execute(list_stmt)
            rows = result.scalars().all()
            return [self._row_to_user(row) for row in rows], total

    async def soft_delete_user(self, user_id: str) -> User | None:
        async with self._sf() as session:
            row = await session.get(UserRow, user_id)
            if row is None:
                return None
            row.deleted = True
            row.deleted_at = datetime.now(UTC)
            # Bump token_version so outstanding JWTs stop validating
            # immediately (same mechanism as a password change).
            row.token_version = row.token_version + 1
            await session.commit()
            await session.refresh(row)
            return self._row_to_user(row)
