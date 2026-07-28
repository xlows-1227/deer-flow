"""One-time migration: import legacy filesystem agents as published-agent drafts.

Usage:
    PYTHONPATH=. python scripts/migrate_published_agents.py [--dry-run] [--user-id USER_ID]

Legacy custom agents live at ``{base_dir}/users/{user_id}/agents/{name}/``
(``SOUL.md`` + ``config.yaml``). This script lists them and turns each into a
``status=draft`` published-agent + draft pair via ``AgentImportService``.
Imports are never auto-published and never delete the source files.

The script is idempotent in reporting: a second run skips agents whose slug
already exists (the import service raises ``ImportAlreadyExistsError``, which
is logged as "already imported" rather than failing the whole run).
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from deerflow.config.app_config import get_app_config
from deerflow.config.paths import get_paths

logger = logging.getLogger(__name__)


def _build_service(user_id: str):
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.published_agent import (
        AgentDraftRepository,
        PublishedAgentRepository,
    )
    from deerflow.publishing.import_service import AgentImportService
    from deerflow.publishing.skills_index import StorageSkillsIndex
    from deerflow.skills.storage import get_or_new_skill_storage

    sf = get_session_factory()
    if sf is None:
        raise RuntimeError("Database engine is not initialised; cannot import agents.")

    storage = get_or_new_skill_storage()

    # The skills index is owner-scoped; build one per import call inside the
    # service by wrapping the storage with a thin resolver.
    class _OwnerAware:
        def is_selectable_by(self, name, owner_user_id):  # noqa: ARG002
            return StorageSkillsIndex(storage, owner_user_id=user_id).is_selectable_by(name, user_id)

    return AgentImportService(
        published_agent_repo=PublishedAgentRepository(sf),
        draft_repo=AgentDraftRepository(sf),
        skills_index=_OwnerAware(),
        base_dir=get_paths().base_dir,
    )


async def _run(*, dry_run: bool, user_id: str) -> None:
    service = _build_service(user_id)
    candidates = service.list_candidates(user_id)
    logger.info("Found %d legacy agent candidate(s) for user '%s'.", len(candidates), user_id)
    for candidate in candidates:
        logger.info("  - %s (model=%s, skills=%d)", candidate.name, candidate.model_name, len(candidate.skills))

    if dry_run:
        logger.info("Dry run: no changes made.")
        return

    for candidate in candidates:
        try:
            report = await service.import_agent(user_id, candidate.name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("  skipped '%s': %s", candidate.name, exc)
            continue
        logger.info(
            "  imported '%s' -> agent_id=%s status=%s unresolved_skills=%s",
            candidate.name,
            report["agent_id"],
            report["status"],
            report["unresolved_skills"],
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Import legacy custom agents as published-agent drafts")
    parser.add_argument("--dry-run", action="store_true", help="List candidates without importing")
    parser.add_argument(
        "--user-id",
        required=True,
        metavar="USER_ID",
        help="User whose legacy agents to import.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    # Touch the config so the DB engine is wired via the normal startup path.
    get_app_config()

    asyncio.run(_run(dry_run=args.dry_run, user_id=args.user_id))


if __name__ == "__main__":
    main()
