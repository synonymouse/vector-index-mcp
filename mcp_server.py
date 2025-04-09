import os
import threading
import json # <-- Add json import
from typing import List, Dict, Any
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from fastapi import FastAPI
from indexer import Indexer
from file_watcher import FileWatcher
from models import IndexedDocument, Settings

app = FastAPI()

# Load environment variables
load_dotenv()


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5

# Define a model for the search result item in the API response
class SearchResultItem(BaseModel):
   document_id: str
   file_path: str
   content_hash: str
   last_modified_timestamp: float
   extracted_text_chunk: str
   metadata: Dict[str, Any] # Parsed metadata
   vector: List[float] = [] # Keep vector if needed in response

class SearchResponse(BaseModel):
    results: List[SearchResultItem] # Use the new response item model

@app.get("/")
async def root():
    return {"message": "MCP Indexing Server"}

class MCPServer:
    def __init__(self):
        self.app = app
        self.settings = Settings()
        self.indexer = Indexer(self.settings)
        self.file_watcher = FileWatcher(
            project_path=self.settings.project_path,
            indexer=self.indexer,
            ignore_patterns=self.settings.ignore_patterns
        )
        self.search_module = None
        print(f"Loaded settings: {self.settings}")
        self._start_file_watcher()

    def _start_file_watcher(self):
        """Start file watcher in a background thread"""
        self.file_watcher.initial_scan()
        self.watcher_thread = threading.Thread(
            target=self.file_watcher.start,
            daemon=True
        )
        self.watcher_thread.start()

    def shutdown(self):
        """Cleanup resources on shutdown"""
        if hasattr(self, 'file_watcher') and self.file_watcher:
            self.file_watcher.stop()
        if hasattr(self, 'watcher_thread') and self.watcher_thread:
            self.watcher_thread.join(timeout=1)

@app.post("/search", response_model=SearchResponse)
async def search_documents(request: SearchRequest):
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
                # Parse the metadata JSON string
                parsed_metadata = json.loads(doc.metadata_json)
            except json.JSONDecodeError:
                # Handle cases where metadata might be invalid JSON
                parsed_metadata = {"error": "invalid metadata format"}

            # Create the response item, copying fields and adding parsed metadata
            processed_results.append(
                SearchResultItem(
                    document_id=doc.document_id,
                    file_path=doc.file_path,
                    content_hash=doc.content_hash,
                    last_modified_timestamp=doc.last_modified_timestamp,
                    extracted_text_chunk=doc.extracted_text_chunk,
                    metadata=parsed_metadata, # Use the parsed dict
                    vector=doc.vector
                )
            )

        return SearchResponse(results=processed_results)
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=500,
            detail=f"Search failed: {str(e)}"
        )

# Create server instance and expose app for uvicorn
mcp_server_instance = MCPServer()
app = mcp_server_instance.app