"""Repository for skill_revisions — immutable, content-deduplicated upserts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
        owner = owner_user_id
        # ``owner_scope`` is the non-NULL dedup key ('public' or the owner id);
        # see the model docstring for why it exists.
        owner_scope = owner if owner is not None else "public"
        stmt = select(SkillRevisionRow).where(
            SkillRevisionRow.skill_name == skill_name,
            SkillRevisionRow.owner_scope == owner_scope,
            SkillRevisionRow.content_checksum == content_checksum,
        )
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is not None:
                return _to_dict(row)
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
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                # Concurrent publish won the insert race on the unique key
                # (skill_name, owner_scope, content_checksum): re-read the
                # now-committed row instead of surfacing a 500 (code-review
                # Important-2). This now protects public skills too, because
                # owner_scope is non-NULL.
                await session.rollback()
                row = (await session.execute(stmt)).scalar_one()
            await session.refresh(row)
            return _to_dict(row)

    async def get(self, revision_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.get(SkillRevisionRow, revision_id)
            return _to_dict(row) if row else None

    async def list_by_skill(self, skill_name: str) -> list[dict[str, Any]]:
        stmt = select(SkillRevisionRow).where(SkillRevisionRow.skill_name == skill_name).order_by(SkillRevisionRow.created_at.desc())
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [_to_dict(row) for row in rows]
