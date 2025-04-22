import logging
import asyncio
from fastapi import FastAPI

from .mcp_server import MCPServer
from .routers import index as index_router
from .routers import status as status_router
from .routers import search as search_router
from . import dependencies

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
log = logging.getLogger(__name__)


app = FastAPI(title="MCP Indexing Server")


@app.get("/", tags=["General"])
async def root():
    """Root endpoint providing basic server information."""
    return {"message": "MCP Indexing Server is running"}


app.include_router(index_router.router)
app.include_router(status_router.router)
app.include_router(search_router.router)


@app.on_event("startup")
async def startup_event():
    """Initializes the MCPServer instance when the app starts."""
    # No longer need global here, we'll modify the imported module's variable
    log.info("Application startup event triggered. Initializing MCPServer...")
    try:
        dependencies.mcp_server_instance = MCPServer()
        # Start the potentially long-running initialization in the background
        asyncio.create_task(dependencies.mcp_server_instance._initialize_dependencies())
        log.info("MCPServer instance created. Background initialization started.")
    except Exception as e:
        log.critical(
            f"Failed to create MCPServer instance during startup: {e}", exc_info=True
        )
        # Depending on severity, you might want to prevent the app from fully starting
        # or raise an error here. For now, we log critically.
        # raise RuntimeError("Critical error during MCPServer initialization") from e


@app.on_event("shutdown")
async def shutdown_event():
    """Handles application shutdown events."""
    log.info("Application shutdown event triggered.")
    # Ensure instance exists in dependencies module before calling shutdown
    if dependencies.mcp_server_instance:
        dependencies.mcp_server_instance.shutdown()
    else:
        log.warning("Shutdown event: MCPServer instance was not initialized.")
