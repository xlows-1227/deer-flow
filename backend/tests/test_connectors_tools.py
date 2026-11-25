import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deerflow.connectors.errors import ConnectorAuthorizationError
from deerflow.connectors.schemas import QueryColumn, QueryResult
from deerflow.connectors.tools import (
    _context,
    _ensure_selected,
    call_connector_action_tool,
    call_onedata_api_tool,
    get_onedata_api_params_tool,
    query_database_tool,
)
from deerflow.tools.tools import get_available_tools


def _config(connectors_enabled: bool):
    return SimpleNamespace(
        tools=[],
        models=[],
        tool_search=SimpleNamespace(enabled=False),
        skill_evolution=SimpleNamespace(enabled=False),
        sandbox=MagicMock(),
        acp_agents={},
        connectors=SimpleNamespace(enabled=connectors_enabled),
    )


@patch("deerflow.tools.tools.is_host_bash_allowed", return_value=True)
def test_connector_tools_hidden_when_disabled(_mock_bash):
    tools = get_available_tools(include_mcp=False, app_config=_config(False))

    assert "list_connectors" not in {tool.name for tool in tools}


@patch("deerflow.tools.tools.is_host_bash_allowed", return_value=True)
def test_connector_tools_loaded_when_enabled(_mock_bash):
    tools = get_available_tools(include_mcp=False, app_config=_config(True))

    names = {tool.name for tool in tools}
    assert {
        "list_connectors",
        "inspect_connector",
        "query_database",
        "sample_database_table",
        "call_connector_action",
        "get_onedata_api_params",
        "call_onedata_api",
    } <= names


def test_call_connector_action_schema_avoids_reserved_args_name():
    """LangChain remaps a parameter named ``args`` to ``v__args`` and breaks invocation."""
    schema = call_connector_action_tool.args
    assert "args" not in schema
    assert "v__args" not in schema
    assert "action_args" in schema
    action_args = schema["action_args"]
    # object | null is encoded as anyOf in the tool schema
    assert action_args.get("type") == "object" or any(option.get("type") == "object" for option in action_args.get("anyOf", []))


def test_onedata_tools_expose_typed_api_id_and_param_data():
    get_params_schema = get_onedata_api_params_tool.args
    call_schema = call_onedata_api_tool.args

    assert get_params_schema["api_id"]["type"] == "integer"
    assert call_schema["api_id"]["type"] == "integer"
    assert "param_data" in call_schema
    assert "action_args" not in get_params_schema


@pytest.mark.asyncio
async def test_get_onedata_api_params_maps_typed_api_id_to_connector_action():
    service = MagicMock()
    service.execute_connector_action = AsyncMock(return_value={"apiId": 639, "requestParam": []})
    runtime = SimpleNamespace(context={"connector_ids": ["conn_1"]}, config={})

    with patch("deerflow.connectors.tools.make_connector_service", return_value=service):
        result = await get_onedata_api_params_tool.coroutine(runtime, "conn_1", 639)

    assert result == {"apiId": 639, "requestParam": []}
    assert service.execute_connector_action.await_args.kwargs["args"] == {"apiId": 639}


@pytest.mark.asyncio
async def test_call_onedata_api_maps_typed_question_parameters_to_connector_action():
    service = MagicMock()
    service.execute_connector_action = AsyncMock(return_value={"status": 200, "data": {"result": []}})
    runtime = SimpleNamespace(context={"connector_ids": ["conn_1"]}, config={})

    with patch("deerflow.connectors.tools.make_connector_service", return_value=service):
        result = await call_onedata_api_tool.coroutine(
            runtime,
            "conn_1",
            639,
            {"startDate": "2026-08-01", "endDate": "2026-08-04", "store_code": "KFC001"},
            "查询 PH 口袋销量",
            20,
            1,
            "store_code asc",
            None,
            True,
        )

    assert result["status"] == 200
    call = service.execute_connector_action.await_args.kwargs
    assert call["capability"] == "onedata.call_api"
    assert call["reason"] == "查询 PH 口袋销量"
    assert call["args"] == {
        "apiId": 639,
        "paramData": {"startDate": "2026-08-01", "endDate": "2026-08-04", "store_code": "KFC001"},
        "pageSize": 20,
        "pageNum": 1,
        "orderBy": "store_code asc",
        "hasTotal": True,
    }


def test_connector_tool_context_reads_selected_connector_ids():
    context = _context(SimpleNamespace(context={"connector_ids": ["conn_1", "conn_2"]}, config={}))

    assert context.connector_ids == ["conn_1", "conn_2"]


def test_connector_tool_rejects_unselected_connector_id():
    context = _context(SimpleNamespace(context={"connector_ids": ["conn_1"]}, config={}))

    _ensure_selected(context, "conn_1")
    with pytest.raises(ConnectorAuthorizationError):
        _ensure_selected(context, "conn_2")


class _FakePaths:
    def __init__(self, base):
        self._base = base

    def sandbox_work_dir(self, thread_id, user_id=None):
        return self._base / thread_id


def _budget_config(inline_max_chars=1000, preview_rows=2, read_file_max_bytes=5 * 1024 * 1024, retention_hours=24):
    return SimpleNamespace(
        connectors=SimpleNamespace(
            result_inline_max_chars=inline_max_chars,
            result_preview_rows=preview_rows,
            result_spill_retention_hours=retention_hours,
        ),
        sandbox=SimpleNamespace(read_file_max_bytes=read_file_max_bytes),
    )


def _runtime(thread_id="thread_1"):
    return SimpleNamespace(
        context={"connector_ids": ["conn_1"], "thread_id": thread_id, "run_id": "run_1"},
        config={},
    )


def _big_result(truncated=False):
    rows = [[f"row-{index:04d}", "x" * 50] for index in range(100)]
    return QueryResult(
        columns=[QueryColumn(name="id"), QueryColumn(name="value")],
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
    )


@pytest.mark.asyncio
async def test_small_query_result_is_returned_inline_unchanged(tmp_path):
    service = MagicMock()
    service.query_database = AsyncMock(
        return_value=QueryResult(columns=[QueryColumn(name="id")], rows=[[1], [2]], row_count=2)
    )
    service.record_result_spill = AsyncMock()

    with (
        patch("deerflow.connectors.tools.make_connector_service", return_value=service),
        patch("deerflow.connectors.result_spill.get_app_config", return_value=_budget_config(10_000)),
        patch("deerflow.connectors.result_spill.get_paths", return_value=_FakePaths(tmp_path)),
    ):
        payload = await query_database_tool.coroutine(_runtime(), "conn_1", "SELECT id FROM t", "test")

    assert payload == {"columns": [{"name": "id", "type": None}], "rows": [[1], [2]], "row_count": 2, "truncated": False, "elapsed_ms": None}
    assert service.record_result_spill.await_count == 0
    assert not (tmp_path / "thread_1").exists()


@pytest.mark.asyncio
async def test_large_query_result_is_spilled_to_ndjson_and_audited(tmp_path):
    service = MagicMock()
    service.query_database = AsyncMock(return_value=_big_result())
    service.record_result_spill = AsyncMock()

    with (
        patch("deerflow.connectors.tools.make_connector_service", return_value=service),
        patch("deerflow.connectors.result_spill.get_app_config", return_value=_budget_config()),
        patch("deerflow.connectors.result_spill.get_paths", return_value=_FakePaths(tmp_path)),
    ):
        summary = await query_database_tool.coroutine(_runtime(), "conn_1", "SELECT id, value FROM t", "test")

    assert summary["spilled"] is True
    assert summary["file"].startswith("/mnt/user-data/workspace/connector_results/")
    assert summary["file"].endswith(".ndjson")
    assert summary["format"] == "ndjson"
    assert summary["total"] == 100
    assert summary["returned"] == 100
    assert summary["truncated"] is False
    assert summary["data_complete"] is True
    assert len(summary["preview"]) == 2
    assert "read_file" in summary["note"]

    host_file = tmp_path / "thread_1" / "connector_results" / summary["file"].rsplit("/", 1)[-1]
    host_meta = tmp_path / "thread_1" / "connector_results" / summary["meta_file"].rsplit("/", 1)[-1]
    assert len(host_file.read_text(encoding="utf-8").splitlines()) == 100
    meta = json.loads(host_meta.read_text(encoding="utf-8"))
    assert meta["sql"] == "SELECT id, value FROM t"
    assert meta["tables"] == ["t"]
    assert meta["returned"] == 100

    spill_audit = service.record_result_spill.await_args
    assert spill_audit.kwargs["operation"] == "query"
    assert spill_audit.kwargs["result_summary"]["returned"] == 100


@pytest.mark.asyncio
async def test_spill_note_skips_read_file_above_read_limit(tmp_path):
    """The note must not suggest read_file when the guard would reject the file."""
    service = MagicMock()
    service.query_database = AsyncMock(return_value=_big_result())
    service.record_result_spill = AsyncMock()

    with (
        patch("deerflow.connectors.tools.make_connector_service", return_value=service),
        patch(
            "deerflow.connectors.result_spill.get_app_config",
            return_value=_budget_config(read_file_max_bytes=10),
        ),
        patch("deerflow.connectors.result_spill.get_paths", return_value=_FakePaths(tmp_path)),
    ):
        summary = await query_database_tool.coroutine(_runtime(), "conn_1", "SELECT id, value FROM t", "test")

    assert summary["spilled"] is True
    assert "bash" in summary["note"]
    assert "start_line" not in summary["note"]
    assert "read_file limit" in summary["note"]


@pytest.mark.asyncio
async def test_expired_spill_files_are_pruned_before_writing(tmp_path):
    import os
    import time as clock

    service = MagicMock()
    service.query_database = AsyncMock(return_value=_big_result())
    service.record_result_spill = AsyncMock()

    spill_dir = tmp_path / "thread_1" / "connector_results"
    spill_dir.mkdir(parents=True)
    expired_data = spill_dir / "expired.ndjson"
    expired_meta = spill_dir / "expired.meta.json"
    fresh_data = spill_dir / "fresh.ndjson"
    for path in (expired_data, expired_meta, fresh_data):
        path.write_text("{}\n", encoding="utf-8")
    old = clock.time() - 25 * 3600
    for path in (expired_data, expired_meta):
        os.utime(path, (old, old))

    with (
        patch("deerflow.connectors.tools.make_connector_service", return_value=service),
        patch("deerflow.connectors.result_spill.get_app_config", return_value=_budget_config()),
        patch("deerflow.connectors.result_spill.get_paths", return_value=_FakePaths(tmp_path)),
    ):
        summary = await query_database_tool.coroutine(_runtime(), "conn_1", "SELECT id, value FROM t", "test")

    assert summary["spilled"] is True
    assert not expired_data.exists()
    assert not expired_meta.exists()
    assert fresh_data.exists()


@pytest.mark.asyncio
async def test_zero_retention_disables_spill_pruning(tmp_path):
    import os
    import time as clock

    service = MagicMock()
    service.query_database = AsyncMock(return_value=_big_result())
    service.record_result_spill = AsyncMock()

    spill_dir = tmp_path / "thread_1" / "connector_results"
    spill_dir.mkdir(parents=True)
    expired_data = spill_dir / "expired.ndjson"
    expired_data.write_text("{}\n", encoding="utf-8")
    old = clock.time() - 25 * 3600
    os.utime(expired_data, (old, old))

    with (
        patch("deerflow.connectors.tools.make_connector_service", return_value=service),
        patch(
            "deerflow.connectors.result_spill.get_app_config",
            return_value=_budget_config(retention_hours=0),
        ),
        patch("deerflow.connectors.result_spill.get_paths", return_value=_FakePaths(tmp_path)),
    ):
        summary = await query_database_tool.coroutine(_runtime(), "conn_1", "SELECT id, value FROM t", "test")

    assert summary["spilled"] is True
    assert expired_data.exists()


@pytest.mark.asyncio
async def test_truncated_large_result_is_marked_incomplete(tmp_path):
    service = MagicMock()
    service.query_database = AsyncMock(return_value=_big_result(truncated=True))
    service.record_result_spill = AsyncMock()

    with (
        patch("deerflow.connectors.tools.make_connector_service", return_value=service),
        patch("deerflow.connectors.result_spill.get_app_config", return_value=_budget_config()),
        patch("deerflow.connectors.result_spill.get_paths", return_value=_FakePaths(tmp_path)),
    ):
        summary = await query_database_tool.coroutine(_runtime(), "conn_1", "SELECT * FROM t", "test")

    assert summary["spilled"] is True
    assert summary["truncated"] is True
    assert summary["total"] is None
    assert summary["returned"] == 100
    assert summary["data_complete"] is False
    assert "incomplete" in summary["warning"]
    assert len(summary["next_steps"]) == 3


@pytest.mark.asyncio
async def test_large_result_without_thread_is_returned_inline(caplog):
    service = MagicMock()
    service.query_database = AsyncMock(return_value=_big_result())
    service.record_result_spill = AsyncMock()

    with (
        patch("deerflow.connectors.tools.make_connector_service", return_value=service),
        patch("deerflow.connectors.result_spill.get_app_config", return_value=_budget_config()),
        caplog.at_level("WARNING"),
    ):
        payload = await query_database_tool.coroutine(_runtime(thread_id=None), "conn_1", "SELECT * FROM t", "test")

    assert payload["row_count"] == 100
    assert "no thread workspace is available" in caplog.text
    assert service.record_result_spill.await_count == 0
