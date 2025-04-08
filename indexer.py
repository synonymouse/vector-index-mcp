import os
from typing import List, Dict, Any
import lancedb
from lancedb.pydantic import pydantic_to_schema
import sentence_transformers
from mcp_server import IndexedDocument, Settings

class Indexer:
    def __init__(self, settings: Settings):
        self.model = sentence_transformers.SentenceTransformer(settings.embedding_model_name)
        self.db = lancedb.connect(settings.lancedb_uri)
        
        schema = pydantic_to_schema(IndexedDocument)
        try:
            self.table = self.db.open_table("documents")
        except FileNotFoundError:
            self.table = self.db.create_table("documents", schema=schema)

    def generate_embedding(self, text: str) -> List[float]:
        return self.model.encode(text).tolist()

    def add_or_update_document(self, doc: IndexedDocument):
        # TODO: Implement robust upsert logic
        # Simple add for now:
        if not doc.vector:
            doc.vector = self.generate_embedding(doc.text)
        self.table.add([doc.dict()])

    def remove_document(self, document_id: str):
        self.table.delete(f'document_id = "{document_id}"')

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