from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from deerflow.connectors.errors import ConnectorError
from deerflow.connectors.schemas import ConnectorRuntimeContext
from deerflow.connectors.service import make_connector_service

router = APIRouter(prefix="/api", tags=["connectors"])


class ConnectorCreateRequest(BaseModel):
    name: str
    display_name: str | None = None
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    credential: dict[str, Any]
    default_policy: dict[str, Any] = Field(default_factory=dict)


class ConnectorUpdateRequest(BaseModel):
    name: str | None = None
    display_name: str | None = None
    config: dict[str, Any] | None = None
    credential: dict[str, Any] | None = None
    default_policy: dict[str, Any] | None = None


class ConnectorConfigTestRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)
    credential: dict[str, Any] | None = None
    default_policy: dict[str, Any] = Field(default_factory=dict)


class ConnectorGrantRequest(BaseModel):
    subject_type: str
    subject_id: str
    capabilities: list[str]
    policy_override: dict[str, Any] = Field(default_factory=dict)


class ConnectorQueryRequest(BaseModel):
    sql: str
    reason: str = ""


class ConnectorSampleRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_name: str = Field(alias="schema")
    table: str
    limit: int = Field(default=20, ge=1, le=100)


class ConnectorActionRequest(BaseModel):
    capability: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


def _user_id(request: Request) -> str | None:
    user = getattr(request.state, "user", None)
    return str(user.id) if user is not None and getattr(user, "id", None) is not None else None


def _context(request: Request) -> ConnectorRuntimeContext:
    return ConnectorRuntimeContext(
        user_id=_user_id(request),
        thread_id=getattr(request.state, "thread_id", None),
        run_id=getattr(request.state, "run_id", None),
    )


def _raise_http(exc: ConnectorError) -> None:
    raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message, "recoverable": exc.recoverable})


def _safe_connector_response(connector: Any) -> dict[str, Any]:
    data = connector.model_dump() if hasattr(connector, "model_dump") else dict(connector)
    credential = data.get("credential")
    if credential and isinstance(credential, dict):
        # Expose the username (it is the database account name, not a secret)
        # so the edit form can show who this connector connects as, and a
        # boolean flag indicating an inline password is set — but never the
        # password itself.
        provider = credential.get("provider")
        safe = {"provider": provider, "ref": credential.get("ref")}
        if provider == "inline":
            safe["username"] = credential.get("username")
            safe["has_password"] = bool(credential.get("ref"))
        data["credential"] = safe
    return data


@router.get("/connector-types")
async def list_connector_types():
    try:
        return {"connector_types": await make_connector_service().list_connector_types()}
    except ConnectorError as exc:
        _raise_http(exc)


@router.get("/connector-types/{type_name}")
async def get_connector_type(type_name: str):
    try:
        return await make_connector_service().get_connector_type(type_name)
    except ConnectorError as exc:
        _raise_http(exc)


@router.post("/connector-types/{type_name}/test")
async def test_connector_type_config(request: Request, type_name: str, payload: ConnectorConfigTestRequest):
    try:
        if payload.credential is None:
            raise HTTPException(status_code=400, detail={"code": "connector.validation", "message": "Connector credential is required", "recoverable": True})
        return (
            await make_connector_service().test_connector_config(
                type_name=type_name,
                config=payload.config,
                credential=payload.credential,
                default_policy=payload.default_policy,
                context=_context(request),
            )
        ).model_dump()
    except ConnectorError as exc:
        _raise_http(exc)


@router.post("/connectors", status_code=201)
async def create_connector(request: Request, payload: ConnectorCreateRequest):
    try:
        connector = await make_connector_service().create_connector(payload.model_dump(), owner_id=_user_id(request))
        return _safe_connector_response(connector)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConnectorError as exc:
        _raise_http(exc)


@router.get("/connectors")
async def list_connectors(request: Request):
    try:
        connectors = await make_connector_service().list_connectors(owner_id=_user_id(request))
        return {"connectors": [_safe_connector_response(item) for item in connectors]}
    except ConnectorError as exc:
        _raise_http(exc)


@router.get("/connectors/{connector_id}")
async def get_connector(request: Request, connector_id: str):
    try:
        connector = await make_connector_service().get_connector(connector_id, owner_id=_user_id(request))
        return _safe_connector_response(connector)
    except ConnectorError as exc:
        _raise_http(exc)


@router.patch("/connectors/{connector_id}")
async def update_connector(request: Request, connector_id: str, payload: ConnectorUpdateRequest):
    try:
        values = {key: value for key, value in payload.model_dump().items() if value is not None}
        connector = await make_connector_service().update_connector(connector_id, values, owner_id=_user_id(request))
        return _safe_connector_response(connector)
    except ConnectorError as exc:
        _raise_http(exc)
    except ValueError as exc:
        # Defensive: turn malformed payloads (e.g. a credential update that
        # forgot the encrypted ref) into a 422 instead of letting FastAPI
        # return a bare 500.
        raise HTTPException(status_code=422, detail={"code": "connector.validation", "message": str(exc), "recoverable": True}) from exc


@router.delete("/connectors/{connector_id}", status_code=204)
async def delete_connector(request: Request, connector_id: str):
    try:
        await make_connector_service().delete_connector(connector_id, owner_id=_user_id(request))
    except ConnectorError as exc:
        _raise_http(exc)


@router.post("/connectors/{connector_id}/disable")
async def disable_connector(request: Request, connector_id: str):
    try:
        connector = await make_connector_service().update_connector(connector_id, {"status": "disabled"}, owner_id=_user_id(request))
        return _safe_connector_response(connector)
    except ConnectorError as exc:
        _raise_http(exc)


@router.post("/connectors/{connector_id}/enable")
async def enable_connector(request: Request, connector_id: str):
    try:
        connector = await make_connector_service().update_connector(connector_id, {"status": "active"}, owner_id=_user_id(request))
        return _safe_connector_response(connector)
    except ConnectorError as exc:
        _raise_http(exc)


@router.post("/connectors/{connector_id}/test")
async def test_connector(request: Request, connector_id: str):
    try:
        return (await make_connector_service().test_connector(connector_id, context=_context(request))).model_dump()
    except ConnectorError as exc:
        _raise_http(exc)


@router.post("/connectors/{connector_id}/test-config")
async def test_existing_connector_config(request: Request, connector_id: str, payload: ConnectorConfigTestRequest):
    try:
        values = payload.model_dump(exclude_none=True)
        return (
            await make_connector_service().test_connector_config_for_instance(
                connector_id,
                values=values,
                context=_context(request),
                owner_id=_user_id(request),
            )
        ).model_dump()
    except ConnectorError as exc:
        _raise_http(exc)


@router.post("/connectors/{connector_id}/introspect")
async def introspect_connector(request: Request, connector_id: str):
    try:
        return (await make_connector_service().introspect_connector(connector_id, context=_context(request))).model_dump()
    except ConnectorError as exc:
        _raise_http(exc)


@router.get("/connectors/{connector_id}/schema")
@router.get("/connectors/{connector_id}/resources")
async def get_connector_schema(request: Request, connector_id: str):
    try:
        cached = await make_connector_service().get_cached_schema(connector_id, owner_id=_user_id(request))
        return cached["metadata_json"] if cached else {"schemas": [], "tables": [], "cached_at": None}
    except ConnectorError as exc:
        _raise_http(exc)


@router.post("/connectors/{connector_id}/query")
async def query_connector(request: Request, connector_id: str, payload: ConnectorQueryRequest):
    try:
        return (await make_connector_service().query_database(connector_id, payload.sql, reason=payload.reason, context=_context(request))).model_dump()
    except ConnectorError as exc:
        _raise_http(exc)


@router.post("/connectors/{connector_id}/sample")
async def sample_connector_table(request: Request, connector_id: str, payload: ConnectorSampleRequest):
    try:
        return (await make_connector_service().sample_database_table(connector_id, schema=payload.schema_name, table=payload.table, limit=payload.limit, context=_context(request))).model_dump()
    except ConnectorError as exc:
        _raise_http(exc)


@router.post("/connectors/{connector_id}/actions")
async def call_connector_action(request: Request, connector_id: str, payload: ConnectorActionRequest):
    try:
        result = await make_connector_service().execute_connector_action(
            connector_id,
            capability=payload.capability,
            args=payload.args,
            reason=payload.reason,
            context=_context(request),
        )
        return result.model_dump() if hasattr(result, "model_dump") else result
    except ConnectorError as exc:
        _raise_http(exc)


@router.post("/connectors/{connector_id}/grants", status_code=201)
async def create_connector_grant(request: Request, connector_id: str, payload: ConnectorGrantRequest):
    try:
        return (await make_connector_service().create_grant(connector_id, payload.model_dump(), created_by=_user_id(request), owner_id=_user_id(request))).model_dump()
    except ConnectorError as exc:
        _raise_http(exc)


@router.get("/connectors/{connector_id}/grants")
async def list_connector_grants(request: Request, connector_id: str):
    try:
        return {"grants": [grant.model_dump() for grant in await make_connector_service().list_grants(connector_id, owner_id=_user_id(request))]}
    except ConnectorError as exc:
        _raise_http(exc)


@router.delete("/connectors/{connector_id}/grants/{grant_id}", status_code=204)
async def delete_connector_grant(request: Request, connector_id: str, grant_id: str):
    try:
        await make_connector_service().delete_grant(connector_id, grant_id, owner_id=_user_id(request))
    except ConnectorError as exc:
        _raise_http(exc)


@router.patch("/connectors/{connector_id}/grants/{grant_id}")
async def update_connector_grant(request: Request, connector_id: str, grant_id: str, payload: ConnectorGrantRequest):
    try:
        return (await make_connector_service().update_grant(connector_id, grant_id, payload.model_dump(), owner_id=_user_id(request))).model_dump()
    except ConnectorError as exc:
        _raise_http(exc)


@router.get("/connectors/{connector_id}/audit")
async def list_connector_audit(request: Request, connector_id: str):
    try:
        return {"audit": await make_connector_service().list_audit(connector_id=connector_id, owner_id=_user_id(request))}
    except ConnectorError as exc:
        _raise_http(exc)


@router.get("/connector-audit")
async def list_all_connector_audit(request: Request):
    try:
        return {"audit": await make_connector_service().list_audit(user_id=_user_id(request))}
    except ConnectorError as exc:
        _raise_http(exc)
