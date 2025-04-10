import pytest
import httpx
import os
import shutil
import asyncio
from pathlib import Path
from unittest.mock import patch

# Ensure the app can be imported. Adjust the path if necessary.
# This assumes mcp_server.py is in the parent directory of tests/
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import the FastAPI app *after* potentially modifying sys.path
# and *before* fixtures that might rely on settings being loaded
from mcp_server import app, get_settings, ServerState, file_watcher, indexer

# Define test paths relative to this file
TESTS_DIR = Path(__file__).parent
TEST_PROJECT_DIR = TESTS_DIR / "test_project"
TEST_LANCEDB_PATH = TESTS_DIR / "test_lancedb"
TEST_PROJECT_FILE = TEST_PROJECT_DIR / "test_file.py"

@pytest.fixture(scope="function", autouse=True)
async def test_environment(monkeypatch):
    """
    Sets up a clean test environment for each test function.
    - Creates test directories (project, lancedb).
    - Creates a dummy file in the test project.
    - Sets environment variables for settings override.
    - Cleans up directories and resets state after the test.
    """
    # --- Setup ---
    # Ensure clean state before test
    if TEST_PROJECT_DIR.exists():
        shutil.rmtree(TEST_PROJECT_DIR)
    if TEST_LANCEDB_PATH.exists():
        shutil.rmtree(TEST_LANCEDB_PATH)

    TEST_PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    TEST_LANCEDB_PATH.mkdir(parents=True, exist_ok=True)
    TEST_PROJECT_FILE.write_text("def hello():\n    print('hello world')\n")

    # Monkeypatch environment variables *before* settings are potentially accessed
    monkeypatch.setenv("PROJECT_PATH", str(TEST_PROJECT_DIR.resolve()))
    monkeypatch.setenv("LANCEDB_PATH", str(TEST_LANCEDB_PATH.resolve()))
    monkeypatch.setenv("LOG_LEVEL", "DEBUG") # Optional: more verbose logs for tests

    # Reset singletons/global state if necessary.
    # This forces Settings to reload with patched env vars if accessed again.
    # We also need to reset the state of the server components.
    # Use patch to temporarily replace instances or reset state
    with patch('mcp_server.settings', get_settings(reload=True)), \
         patch('mcp_server.server_state', ServerState.INITIALIZING), \
         patch('mcp_server.file_watcher', None), \
         patch('mcp_server.indexer', None):

        # Re-initialize components based on patched settings if needed by tests
        # Note: Direct re-initialization might be complex. Tests might need
        # to trigger initialization logic (like the startup event).
        # For now, we rely on endpoint logic to use the patched settings.

        yield # Test runs here

    # --- Teardown ---
    if TEST_PROJECT_DIR.exists():
        shutil.rmtree(TEST_PROJECT_DIR)
    if TEST_LANCEDB_PATH.exists():
        shutil.rmtree(TEST_LANCEDB_PATH)

    # Reset global state after test (important for subsequent tests)
    # This might involve resetting singletons or module-level variables
    # in mcp_server, indexer, file_watcher if they maintain state.
    # Example: Resetting server state if it's a mutable global
    # from mcp_server import server_state # Re-import might be needed
    # server_state = ServerState.INITIALIZING # Or reset function

@pytest.fixture(scope="function")
async def client():
    """Provides an HTTPX async client for testing the FastAPI app."""
    async with httpx.AsyncClient(app=app, base_url="http://test") as ac:
        # Ensure server startup event runs if needed for initialization
        # This might be implicitly handled by AsyncClient or require explicit call
        # await app.router.startup() # If startup logic isn't run automatically
        yield ac
        # Ensure server shutdown event runs if needed for cleanup
        # await app.router.shutdown() # If shutdown logic isn't run automatically


# --- Test Cases ---

@pytest.mark.asyncio
async def test_read_root(client: httpx.AsyncClient):
    """Test the root endpoint."""
    response = await client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Indexing MCP Server is running."}

# Add more tests below...

@pytest.mark.asyncio
async def test_status_initial(client: httpx.AsyncClient):
    """Test initial status endpoint (assuming INITIALIZING from patch)."""
    # The test_environment fixture patches server_state to INITIALIZING
    settings = get_settings()
    # URL Encode the path before sending
    project_path_encoded = settings.project_path.replace("/", "%2F")
    response = await client.get(f"/status/{project_path_encoded}")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == ServerState.INITIALIZING.value
    assert data["project_path"] == settings.project_path
    assert "indexed_chunk_count" in data # Check key exists

@pytest.mark.asyncio
async def test_status_incorrect_path(client: httpx.AsyncClient):
    """Test status endpoint with an incorrect project path."""
    incorrect_path = "nonexistent%2Fpath" # Already URL-encoded style
    response = await client.get(f"/status/{incorrect_path}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Status not found for the specified project path."

@pytest.mark.asyncio
@patch('mcp_server.indexer') # Mock the whole indexer module instance used in mcp_server
async def test_status_chunk_count(mock_indexer, client: httpx.AsyncClient):
    """Test status endpoint reports chunk count from indexer."""
    mock_indexer.get_indexed_chunk_count.return_value = 123
    settings = get_settings()
    project_path_encoded = settings.project_path.replace("/", "%2F")

    # Set state to Watching to simulate a stable state where count is relevant
    with patch('mcp_server.server_state', ServerState.WATCHING):
        response = await client.get(f"/status/{project_path_encoded}")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == ServerState.WATCHING.value
    assert data["project_path"] == settings.project_path
    assert data["indexed_chunk_count"] == 123
    mock_indexer.get_indexed_chunk_count.assert_called_once()

@pytest.mark.asyncio
@patch('mcp_server.asyncio.create_task') # Mock task creation
@patch('mcp_server.file_watcher') # Mock the file_watcher instance
async def test_index_trigger(mock_file_watcher, mock_create_task, client: httpx.AsyncClient):
    """Test triggering the index endpoint successfully."""
    # Ensure initial state allows indexing
    with patch('mcp_server.server_state', ServerState.WATCHING):
        response = await client.post("/index", json={"force_reindex": False})

    assert response.status_code == 200
    assert "Indexing started in background" in response.json()["message"]
    # Check that create_task was called, implying scan was initiated
    mock_create_task.assert_called_once()
    # Check that the correct function (initial_scan) was passed to create_task
    # Access the coroutine function passed to create_task
    coro_func = mock_create_task.call_args[0][0]
    # Check if the coroutine function wraps the intended method
    # This check might be fragile depending on how the coroutine is created
    # A safer check might involve asserting mock_file_watcher.initial_scan was prepared/called
    # For now, let's assume the structure is simple: create_task(fw.initial_scan())
    # A direct comparison might fail due to decorators or partials.
    # Let's check if the mock method was accessed or prepared to be called.
    # This requires the mock_file_watcher to be configured appropriately.
    # If file_watcher is None from the fixture, this test needs adjustment.
    # Let's assume file_watcher is initialized or mocked effectively.
    # A simple check: was the method *accessed* within the endpoint?
    # This isn't perfect. A better mock setup might be needed.
    # For now, asserting create_task was called is the main check.

@pytest.mark.asyncio
async def test_index_trigger_conflict(client: httpx.AsyncClient):
    """Test triggering index endpoint when already scanning."""
    # Set state to SCANNING to simulate conflict
    with patch('mcp_server.server_state', ServerState.SCANNING):
        response = await client.post("/index", json={"force_reindex": False})

    assert response.status_code == 409
    assert "already in progress" in response.json()["detail"]

@pytest.mark.asyncio
@patch('mcp_server.asyncio.create_task')
@patch('mcp_server.file_watcher')
@patch('mcp_server.indexer')
async def test_index_trigger_force_reindex(mock_indexer, mock_file_watcher, mock_create_task, client: httpx.AsyncClient):
    """Test triggering index with force_reindex=True."""
    with patch('mcp_server.server_state', ServerState.WATCHING):
        response = await client.post("/index", json={"force_reindex": True})

    assert response.status_code == 200
    assert "Forced re-indexing started" in response.json()["message"]
    mock_indexer.clear_index.assert_called_once() # Verify index was cleared
    mock_create_task.assert_called_once() # Verify scan task was created
    # Similar check for the task function as in test_index_trigger

@pytest.mark.asyncio
@patch('mcp_server.indexer')
async def test_search(mock_indexer, client: httpx.AsyncClient):
    """Test the search endpoint."""
    mock_search_results = [
        {"file_path": "test/file1.py", "chunk_content": "content 1", "score": 0.9, "start_line": 1, "end_line": 5},
        {"file_path": "test/file2.py", "chunk_content": "content 2", "score": 0.8, "start_line": 10, "end_line": 15},
    ]
    # Adjust mock to return SearchResult objects if the endpoint expects them
    # Assuming the endpoint converts SearchResult to dict based on Pydantic model
    mock_indexer.search.return_value = mock_search_results # Keep as dict if endpoint returns dicts

    # Assume server is in a state where search is allowed (e.g., WATCHING)
    with patch('mcp_server.server_state', ServerState.WATCHING):
        response = await client.post("/search", json={"query": "test query", "top_k": 5})

    assert response.status_code == 200
    data = response.json()
    # Compare based on the expected JSON output structure
    assert data == mock_search_results
    mock_indexer.search.assert_called_once_with(query="test query", top_k=5)

# Test status changes during indexing (requires more complex mocking/waiting)
# @pytest.mark.asyncio
# async def test_status_during_indexing(client: httpx.AsyncClient):
#     # 1. Set state to WATCHING
#     # 2. Mock initial_scan to take time and update state
#     # 3. Call /index
#     # 4. Immediately call /status, check for SCANNING
#     # 5. Wait for mock scan to finish (update state to WATCHING)
#     # 6. Call /status again, check for WATCHING
#     pass # Implementation requires careful async/mock handling