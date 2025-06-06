import os
from typing import List, Optional

from lancedb.pydantic import LanceModel, Vector
from pydantic import BaseModel, Field, validator

from .config import get_vector_index_settings

settings = get_vector_index_settings()
EMBEDDING_DIM = settings.embedding_dim


class FileMetadata(LanceModel):
    original_path: str


class IndexedDocument(LanceModel):
    document_id: str
    file_path: str
    content_hash: str
    last_modified_timestamp: float
    chunk_index: int
    total_chunks: int
    extracted_text_chunk: str
    metadata: FileMetadata
    vector: Optional[Vector(dim=EMBEDDING_DIM)] = Field(default=None)


class Settings(
    BaseModel
):  # Settings should be a standard Pydantic model, not a LanceModel for DB table
    """
    Configuration settings for the vector index MCP server, typically loaded from
    environment variables or a configuration file.
    """

    embedding_model_name: str = Field(
        default_factory=lambda: os.getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2"),
        description="Name of the sentence-transformer model to use for embeddings.",
    )
    lancedb_uri: str = Field(
        default_factory=lambda: os.getenv("LANCEDB_URI", "./.lancedb"),
        description="URI for the LanceDB database. Can be a local path or remote.",
    )
    log_level: str = Field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper(),
        description="Logging level for the application (e.g., DEBUG, INFO, WARNING, ERROR).",
    )
    project_path: str  # This is a required field, typically passed as a CLI argument.
    ignore_patterns: List[str] = Field(
        default_factory=lambda: [
            p.strip()
            for p in os.getenv(
                "IGNORE_PATTERNS",
                ".git,__pycache__,*.pyc,*.DS_Store,.DS_Store",  # Added .DS_Store
            ).split(",")
            if p.strip()  # Ensure no empty strings from multiple commas
        ],
        description="Comma-separated list of .gitignore-style patterns for files/directories to ignore.",
    )

    @validator("log_level")
    def validate_log_level(cls, value):
        allowed_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if value.upper() not in allowed_levels:
            raise ValueError(
                f"Invalid log_level: {value}. Must be one of {allowed_levels}"
            )
        return value.upper()


# --- Models for MCP Tool Arguments and Return Payloads ---
# These models define the structure for data exchanged via MCP tools.
# They should use pydantic.BaseModel, not lancedb.pydantic.LanceModel,
# as they are not directly stored in LanceDB as tables.


class IndexRequest(BaseModel):  # Corrected from LanceModel
    """
    Defines the expected arguments for the 'trigger_index_tool'.
    """

    project_path: str = Field(
        ...,  # Should match the server's configured project_path for this implementation
        description="The project path to scan and index. Currently, this must match the server's configured project path.",
    )
    force_reindex: bool = Field(
        default=False,
        description="If true, the existing index for the project path will be cleared and rebuilt.",
    )


class IndexingStatusResponse(BaseModel):
    """
    Defines the structure of the status information returned by the 'get_status_tool'.
    """

    project_path: str = Field(
        ..., description="The project path to which this status information pertains."
    )
    status: str = Field(  # Consider using an Enum for status if states are well-defined
        ...,
        description="Current operational status of the server and indexer (e.g., INITIALIZING, SCANNING, WATCHING, READY, ERROR).",
    )
    last_scan_start_time: Optional[float] = Field(
        default=None,
        description="Timestamp (Unix epoch seconds) when the last indexing scan started. Null if no scan has run.",
    )
    last_scan_end_time: Optional[float] = Field(
        default=None,
        description="Timestamp (Unix epoch seconds) when the last indexing scan completed. Null if no scan has completed or one is in progress.",
    )
    indexed_chunk_count: Optional[int] = Field(
        default=None,
        description="Total number of document chunks currently present in the index for this project path.",
    )
    error_message: Optional[str] = Field(
        default=None, description="Provides details if the server status is 'ERROR'."
    )


class SearchRequest(BaseModel):  # Corrected from LanceModel
    """
    Defines the expected arguments for the 'search_index_tool'.
    """

    query: str = Field(
        ..., description="The natural language query text to search for."
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=100,
        description="The maximum number of similar document chunks to return.",
    )


class SearchResultItem(BaseModel):  # Corrected from LanceModel
    """
    Represents a single search result item, corresponding to an indexed document chunk.
    This is part of the payload returned by the 'search_index_tool'.
    """

    document_id: str = Field(
        ..., description="Unique identifier of the matched document chunk."
    )
    file_path: str = Field(
        ...,
        description="Path to the original file from which this chunk was extracted.",
    )
    content_hash: str = Field(
        ..., description="SHA256 hash of the original file's content when indexed."
    )
    last_modified_timestamp: float = Field(
        ...,
        description="Last modified timestamp (Unix epoch seconds) of the original file.",
    )
    extracted_text_chunk: str = Field(
        ...,
        description="The actual text content of the document chunk that matched the search query.",
    )
    metadata: FileMetadata = Field(  # Contains original_path
        ..., description="Additional metadata associated with the original file."
    )


class SearchResponse(BaseModel):
    """
    Defines the structure of the complete search results payload returned by the 'search_index_tool'.
    """

    results: List[SearchResultItem] = Field(
        ..., description="A list of search result items, ordered by relevance."
    )
