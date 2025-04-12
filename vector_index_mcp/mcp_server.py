import threading
import time
import datetime
import logging
from typing import Optional


from .indexer import Indexer
from .file_watcher import FileWatcher
from .models import Settings

log = logging.getLogger(__name__)


class MCPServer:
    """Core class managing indexing state, file watching, and scanning logic."""

    def __init__(self):
        self.settings = Settings()
        self.indexer = Indexer(self.settings)
        self.file_watcher = FileWatcher(
            project_path=self.settings.project_path,
            indexer=self.indexer,
            ignore_patterns=self.settings.ignore_patterns,
        )
        self.project_path = self.settings.project_path
        self.status: str = "Idle - Initial Scan Required"
        self.last_scan_start_time: Optional[float] = None
        self.last_scan_end_time: Optional[float] = None
        self.current_error: Optional[str] = None
        self.watcher_thread = None

        log.info(f"MCPServer initialized with settings: {self.settings}")
        log.info(f"Monitoring project path: {self.project_path}")

        self._start_watcher_thread()

    def _start_watcher_thread(self):
        """Starts the file watcher thread."""
        if self.watcher_thread is not None and self.watcher_thread.is_alive():
            log.warning("Watcher thread already running.")
            return

        log.info("Starting file watcher thread...")
        self.watcher_thread = threading.Thread(
            target=self.file_watcher.start,
            daemon=True,
        )
        self.watcher_thread.start()
        self.status = "Watching"
        log.info(
            "File watcher thread started. Initial scan required via /index endpoint."
        )

    def _perform_scan(self, project_path: str, force_reindex: bool):
        """Internal method to run the indexing scan."""
        # NOTE: This currently only works for the configured self.project_path
        if project_path != self.project_path:
            log.warning(
                f"Scan requested for {project_path}, but server configured for {self.project_path}. Ignoring request."
            )
            self.current_error = f"Scan requested for unsupported path: {project_path}"
            self.status = "Error"
            return

        if self.status == "Scanning":
            log.warning("Scan request ignored, another scan is already in progress.")
            return

        log.info(f"Starting scan for {project_path}, force_reindex={force_reindex}")
        self.status = "Scanning"
        self.last_scan_start_time = time.time()
        self.last_scan_end_time = None
        self.current_error = None
        try:
            if force_reindex:
                log.info(f"Clearing existing index for {project_path}...")
                self.indexer.clear_index(project_path)
                log.info(f"Index cleared for {project_path}.")

            log.info(f"Running initial scan for {project_path}...")
            self.file_watcher.initial_scan()  # This blocks until scan is done
            self.last_scan_end_time = time.time()
            self.status = "Watching"  # Assume continuous watching after scan
            log.info(f"Scan completed for {project_path} at {datetime.datetime.now()}")

        except Exception as e:
            self.status = "Error"
            self.current_error = f"Indexing failed: {str(e)}"
            self.last_scan_end_time = time.time()
            log.error(f"ERROR during scan for {project_path}: {e}", exc_info=True)

    def shutdown(self):
        """Cleanup resources on shutdown."""
        log.info("Shutting down MCPServer...")
        if hasattr(self, "file_watcher") and self.file_watcher:
            self.file_watcher.stop()
            log.info("File watcher stopped.")
        if (
            hasattr(self, "watcher_thread")
            and self.watcher_thread
            and self.watcher_thread.is_alive()
        ):
            self.watcher_thread.join(timeout=2)
            if self.watcher_thread.is_alive():
                log.warning("Watcher thread did not exit cleanly.")
            else:
                log.info("Watcher thread joined.")
        log.info("MCPServer shutdown complete.")
