from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from deerflow.publishing.context import PublishedAgentContext
from deerflow.publishing.resolver import (
    AgentNotAvailableError,
    AgentSuspendedError,
    PublishedAgentResolver,
)
from deerflow.publishing.runtime_policy import build_published_run_config


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


class _SkillRevisionRepo:
    def __init__(self, revisions: dict[str, dict] | None = None) -> None:
        self.revisions = revisions or {
            "sr_1": {
                "id": "sr_1",
                "skill_name": "billing-search",
                "content_ref": "cs://skills/sr_1",
                "owner_user_id": None,
                "owner_scope": "public",
                "visibility": "public",
            },
            "sr_2": {
                "id": "sr_2",
                "skill_name": "billing-export",
                "content_ref": "cs://skills/sr_2",
                "owner_user_id": None,
                "owner_scope": "public",
                "visibility": "public",
            },
        }

    async def get(self, revision_id: str, *, owner_user_id: str) -> dict | None:
        revision = self.revisions.get(revision_id)
        if revision is None:
            return None
        owner_scope = revision.get("owner_scope")
        if owner_scope not in {"public", owner_user_id}:
            return None
        return dict(revision)


class _ContentStore:
    def __init__(self, snapshots: dict[str, dict[str, bytes]] | None = None) -> None:
        self.snapshots = snapshots or {
            "cs://skills/sr_1": {
                "SKILL.md": b"---\nname: billing-search\ndescription: Search billing data\nallowed-tools:\n  - web_search\n---\n",
            },
            "cs://skills/sr_2": {
                "SKILL.md": b"---\nname: billing-export\ndescription: Export billing data\nallowed-tools:\n  - read_file\n---\n",
            },
        }

    def get(self, content_ref: str) -> dict[str, bytes]:
        if content_ref not in self.snapshots:
            raise KeyError(content_ref)
        return dict(self.snapshots[content_ref])


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


def _resolver(
    *,
    agent: dict | None = None,
    release: dict | None = None,
    connectors: dict[str, dict] | None = None,
    skill_revisions: _SkillRevisionRepo | None = None,
    content_store: _ContentStore | None = None,
) -> PublishedAgentResolver:
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
        skill_revision_repo=skill_revisions or _SkillRevisionRepo(),
        content_store=content_store or _ContentStore(),
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
    assert context.instructions.startswith("<agent_instructions>\nYou answer billing questions.\n</agent_instructions>\n\n<agent_soul>\nBe concise.\n</agent_soul>")
    assert '<published_skill name="billing-search">' in context.instructions
    assert "description: Search billing data" in context.instructions
    assert context.allowed_tool_names == ("read_file", "web_search")
    assert context.memory_enabled is False
    assert context.effective_quota == ("owner-a", {"daily_runs": 20}, "key_1")
    with pytest.raises(FrozenInstanceError):
        context.release_id = "rel_other"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_acceptance_4_skill_revision_pinning_uses_frozen_body() -> None:
    frozen = _ContentStore(
        {
            "cs://skills/sr_1": {
                "SKILL.md": b"---\nname: billing-search\ndescription: Frozen\nallowed-tools: []\n---\n\nAlways answer with the frozen workflow.\n",
            },
            "cs://skills/sr_2": {
                "SKILL.md": b"---\nname: billing-export\ndescription: Frozen export\nallowed-tools: []\n---\n\nExport only the pinned format.\n",
            },
        }
    )
    live_skill_after_publish = "Always answer with the NEW live workflow."

    context = await _resolver(content_store=frozen).resolve(
        "pa_1",
        source="api",
        credential_id="key_1",
        external_actor="actor-hash",
        conversation_scope="conv_1",
        correlation_id="corr_1",
    )

    assert "Always answer with the frozen workflow." in context.instructions
    assert live_skill_after_publish not in context.instructions
    run_config = build_published_run_config(context)
    runtime_context = run_config["configurable"]["published_agent_context"]
    assert runtime_context is context
    assert "Always answer with the frozen workflow." in runtime_context.instructions
    assert live_skill_after_publish not in runtime_context.instructions


@pytest.mark.asyncio
async def test_resolve_rejects_cross_owner_private_skill_revision() -> None:
    revisions = _SkillRevisionRepo(
        {
            "sr_1": {
                "id": "sr_1",
                "skill_name": "billing-search",
                "content_ref": "cs://skills/sr_1",
                "owner_user_id": "owner-b",
                "owner_scope": "owner-b",
                "visibility": "private",
            },
            "sr_2": {
                "id": "sr_2",
                "skill_name": "billing-export",
                "content_ref": "cs://skills/sr_2",
                "owner_user_id": None,
                "owner_scope": "public",
                "visibility": "public",
            },
        }
    )

    with pytest.raises(AgentNotAvailableError, match="missing skill revision sr_1"):
        await _resolver(skill_revisions=revisions).resolve(
            "pa_1",
            source="api",
            credential_id="key_1",
            external_actor="actor-hash",
            conversation_scope="conv_1",
            correlation_id="corr_1",
        )


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


@pytest.mark.asyncio
async def test_resolve_fails_closed_when_frozen_skill_content_is_missing() -> None:
    resolver = _resolver(content_store=_ContentStore(snapshots={"other": {}}))

    with pytest.raises(AgentNotAvailableError):
        await resolver.resolve(
            "pa_1",
            source="api",
            credential_id="key_1",
            external_actor="actor-hash",
            conversation_scope="conv_1",
            correlation_id="corr_1",
        )


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
