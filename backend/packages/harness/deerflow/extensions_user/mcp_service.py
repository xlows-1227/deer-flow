from __future__ import annotations

from typing import Any

from deerflow.config.effective_config import invalidate_user_extensions_cache
from deerflow.config.extensions_config import McpOAuthConfig, McpServerConfig
from deerflow.extensions_user.schemas import (
    MASKED_VALUE,
    McpConfigResponse,
    McpServerCreateRequest,
    McpServerEnabledRequest,
    McpServerRecord,
    McpServerUpdateRequest,
)
from deerflow.extensions_user.secrets import ExtensionSecretStore
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.user_extension import UserMcpServerRepository, UserMcpServerStateRepository


class UserMcpValidationError(ValueError):
    pass


class UserMcpNotFoundError(LookupError):
    pass


class UserMcpPersistenceError(RuntimeError):
    pass


def _mask_dict(values: dict[str, str]) -> dict[str, str]:
    return {k: MASKED_VALUE for k in values}


def _mask_oauth(oauth: McpOAuthConfig | None) -> McpOAuthConfig | None:
    if oauth is None:
        return None
    return oauth.model_copy(update={"client_secret": None, "refresh_token": None})


def _secrets_payload(env: dict[str, str], headers: dict[str, str], oauth: McpOAuthConfig | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"env": env, "headers": headers}
    if oauth is not None:
        payload["oauth"] = oauth.model_dump()
    return payload


def _row_to_mcp_server_config(row: dict[str, Any], *, secret_store: ExtensionSecretStore) -> McpServerConfig:
    env: dict[str, str] = {}
    headers: dict[str, str] = {}
    oauth: McpOAuthConfig | None = None
    secrets_ref = row.get("secrets_ref")
    if secrets_ref:
        secrets = secret_store.decrypt_json(secrets_ref)
        env = dict(secrets.get("env") or {})
        headers = dict(secrets.get("headers") or {})
        oauth_data = secrets.get("oauth")
        if isinstance(oauth_data, dict) and oauth_data:
            oauth = McpOAuthConfig(**oauth_data)

    return McpServerConfig(
        enabled=bool(row.get("enabled", True)),
        type=str(row.get("type") or "stdio"),
        command=row.get("command"),
        args=list(row.get("args") or []),
        env=env,
        url=row.get("url"),
        headers=headers,
        oauth=oauth,
        description=str(row.get("description") or ""),
    )


def _system_server_record(
    name: str,
    server: McpServerConfig,
    *,
    enabled: bool,
) -> McpServerRecord:
    return McpServerRecord(
        name=name,
        enabled=enabled,
        type=server.type,
        command=server.command,
        args=list(server.args),
        env=_mask_dict(server.env),
        url=server.url,
        headers=_mask_dict(server.headers),
        oauth=_mask_oauth(server.oauth),
        description=server.description,
        source="system",
        editable=False,
    )


def _user_server_record(row: dict[str, Any], *, secret_store: ExtensionSecretStore) -> McpServerRecord:
    env: dict[str, str] = {}
    headers: dict[str, str] = {}
    oauth = None
    secrets_ref = row.get("secrets_ref")
    if secrets_ref:
        secrets = secret_store.decrypt_json(secrets_ref)
        env = _mask_dict(dict(secrets.get("env") or {}))
        headers = _mask_dict(dict(secrets.get("headers") or {}))
        oauth_data = secrets.get("oauth")
        if isinstance(oauth_data, dict) and oauth_data:
            oauth = _mask_oauth(McpOAuthConfig(**oauth_data))

    return McpServerRecord(
        name=row["name"],
        enabled=bool(row.get("enabled", True)),
        type=str(row.get("type") or "stdio"),
        command=row.get("command"),
        args=list(row.get("args") or []),
        env=env,
        url=row.get("url"),
        headers=headers,
        oauth=oauth,
        description=str(row.get("description") or ""),
        source="user",
        editable=True,
    )


def _merge_secrets(
    incoming_env: dict[str, str] | None,
    incoming_headers: dict[str, str] | None,
    incoming_oauth: McpOAuthConfig | None,
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    existing_env = dict((existing or {}).get("env") or {})
    existing_headers = dict((existing or {}).get("headers") or {})
    existing_oauth = (existing or {}).get("oauth")

    merged_env: dict[str, str] = dict(existing_env)
    if incoming_env is not None:
        for key, value in incoming_env.items():
            if value == MASKED_VALUE:
                if key not in existing_env:
                    raise UserMcpValidationError(f"Cannot set env key '{key}' to masked value")
            else:
                merged_env[key] = value

    merged_headers: dict[str, str] = dict(existing_headers)
    if incoming_headers is not None:
        for key, value in incoming_headers.items():
            if value == MASKED_VALUE:
                if key not in existing_headers:
                    raise UserMcpValidationError(f"Cannot set header '{key}' to masked value")
            else:
                merged_headers[key] = value

    merged_oauth = existing_oauth
    if incoming_oauth is not None:
        if isinstance(existing_oauth, dict):
            merged_oauth = dict(existing_oauth)
            if incoming_oauth.client_secret is not None:
                merged_oauth["client_secret"] = incoming_oauth.client_secret or None
            if incoming_oauth.refresh_token is not None:
                merged_oauth["refresh_token"] = incoming_oauth.refresh_token or None
            for field in (
                "enabled",
                "token_url",
                "grant_type",
                "client_id",
                "scope",
                "audience",
                "token_field",
                "token_type_field",
                "expires_in_field",
                "default_token_type",
                "refresh_skew_seconds",
                "extra_token_params",
            ):
                value = getattr(incoming_oauth, field)
                if value is not None and value != "":
                    merged_oauth[field] = value
        else:
            merged_oauth = incoming_oauth.model_dump()

    return {"env": merged_env, "headers": merged_headers, "oauth": merged_oauth}


class UserMcpService:
    def __init__(
        self,
        server_repo: UserMcpServerRepository,
        state_repo: UserMcpServerStateRepository,
        *,
        secret_store: ExtensionSecretStore | None = None,
    ) -> None:
        self._servers = server_repo
        self._states = state_repo
        self._secrets = secret_store or ExtensionSecretStore()

    async def get_config_view(self, user_id: str, system_servers: dict[str, McpServerConfig]) -> McpConfigResponse:
        state_rows = await self._states.list_for_user(user_id)
        state_by_name = {row["server_name"]: bool(row["enabled"]) for row in state_rows}
        servers: dict[str, McpServerRecord] = {}

        for name, server in system_servers.items():
            enabled = state_by_name.get(name, server.enabled)
            servers[name] = _system_server_record(name, server, enabled=enabled)

        for row in await self._servers.list_for_user(user_id):
            servers[row["name"]] = _user_server_record(row, secret_store=self._secrets)

        return McpConfigResponse(mcp_servers=servers)

    async def set_server_enabled(self, user_id: str, name: str, payload: McpServerEnabledRequest, system_servers: dict[str, McpServerConfig]) -> McpServerRecord:
        user_row = await self._servers.get_by_name(user_id, name)
        if user_row is not None:
            updated = await self._servers.update(user_id, name, {"enabled": payload.enabled})
            if updated is None:
                raise UserMcpNotFoundError(f"MCP server {name!r} not found")
            invalidate_user_extensions_cache(user_id)
            return _user_server_record(updated, secret_store=self._secrets)

        if name not in system_servers:
            raise UserMcpNotFoundError(f"MCP server {name!r} not found")
        await self._states.upsert(user_id, name, payload.enabled)
        invalidate_user_extensions_cache(user_id)
        return _system_server_record(name, system_servers[name], enabled=payload.enabled)

    async def create_server(self, user_id: str, payload: McpServerCreateRequest, system_servers: dict[str, McpServerConfig]) -> McpServerRecord:
        name = payload.name.strip()
        if not name:
            raise UserMcpValidationError("Server name is required")
        if name in system_servers:
            raise UserMcpValidationError(f"Cannot create user server with reserved system name: {name}")

        oauth = McpOAuthConfig(**payload.oauth.model_dump()) if payload.oauth is not None else None
        secrets_ref = self._secrets.encrypt_json(_secrets_payload(payload.env, payload.headers, oauth))
        try:
            row = await self._servers.create(
                {
                    "user_id": user_id,
                    "name": name,
                    "enabled": payload.enabled,
                    "type": payload.type,
                    "command": payload.command,
                    "args": payload.args,
                    "url": payload.url,
                    "description": payload.description,
                    "secrets_ref": secrets_ref,
                }
            )
        except ValueError as exc:
            raise UserMcpValidationError(str(exc)) from exc
        invalidate_user_extensions_cache(user_id)
        return _user_server_record(row, secret_store=self._secrets)

    async def update_server(self, user_id: str, name: str, payload: McpServerUpdateRequest) -> McpServerRecord:
        existing = await self._servers.get_by_name(user_id, name)
        if existing is None:
            raise UserMcpNotFoundError(f"MCP server {name!r} not found")

        values: dict[str, Any] = {}
        if payload.enabled is not None:
            values["enabled"] = payload.enabled
        if payload.type is not None:
            values["type"] = payload.type
        if payload.command is not None:
            values["command"] = payload.command
        if payload.args is not None:
            values["args"] = payload.args
        if payload.url is not None:
            values["url"] = payload.url
        if payload.description is not None:
            values["description"] = payload.description

        if any(field in payload.model_dump(exclude_unset=True) for field in ("env", "headers", "oauth")):
            existing_secrets = self._secrets.decrypt_json(existing["secrets_ref"]) if existing.get("secrets_ref") else None
            incoming_oauth = McpOAuthConfig(**payload.oauth.model_dump()) if payload.oauth is not None else None
            merged = _merge_secrets(payload.env, payload.headers, incoming_oauth, existing_secrets)
            values["secrets_ref"] = self._secrets.encrypt_json(merged)

        updated = await self._servers.update(user_id, name, values)
        if updated is None:
            raise UserMcpNotFoundError(f"MCP server {name!r} not found")
        invalidate_user_extensions_cache(user_id)
        return _user_server_record(updated, secret_store=self._secrets)

    async def delete_server(self, user_id: str, name: str) -> None:
        deleted = await self._servers.delete(user_id, name)
        if not deleted:
            raise UserMcpNotFoundError(f"MCP server {name!r} not found")
        invalidate_user_extensions_cache(user_id)

    async def build_user_mcp_servers(self, user_id: str, system_servers: dict[str, McpServerConfig]) -> dict[str, McpServerConfig]:
        state_rows = await self._states.list_for_user(user_id)
        state_by_name = {row["server_name"]: bool(row["enabled"]) for row in state_rows}
        merged: dict[str, McpServerConfig] = {}

        for name, server in system_servers.items():
            enabled = state_by_name.get(name, server.enabled)
            merged[name] = server.model_copy(update={"enabled": enabled})

        for row in await self._servers.list_for_user(user_id):
            merged[row["name"]] = _row_to_mcp_server_config(row, secret_store=self._secrets)

        return merged


def make_user_mcp_service(session_factory=None) -> UserMcpService:
    sf = session_factory or get_session_factory()
    if sf is None:
        raise UserMcpPersistenceError("User MCP persistence is not available when database.backend=memory")
    return UserMcpService(UserMcpServerRepository(sf), UserMcpServerStateRepository(sf))
