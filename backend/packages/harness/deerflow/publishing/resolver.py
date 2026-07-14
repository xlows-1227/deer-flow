"""Resolve a stable published Agent identity into trusted runtime authority."""

from __future__ import annotations

from typing import Any, Protocol

from deerflow.publishing.context import PublishedAgentContext
from deerflow.publishing.instructions import compose_agent_instructions


class AgentNotAvailableError(LookupError):
    """The stable id does not currently resolve to a runnable release."""


class AgentSuspendedError(RuntimeError):
    """The Agent exists but its lifecycle state forbids new runs."""


class PublishedAgentRepoLike(Protocol):
    async def get_owner(self, agent_id: str) -> str | None: ...

    async def get(self, agent_id: str, *, owner_user_id: str) -> dict[str, Any] | None: ...


class AgentReleaseRepoLike(Protocol):
    async def get(self, release_id: str, *, owner_user_id: str) -> dict[str, Any] | None: ...


class ConnectorRepoLike(Protocol):
    async def get_instance(self, connector_id: str, *, owner_id: str) -> dict[str, Any] | None: ...


class QuotaResolverLike(Protocol):
    async def resolve(
        self,
        *,
        owner_user_id: str,
        release: dict[str, Any],
        credential_id: str,
    ) -> Any: ...


class PublishedAgentResolver:
    """Load the current immutable release and derive least-privilege context."""

    def __init__(
        self,
        *,
        agent_repo: PublishedAgentRepoLike,
        release_repo: AgentReleaseRepoLike,
        connector_repo: ConnectorRepoLike,
        quota_resolver: QuotaResolverLike,
    ) -> None:
        self._agents = agent_repo
        self._releases = release_repo
        self._connectors = connector_repo
        self._quotas = quota_resolver

    async def resolve(
        self,
        agent_id: str,
        *,
        source: str,
        credential_id: str,
        external_actor: str,
        conversation_scope: str,
        correlation_id: str,
        idempotency_key: str | None = None,
    ) -> PublishedAgentContext:
        if source not in {"api", "feishu"}:
            raise ValueError("published agent source must be api or feishu")

        owner_user_id = await self._agents.get_owner(agent_id)
        if owner_user_id is None:
            raise AgentNotAvailableError(agent_id)
        agent = await self._agents.get(agent_id, owner_user_id=owner_user_id)
        if agent is None:
            raise AgentNotAvailableError(agent_id)

        status = str(agent.get("status") or "")
        if status in {"suspended", "archived"}:
            raise AgentSuspendedError(agent_id)
        release_id = agent.get("current_release_id")
        if status != "published" or not isinstance(release_id, str) or not release_id:
            raise AgentNotAvailableError(agent_id)

        release = await self._releases.get(release_id, owner_user_id=owner_user_id)
        if release is None or release.get("agent_id") != agent_id:
            raise AgentNotAvailableError(agent_id)

        connector_capabilities = await self._active_connector_capabilities(
            owner_user_id=owner_user_id,
            release=release,
        )
        effective_quota = await self._quotas.resolve(
            owner_user_id=owner_user_id,
            release=release,
            credential_id=credential_id,
        )
        instructions = compose_agent_instructions(
            str(release.get("agent_markdown") or ""),
            str(release.get("soul_markdown") or ""),
        )
        model_name = release.get("model_name")
        if not isinstance(model_name, str) or not model_name:
            raise AgentNotAvailableError(agent_id)

        return PublishedAgentContext(
            owner_user_id=owner_user_id,
            agent_id=agent_id,
            release_id=release_id,
            source=source,  # type: ignore[arg-type]
            credential_id=credential_id,
            external_actor=external_actor,
            conversation_scope=conversation_scope,
            skill_revision_ids=tuple(
                sorted(
                    str(item["skill_revision_id"])
                    for item in release.get("skills") or []
                    if item.get("skill_revision_id")
                )
            ),
            connector_capabilities=connector_capabilities,
            tool_groups=tuple(str(group) for group in release.get("tool_groups") or []),
            model_name=model_name,
            instructions=instructions,
            effective_quota=effective_quota,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )

    async def _active_connector_capabilities(
        self,
        *,
        owner_user_id: str,
        release: dict[str, Any],
    ) -> tuple[tuple[str, str], ...]:
        active: list[tuple[str, str]] = []
        for grant in release.get("connector_grants") or []:
            connector_id = str(grant.get("connector_instance_id") or "")
            capability = str(grant.get("capability") or "")
            if not connector_id or not capability:
                continue
            instance = await self._connectors.get_instance(connector_id, owner_id=owner_user_id)
            if instance is None:
                continue
            supported = instance.get("supported_capabilities")
            if not isinstance(supported, (list, tuple, set, frozenset)) or capability not in supported:
                continue
            active.append((connector_id, capability))
        return tuple(sorted(set(active)))
