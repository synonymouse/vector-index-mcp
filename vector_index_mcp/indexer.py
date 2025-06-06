import os
import logging
from typing import List, TypedDict, Optional
import lancedb
import pyarrow as pa
import numpy as np
import sentence_transformers
from .models import (
    IndexedDocument,
    Settings,
)

log = logging.getLogger(__name__) # BasicConfig should be handled at the application entry point (main_mcp.py)


class FileMetadataDict(TypedDict):
    """
    Typed dictionary representing the serialized form of FileMetadata.
    Used for structuring search results.
    """
    original_path: str


class SearchResultDict(TypedDict):
    """
    Typed dictionary representing the structure of a single search result
    as returned by the `Indexer.search` method.
    """
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
    """
    Manages the vector index, including loading embedding models,
    connecting to LanceDB, adding/updating documents, and performing searches.
    """
    def __init__(self, settings: Settings):
        """
        Initializes the Indexer.

        Args:
            settings: Configuration settings for the indexer.
        """
        self.settings = settings
        log.info(f"Initializing Indexer with embedding model '{settings.embedding_model_name}' and LanceDB URI '{settings.lancedb_uri}'.")
        self.model: Optional[sentence_transformers.SentenceTransformer] = None
        self.db: Optional[lancedb.DBConnection] = None
        self.table: Optional[lancedb.table.Table] = None
        self.table_name = "documents" # Name of the table in LanceDB

    async def load_resources(self):
        """
        Asynchronously loads the sentence embedding model and connects to the LanceDB database.
        It attempts to open an existing table or creates a new one if not found or incompatible.

        Raises:
            RuntimeError: If the embedding model fails to load or the database connection cannot be established.
            Exception: Propagates exceptions from underlying libraries during resource loading.
        """
        log.info("Indexer: Starting to load resources (model and database).")

        try:
            log.info(f"Indexer: Loading sentence transformer model '{self.settings.embedding_model_name}'...")
            self.model = sentence_transformers.SentenceTransformer(
                self.settings.embedding_model_name
            )
            # The following log is for deep debugging if model loading behaves unexpectedly.
            log.debug(f"Indexer: Model '{self.settings.embedding_model_name}' loaded. Type: {type(self.model)}.")
        except BaseException as be: # Catching BaseException for comprehensive error logging, including system exits or memory errors.
            log.critical(
                f"Indexer: CRITICAL FAILURE loading sentence transformer model '{self.settings.embedding_model_name}': {type(be).__name__}: {be}",
                exc_info=True,
            )
            self.model = None # Ensure model is None if loading fails.
            raise # Re-raise to be handled by the calling MCPServer initialization.

        if self.model is None:
            # This case should ideally not be reached if SentenceTransformer raises an exception on failure.
            # However, it's a safeguard.
            err_msg = f"Indexer: SentenceTransformer model '{self.settings.embedding_model_name}' is None after load attempt without a caught BaseException. This indicates an unexpected silent failure during model initialization."
            log.critical(err_msg)
            raise RuntimeError(err_msg)

        try:
            log.info(f"Indexer: Connecting to LanceDB at URI: {self.settings.lancedb_uri}")
            self.db = lancedb.connect(self.settings.lancedb_uri)
            log.info(f"Indexer: Successfully connected to LanceDB.")
        except Exception as e:
            log.error(
                f"Indexer: Failed to connect to LanceDB at '{self.settings.lancedb_uri}': {e}",
                exc_info=True,
            )
            raise # Re-raise critical error, to be handled by MCPServer.

        log.info(f"Indexer: Preparing table '{self.table_name}' using schema from 'IndexedDocument' model.")
        try:
            # Attempt to open the table first.
            self.table = self.db.open_table(self.table_name)
            log.info(f"Indexer: Successfully opened existing LanceDB table '{self.table_name}'.")
            # TODO: Consider adding schema validation here to ensure the existing table matches IndexedDocument.
        except (FileNotFoundError, ValueError, pa.lib.ArrowIOError) as e:
            # FileNotFoundError: Table does not exist.
            # ValueError: Often indicates schema incompatibility with an existing table.
            # pa.lib.ArrowIOError: Can occur if the table directory is corrupted or not a valid LanceDB table.
            log.warning(
                f"Indexer: Table '{self.table_name}' not found or potentially incompatible ({type(e).__name__}: {e}). Attempting to create/recreate."
            )
            try:
                if isinstance(e, (ValueError, pa.lib.ArrowIOError)): # If schema is incompatible or table is corrupt
                    try:
                        self.db.drop_table(self.table_name)
                        log.info(
                            f"Indexer: Dropped existing (potentially incompatible/corrupt) table '{self.table_name}' before recreation."
                        )
                    except Exception as drop_e:
                        log.warning(
                            f"Indexer: Failed to explicitly drop table '{self.table_name}' during recreation attempt: {drop_e}"
                        )
                # Create the table using the Pydantic model schema.
                self.table = self.db.create_table(
                    self.table_name, schema=IndexedDocument, mode="create" # 'create' mode fails if table exists
                )
                log.info(
                    f"Indexer: Successfully created new LanceDB table '{self.table_name}' using schema from 'IndexedDocument'."
                )
                # It's good practice to create the vector index immediately after table creation if data will be added soon.
                self.create_vector_index(replace=True) # Create index on the new table
            except Exception as ce:
                log.error(
                    f"Indexer: CRITICAL FAILURE: Could not create or recreate LanceDB table '{self.table_name}': {ce}",
                    exc_info=True,
                )
                raise # This is a fatal error for the indexer's operation.
        log.info("Indexer: All resources (model and database table) loaded and initialized successfully.")

    def create_vector_index(self, replace: bool = False):
        """
        Creates a vector search index on the 'vector' column of the table.

        Args:
            replace: If True, replaces an existing index. Defaults to False.
        """
        if not self.table:
            log.error("Indexer: Cannot create vector index because the table is not initialized.")
            return
        try:
            log.info(
                f"Indexer: Attempting to create vector index on table '{self.table_name}' (column 'vector', replace={replace})."
            )
            # Parameters like num_partitions, num_sub_vectors can be tuned for performance vs. accuracy.
            # Default IVF_PQ index is generally a good starting point.
            self.table.create_index(vector_column_name="vector", replace=replace)
            log.info(
                f"Indexer: Successfully created/verified vector index on table '{self.table_name}'."
            )
        except Exception as index_e:
            log.error(
                f"Indexer: Failed to create vector index on table '{self.table_name}': {index_e}",
                exc_info=True,
            )
            # Depending on the application, this might be a critical error or a recoverable one.

    def generate_embedding(self, text: str) -> np.ndarray:
        """
        Generates a vector embedding for the given text using the loaded sentence transformer model.
        Ensures the embedding is a float32 numpy array.

        Args:
            text: The input text to embed.

        Returns:
            A numpy array representing the vector embedding.

        Raises:
            RuntimeError: If the embedding model is not loaded.
            Exception: Propagates exceptions from the embedding model.
        """
        log.debug(f"Indexer: Generating embedding for text snippet: '{text[:100]}...'")
        if self.model is None:
            # This should ideally be caught earlier during load_resources or by checks in calling methods.
            log.critical("Indexer: Embedding model (self.model) is None when generate_embedding was called. This is a critical state.")
            raise RuntimeError("Embedding model is not loaded. Cannot generate embedding.")
        try:
            embedding = self.model.encode(text, normalize_embeddings=True) # Normalizing is often good for cosine similarity
            return embedding.astype(np.float32) # Ensure float32 for compatibility with LanceDB/Arrow
        except AttributeError as ae:
            # This might happen if self.model is not a valid SentenceTransformer object despite not being None.
            log.error(f"Indexer: AttributeError during embedding generation. self.model type: {type(self.model)}. Error: {ae}", exc_info=True)
            raise
        except Exception as e:
            log.error(
                f"Indexer: Failed to generate embedding for text snippet '{text[:100]}...': {e}",
                exc_info=True,
            )
            raise # Re-raise to allow caller to handle.

    def add_or_update_document(self, doc: IndexedDocument):
        """
        Adds or updates a single document chunk (represented by an IndexedDocument object)
        into the LanceDB table. This involves generating an embedding for the text chunk.

        Note: LanceDB's `add` with LanceModels typically handles upserts based on primary keys
        if defined in the model, or simply adds if no such concept is used for replacement.
        For explicit updates, one might need to delete then add. Here, we assume `add` is sufficient
        or that upstream logic handles de-duplication/updates by removing old versions first.

        Args:
            doc: An `IndexedDocument` object containing the data for the chunk.
                 The `vector` field will be populated by this method.
        """
        if not self.table:
            log.error(f"Indexer: Cannot add document '{doc.document_id}'; table is not initialized.")
            return # Or raise an error

        try:
            vector_embedding = self.generate_embedding(doc.extracted_text_chunk)
            # Pydantic V2 uses model_copy, V1 uses copy. Assuming V1 for .copy()
            doc_with_vector = doc.copy(update={"vector": vector_embedding.tolist()})

            self.table.add([doc_with_vector]) # Add as a list containing the single Pydantic object
            log.debug(f"Indexer: Successfully added/updated document chunk ID: {doc.document_id}, file: {doc.file_path}")
        except Exception as e:
            log.error(
                f"Indexer: Error adding/updating document chunk ID {doc.document_id} (file: {doc.file_path}): {e}",
                exc_info=True,
            )
            # Depending on requirements, might raise this error or log and continue.

    def remove_document(self, file_path: str) -> bool:
        """
        Removes all document chunks associated with a given `file_path` from the index.

        Args:
            file_path: The path of the file whose chunks are to be removed.

        Returns:
            True if the delete operation was successfully issued, False otherwise.
            Note: LanceDB's delete operation might not return the count of deleted rows directly.
        """
        if not self.table:
            log.warning("Indexer: Table not initialized. Cannot remove document chunks.")
            return False
        try:
            # Construct a SQL-like filter condition for the delete operation.
            # Ensure file_path is properly quoted if it can contain special characters, though LanceDB might handle this.
            delete_condition = f"file_path = '{file_path}'"
            log.info(f"Indexer: Issuing delete command for document chunks with file_path: '{file_path}' (condition: \"{delete_condition}\")")
            self.table.delete(delete_condition)
            # LanceDB's delete operation typically returns None on success or raises an error.
            # A more robust check might involve querying count before and after if necessary.
            log.info(f"Indexer: Delete command for file_path '{file_path}' completed. Check logs for any LanceDB errors if issues persist.")
            return True
        except Exception as e:
            log.error(f"Indexer: Error deleting document chunks for file_path '{file_path}': {e}", exc_info=True)
            return False

    def search(self, query_text: str, top_k: int = 5) -> List[SearchResultDict]:
        """
        Performs a semantic search for documents similar to the `query_text`.

        Args:
            query_text: The text to search for.
            top_k: The maximum number of results to return.

        Returns:
            A list of `SearchResultDict` objects, each representing a found document chunk.

        Raises:
            ValueError: If the search operation fails or `query_text` is empty.
        """
        if not self.table:
            log.error("Indexer: Cannot perform search because the table is not initialized.")
            raise ValueError("Search failed: Index table not available.")
        if not query_text:
            log.warning("Indexer: Received empty query text for search. Returning no results.")
            return []

        try:
            log.info(f"Indexer: Performing search for query: '{query_text[:70]}...', top_k={top_k}")
            query_embedding = self.generate_embedding(query_text)

            # Perform the search against the 'vector' column.
            search_result_builder = self.table.search(query_embedding).limit(top_k)
            # to_pydantic converts the results into a list of Pydantic model instances.
            pydantic_results: List[IndexedDocument] = search_result_builder.to_pydantic(IndexedDocument)

            # Convert Pydantic models to the SearchResultDict typed dictionary.
            # Exclude 'vector' from the final result as it's large and usually not needed by clients.
            typed_results: List[SearchResultDict] = [
                r.dict(exclude={'vector'}) for r in pydantic_results # Use .dict(exclude=...) for Pydantic V1
            ]
            log.info(
                f"Indexer: Search for '{query_text[:70]}...' returned {len(typed_results)} results."
            )
            return typed_results
        except Exception as e:
            log.error(
                f"Indexer: Search failed for query '{query_text[:70]}...': {e}", exc_info=True
            )
            # Re-raise as a ValueError to indicate a problem with the search operation itself.
            raise ValueError(f"Search operation failed: {str(e)}")

    def get_indexed_chunk_count(self, project_path: Optional[str] = None) -> int:
        """
        Counts the number of indexed chunks. If `project_path` is provided,
        it counts chunks associated with that specific project path prefix.
        Otherwise, it counts all chunks in the table.

        Args:
            project_path: Optional. The project path prefix to filter by.

        Returns:
            The number of (matching) indexed chunks.
        """
        if not self.table:
            log.warning("Indexer: Table not initialized. Cannot get chunk count.")
            return 0

        filter_clause = None
        if project_path:
            # Basic sanitization for the LIKE pattern.
            # This is a simple measure; for complex user inputs, more robust sanitization might be needed.
            safe_project_path_segment = project_path.replace("'", "''").replace("%", "\\%").replace("_", "\\_")
            filter_clause = f"file_path LIKE '{safe_project_path_segment}%'"
            log.debug(f"Indexer: Counting chunks with filter: \"{filter_clause}\"")
        else:
            log.debug("Indexer: Counting all chunks in the table.")

        try:
            count = self.table.count_rows(filter_clause) # filter_clause can be None for no filter
            log.info(f"Indexer: Found {count} indexed chunks" + (f" for project path prefix '{project_path}'." if project_path else "."))
            return count
        except Exception as e:
            log.error(
                f"Indexer: Error counting chunks" + (f" for project path '{project_path}'" if project_path else "") + f": {e}",
                exc_info=True,
            )
            return 0 # Return 0 on error to avoid breaking callers expecting an int.

    def clear_index(self, project_path: Optional[str] = None):
        """
        Removes document chunks from the index. If `project_path` is provided,
        only chunks associated with that project path prefix are removed.
        Otherwise, ALL chunks in the table are removed (effectively clearing the entire table content).

        Args:
            project_path: Optional. The project path prefix for targeted deletion.
                          If None, all documents are deleted.
        """
        if not self.table:
            log.warning("Indexer: Table not initialized. Cannot clear index.")
            return

        where_clause = None
        log_message_segment = "all documents"
        if project_path:
            safe_project_path_segment = project_path.replace("'", "''").replace("%", "\\%").replace("_", "\\_")
            where_clause = f"file_path LIKE '{safe_project_path_segment}%'"
            log_message_segment = f"documents for project path prefix '{project_path}'"

        try:
            count_before = self.table.count_rows(where_clause)
            if count_before > 0:
                log.info(f"Indexer: Attempting to delete {count_before} chunks from {log_message_segment} (filter: \"{where_clause}\").")
                self.table.delete(where_clause) # delete() returns None on success
                # Verify deletion if possible, or assume success if no exception.
                # count_after = self.table.count_rows(where_clause)
                log.info(
                    f"Indexer: Successfully issued delete command for {count_before} chunks from {log_message_segment}."
                )
            else:
                log.info(
                    f"Indexer: No chunks found to delete for {log_message_segment}."
                )
        except Exception as e:
            log.error(
                f"Indexer: Error clearing index for {log_message_segment}: {e}",
                exc_info=True,
            )
