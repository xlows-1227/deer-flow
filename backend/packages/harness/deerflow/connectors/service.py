from __future__ import annotations

import importlib
import re
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.config.app_config import AppConfig, get_app_config
from deerflow.connectors.audit import write_connector_audit
from deerflow.connectors.errors import ConnectorDisabledError, ConnectorError, ConnectorNotFoundError, ConnectorValidationError
from deerflow.connectors.policy import authorize_connector_action, merge_connector_policies, merge_database_policies
from deerflow.connectors.registry import ConnectorRegistry, get_connector_registry
from deerflow.connectors.resources import connector_safe_summary
from deerflow.connectors.schemas import (
    DATABASE_QUERY,
    DATABASE_SCHEMA_INSPECT,
    DATABASE_TABLE_SAMPLE,
    ConnectorCredentialRef,
    ConnectorGrant,
    ConnectorInstance,
    ConnectorMetadata,
    ConnectorRuntimeContext,
    ConnectorTestResult,
    DatabasePolicy,
    QueryResult,
)
from deerflow.connectors.secrets import MultiSecretStore, SecretStore
from deerflow.connectors.sql_safety import validate_read_only_sql
from deerflow.persistence.connector import ConnectorRepository
from deerflow.persistence.engine import get_session_factory

_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _assert_safe_identifier(value: str) -> None:
    if not _SAFE_IDENTIFIER_RE.match(value):
        raise ConnectorValidationError(f"Invalid SQL identifier: {value}")


def _load_adapter(adapter_path: str):
    module_name, class_name = adapter_path.split(":", 1)
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    return cls()


def _serialize_credential_for_storage(credential: dict[str, Any] | None, *, inline_store) -> dict[str, Any] | None:
    """Normalize a credential dict before it hits the repository.

    Inline credentials are encrypted into the ``ref`` slot so the database
    never sees a plaintext password. The ``username`` field is kept on the
    credential dict (and persisted to its own column by the repository) so
    the edit form can show who this connector connects as without having to
    decrypt the secret blob. We deliberately strip ``password`` so it
    cannot leak via debug dumps.

    Callers that update an existing inline connector may legitimately omit
    ``password`` (they want to keep the stored secret). In that case the
    repository needs the existing ``ref`` to persist alongside the new
    username; we therefore accept the previous credential and merge it
    here so the final dict is always complete.
    """
    if not credential:
        return credential
    data = dict(credential)
    provider = data.get("provider") or "env"
    if provider == "inline":
        password = data.get("password")
        if password:
            payload = {"username": data.get("username") or "", "password": password}
            data["ref"] = inline_store.encrypt(payload)
            data.pop("password", None)
    else:
        # Non-inline providers (env, encrypted_db) have no username column.
        data.pop("username", None)
    return data


def _merge_credential_with_existing(
    new_credential: dict[str, Any] | None,
    existing: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Carry the stored ``ref`` (and other internal fields) when the client
    sends an incomplete update.

    The repository stores the encrypted password under ``ref`` for inline
    credentials, and the only way to keep that secret across a PATCH is for
    the service to merge the new credential with the previously persisted
    one. The edit form intentionally omits the password (the placeholder
    dots tell the user "leave empty to keep") so this merge is the common
    case rather than an edge case.
    """
    if not new_credential:
        return new_credential
    merged = dict(new_credential)
    provider = merged.get("provider") or "env"
    if provider == "inline":
        if not merged.get("ref") and existing and existing.get("provider") == "inline":
            # Carry the encrypted blob forward so the inline secret survives.
            merged["ref"] = existing.get("ref")
        # Username may also be omitted; fall back to the stored value.
        if merged.get("username") is None and existing and existing.get("provider") == "inline":
            merged["username"] = existing.get("username")
    return merged


class ConnectorService:
    def __init__(
        self,
        repository: ConnectorRepository,
        *,
        registry: ConnectorRegistry | None = None,
        secret_store: SecretStore | None = None,
        app_config: AppConfig | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry or get_connector_registry()
        # Default to a multi-provider store so env refs and inline (encrypted)
        # credentials can coexist without callers having to wire two stores.
        self.secret_store = secret_store or MultiSecretStore()
        self.app_config = app_config or get_app_config()
        self._adapters: dict[str, Any] = {}

    def _adapter_for(self, type_name: str):
        self._ensure_type_enabled(type_name)
        definition = self.registry.get(type_name)
        adapter = self._adapters.get(type_name)
        if adapter is None:
            adapter = _load_adapter(definition.adapter)
            self._adapters[type_name] = adapter
        return adapter

    def _inline_store(self):
        """Return the InlineSecretStore used to encrypt inline credentials.

        If the service was constructed with a custom store (e.g. in tests) we
        still want to encrypt through a real Fernet instance so the stored
        ref is round-trippable by the default MultiSecretStore.
        """
        from deerflow.connectors.secrets import InlineSecretStore

        return InlineSecretStore()

    def _enabled_types(self) -> set[str]:
        return set(self.app_config.connectors.enabled_types or [])

    def _ensure_type_enabled(self, type_name: str) -> None:
        self.registry.get(type_name)
        enabled = self._enabled_types()
        if enabled and type_name not in enabled:
            raise ConnectorDisabledError(f"Connector type is disabled: {type_name}", recoverable=True)

    def _system_database_policy(self) -> dict[str, Any]:
        return self.app_config.connectors.default_policy.database.model_dump()

    def _system_policy_for(self, category: str) -> dict[str, Any]:
        if category == "database":
            return self._system_database_policy()
        return {}

    def _merged_default_policy_for(self, definition, instance_policy: dict[str, Any]) -> dict[str, Any]:
        if definition.category == "database":
            return merge_database_policies(definition.default_policy, instance_policy).model_dump()
        return merge_connector_policies(definition.default_policy, instance_policy)

    def _database_policy_from_decision(self, decision) -> DatabasePolicy:
        return DatabasePolicy.model_validate(decision.effective_policy)

    def _policy_summary_for(self, category: str, policy: dict[str, Any]) -> dict[str, Any]:
        if category == "database":
            db_policy = DatabasePolicy.model_validate(policy)
            return {
                "mode": db_policy.mode,
                "max_rows": db_policy.max_rows,
                "allowed_schemas": db_policy.allowed_schemas,
            }
        return {key: value for key, value in policy.items() if key.startswith(("allowed_", "max_")) or key in {"mode"}}

    async def create_connector(self, values: dict[str, Any], *, owner_id: str | None) -> ConnectorInstance:
        type_name = str(values["type"])
        self._ensure_type_enabled(type_name)
        validated_config = self.registry.validate_config(type_name, dict(values.get("config") or {}))
        definition = self.registry.get(type_name)
        policy = self._merged_default_policy_for(definition, values.get("default_policy") or {})
        credential = _serialize_credential_for_storage(values.get("credential"), inline_store=self._inline_store())
        created = await self.repository.create_instance(
            {
                **values,
                "owner_id": owner_id,
                "config": validated_config,
                "default_policy": policy,
                "credential": credential,
            }
        )
        return ConnectorInstance.model_validate(created)

    async def list_connector_types(self) -> list[dict[str, Any]]:
        enabled = set(self.app_config.connectors.enabled_types or [])
        return [definition.safe_dump() for definition in self.registry.list() if not enabled or definition.type in enabled]

    async def get_connector_type(self, type_name: str) -> dict[str, Any]:
        self._ensure_type_enabled(type_name)
        return self.registry.get(type_name).safe_dump()

    async def get_connector(self, connector_id: str, *, owner_id: str | None | object = ...) -> ConnectorInstance:
        row = await self.repository.get_instance(connector_id, owner_id=owner_id)
        if row is None:
            raise ConnectorNotFoundError(f"Connector not found: {connector_id}")
        return ConnectorInstance.model_validate(row)

    async def list_connectors(self, *, owner_id: str | None | object = ..., include_disabled: bool = True) -> list[ConnectorInstance]:
        return [ConnectorInstance.model_validate(item) for item in await self.repository.list_instances(owner_id=owner_id, include_disabled=include_disabled)]

    async def update_connector(self, connector_id: str, values: dict[str, Any], *, owner_id: str | None | object = ...) -> ConnectorInstance:
        existing = await self.repository.get_instance(connector_id, owner_id=owner_id)
        if existing is None:
            raise ConnectorNotFoundError(f"Connector not found: {connector_id}")
        instance = ConnectorInstance.model_validate(existing)
        if values.get("status") == "active":
            self._ensure_type_enabled(instance.type)
        if "config" in values:
            values["config"] = self.registry.validate_config(instance.type, dict(values.get("config") or {}))
        if "default_policy" in values:
            definition = self.registry.get(instance.type)
            values["default_policy"] = self._merged_default_policy_for(definition, values.get("default_policy") or {})
        if "credential" in values:
            # Merge with the existing credential so a partial update (e.g.
            # "rotate only the username", or "leave the password alone")
            # keeps the encrypted ref instead of dropping it on the floor.
            existing_cred = existing.get("credential") if isinstance(existing, dict) else None
            merged = _merge_credential_with_existing(values.get("credential"), existing_cred)
            if merged is not None and not merged.get("ref"):
                raise ConnectorValidationError(
                    "Inline connector requires a password (or a previously stored secret).",
                    recoverable=True,
                )
            values["credential"] = _serialize_credential_for_storage(merged, inline_store=self._inline_store())
        row = await self.repository.update_instance(connector_id, values, owner_id=owner_id)
        if row is None:
            raise ConnectorNotFoundError(f"Connector not found: {connector_id}")
        return ConnectorInstance.model_validate(row)

    async def delete_connector(self, connector_id: str, *, owner_id: str | None | object = ...) -> bool:
        if not await self.repository.soft_delete_instance(connector_id, owner_id=owner_id):
            raise ConnectorNotFoundError(f"Connector not found: {connector_id}")
        return True

    async def create_grant(self, connector_id: str, values: dict[str, Any], *, created_by: str | None, owner_id: str | None | object = ...) -> ConnectorGrant:
        await self.get_connector(connector_id, owner_id=owner_id)
        grant = await self.repository.create_grant({"connector_id": connector_id, "created_by": created_by, **values})
        return ConnectorGrant.model_validate(grant)

    async def list_grants(self, connector_id: str, *, owner_id: str | None | object = ...) -> list[ConnectorGrant]:
        await self.get_connector(connector_id, owner_id=owner_id)
        return [ConnectorGrant.model_validate(item) for item in await self.repository.list_grants(connector_id)]

    async def update_grant(self, connector_id: str, grant_id: str, values: dict[str, Any], *, owner_id: str | None | object = ...) -> ConnectorGrant:
        await self.get_connector(connector_id, owner_id=owner_id)
        row = await self.repository.update_grant(grant_id, values, connector_id=connector_id)
        if row is None:
            raise ConnectorNotFoundError(f"Connector grant not found: {grant_id}")
        return ConnectorGrant.model_validate(row)

    async def delete_grant(self, connector_id: str, grant_id: str, *, owner_id: str | None | object = ...) -> bool:
        await self.get_connector(connector_id, owner_id=owner_id)
        if not await self.repository.delete_grant(grant_id, connector_id=connector_id):
            raise ConnectorNotFoundError(f"Connector grant not found: {grant_id}")
        return True

    async def _load_instance_and_grants(self, connector_id: str) -> tuple[ConnectorInstance, list[ConnectorGrant]]:
        instance = await self.get_connector(connector_id, owner_id=...)
        self._ensure_type_enabled(instance.type)
        if instance.status != "active":
            raise ConnectorDisabledError(f"Connector is not active: {connector_id}", recoverable=True)
        grants = [ConnectorGrant.model_validate(item) for item in await self.repository.list_grants(connector_id)]
        return instance, grants

    def _resolve_secret(self, credential: ConnectorCredentialRef, context: ConnectorRuntimeContext) -> dict[str, Any]:
        secret = self.secret_store.get_secret(credential, context)
        value = secret.value
        # Support structured secrets (JSON dict with username/password) in addition to plain URL strings.
        if value.strip().startswith(("{", "[")):
            try:
                import json

                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
        return {"value": value}

    def _transient_instance(
        self,
        *,
        type_name: str,
        config: dict[str, Any],
        credential: dict[str, Any] | ConnectorCredentialRef,
        default_policy: dict[str, Any] | None = None,
        existing: ConnectorInstance | None = None,
    ) -> ConnectorInstance:
        self._ensure_type_enabled(type_name)
        validated_config = self.registry.validate_config(type_name, dict(config or {}))
        definition = self.registry.get(type_name)
        policy = self._merged_default_policy_for(definition, default_policy or {})
        return ConnectorInstance(
            id=existing.id if existing else "conn_draft",
            tenant_id=existing.tenant_id if existing else None,
            owner_id=existing.owner_id if existing else None,
            name=existing.name if existing else "draft",
            display_name=existing.display_name if existing else None,
            type=type_name,
            status=existing.status if existing else "active",
            config=validated_config,
            credential=ConnectorCredentialRef.model_validate(credential),
            default_policy=policy,
        )

    async def test_connector_config(
        self,
        *,
        type_name: str,
        config: dict[str, Any],
        credential: dict[str, Any],
        default_policy: dict[str, Any] | None = None,
        context: ConnectorRuntimeContext,
    ) -> ConnectorTestResult:
        instance = self._transient_instance(type_name=type_name, config=config, credential=credential, default_policy=default_policy)
        secrets = self._resolve_secret(instance.credential, context)
        return await self._adapter_for(instance.type).test(instance, secrets)

    async def test_connector_config_for_instance(
        self,
        connector_id: str,
        *,
        values: dict[str, Any],
        context: ConnectorRuntimeContext,
        owner_id: str | None | object = ...,
    ) -> ConnectorTestResult:
        existing = await self.get_connector(connector_id, owner_id=owner_id)
        instance = self._transient_instance(
            type_name=existing.type,
            config=dict(values.get("config") or existing.config),
            credential=values.get("credential") or existing.credential,
            default_policy=values.get("default_policy") or existing.default_policy,
            existing=existing,
        )
        secrets = self._resolve_secret(instance.credential, context)
        return await self._adapter_for(instance.type).test(instance, secrets)

    async def test_connector(self, connector_id: str, *, context: ConnectorRuntimeContext) -> ConnectorTestResult:
        start = time.perf_counter()
        instance = await self.get_connector(connector_id, owner_id=...)
        adapter = self._adapter_for(instance.type)
        try:
            secrets = self._resolve_secret(instance.credential, context)
            result = await adapter.test(instance, secrets)
            await self.repository.update_instance(connector_id, {"health": result.model_dump(), "status": instance.status, "last_tested_at": datetime.now(UTC)}, owner_id=...)
            await write_connector_audit(
                self.repository,
                connector_id=connector_id,
                connector_type=instance.type,
                context=context,
                capability=DATABASE_SCHEMA_INSPECT,
                operation="test",
                decision="allow",
                result_summary=result.model_dump(),
                elapsed_ms=int((time.perf_counter() - start) * 1000),
            )
            return result
        except ConnectorError as exc:
            await write_connector_audit(self.repository, connector_id=connector_id, connector_type=instance.type, context=context, capability=DATABASE_SCHEMA_INSPECT, operation="test", decision="error", error_code=exc.code, error_message=exc.message)
            raise

    async def introspect_connector(self, connector_id: str, *, context: ConnectorRuntimeContext) -> ConnectorMetadata:
        instance, grants = await self._load_instance_and_grants(connector_id)
        start = time.perf_counter()
        try:
            definition = self.registry.get(instance.type)
            decision = authorize_connector_action(
                connector_policy=instance.default_policy,
                grants=grants,
                context=context,
                capability=DATABASE_SCHEMA_INSPECT,
                system_policy=self._system_policy_for(definition.category),
                type_policy=definition.default_policy,
                owner_id=instance.owner_id,
            )
            secrets = self._resolve_secret(instance.credential, context)
            metadata = await self._adapter_for(instance.type).introspect(instance, secrets)
            await self.repository.put_metadata(connector_id, "schema", metadata.model_dump())
            await write_connector_audit(
                self.repository,
                connector_id=connector_id,
                connector_type=instance.type,
                context=context,
                capability=DATABASE_SCHEMA_INSPECT,
                operation="introspect",
                decision="allow",
                result_summary={"schemas": len(metadata.schemas), "tables": len(metadata.tables), "policy": decision.reason},
                elapsed_ms=int((time.perf_counter() - start) * 1000),
            )
            return metadata
        except ConnectorError as exc:
            await write_connector_audit(self.repository, connector_id=connector_id, connector_type=instance.type, context=context, capability=DATABASE_SCHEMA_INSPECT, operation="introspect", decision="deny" if exc.status_code in (400, 403) else "error", error_code=exc.code, error_message=exc.message)
            raise

    async def get_cached_schema(self, connector_id: str, *, owner_id: str | None | object = ...) -> dict[str, Any] | None:
        instance = await self.get_connector(connector_id, owner_id=owner_id)
        self._ensure_type_enabled(instance.type)
        if instance.status != "active":
            raise ConnectorDisabledError(f"Connector is not active: {connector_id}", recoverable=True)
        return await self.repository.get_metadata(connector_id, "schema")

    async def query_database(self, connector_id: str, sql: str, *, reason: str, context: ConnectorRuntimeContext) -> QueryResult:
        instance, grants = await self._load_instance_and_grants(connector_id)
        start = time.perf_counter()
        safety = None
        try:
            definition = self.registry.get(instance.type)
            decision = authorize_connector_action(
                connector_policy=instance.default_policy,
                grants=grants,
                context=context,
                capability=DATABASE_QUERY,
                system_policy=self._system_policy_for(definition.category),
                type_policy=definition.default_policy,
                owner_id=instance.owner_id,
            )
            policy = self._database_policy_from_decision(decision)
            safety = validate_read_only_sql(sql, policy=policy, dialect=instance.type, default_schema=instance.config.get("database"))
            secrets = self._resolve_secret(instance.credential, context)
            result = await self._adapter_for(instance.type).query(instance, safety.sql, policy, context, secrets=secrets)
            await self.repository.update_instance(connector_id, {"last_used_at": datetime.now(UTC)}, owner_id=...)
            await write_connector_audit(
                self.repository,
                connector_id=connector_id,
                connector_type=instance.type,
                context=context,
                capability=DATABASE_QUERY,
                operation="query",
                decision="allow",
                request_summary={"sql_hash": safety.sql_hash, "sql_preview": safety.normalized_preview, "tables": safety.tables, "reason": reason},
                result_summary={"row_count": result.row_count, "truncated": result.truncated},
                elapsed_ms=int((time.perf_counter() - start) * 1000),
            )
            return result
        except ConnectorError as exc:
            await write_connector_audit(
                self.repository,
                connector_id=connector_id,
                connector_type=instance.type,
                context=context,
                capability=DATABASE_QUERY,
                operation="query",
                decision="deny" if exc.status_code in (400, 403) else "error",
                request_summary={"sql_hash": safety.sql_hash, "tables": safety.tables} if safety else {},
                error_code=exc.code,
                error_message=exc.message,
            )
            raise

    async def sample_database_table(self, connector_id: str, *, schema: str, table: str, limit: int, context: ConnectorRuntimeContext) -> QueryResult:
        _assert_safe_identifier(schema)
        _assert_safe_identifier(table)
        sql = f"SELECT * FROM `{schema}`.`{table}` LIMIT {max(1, min(limit, 100))}"
        return await self.query_database(connector_id, sql, reason=f"Sample table {schema}.{table}", context=context)

    async def execute_connector_action(
        self,
        connector_id: str,
        *,
        capability: str,
        args: dict[str, Any],
        reason: str,
        context: ConnectorRuntimeContext,
    ) -> Any:
        """Execute a connector capability through the generic adapter seam.

        Database query/sample/inspect capabilities keep their specialized
        safety paths. Other connector categories can implement ``execute`` on
        their adapter without changing the service, API governance, grants, or
        audit pipeline.
        """
        if capability == DATABASE_QUERY:
            return await self.query_database(connector_id, str(args.get("sql", "")), reason=reason, context=context)
        if capability == DATABASE_SCHEMA_INSPECT:
            return await self.introspect_connector(connector_id, context=context)
        if capability == DATABASE_TABLE_SAMPLE:
            return await self.sample_database_table(
                connector_id,
                schema=str(args.get("schema") or args.get("schema_name") or ""),
                table=str(args.get("table") or ""),
                limit=int(args.get("limit") or 20),
                context=context,
            )

        instance, grants = await self._load_instance_and_grants(connector_id)
        start = time.perf_counter()
        try:
            definition = self.registry.get(instance.type)
            decision = authorize_connector_action(
                connector_policy=instance.default_policy,
                grants=grants,
                context=context,
                capability=capability,
                system_policy=self._system_policy_for(definition.category),
                type_policy=definition.default_policy,
                owner_id=instance.owner_id,
            )
            secrets = self._resolve_secret(instance.credential, context)
            result = await self._adapter_for(instance.type).execute(
                instance,
                capability,
                args,
                decision.effective_policy,
                context,
                secrets=secrets,
            )
            result_summary = result.model_dump() if hasattr(result, "model_dump") else {"result_type": type(result).__name__}
            await write_connector_audit(
                self.repository,
                connector_id=connector_id,
                connector_type=instance.type,
                context=context,
                capability=capability,
                operation="action",
                decision="allow",
                request_summary={"reason": reason, "arg_keys": sorted(args.keys())},
                result_summary=result_summary if isinstance(result_summary, dict) else {},
                elapsed_ms=int((time.perf_counter() - start) * 1000),
            )
            return result
        except ConnectorError as exc:
            await write_connector_audit(
                self.repository,
                connector_id=connector_id,
                connector_type=instance.type,
                context=context,
                capability=capability,
                operation="action",
                decision="deny" if exc.status_code in (400, 403) else "error",
                request_summary={"reason": reason, "arg_keys": sorted(args.keys())},
                error_code=exc.code,
                error_message=exc.message,
            )
            raise

    async def list_available_summaries(self, *, context: ConnectorRuntimeContext, capability: str | None = None) -> list[dict[str, Any]]:
        instances = await self.list_connectors(owner_id=..., include_disabled=False)
        selected_ids = set(context.connector_ids or [])
        if selected_ids:
            instances = [instance for instance in instances if instance.id in selected_ids]
        grants_map = await self.repository.list_grants_for_connectors([i.id for i in instances])
        summaries: list[dict[str, Any]] = []
        for instance in instances:
            grants = [ConnectorGrant.model_validate(item) for item in grants_map.get(instance.id, [])]
            try:
                self._ensure_type_enabled(instance.type)
                definition = self.registry.get(instance.type)
            except ConnectorError:
                continue
            requested = capability or (definition.capabilities[0] if definition.capabilities else "")
            try:
                decision = authorize_connector_action(
                    connector_policy=instance.default_policy,
                    grants=grants,
                    context=context,
                    capability=requested,
                    system_policy=self._system_policy_for(definition.category),
                    type_policy=definition.default_policy,
                    owner_id=instance.owner_id,
                )
            except ConnectorError:
                continue
            summaries.append(
                connector_safe_summary(
                    instance,
                    definition.capabilities,
                    self._policy_summary_for(definition.category, decision.effective_policy),
                )
            )
        return summaries

    async def list_audit(self, *, connector_id: str | None = None, user_id: str | None = None, owner_id: str | None | object = ..., limit: int = 100) -> list[dict[str, Any]]:
        if connector_id is not None:
            await self.get_connector(connector_id, owner_id=owner_id)
            return await self.repository.list_audit(connector_id=connector_id, limit=limit)
        return await self.repository.list_audit(user_id=user_id, limit=limit)


def make_connector_service(session_factory: async_sessionmaker[AsyncSession] | None = None, *, app_config: AppConfig | None = None) -> ConnectorService:
    sf = session_factory or get_session_factory()
    if sf is None:
        raise ConnectorValidationError("Connector persistence is not available when database.backend=memory")
    return ConnectorService(ConnectorRepository(sf), app_config=app_config)
