import logging
from .mcp_server import MCPServer
from .indexer import Indexer

log = logging.getLogger(__name__)

# Global instance, managed by startup/shutdown events in main.py
mcp_server_instance: MCPServer | None = None


def get_server_instance() -> MCPServer:
    """Dependency to get the global MCPServer instance."""
    if mcp_server_instance is None:
        # This should not happen in normal operation after app startup
        log.critical("MCPServer instance not initialized before request.")
        raise RuntimeError("Server instance is not available.")
    return mcp_server_instance


def get_indexer() -> Indexer:
    """Dependency to get the Indexer instance from the MCPServer."""
    server = get_server_instance()
    if not hasattr(server, "indexer") or server.indexer is None:
        log.error("Indexer not available in MCPServer instance.")
        raise RuntimeError("Indexer service is unavailable.")
    return server.indexer
