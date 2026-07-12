"""Import service that turns legacy filesystem agents into draft rows (F1.7).

Legacy custom agents live at ``{base_dir}/users/{user_id}/agents/{name}/`` as a
``SOUL.md`` + ``config.yaml`` pair. This service lists them as candidates and,
on import, creates a ``status=draft`` published-agent + draft pair through the
existing repositories, mapping:

- ``SOUL.md``  -> ``draft.soul_markdown``
- ``config.model``        -> ``draft.model_name``
- ``config.tool_groups``  -> ``draft.tool_groups``
- ``config.skills``       -> ``draft.skills`` (only names resolvable by the
  skills index; the rest are reported in ``unresolved_skills``)

There is no ``AGENT.md`` in the legacy layout, so ``agent_markdown`` is left
empty. Imports are never auto-published (``status=draft``, ``current_release_id``
is NULL) and never delete the source files — the legacy runtime keeps working
during the migration window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from deerflow.persistence.published_agent import (
    AgentDraftRepository,
    PublishedAgentRepository,
)
from deerflow.publishing.draft_service import SkillsIndex


class ImportAlreadyExistsError(Exception):
    """Raised when an agent with the same slug has already been imported."""


@dataclass
class ImportCandidate:
    """A discovered legacy agent that can be imported as a draft."""

    name: str
    display_name: str
    description: str
    soul_markdown: str
    model_name: str | None
    tool_groups: list[str]
    skills: list[str]
    source_dir: str


@dataclass
class ImportReport:
    agent_id: str
    slug: str
    status: str
    current_release_id: str | None
    unresolved_skills: list[str] = field(default_factory=list)


class AgentImportService:
    def __init__(
        self,
        *,
        published_agent_repo: PublishedAgentRepository,
        draft_repo: AgentDraftRepository,
        skills_index: SkillsIndex,
        base_dir: str | Path,
    ) -> None:
        self._agents = published_agent_repo
        self._drafts = draft_repo
        self._skills = skills_index
        self._base_dir = Path(base_dir)

    # ------------------------------------------------------------------
    # candidates
    # ------------------------------------------------------------------

    def list_candidates(self, owner_user_id: str) -> list[ImportCandidate]:
        """Return every legacy agent directory owned by ``owner_user_id``."""
        root = self._base_dir / "users" / owner_user_id / "agents"
        if not root.exists():
            return []
        candidates: list[ImportCandidate] = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            cfg_path = entry / "config.yaml"
            soul_path = entry / "SOUL.md"
            if not cfg_path.exists() and not soul_path.exists():
                continue
            cfg = self._load_config(cfg_path)
            soul = soul_path.read_text(encoding="utf-8") if soul_path.exists() else ""
            candidates.append(
                ImportCandidate(
                    name=entry.name,
                    display_name=cfg.get("name") or entry.name,
                    description=cfg.get("description") or "",
                    soul_markdown=soul,
                    model_name=cfg.get("model"),
                    tool_groups=list(cfg.get("tool_groups") or []),
                    skills=list(cfg.get("skills") or []),
                    source_dir=str(entry),
                )
            )
        return candidates

    @staticmethod
    def _load_config(cfg_path: Path) -> dict[str, Any]:
        if not cfg_path.exists():
            return {}
        try:
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # import
    # ------------------------------------------------------------------

    async def import_agent(self, owner_user_id: str, name: str) -> dict[str, Any]:
        """Import one legacy agent as a draft. Raises if already imported."""
        candidates = {c.name: c for c in self.list_candidates(owner_user_id)}
        candidate = candidates.get(name)
        if candidate is None:
            raise FileNotFoundError(f"No legacy agent named '{name}' for user {owner_user_id}")

        try:
            agent = await self._agents.create_agent(
                owner_user_id=owner_user_id,
                slug=candidate.name,
                display_name=candidate.display_name,
                description=candidate.description or None,
            )
        except ValueError as exc:
            raise ImportAlreadyExistsError(str(exc)) from exc

        draft = await self._drafts.get(agent["id"], owner_user_id=owner_user_id)
        if draft is not None:
            # Map the legacy fields onto the freshly-seeded draft.
            await self._drafts.update_with_revision(
                agent["id"],
                owner_user_id=owner_user_id,
                revision=draft["revision"],
                soul_markdown=candidate.soul_markdown,
                model_name=candidate.model_name,
                tool_groups=candidate.tool_groups,
            )

        # Resolve skill names against the index; unresolvable ones are reported.
        unresolved: list[str] = []
        selected: list[dict[str, str]] = []
        for skill_name in candidate.skills:
            if self._skills.is_selectable_by(skill_name, owner_user_id):
                # Source classification left to the caller / index; default public.
                selected.append({"skill_name": skill_name, "source": "public"})
            else:
                unresolved.append(skill_name)
        if selected:
            await self._drafts.replace_skills(agent["id"], owner_user_id=owner_user_id, skills=selected)

        return {
            "agent_id": agent["id"],
            "slug": agent["slug"],
            "status": agent["status"],
            "current_release_id": agent["current_release_id"],
            "unresolved_skills": unresolved,
        }
