import logging

from fastapi import APIRouter, HTTPException

from deerflow.extensions_user.image_service import UserImagePersistenceError, make_user_image_service
from deerflow.extensions_user.schemas import ImageConfigResponse, ImageConfigUpdateRequest
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["tools"])


def _user_id() -> str:
    return get_effective_user_id()


@router.get(
    "/tools/image-generation/config",
    response_model=ImageConfigResponse,
    summary="Get image generation tool configuration",
)
async def get_image_generation_configuration() -> ImageConfigResponse:
    user_id = _user_id()
    try:
        return await make_user_image_service().get_config_view(user_id)
    except UserImagePersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put(
    "/tools/image-generation/config",
    response_model=ImageConfigResponse,
    summary="Update image generation tool configuration",
)
async def update_image_generation_configuration(
    request: ImageConfigUpdateRequest,
) -> ImageConfigResponse:
    user_id = _user_id()
    try:
        return await make_user_image_service().update_config(user_id, request)
    except UserImagePersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
