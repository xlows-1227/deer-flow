import pytest

from deerflow.connectors.errors import ConnectorSqlSafetyError
from deerflow.connectors.schemas import DatabasePolicy
from deerflow.connectors.sql_safety import validate_read_only_sql


def test_read_only_sql_adds_limit_and_extracts_tables():
    result = validate_read_only_sql(
        "select * from orders.fact_orders",
        policy=DatabasePolicy(allowed_schemas=["orders"], max_rows=50),
        dialect="mysql",
    )

    assert result.sql.endswith("LIMIT 50")
    assert result.tables == ["orders.fact_orders"]
    assert result.sql_hash.startswith("sha256:")


@pytest.mark.parametrize("sql", ["delete from t", "drop table t", "show tables", "select * from a; select * from b"])
def test_read_only_sql_rejects_unsafe_sql(sql):
    with pytest.raises(ConnectorSqlSafetyError):
        validate_read_only_sql(sql, policy=DatabasePolicy(), dialect="starrocks")


def test_read_only_sql_rejects_blocked_table():
    with pytest.raises(ConnectorSqlSafetyError):
        validate_read_only_sql(
            "select * from mart.payment_cards limit 10",
            policy=DatabasePolicy(allowed_schemas=["mart"], blocked_tables=["payment_cards"]),
            dialect="mysql",
        )
