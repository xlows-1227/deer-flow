"""Skill share-grant catalog (persistence layer).

:mod:`deerflow.persistence.skill_share.model` defines the SQLAlchemy row
for the ``skill_shares`` table; :mod:`deerflow.persistence.skill_share.store`
wraps CRUD lookups.  The table is created by the Alembic revision
``2026_08_21_skill_shares``.

Only Gateway processes use this package directly — sandbox workers rely on
the storage-level owner isolation and the router's share-aware loader.
"""

from deerflow.persistence.skill_share.model import SkillShareRow
from deerflow.persistence.skill_share.store import SkillShareRepository

__all__ = ["SkillShareRow", "SkillShareRepository"]
