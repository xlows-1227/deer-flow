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


def test_read_only_sql_strips_line_and_block_comments():
    result = validate_read_only_sql(
        """
        -- monthly portal metrics
        SELECT
          visit_cnt, /* total */
          self_solve_rate
        FROM dws_suwen_portal_total_num_count
        WHERE dt BETWEEN '2026-06-01' AND '2026-06-30' -- june
        LIMIT 10
        """,
        policy=DatabasePolicy(require_limit=False),
        dialect="mysql",
    )

    assert "--" not in result.sql
    assert "/*" not in result.sql
    assert "*/" not in result.sql
    assert "monthly portal metrics" not in result.sql
    assert "SELECT visit_cnt, self_solve_rate FROM dws_suwen_portal_total_num_count" in result.sql
    assert "WHERE dt BETWEEN '2026-06-01' AND '2026-06-30' LIMIT 10" in result.sql


def test_read_only_sql_preserves_comment_like_text_inside_string_literals():
    result = validate_read_only_sql(
        "SELECT * FROM notes WHERE body = 'has -- dashes /* and block */ #hash' LIMIT 5",
        policy=DatabasePolicy(require_limit=False),
        dialect="mysql",
    )

    assert result.sql == "SELECT * FROM notes WHERE body = 'has -- dashes /* and block */ #hash' LIMIT 5"


def test_read_only_sql_strips_hash_comments():
    result = validate_read_only_sql(
        "SELECT id FROM orders # trailing comment\nWHERE status = 'open' LIMIT 5",
        policy=DatabasePolicy(require_limit=False),
        dialect="mysql",
    )

    assert "#" not in result.sql
    assert "trailing comment" not in result.sql
    assert result.sql == "SELECT id FROM orders WHERE status = 'open' LIMIT 5"
