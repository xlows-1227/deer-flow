"""End-to-end acceptance gate for multi-tenant Published Agents.

Each test maps one-to-one to design §19. The suite intentionally composes the
real SQLite repositories and publishing services so the final gate verifies
cross-module invariants rather than repeating isolated unit assertions.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import FastAPI, Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.channels.base import Channel
from app.channels.feishu import FeishuChannel
from app.channels.manager import ChannelManager
from app.channels.message_bus import MessageBus, OutboundMessage
from app.channels.published_runtime import (
    GatewayPublishedRunExecutor,
    PublishedChannelRuntime,
)
from app.channels.store import ChannelStore
from app.channels.supervisor import FeishuSupervisor
from app.gateway.deps import (
    get_agent_usage_repo,
    get_external_audit_repo,
    get_external_conversation_repo,
    get_external_idempotency_repo,
    get_published_agent_repo,
    get_published_agent_resolver,
    get_quota_ledger,
)
from app.gateway.external.agent_auth import AgentAPIAuthMiddleware
from app.gateway.external.agent_serialization import (
    assert_public_payload_safe,
    sanitize_stream_payload,
    serialize_agent_metadata,
)
from app.gateway.routers import (
    agent_public_api,
    published_agent_channels,
    published_agent_keys,
    published_agents,
)
from app.gateway.routers.agent_public_api import AgentRunCreateRequest
from deerflow.config.app_config import AppConfig
from deerflow.config.connectors_config import ConnectorsConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.connectors.errors import ConnectorAuthorizationError
from deerflow.connectors.registry import ConnectorRegistry
from deerflow.connectors.schemas import (
    ConnectorRuntimeContext,
    ConnectorTypeDefinition,
)
from deerflow.connectors.secrets import SecretValue
from deerflow.connectors.service import ConnectorService
from deerflow.persistence.agent_api_key import AgentAPIKeyRepository
from deerflow.persistence.agent_channel import AgentChannelRepository
from deerflow.persistence.agent_release import AgentReleaseRepository
from deerflow.persistence.agent_usage import AgentUsageRepository
from deerflow.persistence.base import Base
from deerflow.persistence.channel_mapping import (
    SYSTEM_CHANNEL_MAPPING_SCOPE,
    ChannelEventRepository,
    ChannelMappingRepository,
)
from deerflow.persistence.connector import ConnectorRepository
from deerflow.persistence.external_audit import ExternalAuditRepository
from deerflow.persistence.external_conversation import ExternalConversationRepository
from deerflow.persistence.external_idempotency import ExternalIdempotencyRepository
from deerflow.persistence.published_agent import (
    AgentDraftRepository,
    PublishedAgentRepository,
)
from deerflow.persistence.skill_revision import SkillRevisionRepository
from deerflow.publishing.content_store import LocalContentStore
from deerflow.publishing.context import PublishedAgentContext
from deerflow.publishing.draft_service import DraftService
from deerflow.publishing.feishu_credentials import (
    FeishuCredentials,
    encode_feishu_credentials,
)
from deerflow.publishing.publish_service import PublishService
from deerflow.publishing.quota import (
    PlatformQuota,
    PublishedQuotaResolver,
    QuotaExceededError,
    QuotaLedger,
    resolve_effective_quota,
)
from deerflow.publishing.resolver import PublishedAgentResolver
from deerflow.publishing.secret_store import LocalEncryptedSecretStore
from deerflow.publishing.skills_index import SkillPublishSnapshot
from deerflow.runtime import DisconnectMode, RunRecord, RunStatus


class _MutableSkillsIndex:
    """Small authoritative public/private Skill catalog with mutable live bodies."""

    def __init__(self) -> None:
        self.entries: dict[str, dict[str, Any]] = {
            "public-search": {
                "visibility": "public",
                "owner": None,
                "description": "Search public sources",
                "caps": [],
                "allowed_tools": ["web_search"],
                "body": "Use the public search workflow from release one.",
            },
            "private-notes": {
                "visibility": "private",
                "owner": "owner-a",
                "description": "Read owner notes",
                "caps": [],
                "allowed_tools": ["read_file"],
                "body": "Use owner A's frozen private notes workflow.",
            },
            "db-report": {
                "visibility": "public",
                "owner": None,
                "description": "Query approved reporting data",
                "caps": ["database.query"],
                "allowed_tools": [],
                "body": "Query only explicitly granted reporting data.",
            },
        }

    def _selectable(self, name: str, owner_user_id: str) -> bool:
        entry = self.entries.get(name)
        return bool(entry) and (entry["visibility"] == "public" or entry["owner"] == owner_user_id)

    def is_selectable_by(self, name: str, owner_user_id: str) -> bool:
        return self._selectable(name, owner_user_id)

    def get(self, name: str) -> dict[str, Any] | None:
        entry = self.entries.get(name)
        return dict(entry) if entry is not None else None

    def list_selectable_by(self, owner_user_id: str) -> list[dict[str, Any]]:
        return [
            {
                "skill_name": name,
                "source": entry["visibility"],
                "description": entry["description"],
                "declared_connector_caps": list(entry["caps"]),
            }
            for name, entry in sorted(self.entries.items())
            if self._selectable(name, owner_user_id)
        ]

    def resolve_publish_snapshots(
        self,
        skill_names: list[str] | None,
        owner_user_id: str,
    ) -> dict[str, SkillPublishSnapshot | None]:
        names = [name for name in sorted(self.entries) if self._selectable(name, owner_user_id)] if skill_names is None else list(dict.fromkeys(skill_names))
        result: dict[str, SkillPublishSnapshot | None] = {}
        for name in names:
            if not self._selectable(name, owner_user_id):
                result[name] = None
                continue
            entry = self.entries[name]
            allowed_tools = entry["allowed_tools"]
            allowed_yaml = "allowed-tools: []" if not allowed_tools else "allowed-tools:\n" + "\n".join(f"  - {tool}" for tool in allowed_tools)
            skill_markdown = (f"---\nname: {name}\ndescription: {entry['description']}\n{allowed_yaml}\n---\n\n{entry['body']}\n").encode()
            visibility = str(entry["visibility"])
            result[name] = SkillPublishSnapshot(
                skill_name=name,
                source=visibility,
                visibility=visibility,
                owner_user_id=str(entry["owner"]) if visibility == "private" else None,
                declared_connector_caps=tuple(entry["caps"]),
                files=(("SKILL.md", skill_markdown),),
            )
        return result


class _ConnectorIndex:
    """Authority-enriched Connector catalog; secrets never leave this boundary."""

    def __init__(self) -> None:
        self.instances: dict[str, dict[str, Any]] = {
            "conn-a": {
                "id": "conn-a",
                "owner_id": "owner-a",
                "status": "active",
                "supported_capabilities": ("database.query", "mail.send"),
                "secret_ref": "secret://connector/owner-a-private",
            },
            "conn-b": {
                "id": "conn-b",
                "owner_id": "owner-b",
                "status": "active",
                "supported_capabilities": ("database.query",),
                "secret_ref": "secret://connector/owner-b-private",
            },
        }

    async def get_instance(self, connector_id: str, *, owner_id: Any = ...) -> dict[str, Any] | None:
        instance = self.instances.get(connector_id)
        if instance is None or (owner_id is not ... and instance["owner_id"] != owner_id):
            return None
        return dict(instance)


class _AcceptanceConnectorSecretStore:
    secret = "token=acceptance-secret-value"

    def get_secret(self, credential, context=None):  # noqa: ARG002
        return SecretValue(self.secret)


class _AcceptanceConnectorAdapter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(
        self,
        instance,
        capability,
        args,
        policy,
        context,
        *,
        secrets=None,
    ):
        assert secrets == {"value": _AcceptanceConnectorSecretStore.secret}
        self.calls.append(
            {
                "connector_id": instance.id,
                "capability": capability,
                "args": dict(args),
                "owner": context.user_id,
                "policy": dict(policy),
            }
        )
        return {"message_id": "safe-message-1", "accepted": True}


class _AcceptanceFeishuChannel(Channel):
    """Controllable transport used to exercise the real supervisor boundary."""

    def __init__(
        self,
        bus: MessageBus,
        *,
        app_id: str,
        app_secret: str,
        verification_token: str,
        encrypt_key: str,
        binding_id: str,
        agent_id: str,
        runtime_error_callback: Any,
        runtime_health_callback: Any | None = None,
    ) -> None:
        super().__init__(name=f"feishu:{binding_id}", bus=bus, config={})
        self.app_id = app_id
        self.app_secret = app_secret
        self.verification_token = verification_token
        self.encrypt_key = encrypt_key
        self.binding_id = binding_id
        self.agent_id = agent_id
        self.runtime_error_callback = runtime_error_callback
        self.runtime_health_callback = runtime_health_callback

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        return None


class _AcceptanceFeishuFactory:
    def __init__(self) -> None:
        self.instances: dict[str, _AcceptanceFeishuChannel] = {}

    def __call__(self, bus: MessageBus, **kwargs: Any) -> _AcceptanceFeishuChannel:
        channel = _AcceptanceFeishuChannel(bus, **kwargs)
        self.instances[channel.binding_id] = channel
        return channel


@dataclass
class _AcceptanceEnvironment:
    session_factory: async_sessionmaker[AsyncSession]
    agents: PublishedAgentRepository
    drafts: AgentDraftRepository
    releases: AgentReleaseRepository
    skill_revisions: SkillRevisionRepository
    keys: AgentAPIKeyRepository
    channels: AgentChannelRepository
    mappings: ChannelMappingRepository
    events: ChannelEventRepository
    usage: AgentUsageRepository
    audit: ExternalAuditRepository
    conversations: ExternalConversationRepository
    idempotency: ExternalIdempotencyRepository
    draft_service: DraftService
    publish_service: PublishService
    resolver: PublishedAgentResolver
    quota_ledger: QuotaLedger
    skills: _MutableSkillsIndex
    connectors: _ConnectorIndex
    connector_repository: ConnectorRepository
    connector_service: ConnectorService
    connector_adapter: _AcceptanceConnectorAdapter


@pytest_asyncio.fixture
async def acceptance_env(tmp_path) -> _AcceptanceEnvironment:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'acceptance.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    agents = PublishedAgentRepository(session_factory)
    drafts = AgentDraftRepository(session_factory)
    releases = AgentReleaseRepository(session_factory)
    skill_revisions = SkillRevisionRepository(session_factory)
    keys = AgentAPIKeyRepository(session_factory, pepper="acceptance-pepper-" * 4)
    channels = AgentChannelRepository(session_factory)
    mappings = ChannelMappingRepository(session_factory)
    events = ChannelEventRepository(session_factory)
    usage = AgentUsageRepository(session_factory)
    audit = ExternalAuditRepository(session_factory)
    conversations = ExternalConversationRepository(session_factory)
    idempotency = ExternalIdempotencyRepository(session_factory)
    connector_repository = ConnectorRepository(session_factory)
    skills = _MutableSkillsIndex()
    connectors = _ConnectorIndex()
    connector_registry = ConnectorRegistry()
    connector_registry.register(
        ConnectorTypeDefinition(
            type="acceptance-messaging",
            category="messaging",
            display_name="Acceptance messaging",
            adapter="tests.test_acceptance_multi_tenant:_AcceptanceConnectorAdapter",
            capabilities=["mail.send"],
        )
    )
    connector_adapter = _AcceptanceConnectorAdapter()
    connector_service = ConnectorService(
        connector_repository,
        registry=connector_registry,
        secret_store=_AcceptanceConnectorSecretStore(),
        app_config=AppConfig(
            sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
            connectors=ConnectorsConfig(
                enabled=True,
                enabled_types=["acceptance-messaging"],
            ),
        ),
    )
    connector_service._adapters["acceptance-messaging"] = connector_adapter
    content_store = LocalContentStore(base_dir=tmp_path)
    draft_service = DraftService(
        published_agent_repo=agents,
        draft_repo=drafts,
        skills_index=skills,
        connector_repo=connectors,
    )
    publish_service = PublishService(
        published_agent_repo=agents,
        draft_repo=drafts,
        release_repo=releases,
        skill_revision_repo=skill_revisions,
        content_store=content_store,
        skills_index=skills,
        connector_repo=connectors,
        model_index={"model-a"},
        tool_group_whitelist={"web"},
    )
    resolver = PublishedAgentResolver(
        agent_repo=agents,
        release_repo=releases,
        connector_repo=connectors,
        quota_resolver=PublishedQuotaResolver(PlatformQuota(), keys),
        skill_revision_repo=skill_revisions,
        content_store=content_store,
    )
    environment = _AcceptanceEnvironment(
        session_factory=session_factory,
        agents=agents,
        drafts=drafts,
        releases=releases,
        skill_revisions=skill_revisions,
        keys=keys,
        channels=channels,
        mappings=mappings,
        events=events,
        usage=usage,
        audit=audit,
        conversations=conversations,
        idempotency=idempotency,
        draft_service=draft_service,
        publish_service=publish_service,
        resolver=resolver,
        quota_ledger=QuotaLedger(usage),
        skills=skills,
        connectors=connectors,
        connector_repository=connector_repository,
        connector_service=connector_service,
        connector_adapter=connector_adapter,
    )
    try:
        yield environment
    finally:
        await engine.dispose()


async def _create_ready_agent(
    environment: _AcceptanceEnvironment,
    *,
    owner: str,
    slug: str,
    agent_markdown: str = "# Agent",
    soul_markdown: str = "# Soul",
    skills: tuple[str, ...] = (),
    grants: tuple[tuple[str, str], ...] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    agent = await environment.draft_service.create_agent(
        owner_user_id=owner,
        slug=slug,
        display_name=slug.replace("-", " ").title(),
    )
    await environment.draft_service.update_draft(
        agent["id"],
        owner_user_id=owner,
        revision=1,
        agent_markdown=agent_markdown,
        soul_markdown=soul_markdown,
        model_name="model-a",
        tool_groups=["web"],
    )
    await environment.draft_service.set_skills(
        agent["id"],
        owner_user_id=owner,
        skills=[{"skill_name": name, "source": "client-value-is-ignored"} for name in skills],
    )
    await environment.draft_service.set_connector_grants(
        agent["id"],
        owner_user_id=owner,
        grants=[
            {
                "connector_instance_id": connector_id,
                "capability": capability,
            }
            for connector_id, capability in grants
        ],
    )
    published = await environment.publish_service.publish(agent["id"], owner_user_id=owner)
    return agent, published


async def _resolve(
    environment: _AcceptanceEnvironment,
    agent_id: str,
    *,
    source: str = "api",
    credential_id: str = "key-missing",
    conversation_scope: str = "conversation-1",
) -> PublishedAgentContext:
    return await environment.resolver.resolve(
        agent_id,
        source=source,  # type: ignore[arg-type]
        credential_id=credential_id,
        external_actor="hashed-actor",
        conversation_scope=conversation_scope,
        correlation_id=f"correlation-{agent_id}",
    )


def _usage_values(
    *,
    owner: str,
    agent_id: str,
    run_id: str,
    credential_id: str = "key-1",
    source: str = "api",
) -> dict[str, Any]:
    return {
        "owner_user_id": owner,
        "agent_id": agent_id,
        "source": source,
        "credential_id": credential_id,
        "external_actor_hash": "actor-hash",
        "conversation_id": "conversation-1",
        "run_id": run_id,
        "model": "model-a",
        "input_tokens": 5,
        "output_tokens": 7,
        "total_tokens": 12,
        "latency_ms": 20,
        "status": "success",
        "error_class": None,
        "idempotency_key": None,
        "correlation_id": f"correlation-{run_id}",
    }


def _quota_context(
    *,
    agent_id: str,
    credential_id: str,
    correlation_id: str,
    effective_quota: Any,
) -> PublishedAgentContext:
    return PublishedAgentContext(
        owner_user_id="owner-a",
        agent_id=agent_id,
        release_id="release-quota",
        source="api",
        credential_id=credential_id,
        external_actor="actor-hash",
        conversation_scope="conversation-quota",
        skill_revision_ids=(),
        connector_capabilities=(),
        tool_groups=(),
        model_name="model-a",
        instructions="Quota acceptance",
        effective_quota=effective_quota,
        correlation_id=correlation_id,
        idempotency_key=None,
    )


@pytest.mark.asyncio
async def test_acceptance_1_tenant_isolation(acceptance_env: _AcceptanceEnvironment) -> None:
    env = acceptance_env
    a1, a1_release = await _create_ready_agent(env, owner="owner-a", slug="a-one", skills=("private-notes",))
    await _create_ready_agent(env, owner="owner-a", slug="a-two")
    await _create_ready_agent(env, owner="owner-b", slug="b-one")
    await _create_ready_agent(env, owner="owner-b", slug="b-two")
    key = await env.keys.create(agent_id=a1["id"], owner_user_id="owner-a", name="A production")
    channel = await env.channels.create(
        agent_id=a1["id"],
        owner_user_id="owner-a",
        app_id="cli_owner_a",
        secret_ref="secret://feishu/owner-a",
    )
    await env.usage.record_usage(
        _usage_values(owner="owner-a", agent_id=a1["id"], run_id="run-owner-a"),
        owner_user_id="owner-a",
    )
    await env.audit.append(
        {
            "request_id": "request-owner-a",
            "user_id": None,
            "api_key_id": None,
            "owner_user_id": "owner-a",
            "agent_id": a1["id"],
            "credential_id": key["id"],
            "external_actor_hash": "actor-hash",
            "source": "api",
            "action": "run.create",
            "resource_type": "run",
            "resource_id": "run-owner-a",
            "skill_name": None,
            "method": "POST",
            "path_template": "/api/v1/agents/{agent_id}/conversations/{conversation_id}/runs",
            "status_code": 200,
            "client_ip_hash": "ip-hash",
            "user_agent": "redacted-at-owner-boundary",
            "duration_ms": 4,
        }
    )

    app = FastAPI()

    @app.middleware("http")
    async def owner_b_session(request: Request, call_next):
        request.state.user = SimpleNamespace(id="owner-b")
        request.state.auth_method = "session"
        return await call_next(request)

    app.state.draft_service = env.draft_service
    app.state.publish_service = env.publish_service
    app.state.agent_api_key_repo = env.keys
    app.state.agent_channel_repo = env.channels
    app.dependency_overrides[get_agent_usage_repo] = lambda: env.usage
    app.dependency_overrides[get_external_audit_repo] = lambda: env.audit
    app.include_router(published_agents.router)
    app.include_router(published_agent_keys.router)
    app.include_router(published_agent_channels.router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        listing = await client.get("/api/published-agents")
        hidden_detail = await client.get(f"/api/published-agents/{a1['id']}")
        hidden_write = await client.patch(
            f"/api/published-agents/{a1['id']}/draft",
            json={"revision": 3, "agent_markdown": "cross-tenant overwrite"},
        )
        hidden_lifecycle = await client.post(f"/api/published-agents/{a1['id']}/archive")
        hidden_releases = await client.get(f"/api/published-agents/{a1['id']}/releases")
        hidden_keys = await client.get(f"/api/published-agents/{a1['id']}/keys")
        hidden_channels = await client.get(f"/api/published-agents/{a1['id']}/channels")
        hidden_usage = await client.get(f"/api/published-agents/{a1['id']}/usage")
        hidden_audit = await client.get(f"/api/published-agents/{a1['id']}/audit")

    assert listing.status_code == 200
    assert {item["slug"] for item in listing.json()} == {"b-one", "b-two"}
    for response in (
        hidden_detail,
        hidden_write,
        hidden_lifecycle,
        hidden_releases,
        hidden_keys,
        hidden_channels,
        hidden_usage,
        hidden_audit,
    ):
        assert response.status_code == 404
    assert (await env.drafts.get(a1["id"], owner_user_id="owner-a"))["agent_markdown"] != "cross-tenant overwrite"

    release = await env.releases.get(a1_release["release_id"], owner_user_id="owner-a")
    private_revision_id = release["skills"][0]["skill_revision_id"]
    assert await env.skill_revisions.get(private_revision_id, owner_user_id="owner-b") is None
    assert channel is not None


@pytest.mark.asyncio
async def test_acceptance_2_instruction_combinations(acceptance_env: _AcceptanceEnvironment) -> None:
    env = acceptance_env
    combinations = (
        ("only-agent", "# Agent only", ""),
        ("only-soul", "", "# Soul only"),
        ("both-files", "# Agent", "# Soul"),
    )
    for slug, agent_markdown, soul_markdown in combinations:
        _agent, published = await _create_ready_agent(
            env,
            owner="owner-a",
            slug=slug,
            agent_markdown=agent_markdown,
            soul_markdown=soul_markdown,
        )
        release = await env.releases.get(published["release_id"], owner_user_id="owner-a")
        assert release["agent_markdown"] == agent_markdown
        assert release["soul_markdown"] == soul_markdown


@pytest.mark.asyncio
async def test_acceptance_3_draft_isolation(acceptance_env: _AcceptanceEnvironment) -> None:
    env = acceptance_env
    agent, published = await _create_ready_agent(
        env,
        owner="owner-a",
        slug="draft-isolation",
        agent_markdown="Release-one behavior",
        soul_markdown="Release-one soul",
    )
    before = await _resolve(env, agent["id"])
    draft = await env.drafts.get(agent["id"], owner_user_id="owner-a")
    await env.draft_service.update_draft(
        agent["id"],
        owner_user_id="owner-a",
        revision=draft["revision"],
        agent_markdown="Unsaved-to-production draft behavior",
        soul_markdown="Draft-only soul",
    )
    after = await _resolve(env, agent["id"])

    assert before.release_id == published["release_id"] == after.release_id
    assert before.instructions == after.instructions
    assert "Release-one behavior" in after.instructions
    assert "Unsaved-to-production" not in after.instructions


@pytest.mark.asyncio
async def test_acceptance_4_skill_revision_pinning(acceptance_env: _AcceptanceEnvironment) -> None:
    env = acceptance_env
    agent, published = await _create_ready_agent(
        env,
        owner="owner-a",
        slug="skill-pinning",
        skills=("public-search", "private-notes"),
    )
    release = await env.releases.get(published["release_id"], owner_user_id="owner-a")
    pinned_ids = {item["skill_revision_id"] for item in release["skills"]}
    env.skills.entries["public-search"]["body"] = "NEW live public search workflow."
    env.skills.entries["private-notes"]["body"] = "NEW live private notes workflow."

    context = await _resolve(env, agent["id"])

    assert set(context.skill_revision_ids) == pinned_ids
    assert "public search workflow from release one" in context.instructions
    assert "frozen private notes workflow" in context.instructions
    assert "NEW live" not in context.instructions


@pytest.mark.asyncio
async def test_acceptance_5_connector_grants(acceptance_env: _AcceptanceEnvironment) -> None:
    env = acceptance_env
    await env.connector_service.create_connector(
        {
            "id": "conn-a",
            "name": "acceptance-mail",
            "type": "acceptance-messaging",
            "config": {},
            "credential": {
                "provider": "env",
                "ref": "ACCEPTANCE_CONNECTOR_SECRET",
            },
        },
        owner_id="owner-a",
    )
    agent, _published = await _create_ready_agent(
        env,
        owner="owner-a",
        slug="connector-grants",
        grants=(("conn-a", "mail.send"),),
    )
    context = await _resolve(env, agent["id"])
    runtime_context = ConnectorRuntimeContext(
        user_id=context.owner_user_id,
        agent_id=context.agent_id,
        run_id="run-connector-acceptance",
        connector_capabilities={connector_id: [capability] for connector_id, capability in context.connector_capabilities},
    )

    assert context.connector_capabilities == (("conn-a", "mail.send"),)
    result = await env.connector_service.execute_connector_action(
        "conn-a",
        capability="mail.send",
        args={"recipient": "safe@example.com", "subject": "Acceptance"},
        reason="M4 acceptance",
        context=runtime_context,
    )
    assert result == {"message_id": "safe-message-1", "accepted": True}
    assert env.connector_adapter.calls == [
        {
            "connector_id": "conn-a",
            "capability": "mail.send",
            "args": {
                "recipient": "safe@example.com",
                "subject": "Acceptance",
            },
            "owner": "owner-a",
            "policy": {},
        }
    ]

    with pytest.raises(ConnectorAuthorizationError) as denied:
        await env.connector_service.execute_connector_action(
            "conn-a",
            capability="mail.send",
            args={"recipient": "attacker@example.com"},
            reason="denied replay",
            context=runtime_context.model_copy(update={"connector_capabilities": {"conn-a": []}}),
        )
    audits = await env.connector_repository.list_audit(connector_id="conn-a")
    assert {row["decision"] for row in audits} == {"allow", "deny"}
    serialized = repr(
        {
            "result": result,
            "error": str(denied.value),
            "audits": audits,
            "context": context,
        }
    )
    assert _AcceptanceConnectorSecretStore.secret not in serialized
    assert "ACCEPTANCE_CONNECTOR_SECRET" not in serialized


@pytest.mark.asyncio
async def test_acceptance_8_mapping_and_memoryless(acceptance_env: _AcceptanceEnvironment) -> None:
    env = acceptance_env
    agent, _published = await _create_ready_agent(env, owner="owner-a", slug="feishu-memoryless")
    binding = await env.channels.create(
        agent_id=agent["id"],
        owner_user_id="owner-a",
        app_id="cli_mapping",
        secret_ref="secret://feishu/mapping",
    )
    assert binding is not None

    private_a = await env.mappings.get_or_create_thread(
        binding_id=binding["id"],
        agent_id=agent["id"],
        chat_id="private-chat",
        feishu_user_id="user-a",
        chat_type="p2p",
        system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE,
    )
    private_b = await env.mappings.get_or_create_thread(
        binding_id=binding["id"],
        agent_id=agent["id"],
        chat_id="private-chat",
        feishu_user_id="user-b",
        chat_type="p2p",
        system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE,
    )
    group_a = await env.mappings.get_or_create_thread(
        binding_id=binding["id"],
        agent_id=agent["id"],
        chat_id="group-chat",
        feishu_user_id="user-a",
        chat_type="group",
        system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE,
    )
    group_b = await env.mappings.get_or_create_thread(
        binding_id=binding["id"],
        agent_id=agent["id"],
        chat_id="group-chat",
        feishu_user_id="user-b",
        chat_type="group",
        system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE,
    )
    context = await _resolve(
        env,
        agent["id"],
        source="feishu",
        credential_id=binding["id"],
        conversation_scope=private_a,
    )

    assert private_a != private_b
    assert group_a == group_b
    assert context.memory_enabled is False


@pytest.mark.asyncio
async def test_acceptance_9_stable_identity(acceptance_env: _AcceptanceEnvironment) -> None:
    env = acceptance_env
    agent, first_release = await _create_ready_agent(env, owner="owner-a", slug="stable-identity")
    key = await env.keys.create(agent_id=agent["id"], owner_user_id="owner-a", name="Stable key")
    binding = await env.channels.create(
        agent_id=agent["id"],
        owner_user_id="owner-a",
        app_id="cli_stable",
        secret_ref="secret://feishu/stable",
    )
    assert binding is not None
    conversation = await env.conversations.create(
        {
            "conversation_id": "conversation-stable",
            "user_id": "owner-a",
            "credential_id": key["id"],
            "source": f"agent-api:{key['id']}",
            "external_conversation_id": "customer-stable",
            "thread_id": "thread-stable",
            "agent_id": agent["id"],
        }
    )
    mapped_thread = await env.mappings.get_or_create_thread(
        binding_id=binding["id"],
        agent_id=agent["id"],
        chat_id="chat-stable",
        feishu_user_id="user-stable",
        chat_type="p2p",
        system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE,
    )
    api_path = f"/api/v1/agents/{agent['id']}"

    draft = await env.drafts.get(agent["id"], owner_user_id="owner-a")
    await env.draft_service.update_draft(
        agent["id"],
        owner_user_id="owner-a",
        revision=draft["revision"],
        agent_markdown="Release two",
    )
    second_release = await env.publish_service.publish(agent["id"], owner_user_id="owner-a")
    await env.publish_service.rollback(agent["id"], owner_user_id="owner-a", release_no=1)

    refreshed = await env.agents.get(agent["id"], owner_user_id="owner-a")
    remapped_thread = await env.mappings.get_or_create_thread(
        binding_id=binding["id"],
        agent_id=agent["id"],
        chat_id="chat-stable",
        feishu_user_id="user-stable",
        chat_type="p2p",
        system_scope=SYSTEM_CHANNEL_MAPPING_SCOPE,
    )
    assert second_release["release_id"] != first_release["release_id"]
    assert refreshed["id"] == agent["id"]
    assert refreshed["current_release_id"] == first_release["release_id"]
    assert f"/api/v1/agents/{refreshed['id']}" == api_path
    assert (await env.keys.verify(key["api_key"]))["id"] == key["id"]
    assert (await env.channels.get(agent["id"], binding["id"], owner_user_id="owner-a"))["id"] == binding["id"]
    assert (
        await env.conversations.get_for_agent(
            conversation["conversation_id"],
            owner_user_id="owner-a",
            agent_id=agent["id"],
            credential_id=key["id"],
        )
    )["thread_id"] == "thread-stable"
    assert remapped_thread == mapped_thread


def test_acceptance_10_no_release_leak() -> None:
    with pytest.raises(ValidationError):
        AgentRunCreateRequest.model_validate(
            {
                "message": "hello",
                "release_id": "rel_attacker",
            }
        )

    public = serialize_agent_metadata(
        {
            "id": "pa-public",
            "display_name": "Public agent",
            "description": "Safe metadata",
            "avatar_ref": None,
            "current_release_id": "rel-internal",
            "owner_user_id": "owner-internal",
        }
    )
    stream = sanitize_stream_payload(
        {
            "type": "message",
            "content": "safe",
            "release_id": "rel-internal",
            "configurable": {"release_id": "rel-internal"},
        }
    )
    assert public == {
        "agent_id": "pa-public",
        "display_name": "Public agent",
        "description": "Safe metadata",
        "avatar": None,
    }
    assert stream == {"type": "message", "content": "safe"}
    assert_public_payload_safe(public)
    assert_public_payload_safe(stream)


@pytest.mark.asyncio
async def test_acceptance_11_multi_keys(acceptance_env: _AcceptanceEnvironment) -> None:
    env = acceptance_env
    agent, _published = await _create_ready_agent(env, owner="owner-a", slug="multi-keys")
    first = await env.keys.create(
        agent_id=agent["id"],
        owner_user_id="owner-a",
        name="Partner A",
        quota_overrides={"daily_runs": 10},
    )
    second = await env.keys.create(
        agent_id=agent["id"],
        owner_user_id="owner-a",
        name="Partner B",
        quota_overrides={"daily_runs": 2},
    )
    rotated = await env.keys.rotate(
        agent["id"],
        first["id"],
        owner_user_id="owner-a",
        overlap_seconds=0,
    )
    assert rotated is not None
    assert rotated["rotation_of"] == first["id"]
    assert await env.keys.verify(first["api_key"]) is None
    assert (await env.keys.verify(rotated["api_key"]))["id"] == rotated["id"]
    assert (await env.keys.verify(second["api_key"]))["quota_overrides"] == {"daily_runs": 2}
    assert await env.keys.revoke(agent["id"], second["id"], owner_user_id="owner-a")
    assert await env.keys.verify(second["api_key"]) is None


@pytest.mark.asyncio
async def test_acceptance_12_quota_prerun_rejection(acceptance_env: _AcceptanceEnvironment) -> None:
    env = acceptance_env
    platform = PlatformQuota(
        max_concurrent_runs_per_agent=1,
        daily_runs_default=3,
    )
    platform_limited = resolve_effective_quota(
        platform,
        {"max_concurrent_runs": 99},
        {},
    )
    first = await env.quota_ledger.reserve(
        _quota_context(
            agent_id="quota-platform",
            credential_id="key-platform",
            correlation_id="correlation-platform-1",
            effective_quota=platform_limited,
        ),
        request_key="quota-platform-1",
    )
    with pytest.raises(QuotaExceededError, match="max_concurrent_runs_exceeded"):
        await env.quota_ledger.reserve(
            _quota_context(
                agent_id="quota-platform",
                credential_id="key-platform",
                correlation_id="correlation-platform-2",
                effective_quota=platform_limited,
            ),
            request_key="quota-platform-2",
        )
    assert len(await env.usage.list_reservations(owner_user_id="owner-a", agent_id="quota-platform")) == 1
    assert await env.quota_ledger.release(first.id, owner_user_id="owner-a")

    owner_limited = resolve_effective_quota(platform, {"daily_runs": 1}, {})
    reservation = await env.quota_ledger.reserve(
        _quota_context(
            agent_id="quota-owner",
            credential_id="key-owner",
            correlation_id="correlation-owner-1",
            effective_quota=owner_limited,
        ),
        request_key="quota-owner-1",
    )
    usage = _usage_values(
        owner="owner-a",
        agent_id="quota-owner",
        run_id="run-quota-owner",
        credential_id="key-owner",
    )
    assert await env.quota_ledger.settle(
        reservation.id,
        owner_user_id="owner-a",
        tokens_used=12,
        status="success",
        run_id="run-quota-owner",
        usage=usage,
    )
    with pytest.raises(QuotaExceededError, match="daily_runs_exceeded"):
        await env.quota_ledger.reserve(
            _quota_context(
                agent_id="quota-owner",
                credential_id="key-owner",
                correlation_id="correlation-owner-2",
                effective_quota=owner_limited,
            ),
            request_key="quota-owner-2",
        )
    _row, duplicate_created = await env.usage.record_usage(usage, owner_user_id="owner-a")
    assert duplicate_created is False
    aggregate = await env.usage.aggregate_daily(
        owner_user_id="owner-a",
        agent_id="quota-owner",
        since=datetime.now(UTC) - timedelta(days=1),
    )
    assert aggregate["totals"]["runs"] == 1
    assert aggregate["totals"]["total_tokens"] == 12


@pytest.mark.asyncio
async def test_acceptance_13_idempotency(
    acceptance_env: _AcceptanceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env = acceptance_env
    agent, _published = await _create_ready_agent(env, owner="owner-a", slug="idempotency")
    binding = await env.channels.create(
        agent_id=agent["id"],
        owner_user_id="owner-a",
        app_id="cli_idempotency",
        secret_ref="secret://feishu/idempotency",
    )
    assert binding is not None
    assert await env.channels.activate(
        agent["id"],
        binding["id"],
        owner_user_id="owner-a",
    )

    key = await env.keys.create(
        agent_id=agent["id"],
        owner_user_id="owner-a",
        name="Idempotency acceptance",
    )
    conversation_id = "conversation-idempotency"
    thread_id = "thread-idempotency"
    await env.conversations.create(
        {
            "conversation_id": conversation_id,
            "user_id": "owner-a",
            "credential_id": key["id"],
            "source": f"agent-api:{key['id']}",
            "external_conversation_id": "external-idempotency",
            "thread_id": thread_id,
            "agent_id": agent["id"],
        }
    )

    records: dict[str, RunRecord] = {}
    starts = 0

    async def start_once(
        body,
        requested_thread_id,
        request,
        *,
        published_context=None,
        run_id=None,
        **_kwargs,
    ) -> RunRecord:
        nonlocal starts
        starts += 1
        assert requested_thread_id == thread_id
        assert published_context.agent_id == agent["id"]
        record = RunRecord(
            run_id=str(run_id),
            thread_id=requested_thread_id,
            assistant_id="lead_agent",
            status=RunStatus.running,
            on_disconnect=DisconnectMode.continue_,
            metadata=dict(body.metadata),
        )
        record.created_at = datetime.now(UTC).isoformat()
        record.updated_at = record.created_at

        async def complete() -> None:
            await asyncio.sleep(0)
            record.total_input_tokens = 5
            record.total_output_tokens = 7
            record.total_tokens = 12
            record.last_ai_message = "completed once"
            record.status = RunStatus.success
            record.updated_at = datetime.now(UTC).isoformat()

        record.task = asyncio.create_task(complete())
        records[record.run_id] = record
        return record

    class RunManager:
        async def get(self, run_id, *, user_id):
            assert user_id == "owner-a"
            return records.get(run_id)

        async def cancel(self, run_id):
            record = records[run_id]
            record.status = RunStatus.interrupted
            return True

    monkeypatch.setattr(agent_public_api, "start_run", start_once)
    app = FastAPI()
    app.state.agent_api_key_repo = env.keys
    app.state.published_agent_repo = env.agents
    app.state.run_manager = RunManager()
    app.add_middleware(AgentAPIAuthMiddleware)
    app.include_router(agent_public_api.router)
    app.dependency_overrides[get_external_conversation_repo] = lambda: env.conversations
    app.dependency_overrides[get_external_idempotency_repo] = lambda: env.idempotency
    app.dependency_overrides[get_published_agent_repo] = lambda: env.agents
    app.dependency_overrides[get_published_agent_resolver] = lambda: env.resolver
    app.dependency_overrides[get_quota_ledger] = lambda: env.quota_ledger

    path = f"/api/v1/agents/{agent['id']}/conversations/{conversation_id}/runs"
    headers = {
        "Authorization": f"Bearer {key['api_key']}",
        "Idempotency-Key": "request-1",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        first_response = await client.post(
            path,
            json={"message": "run exactly once"},
            headers=headers,
        )
        run_id = first_response.json()["run_id"]
        await records[run_id].task
        settlement_tasks = list(getattr(app.state, "agent_quota_tasks", set()))
        if settlement_tasks:
            await asyncio.gather(*settlement_tasks)
        replay_response = await client.post(
            path,
            json={"message": "run exactly once"},
            headers=headers,
        )

    assert first_response.status_code == replay_response.status_code == 202
    assert replay_response.json()["run_id"] == run_id
    assert replay_response.json()["status"] == "completed"
    assert starts == 1

    feishu_starts = 0

    async def start_feishu_once(
        body,
        requested_thread_id,
        request,
        *,
        published_context=None,
        run_id=None,
        **_kwargs,
    ) -> RunRecord:
        nonlocal feishu_starts
        feishu_starts += 1
        assert published_context.agent_id == agent["id"]
        assert published_context.source == "feishu"
        record = RunRecord(
            run_id=str(run_id),
            thread_id=requested_thread_id,
            assistant_id="lead_agent",
            status=RunStatus.running,
            on_disconnect=DisconnectMode.continue_,
            metadata=dict(body.metadata),
        )
        record.created_at = datetime.now(UTC).isoformat()
        record.updated_at = record.created_at

        async def complete() -> None:
            await asyncio.sleep(0)
            record.total_input_tokens = 3
            record.total_output_tokens = 5
            record.total_tokens = 8
            record.last_ai_message = "feishu completed once"
            record.status = RunStatus.success
            record.updated_at = datetime.now(UTC).isoformat()

        record.task = asyncio.create_task(complete())
        records[record.run_id] = record
        return record

    channel_runtime = PublishedChannelRuntime(
        mapping_store=env.mappings,
        resolver=env.resolver,
        quota_ledger=env.quota_ledger,
        executor=GatewayPublishedRunExecutor(
            app,
            run_starter=start_feishu_once,
        ),
    )
    bus = MessageBus()
    manager = ChannelManager(
        bus,
        ChannelStore(tmp_path / "acceptance-channel-store.json"),
        published_runtime=channel_runtime,
    )
    outbound = asyncio.get_running_loop().create_future()

    async def capture_outbound(message) -> None:
        if not outbound.done():
            outbound.set_result(message)

    bus.subscribe_outbound(capture_outbound)
    await manager.start()
    channel = FeishuChannel(
        bus,
        app_id="cli_idempotency",
        app_secret="acceptance-secret",
        verification_token="acceptance-verification-token",
        binding_id=binding["id"],
        agent_id=agent["id"],
        event_deduplicator=env.events,
    )
    channel._main_loop = asyncio.get_running_loop()
    feishu_event = SimpleNamespace(
        header=SimpleNamespace(
            event_id="event-idempotency",
            create_time=str(int(time.time() * 1000)),
            token="acceptance-verification-token",
        ),
        event=SimpleNamespace(
            message=SimpleNamespace(
                chat_id="chat-idempotency",
                message_id="message-idempotency",
                root_id=None,
                thread_id=None,
                chat_type="p2p",
                content=json.dumps({"text": "run Feishu exactly once"}),
            ),
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="feishu-user-idempotency")),
        ),
    )
    try:
        channel._on_message(feishu_event)
        channel._on_message(feishu_event)
        feishu_response = await asyncio.wait_for(outbound, timeout=2)
        await asyncio.sleep(0.05)
    finally:
        await manager.stop()

    assert feishu_response.text == "feishu completed once"
    assert feishu_starts == 1
    aggregate = await env.usage.aggregate_daily(
        owner_user_id="owner-a",
        agent_id=agent["id"],
        since=datetime.now(UTC) - timedelta(days=1),
    )
    assert aggregate["totals"]["runs"] == 2
    assert aggregate["totals"]["total_tokens"] == 20

    claim_values = {
        "user_id": "owner-a",
        "api_key_id": "key-idempotency",
        "idempotency_key": "repository-concurrency-check",
        "request_hash": "hash-1",
        "run_id": "run-repository-check",
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    first, first_created = await env.idempotency.claim(claim_values)
    replay, replay_created = await env.idempotency.claim(claim_values)
    assert first_created is True
    assert replay_created is False
    assert replay["id"] == first["id"]


@pytest.mark.asyncio
async def test_acceptance_14_failure_isolation(
    acceptance_env: _AcceptanceEnvironment,
    tmp_path: Path,
) -> None:
    env = acceptance_env
    failed_agent, _ = await _create_ready_agent(
        env,
        owner="owner-a",
        slug="failed-agent",
    )
    healthy_agent, _ = await _create_ready_agent(
        env,
        owner="owner-b",
        slug="healthy-agent",
    )
    secrets = LocalEncryptedSecretStore(
        tmp_path / "acceptance-feishu-secrets",
        key=Fernet.generate_key(),
    )
    failed_secret_ref = await secrets.put(
        encode_feishu_credentials(
            FeishuCredentials(
                app_secret="failed-app-secret",
                verification_token="failed-verification-token",
            )
        )
    )
    healthy_secret_ref = await secrets.put(
        encode_feishu_credentials(
            FeishuCredentials(
                app_secret="healthy-app-secret",
                verification_token="healthy-verification-token",
            )
        )
    )
    failed_binding = await env.channels.create(
        agent_id=failed_agent["id"],
        owner_user_id="owner-a",
        app_id="cli_failed",
        secret_ref=failed_secret_ref,
    )
    healthy_binding = await env.channels.create(
        agent_id=healthy_agent["id"],
        owner_user_id="owner-b",
        app_id="cli_healthy",
        secret_ref=healthy_secret_ref,
    )
    assert failed_binding is not None and healthy_binding is not None
    factory = _AcceptanceFeishuFactory()
    supervisor = FeishuSupervisor(
        env.channels,
        secrets,
        MessageBus(),
        channel_factory=factory,
    )
    try:
        await supervisor.start_binding(failed_binding["id"])
        await supervisor.start_binding(healthy_binding["id"])
        assert set(supervisor.running_binding_ids) == {
            failed_binding["id"],
            healthy_binding["id"],
        }

        await factory.instances[failed_binding["id"]].runtime_error_callback("injected acceptance runtime failure")

        failed_health = supervisor.health()[failed_binding["id"]]
        healthy_health = supervisor.health()[healthy_binding["id"]]
        failed_row = await env.channels.get(
            failed_agent["id"],
            failed_binding["id"],
            owner_user_id="owner-a",
        )
        healthy_row = await env.channels.get(
            healthy_agent["id"],
            healthy_binding["id"],
            owner_user_id="owner-b",
        )
        healthy_context = await _resolve(env, healthy_agent["id"])

        assert supervisor.running_binding_ids == (healthy_binding["id"],)
        assert failed_health.health == "unhealthy"
        assert failed_health.running is False
        assert failed_row["health"] == "unhealthy"
        assert failed_row["status"] == "active"
        assert healthy_health.health == "healthy"
        assert healthy_health.running is True
        assert healthy_row["health"] == "healthy"
        assert healthy_row["status"] == "active"
        assert factory.instances[healthy_binding["id"]].is_running is True
        assert healthy_context.agent_id == healthy_agent["id"]
    finally:
        await supervisor.shutdown()
