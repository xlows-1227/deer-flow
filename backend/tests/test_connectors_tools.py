from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deerflow.connectors.errors import ConnectorAuthorizationError
from deerflow.connectors.tools import (
    _context,
    _ensure_selected,
    call_connector_action_tool,
    call_onedata_api_tool,
    get_onedata_api_params_tool,
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
