import time

import pytest

from .conftest import read_mcp_response, read_stderr, send_mcp_request

# No need to import helper functions or fixtures directly, pytest handles conftest.py


def test_initialize(server_process):
    """
    Test the Initialize request.
    Verifies that the server responds with its name, version, and capabilities.
    """
    send_mcp_request(server_process, "initialize", request_id="init_test_1")
    response = read_mcp_response(server_process)

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == "init_test_1"
    assert "result" in response, f"Error in response: {response.get('error')}"
    assert "error" not in response

    send_mcp_request(server_process, "notifications/initialized", request_id=None)

    result = response["result"]
    assert "serverInfo" in result, "serverInfo missing from initialize response result"
    server_info = result["serverInfo"]
    assert server_info["name"] == "vector-index-mcp"
    assert "version" in server_info

    assert "capabilities" in result, (
        "capabilities missing from initialize response result"
    )
    capabilities = result["capabilities"]
    assert isinstance(capabilities, dict)
    assert "tools" in capabilities
    assert "resources" in capabilities


def test_indexing_triggered_on_startup(server_process):
    """
    Test that project indexing is automatically triggered when the MCP server starts.
    This is verified by checking for a specific log message in the server's stderr.
    """
    # The server starts via the fixture. We send initialize and initialized
    # to complete the handshake and trigger the server's startup logic.
    send_mcp_request(server_process, "initialize", request_id="init_test_1")
    read_mcp_response(server_process)
    send_mcp_request(server_process, "notifications/initialized", request_id=None)

    # Reliably wait for the target log message to appear in stderr.
    max_wait_time = 30  # seconds
    start_time = time.time()
    stderr_output = ""
    log_message_found = False

    while time.time() - start_time < max_wait_time:
        stderr_output += read_stderr(server_process, timeout=1.0)
        if "Triggering initial project file scan on server startup..." in stderr_output:
            log_message_found = True
            break
        time.sleep(0.5)

    if not log_message_found:
        pytest.fail(
            f"Log message not found after {max_wait_time} seconds. "
            f"Captured stderr:\n{stderr_output}"
        )

    assert "Triggering initial project file scan on server startup..." in stderr_output


def test_list_tools(server_process):
    """
    Test the ListTools request.
    Verifies that the server returns the expected list of tools
    with their names, descriptions, and input schemas.
    """
    send_mcp_request(server_process, "initialize", request_id="init_list_tools")
    init_response = read_mcp_response(server_process)
    assert "result" in init_response, (
        f"Error in init response: {init_response.get('error')}"
    )
    send_mcp_request(server_process, "notifications/initialized", request_id=None)

    send_mcp_request(server_process, "tools/list", request_id="list_tools_test_1")
    response = read_mcp_response(server_process)

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == "list_tools_test_1"
    assert "result" in response, f"Error in ListTools response: {response.get('error')}"
    assert "error" not in response

    tools_payload = response["result"]
    assert "tools" in tools_payload, "Key 'tools' missing in ListTools result"
    tools_list = tools_payload["tools"]
    assert isinstance(tools_list, list)

    expected_tool_names = {"trigger_index", "get_status", "search_index"}
    found_tool_names = {tool["name"] for tool in tools_list}
    assert found_tool_names == expected_tool_names

    for tool in tools_list:
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool

        if tool["name"] == "trigger_index":
            assert tool["inputSchema"]["type"] == "object"
            assert "properties" in tool["inputSchema"]
            assert "force_reindex" in tool["inputSchema"]["properties"]
        elif tool["name"] == "get_status":
            expected_schema = {
                "type": "object",
                "properties": {},
                "title": "get_status_toolArguments",
            }
            assert tool["inputSchema"] == expected_schema
        elif tool["name"] == "search_index":
            assert tool["inputSchema"]["type"] == "object"
            assert "properties" in tool["inputSchema"]
            assert "query" in tool["inputSchema"]["properties"]
            assert "top_k" in tool["inputSchema"]["properties"]
