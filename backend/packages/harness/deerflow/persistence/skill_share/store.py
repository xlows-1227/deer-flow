"""Async repository for skill share grants.

Supports the standard ``skill → owner → sharees`` access pattern used by
the Gateway share-management endpoints and the skill visibility filter.

Only the database-backed implementation exists today; ``memory`` mode
deployments fall back to empty share relationships and effectively keep
every custom skill private to its owner (which matches the no-auth /
single-user default behaviour).
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.skill_share.model import SkillShareRow


class SkillShareRepository:
    """SQLAlchemy-backed repository for :class:`SkillShareRow`."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # ── Lookups ────────────────────────────────────────────────────────

    async def list_sharees_for_skill(
        self,
        skill_name: str,
        owner_user_id: str | None = None,
    ) -> list[SkillShareRow]:
        """Return all sharee rows for ``skill_name``.

        If ``owner_user_id`` is provided the lookup is scoped to that
        single owner; when omitted the caller gets *every* grant row for
        the skill name regardless of owner (used by the REST layer when
        aggregating custom-skill metadata for the UI because the on-disk
        namespace already de-duplicates skill names across owners).
        """
        clauses = [SkillShareRow.skill_name == skill_name]
        if owner_user_id is not None:
            clauses.append(SkillShareRow.owner_user_id == owner_user_id)
        stmt = select(SkillShareRow).where(*clauses)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_sharees_user_ids_for_skill(
        self,
        *,
        skill_name: str,
        owner_user_id: str,
    ) -> set[str]:
        """Return the set of ``shared_with_user_id`` for an owner skill.

        Convenience wrapper used by the SkillResponse builder and the
        permission check on write endpoints.
        """
        rows = await self.list_sharees_for_skill(
            skill_name,
            owner_user_id=owner_user_id,
        )
        return {row.shared_with_user_id for row in rows}

    async def list_shares_for_shared_user(self, user_id: str) -> list[SkillShareRow]:
        """Return all grants for a *recipient* user (i.e. shared-with-me).

        Each returned row contains both the ``skill_name`` and the
        ``owner_user_id`` the visibility loader needs to re-read the
        SKILL.md from its canonical disk location.
        """
        stmt = select(SkillShareRow).where(
            SkillShareRow.shared_with_user_id == user_id,
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_skill_names_shared_with_user(self, user_id: str) -> set[str]:
        """Return every custom skill name that ``user_id`` has been granted access to.

        Thin wrapper around :meth:`list_shares_for_shared_user` kept for
        backwards compatibility with call sites that only need the names.
        """
        rows = await self.list_shares_for_shared_user(user_id)
        return {row.skill_name for row in rows}

    # ── Mutation ───────────────────────────────────────────────────────

    async def replace_sharees(
        self,
        *,
        skill_name: str,
        owner_user_id: str,
        sharee_user_ids: set[str],
    ) -> int:
        """Replace the full set of sharees for an owner-owned skill.

        *Self-share prevention is the CALLER's responsibility* — this
        method rejects ``owner_user_id`` appearing in ``sharee_user_ids``
        by raising :class:`ValueError`.  Duplicates within
        ``sharee_user_ids`` are silently de-duplicated via the ``set``
        interface and the ``uq_skill_shares_grant`` unique constraint.

        Returns the total number of grants retained after the replace
        operation (i.e. ``len(sharee_user_ids)`` after removing any
        invalid entries).  The caller should wrap a non-empty grant list
        change with an audit log once one is added.
        """
        sharee_user_ids = {uid for uid in sharee_user_ids if uid}
        if owner_user_id in sharee_user_ids:
            raise ValueError("Cannot share a custom skill with its owner (self-share forbidden)")

        async with self._sf() as session:
            # 1) Delete existing grants for this (skill, owner).
            del_stmt = delete(SkillShareRow).where(
                SkillShareRow.skill_name == skill_name,
                SkillShareRow.owner_user_id == owner_user_id,
            )
            await session.execute(del_stmt)
            # 2) Insert rows for each sharee.
            for user_id in sharee_user_ids:
                row = SkillShareRow(
                    id=str(uuid4()),
                    skill_name=skill_name,
                    owner_user_id=owner_user_id,
                    shared_with_user_id=user_id,
                )
                session.add(row)
            await session.commit()
        return len(sharee_user_ids)
