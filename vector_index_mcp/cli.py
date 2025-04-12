import os
import uvicorn
from dotenv import load_dotenv, find_dotenv
import logging

# Configure logging early
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
log = logging.getLogger(__name__)


def main():
    """
    Entry point for the vector-index-mcp command.
    Loads environment variables from the current working directory's .env file
    and starts the Uvicorn server.
    """
    log.info("Attempting to load .env file from current working directory...")
    # find_dotenv(usecwd=True) searches the current working directory and parents
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        log.info(f"Loading environment variables from: {dotenv_path}")
        load_dotenv(dotenv_path=dotenv_path)
    else:
        log.warning(
            "No .env file found in the current directory or parent directories."
        )

    # Import the app *after* loading dotenv, in case app initialization depends on env vars
    try:
        from .main import app  # noqa: F401 - App is used by uvicorn string reference
    except ImportError as e:
        log.critical(f"Failed to import FastAPI app from .main: {e}", exc_info=True)
        return  # Exit if app cannot be imported

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))  # Ensure port is an integer

    log.info(f"Starting MCP Indexing Server on {host}:{port}")
    # Note: We don't use reload=True here as this is for the packaged application
    uvicorn.run(
        "vector_index_mcp.main:app",
        host=host,
        port=port,
        log_level="info",  # Uvicorn's log level
    )


if __name__ == "__main__":
    # This allows running the script directly for testing, though 'pipx run' is the intended method
    main()
