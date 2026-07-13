"""PostgreSQL row-lock regression tests for conversational authoring UOWs."""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.agent_release import AgentReleaseRepository
from deerflow.persistence.base import Base
from deerflow.persistence.published_agent import AgentDraftRepository, PublishedAgentRepository
from deerflow.persistence.skill_revision import SkillRevisionRepository
from deerflow.publishing.content_store import LocalContentStore
from deerflow.publishing.publish_service import PublishError, PublishService


class _NoSkillsIndex:
    def resolve_publish_snapshots(self, skill_names, owner_user_id):  # noqa: ARG002
        assert skill_names == []
        return {}


class _NoConnectors:
    async def get_instance(self, connector_id, *, owner_id=...):  # noqa: ARG002
        return None


@pytest.mark.asyncio
async def test_duplicate_setup_and_structured_patch_cannot_both_write_stale_revision():
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        if os.environ.get("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("PostgreSQL authoring concurrency gate is required but TEST_POSTGRES_URL is unset")
        pytest.skip("local PostgreSQL unavailable")

    schema = f"authoring_{uuid4().hex}"
    admin = create_async_engine(url)
    scoped = None
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        scoped = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
        async with scoped.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sf = async_sessionmaker(scoped, expire_on_commit=False)
        agents = PublishedAgentRepository(sf)
        drafts = AgentDraftRepository(sf)
        created = await agents.setup_authoring_bundle(
            owner_user_id="user-a",
            slug="race",
            display_name="Race",
            description="old",
            soul_markdown="old soul",
            skills=[],
        )
        agent_id = created["agent"]["id"]

        setup_has_lock = asyncio.Event()
        patch_at_cas = asyncio.Event()

        async def _hold_setup_after_draft_lock() -> None:
            setup_has_lock.set()
            await patch_at_cas.wait()

        async def _mark_patch_at_cas() -> None:
            patch_at_cas.set()

        agents._after_authoring_draft_lock = _hold_setup_after_draft_lock  # noqa: SLF001
        drafts._before_cas = _mark_patch_at_cas  # noqa: SLF001

        setup_task = asyncio.create_task(
            agents.setup_authoring_bundle(
                owner_user_id="user-a",
                slug="race",
                display_name="Race",
                description="setup",
                soul_markdown="setup soul",
                skills=[],
            )
        )
        await asyncio.wait_for(setup_has_lock.wait(), timeout=5)
        patch_task = asyncio.create_task(
            drafts.update_bundle(
                agent_id,
                owner_user_id="user-a",
                revision=1,
                soul_markdown="patch soul",
            )
        )
        setup_result, patch_result = await asyncio.wait_for(
            asyncio.gather(setup_task, patch_task),
            timeout=10,
        )

        assert setup_result["draft"]["revision"] == 2
        assert patch_result is None
        final = await drafts.get(agent_id, owner_user_id="user-a")
        assert final["revision"] == 2
        assert final["soul_markdown"] == "setup soul"
    finally:
        if scoped is not None:
            await scoped.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.asyncio
async def test_publish_rejects_snapshot_when_concurrent_patch_commits(tmp_path):
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        if os.environ.get("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("PostgreSQL publish concurrency gate is required but TEST_POSTGRES_URL is unset")
        pytest.skip("local PostgreSQL unavailable")

    schema = f"publish_{uuid4().hex}"
    admin = create_async_engine(url)
    scoped = None
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        scoped = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
        async with scoped.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sf = async_sessionmaker(scoped, expire_on_commit=False)
        agents = PublishedAgentRepository(sf)
        drafts = AgentDraftRepository(sf)
        releases = AgentReleaseRepository(sf)
        service = PublishService(
            published_agent_repo=agents,
            draft_repo=drafts,
            release_repo=releases,
            skill_revision_repo=SkillRevisionRepository(sf),
            content_store=LocalContentStore(tmp_path),
            skills_index=_NoSkillsIndex(),
            connector_repo=_NoConnectors(),
            model_index=set(),
            tool_group_whitelist=set(),
        )
        created = await agents.setup_authoring_bundle(
            owner_user_id="user-a",
            slug="publish-race",
            display_name="Publish race",
            soul_markdown="stable soul",
            skills=[],
        )
        agent_id = created["agent"]["id"]

        snapshot_ready = asyncio.Event()
        patch_finished = asyncio.Event()

        async def _pause_after_snapshot(_draft) -> None:
            snapshot_ready.set()
            await patch_finished.wait()

        drafts._after_publish_snapshot = _pause_after_snapshot  # noqa: SLF001
        publish_task = asyncio.create_task(service.publish(agent_id, owner_user_id="user-a"))
        await asyncio.wait_for(snapshot_ready.wait(), timeout=5)
        patched = await drafts.update_bundle(
            agent_id,
            owner_user_id="user-a",
            revision=1,
            soul_markdown="concurrent soul",
        )
        assert patched is not None
        assert patched["revision"] == 2
        patch_finished.set()

        with pytest.raises(PublishError) as exc_info:
            await asyncio.wait_for(publish_task, timeout=10)
        assert [violation.code for violation in exc_info.value.violations] == ["DRAFT_REVISION_CONFLICT"]
        assert await releases.list_by_agent(agent_id, owner_user_id="user-a") == []
    finally:
        if scoped is not None:
            await scoped.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
