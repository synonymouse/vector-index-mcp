import logging
import os

from fastapi import APIRouter, Depends, HTTPException

from .. import dependencies
from ..mcp_server import MCPServer, ServerStatus

# Import models and dependency provider
from ..models import IndexingStatusResponse

log = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/status/{project_path:path}",
    response_model=IndexingStatusResponse,
    tags=["Status"],
)
async def get_indexing_status(
    project_path: str,
    server_instance: MCPServer = Depends(dependencies.get_server_instance),
):
    """
    Gets the current status of the MCP server instance for the specified project path.

    Returns the status if the `project_path` matches the server's configuration.
    Otherwise, returns a 404 Not Found error.
    """
    # Validate project path by comparing absolute paths
    req_abs_path = os.path.abspath(project_path)
    srv_abs_path = os.path.abspath(server_instance.settings.project_path)
    if req_abs_path != srv_abs_path:
        log.warning(f"Path mismatch: Request='{req_abs_path}', Server='{srv_abs_path}'")
        raise HTTPException(
            status_code=404,
            detail="Project path not found or not managed by this server.",
        )

    chunk_count = None
    error_msg = None

    if server_instance.indexer:
        try:
            chunk_count = server_instance.indexer.get_indexed_chunk_count()
        except Exception as e:
            log.error(f"Error getting chunk count for status: {e}")
            # Reflect this as an issue, maybe set status to ERROR? For now, just log.
            error_msg = f"Failed to retrieve chunk count: {e}"

    # Determine error message based on status if not already set by chunk count retrieval
    if error_msg is None:
        if server_instance.status == ServerStatus.READY and not server_instance.indexer:
            log.error("Server status is READY but indexer is not available.")
            error_msg = (
                "Server is READY but indexer is missing."  # Indicate inconsistency
            )
        elif server_instance.status == ServerStatus.ERROR:
            error_msg = (
                str(server_instance.initialization_error)
                if server_instance.initialization_error
                else "Unknown server error."
            )

    # Construct the response using the current state from the MCPServer instance
    return IndexingStatusResponse(
        project_path=server_instance.settings.project_path, # Always return the configured path
        status=server_instance.status.name if isinstance(server_instance.status, ServerStatus) else server_instance.status,
        last_scan_start_time=server_instance.last_scan_start_time,
        last_scan_end_time=server_instance.last_scan_end_time,
        indexed_chunk_count=chunk_count,
        error_message=error_msg,
    )
