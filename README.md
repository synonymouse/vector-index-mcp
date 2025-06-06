# MCP Server: Software Project Indexing & Semantic Search

This project provides a Model Context Protocol (MCP) server designed to index software project files and offer semantic search capabilities over the indexed content. It monitors project directories for changes and maintains an up-to-date index.

For detailed design and architectural decisions, please refer to [ARCHITECTURE.md](ARCHITECTURE.md).

## Usage (End Users)

This server is designed to be run easily by pointing it to the software project you want to index.

### 1. Run the Server

Navigate to a convenient location in your terminal. The server is started using the `python -m` command, specifying the module and the path to the project you wish to index.

```bash
python -m vector_index_mcp.main_mcp <path_to_your_project>
```

For example, to index a project located at `~/dev/my_cool_project`, you would run:
```bash
python -m vector_index_mcp.main_mcp ~/dev/my_cool_project
```
Or, to index the current directory:
```bash
python -m vector_index_mcp.main_mcp .
```

The server will start, begin watching the specified project path for changes, and create/use a LanceDB database within that project's directory (by default).

### 2. Configuration

*   **`PROJECT_PATH` (Required, via Command Line):**
    *   This is now passed as a direct command-line argument to the server, as shown above. It specifies the root directory of the software project you want to index.

*   **Environment Variables (Optional, via `.env` file):**
    *   Other settings can be configured using an `.env` file placed in the directory **from where you run the `python -m ...` command**. The server uses `pydantic-settings` and will automatically load this `.env` file.
    *   The following variables are supported:
        *   `LANCEDB_URI`: Path where the LanceDB vector database will be stored.
            *   Default: `./.lancedb` (relative to the indexed project's path, meaning it's stored within the project itself).
        *   `EMBEDDING_MODEL_NAME`: The Hugging Face Sentence Transformer model used for generating embeddings.
            *   Default: `all-MiniLM-L6-v2`.
        *   `IGNORE_PATTERNS`: Comma-separated list of glob patterns specifying files/directories to exclude from indexing (e.g., `__pycache__/*,.git/*,*.db`). These patterns are relative to the `PROJECT_PATH`.
            *   Default: `.*,*.db,*.sqlite,*.log,node_modules/*,venv/*,.git/*`
        *   `LOG_LEVEL`: Logging level for the application.
            *   Default: `INFO`.

    *   **Example `.env` file (place this where you run the server command):**
        ```dotenv
        # --- Optional (Defaults Provided) ---
        # URI for the LanceDB database. Default is './.lancedb' (inside the indexed project).
        # LANCEDB_URI=./my_project_index_data/.lancedb

        # Name of the Sentence Transformer model. Default is 'all-MiniLM-L6-v2'.
        # EMBEDDING_MODEL_NAME=sentence-transformers/paraphrase-multilingual-mpnet-base-v2

        # Comma-separated list of glob patterns to ignore.
        # Default: .*,*.db,*.sqlite,*.log,node_modules/*,venv/*,.git/*
        # IGNORE_PATTERNS=*.log,*.tmp,node_modules/*,dist/*,build/*

        # Logging level. Default is INFO
        # LOG_LEVEL=DEBUG
        ```

### 3. Accessing the Server

Once running, the server provides its functionality via the Model Context Protocol (MCP). You can interact with it using MCP-compatible clients, such as:
*   Claude Desktop (if configured to connect to this server)
*   `mcp inspect` CLI tool for exploring available tools and resources.

The server will announce its availability over MCP.

## Development Setup

If you want to contribute to or modify the server itself:

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/synonymouse/vector-index-mcp.git
    cd vector-index-mcp
    ```
2.  **Set up the development environment:**
    This command creates a virtual environment (`.venv`) if it doesn't exist, and installs the project in editable mode (`-e`) along with all runtime and development dependencies specified in `pyproject.toml` (`.[dev]`).
    ```bash
    make install-dev
    ```
3.  **Activate the virtual environment:**
    ```bash
    source .venv/bin/activate
    ```
    (On Windows using Git Bash or WSL, the command is the same. For Command Prompt/PowerShell, use `.venv\Scripts\activate`)

4.  **(Optional) Create a `.env` file in the `vector-index-mcp` project root** for development-specific settings if needed. For example, to specify a test project path when running directly:
    ```bash
    # In vector-index-mcp/.env (for development)
    # No PROJECT_PATH here, as it's passed as an argument
    LANCEDB_URI=./.dev_lancedb
    EMBEDDING_MODEL_NAME=all-MiniLM-L6-v2
    LOG_LEVEL=DEBUG
    IGNORE_PATTERNS=__pycache__/*,.git/*,*.tmp
    ```
    Then run the server pointing to a test project:
    ```bash
    python vector_index_mcp/main_mcp.py ../path/to/your/test_project
    ```

## Development Commands

Ensure your virtual environment is activated (`source .venv/bin/activate`) before running these `make` commands from the `vector-index-mcp` project root:

*   `make test`: Run the test suite using `pytest`.
*   `make lint`: Check code style and format using `ruff`.
*   `make run-dev`: Runs the development server, indexing the current directory.
*   `make clean`: Remove temporary files (`__pycache__`, build artifacts, etc.).
*   `make help`: Display a list of available commands.

## MCP Interface (MCP Tools)

The server provides its functionality through the Model Context Protocol (MCP). Interaction with these tools occurs via an MCP-compatible client.

### Available MCP Tools

The following tools are exposed by the server (defined in `vector_index_mcp/main_mcp.py`):

1.  **`trigger_index`**
    *   **Description:** Triggers the indexing process for the specified project path.
    *   **Arguments:**
        *   `force_reindex: bool` (optional, default: `False`): If true, forces a re-index, clearing any existing index data for the project first.
    *   **Note:** The `project_path` itself is implicitly handled by the server instance, as it's configured at startup. The tool acts on this pre-configured path.

2.  **`get_status`**
    *   **Description:** Gets the current status of the indexer (e.g., idle, indexing, last_indexed_time).
    *   **Arguments:** None.

3.  **`search_index`**
    *   **Description:** Searches the vector index for a given query.
    *   **Arguments:**
        *   `query: str` (required): The search query string.
        *   `top_k: int` (optional, default: `5`): The number of top results to return.

### Interacting with MCP Tools

Use an MCP client (like `mcp inspect` or a programmatic client) to discover and call these tools. The client will handle the communication with the server.

## Project Structure

```mermaid
graph TD
    MainMCP["vector_index_mcp/main_mcp.py (FastMCP Server)"]
    MCPServerClass["vector_index_mcp/mcp_server.py (MCPServer Class)"]
    Indexer["vector_index_mcp/indexer.py"]
    FileWatcher["vector_index_mcp/file_watcher.py"]
    ContentExtractor["vector_index_mcp/content_extractor.py"]
    Config["vector_index_mcp/config.py (Settings)"]
    Models["vector_index_mcp/models.py (Data Models)"]
    PyProject["pyproject.toml"]
    README["README.md"]
    REFACTORING_PLAN["REFACTORING_PLAN.md"]
    Tests["tests/"]

    MainMCP --> MCPServerClass
    MainMCP --> Config
    MCPServerClass --> Indexer
    MCPServerClass --> FileWatcher
    MCPServerClass --> Config
    Indexer --> ContentExtractor
    Indexer --> Models
    Indexer --> Config
    FileWatcher --> Indexer
    FileWatcher --> Models
    FileWatcher --> Config

    PyProject -. Used for build and dependencies .-> MainMCP
    PyProject -. Used for build and dependencies .-> MCPServerClass
    PyProject -. Used for build and dependencies .-> Indexer
    PyProject -. Used for build and dependencies .-> FileWatcher
    PyProject -. Used for build and dependencies .-> ContentExtractor
    PyProject -. Used for build and dependencies .-> Config
    PyProject -. Used for build and dependencies .-> Models
