import os
import threading
import json # <-- Add json import
import time # <-- Add time import
import datetime # <-- Add datetime import
import json # <-- Keep json import
import threading # <-- Keep threading import
from typing import List, Dict, Any, Optional # <-- Add Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException # <-- Add BackgroundTasks, HTTPException
from pathlib import Path # <-- Add Path import
from dotenv import load_dotenv
from pydantic import BaseModel # Keep BaseModel for SearchRequest
from pydantic_settings import BaseSettings

from indexer import Indexer
from file_watcher import FileWatcher
# Import ALL necessary models from models.py
from models import IndexedDocument, Settings, IndexRequest, IndexingStatusResponse, SearchResultItem, SearchResponse, SearchRequest # <-- Added SearchRequest here

app = FastAPI()

# Load environment variables
load_dotenv()


# SearchRequest, SearchResultItem, SearchResponse, IndexRequest, IndexingStatusResponse
# are now imported from models.py. Remove definitions from here.

@app.get("/")
async def root():
    return {"message": "MCP Indexing Server"}

class MCPServer:
    def __init__(self):
        self.app = app
        self.settings = Settings()
        self.indexer = Indexer(self.settings)
        # NOTE: FileWatcher is tied to settings.project_path
        self.file_watcher = FileWatcher(
            project_path=self.settings.project_path,
            indexer=self.indexer,
            ignore_patterns=self.settings.ignore_patterns
        )
        self.search_module = None # Assuming this might be used later

        # --- State Tracking ---
        # NOTE: Simplified state for the single configured project path
        self.project_path = self.settings.project_path # Store the configured path
        self.status: str = "Initializing" # Initial status before first scan
        self.last_scan_start_time: Optional[float] = None
        self.last_scan_end_time: Optional[float] = None
        self.current_error: Optional[str] = None
        self.watcher_thread = None # Initialize watcher_thread attribute

        print(f"Loaded settings: {self.settings}")
        print(f"Monitoring project path: {self.project_path}")
        # Start initial scan and watch in background
        self._start_initial_scan_and_watch()

    def _perform_scan(self, project_path: str, force_reindex: bool):
        """Internal method to run the indexing scan."""
        # NOTE: This currently only works for the configured self.project_path
        if project_path != self.project_path:
             # In a multi-project setup, this would handle different paths
             print(f"Warning: Scan requested for {project_path}, but server configured for {self.project_path}. Ignoring request.")
             # Or raise an error, or adapt FileWatcher/Indexer
             self.current_error = f"Scan requested for unsupported path: {project_path}"
             self.status = "Error"
             return

        # Prevent concurrent scans
        if self.status == "Scanning":
            print("Scan request ignored, another scan is already in progress.")
            # Optionally update error state or just return
            return

        print(f"Starting scan for {project_path}, force_reindex={force_reindex}")
        self.status = "Scanning"
        self.last_scan_start_time = time.time()
        self.last_scan_end_time = None
        self.current_error = None
        try:
            if force_reindex:
                print(f"Clearing existing index for {project_path}...")
                self.indexer.clear_index(project_path)
                print(f"Index cleared for {project_path}.")

            print(f"Running initial scan for {project_path}...")
            # Assuming initial_scan processes the path given to FileWatcher
            self.file_watcher.initial_scan() # This blocks until scan is done
            self.last_scan_end_time = time.time()
            # If watcher is running continuously, status should be 'Watching'
            # If watcher only scans then stops, status could be 'Idle' or 'Completed'
            self.status = "Watching" # Assume continuous watching after scan
            print(f"Scan completed for {project_path} at {datetime.datetime.now()}")

        except Exception as e:
            self.status = "Error"
            self.current_error = f"Indexing failed: {str(e)}"
            self.last_scan_end_time = time.time() # Record end time even on error
            print(f"ERROR during scan for {project_path}: {e}") # Replace with logging

    def _start_initial_scan_and_watch(self):
        """Start the initial scan and continuous watching in background threads."""
        # Run initial scan in a separate thread first
        print("Starting initial background scan...")
        initial_scan_thread = threading.Thread(
            target=self._perform_scan,
            args=(self.project_path, False), # Initial scan, don't force reindex
            daemon=True
        )
        initial_scan_thread.start()

        # Start the continuous watcher thread.
        # Ensure it doesn't start processing events *before* the initial scan is done.
        # The FileWatcher's start() method likely blocks, so it's okay to start it.
        # If start() returns immediately, need careful synchronization.
        # Assuming file_watcher.start() blocks until stopped.
        if self.watcher_thread is None:
             print("Starting file watcher thread...")
             self.watcher_thread = threading.Thread(
                 target=self.file_watcher.start, # This runs observer.schedule() and observer.join()
                 daemon=True
             )
             self.watcher_thread.start()
             print("File watcher thread started.")
        else:
             print("File watcher thread already running.")


    def shutdown(self):
        """Cleanup resources on shutdown"""
        if hasattr(self, 'file_watcher') and self.file_watcher:
            self.file_watcher.stop()
        if hasattr(self, 'watcher_thread') and self.watcher_thread:
            self.watcher_thread.join(timeout=1)
# --- API Endpoints ---

@app.post("/index")
async def trigger_index(request: IndexRequest, background_tasks: BackgroundTasks):
    """
    Triggers the indexing process for the configured project path.
    NOTE: This endpoint currently only operates on the single project path
    defined in the server's settings, ignoring the 'project_path' in the request body
    if it differs. A 409 is returned if a scan is already running.
    """
    # Validate request path against server's configured path
    if request.project_path != mcp_server_instance.project_path:
        # Log a warning but proceed with the configured path.
        # Alternatively, return a 400 Bad Request error.
        print(f"Warning: Index request received for path '{request.project_path}', "
              f"but server is configured for '{mcp_server_instance.project_path}'. "
              f"Proceeding with configured path.")
        # path_to_index = mcp_server_instance.project_path # Use configured path
        # If strict path matching is required:
        # raise HTTPException(status_code=400, detail=f"Server only handles path: {mcp_server_instance.project_path}")

    path_to_index = mcp_server_instance.project_path # Explicitly use configured path

    # Check if a scan is already in progress
    if mcp_server_instance.status == "Scanning":
        raise HTTPException(status_code=409, detail="An indexing scan is already in progress.")

    force_reindex = request.force_reindex

    print(f"Received index request for {path_to_index}, force_reindex={force_reindex}. Adding to background tasks.")
    # Add the scan task to be run in the background
    background_tasks.add_task(
        mcp_server_instance._perform_scan,
        project_path=path_to_index,
        force_reindex=force_reindex
    )
    # Return immediate confirmation
    return {"message": f"Indexing process initiated for {path_to_index} in the background."}


@app.get("/status/{project_path:path}", response_model=IndexingStatusResponse)
async def get_indexing_status(project_path: str):
    """
    Gets the current indexing status for the specified project path.
    NOTE: This endpoint currently only returns status for the single project path
    defined in the server's settings. Returns 'Not Found' status for other paths.
    """
    # URL decode might be needed if paths have special chars, FastAPI handles :path well
    # Normalize both paths for robust comparison
    requested_path_norm = Path(project_path).resolve()
    server_path_norm = Path(mcp_server_instance.project_path).resolve()
    # Check if the requested path matches the server's configured path
    if requested_path_norm != server_path_norm:
        return IndexingStatusResponse(
            project_path=project_path,
            status="Not Found",
            error_message="Status requested for a path not managed by this server instance."
        )

    # Get chunk count for the configured path
    chunk_count = None # Default to None
    if mcp_server_instance.status not in ["Initializing", "Error", "Scanning"]:
        # Only query count if index is expected to be stable/ready
        try:
            chunk_count = mcp_server_instance.indexer.get_indexed_chunk_count(
                mcp_server_instance.project_path
            )
        except Exception as e:
            # Handle potential errors during count retrieval
            print(f"Error getting chunk count for status: {e}")
            # Optionally update server status or error message here
            mcp_server_instance.current_error = f"Failed to retrieve chunk count: {e}"


    # Construct the response using the current state from the MCPServer instance
    return IndexingStatusResponse(
        project_path=mcp_server_instance.project_path,
        status=mcp_server_instance.status,
        last_scan_start_time=mcp_server_instance.last_scan_start_time,
        last_scan_end_time=mcp_server_instance.last_scan_end_time,
        indexed_chunk_count=chunk_count,
        error_message=mcp_server_instance.current_error
    )


@app.post("/search", response_model=SearchResponse)
async def search_documents(request: SearchRequest):
    # NOTE: Search implicitly uses the index built for the configured project path.
    if mcp_server_instance.status == "Scanning":
         raise HTTPException(status_code=409, detail="Search unavailable: Indexing is currently in progress.")
    if mcp_server_instance.status == "Error":
         raise HTTPException(status_code=503, detail=f"Search unavailable due to indexing error: {mcp_server_instance.current_error}")
    if mcp_server_instance.status == "Initializing":
         raise HTTPException(status_code=503, detail="Search unavailable: Server is initializing.")


    try:
        # Get raw results (List[IndexedDocument]) from indexer
        raw_results = mcp_server_instance.indexer.search(
            query_text=request.query,
            top_k=request.top_k
        )

        # Process results for API response
        processed_results: List[SearchResultItem] = []
        for doc in raw_results:
            try:
                # Parse the metadata JSON string if it exists and is valid
                # Use dictionary access for mocked results
                metadata_json = doc.get('metadata_json') # Use .get for safety
                parsed_metadata = json.loads(metadata_json) if metadata_json else {}
            except (json.JSONDecodeError, TypeError):
                 # Handle cases where metadata might be invalid JSON or None
                parsed_metadata = {"error": "invalid or missing metadata format"}


            # Create the response item, copying fields and adding parsed metadata
            processed_results.append(
                SearchResultItem(
                    document_id=doc.get('document_id'), # Use .get for safety
                    file_path=doc.get('file_path'),
                    content_hash=doc.get('content_hash'),
                    last_modified_timestamp=doc.get('last_modified_timestamp'),
                    extracted_text_chunk=doc.get('extracted_text_chunk'),
                    metadata=parsed_metadata,
                    vector=doc.get('vector') # Use .get for safety
                )
            )

        return SearchResponse(results=processed_results)
    except Exception as e:
        # Keep existing error handling
        raise HTTPException(
            status_code=500,
            detail=f"Search failed: {str(e)}"
        )

# Create server instance and expose app for uvicorn
mcp_server_instance = MCPServer()
app = mcp_server_instance.app