import os
import threading
from typing import List, Dict, Any
from dotenv import load_dotenv
from pydantic import BaseModel, Field, BaseSettings
from fastapi import FastAPI
from indexer import Indexer
from file_watcher import FileWatcher

app = FastAPI()

# Load environment variables
load_dotenv()

class IndexedDocument(BaseModel):
    document_id: str
    file_path: str
    content_hash: str
    last_modified_timestamp: float
    extracted_text_chunk: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    vector: List[float] = []

class Settings(BaseSettings):
    embedding_model_name: str = os.getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
    lancedb_uri: str = os.getenv("LANCEDB_URI", "./.lancedb")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    project_path: str = os.getenv("PROJECT_PATH", ".")
    ignore_patterns: List[str] = os.getenv("IGNORE_PATTERNS", ".git,__pycache__,*.pyc").split(",")

class SearchRequest(BaseModel):
    query: str
    top_k: int = 5

class SearchResponse(BaseModel):
    results: List[IndexedDocument]

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
        search_results = mcp_server_instance.indexer.search(
            query_text=request.query,
            top_k=request.top_k
        )
        return SearchResponse(results=search_results)
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=500,
            detail=f"Search failed: {str(e)}"
        )

# Create server instance and expose app for uvicorn
mcp_server_instance = MCPServer()
app = mcp_server_instance.app