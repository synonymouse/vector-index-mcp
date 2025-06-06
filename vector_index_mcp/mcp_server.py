import asyncio
import logging
import os
import threading
import time
from enum import Enum, auto
from typing import Optional, Any

from .file_watcher import FileWatcher
from .indexer import Indexer
from .models import Settings

log = logging.getLogger(__name__)


class ServerStatus(Enum):
    """Represents the initialization status of the server."""

    INITIALIZING = auto()  # Server is starting up, indexer not ready
    SCANNING = auto()  # Indexer is actively scanning/processing files
    WATCHING = auto()  # Indexer is ready and watching for file changes
    READY = auto()  # Alias for WATCHING, indicating operational state
    ERROR = auto()  # Server encountered an unrecoverable error during init


class MCPServer:
    """Core class managing indexing state, file watching, and scanning logic."""

    def __init__(self, project_path: str):
        """
        Initializes the MCPServer.

        Args:
            project_path: The root path of the project to be indexed and monitored.
        """
        self.project_path = project_path
        self.settings = Settings(project_path=self.project_path)

        self.indexer: Optional[Indexer] = None
        self.status: ServerStatus = ServerStatus.INITIALIZING
        self.initialization_error: Optional[Exception] = None

        # Calculate absolute LanceDB path to ensure it's ignored by the watcher
        lancedb_uri_str = str(self.settings.lancedb_uri)
        # os.path.join correctly handles lancedb_uri_str being absolute or relative.
        self.abs_lancedb_path = os.path.realpath(
            os.path.join(self.project_path, lancedb_uri_str)
        )
        log.debug(
            f"Canonical absolute LanceDB path for watcher ignore: {self.abs_lancedb_path}"
        )

        self.file_watcher = FileWatcher(
            project_path=self.project_path,
            indexer=None,  # Will be set after Indexer is initialized
            event_loop=None,  # Will be set after event loop is running
            ignore_patterns=list(
                self.settings.ignore_patterns
            ),  # Pass original patterns from settings
            abs_lancedb_path_to_ignore=self.abs_lancedb_path,
        )
        self.last_scan_start_time: Optional[float] = None
        self.last_scan_end_time: Optional[float] = None
        self.current_error: Optional[str] = None
        self.watcher_thread = None

        log.info(f"Monitoring project path: {self.project_path}")

    async def _initialize_dependencies(self):
        """
        Initializes asynchronous dependencies, primarily the Indexer,
        and sets the server status accordingly. Starts the file watcher thread
        upon successful initialization.
        """
        log.info("Starting MCPServer dependencies initialization...")
        try:
            indexer = Indexer(self.settings)

            await indexer.load_resources()  # Load any async resources for the indexer

            self.indexer = indexer
            self.file_watcher.indexer = self.indexer  # Provide the initialized indexer
            try:
                self.file_watcher.event_loop = (
                    asyncio.get_running_loop()
                )  # Provide the running event loop
                log.debug(
                    "MCPServer._initialize_dependencies: Event loop assigned to FileWatcher."
                )
            except RuntimeError as e:
                log.error(
                    f"MCPServer._initialize_dependencies: Could not get running event loop: {e}. FileWatcher may not function correctly for async tasks.",
                    exc_info=True,
                )
                # Depending on strictness, could raise here or allow FileWatcher to operate in a limited mode / log errors later.

            log.debug(
                "MCPServer._initialize_dependencies: Indexer and FileWatcher configured. About to set status to READY."
            )
            self.status = ServerStatus.READY
            log.info(
                "MCPServer dependencies initialized successfully. Server is READY."
            )
            log.debug(
                "MCPServer._initialize_dependencies: Status set to READY. About to start watcher thread."
            )
            # _start_watcher_thread is a synchronous method that starts a new thread.
            self._start_watcher_thread()
            log.debug(
                "MCPServer._initialize_dependencies: Watcher thread start initiated."
            )

        except Exception as e:
            log.critical(
                f"Fatal error during MCPServer dependency initialization: {e}",
                exc_info=True,
            )
            self.initialization_error = e
            self.status = ServerStatus.ERROR

    def _start_watcher_thread(self):
        """
        Starts the file watcher in a separate daemon thread.
        Logs a warning if the thread is already running.
        """
        if self.watcher_thread is not None and self.watcher_thread.is_alive():
            log.warning(
                "Attempted to start file watcher thread, but it is already running."
            )
            return

        log.info("Starting file watcher thread...")
        self.watcher_thread = threading.Thread(
            target=self.file_watcher.start,  # The method to be executed in the new thread
            daemon=True,  # Ensures thread exits when the main program exits
        )
        self.watcher_thread.start()
        self.status = ServerStatus.WATCHING  # Update server status
        log.info(
            "File watcher thread started. Server is now WATCHING. "
            "An initial scan may be required via the 'trigger_index_tool'."
        )

    async def _scan_project_files(self, project_path: str, force_reindex: bool):
        """
        Internal method to run the indexing scan for the configured project path.
        This method updates the server's status and handles logging for the scan process.

        Args:
            project_path: The path to the project to scan. Currently, must match the server's configured project_path.
            force_reindex: If True, clears the existing index before scanning.

        Raises:
            ValueError: If `project_path` does not match the server's configured project path.
            RuntimeError: If a scan is already in progress.
            Exception: Re-raises exceptions that occur during the indexing process.
        """
        # NOTE: This server instance is designed to handle one project_path at a time.
        if project_path != self.project_path:
            log.error(
                f"Scan requested for '{project_path}', but server is configured for '{self.project_path}'. Request denied."
            )
            self.current_error = f"Scan requested for unsupported path: {project_path}"
            raise ValueError(f"Scan requested for unsupported path: {project_path}")

        if self.status == ServerStatus.SCANNING:
            log.warning("Scan request ignored: another scan is already in progress.")
            raise RuntimeError(
                "Scan request ignored: another scan is already in progress."
            )

        log.info(
            f"Starting project file scan for '{project_path}'. force_reindex={force_reindex}"
        )
        self.status = ServerStatus.SCANNING
        self.last_scan_start_time = time.time()
        self.last_scan_end_time = None
        self.current_error = None  # Clear previous errors before a new scan
        try:
            if force_reindex:
                log.info(
                    f"Force re-index: Clearing existing index for '{project_path}'..."
                )
                await asyncio.to_thread(
                    self.indexer.clear_index, project_path
                )  # Ensure clear_index is thread-safe
                log.info(f"Index successfully cleared for '{project_path}'.")

            log.info(f"Running file system scan and indexing for '{project_path}'...")
            await asyncio.to_thread(
                self.file_watcher.initial_scan
            )  # initial_scan should handle its own detailed file logging
            self.last_scan_end_time = time.time()
            duration = self.last_scan_end_time - self.last_scan_start_time
            self.status = (
                ServerStatus.WATCHING
            )  # After scan, server returns to watching state
            log.info(
                f"Project file scan completed for '{project_path}' in {duration:.2f} seconds. Server is now WATCHING."
            )
            # The calling tool is responsible for returning a success message to the user.

        except Exception as e:
            self.status = (
                ServerStatus.ERROR
            )  # If scan fails, server is in an error state regarding indexing
            self.current_error = f"Indexing scan failed: {str(e)}"
            self.last_scan_end_time = time.time()
            log.error(
                f"Critical error during file scan for '{project_path}': {e}",
                exc_info=True,
            )
            raise  # Re-raise to allow the calling tool to report the failure

    async def shutdown(self):
        """
        Gracefully shuts down the MCPServer, stopping the file watcher and joining its thread.
        """
        log.info("MCPServer shutdown process initiated...")
        if self.file_watcher:
            log.info("Stopping file watcher...")
            await asyncio.to_thread(self.file_watcher.stop)
            log.info("File watcher stop signal sent.")
        if self.watcher_thread and self.watcher_thread.is_alive():
            log.info("Joining file watcher thread...")
            await asyncio.to_thread(
                self.watcher_thread.join, timeout=5
            )  # Increased timeout slightly
            if self.watcher_thread.is_alive():
                log.warning("File watcher thread did not exit cleanly after timeout.")
            else:
                log.info("File watcher thread successfully joined.")
        log.info("MCPServer shutdown complete.")

    async def get_current_status(self) -> dict[str, Any]:
        """
        Collects and returns the current operational status of the MCPServer,
        including project path, server status, scan times, and index statistics.
        """
        log.debug("Fetching current server status...")
        indexed_chunk_count = None
        if self.indexer:
            try:
                indexed_chunk_count = await self.indexer.get_indexed_chunk_count()
            except Exception as e:
                log.error(f"Failed to retrieve indexed chunk count: {e}", exc_info=True)

        error_message_to_report = None
        if self.current_error:
            error_message_to_report = self.current_error
        elif self.initialization_error:  # Fallback to initialization error if present
            error_message_to_report = (
                f"Initialization Error: {str(self.initialization_error)}"
            )

        status_payload = {
            "project_path": self.project_path,
            "status": self.status.name,
            "last_scan_start_time": self.last_scan_start_time,
            "last_scan_end_time": self.last_scan_end_time,
            "indexed_chunk_count": indexed_chunk_count,
            "error_message": error_message_to_report,
        }
        log.debug(f"Current server status: {status_payload}")
        return status_payload

    async def perform_search(self, query_text: str, top_k: int) -> list[dict[str, Any]]:
        """
        Performs a search query against the vector index.

        Args:
            query_text: The text to search for.
            top_k: The maximum number of results to return.

        Returns:
            A list of search results.

        Raises:
            RuntimeError: If the indexer is not available or if the search operation fails.
        """
        if not self.indexer:
            log.error("Search request failed: Indexer is not available.")
            raise RuntimeError(
                "Indexer not available. Please initialize the server or check its status."
            )

        # READY is an alias for WATCHING. Allow search if watching or ready.
        if self.status not in [
            ServerStatus.WATCHING,
            ServerStatus.READY,
            ServerStatus.SCANNING,
        ]:  # Allow search even if scanning, though results might be partial
            log.warning(
                f"Performing search while server status is '{self.status.name}'. Results may be incomplete or reflect ongoing indexing."
            )
            # No error raised, allow search but warn.

        try:
            log.info(f"Performing search for query: '{query_text}', top_k={top_k}")
            # self.indexer.search is now an async method
            results = await self.indexer.search(query_text=query_text, top_k=top_k)
            log.info(f"Search for '{query_text}' returned {len(results)} results.")
            return results
        except Exception as e:
            log.error(
                f"Error during search operation for query '{query_text}': {e}",
                exc_info=True,
            )
            raise RuntimeError(f"Search failed: {str(e)}")
