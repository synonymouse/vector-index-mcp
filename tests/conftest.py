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
    "main_mcp.py",
)


def start_server_process(env_vars):
    """Starts the server subprocess with given environment variables."""
    process_env = os.environ.copy()
    process_env.update(env_vars)

    python_executable = sys.executable
    project_path_arg = env_vars["PROJECT_PATH"]

    proc = subprocess.Popen(
        [
            python_executable,
            SERVER_SCRIPT_PATH,
            project_path_arg,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=process_env,
        text=True,
        bufsize=1,
    )
    time.sleep(15)
    return proc


def send_mcp_request(process, method, params=None, request_id=1):
    """Constructs and sends a JSON-RPC request to the process."""
    request_obj = {
        "jsonrpc": "2.0",
        "method": method,
    }
    if request_id is not None:
        request_obj["id"] = request_id

    if method == "initialize":
        request_obj["params"] = {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "pytest-mcp-client", "version": "0.1.0"},
        }
    elif params:
        request_obj["params"] = params

    request_str = json.dumps(request_obj) + "\n"

    if process.stdin is None:
        raise BrokenPipeError("Stdin is not available")

    process.stdin.write(request_str)
    process.stdin.flush()


def read_mcp_response(process, timeout=20):
    """Reads and parses a JSON-RPC response from the process using select for timeout."""
    if process.stdout is None:
        log.error("process.stdout is None, cannot read response.")
        raise BrokenPipeError("Stdout is not available")

    if not hasattr(process.stdout, "fileno"):
        log.error("process.stdout does not have a fileno method, cannot use select.")
        raise ValueError(
            "process.stdout does not have a fileno method, cannot use select."
        )

    stdout_fd = -1
    try:
        stdout_fd = process.stdout.fileno()
    except ValueError as e:
        stderr_output = read_stderr(process)
        log.error(
            f"Failed to get fileno from process.stdout. It might be closed. Error: {e}. Stderr: {stderr_output}"
        )
        raise BrokenPipeError(
            f"Failed to get fileno from process.stdout (it might be closed): {e}. Stderr: {stderr_output}"
        ) from e

    try:
        ready_to_read, _, _ = select.select([stdout_fd], [], [], timeout)
    except ValueError as e:
        stderr_output = read_stderr(process)
        log.error(
            f"select.select error on stdout (fd: {stdout_fd}): {e}. Process poll: {process.poll()}. Stderr: {stderr_output}"
        )
        raise BrokenPipeError(
            f"select.select error on stdout (fd: {stdout_fd}, possibly closed): {e}. Stderr: {stderr_output}"
        ) from e
    except Exception as e:
        stderr_output = read_stderr(process)
        log.error(
            f"Unexpected error during select.select on stdout (fd: {stdout_fd}): {e}. Stderr: {stderr_output}"
        )
        raise RuntimeError(
            f"Unexpected error during select.select: {e}. Stderr: {stderr_output}"
        ) from e

    if not ready_to_read:
        stderr_output = read_stderr(process)
        log.warning(
            f"Timeout ({timeout}s) reading from server stdout. Stderr: {stderr_output}"
        )
        raise TimeoutError(
            f"Timeout ({timeout}s) reading from server stdout. Stderr: {stderr_output}"
        )

    response_str = ""
    try:
        response_str = process.stdout.readline()
    except ValueError as e:
        stderr_output = read_stderr(process)
        log.error(
            f"Error reading line from stdout after select indicated readiness (fd: {stdout_fd}): {e}. Stderr: {stderr_output}"
        )
        raise EOFError(
            f"Error reading line from stdout (it might be closed): {e}. Stderr: {stderr_output}"
        ) from e

    if not response_str:
        stderr_output = read_stderr(process)
        log.warning(
            f"No response received from server (EOF or empty line read from stdout). Stderr: {stderr_output}"
        )
        raise EOFError(
            f"No response received from server (EOF or empty line read from stdout). Stderr: {stderr_output}"
        )

    log.debug(f"Raw response string from server: '{response_str.strip()}'")

    try:
        response_data = json.loads(response_str)
        log.debug(f"Successfully parsed MCP Response: {response_data}")
        return response_data
    except json.JSONDecodeError as e:
        log.error(
            f"Failed to decode JSON response: '{response_str.strip()}'. Error: {e}"
        )
        raise json.JSONDecodeError(
            f"Failed to decode JSON response: '{response_str.strip()}'. Original error: {e}. Stderr: {read_stderr(process)}",
            e.doc,
            e.pos,
        ) from e


def read_stderr(process, timeout=0.1):
    """Reads from stderr of the process."""
    if process.stderr is None:
        return "Stderr not available"

    output = []
    process.stderr.flush()

    if (
        hasattr(os, "fcntl")
        and hasattr(os, "F_GETFL")
        and hasattr(os, "F_SETFL")
        and hasattr(os, "O_NONBLOCK")
    ):
        fd = process.stderr.fileno()
        fl = os.fcntl(fd, os.F_GETFL)
        try:
            os.fcntl(fd, os.F_SETFL, fl | os.O_NONBLOCK)
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    line = process.stderr.readline()
                    if line:
                        output.append(line)
                    else:
                        break
                except BlockingIOError:
                    if not (time.time() - start_time < timeout):
                        break
                    time.sleep(0.01)
                except Exception:
                    break
        finally:
            os.fcntl(fd, os.F_SETFL, fl)
    else:
        end_time = time.time() + timeout
        while time.time() < end_time:
            remaining_timeout = max(0, end_time - time.time())
            if remaining_timeout == 0:
                break

            try:
                ready_to_read, _, _ = select.select(
                    [process.stderr], [], [], remaining_timeout
                )
            except ValueError:
                break

            if process.stderr in ready_to_read:
                try:
                    line = process.stderr.readline()
                    if line:
                        output.append(line)
                    else:  # EOF
                        break
                except Exception:
                    break
            else:
                break

    return "".join(output)


@pytest.fixture(scope="function")
def temp_project_dir():
    """Creates a temporary directory for testing project path."""
    temp_dir = tempfile.mkdtemp(prefix="test_project_")
    with open(os.path.join(temp_dir, "dummy.txt"), "w") as f:
        f.write("test content")
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture(scope="function")
def temp_lancedb_uri(temp_project_dir):
    """Creates a temporary LanceDB URI within the temp_project_dir."""
    db_path = os.path.join(temp_project_dir, ".lancedb")
    return db_path


@pytest.fixture(scope="function")
def server_process(temp_project_dir, temp_lancedb_uri):
    """Fixture to start and stop the MCP server process for each test function."""
    env_vars = {
        "PROJECT_PATH": temp_project_dir,
        "LANCEDB_URI": temp_lancedb_uri,
        "LOG_LEVEL": "DEBUG",
        "IGNORE_PATTERNS": json.dumps(
            [".*", "*.db", "*.sqlite", "*.log", "node_modules/*", "venv/*", ".git/*"]
        ),
        "TESTING_MODE": "true",
        "HF_HUB_OFFLINE": "1",  # Prevent HuggingFace Hub network calls
    }
    proc = start_server_process(env_vars)

    if proc.poll() is not None:
        stderr_output = read_stderr(proc, timeout=1)
        raise RuntimeError(
            f"Server process failed to start. Exit code: {proc.returncode}. Stderr: {stderr_output}"
        )

    yield proc

    if proc.poll() is None:
        try:
            send_mcp_request(proc, "Shutdown", request_id=None)
            time.sleep(0.5)
        except (BrokenPipeError, EOFError):
            pass
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

    stderr_output = read_stderr(proc, timeout=1)
    if stderr_output:
        print(f"Server stderr during teardown:\n{stderr_output}")
