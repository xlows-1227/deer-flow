"""Typed errors exposed by the External API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ExternalErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: dict[str, Any] | None = None


class ExternalErrorResponse(BaseModel):
    error: ExternalErrorDetail


class ExternalAPIError(Exception):
    """Domain error that can be rendered without exposing internal details."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details

    def to_response(self, *, request_id: str | None = None) -> dict[str, Any]:
        return ExternalErrorResponse(
            error=ExternalErrorDetail(
                code=self.code,
                message=self.message,
                request_id=request_id,
                details=self.details,
            )
        ).model_dump(exclude_none=True)
