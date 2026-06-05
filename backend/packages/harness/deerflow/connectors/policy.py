from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from deerflow.connectors.errors import ConnectorAuthorizationError
from deerflow.connectors.schemas import AuthorizationDecision, ConnectorGrant, ConnectorRuntimeContext, DatabasePolicy


def _intersect_optional(left: list[str] | None, right: list[str] | None) -> list[str] | None:
    if left is None:
        return list(right) if right is not None else None
    if right is None:
        return list(left)
    return sorted(set(left) & set(right))


def _union(left: list[str] | None, right: list[str] | None) -> list[str]:
    return sorted(set(left or []) | set(right or []))


def merge_connector_policies(*policies: dict[str, Any] | None) -> dict[str, Any]:
    """Merge generic connector policies from broad to narrow.

    This keeps the platform layer connector-type neutral. Database policies get
    an additional typed wrapper through ``merge_database_policies``.
    """
    effective: dict[str, Any] = {}
    for policy in policies:
        if not policy:
            continue
        for key, value in policy.items():
            if value is None:
                continue
            current = effective.get(key)
            if key.startswith("allowed_") and isinstance(value, list):
                effective[key] = _intersect_optional(current if isinstance(current, list) else None, value)
            elif key.startswith("blocked_") and isinstance(value, list):
                effective[key] = _union(current if isinstance(current, list) else [], value)
            elif key.startswith("max_") and isinstance(value, int):
                effective[key] = min(current, value) if isinstance(current, int) else value
            elif key.endswith("_timeout_ms") and isinstance(value, int):
                effective[key] = min(current, value) if isinstance(current, int) else value
            elif key.startswith("allow_") and isinstance(value, bool):
                effective[key] = bool(current) and value if isinstance(current, bool) else value
            elif key.endswith("_enabled") and isinstance(value, bool):
                effective[key] = bool(current) and value if isinstance(current, bool) else value
            elif key == "mode":
                # Preserve the more restrictive read-only posture if any layer
                # selected it. Future non-database connectors can use their own
                # mode values without being coerced into DatabasePolicy.
                effective[key] = "read_only" if current == "read_only" or value == "read_only" else value
            else:
                effective[key] = value
    return effective


def merge_database_policies(*policies: dict[str, Any] | DatabasePolicy | None) -> DatabasePolicy:
    raw_policies = [raw.model_dump() if isinstance(raw, DatabasePolicy) else raw for raw in policies]
    merged = merge_connector_policies(DatabasePolicy().model_dump(), *raw_policies)
    merged["mode"] = "read_only"
    merged["allow_write"] = False
    merged["allow_ddl"] = False
    return DatabasePolicy.model_validate(merged)


def _grant_matches(grant: ConnectorGrant, context: ConnectorRuntimeContext, capability: str) -> bool:
    if capability not in grant.capabilities:
        return False
    if grant.expires_at is not None and grant.expires_at <= datetime.now(UTC):
        return False
    subject_value = {
        "user": context.user_id,
        "thread": context.thread_id,
        "agent": context.agent_id,
        "skill": context.skill_name,
    }.get(grant.subject_type)
    return subject_value is not None and str(subject_value) == grant.subject_id


def authorize_connector_action(
    *,
    connector_policy: dict[str, Any],
    grants: list[ConnectorGrant],
    context: ConnectorRuntimeContext,
    capability: str,
    system_policy: dict[str, Any] | None = None,
    type_policy: dict[str, Any] | None = None,
    runtime_policy: dict[str, Any] | None = None,
    owner_id: str | None = None,
) -> AuthorizationDecision:
    if owner_id and context.user_id == owner_id:
        return AuthorizationDecision(
            allow=True,
            effective_policy=merge_connector_policies(system_policy, type_policy, connector_policy, runtime_policy),
            reason="connector owner matched",
        )

    matching_grants = [grant for grant in grants if _grant_matches(grant, context, capability)]
    if not matching_grants:
        raise ConnectorAuthorizationError(f"Current context is not granted capability {capability}", recoverable=True)

    if len(matching_grants) == 1:
        grant = matching_grants[0]
        return AuthorizationDecision(
            allow=True,
            effective_policy=merge_connector_policies(system_policy, type_policy, connector_policy, grant.policy_override, runtime_policy),
            reason=f"{grant.subject_type} grant matched",
            matched_grant_id=grant.id,
        )

    # Merge policy overrides from all matching grants so the most restrictive composite applies.
    merged_override: dict[str, Any] = {}
    for grant in matching_grants:
        merged_override = merge_connector_policies(merged_override, grant.policy_override)
    return AuthorizationDecision(
        allow=True,
        effective_policy=merge_connector_policies(system_policy, type_policy, connector_policy, merged_override, runtime_policy),
        reason=f"multiple grants matched ({len(matching_grants)})",
        matched_grant_id=matching_grants[0].id,
    )
