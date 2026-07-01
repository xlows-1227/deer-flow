from __future__ import annotations

from fastapi import APIRouter, HTTPException

from deerflow.runtime.user_context import get_effective_user_id
from deerflow.user_models.schemas import UserModelCreateRequest, UserModelListResponse, UserModelRecord, UserModelUpdateRequest
from deerflow.user_models.service import (
    UserModelNotFoundError,
    UserModelPersistenceError,
    UserModelValidationError,
    make_user_model_service,
)

router = APIRouter(prefix="/api/models/custom", tags=["models"])


class UserModelCreateBody(UserModelCreateRequest):
    pass


class UserModelUpdateBody(UserModelUpdateRequest):
    pass


@router.get("", response_model=UserModelListResponse, summary="List custom models for the current user")
async def list_custom_models() -> UserModelListResponse:
    user_id = get_effective_user_id()
    if user_id == "default":
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        models = await make_user_model_service().list_models(user_id)
    except UserModelPersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return UserModelListResponse(models=models)


@router.post("", response_model=UserModelRecord, status_code=201, summary="Create a custom model")
async def create_custom_model(payload: UserModelCreateBody) -> UserModelRecord:
    user_id = get_effective_user_id()
    if user_id == "default":
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        return await make_user_model_service().create_model(user_id, payload)
    except UserModelValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UserModelPersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/{model_id}", response_model=UserModelRecord, summary="Update a custom model")
async def update_custom_model(model_id: str, payload: UserModelUpdateBody) -> UserModelRecord:
    user_id = get_effective_user_id()
    if user_id == "default":
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        return await make_user_model_service().update_model(user_id, model_id, payload)
    except UserModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UserModelValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UserModelPersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/{model_id}", status_code=204, summary="Delete a custom model")
async def delete_custom_model(model_id: str) -> None:
    user_id = get_effective_user_id()
    if user_id == "default":
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        await make_user_model_service().delete_model(user_id, model_id)
    except UserModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UserModelPersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
