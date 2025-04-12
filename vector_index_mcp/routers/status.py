import logging
from pathlib import Path
from fastapi import APIRouter, Depends

# Import models and dependency provider
from ..models import IndexingStatusResponse
from ..dependencies import get_server_instance
from ..mcp_server import MCPServer

log = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/status/{project_path:path}",
    response_model=IndexingStatusResponse,
    tags=["Status"],
)
async def get_indexing_status(
    project_path: str,
    server_instance: MCPServer = Depends(get_server_instance),
):
    """
    Gets the current indexing status for the specified project path.
    NOTE: This endpoint currently only returns status for the single project path
    defined in the server's settings. Returns 'Not Found' status for other paths.
    """
    # URL decode might be needed if paths have special chars, FastAPI handles :path well
    # Normalize both paths for robust comparison
    try:
        # Use the injected instance's project path
        requested_path_norm = Path(project_path).resolve()
        server_path_norm = Path(server_instance.project_path).resolve()
    except Exception as e:
        # Handle potential errors during path resolution (e.g., invalid path chars)
        log.warning(f"Could not resolve requested path '{project_path}': {e}")
        # Return 'Not Found' or a more specific error like 400 Bad Request
        return IndexingStatusResponse(
            project_path=project_path,
            status="Error",
            error_message=f"Invalid project path provided: {project_path}",
        )

    # Check if the requested path matches the server's configured path
    if requested_path_norm != server_path_norm:
        # Construct the response using the current state from the injected MCPServer instance
        return IndexingStatusResponse(
            project_path=project_path,
            status="Not Found",
            error_message="Status requested for a path not managed by this server instance.",
        )

    # Get chunk count for the configured path using the injected instance
    chunk_count = None  # Default to None
    if server_instance.status not in [
        "Initializing",
        "Error",
        "Scanning",
        "Idle - Initial Scan Required",
    ]:
        # Only query count if index is expected to be stable/ready (i.e., 'Watching' or post-initial scan)
        try:
            # Access indexer via the injected server instance
            chunk_count = server_instance.indexer.get_indexed_chunk_count(
                server_instance.project_path
            )
        except Exception as e:
            # Handle potential errors during count retrieval
            log.error(f"Error getting chunk count for status: {e}")
            # Optionally update server status or error message here
            server_instance.current_error = f"Failed to retrieve chunk count: {e}"

    # Construct the response using the current state from the MCPServer instance
    return IndexingStatusResponse(
        project_path=server_instance.project_path,
        status=server_instance.status,
        last_scan_start_time=server_instance.last_scan_start_time,
        last_scan_end_time=server_instance.last_scan_end_time,
        indexed_chunk_count=chunk_count,
        error_message=server_instance.current_error,
    )
