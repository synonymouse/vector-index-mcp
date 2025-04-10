import pytest
import pytest_asyncio # Add import for the fixture decorator
import httpx
import os
import shutil
import asyncio
from pathlib import Path
from unittest.mock import patch
import urllib.parse # Add import

# Ensure the app can be imported. Adjust the path if necessary.
# This assumes mcp_server.py is in the parent directory of tests/
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import the FastAPI app *after* potentially modifying sys.path
# and *before* fixtures that might rely on settings being loaded
from mcp_server import app, mcp_server_instance # Import the instance

# Define test paths relative to this file
TESTS_DIR = Path(__file__).parent
TEST_PROJECT_DIR = TESTS_DIR / "test_project"
TEST_LANCEDB_PATH = TESTS_DIR / "test_lancedb"
TEST_PROJECT_FILE = TEST_PROJECT_DIR / "test_file.py"

@pytest.fixture(scope="function", autouse=True)
def test_environment(monkeypatch): # Make fixture synchronous
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

    # Monkeypatch environment variables *before* creating the new Settings instance
    # Use relative path for testing
    monkeypatch.setenv("PROJECT_PATH", str(TEST_PROJECT_DIR))
    monkeypatch.setenv("LANCEDB_PATH", str(TEST_LANCEDB_PATH.resolve()))
    monkeypatch.setenv("LOG_LEVEL", "DEBUG") # Optional: more verbose logs for tests

    # Create a new Settings instance that reflects the monkeypatched environment
    # Import Settings here if not already imported globally in the file
    from models import Settings
    test_settings = Settings()

    # Patch attributes on the actual server instance using the new settings
    # Ensure mcp_server_instance uses these settings during the test
    with patch('mcp_server.mcp_server_instance.settings', test_settings), \
         patch('mcp_server.mcp_server_instance.project_path', test_settings.project_path), \
         patch('mcp_server.mcp_server_instance.status', "Initializing"), \
         patch('mcp_server.mcp_server_instance.file_watcher', None), \
         patch('mcp_server.mcp_server_instance.indexer', None):

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

@pytest_asyncio.fixture(scope="function") # Use pytest_asyncio.fixture for async fixture
async def client():
    """Provides an HTTPX async client for testing the FastAPI app."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as ac:
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
    assert response.json() == {"message": "MCP Indexing Server"}

# Add more tests below...

@pytest.mark.asyncio
async def test_status_initial(client: httpx.AsyncClient):
    """Test initial status endpoint (assuming INITIALIZING from patch)."""
    # The test_environment fixture patches server_state to INITIALIZING
    settings = mcp_server_instance.settings
    # Use the path directly
    # Use the path directly, adding a trailing slash to match potential redirect behavior
    response = await client.get(f"/status/{settings.project_path}/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "Initializing" # Use string status
    assert data["project_path"] == settings.project_path
    assert "indexed_chunk_count" in data # Check key exists

@pytest.mark.asyncio
async def test_status_incorrect_path(client: httpx.AsyncClient):
    """Test status endpoint with an incorrect project path."""
    incorrect_path = "nonexistent%2Fpath" # Already URL-encoded style
    response = await client.get(f"/status/{incorrect_path}")
    assert response.status_code == 200 # Endpoint returns 200 OK for unmatched paths
    data = response.json()
    assert data["status"] == "Not Found"
    assert data["project_path"] == incorrect_path.replace("%2F", "/") # Compare decoded path
    assert "not managed by this server instance" in data["error_message"]

@pytest.mark.asyncio
@patch('mcp_server.mcp_server_instance.indexer') # Mock the indexer instance attribute
async def test_status_chunk_count(mock_indexer, client: httpx.AsyncClient):
    """Test status endpoint reports chunk count from indexer."""
    mock_indexer.get_indexed_chunk_count.return_value = 123
    settings = mcp_server_instance.settings
    # Use the path directly - FastAPI's path parameter with :path converter handles URL encoding

    # Set state to Watching to simulate a stable state where count is relevant
    with patch('mcp_server.mcp_server_instance.status', "Watching"): # Patch instance status
        # Use the path directly
        response = await client.get(f"/status/{settings.project_path}/")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "Watching" # Use string status
    assert data["project_path"] == settings.project_path
    assert data["indexed_chunk_count"] == 123
    mock_indexer.get_indexed_chunk_count.assert_called_once()

@pytest.mark.asyncio
@patch('fastapi.BackgroundTasks.add_task') # Mock FastAPI's background task adder
@patch('mcp_server.mcp_server_instance.file_watcher') # Mock the file_watcher instance attribute
async def test_index_trigger(mock_file_watcher, mock_add_task, client: httpx.AsyncClient): # Rename mock
    """Test triggering the index endpoint successfully."""
    settings = mcp_server_instance.settings # Get settings to access project_path
    # Ensure initial state allows indexing
    with patch('mcp_server.mcp_server_instance.status', "Watching"): # Patch instance status
        response = await client.post("/index", json={"project_path": settings.project_path, "force_reindex": False})

    assert response.status_code == 200
    # Check for the actual message format
    assert f"Indexing process initiated for {settings.project_path} in the background." == response.json()["message"]
    # Check that create_task was called, implying scan was initiated
    mock_add_task.assert_called_once() # Check add_task mock
    # Check that the correct function (initial_scan) was passed to create_task
    # Access the coroutine function passed to create_task
    # Check the arguments passed to add_task
    # First arg is the task function, subsequent are args/kwargs for it
    task_func = mock_add_task.call_args[0][0]
    task_kwargs = mock_add_task.call_args[1]
    assert task_func == mcp_server_instance._perform_scan
    assert task_kwargs.get("force_reindex") is False
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
    settings = mcp_server_instance.settings # Get settings to access project_path
    # Set state to SCANNING to simulate conflict
    with patch('mcp_server.mcp_server_instance.status', "Scanning"): # Patch instance status
        response = await client.post("/index", json={"project_path": settings.project_path, "force_reindex": False})

    assert response.status_code == 409
    assert "already in progress" in response.json()["detail"]

@pytest.mark.asyncio
@patch('fastapi.BackgroundTasks.add_task') # Mock FastAPI's background task adder
@patch('mcp_server.mcp_server_instance.file_watcher') # Mock instance attribute
@patch('mcp_server.mcp_server_instance.indexer') # Mock instance attribute
async def test_index_trigger_force_reindex(mock_indexer, mock_file_watcher, mock_add_task, client: httpx.AsyncClient): # Rename mock
    settings = mcp_server_instance.settings # Get settings to access project_path
    """Test triggering index with force_reindex=True."""
    with patch('mcp_server.mcp_server_instance.status', "Watching"): # Patch instance status
        response = await client.post("/index", json={"project_path": settings.project_path, "force_reindex": True})

    assert response.status_code == 200
    # Check for the actual message format
    assert f"Indexing process initiated for {settings.project_path} in the background." == response.json()["message"]
    # Note: clear_index is called within the background task (_perform_scan),
    # so asserting it here immediately after the endpoint call will fail.
    # We rely on asserting that add_task was called with the correct arguments.
    # mock_indexer.clear_index.assert_called_once_with(mcp_server_instance.project_path) # Removed this assertion
    mock_add_task.assert_called_once() # Verify scan task was added
    # Check the arguments passed to add_task
    task_func = mock_add_task.call_args[0][0]
    task_kwargs = mock_add_task.call_args[1]
    assert task_func == mcp_server_instance._perform_scan
    assert task_kwargs.get("force_reindex") is True
    # Similar check for the task function as in test_index_trigger

@pytest.mark.asyncio
@patch('mcp_server.mcp_server_instance.indexer') # Mock instance attribute
async def test_search(mock_indexer, client: httpx.AsyncClient):
    import json # Add json import for stringifying metadata
    """Test the search endpoint."""
    mock_search_results = [
        {
            "document_id": "test/file1.py::0",
            "file_path": "test/file1.py",
            "content_hash": "hash1",
            "last_modified_timestamp": 1678886400.0,
            "extracted_text_chunk": "content 1",
            "metadata_json": json.dumps({"some": "data1"}), # Provide as JSON string
            "metadata": {"some": "data1"}, # Example metadata
            "vector": [0.1, 0.2] # Example vector
        },
        {
            "document_id": "test/file2.py::0",
            "file_path": "test/file2.py",
            "content_hash": "hash2",
            "last_modified_timestamp": 1678886401.0,
            "extracted_text_chunk": "content 2",
            "metadata_json": json.dumps({"some": "data2"}), # Provide as JSON string
            "metadata": {"some": "data2"},
            "vector": [0.3, 0.4]
        },
    ]
    # Adjust mock to return SearchResult objects if the endpoint expects them
    # Assuming the endpoint converts SearchResult to dict based on Pydantic model
    mock_indexer.search.return_value = mock_search_results # Keep as dict if endpoint returns dicts

    # Assume server is in a state where search is allowed (e.g., WATCHING)
    with patch('mcp_server.mcp_server_instance.status', "Watching"): # Patch instance status
        response = await client.post("/search", json={"query": "test query", "top_k": 5})

    assert response.status_code == 200
    data = response.json()
    # Compare based on the expected JSON output structure
    # We need to compare against the *expected output* of the API,
    # which has 'metadata' as a dict, not 'metadata_json' as a string.
    expected_api_results = [item | {"metadata": json.loads(item.pop("metadata_json"))} for item in mock_search_results]
    assert data["results"] == expected_api_results # Compare the list within the 'results' key
    mock_indexer.search.assert_called_once_with(query_text="test query", top_k=5) # Match actual kwarg

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