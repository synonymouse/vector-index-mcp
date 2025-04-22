import logging
from typing import List
from fastapi import APIRouter, HTTPException, Depends

from ..models import SearchRequest, SearchResponse, SearchResultItem, FileMetadata
from .. import dependencies
from ..mcp_server import MCPServer, ServerStatus

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/search", response_model=SearchResponse, tags=["Search"])
async def search_documents(
    request: SearchRequest,
    server_instance: MCPServer = Depends(dependencies.get_server_instance),
):
    # NOTE: Search implicitly uses the index built for the configured project path.

    if server_instance.status == ServerStatus.INITIALIZING:
        raise HTTPException(
            status_code=503, detail="Server is initializing, please try again later."
        )
    elif server_instance.status == ServerStatus.ERROR:
        error_msg = (
            f"Server initialization failed: {server_instance.initialization_error}"
            if server_instance.initialization_error
            else "Server initialization failed."
        )
        raise HTTPException(status_code=500, detail=error_msg)
    # Only proceed if status is READY (implicitly, as other states raise exceptions)

    try:
        # Ensure indexer exists before trying to search (it should if status is READY)
        if not server_instance.indexer:
            raise HTTPException(
                status_code=500,
                detail="Indexer not available despite server being ready.",
            )

        raw_results = server_instance.indexer.search(
            query_text=request.query, top_k=request.top_k
        )

        processed_results: List[SearchResultItem] = []
        for doc in raw_results:
            metadata_obj = doc.get("metadata")
            # Basic check: LanceDB might return dicts, ensure it's usable.
            if not isinstance(metadata_obj, (dict, FileMetadata)):
                log.warning(
                    f"Unexpected metadata format for doc_id {doc.get('document_id')}: {type(metadata_obj)}"
                )
                metadata_obj = FileMetadata(original_path="<metadata error>")

            processed_results.append(
                SearchResultItem(
                    document_id=doc.get("document_id"),
                    file_path=doc.get("file_path"),
                    content_hash=doc.get("content_hash"),
                    last_modified_timestamp=doc.get("last_modified_timestamp"),
                    extracted_text_chunk=doc.get("extracted_text_chunk"),
                    metadata=metadata_obj,
                )
            )

        return SearchResponse(results=processed_results)
    except Exception as e:
        # Keep existing error handling
        log.error(
            f"Search failed for query '{request.query[:50]}...': {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")
