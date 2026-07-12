"""Publish-time validation for an agent draft (design doc §8.2).

The validator is a pure function: given a draft dict plus collaborator indices
(skills, connectors, models, tool-group whitelist, platform quota), it returns
the full list of :class:`PublishViolation` — it aggregates rather than
failing on the first problem, so the Studio UI can render every issue at once.

The eight rules mirror the design doc one-to-one:

1. at least one instruction file non-empty
2. instruction size within limit
3. model available to the owner
4. every selected skill exists, is enabled, and is public or owner-private
5. every skill-declared connector capability is covered by the draft grants
6. every granted connector instance still belongs to the owner
7. every tool group is in the platform whitelist
8. every owner quota override is within the platform hard limit
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Per-file instruction size cap (default 200KB; overridable via config in a
# later milestone). The check is per-file, not combined, because AGENT.md and
# SOUL.md are rendered into separate prompt blocks.
MAX_INSTRUCTION_BYTES = 200 * 1024

# Platform hard limits. Owner overrides may be more restrictive but never above
# these. M2's quota engine consumes the same values; defined here so the
# validator stays self-contained.
PLATFORM_QUOTA_DEFAULTS: dict[str, int] = {
    "max_concurrent_runs": 8,
    "daily_runs": 1000,
    "daily_tokens": 2_000_000,
    "max_run_seconds": 600,
    "max_tokens_per_run": 200_000,
}


@dataclass(frozen=True)
class PublishViolation:
    """A single publish-blocker with a stable ``code`` for i18n/UI branching."""

    code: str
    message: str
    field: str | None = None


class SkillsIndexLike:
    """Structural shape the validator expects from a skills index."""

    def is_selectable_by(self, name: str, owner_user_id: str) -> bool: ...
    def get(self, name: str) -> dict[str, Any] | None: ...


def validate_draft_for_publish(
    draft: dict[str, Any],
    *,
    owner_user_id: str,
    skills_index: SkillsIndexLike,
    connector_repo: Any,
    model_index: set[str],
    tool_group_whitelist: set[str],
    platform_quota: dict[str, int],
) -> list[PublishViolation]:
    """Return every violation on ``draft``; an empty list means it is publishable.

    All collaborators are passed in explicitly so the function is pure and
    trivially testable. The connector check is the only one that needs async
    resolution; the caller pre-resolves connector ownership and passes a
    ``connector_repo`` whose ``get_instance`` is awaitable. To keep this entry
    point synchronous, connector ownership is resolved eagerly by the caller
    when wiring the validator (the publish service does that before calling).
    Here we treat ``connector_repo`` as already-validated and only check the
    *declared* capabilities against the grants; the publish service performs
    the async owner check separately.
    """
    violations: list[PublishViolation] = []

    agent_md = (draft.get("agent_markdown") or "").strip()
    soul_md = (draft.get("soul_markdown") or "").strip()

    # Rule 1
    if not agent_md and not soul_md:
        violations.append(
            PublishViolation(
                code="EMPTY_INSTRUCTIONS",
                message="At least one of AGENT.md or SOUL.md must be non-empty.",
                field="agent_markdown",
            )
        )

    # Rule 2 (per-file)
    for field, value in (("agent_markdown", draft.get("agent_markdown") or ""), ("soul_markdown", draft.get("soul_markdown") or "")):
        if value and len(value.encode("utf-8")) > MAX_INSTRUCTION_BYTES:
            violations.append(
                PublishViolation(
                    code="INSTRUCTION_TOO_LARGE",
                    message=f"{field} exceeds the {MAX_INSTRUCTION_BYTES}-byte limit.",
                    field=field,
                )
            )

    # Rule 3
    model_name = draft.get("model_name")
    if model_name and model_name not in model_index:
        violations.append(
            PublishViolation(
                code="MODEL_NOT_AVAILABLE",
                message=f"Model '{model_name}' is not available to this owner.",
                field="model_name",
            )
        )

    # Rules 4 & 5 — skills
    granted_caps = {(g["connector_instance_id"], g["capability"]) for g in draft.get("connector_grants") or []}
    for entry in draft.get("skills") or []:
        name = entry["skill_name"]
        if not skills_index.is_selectable_by(name, owner_user_id):
            violations.append(
                PublishViolation(
                    code="SKILL_NOT_FOUND",
                    message=f"Skill '{name}' is not available to this owner.",
                    field="skills",
                )
            )
            continue
        info = skills_index.get(name)
        # Each declared connector capability must be covered by a grant.
        for cap in (info or {}).get("caps", []) if isinstance(info, dict) else []:
            # A capability is satisfied if any granted (instance, capability)
            # pair matches the capability string (instance-agnostic check: the
            # publish service separately confirms the granted instances belong
            # to the owner).
            if not any(granted_cap[1] == cap for granted_cap in granted_caps):
                violations.append(
                    PublishViolation(
                        code="CONNECTOR_NOT_GRANTED",
                        message=f"Skill '{name}' requires connector capability '{cap}' which is not granted.",
                        field="connector_grants",
                    )
                )

    # Rule 6 — granted connector instances must belong to the owner and be active.
    # ``connector_repo.get_instance`` is synchronous here (the publish service
    # pre-resolves async connector lookups into a sync adapter before calling).
    for grant in draft.get("connector_grants") or []:
        instance = connector_repo.get_instance(grant["connector_instance_id"], owner_id=owner_user_id)
        if instance is None:
            violations.append(
                PublishViolation(
                    code="CONNECTOR_NOT_OWNED",
                    message=f"Connector instance '{grant['connector_instance_id']}' is not available to this owner.",
                    field="connector_grants",
                )
            )

    # Rule 7 — tool groups
    for group in draft.get("tool_groups") or []:
        if group not in tool_group_whitelist:
            violations.append(
                PublishViolation(
                    code="TOOL_GROUP_UNKNOWN",
                    message=f"Tool group '{group}' is not in the platform whitelist.",
                    field="tool_groups",
                )
            )

    # Rule 8 — quota overrides within platform hard limit
    for key, value in (draft.get("quota_overrides") or {}).items():
        if not isinstance(value, int):
            violations.append(
                PublishViolation(
                    code="QUOTA_EXCEEDS_PLATFORM",
                    message=f"Quota override '{key}' must be an integer.",
                    field="quota_overrides",
                )
            )
            continue
        hard_limit = platform_quota.get(key)
        if hard_limit is not None and value > hard_limit:
            violations.append(
                PublishViolation(
                    code="QUOTA_EXCEEDS_PLATFORM",
                    message=f"Quota override '{key}'={value} exceeds platform hard limit {hard_limit}.",
                    field="quota_overrides",
                )
            )

    return violations
