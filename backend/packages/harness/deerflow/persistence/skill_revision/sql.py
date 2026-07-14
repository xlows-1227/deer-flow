"""Repository for skill_revisions — immutable, content-deduplicated upserts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, or_, select
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
        self._after_initial_miss = None

    @staticmethod
    def _assert_content_invariants(
        row: SkillRevisionRow,
        *,
        owner_user_id: str | None,
        visibility: str,
        content_ref: str,
        declared_connector_caps: Sequence[Mapping[str, Any] | str],
    ) -> None:
        expected_caps = list(declared_connector_caps)
        mismatches: list[str] = []
        if row.owner_user_id != owner_user_id:
            mismatches.append("owner_user_id")
        if row.visibility != visibility:
            mismatches.append("visibility")
        if row.content_ref != content_ref:
            mismatches.append("content_ref")
        if list(row.declared_connector_caps_json or []) != expected_caps:
            mismatches.append("declared_connector_caps")
        if mismatches:
            raise RuntimeError(f"Skill revision metadata mismatch for identical content checksum: {', '.join(mismatches)}")

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
        row land in the same transaction. If the caller's transaction is rolled
        back, the revision insert is undone too.

        Concurrent inserts of the same ``(skill_name, owner_scope, checksum)``
        are handled WITHOUT a SAVEPOINT: the INSERT uses dialect-appropriate
        ``ON CONFLICT DO NOTHING`` (PostgreSQL) or ``INSERT OR IGNORE`` (SQLite)
        so a unique-constraint conflict never raises an IntegrityError and never
        pollutes the outer transaction (seventh-review Important-1). After the
        conflict-ignoring insert, the canonical row is re-read via SELECT.
        """
        owner = owner_user_id
        owner_scope = owner if owner is not None else "public"
        stmt = select(SkillRevisionRow).where(
            SkillRevisionRow.skill_name == skill_name,
            SkillRevisionRow.owner_scope == owner_scope,
            SkillRevisionRow.content_checksum == content_checksum,
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            self._assert_content_invariants(
                row,
                owner_user_id=owner,
                visibility=visibility,
                content_ref=content_ref,
                declared_connector_caps=declared_connector_caps,
            )
            return row
        if self._after_initial_miss is not None:
            await self._after_initial_miss()

        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        row_id = f"skr_{uuid4().hex}"
        values = {
            "id": row_id,
            "skill_name": skill_name,
            "owner_user_id": owner,
            "owner_scope": owner_scope,
            "visibility": visibility,
            "content_checksum": content_checksum,
            "content_ref": content_ref,
            "declared_connector_caps_json": list(declared_connector_caps),
        }
        dialect_name = session.bind.dialect.name if session.bind else "sqlite"
        if dialect_name == "postgresql":
            stmt_ins = pg_insert(SkillRevisionRow).values(**values)
            stmt_ins = stmt_ins.on_conflict_do_nothing(constraint="uq_skill_revisions_content")
        else:
            stmt_ins = sqlite_insert(SkillRevisionRow).values(**values)
            stmt_ins = stmt_ins.on_conflict_do_nothing(index_elements=["skill_name", "owner_scope", "content_checksum"])
        await session.execute(stmt_ins)
        await session.flush()
        # Re-read: either our row or the canonical from a concurrent insert.
        row = (await session.execute(stmt)).scalar_one()
        self._assert_content_invariants(
            row,
            owner_user_id=owner,
            visibility=visibility,
            content_ref=content_ref,
            declared_connector_caps=declared_connector_caps,
        )
        return row

    async def get(self, revision_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        """Return a public or caller-owned immutable revision, else ``None``."""
        stmt = select(SkillRevisionRow).where(
            SkillRevisionRow.id == revision_id,
            or_(
                and_(
                    SkillRevisionRow.owner_scope == "public",
                    SkillRevisionRow.owner_user_id.is_(None),
                    SkillRevisionRow.visibility == "public",
                ),
                and_(
                    SkillRevisionRow.owner_scope == owner_user_id,
                    SkillRevisionRow.owner_user_id == owner_user_id,
                ),
            ),
        )
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return _to_dict(row) if row else None

    async def list_by_skill(self, skill_name: str) -> list[dict[str, Any]]:
        stmt = select(SkillRevisionRow).where(SkillRevisionRow.skill_name == skill_name).order_by(SkillRevisionRow.created_at.desc())
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [_to_dict(row) for row in rows]
