# MCP Server: Software Project Indexing & Semantic Search

This project provides a Model Context Protocol (MCP) server designed to index software project files and offer semantic search capabilities over the indexed content. It monitors project directories for changes and maintains an up-to-date index.

For detailed design and architectural decisions, please refer to [ARCHITECTURE.md](ARCHITECTURE.md).

## Setup

1.  **Clone the repository:**
    ```bash
    git clone <your-repository-url>
    cd <repository-directory>
    ```

2.  **Create and activate a Python virtual environment:**
    ```bash
    python -m venv venv
    # On Windows
    .\venv\Scripts\activate
    # On macOS/Linux
    source venv/bin/activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Install development dependencies (for running tests):**
    ```bash
    pip install -r requirements-dev.txt
    ```

## Configuration

The server requires environment variables for configuration. Create a `.env` file in the project root directory:

```dotenv
# --- Required ---
# Path to the software project you want to index
PROJECT_PATH=/path/to/your/software/project

# --- Optional (Defaults Provided) ---
# URI for the LanceDB database. Can be a local path.
LANCEDB_URI=./.lancedb

# Name of the Sentence Transformer model to use for embeddings
EMBEDDING_MODEL_NAME=all-MiniLM-L6-v2

# Comma-separated list of glob patterns to ignore during indexing
# Example: IGNORE_PATTERNS=*.log,*.tmp,__pycache__/*,node_modules/*,.git/*
IGNORE_PATTERNS=__pycache__/*,.git/*,*.db

# Host and Port for the server
HOST=0.0.0.0
PORT=8000
```

**Explanation:**

*   `PROJECT_PATH`: **Required.** Absolute or relative path to the root directory of the project to be indexed.
*   `LANCEDB_URI`: Path where the LanceDB vector database will be stored. Defaults to `./.lancedb`.
*   `EMBEDDING_MODEL_NAME`: The Hugging Face Sentence Transformer model used for generating embeddings. Defaults to `all-MiniLM-L6-v2`. Other models might require installing different dependencies.
*   `IGNORE_PATTERNS`: Comma-separated list of glob patterns specifying files/directories to exclude from indexing. Defaults to `__pycache__/*,.git/*,*.db`.
*   `HOST`: Host address for the server. Defaults to `0.0.0.0`.
*   `PORT`: Port for the server. Defaults to `8000`.

## Running the Server

Ensure your `.env` file is configured correctly and your virtual environment is activated.

```bash
uvicorn mcp_server:app --reload --host $HOST --port $PORT
```

The server will start, initialize the indexer (potentially performing an initial scan if the database is new or empty), and begin watching the `PROJECT_PATH` for changes.

## API Endpoints

The server exposes the following HTTP endpoints:

### 1. Root

*   **Method:** `GET`
*   **Path:** `/`
*   **Description:** Simple health check endpoint.
*   **Example Response (200 OK):**
    ```json
    {
      "message": "MCP Indexing Server is running."
    }
    ```

### 2. Index Project

*   **Method:** `POST`
*   **Path:** `/index`
*   **Description:** Triggers a full re-indexing of the configured `PROJECT_PATH`. This can be useful if you suspect the index is out of sync or after changing ignore patterns.
*   **Example Request Body:**
    ```json
    {
      "project_path": "/path/to/your/software/project" // Optional, defaults to PROJECT_PATH from .env
    }
    ```
*   **Example Response (200 OK):**
    ```json
    {
      "message": "Indexing started for project: /path/to/your/software/project",
      "files_queued": 150
    }
    ```
*   **Example Response (400 Bad Request):**
    ```json
    {
      "detail": "Project path mismatch or not configured."
    }
    ```

### 3. Semantic Search

*   **Method:** `POST`
*   **Path:** `/search`
*   **Description:** Performs a semantic search over the indexed content for the configured `PROJECT_PATH`.
*   **Example Request Body:**
    ```json
    {
      "query": "How is user authentication handled?",
      "project_path": "/path/to/your/software/project", // Optional, defaults to PROJECT_PATH from .env
      "top_k": 5 // Optional, defaults to 5
    }
    ```
*   **Example Response (200 OK):**
    ```json
    {
      "query": "How is user authentication handled?",
      "results": [
        {
          "file_path": "src/auth/service.py",
          "score": 0.85,
          "content_preview": "... uses JWT tokens for authentication ..."
        },
        {
          "file_path": "docs/auth.md",
          "score": 0.78,
          "content_preview": "... Authentication Flow: User logs in -> Receives JWT ..."
        }
      ]
    }
    ```
*   **Example Response (404 Not Found):**
    ```json
    {
      "detail": "Index not found for project path: /path/to/your/software/project. Please index first."
    }
    ```

### 4. Get Indexing Status

*   **Method:** `GET`
*   **Path:** `/status/{project_path:path}`
*   **Description:** Retrieves the current indexing status for the specified project path. The project path must be URL-encoded if it contains special characters.
*   **Example Request:** `GET /status/%2Fpath%2Fto%2Fyour%2Fsoftware%2Fproject`
*   **Example Response (200 OK):**
    ```json
    {
      "project_path": "/path/to/your/software/project",
      "status": "idle", // or "indexing", "watching"
      "last_indexed_count": 150,
      "last_indexed_time": "2025-04-10T14:30:00Z",
      "error": null // or error message if indexing failed
    }
    ```
*   **Example Response (404 Not Found):**
    ```json
    {
      "detail": "Status not found for project path: /path/to/nonexistent/project"
    }
    ```

## Running Tests

Ensure you have installed the development dependencies (`requirements-dev.txt`).

From the project root directory:

```bash
pytest -v