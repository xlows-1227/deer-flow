import pytest

from deerflow.connectors.errors import ConnectorAuthorizationError
from deerflow.connectors.policy import authorize_connector_action, merge_connector_policies, merge_database_policies
from deerflow.connectors.schemas import ConnectorGrant, ConnectorRuntimeContext


def test_merge_database_policies_takes_stricter_values():
    policy = merge_database_policies(
        {"allowed_schemas": ["mart", "public"], "blocked_tables": ["secret"], "max_rows": 10000},
        {"allowed_schemas": ["mart"], "blocked_tables": ["cards"], "max_rows": 100},
    )

    assert policy.allowed_schemas == ["mart"]
    assert policy.blocked_tables == ["cards", "secret"]
    assert policy.max_rows == 100
    assert policy.allow_write is False


def test_merge_connector_policies_keeps_non_database_keys():
    policy = merge_connector_policies(
        {"allowed_spaces": ["kb", "ops"], "export_enabled": False, "max_documents_per_query": 20},
        {"allowed_spaces": ["kb"], "export_enabled": True, "max_documents_per_query": 5},
    )

    assert policy["allowed_spaces"] == ["kb"]
    assert policy["export_enabled"] is False
    assert policy["max_documents_per_query"] == 5


def test_authorize_owner_without_grant():
    decision = authorize_connector_action(
        connector_policy={},
        grants=[],
        context=ConnectorRuntimeContext(user_id="u1"),
        capability="database.query",
        owner_id="u1",
    )

    assert decision.allow is True
    assert decision.reason == "connector owner matched"
    assert isinstance(decision.effective_policy, dict)


def test_authorize_non_database_policy_does_not_require_database_shape():
    decision = authorize_connector_action(
        connector_policy={"allowed_spaces": ["product_kb"], "max_documents_per_query": 20},
        grants=[ConnectorGrant(id="g1", connector_id="c1", subject_type="skill", subject_id="doc-reader", capabilities=["document.read"])],
        context=ConnectorRuntimeContext(skill_name="doc-reader"),
        capability="document.read",
    )

    assert decision.effective_policy["allowed_spaces"] == ["product_kb"]
    assert decision.effective_policy["max_documents_per_query"] == 20


def test_authorize_denies_without_matching_grant():
    with pytest.raises(ConnectorAuthorizationError):
        authorize_connector_action(
            connector_policy={},
            grants=[ConnectorGrant(id="g1", connector_id="c1", subject_type="skill", subject_id="analysis", capabilities=["database.query"])],
            context=ConnectorRuntimeContext(skill_name="other"),
            capability="database.query",
        )
