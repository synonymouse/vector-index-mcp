import sys
import logging
import json
from contextlib import asynccontextmanager
from mcp.server.fastmcp import FastMCP
from vector_index_mcp.mcp_server import MCPServer

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan_manager(mcp_app: FastMCP):
    """
    Asynchronous context manager to manage the lifecycle of the MCPServer instance.
    It initializes the server on startup and shuts it down on exit.
    The MCPServer instance is made available via `mcp_app.mcp_server`.
    """
    # project_path_from_cli will be accessed from mcp_app
    project_path_from_cli = mcp_app.cli_project_path  # Get from mcp_app directly
    log.info(
        f"Lifespan: Initializing MCPServer with project_path: {project_path_from_cli}"
    )

    mcp_server_instance = MCPServer(project_path=project_path_from_cli)
    await mcp_server_instance._initialize_dependencies()

    mcp_app.mcp_server = mcp_server_instance  # Assign to mcp_app directly
    log.info("Lifespan: MCPServer initialized and assigned to mcp_app.mcp_server.")
    log.debug(f"Lifespan: mcp_app.mcp_server is now: {mcp_app.mcp_server}")

    try:
        log.debug("Lifespan: Yielding to FastMCP to start processing requests.")
        yield
    finally:
        log.info("Lifespan: Shutting down MCPServer...")
        if (
            hasattr(mcp_app, "mcp_server") and mcp_app.mcp_server
        ):  # Check on mcp_app directly
            await mcp_app.mcp_server.shutdown()  # Access from mcp_app directly
        log.info("Lifespan: MCPServer has shut down.")


mcp = FastMCP(
    name="vector-index-mcp", version="0.2.1", lifespan=lifespan_manager
)  # Updated version slightly


@mcp.tool(
    name="trigger_index",
    description="Triggers the indexing process for the project. Can force re-indexing.",
)
async def trigger_index_tool(force_reindex: bool = False) -> dict:
    """
    MCP tool to trigger the indexing or re-indexing of project files.
    """
    try:
        mcp_server = mcp.mcp_server  # Access mcp_server directly from mcp
        if not mcp_server:
            log.error("trigger_index_tool: MCPServer is not initialized.")
            raise RuntimeError("MCPServer is not initialized.")
        # _scan_project_files expects project_path, which is part of mcp_server instance
        log.info(
            f"trigger_index_tool: Triggering scan with force_reindex={force_reindex}"
        )
        await mcp_server._scan_project_files(
            project_path=mcp_server.project_path, force_reindex=force_reindex
        )
        return {
            "content": [{"type": "text", "text": "Indexing successfully triggered."}],
            "isError": False,
        }
    except Exception as e:
        log.error(f"Error in trigger_index_tool: {e}", exc_info=True)
        return {
            "content": [
                {"type": "text", "text": f"Error triggering indexing: {str(e)}"}
            ],
            "isError": True,
        }


@mcp.tool(
    name="get_status",
    description="Gets the current status of the MCP server and indexer.",
)
async def get_status_tool() -> dict:
    """
    MCP tool to retrieve the current status of the server and indexer.
    """
    try:
        mcp_server = mcp.mcp_server  # Access mcp_server directly from mcp
        if not mcp_server:
            log.error("get_status_tool: MCPServer is not initialized.")
            raise RuntimeError("MCPServer is not initialized.")
        log.debug("get_status_tool: Fetching current status.")
        status_data = await mcp_server.get_current_status()
        return {
            "content": [{"type": "text", "text": json.dumps(status_data)}],
            "isError": False,
        }
    except Exception as e:
        log.error(f"Error in get_status_tool: {e}", exc_info=True)
        return {
            "content": [{"type": "text", "text": f"Error getting status: {str(e)}"}],
            "isError": True,
        }


@mcp.tool(
    name="search_index", description="Searches the vector index for a given query."
)
async def search_index_tool(query: str, top_k: int = 5) -> dict:
    """
    MCP tool to perform a search in the vector index.
    """
    try:
        mcp_server = mcp.mcp_server  # Access mcp_server directly from mcp
        if not mcp_server:
            log.error("search_index_tool: MCPServer is not initialized.")
            raise RuntimeError("MCPServer is not initialized.")
        log.info(
            f"search_index_tool: Performing search for query='{query}', top_k={top_k}"
        )
        results = await mcp_server.perform_search(query_text=query, top_k=top_k)
        return {
            "content": [{"type": "text", "text": json.dumps(results)}],
            "isError": False,
        }
    except Exception as e:
        log.error(f"Error in search_index_tool: {e}", exc_info=True)
        return {
            "content": [{"type": "text", "text": f"Error performing search: {str(e)}"}],
            "isError": True,
        }


def main():
    """
    Main entry point for the MCP server.
    Parses command-line arguments for the project path and starts the server.
    """
    if len(sys.argv) < 2:
        log.critical(
            "Error: Project path not specified."
        )  # Changed to critical as it prevents server start
        print("Usage: python -m vector_index_mcp.main_mcp <project_path>")
        sys.exit(1)

    project_path = sys.argv[1]
    mcp.cli_project_path = project_path  # Save for lifespan manager directly on mcp

    log.info(
        f"MCP Main: Project path set to '{project_path}'. About to call mcp.run()."
    )
    mcp.run(transport="stdio")
    log.info(
        "MCP Main: mcp.run() has exited."
    )  # This line might only be reached on clean shutdown


if __name__ == "__main__":
    main()
