from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.gateway.deps import get_api_key_repo, get_external_audit_repo, get_external_conversation_repo, get_external_idempotency_repo


@pytest.mark.parametrize(
    "getter,attr",
    [
        (get_api_key_repo, "api_key_repo"),
        (get_external_conversation_repo, "external_conversation_repo"),
        (get_external_idempotency_repo, "external_idempotency_repo"),
        (get_external_audit_repo, "external_audit_repo"),
    ],
)
def test_external_dependency_getters(getter, attr):
    repository = object()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**{attr: repository})))
    assert getter(request) is repository


@pytest.mark.parametrize(
    "getter",
    [get_api_key_repo, get_external_conversation_repo, get_external_idempotency_repo, get_external_audit_repo],
)
def test_external_dependency_getters_fail_closed(getter):
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    with pytest.raises(HTTPException) as exc:
        getter(request)
    assert exc.value.status_code == 503
    assert exc.value.detail == "External API persistence not available"
