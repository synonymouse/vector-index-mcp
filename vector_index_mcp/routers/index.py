import logging
from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends
from ..mcp_server import MCPServer, ServerStatus
from .. import dependencies

# Import models and dependency provider from main
from ..models import IndexRequest

log = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/index", tags=["Indexing"], status_code=202
)  # Use 202 Accepted for background tasks
async def trigger_index(
    request: IndexRequest,
    background_tasks: BackgroundTasks,
    server_instance: MCPServer = Depends(dependencies.get_server_instance),
):
    """
    Triggers the indexing process for the configured project path.
    NOTE: This endpoint currently only operates on the single project path
    defined in the server's settings, ignoring the 'project_path' in the request body
    if it differs. A 409 is returned if a scan is already running.
    """
    # Validate request path against server's configured path
    if request.project_path != server_instance.project_path:
        # Log a warning but proceed with the configured path.
        # Alternatively, return a 400 Bad Request error.
        log.warning(
            f"Index request received for path '{request.project_path}', "
            f"but server is configured for '{server_instance.project_path}'. "
            f"Proceeding with configured path."
        )
        # TODO: Decide if a 400 Bad Request should be raised instead if paths mismatch.
        # raise HTTPException(status_code=400, detail=f"Server only handles path: {server_instance.project_path}")

    path_to_index = server_instance.project_path  # Explicitly use configured path

    # Check server readiness before proceeding
    if server_instance.status == ServerStatus.INITIALIZING:
        raise HTTPException(
            status_code=503, detail="Server is initializing, please try again later."
        )
    elif server_instance.status == ServerStatus.ERROR:
        error_msg = (
            f"Server initialization failed: {server_instance.initialization_error}"
            if server_instance.initialization_error
            else "Server initialization failed."
        )
        raise HTTPException(status_code=500, detail=error_msg)
    elif server_instance.status == ServerStatus.SCANNING:
        raise HTTPException(
            status_code=409, detail="A scan is already in progress."
        )

    force_reindex = request.force_reindex

    log.info(
        f"Received index request for {path_to_index}, force_reindex={force_reindex}. Adding to background tasks."
    )
    # Add the scan task to be run in the background
    # Add the scan task to be run in the background using the injected instance
    background_tasks.add_task(
        server_instance._perform_scan,
        project_path=path_to_index,
        force_reindex=force_reindex,
    )
    # Return immediate confirmation
    return {
        "message": f"Indexing process initiated for {path_to_index} in the background."
    }
