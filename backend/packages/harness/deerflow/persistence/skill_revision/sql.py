"""Repository for skill_revisions — immutable, content-deduplicated upserts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.skill_revision.model import SkillRevisionRow


def _now() -> datetime:
    return datetime.now(UTC)


def _to_dict(row: SkillRevisionRow) -> dict[str, Any]:
    data = row.to_dict()
    data["declared_connector_caps"] = list(data.pop("declared_connector_caps_json") or [])
    return data


class SkillRevisionRepository:
    """Content-addressed upsert for skill revisions.

    ``get_or_create`` is idempotent on ``(skill_name, owner_user_id,
    content_checksum)``: if a revision already exists for that key it is
    returned unchanged, otherwise a new immutable row is inserted. The
    repository exposes no mutation methods — revisions are write-once.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get_or_create(
        self,
        *,
        skill_name: str,
        owner_user_id: str | None,
        visibility: str,
        content_checksum: str,
        content_ref: str,
        declared_connector_caps: Sequence[Mapping[str, Any] | str],
    ) -> dict[str, Any]:
        async with self._sf() as session:
            row = await self._get_or_create_in_session(
                session,
                skill_name=skill_name,
                owner_user_id=owner_user_id,
                visibility=visibility,
                content_checksum=content_checksum,
                content_ref=content_ref,
                declared_connector_caps=declared_connector_caps,
            )
            await session.commit()
            await session.refresh(row)
            return _to_dict(row)

    async def _get_or_create_in_session(
        self,
        session: AsyncSession,
        *,
        skill_name: str,
        owner_user_id: str | None,
        visibility: str,
        content_checksum: str,
        content_ref: str,
        declared_connector_caps: Sequence[Mapping[str, Any] | str],
    ) -> SkillRevisionRow:
        """Core upsert on a shared session (does NOT commit).

        Used by ``PublishService.publish`` so skill revisions and the release
        row land in the same transaction (fourth-review Important-1). If the
        caller's transaction is rolled back, the revision insert is undone too.

        Concurrent inserts of the same ``(skill_name, owner_scope, checksum)``
        are handled via a SAVEPOINT: the insert is attempted inside a nested
        transaction, and on a unique-constraint conflict the savepoint is rolled
        back (not the outer transaction) and the canonical row is re-read
        (fifth-review Important-1).
        """
        from sqlalchemy.exc import IntegrityError as SAIntegrityError

        owner = owner_user_id
        owner_scope = owner if owner is not None else "public"
        stmt = select(SkillRevisionRow).where(
            SkillRevisionRow.skill_name == skill_name,
            SkillRevisionRow.owner_scope == owner_scope,
            SkillRevisionRow.content_checksum == content_checksum,
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            return row
        row = SkillRevisionRow(
            id=f"skr_{uuid4().hex}",
            skill_name=skill_name,
            owner_user_id=owner,
            owner_scope=owner_scope,
            visibility=visibility,
            content_checksum=content_checksum,
            content_ref=content_ref,
            declared_connector_caps_json=list(declared_connector_caps),
        )
        # Use a SAVEPOINT so a unique-constraint conflict from a concurrent
        # insert only rolls back this nested transaction, not the caller's
        # outer publish transaction. The conflict is caught INSIDE the
        # savepoint so begin_nested commits (a no-op rollback) cleanly.
        inserted = True
        async with session.begin_nested():
            try:
                session.add(row)
                await session.flush()
            except SAIntegrityError:
                inserted = False
        if not inserted:
            # Concurrent insert won the race; re-read the canonical row.
            return (await session.execute(stmt)).scalar_one()
        return row

    async def get(self, revision_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.get(SkillRevisionRow, revision_id)
            return _to_dict(row) if row else None

    async def list_by_skill(self, skill_name: str) -> list[dict[str, Any]]:
        stmt = select(SkillRevisionRow).where(SkillRevisionRow.skill_name == skill_name).order_by(SkillRevisionRow.created_at.desc())
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [_to_dict(row) for row in rows]
