import logging
from typing import List, Optional, TypedDict

import lancedb
import numpy as np
import sentence_transformers
from lancedb.db import AsyncConnection  # For type hinting
from lancedb.table import AsyncTable  # For type hinting

from .models import (
    IndexedDocument,
    Settings,
)

log = logging.getLogger(
    __name__
)  # BasicConfig should be handled at the application entry point (main_mcp.py)


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
        log.info(
            f"Initializing Indexer with embedding model '{settings.embedding_model_name}' and LanceDB URI '{settings.lancedb_uri}'."
        )
        self.model: Optional[sentence_transformers.SentenceTransformer] = None
        self.db: Optional[AsyncConnection] = None
        self.table: Optional[AsyncTable] = None
        self.table_name = "documents"  # Name of the table in LanceDB

    async def load_resources(self, recreate_if_exists: bool = False):
        """
        Asynchronously loads the sentence embedding model, connects to the LanceDB database,
        and prepares the table.
        It attempts to open an existing table or creates a new one if not found or incompatible.

        Args:
            recreate_if_exists: If True, drops and recreates the table if it exists.

        Raises:
            RuntimeError: If the embedding model fails to load, the database connection
                          cannot be established, or the table cannot be initialized.
            Exception: Propagates exceptions from underlying libraries during resource loading.
        """
        log.info("Indexer: Starting to load resources (model and database).")
        self.table = None  # Initialize self.table

        # Load Sentence Transformer Model
        try:
            log.info(
                f"Indexer: Loading sentence transformer model '{self.settings.embedding_model_name}'..."
            )
            # Model loading is CPU-bound, consider to_thread if it becomes a bottleneck
            # For now, direct call as it's usually part of startup.
            self.model = sentence_transformers.SentenceTransformer(
                self.settings.embedding_model_name
            )
            log.debug(
                f"Indexer: Model '{self.settings.embedding_model_name}' loaded. Type: {type(self.model)}."
            )
        except BaseException as be:
            log.critical(
                f"Indexer: CRITICAL FAILURE loading sentence transformer model '{self.settings.embedding_model_name}': {type(be).__name__}: {be}",
                exc_info=True,
            )
            self.model = None
            raise
        if self.model is None:
            err_msg = f"Indexer: SentenceTransformer model '{self.settings.embedding_model_name}' is None after load attempt. This indicates an unexpected silent failure."
            log.critical(err_msg)
            raise RuntimeError(err_msg)

        # Connect to LanceDB
        try:
            log.info(
                f"Indexer: Connecting to LanceDB asynchronously at URI: {self.settings.lancedb_uri}"
            )
            self.db = await lancedb.connect_async(self.settings.lancedb_uri)
            log.info(
                f"Indexer: Successfully connected to LanceDB asynchronously. DB object: {self.db}"
            )
        except Exception as e:
            log.error(
                f"Indexer: Failed to connect to LanceDB at '{self.settings.lancedb_uri}': {e}",
                exc_info=True,
            )
            raise

        # Open or Create Table
        log.info(
            f"Indexer: Preparing table '{self.table_name}' using schema from 'IndexedDocument' model."
        )
        self.table = None

        table_opened_successfully = False
        table_created_successfully = False

        try:
            if not recreate_if_exists:
                log.info(f"Attempting to open existing table: {self.table_name}")
                try:
                    opened_table = await self.db.open_table(self.table_name)
                    if opened_table:
                        self.table = opened_table
                        log.info(
                            f"Successfully opened existing table: {self.table_name}. self.table: {self.table}, type: {type(self.table)}"
                        )
                        table_opened_successfully = True
                    else:
                        log.warning(
                            f"db.open_table for '{self.table_name}' returned None/falsey. Will attempt to create."
                        )
                except FileNotFoundError:
                    log.info(
                        f"Table '{self.table_name}' not found. Will attempt to create."
                    )
                except Exception as oe:
                    log.warning(
                        f"Error opening table '{self.table_name}': {oe}. Will attempt to create."
                    )

            if (
                not table_opened_successfully
            ):  # Covers not found, error opening, or recreate_if_exists=True
                log.info(
                    f"Attempting to create/overwrite table: {self.table_name} with schema {IndexedDocument}"
                )
                created_table_obj = await self.db.create_table(
                    self.table_name, schema=IndexedDocument, mode="overwrite"
                )
                log.info(
                    f"db.create_table returned: {created_table_obj}, type: {type(created_table_obj)}"
                )
                if created_table_obj and isinstance(created_table_obj, AsyncTable):
                    self.table = created_table_obj
                    log.info(
                        f"Successfully created/overwritten and assigned table '{self.table_name}'. self.table: {self.table}"
                    )
                    table_created_successfully = True
                else:
                    log.error(
                        f"CRITICAL: Async db.create_table for '{self.table_name}' did not return a valid AsyncTable object. Returned: {created_table_obj}"
                    )
                    self.table = None

        except Exception as e:
            log.exception(
                f"CRITICAL: Error during table open/create logic for '{self.table_name}'. Error: {e}"
            )
            self.table = None
            raise RuntimeError(
                f"Fatal error initializing table '{self.table_name}'"
            ) from e

        # Index creation logic
        MIN_ROWS_FOR_DEFAULT_INDEX = 256  # Default for IVF_PQ, adjust if necessary
        if self.table and isinstance(self.table, AsyncTable):
            if table_opened_successfully and not table_created_successfully:
                num_rows = await self.table.count_rows()
                log.info(
                    f"Table '{self.table_name}' was opened and contains {num_rows} rows."
                )
                if num_rows >= MIN_ROWS_FOR_DEFAULT_INDEX:
                    log.info(
                        f"Sufficient data ({num_rows} >= {MIN_ROWS_FOR_DEFAULT_INDEX}) for default index creation. Ensuring vector index exists (replace=True)."
                    )
                    await self.create_vector_index(table_obj=self.table, replace=True)
                else:
                    log.warning(
                        f"Table '{self.table_name}' has only {num_rows} rows, which is less than the required {MIN_ROWS_FOR_DEFAULT_INDEX} "
                        f"for default index creation. Index creation will be deferred or a flat index might be considered later."
                    )
            elif table_created_successfully:
                log.info(
                    f"Table '{self.table_name}' was newly created/overwritten. Vector index creation will be handled upon data addition or explicit trigger."
                )
            # If neither, self.table might be None or invalid, which is handled by the else below.
        else:
            final_error_msg = f"Indexer critically failed: self.table for '{self.table_name}' is not a valid AsyncTable object after all attempts. self.table: {self.table}."
            log.error(final_error_msg)
            raise RuntimeError(final_error_msg)

        log.info(
            "Indexer: Model and database table loaded and initialized. Vector index creation may be deferred for new or small tables."
        )

    async def create_vector_index(self, table_obj: AsyncTable, replace: bool = False):
        """
        Creates a vector search index on the 'vector' column of the provided async table object.

        Args:
            table_obj: The LanceDB table object to create the index on.
            replace: If True, replaces an existing index. Defaults to False.

        Raises:
            RuntimeError: If the table object is invalid or if index creation fails.
        """
        if not table_obj:
            log.error(
                "Indexer: Cannot create vector index because the provided LanceDB table object is invalid (None)."
            )
            raise RuntimeError(
                "Failed to create vector index: LanceDB table object is not available."
            )

        table_name_for_log = "unknown_table_passed_to_create_vector_index"
        try:
            # Attempt to get table name for logging, handle if it's not directly available or different
            if hasattr(table_obj, "name"):
                table_name_for_log = table_obj.name
        except Exception:
            pass  # Keep default log name

        try:
            log.info(
                f"Indexer: Attempting to create vector index on table '{table_name_for_log}' (column 'vector', replace={replace})."
            )
            # Parameters like num_partitions, num_sub_vectors can be tuned for performance vs. accuracy.
            # Default IVF_PQ index is generally a good starting point.
            await table_obj.create_index(
                "vector", replace=replace
            )  # Pass column name as first arg
            log.info(
                f"Indexer: Successfully created/verified vector index on table '{table_name_for_log}'."
            )
        except Exception as index_e:
            log.error(
                f"Indexer: Failed to create vector index on table '{table_name_for_log}': {index_e}",
                exc_info=True,
            )
            # Propagate the error to ensure initialization fails if index creation doesn't succeed.
            raise RuntimeError(
                f"Failed to create vector index on table '{table_name_for_log}': {index_e}"
            ) from index_e

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
            log.critical(
                "Indexer: Embedding model (self.model) is None when generate_embedding was called. This is a critical state."
            )
            raise RuntimeError(
                "Embedding model is not loaded. Cannot generate embedding."
            )
        try:
            embedding = self.model.encode(
                text, normalize_embeddings=True
            )  # Normalizing is often good for cosine similarity
            return embedding.astype(
                np.float32
            )  # Ensure float32 for compatibility with LanceDB/Arrow
        except AttributeError as ae:
            # This might happen if self.model is not a valid SentenceTransformer object despite not being None.
            log.error(
                f"Indexer: AttributeError during embedding generation. self.model type: {type(self.model)}. Error: {ae}",
                exc_info=True,
            )
            raise
        except Exception as e:
            log.error(
                f"Indexer: Failed to generate embedding for text snippet '{text[:100]}...': {e}",
                exc_info=True,
            )
            raise  # Re-raise to allow caller to handle.

    async def add_or_update_document(self, doc: IndexedDocument):
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
            log.error(
                f"Indexer: Cannot add document '{doc.document_id}'; table is not initialized."
            )
            return  # Or raise an error

        try:
            vector_embedding = self.generate_embedding(doc.extracted_text_chunk)
            # Pydantic V2 uses model_copy, V1 uses copy. Assuming V1 for .copy()
            doc_with_vector = doc.copy(update={"vector": vector_embedding.tolist()})

            await self.table.add(
                [doc_with_vector]
            )  # Add as a list containing the single Pydantic object
            log.debug(
                f"Indexer: Successfully added/updated document chunk ID: {doc.document_id}, file: {doc.file_path}"
            )
        except Exception as e:
            log.error(
                f"Indexer: Error adding/updating document chunk ID {doc.document_id} (file: {doc.file_path}): {e}",
                exc_info=True,
            )
            # Depending on requirements, might raise this error or log and continue.

    async def remove_document(self, file_path: str) -> bool:
        """
        Removes all document chunks associated with a given `file_path` from the index.

        Args:
            file_path: The path of the file whose chunks are to be removed.

        Returns:
            True if the delete operation was successfully issued, False otherwise.
            Note: LanceDB's delete operation might not return the count of deleted rows directly.
        """
        if not self.table:
            log.warning(
                "Indexer: Table not initialized. Cannot remove document chunks."
            )
            return False
        try:
            # Construct a SQL-like filter condition for the delete operation.
            # Ensure file_path is properly quoted if it can contain special characters, though LanceDB might handle this.
            delete_condition = f"file_path = '{file_path}'"
            log.info(
                f"Indexer: Issuing delete command for document chunks with file_path: '{file_path}' (condition: \"{delete_condition}\")"
            )
            await self.table.delete(delete_condition)
            # LanceDB's delete operation typically returns None on success or raises an error.
            # A more robust check might involve querying count before and after if necessary.
            log.info(
                f"Indexer: Delete command for file_path '{file_path}' completed. Check logs for any LanceDB errors if issues persist."
            )
            return True
        except Exception as e:
            log.error(
                f"Indexer: Error deleting document chunks for file_path '{file_path}': {e}",
                exc_info=True,
            )
            return False

    async def search(self, query_text: str, top_k: int = 5) -> List[SearchResultDict]:
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
            log.error(
                "Indexer: Cannot perform search because the table is not initialized."
            )
            raise ValueError("Search failed: Index table not available.")
        if not query_text:
            log.warning(
                "Indexer: Received empty query text for search. Returning no results."
            )
            return []

        try:
            log.info(
                f"Indexer: Performing search for query: '{query_text[:70]}...', top_k={top_k}"
            )
            query_embedding = self.generate_embedding(query_text)

            # Perform the search against the 'vector' column.
            # self.table.search() is an async method and returns an AsyncVectorQuery object.
            # .limit() can be chained on the AsyncVectorQuery object.
            # .to_arrow() is an async method on AsyncVectorQuery that returns a pyarrow.Table.
            async_search_obj = await self.table.search(
                query_embedding
            )  # This is an AsyncVectorQuery
            query_builder = async_search_obj.limit(top_k)
            arrow_table = await query_builder.to_arrow()
            dict_results = arrow_table.to_pylist()
            # Manually convert dicts to Pydantic models
            pydantic_results: List[IndexedDocument] = [
                IndexedDocument(**row) for row in dict_results
            ]

            # Convert Pydantic models to the SearchResultDict typed dictionary.
            # Exclude 'vector' from the final result as it's large and usually not needed by clients.
            typed_results: List[SearchResultDict] = [
                r.dict(exclude={"vector"})
                for r in pydantic_results  # Use .dict(exclude=...) for Pydantic V1
            ]
            log.info(
                f"Indexer: Search for '{query_text[:70]}...' returned {len(typed_results)} results."
            )
            return typed_results
        except Exception as e:
            log.error(
                f"Indexer: Search failed for query '{query_text[:70]}...': {e}",
                exc_info=True,
            )
            # Re-raise as a ValueError to indicate a problem with the search operation itself.
            raise ValueError(f"Search operation failed: {str(e)}")

    async def get_indexed_chunk_count(self, project_path: Optional[str] = None) -> int:
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
            safe_project_path_segment = (
                project_path.replace("'", "''").replace("%", "\\%").replace("_", "\\_")
            )
            filter_clause = f"file_path LIKE '{safe_project_path_segment}%'"
            log.debug(f'Indexer: Counting chunks with filter: "{filter_clause}"')
        else:
            log.debug("Indexer: Counting all chunks in the table.")

        try:
            count = await self.table.count_rows(
                filter_clause
            )  # filter_clause can be None for no filter
            log.info(
                f"Indexer: Found {count} indexed chunks"
                + (
                    f" for project path prefix '{project_path}'."
                    if project_path
                    else "."
                )
            )
            return count
        except Exception as e:
            log.error(
                "Indexer: Error counting chunks"
                + (f" for project path '{project_path}'" if project_path else "")
                + f": {e}",
                exc_info=True,
            )
            return 0  # Return 0 on error to avoid breaking callers expecting an int.

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
            safe_project_path_segment = (
                project_path.replace("'", "''").replace("%", "\\%").replace("_", "\\_")
            )
            where_clause = f"file_path LIKE '{safe_project_path_segment}%'"
            log_message_segment = f"documents for project path prefix '{project_path}'"

        try:
            count_before = self.table.count_rows(where_clause)
            if count_before > 0:
                log.info(
                    f'Indexer: Attempting to delete {count_before} chunks from {log_message_segment} (filter: "{where_clause}").'
                )
                self.table.delete(where_clause)  # delete() returns None on success
                # Verify deletion if possible, or assume success if no exception.
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
