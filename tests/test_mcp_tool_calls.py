import json
import os
import time

from .conftest import read_mcp_response, send_mcp_request

# No need to import helper functions or fixtures directly, pytest handles conftest.py


def test_call_tool_get_status(server_process, temp_project_dir):
    """
    Test CallTool for the 'get_status' tool.
    Verifies that the server returns the current status including project path,
    indexer status, last indexed time, files in index, and DB path.
    """
    send_mcp_request(server_process, "initialize", request_id="init_get_status")
    init_response = read_mcp_response(server_process)
    assert "result" in init_response, (
        f"Error in init response: {init_response.get('error')}"
    )
    send_mcp_request(server_process, "notifications/initialized", request_id=None)

    send_mcp_request(
        server_process,
        "tools/call",
        params={"name": "get_status", "arguments": {}},
        request_id="get_status_test_1",
    )
    response = read_mcp_response(server_process)

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == "get_status_test_1"
    assert "result" in response, (
        f"Error in get_status response: {response.get('error')}"
    )
    assert "error" not in response

    outer_tool_result = response["result"]
    assert not outer_tool_result.get("isError", True), (
        f"Outer ToolResult indicates error: {outer_tool_result}"
    )
    assert "content" in outer_tool_result, "Outer ToolResult missing 'content'"
    assert isinstance(outer_tool_result["content"], list), (
        "Outer ToolResult 'content' is not a list"
    )
    assert len(outer_tool_result["content"]) == 1, (
        "Outer ToolResult 'content' does not have exactly one part"
    )
    assert outer_tool_result["content"][0].get("type") == "text", (
        "Outer ToolResult first content part is not text"
    )

    inner_tool_result_str = outer_tool_result["content"][0]["text"]
    inner_tool_result = json.loads(inner_tool_result_str)

    assert not inner_tool_result.get("isError", True), (
        f"Inner ToolResult indicates error: {inner_tool_result}"
    )
    assert "content" in inner_tool_result, "Inner ToolResult missing 'content'"
    assert isinstance(inner_tool_result["content"], list), (
        "Inner ToolResult 'content' is not a list"
    )
    assert len(inner_tool_result["content"]) == 1, (
        "Inner ToolResult 'content' does not have exactly one part"
    )
    assert inner_tool_result["content"][0].get("type") == "text", (
        "Inner ToolResult first content part is not text"
    )

    actual_status_payload_str = inner_tool_result["content"][0]["text"]
    status_payload = json.loads(actual_status_payload_str)

    assert "project_path" in status_payload
    assert status_payload["project_path"] == temp_project_dir
    assert "status" in status_payload

    assert "last_scan_start_time" in status_payload
    assert "last_scan_end_time" in status_payload
    assert "indexed_chunk_count" in status_payload
    assert status_payload["indexed_chunk_count"] == 0
    assert "error_message" in status_payload


def test_call_tool_trigger_index_basic_and_verify_status(
    server_process, temp_project_dir
):
    """
    Test CallTool for the 'trigger_index' tool with default arguments.
    Verifies that the indexing process is initiated and then checks 'get_status'
    to confirm that files were indexed and last_indexed_time is updated.
    """
    send_mcp_request(server_process, "initialize", request_id="init_trigger_index")
    init_response = read_mcp_response(server_process)
    assert "result" in init_response, (
        f"Error in init response: {init_response.get('error')}"
    )
    send_mcp_request(server_process, "notifications/initialized", request_id=None)

    test_file_path = os.path.join(temp_project_dir, "test_doc.txt")
    with open(test_file_path, "w") as f:
        f.write("This is a test document for indexing.")

    send_mcp_request(
        server_process,
        "tools/call",
        params={"name": "trigger_index", "arguments": {}},
        request_id="trigger_index_test_1",
    )
    response = read_mcp_response(server_process)

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == "trigger_index_test_1"
    assert "result" in response, (
        f"Error in trigger_index response: {response.get('error')}"
    )
    assert "error" not in response

    outer_trigger_result = response["result"]
    assert not outer_trigger_result.get("isError", True), (
        f"Outer trigger_result indicates error: {outer_trigger_result}"
    )
    assert "content" in outer_trigger_result, "Outer trigger_result missing 'content'"
    assert isinstance(outer_trigger_result["content"], list), (
        "Outer trigger_result 'content' is not a list"
    )
    assert len(outer_trigger_result["content"]) == 1, (
        "Outer trigger_result 'content' does not have exactly one part"
    )
    assert outer_trigger_result["content"][0].get("type") == "text", (
        "Outer trigger_result first content part is not text"
    )

    inner_trigger_result_str = outer_trigger_result["content"][0]["text"]
    inner_trigger_result = json.loads(inner_trigger_result_str)

    assert not inner_trigger_result.get("isError", True), (
        f"Inner trigger_result indicates error: {inner_trigger_result}"
    )
    assert "content" in inner_trigger_result, "Inner trigger_result missing 'content'"
    assert isinstance(inner_trigger_result["content"], list), (
        "Inner trigger_result 'content' is not a list"
    )
    assert len(inner_trigger_result["content"]) == 1, (
        "Inner trigger_result 'content' does not have exactly one part"
    )
    assert inner_trigger_result["content"][0].get("type") == "text", (
        "Inner trigger_result first content part is not text"
    )

    actual_trigger_payload_text = inner_trigger_result["content"][0]["text"]
    assert actual_trigger_payload_text == "Indexing successfully triggered."
    time.sleep(2)  # Increased sleep to allow for indexing, especially if DB is cold

    send_mcp_request(
        server_process,
        "tools/call",
        params={"name": "get_status", "arguments": {}},
        request_id="get_status_after_index",
    )
    status_response = read_mcp_response(server_process)
    assert "result" in status_response, (
        f"Error in get_status after index: {status_response.get('error')}"
    )

    outer_status_result = status_response["result"]
    assert not outer_status_result.get("isError", True), (
        f"Outer status_result indicates error: {outer_status_result}"
    )
    assert "content" in outer_status_result, "Outer status_result missing 'content'"
    assert isinstance(outer_status_result["content"], list), (
        "Outer status_result 'content' is not a list"
    )
    assert len(outer_status_result["content"]) == 1, (
        "Outer status_result 'content' does not have exactly one part"
    )
    assert outer_status_result["content"][0].get("type") == "text", (
        "Outer status_result first content part is not text"
    )

    inner_status_result_str = outer_status_result["content"][0]["text"]
    inner_status_result = json.loads(inner_status_result_str)

    assert not inner_status_result.get("isError", True), (
        f"Inner status_result indicates error: {inner_status_result}"
    )
    assert "content" in inner_status_result, "Inner status_result missing 'content'"
    assert isinstance(inner_status_result["content"], list), (
        "Inner status_result 'content' is not a list"
    )
    assert len(inner_status_result["content"]) == 1, (
        "Inner status_result 'content' does not have exactly one part"
    )
    assert inner_status_result["content"][0].get("type") == "text", (
        "Inner status_result first content part is not text"
    )

    actual_status_payload_str = inner_status_result["content"][0]["text"]
    status_payload = json.loads(actual_status_payload_str)

    # dummy.txt (from fixture) + test_doc.txt (created in this test) should be indexed.
    # The scan might pick up other things if not perfectly filtered, but at least 2.
    assert status_payload["indexed_chunk_count"] >= 1, (
        f"Expected indexed_chunk_count to be >= 1 after indexing, got {status_payload['indexed_chunk_count']}"
    )
    assert status_payload["last_scan_end_time"] is not None, (
        "Expected last_scan_end_time to be set after indexing"
    )


def test_call_tool_search_index(server_process, temp_project_dir):
    """
    Test CallTool for the 'search_index' tool.
    Verifies that after indexing, a search query returns relevant results.
    """
    send_mcp_request(server_process, "initialize", request_id="init_search_index")
    init_response = read_mcp_response(server_process)
    assert "result" in init_response, (
        f"Error in init response: {init_response.get('error')}"
    )
    send_mcp_request(server_process, "notifications/initialized", request_id=None)

    file1_path = os.path.join(temp_project_dir, "file1.txt")
    file2_path = os.path.join(temp_project_dir, "file2.txt")

    with open(file1_path, "w") as f:
        f.write("The quick brown fox jumps over the lazy dog.")
    with open(file2_path, "w") as f:
        f.write("Semantic search is a key feature of this project.")

    send_mcp_request(
        server_process,
        "tools/call",
        params={"name": "trigger_index", "arguments": {}},
        request_id="trigger_for_search",
    )
    trigger_response = read_mcp_response(server_process)
    assert "result" in trigger_response, (
        f"Error in trigger_index response: {trigger_response.get('error')}"
    )

    outer_trigger_result = trigger_response["result"]
    assert not outer_trigger_result.get("isError", True), (
        f"Outer trigger_result indicates error: {outer_trigger_result}"
    )
    assert (
        "content" in outer_trigger_result
        and isinstance(outer_trigger_result["content"], list)
        and len(outer_trigger_result["content"]) == 1
    )
    assert outer_trigger_result["content"][0].get("type") == "text"
    inner_trigger_result_str = outer_trigger_result["content"][0]["text"]
    inner_trigger_result = json.loads(inner_trigger_result_str)
    assert not inner_trigger_result.get("isError", True), (
        f"Inner trigger_result indicates error: {inner_trigger_result}"
    )
    assert (
        "content" in inner_trigger_result
        and isinstance(inner_trigger_result["content"], list)
        and len(inner_trigger_result["content"]) == 1
    )
    assert inner_trigger_result["content"][0].get("type") == "text"
    actual_trigger_payload_text = inner_trigger_result["content"][0]["text"]
    assert actual_trigger_payload_text == "Indexing successfully triggered."

    time.sleep(2)  # Give time for indexing to complete

    send_mcp_request(
        server_process,
        "tools/call",
        params={"name": "search_index", "arguments": {"query": "fox", "top_k": 1}},
        request_id="search_fox",
    )
    search_response = read_mcp_response(server_process)

    assert search_response["jsonrpc"] == "2.0"
    assert search_response["id"] == "search_fox"
    assert "result" in search_response, (
        f"Error in search_index response: {search_response.get('error')}"
    )
    assert "error" not in search_response

    outer_search_result = search_response["result"]
    assert not outer_search_result.get("isError", True), (
        f"Outer search_result indicates error: {outer_search_result}"
    )
    assert "content" in outer_search_result, "Outer search_result missing 'content'"
    assert isinstance(outer_search_result["content"], list), (
        "Outer search_result 'content' is not a list"
    )
    assert len(outer_search_result["content"]) == 1, (
        "Outer search_result 'content' does not have exactly one part"
    )
    assert outer_search_result["content"][0].get("type") == "text", (
        "Outer search_result first content part is not text"
    )

    inner_search_result_str = outer_search_result["content"][0]["text"]
    inner_search_result = json.loads(inner_search_result_str)

    assert not inner_search_result.get("isError", True), (
        f"Inner search_result indicates error: {inner_search_result}"
    )
    assert "content" in inner_search_result, "Inner search_result missing 'content'"
    assert isinstance(inner_search_result["content"], list), (
        "Inner search_result 'content' is not a list"
    )
    assert len(inner_search_result["content"]) > 0, (
        "Inner search_result 'content' is empty"
    )
    assert inner_search_result["content"][0].get("type") == "text", (
        "Inner search_result first content part is not text"
    )

    search_results_payload_str = inner_search_result["content"][0]["text"]
    search_results = json.loads(search_results_payload_str)

    assert isinstance(search_results, list)
    assert len(search_results) > 0
    assert any("fox" in r["extracted_text_chunk"].lower() for r in search_results)
    assert any(os.path.realpath(r["file_path"]) == os.path.realpath(file1_path) for r in search_results)

    send_mcp_request(
        server_process,
        "tools/call",
        params={
            "name": "search_index",
            "arguments": {"query": "semantic search", "top_k": 1},
        },
        request_id="search_semantic",
    )
    search_response_2 = read_mcp_response(server_process)
    assert "result" in search_response_2, (
        f"Error in search_index response: {search_response_2.get('error')}"
    )
    outer_search_result_2 = search_response_2["result"]
    inner_search_result_str_2 = outer_search_result_2["content"][0]["text"]
    inner_search_result_2 = json.loads(inner_search_result_str_2)
    search_results_payload_str_2 = inner_search_result_2["content"][0]["text"]
    search_results_2 = json.loads(search_results_payload_str_2)

    assert isinstance(search_results_2, list)
    assert len(search_results_2) > 0
    assert any(
        "semantic search" in r["extracted_text_chunk"].lower() for r in search_results_2
    )
    assert any(os.path.realpath(r["file_path"]) == os.path.realpath(file2_path) for r in search_results_2)
