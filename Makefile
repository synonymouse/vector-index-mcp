# Makefile for vector-index-mcp development

# Variables
VENV_DIR := .venv
PYTHON := $(VENV_DIR)/bin/python
PIP := $(VENV_DIR)/bin/pip

# Phony targets (targets that don't represent files)
.PHONY: all install-dev test lint run-dev clean help

# Default target
all: help

# Help target to display available commands
help:
	@echo "Available commands:"
	@echo "  make install-dev  - Set up the development environment (create venv, install deps)"
	@echo "  make test         - Run the test suite using pytest"
	@echo "  make lint         - Run linting and formatting checks using ruff"
	@echo "  make run-dev      - Run the development server with auto-reload"
	@echo "  make clean        - Remove temporary files (__pycache__, .pytest_cache, etc.)"
	@echo "  make help         - Show this help message"

# Setup development environment
install-dev: $(VENV_DIR)/bin/activate
$(VENV_DIR)/bin/activate: pyproject.toml
	@echo "Setting up development environment in $(VENV_DIR)..."
	test -d $(VENV_DIR) || python3 -m venv $(VENV_DIR)
	# Ensure pip is upgraded using the venv's python
	$(PYTHON) -m pip install --upgrade pip
	@echo "Installing project in editable mode with development dependencies..."
	# Install dependencies using the venv's python/pip
	$(PYTHON) -m pip install -e .[dev]
	@echo "Development environment ready. Activate with: source $(VENV_DIR)/bin/activate"
	@touch $(VENV_DIR)/bin/activate # Mark as updated

# Run tests
test: $(VENV_DIR)/bin/activate
	@echo "Running tests..."
	$(PYTHON) -m pytest -v

# Run linter/formatter
lint: $(VENV_DIR)/bin/activate
	@echo "Running linter and formatter (ruff)..."
	$(PYTHON) -m ruff check . --fix
	$(PYTHON) -m ruff format .

# Run development server
# Assumes HOST and PORT are set in the environment or uses defaults in .env
# Requires a .env file in the project root for development convenience
run-dev: $(VENV_DIR)/bin/activate
	@echo "Starting development server (uvicorn with reload)..."
	@echo "Starting development server, indexing current directory..."
	$(PYTHON) -m vector_index_mcp.main_mcp .

# Clean temporary files
clean:
	@echo "Cleaning up temporary files..."
	find . -type f -name '*.py[co]' -delete
	find . -type d -name '__pycache__' -delete
	rm -rf .pytest_cache
	rm -rf build dist *.egg-info
	# Optionally remove venv: rm -rf $(VENV_DIR)