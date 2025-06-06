import json
import logging
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

log = logging.getLogger(__name__)

# Define the path to the server script
SERVER_SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),  # Go up to vector-index-mcp directory
    "vector_index_mcp",
    "mcp_stdio_server.py"
)

def start_server_process(env_vars):
    """Starts the server subprocess with given environment variables."""
    process_env = os.environ.copy()
    process_env.update(env_vars)
    
    # Ensure python executable is the same as the one running pytest
    python_executable = sys.executable

    proc = subprocess.Popen(
        [python_executable, SERVER_SCRIPT_PATH],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, # Capture stderr for debugging
        env=process_env,
        text=True, # Work with text streams
        bufsize=1  # Line buffered
    )
    # Give the server a moment to start
    time.sleep(1) # Increased sleep for server startup
    return proc

def send_mcp_request(process, method, params=None, request_id=1):
    """Constructs and sends a JSON-RPC request to the process."""
    request_obj = {
        "jsonrpc": "2.0",
        "method": method,
        "id": request_id
    }
    if params:
        request_obj["params"] = params
    
    request_str = json.dumps(request_obj) + "\\n"
    
    if process.stdin is None:
        raise BrokenPipeError("Stdin is not available")
    
    process.stdin.write(request_str)
    process.stdin.flush()

def read_mcp_response(process, timeout=20):
    """Reads and parses a JSON-RPC response from the process using select for timeout."""
    if process.stdout is None:
        # This should not happen if subprocess.Popen was successful with PIPE
        log.error("process.stdout is None, cannot read response.")
        raise BrokenPipeError("Stdout is not available")

    if not hasattr(process.stdout, 'fileno'):
         # TextIOWrapper (from text=True) should have fileno().
         log.error("process.stdout does not have a fileno method, cannot use select.")
         raise ValueError("process.stdout does not have a fileno method, cannot use select.")

    stdout_fd = -1
    try:
        stdout_fd = process.stdout.fileno()
    except ValueError as e: # fileno() can raise ValueError if stream is closed
        stderr_output = read_stderr(process)
        log.error(f"Failed to get fileno from process.stdout. It might be closed. Error: {e}. Stderr: {stderr_output}")
        raise BrokenPipeError(f"Failed to get fileno from process.stdout (it might be closed): {e}. Stderr: {stderr_output}") from e
    
    try:
        ready_to_read, _, _ = select.select([stdout_fd], [], [], timeout)
    except ValueError as e:
        # This can happen if stdout_fd is closed or invalid (e.g., -1 after process death)
        stderr_output = read_stderr(process)
        log.error(f"select.select error on stdout (fd: {stdout_fd}): {e}. Process poll: {process.poll()}. Stderr: {stderr_output}")
        raise BrokenPipeError(f"select.select error on stdout (fd: {stdout_fd}, possibly closed): {e}. Stderr: {stderr_output}") from e
    except Exception as e: # Catch any other select errors
        stderr_output = read_stderr(process)
        log.error(f"Unexpected error during select.select on stdout (fd: {stdout_fd}): {e}. Stderr: {stderr_output}")
        raise RuntimeError(f"Unexpected error during select.select: {e}. Stderr: {stderr_output}") from e

    if not ready_to_read:
        # Timeout occurred
        stderr_output = read_stderr(process) # Capture stderr for debugging
        log.warning(f"Timeout ({timeout}s) reading from server stdout. Stderr: {stderr_output}")
        raise TimeoutError(f"Timeout ({timeout}s) reading from server stdout. Stderr: {stderr_output}")

    response_str = ""
    try:
        # process.stdout was opened with text=True, so readline() returns a string.
        # If process exited, readline() might return empty string or raise an error.
        response_str = process.stdout.readline()
    except ValueError as e: # e.g. "I/O operation on closed file"
        stderr_output = read_stderr(process)
        log.error(f"Error reading line from stdout after select indicated readiness (fd: {stdout_fd}): {e}. Stderr: {stderr_output}")
        raise EOFError(f"Error reading line from stdout (it might be closed): {e}. Stderr: {stderr_output}") from e

    if not response_str: # Empty string from readline() indicates EOF
        stderr_output = read_stderr(process)
        log.warning(f"No response received from server (EOF or empty line read from stdout). Stderr: {stderr_output}")
        raise EOFError(f"No response received from server (EOF or empty line read from stdout). Stderr: {stderr_output}")

    log.debug(f"Raw response string from server: '{response_str.strip()}'")

    try:
        response_data = json.loads(response_str)
        log.debug(f"Successfully parsed MCP Response: {response_data}")
        return response_data
    except json.JSONDecodeError as e:
        log.error(f"Failed to decode JSON response: '{response_str.strip()}'. Error: {e}")
        # Re-raise with the original document and position, adding current stderr.
        # e.doc is the original string that json.loads tried to parse.
        raise json.JSONDecodeError(
            f"Failed to decode JSON response: '{response_str.strip()}'. Original error: {e}. Stderr: {read_stderr(process)}",
            e.doc,
            e.pos
        ) from e

def read_stderr(process, timeout=0.1):
    """Reads from stderr of the process."""
    if process.stderr is None:
        return "Stderr not available"
    
    output = []
    process.stderr.flush() # Flush stderr before attempting to read

    # Try to use fcntl for POSIX non-blocking reads
    if hasattr(os, "fcntl") and hasattr(os, "F_GETFL") and hasattr(os, "F_SETFL") and hasattr(os, "O_NONBLOCK"):
        fd = process.stderr.fileno()
        fl = os.fcntl(fd, os.F_GETFL)
        try:
            os.fcntl(fd, os.F_SETFL, fl | os.O_NONBLOCK)
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    line = process.stderr.readline() # In non-blocking mode
                    if line:
                        output.append(line)
                    else: # Empty string means EOF for non-blocking text reads
                        break
                except BlockingIOError:
                    if not (time.time() - start_time < timeout): # Check overall timeout
                        break
                    time.sleep(0.01) # Wait briefly for more data
                except Exception: # Catch any other read error
                    break
        finally:
            # Restore original flags
            os.fcntl(fd, os.F_SETFL, fl)
    else:
        # Fallback to select-based reading for cross-platform compatibility
        # or when fcntl is not available.
        end_time = time.time() + timeout
        while time.time() < end_time:
            remaining_timeout = max(0, end_time - time.time())
            if remaining_timeout == 0:
                break
            
            try:
                ready_to_read, _, _ = select.select([process.stderr], [], [], remaining_timeout)
            except ValueError: # E.g. Invalid/closed file descriptor
                break # Cannot use select

            if process.stderr in ready_to_read:
                try:
                    line = process.stderr.readline()
                    if line:
                        output.append(line)
                    else:  # EOF
                        break
                except Exception: # Error during readline
                    break
            else:
                # select.select timed out, or fd not ready
                break
    
    return "".join(output)


@pytest.fixture(scope="function")
def temp_project_dir():
    """Creates a temporary directory for testing project path."""
    temp_dir = tempfile.mkdtemp(prefix="test_project_")
    # Create a dummy file to ensure the directory is not empty for some tests
    with open(os.path.join(temp_dir, "dummy.txt"), "w") as f:
        f.write("test content")
    yield temp_dir
    shutil.rmtree(temp_dir)

@pytest.fixture(scope="function")
def temp_lancedb_uri(temp_project_dir):
    """Creates a temporary LanceDB URI within the temp_project_dir."""
    db_path = os.path.join(temp_project_dir, ".lancedb")
    # No need to create the directory, LanceDB handles it.
    return db_path # LanceDB will append .lancedb if it's a directory path

@pytest.fixture(scope="function")
def server_process(temp_project_dir, temp_lancedb_uri):
    """Fixture to start and stop the MCP server process for each test function."""
    env_vars = {
        "PROJECT_PATH": temp_project_dir,
        "LANCEDB_URI": temp_lancedb_uri,
        "LOG_LEVEL": "DEBUG" # For more verbose output during tests
    }
    proc = start_server_process(env_vars)
    
    # Check if process started correctly
    if proc.poll() is not None: # Process terminated prematurely
        stderr_output = read_stderr(proc, timeout=1)
        raise RuntimeError(f"Server process failed to start. Exit code: {proc.returncode}. Stderr: {stderr_output}")

    yield proc
    
    # Teardown: stop the server
    if proc.poll() is None: # If process is still running
        try:
            # Send Shutdown request (optional, but good practice)
            send_mcp_request(proc, "Shutdown")
            # Give it a moment to process Shutdown
            time.sleep(0.5)
        except (BrokenPipeError, EOFError):
            # Server might have already exited or stdin/stdout closed
            pass
        finally:
            if proc.poll() is None:
                proc.terminate() # Force terminate if still running
                try:
                    proc.wait(timeout=2) # Wait for termination
                except subprocess.TimeoutExpired:
                    proc.kill() # Kill if terminate doesn't work
                    proc.wait() # Ensure it's killed
    
    # Read any remaining stderr output for debugging purposes if a test failed
    stderr_output = read_stderr(proc, timeout=1)
    if stderr_output:
        print(f"Server stderr during teardown:\n{stderr_output}")


# --- Test Cases ---

def test_initialize(server_process):
    """
    Test the Initialize request.
    Verifies that the server responds with its name, version, and capabilities.
    """
    send_mcp_request(server_process, "Initialize", request_id="init_test_1")
    response = read_mcp_response(server_process)

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == "init_test_1"
    assert "result" in response, f"Error in response: {response.get('error')}"
    assert "error" not in response

    result = response["result"]
    assert result["name"] == "vector-index-mcp"
    assert "version" in result # Version might change, so just check presence
    assert isinstance(result["capabilities"], dict)
    assert "tools" in result["capabilities"] # Tools are listed by ListTools, but key should exist
    assert "resources" in result["capabilities"]

def test_list_tools(server_process):
    """
    Test the ListTools request.
    Verifies that the server returns the expected list of tools
    with their names, descriptions, and input schemas.
    """
    # Initialize first, as per MCP spec
    send_mcp_request(server_process, "Initialize", request_id="init_list_tools")
    init_response = read_mcp_response(server_process)
    assert "result" in init_response, f"Error in init response: {init_response.get('error')}"

    send_mcp_request(server_process, "ListTools", request_id="list_tools_test_1")
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
        assert "inputSchema" in tool # Changed from input_schema
        # output_schema is not part of the MCP spec for ListTools response and not sent by server
        
        if tool["name"] == "trigger_index":
            assert tool["inputSchema"]["type"] == "object"
            assert "properties" in tool["inputSchema"]
            assert "force_reindex" in tool["inputSchema"]["properties"]
        elif tool["name"] == "get_status":
            # Server provides: {"type": "object", "properties": {}}
            assert tool["inputSchema"] == {"type": "object", "properties": {}}
        elif tool["name"] == "search_index":
            assert tool["inputSchema"]["type"] == "object"
            assert "properties" in tool["inputSchema"]
            assert "query" in tool["inputSchema"]["properties"]
            assert "top_k" in tool["inputSchema"]["properties"] # Changed from k


def test_call_tool_get_status(server_process, temp_project_dir):
    """
    Test CallTool for the 'get_status' tool.
    Verifies that the server returns the current status including project path,
    indexer status, last indexed time, files in index, and DB path.
    """
    send_mcp_request(server_process, "Initialize", request_id="init_get_status")
    init_response = read_mcp_response(server_process)
    assert "result" in init_response, f"Error in init response: {init_response.get('error')}"

    send_mcp_request(
        server_process,
        "CallTool",
        params={"tool_name": "get_status", "arguments": {}},
        request_id="get_status_test_1"
    )
    response = read_mcp_response(server_process)

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == "get_status_test_1"
    assert "result" in response, f"Error in get_status response: {response.get('error')}"
    assert "error" not in response

    status_result = response["result"]
    assert "project_path" in status_result
    assert status_result["project_path"] == temp_project_dir
    assert "status" in status_result # e.g., "idle", "indexing"
    assert "last_indexed_time" in status_result # Can be None initially
    assert "files_in_index" in status_result
    assert "db_path" in status_result

def test_call_tool_trigger_index_basic_and_verify_status(server_process, temp_project_dir):
    """
    Test CallTool for the 'trigger_index' tool with default arguments.
    Verifies that the indexing process is initiated and then checks 'get_status'
    to confirm that files were indexed and last_indexed_time is updated.
    """
    send_mcp_request(server_process, "Initialize", request_id="init_trigger_index")
    init_response = read_mcp_response(server_process)
    assert "result" in init_response, f"Error in init response: {init_response.get('error')}"

    # Create a dummy file in the temp_project_dir to be indexed
    test_file_path = os.path.join(temp_project_dir, "test_doc.txt")
    with open(test_file_path, "w") as f:
        f.write("This is a test document for indexing.")

    send_mcp_request(
        server_process,
        "CallTool",
        params={"tool_name": "trigger_index", "arguments": {}}, # No specific args for basic trigger
        request_id="trigger_index_test_1"
    )
    response = read_mcp_response(server_process)

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == "trigger_index_test_1"
    assert "result" in response, f"Error in trigger_index response: {response.get('error')}"
    assert "error" not in response

    trigger_result = response["result"]
    # Server response for trigger_index is: {"message": "Indexing process successfully initiated."}
    assert "message" in trigger_result
    assert trigger_result["message"] == "Indexing process successfully initiated."
    
    # Allow some time for indexing to complete (even if it's quick for one file)
    time.sleep(0.5)

    # Check status after indexing
    send_mcp_request(
        server_process,
        "CallTool",
        params={"tool_name": "get_status", "arguments": {}},
        request_id="get_status_after_index"
    )
    status_response = read_mcp_response(server_process)
    assert "result" in status_response, f"Error in get_status after index: {status_response.get('error')}"
    status_data = status_response["result"]
    assert status_data["files_in_index"] > 0, "Expected files_in_index to be > 0 after indexing"
    assert status_data["last_indexed_time"] is not None, "Expected last_indexed_time to be set after indexing"

# Placeholder for more tests, e.g., search_index
# def test_call_tool_search_index(server_process, temp_project_dir):
#     # 1. Initialize
#     # 2. Trigger index (ensure some content is indexed)
#     # 3. Call search_index
#     # 4. Verify results
#     pass