import pytest
import pytest_asyncio
import httpx
import os
import shutil
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from vector_index_mcp.main import app
from vector_index_mcp.models import FileMetadata

TESTS_DIR = Path(__file__).parent
TEST_PROJECT_DIR = TESTS_DIR / "test_project"
TEST_LANCEDB_PATH = TESTS_DIR / "test_lancedb"
TEST_PROJECT_FILE = TEST_PROJECT_DIR / "test_file.py"


@pytest.fixture(scope="function")
def test_server_instance(monkeypatch):
    """
    Sets up a clean test environment for each test function.
    - Creates test directories (project, lancedb).
    - Creates a dummy file in the test project.
    - Sets environment variables for settings override.
    - Cleans up directories and resets state after the test.
    """
    # --- Setup ---
    if TEST_PROJECT_DIR.exists():
        shutil.rmtree(TEST_PROJECT_DIR)
    if TEST_LANCEDB_PATH.exists():
        shutil.rmtree(TEST_LANCEDB_PATH)

    TEST_PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    TEST_LANCEDB_PATH.mkdir(parents=True, exist_ok=True)
    TEST_PROJECT_FILE.write_text("def hello():\n    print('hello world')\n")

    monkeypatch.setenv("PROJECT_PATH", str(TEST_PROJECT_DIR))
    monkeypatch.setenv("LANCEDB_PATH", str(TEST_LANCEDB_PATH.resolve()))
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    from vector_index_mcp.mcp_server import MCPServer

    test_server_instance = MCPServer()

    with patch("vector_index_mcp.dependencies.mcp_server_instance", test_server_instance), patch.object(
        test_server_instance, "status", "Initializing", create=True
    ), patch.object(
        test_server_instance, "file_watcher", None, create=True
    ), patch.object(test_server_instance, "indexer", None, create=True):
        # Note: We don't need to patch settings or project_path on the instance itself
        # because the test_server_instance was created *with* the correct settings
        # due to the monkeypatched environment variables.

        yield test_server_instance

    # --- Teardown (after yield) ---
    with patch("vector_index_mcp.dependencies.mcp_server_instance", None):
        pass


@pytest_asyncio.fixture(scope="function")
async def client():
    """Provides an HTTPX async client for testing the FastAPI app."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
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
    assert response.json() == {"message": "MCP Indexing Server is running"}


# Add more tests below...


@pytest.mark.asyncio
async def test_status_initial(client: httpx.AsyncClient, test_server_instance):
    """Test initial status endpoint (assuming INITIALIZING from patch)."""
    settings = test_server_instance.settings
    response = await client.get(f"/status/{settings.project_path}/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "Initializing"
    assert data["project_path"] == settings.project_path
    assert "indexed_chunk_count" in data


@pytest.mark.asyncio
async def test_status_incorrect_path(client: httpx.AsyncClient, test_server_instance):
    """Test status endpoint with an incorrect project path."""
    incorrect_path = "nonexistent%2Fpath"
    response = await client.get(f"/status/{incorrect_path}")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "Not Found"
    assert data["project_path"] == incorrect_path.replace("%2F", "/")
    assert "not managed by this server instance" in data["error_message"]


@pytest.mark.asyncio
@patch("vector_index_mcp.dependencies.mcp_server_instance.indexer")
async def test_status_chunk_count(
    mock_indexer, client: httpx.AsyncClient, test_server_instance
):
    """Test status endpoint reports chunk count from indexer."""
    mock_indexer.get_indexed_chunk_count.return_value = 123
    settings = test_server_instance.settings

    with patch("vector_index_mcp.dependencies.mcp_server_instance.status", "Watching"):
        response = await client.get(f"/status/{settings.project_path}/")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "Watching"
    assert data["project_path"] == settings.project_path
    assert data["indexed_chunk_count"] == 123
    mock_indexer.get_indexed_chunk_count.assert_called_once()


@pytest.mark.asyncio
@patch("fastapi.BackgroundTasks.add_task")
@patch("vector_index_mcp.dependencies.mcp_server_instance.file_watcher")
async def test_index_trigger(
    mock_file_watcher, mock_add_task, client: httpx.AsyncClient, test_server_instance
):
    """Test triggering the index endpoint successfully."""
    settings = test_server_instance.settings
    with patch("vector_index_mcp.dependencies.mcp_server_instance.status", "Watching"):
        response = await client.post(
            "/index",
            json={"project_path": settings.project_path, "force_reindex": False},
        )

    assert response.status_code == 202
    assert (
        f"Indexing process initiated for {settings.project_path} in the background."
        == response.json()["message"]
    )
    mock_add_task.assert_called_once()
    task_func = mock_add_task.call_args[0][0]
    task_kwargs = mock_add_task.call_args[1]
    assert task_func == test_server_instance._perform_scan
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
async def test_index_trigger_conflict(client: httpx.AsyncClient, test_server_instance):
    """Test triggering index endpoint when already scanning."""
    settings = test_server_instance.settings
    with patch("vector_index_mcp.dependencies.mcp_server_instance.status", "Scanning"):
        response = await client.post(
            "/index",
            json={"project_path": settings.project_path, "force_reindex": False},
        )

    assert response.status_code == 409
    assert "already in progress" in response.json()["detail"]


@pytest.mark.asyncio
@patch("fastapi.BackgroundTasks.add_task")
@patch("vector_index_mcp.dependencies.mcp_server_instance.file_watcher")
@patch("vector_index_mcp.dependencies.mcp_server_instance.indexer")
async def test_index_trigger_force_reindex(
    mock_indexer,
    mock_file_watcher,
    mock_add_task,
    client: httpx.AsyncClient,
    test_server_instance,
):
    settings = test_server_instance.settings
    """Test triggering index with force_reindex=True."""
    with patch("vector_index_mcp.dependencies.mcp_server_instance.status", "Watching"):
        response = await client.post(
            "/index",
            json={"project_path": settings.project_path, "force_reindex": True},
        )

    assert response.status_code == 202
    assert (
        f"Indexing process initiated for {settings.project_path} in the background."
        == response.json()["message"]
    )
    # Note: clear_index is called within the background task (_perform_scan),
    # so asserting it here immediately after the endpoint call will fail.
    mock_add_task.assert_called_once()
    task_func = mock_add_task.call_args[0][0]
    task_kwargs = mock_add_task.call_args[1]
    assert task_func == test_server_instance._perform_scan
    assert task_kwargs.get("force_reindex") is True


@pytest.mark.asyncio
@patch("vector_index_mcp.dependencies.mcp_server_instance.indexer")
async def test_search(mock_indexer, client: httpx.AsyncClient, test_server_instance):
    """Test the search endpoint."""
    mock_search_results = [
        {
            "document_id": "test/file1.py::0",
            "file_path": "test/file1.py",
            "content_hash": "hash1",
            "last_modified_timestamp": 1678886400.0,
            "extracted_text_chunk": "content 1",
            "metadata": FileMetadata(original_path="test/file1.py"),
            "vector": [0.1, 0.2],
        },
        {
            "document_id": "test/file2.py::0",
            "file_path": "test/file2.py",
            "content_hash": "hash2",
            "last_modified_timestamp": 1678886401.0,
            "extracted_text_chunk": "content 2",
            "metadata": FileMetadata(original_path="test/file2.py"),
            "vector": [0.3, 0.4],
        },
    ]
    mock_indexer.search.return_value = mock_search_results

    with patch("vector_index_mcp.dependencies.mcp_server_instance.status", "Watching"):
        response = await client.post(
            "/search", json={"query": "test query", "top_k": 5}
        )

    assert response.status_code == 200
    data = response.json()
    expected_api_results = []
    for item in mock_search_results:
        expected_item = item.copy()
        expected_item.pop("vector", None)

        if isinstance(expected_item["metadata"], FileMetadata):
            expected_item["metadata"] = expected_item["metadata"].model_dump()
        expected_api_results.append(expected_item)
    assert data["results"] == expected_api_results
    mock_indexer.search.assert_called_once_with(query_text="test query", top_k=5)


# TODO: Implement test_status_during_indexing if needed, requires careful async/mock handling.
