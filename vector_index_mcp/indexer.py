import os
import logging
from typing import List, TypedDict
import lancedb
import pyarrow as pa
import numpy as np
import sentence_transformers
from .models import (
    IndexedDocument,
    Settings,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
log = logging.getLogger(__name__)


class FileMetadataDict(TypedDict):
    """Represents the FileMetadata model when serialized to a dict."""

    original_path: str


class SearchResultDict(TypedDict):
    """Represents the structure of a single search result dict returned by indexer.search."""

    document_id: str
    file_path: str
    content_hash: str
    last_modified_timestamp: float
    chunk_index: int
    total_chunks: int
    extracted_text_chunk: str
    metadata: FileMetadataDict
    # vector is intentionally omitted as it's usually not needed by the caller
    # score: float # Optional: Include score if returned by search method


class Indexer:
    def __init__(self, settings: Settings):
        self.settings = settings
        log.info(f"Initializing Indexer with settings: {settings}")
        self.model = None
        self.db = None
        self.table = None
        self.table_name = "documents"

    async def load_resources(self):
        """Asynchronously loads the embedding model and connects to the database."""
        log.info("Loading Indexer resources...")

        try:
            self.model = sentence_transformers.SentenceTransformer(
                self.settings.embedding_model_name
            )
            log.info(f"Loaded embedding model: {self.settings.embedding_model_name}")
        except Exception as e:
            log.error(
                f"Failed to load sentence transformer model '{self.settings.embedding_model_name}': {e}",
                exc_info=True,
            )
            raise  # Re-raise critical error

        try:
            self.db = lancedb.connect(self.settings.lancedb_uri)
            log.info(f"Connected to LanceDB at URI: {self.settings.lancedb_uri}")
        except Exception as e:
            log.error(
                f"Failed to connect to LanceDB at '{self.settings.lancedb_uri}': {e}",
                exc_info=True,
            )
            raise  # Re-raise critical error

        log.info("Using schema inferred from IndexedDocument model.")
        try:
            # Attempt to open the table.
            self.table = self.db.open_table(self.table_name)
            log.info(f"Opened existing table '{self.table_name}'.")
            # Optional: Add schema validation if needed here.
        except (
            FileNotFoundError,
            ValueError,
            pa.lib.ArrowIOError,
        ) as e:
            log.warning(
                f"Table '{self.table_name}' not found or schema potentially incompatible ({type(e).__name__}: {e}). Attempting to create/recreate."
            )
            try:
                # Drop if necessary (e.g., based on specific error types indicating incompatibility)
                if isinstance(e, ValueError):  # Example condition
                    try:
                        self.db.drop_table(self.table_name)
                        log.info(
                            f"Dropped potentially incompatible table '{self.table_name}' before recreating."
                        )
                    except Exception as drop_e:
                        log.warning(
                            f"Failed to explicitly drop table '{self.table_name}' before recreation: {drop_e}"
                        )

                # Create table using the LanceModel schema.
                self.table = self.db.create_table(
                    self.table_name, schema=IndexedDocument, mode="create"
                )
                log.info(
                    f"Created new table '{self.table_name}' using schema from IndexedDocument model."
                )
                # Consider triggering index creation here or separately after data loading.
            except Exception as ce:
                log.error(
                    f"Failed to create table '{self.table_name}': {ce}",
                    exc_info=True,
                )
                raise

        log.info("Indexer resources loaded successfully.")

    def create_vector_index(self, replace=False):
        """Creates the vector index on the table."""
        try:
            log.info(
                f"Attempting to create vector index on '{self.table_name}' (replace={replace})..."
            )
            # Configure index parameters if needed (e.g., num_partitions, num_sub_vectors)
            # Example: self.table.create_index(vector_column_name="vector", replace=replace, metric="cosine", num_partitions=256, num_sub_vectors=96)
            self.table.create_index(vector_column_name="vector", replace=replace)
            log.info(
                f"Successfully created/verified vector index on '{self.table_name}'."
            )
        except Exception as index_e:
            log.error(
                f"Failed to create vector index on table '{self.table_name}': {index_e}",
                exc_info=True,
            )

    def generate_embedding(
        self, text: str
    ) -> np.ndarray:  # Return numpy array for efficiency
        """Generates a vector embedding for the given text, ensuring it's float32."""
        try:
            embedding = self.model.encode(text, normalize_embeddings=True)
            return embedding.astype(np.float32)
        except Exception as e:
            log.error(
                f"Failed to generate embedding for text snippet: {text[:100]}... Error: {e}",
                exc_info=True,
            )

            raise

    def add_or_update_document(self, doc: IndexedDocument):
        """Adds or updates a single document chunk in the index."""
        try:
            # Embedding generation remains the same
            vector_embedding = self.generate_embedding(doc.extracted_text_chunk)

            # Convert NumPy array to Python list to avoid Pydantic serialization warnings
            vector_as_list = vector_embedding.tolist()

            # Create the Pydantic object with the generated vector as a Python list
            doc_with_vector = doc.copy(update={"vector": vector_as_list})

            # Add the Pydantic object directly (or as a list)
            # LanceDB handles the conversion based on the LanceModel schema.
            self.table.add([doc_with_vector])

            log.debug(f"Added/Updated document chunk: {doc.document_id}")

        except Exception as e:
            log.error(
                f"Error adding/updating document chunk {doc.document_id}: {e}",
                exc_info=True,
            )
            # Decide on error handling: skip, retry, raise?

    def remove_document(self, file_path: str):
        """Removes all document chunks associated with a given file_path."""
        try:
            # Use LanceDB's delete method with a WHERE clause
            # Ensure proper quoting/escaping if file_path can contain special characters
            safe_file_path = file_path.replace("'", "''")  # Basic SQL-like escaping
            where_clause = f"file_path = '{safe_file_path}'"
            count = self.table.delete(where_clause)
            if count is not None and count > 0:
                log.info(f"Deleted {count} chunks for file: {file_path}")
            else:
                log.debug(f"No chunks found to delete for file: {file_path}")
        except Exception as e:
            log.error(f"Error deleting chunks for file {file_path}: {e}", exc_info=True)

    def search(self, query_text: str, top_k: int = 5) -> List[SearchResultDict]:
        """Search for documents semantically similar to the query text."""
        if not query_text:
            log.warning("Received empty query text for search")
            return []
        try:
            query_embedding = self.generate_embedding(query_text)

            search_result = self.table.search(query_embedding).limit(top_k)

            pydantic_results = search_result.to_pydantic(IndexedDocument)

            typed_results: List[SearchResultDict] = [
                doc.model_dump() for doc in pydantic_results
            ]
            log.info(
                f"Search for '{query_text[:50]}...' returned {len(typed_results)} results."
            )
            return typed_results

        except Exception as e:
            # Check for specific LanceDB errors if possible
            # Example: If index is missing or query vector dimension mismatch
            log.error(
                f"Search failed for query '{query_text[:50]}...': {e}", exc_info=True
            )
            # Re-raise a user-friendly error or return empty list?
            raise ValueError(f"Search operation failed: {str(e)}")  # Re-raise for now

    def get_indexed_chunk_count(self, project_path: str) -> int:
        """Counts the number of indexed chunks associated with a given project_path prefix."""
        # Ensure project_path is handled safely if it comes from user input
        safe_project_path = project_path.replace(
            "'", "''"
        )  # Basic SQL injection protection
        # Use LIKE for prefix matching. Ensure the pattern is correct.
        # If project_path is '.', LIKE '.%' might not be what's intended if paths are relative.
        # Adjust logic based on how file_paths are stored (absolute vs relative).
        # Assuming relative paths starting from the project root:
        filter_clause = f"file_path LIKE '{safe_project_path}%'"
        try:
            count = self.table.count_rows(filter_clause)
            log.debug(f"Found {count} chunks for project path prefix: {project_path}")
            return count
        except Exception as e:
            log.error(
                f"Error counting chunks for project path {project_path}: {e}",
                exc_info=True,
            )
            return 0

    def clear_index(self, project_path: str):
        """Removes all document chunks associated with a given project_path prefix."""
        safe_project_path = project_path.replace("'", "''")
        where_clause = f"file_path LIKE '{safe_project_path}%'"
        try:
            count_before = self.table.count_rows(where_clause)
            if count_before > 0:
                deleted_count = self.table.delete(where_clause)
                log.info(
                    f"Cleared index: Deleted {deleted_count} chunks for project path prefix: {project_path}"
                )
            else:
                log.info(
                    f"Index clear: No chunks found for project path prefix: {project_path}"
                )
        except Exception as e:
            log.error(
                f"Error clearing index for project path {project_path}: {e}",
                exc_info=True,
            )
