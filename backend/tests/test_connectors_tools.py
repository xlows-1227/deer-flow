from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from deerflow.connectors.errors import ConnectorAuthorizationError
from deerflow.connectors.tools import _context, _ensure_selected
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
    assert {"list_connectors", "inspect_connector", "query_database", "sample_database_table", "call_connector_action"} <= names


def test_connector_tool_context_reads_selected_connector_ids():
    context = _context(SimpleNamespace(context={"connector_ids": ["conn_1", "conn_2"]}, config={}))

    assert context.connector_ids == ["conn_1", "conn_2"]


def test_connector_tool_rejects_unselected_connector_id():
    context = _context(SimpleNamespace(context={"connector_ids": ["conn_1"]}, config={}))

    _ensure_selected(context, "conn_1")
    with pytest.raises(ConnectorAuthorizationError):
        _ensure_selected(context, "conn_2")
