import pytest
from pydantic import ValidationError

from app.gateway.external.errors import ExternalAPIError
from app.gateway.external.models import ExternalConversationCreateRequest, ExternalRunCreateRequest
from app.gateway.external.status import to_external_run_status


def test_conversation_request_accepts_external_mapping_and_default_skill():
    request = ExternalConversationCreateRequest(
        source="crm",
        external_conversation_id="customer-1",
        default_skill="customer-summary",
    )
    assert request.source == "crm"
    assert request.default_skill == "customer-summary"


@pytest.mark.parametrize("field", ["user_id", "thread_id", "config", "context", "connector_ids"])
def test_conversation_request_rejects_internal_fields(field):
    with pytest.raises(ValidationError):
        ExternalConversationCreateRequest(**{field: "forbidden"})


@pytest.mark.parametrize("field", ["user_id", "thread_id", "config", "context", "connector_ids", "agent"])
def test_run_request_rejects_internal_fields(field):
    with pytest.raises(ValidationError):
        ExternalRunCreateRequest(message="hello", **{field: "forbidden"})


def test_run_request_rejects_invalid_skill_and_oversized_metadata():
    with pytest.raises(ValidationError):
        ExternalRunCreateRequest(message="hello", skill="../unsafe")
    with pytest.raises(ValidationError):
        ExternalRunCreateRequest(message="hello", metadata={"large": "x" * (33 * 1024)})
    with pytest.raises(ValidationError):
        ExternalRunCreateRequest(message="hello", metadata={"not_json": float("nan")})


@pytest.mark.parametrize(
    ("internal", "external"),
    [
        ("pending", "pending"),
        ("running", "running"),
        ("success", "completed"),
        ("interrupted", "cancelled"),
        ("error", "failed"),
        ("timeout", "failed"),
    ],
)
def test_status_mapping(internal, external):
    assert to_external_run_status(internal) == external


def test_external_error_serialization():
    error = ExternalAPIError(code="skill_not_available", message="The requested skill is not available.", status_code=404)
    assert error.to_response(request_id="req_x") == {
        "error": {
            "code": "skill_not_available",
            "message": "The requested skill is not available.",
            "request_id": "req_x",
        }
    }
