import logging
import json
from typing import List
from fastapi import APIRouter, HTTPException, Depends

# Import models and dependency provider
from models import SearchRequest, SearchResponse, SearchResultItem
from main import get_server_instance
from mcp_server import MCPServer

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/search", response_model=SearchResponse, tags=["Search"])
async def search_documents(
    request: SearchRequest,
    server_instance: MCPServer = Depends(get_server_instance),
):
    # NOTE: Search implicitly uses the index built for the configured project path.
    # Use the injected instance
    if server_instance.status == "Scanning":
        raise HTTPException(
            status_code=409, detail="Search unavailable: Indexing is currently in progress."
        )
    if server_instance.status == "Error":
        raise HTTPException(
            status_code=503,
            detail=f"Search unavailable due to indexing error: {server_instance.current_error}",
        )
    # Also check for the initial state before the first scan
    if server_instance.status in ["Initializing", "Idle - Initial Scan Required"]:
        raise HTTPException(
            status_code=503,
            detail="Search unavailable: Index not yet built or server initializing.",
        )

    try:
        # Get raw results (List[Dict[str, Any]]) from indexer
        # Access indexer via the injected server instance
        raw_results = server_instance.indexer.search(
            query_text=request.query, top_k=request.top_k
        )

        # Process results for API response
        processed_results: List[SearchResultItem] = []
        for doc in raw_results:
            try:
                # Parse the metadata JSON string if it exists and is valid
                # Assuming 'doc' is a dictionary-like object from the indexer search result
                metadata_json = doc.get("metadata_json")
                parsed_metadata = json.loads(metadata_json) if metadata_json else {}
            except (json.JSONDecodeError, TypeError):
                # Handle cases where metadata might be invalid JSON or None
                parsed_metadata = {"error": "invalid or missing metadata format"}
                log.warning(f"Invalid metadata format for doc_id {doc.get('document_id')}: {metadata_json}")


            # Create the response item, copying fields and adding parsed metadata
            processed_results.append(
                SearchResultItem(
                    document_id=doc.get("document_id"),
                    file_path=doc.get("file_path"),
                    content_hash=doc.get("content_hash"),
                    last_modified_timestamp=doc.get("last_modified_timestamp"),
                    extracted_text_chunk=doc.get("extracted_text_chunk"),
                    metadata=parsed_metadata,
                    # vector field is intentionally excluded from the response
                )
            )

        return SearchResponse(results=processed_results)
    except Exception as e:
        # Keep existing error handling
        log.error(f"Search failed for query '{request.query[:50]}...': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")