import logging
from fastapi import FastAPI, Depends

from mcp_server import MCPServer
from indexer import Indexer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
log = logging.getLogger(__name__)

mcp_server_instance: MCPServer = None

def get_server_instance() -> MCPServer:
    """Dependency to get the global MCPServer instance."""
    if mcp_server_instance is None:
        # This should not happen in normal operation after app startup
        log.critical("MCPServer instance not initialized before request.")
        raise RuntimeError("Server instance is not available.")
    return mcp_server_instance

def get_indexer() -> Indexer:
    """Dependency to get the Indexer instance from the MCPServer."""
    server = get_server_instance() # Get instance via the other dependency
    if not hasattr(server, 'indexer') or server.indexer is None:
        log.error("Indexer not available in MCPServer instance.")
        raise RuntimeError("Indexer service is unavailable.")
    return server.indexer

from routers import index as index_router
from routers import status as status_router
from routers import search as search_router


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
    global mcp_server_instance
    log.info("Application startup event triggered. Initializing MCPServer...")
    try:
        mcp_server_instance = MCPServer()
        log.info("MCPServer instance initialized successfully.")
    except Exception as e:
        log.critical(f"Failed to initialize MCPServer during startup: {e}", exc_info=True)
        # Depending on severity, you might want to prevent the app from fully starting
        # or raise an error here. For now, we log critically.
        # raise RuntimeError("Critical error during MCPServer initialization") from e

@app.on_event("shutdown")
async def shutdown_event():
    """Handles application shutdown events."""
    log.info("Application shutdown event triggered.")
    # Ensure instance exists before calling shutdown
    if mcp_server_instance:
        mcp_server_instance.shutdown()
    else:
        log.warning("Shutdown event: MCPServer instance was not initialized.")
