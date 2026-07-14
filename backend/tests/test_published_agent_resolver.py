from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from deerflow.publishing.context import PublishedAgentContext
from deerflow.publishing.resolver import (
    AgentNotAvailableError,
    AgentSuspendedError,
    PublishedAgentResolver,
)


class _AgentRepo:
    def __init__(self, agent: dict | None) -> None:
        self.agent = agent

    async def get_owner(self, agent_id: str) -> str | None:
        if self.agent is None or self.agent["id"] != agent_id:
            return None
        return self.agent["owner_user_id"]

    async def get(self, agent_id: str, *, owner_user_id: str) -> dict | None:
        if self.agent is None:
            return None
        if self.agent["id"] != agent_id or self.agent["owner_user_id"] != owner_user_id:
            return None
        return dict(self.agent)


class _ReleaseRepo:
    def __init__(self, release: dict | None) -> None:
        self.release = release

    async def get(self, release_id: str, *, owner_user_id: str) -> dict | None:  # noqa: ARG002
        if self.release is None or self.release.get("id") != release_id:
            return None
        return dict(self.release)


class _ConnectorRepo:
    def __init__(self, instances: dict[str, dict]) -> None:
        self.instances = instances

    async def get_instance(self, connector_id: str, *, owner_id: str) -> dict | None:
        instance = self.instances.get(connector_id)
        if instance is None or instance.get("owner_id") != owner_id or instance.get("status") != "active":
            return None
        return dict(instance)


class _QuotaResolver:
    async def resolve(self, *, owner_user_id: str, release: dict, credential_id: str):
        return (owner_user_id, release["quota_overrides"], credential_id)


def _agent(*, status: str = "published", release_id: str | None = "rel_1") -> dict:
    return {
        "id": "pa_1",
        "owner_user_id": "owner-a",
        "status": status,
        "current_release_id": release_id,
    }


def _release() -> dict:
    return {
        "id": "rel_1",
        "agent_id": "pa_1",
        "agent_markdown": "You answer billing questions.",
        "soul_markdown": "Be concise.",
        "model_name": "model-a",
        "tool_groups": ["search", "database"],
        "quota_overrides": {"daily_runs": 20},
        "skills": [{"skill_revision_id": "sr_2"}, {"skill_revision_id": "sr_1"}],
        "connector_grants": [
            {"connector_instance_id": "conn-live", "capability": "mail.send"},
            {"connector_instance_id": "conn-revoked", "capability": "drive.read"},
        ],
    }


def _resolver(*, agent: dict | None = None, release: dict | None = None, connectors: dict[str, dict] | None = None) -> PublishedAgentResolver:
    return PublishedAgentResolver(
        agent_repo=_AgentRepo(_agent() if agent is None else agent),
        release_repo=_ReleaseRepo(_release() if release is None else release),
        connector_repo=_ConnectorRepo(
            connectors
            if connectors is not None
            else {
                "conn-live": {
                    "id": "conn-live",
                    "owner_id": "owner-a",
                    "status": "active",
                    "supported_capabilities": ("mail.send",),
                }
            }
        ),
        quota_resolver=_QuotaResolver(),
    )


@pytest.mark.asyncio
async def test_resolve_published_agent_builds_trusted_frozen_context() -> None:
    context = await _resolver().resolve(
        "pa_1",
        source="api",
        credential_id="key_1",
        external_actor="actor-hash",
        conversation_scope="conv_1",
        correlation_id="corr_1",
        idempotency_key="idem_1",
    )

    assert context.owner_user_id == "owner-a"
    assert context.release_id == "rel_1"
    assert context.skill_revision_ids == ("sr_1", "sr_2")
    assert context.connector_capabilities == (("conn-live", "mail.send"),)
    assert context.tool_groups == ("search", "database")
    assert context.model_name == "model-a"
    assert context.instructions == (
        "<agent_instructions>\nYou answer billing questions.\n</agent_instructions>\n\n"
        "<agent_soul>\nBe concise.\n</agent_soul>"
    )
    assert context.memory_enabled is False
    assert context.effective_quota == ("owner-a", {"daily_runs": 20}, "key_1")
    with pytest.raises(FrozenInstanceError):
        context.release_id = "rel_other"  # type: ignore[misc]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent", "error"),
    [
        (_agent(status="draft"), AgentNotAvailableError),
        (_agent(release_id=None), AgentNotAvailableError),
        (_agent(status="suspended"), AgentSuspendedError),
        (_agent(status="archived"), AgentSuspendedError),
    ],
)
async def test_resolve_rejects_unavailable_agent(agent: dict, error: type[Exception]) -> None:
    with pytest.raises(error):
        await _resolver(agent=agent).resolve(
            "pa_1",
            source="api",
            credential_id="key_1",
            external_actor="actor-hash",
            conversation_scope="conv_1",
            correlation_id="corr_1",
        )


@pytest.mark.asyncio
async def test_resolve_rejects_missing_or_mismatched_release() -> None:
    missing = _resolver(release={})
    with pytest.raises(AgentNotAvailableError):
        await missing.resolve(
            "pa_1",
            source="api",
            credential_id="key_1",
            external_actor="actor-hash",
            conversation_scope="conv_1",
            correlation_id="corr_1",
        )

    wrong_release = _release()
    wrong_release["agent_id"] = "pa_other"
    with pytest.raises(AgentNotAvailableError):
        await _resolver(release=wrong_release).resolve(
            "pa_1",
            source="api",
            credential_id="key_1",
            external_actor="actor-hash",
            conversation_scope="conv_1",
            correlation_id="corr_1",
        )


@pytest.mark.asyncio
async def test_connector_revocation_takes_effect_without_mutating_release() -> None:
    release = _release()
    resolver = _resolver(
        release=release,
        connectors={
            "conn-live": {
                "id": "conn-live",
                "owner_id": "owner-a",
                "status": "active",
                "supported_capabilities": ("mail.send",),
            },
            "conn-revoked": {
                "id": "conn-revoked",
                "owner_id": "owner-a",
                "status": "revoked",
                "supported_capabilities": ("drive.read",),
            },
        },
    )

    context = await resolver.resolve(
        "pa_1",
        source="api",
        credential_id="key_1",
        external_actor="actor-hash",
        conversation_scope="conv_1",
        correlation_id="corr_1",
    )

    assert context.connector_capabilities == (("conn-live", "mail.send"),)
    assert release["connector_grants"][1] == {
        "connector_instance_id": "conn-revoked",
        "capability": "drive.read",
    }


def test_published_context_cannot_enable_memory() -> None:
    values = {
        "owner_user_id": "owner-a",
        "agent_id": "pa_1",
        "release_id": "rel_1",
        "source": "api",
        "credential_id": "key_1",
        "external_actor": "actor-hash",
        "conversation_scope": "conv_1",
        "skill_revision_ids": (),
        "connector_capabilities": (),
        "tool_groups": (),
        "model_name": "model-a",
        "instructions": "instructions",
        "effective_quota": object(),
        "correlation_id": "corr_1",
        "idempotency_key": None,
    }
    with pytest.raises(ValueError, match="memory-free"):
        PublishedAgentContext(**values, memory_enabled=True)
