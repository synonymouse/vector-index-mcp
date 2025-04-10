import os
from typing import List, Dict, Any
import lancedb
from lancedb.pydantic import pydantic_to_schema
import sentence_transformers
from models import IndexedDocument, Settings

class Indexer:
    def __init__(self, settings: Settings):
        self.model = sentence_transformers.SentenceTransformer(settings.embedding_model_name)
        self.db = lancedb.connect(settings.lancedb_uri)
        
        schema = pydantic_to_schema(IndexedDocument)
        try:
            self.table = self.db.open_table("documents")
        except ValueError:
            self.table = self.db.create_table("documents", schema=schema)

    def generate_embedding(self, text: str) -> List[float]:
        return self.model.encode(text).tolist()

    def add_or_update_document(self, doc: IndexedDocument):
        """Adds or updates a single document chunk in the index.
        Assumes that the document_id is unique per chunk.
        Relies on file_watcher to remove old chunks before adding updated ones.
        """
        # Ensure vector exists for the chunk's text
        if not doc.vector:
            # Use the actual chunk content for embedding
            doc.vector = self.generate_embedding(doc.extracted_text_chunk)

        # LanceDB's add can often act as upsert if IDs match, but explicit
        # removal in file_watcher for modifications is safer.
        # We use dict() for compatibility with LanceDB add method.
        try:
            self.table.add([doc.dict()])
        except Exception as e:
            # Log or handle specific LanceDB errors if necessary
            print(f"Error adding document chunk {doc.document_id}: {e}") # Using print for visibility, replace with logging
            # Consider re-raising or specific error handling

    def remove_document(self, file_path: str):
        """Removes all document chunks associated with a given file_path."""
        try:
            # Use the file_path field to delete all related chunks
            where_clause = f"file_path = '{file_path}'"
            self.table.delete(where_clause)
            print(f"Deleted chunks for file: {file_path}") # Replace with logging
        except Exception as e:
            print(f"Error deleting chunks for file {file_path}: {e}") # Replace with logging
            # Consider re-raising or specific error handling

    def search(self, query_text: str, top_k: int = 5) -> List[IndexedDocument]:
        """Search for documents semantically similar to the query text.
        
        Args:
            query_text: Text to search for
            top_k: Maximum number of results to return
            
        Returns:
            List of IndexedDocument objects matching the query
        """
        try:
            query_embedding = self.generate_embedding(query_text)
            results = self.table.search(query_embedding).limit(top_k).to_pydantic(IndexedDocument)
            return results
        except Exception as e:
            raise ValueError(f"Search failed: {str(e)}")