from typing import List, Dict, Any # Keep Any for internal use before serialization
from pydantic import BaseModel, Field
import os
import json # Import json for potential default serialization if needed

class IndexedDocument(BaseModel):
    # Unique ID for the chunk, e.g., "path/to/file.txt::0"
    document_id: str = Field(..., description="Unique identifier for the document chunk")
    file_path: str = Field(..., description="Path to the original file")
    content_hash: str = Field(..., description="Hash of the original file's content")
    last_modified_timestamp: float = Field(..., description="Last modified timestamp of the original file")
    chunk_index: int = Field(..., description="Index of this chunk within the file")
    total_chunks: int = Field(..., description="Total number of chunks for the file")
    extracted_text_chunk: str = Field(..., description="The actual text content of this chunk")
    # Store metadata as a JSON string in the model intended for LanceDB
    metadata_json: str = Field(default="{}", description="JSON string representation of metadata")
    vector: List[float] = Field(default=[], description="Embedding vector for the chunk")

    # Keep the original metadata dict for internal processing if needed,
    # but it won't be part of the LanceDB schema directly.
    # We'll handle serialization before saving.
    # Alternatively, handle the dict entirely outside this model before creating it.
    # For simplicity here, let's assume serialization happens *before*
    # creating the IndexedDocument instance meant for LanceDB.

class Settings(BaseModel):
    embedding_model_name: str = os.getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
    lancedb_uri: str = os.getenv("LANCEDB_URI", "./.lancedb")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    project_path: str = os.getenv("PROJECT_PATH", ".")
    ignore_patterns: List[str] = os.getenv("IGNORE_PATTERNS", ".git,__pycache__,*.pyc").split(",")

# --- Models for API Endpoints ---

class IndexRequest(BaseModel):
    """Request body for the /index endpoint."""
    project_path: str = Field(..., description="The project path to index (currently must match server config)")
    force_reindex: bool = Field(default=False, description="If true, clear existing index before scanning")

class IndexingStatusResponse(BaseModel):
    """Response body for the /status endpoint."""
    project_path: str = Field(..., description="The project path this status pertains to")
    status: str = Field(..., description="Current indexing status (e.g., Initializing, Scanning, Watching, Error, Not Found)")
    last_scan_start_time: Optional[float] = Field(default=None, description="Timestamp (UTC epoch seconds) when the last scan started")
    last_scan_end_time: Optional[float] = Field(default=None, description="Timestamp (UTC epoch seconds) when the last scan finished")
    indexed_chunk_count: Optional[int] = Field(default=None, description="Number of document chunks currently indexed for the path")
    error_message: Optional[str] = Field(default=None, description="Details if the status is 'Error'")