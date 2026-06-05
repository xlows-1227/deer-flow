from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.config.app_config import AppConfig
from deerflow.config.connectors_config import ConnectorsConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.connectors.errors import ConnectorDisabledError, ConnectorNotFoundError, ConnectorValidationError
from deerflow.connectors.registry import ConnectorRegistry
from deerflow.connectors.schemas import ConnectorCredentialRef, ConnectorMetadata, ConnectorRuntimeContext, ConnectorTestResult, ConnectorTypeDefinition, QueryColumn, QueryResult
from deerflow.connectors.secrets import InlineSecretStore, MultiSecretStore, SecretValue
from deerflow.connectors.service import ConnectorService
from deerflow.persistence.base import Base
from deerflow.persistence.connector import ConnectorRepository


class FakeSecretStore:
    def get_secret(self, credential, context=None):  # noqa: ARG002
        return SecretValue("mysql://user:pw@db/orders")


class FakeAdapter:
    async def test(self, instance, secrets):  # noqa: ARG002
        return ConnectorTestResult(status="ok", capabilities=["database.query"])

    async def introspect(self, instance, secrets):  # noqa: ARG002
        return ConnectorMetadata(schemas=[{"name": "orders", "tables": []}], tables=[])

    async def query(self, instance, sql, policy, context, *, secrets=None):  # noqa: ARG002
        return QueryResult(columns=[QueryColumn(name="n", type="integer")], rows=[[1]], row_count=1)


class FakeDocumentAdapter:
    async def test(self, instance, secrets):  # noqa: ARG002
        return ConnectorTestResult(status="ok", capabilities=["document.read"])

    async def introspect(self, instance, secrets):  # noqa: ARG002
        return ConnectorMetadata(schemas=[], tables=[])

    async def execute(self, instance, capability, args, policy, context, *, secrets=None):  # noqa: ARG002
        return {"document_id": args["document_id"], "policy": policy}


@pytest_asyncio.fixture()
async def connector_service(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'connectors.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    repo = ConnectorRepository(async_sessionmaker(engine, expire_on_commit=False))
    app_config = AppConfig(sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"), connectors=ConnectorsConfig(enabled=True))
    service = ConnectorService(repo, secret_store=FakeSecretStore(), app_config=app_config)
    service._adapters["mysql"] = FakeAdapter()
    try:
        yield service
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_connector_service_query_audits_and_masks_secrets(connector_service: ConnectorService):
    connector = await connector_service.create_connector(
        {
            "name": "orders",
            "type": "mysql",
            "config": {"host": "db", "database": "orders"},
            "credential": {"provider": "env", "ref": "MYSQL_URL"},
            "default_policy": {"allowed_schemas": ["orders"], "max_rows": 10},
        },
        owner_id="u1",
    )

    result = await connector_service.query_database(
        connector.id,
        "select * from orders.fact_orders",
        reason="test",
        context=ConnectorRuntimeContext(user_id="u1"),
    )

    assert result.rows == [[1]]
    audit = await connector_service.repository.list_audit(connector_id=connector.id)
    assert audit[0]["request_summary_json"]["tables"] == ["orders.fact_orders"]
    assert "MYSQL_URL" not in str(audit)


@pytest.mark.asyncio
async def test_connector_service_rejects_disabled_connector_type(connector_service: ConnectorService):
    connector_service.app_config.connectors.enabled_types = ["starrocks"]

    with pytest.raises(ConnectorDisabledError):
        await connector_service.create_connector(
            {
                "name": "orders",
                "type": "mysql",
                "config": {"host": "db", "database": "orders"},
                "credential": {"provider": "env", "ref": "MYSQL_URL"},
            },
            owner_id="u1",
        )

    with pytest.raises(ConnectorDisabledError):
        await connector_service.get_connector_type("mysql")


@pytest.mark.asyncio
async def test_connector_service_validates_config_updates(connector_service: ConnectorService):
    connector = await connector_service.create_connector(
        {
            "name": "orders",
            "type": "mysql",
            "config": {"host": "db", "database": "orders"},
            "credential": {"provider": "env", "ref": "MYSQL_URL"},
        },
        owner_id="u1",
    )

    with pytest.raises(ConnectorValidationError):
        await connector_service.update_connector(connector.id, {"config": {"host": "db"}}, owner_id="u1")


@pytest.mark.asyncio
async def test_connector_service_tests_unsaved_config_without_persisting(connector_service: ConnectorService):
    result = await connector_service.test_connector_config(
        type_name="mysql",
        config={"host": "db", "database": "orders"},
        credential={"provider": "env", "ref": "MYSQL_URL"},
        context=ConnectorRuntimeContext(user_id="u1"),
    )

    assert result.status == "ok"
    assert await connector_service.list_connectors(owner_id=...) == []


@pytest.mark.asyncio
async def test_connector_service_tests_edited_config_with_existing_credential(connector_service: ConnectorService):
    connector = await connector_service.create_connector(
        {
            "name": "orders",
            "type": "mysql",
            "config": {"host": "db", "database": "orders"},
            "credential": {"provider": "env", "ref": "MYSQL_URL"},
        },
        owner_id="u1",
    )

    result = await connector_service.test_connector_config_for_instance(
        connector.id,
        values={"config": {"host": "db2", "database": "orders"}},
        context=ConnectorRuntimeContext(user_id="u1"),
        owner_id="u1",
    )

    assert result.status == "ok"
    stored = await connector_service.get_connector(connector.id, owner_id="u1")
    assert stored.config["host"] == "db"


@pytest.mark.asyncio
async def test_connector_service_lists_only_authorized_summaries(connector_service: ConnectorService):
    connector = await connector_service.create_connector(
        {
            "name": "orders",
            "type": "mysql",
            "config": {"host": "db", "database": "orders"},
            "credential": {"provider": "env", "ref": "MYSQL_URL"},
        },
        owner_id="owner",
    )
    await connector_service.create_grant(
        connector.id,
        {"subject_type": "skill", "subject_id": "analysis", "capabilities": ["database.query"]},
        created_by="owner",
    )

    summaries = await connector_service.list_available_summaries(
        context=ConnectorRuntimeContext(skill_name="analysis"),
        capability="database.query",
    )

    assert summaries[0]["id"] == connector.id
    assert summaries[0]["connection"] == {"host": "db", "port": 3306, "database": "orders"}
    assert "config" not in summaries[0]
    assert "credential" not in summaries[0]


@pytest.mark.asyncio
async def test_connector_service_filters_summaries_by_selected_connector_ids(connector_service: ConnectorService):
    orders = await connector_service.create_connector(
        {
            "name": "orders",
            "type": "mysql",
            "config": {"host": "db", "database": "orders"},
            "credential": {"provider": "env", "ref": "MYSQL_URL"},
        },
        owner_id="owner",
    )
    finance = await connector_service.create_connector(
        {
            "name": "finance",
            "type": "mysql",
            "config": {"host": "db", "database": "finance"},
            "credential": {"provider": "env", "ref": "MYSQL_URL"},
        },
        owner_id="owner",
    )

    summaries = await connector_service.list_available_summaries(
        context=ConnectorRuntimeContext(user_id="owner", connector_ids=[finance.id]),
        capability="database.query",
    )

    assert [summary["id"] for summary in summaries] == [finance.id]
    assert orders.id not in {summary["id"] for summary in summaries}


@pytest.mark.asyncio
async def test_connector_service_grants_require_connector_owner(connector_service: ConnectorService):
    connector = await connector_service.create_connector(
        {
            "name": "orders",
            "type": "mysql",
            "config": {"host": "db", "database": "orders"},
            "credential": {"provider": "env", "ref": "MYSQL_URL"},
        },
        owner_id="owner",
    )
    grant = await connector_service.create_grant(
        connector.id,
        {"subject_type": "skill", "subject_id": "analysis", "capabilities": ["database.query"]},
        created_by="owner",
        owner_id="owner",
    )

    with pytest.raises(ConnectorNotFoundError):
        await connector_service.list_grants(connector.id, owner_id="other")
    with pytest.raises(ConnectorNotFoundError):
        await connector_service.update_grant(connector.id, grant.id, {"capabilities": ["database.schema.inspect"]}, owner_id="other")


@pytest.mark.asyncio
async def test_connector_service_executes_non_database_action(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'connectors.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    repo = ConnectorRepository(async_sessionmaker(engine, expire_on_commit=False))
    registry = ConnectorRegistry()
    registry.register(
        ConnectorTypeDefinition(
            type="doc_test",
            category="document",
            display_name="Doc Test",
            adapter="tests.fake:FakeDocumentAdapter",
            capabilities=["document.read"],
            config_schema={"space": {"type": "string", "required": True}},
            default_policy={"allowed_spaces": ["kb"], "max_documents_per_query": 5},
        )
    )
    service = ConnectorService(
        repo,
        registry=registry,
        secret_store=FakeSecretStore(),
        app_config=AppConfig(sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"), connectors=ConnectorsConfig(enabled=True, enabled_types=["doc_test"])),
    )
    service._adapters["doc_test"] = FakeDocumentAdapter()
    try:
        connector = await service.create_connector(
            {
                "name": "docs",
                "type": "doc_test",
                "config": {"space": "kb"},
                "credential": {"provider": "env", "ref": "DOC_SECRET"},
            },
            owner_id="owner",
        )
        await service.create_grant(
            connector.id,
            {"subject_type": "skill", "subject_id": "reader", "capabilities": ["document.read"], "policy_override": {"allowed_spaces": ["kb"]}},
            created_by="owner",
        )

        result = await service.execute_connector_action(
            connector.id,
            capability="document.read",
            args={"document_id": "doc_123"},
            reason="read test",
            context=ConnectorRuntimeContext(skill_name="reader"),
        )

        assert result["document_id"] == "doc_123"
        assert result["policy"]["allowed_spaces"] == ["kb"]
        audit = await service.repository.list_audit(connector_id=connector.id)
        assert audit[0]["capability"] == "document.read"
    finally:
        await engine.dispose()


class _RecordingAdapter:
    def __init__(self):
        self.received_secrets: list[dict] = []

    async def test(self, instance, secrets):  # noqa: ARG002
        self.received_secrets.append(dict(secrets))
        return ConnectorTestResult(status="ok", capabilities=["database.query"])


@pytest.mark.asyncio
async def test_connector_service_encrypts_inline_credential_on_create(tmp_path):
    import json

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'connectors.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    repo = ConnectorRepository(async_sessionmaker(engine, expire_on_commit=False))
    app_config = AppConfig(
        sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
        connectors=ConnectorsConfig(enabled=True),
    )
    service = ConnectorService(
        repo,
        secret_store=MultiSecretStore(),
        app_config=app_config,
    )
    adapter = _RecordingAdapter()
    service._adapters["mysql"] = adapter
    try:
        connector = await service.create_connector(
            {
                "name": "orders_inline",
                "type": "mysql",
                "config": {"host": "db", "database": "orders"},
                "credential": {
                    "provider": "inline",
                    "username": "readonly",
                    "password": "s3cr3t",
                },
            },
            owner_id="u1",
        )

        # The stored ref must be a Fernet token, not the raw password.
        assert connector.credential.provider == "inline"
        assert isinstance(connector.credential.ref, str)
        assert "s3cr3t" not in connector.credential.ref
        assert connector.credential.password in (None, "")

        # Round-trip via the multi store: ref decrypts back to {username, password}.
        decrypted = MultiSecretStore().get_secret(
            ConnectorCredentialRef(provider="inline", ref=connector.credential.ref)
        )
        # ``decrypted.value`` is a JSON string per InlineSecretStore's contract.
        payload = json.loads(decrypted.value)
        assert payload == {"username": "readonly", "password": "s3cr3t"}

        # Running test() flows the decrypted secret through to the adapter.
        await service.test_connector(connector.id, context=ConnectorRuntimeContext(user_id="u1"))
        assert adapter.received_secrets == [{"username": "readonly", "password": "s3cr3t"}]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_connector_service_inline_credential_update_requires_password(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'connectors.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    repo = ConnectorRepository(async_sessionmaker(engine, expire_on_commit=False))
    app_config = AppConfig(
        sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
        connectors=ConnectorsConfig(enabled=True),
    )
    service = ConnectorService(repo, secret_store=MultiSecretStore(), app_config=app_config)
    service._adapters["mysql"] = _RecordingAdapter()
    try:
        connector = await service.create_connector(
            {
                "name": "orders_inline",
                "type": "mysql",
                "config": {"host": "db", "database": "orders"},
                "credential": {"provider": "inline", "username": "readonly", "password": "first"},
            },
            owner_id="u1",
        )
        original_ref = connector.credential.ref

        # Patching without a new password leaves the stored secret untouched.
        await service.update_connector(
            connector.id,
            {"name": "orders_inline", "config": {"host": "db2", "database": "orders"}},
            owner_id="u1",
        )
        stored = await service.get_connector(connector.id, owner_id="u1")
        assert stored.credential.ref == original_ref

        # Supplying a fresh password rotates the encrypted ref.
        await service.update_connector(
            connector.id,
            {"credential": {"provider": "inline", "username": "readonly", "password": "second"}},
            owner_id="u1",
        )
        rotated = await service.get_connector(connector.id, owner_id="u1")
        assert rotated.credential.ref != original_ref
        decrypted = InlineSecretStore().decrypt(rotated.credential.ref)
        assert decrypted == {"username": "readonly", "password": "second"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_default_secret_store_dispatches_to_inline(tmp_path):
    """The default service (no explicit store) must support inline credentials."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'connectors.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    repo = ConnectorRepository(async_sessionmaker(engine, expire_on_commit=False))
    app_config = AppConfig(
        sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
        connectors=ConnectorsConfig(enabled=True),
    )
    service = ConnectorService(repo, app_config=app_config)
    assert isinstance(service.secret_store, MultiSecretStore)

    service._adapters["mysql"] = _RecordingAdapter()
    try:
        await service.create_connector(
            {
                "name": "orders_default",
                "type": "mysql",
                "config": {"host": "db", "database": "orders"},
                "credential": {"provider": "inline", "username": "u", "password": "p"},
            },
            owner_id="u1",
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_connector_service_persists_username_for_inline_credentials(tmp_path):
    """The repository must round-trip the username so the edit form can show it.

    Regression for the case where the API returned ``credential = {provider, ref}``
    only, dropping the username column on the floor and leaving the edit form
    blank. Inline credentials store the password encrypted inside ``ref``; the
    username is kept in a separate plaintext column so we can display it
    without decrypting.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'connectors.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    repo = ConnectorRepository(async_sessionmaker(engine, expire_on_commit=False))
    app_config = AppConfig(
        sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
        connectors=ConnectorsConfig(enabled=True),
    )
    service = ConnectorService(repo, secret_store=MultiSecretStore(), app_config=app_config)
    service._adapters["mysql"] = _RecordingAdapter()
    try:
        connector = await service.create_connector(
            {
                "name": "orders_persist",
                "type": "mysql",
                "config": {"host": "db", "database": "orders"},
                "credential": {
                    "provider": "inline",
                    "username": "readonly",
                    "password": "s3cr3t",
                },
            },
            owner_id="u1",
        )
        # The stored row carries the username next to the encrypted ref…
        row = await repo.get_instance(connector.id, owner_id="u1")
        assert row is not None
        assert row["credential"]["username"] == "readonly"
        assert row["credential"]["provider"] == "inline"
        assert "s3cr3t" not in (row["credential"]["ref"] or "")

        # …and the service-level read surfaces it through ConnectorCredentialRef.
        reloaded = await service.get_connector(connector.id, owner_id="u1")
        assert reloaded.credential.username == "readonly"
        assert reloaded.credential.ref is not None
        assert "s3cr3t" not in reloaded.credential.ref

        # Switching the credential back to env should clear the username
        # column so a stale account name doesn't linger next to a new ref.
        await service.update_connector(
            connector.id,
            {"credential": {"provider": "env", "ref": "MYSQL_URL"}},
            owner_id="u1",
        )
        rotated = await service.get_connector(connector.id, owner_id="u1")
        assert rotated.credential.provider == "env"
        assert rotated.credential.ref == "MYSQL_URL"
        assert rotated.credential.username is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_connector_service_partial_inline_credential_preserves_existing_secret(tmp_path):
    """Regression for the 500 the user hit when editing an inline connector.

    The edit form intentionally omits the password (the placeholder dots
    tell the user "leave empty to keep") and may also omit the ref. The
    service must merge the new credential with the previously stored one
    so the encrypted secret survives — and it must NEVER persist an
    inline credential without a ref (that was the 500's root cause).
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'connectors.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    repo = ConnectorRepository(async_sessionmaker(engine, expire_on_commit=False))
    app_config = AppConfig(
        sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
        connectors=ConnectorsConfig(enabled=True),
    )
    service = ConnectorService(repo, secret_store=MultiSecretStore(), app_config=app_config)
    service._adapters["mysql"] = _RecordingAdapter()
    try:
        connector = await service.create_connector(
            {
                "name": "orders_partial",
                "type": "mysql",
                "config": {"host": "db", "database": "orders"},
                "credential": {"provider": "inline", "username": "readonly", "password": "s3cr3t"},
            },
            owner_id="u1",
        )
        original_ref = connector.credential.ref

        # The realistic edit-form payload: provider + username only, no
        # password and no ref. The service must keep the stored ref.
        updated = await service.update_connector(
            connector.id,
            {"credential": {"provider": "inline", "username": "newuser"}},
            owner_id="u1",
        )
        assert updated.credential.ref == original_ref
        assert updated.credential.username == "newuser"

        # Rotating only the username must not affect runtime behavior: the
        # stored ref still decrypts to the original password.
        await service.test_connector(connector.id, context=ConnectorRuntimeContext(user_id="u1"))
        assert service._adapters["mysql"].received_secrets == [
            {"username": "newuser", "password": "s3cr3t"}
        ]

        # A truly empty inline credential (provider but no username, no
        # ref, no password) carries no signal — the merge keeps the stored
        # ref + username. The important guarantee is that we don't 500.
        unchanged = await service.update_connector(
            connector.id,
            {"credential": {"provider": "inline"}},
            owner_id="u1",
        )
        assert unchanged.credential.ref == original_ref
        assert unchanged.credential.username == "newuser"
    finally:
        await engine.dispose()
