import os
import hashlib
import logging
import json
from pathlib import Path
import pathspec
from typing import List, Dict, TypedDict
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from indexer import Indexer
from models import IndexedDocument, FileMetadata
from content_extractor import chunk_content

class KnownFileInfo(TypedDict):
    """Structure for storing info about known files."""
    hash: str
    last_modified: float


class FileWatcher:
    def __init__(
        self, project_path: str, indexer: Indexer, ignore_patterns: List[str] = None
    ):
        self.project_path = project_path
        self.project_root = Path(project_path).resolve()
        self.indexer = indexer
        self.known_files: Dict[str, KnownFileInfo] = {}

        patterns = ignore_patterns or []
        gitignore_path = self.project_root / ".gitignore"
        if gitignore_path.is_file():
            try:
                with open(gitignore_path, "r") as f:
                    patterns.extend(f.read().splitlines())
            except Exception as e:
                logging.error(f"Error reading .gitignore file at {gitignore_path}: {e}")
        self.path_spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)

        self.observer = Observer()
        self.event_handler = ProjectEventHandler(self)

    def _calculate_hash(self, file_path: str) -> str:
        try:
            with open(file_path, "rb") as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
                return file_hash
        except Exception as e:
            logging.error(f"Error calculating hash for {file_path}: {e}")
            return ""

    def _get_last_modified(self, file_path: str) -> float:
        try:
            return os.path.getmtime(file_path)
        except Exception as e:
            logging.error(f"Error getting last modified time for {file_path}: {e}")
            return 0

    def _should_ignore(self, file_path: str) -> bool:
        """Check if a file path should be ignored based on .gitignore rules."""
        absolute_path = Path(file_path).resolve()
        if absolute_path.is_dir():
            return True

        try:
            relative_path = absolute_path.relative_to(self.project_root)
            return self.path_spec.match_file(str(relative_path))
        except ValueError:
            # File is outside the project root, ignore it
            return True
        return False

    def _process_and_index_file(self, file_path: str) -> bool:
        """Reads, chunks, and indexes a single file. Updates known_files."""
        try:
            file_hash = self._calculate_hash(file_path)
            last_modified = self._get_last_modified(file_path)

            if not file_hash:
                return False

            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            chunks = chunk_content(content)
            total_chunks = len(chunks)

            if total_chunks == 0:
                logging.info(f"Skipping empty or unchunkable file: {file_path}")
                # Even if empty, record its hash/mtime to avoid reprocessing if unchanged
                self.known_files[file_path] = {
                    "hash": file_hash,
                    "last_modified": last_modified,
                }
                # Ensure any previous index entries are removed if the file became empty
                self.indexer.remove_document(file_path)
                return True


            for i, chunk_text in enumerate(chunks):
                chunk_doc_id = f"{file_path}::{i}"
                document = IndexedDocument(
                    document_id=chunk_doc_id,
                    file_path=file_path,
                    content_hash=file_hash,
                    last_modified_timestamp=last_modified,
                    chunk_index=i,
                    total_chunks=total_chunks,
                    extracted_text_chunk=chunk_text,
                    metadata=FileMetadata(original_path=file_path),
                    # Vector will be added by indexer
                )
                self.indexer.add_or_update_document(document)

            # Update known files only after successful processing of all chunks
            self.known_files[file_path] = {
                "hash": file_hash,
                "last_modified": last_modified,
            }
            logging.info(f"Indexed {total_chunks} chunks for file: {file_path}")
            return True

        except Exception as e:
            logging.error(f"Error processing file {file_path}: {e}")
            return False

    def initial_scan(self):
        logging.info("Starting initial project scan...")
        processed_files = 0
        for root, _, files in os.walk(self.project_path):
            for file in files:
                file_path = os.path.join(root, file)
                if self._should_ignore(file_path):
                    continue
                if self._process_and_index_file(file_path):
                    processed_files += 1
        logging.info(f"Initial scan complete. Processed {processed_files} files.")

    def process_creation(self, file_path: str):
        if self._should_ignore(file_path):
            return
        logging.debug(f"Processing creation: {file_path}")
        self._process_and_index_file(file_path)

    def process_modification(self, file_path: str):
        if self._should_ignore(file_path):
            return

        logging.debug(f"Processing modification: {file_path}")
        current_hash = self._calculate_hash(file_path)
        current_modified = self._get_last_modified(file_path)

        known_info = self.known_files.get(file_path)

        # Check if the file is known and if hash or modification time has changed
        needs_update = False
        if not known_info:
            needs_update = (
                True  # File wasn't known before (edge case, treat as creation)
            )
            logging.warning(
                f"Modified event for unknown file: {file_path}. Processing as new."
            )
        elif (
            current_hash != known_info["hash"]
            or current_modified != known_info["last_modified"]
        ):
            needs_update = True
        # Add check for hash calculation failure
        elif not current_hash:
            logging.error(
                f"Hash calculation failed for modified file {file_path}. Skipping update."
            )
            needs_update = False  # Cannot proceed without hash

        if needs_update:
            logging.info(f"Detected change in {file_path}. Re-indexing...")
            try:
                self.indexer.remove_document(file_path)
                logging.debug(f"Removed old chunks for {file_path}")

                self._process_and_index_file(file_path)

            except Exception as e:
                logging.error(
                    f"Error during re-indexing of modified file {file_path}: {e}"
                )
        else:
            logging.debug(f"No significant change detected for {file_path}. Skipping.")

    def process_deletion(self, file_path: str):
        # No need to check _should_ignore here, if it was indexed, we should remove it.
        if file_path in self.known_files:
            logging.debug(f"Processing deletion: {file_path}")
            try:
                # The indexer's remove_document should handle removing all chunks
                self.indexer.remove_document(file_path)
                del self.known_files[file_path]
                logging.info(f"Removed index entries for deleted file: {file_path}")
            except Exception as e:
                logging.error(
                    f"Error removing index entries for deleted file {file_path}: {e}"
                )
        else:
            logging.debug(f"Deletion event for untracked file: {file_path}")

    def start(self):
        self.observer.schedule(self.event_handler, self.project_path, recursive=True)
        self.observer.start()

    def stop(self):
        self.observer.stop()
        self.observer.join()


class ProjectEventHandler(FileSystemEventHandler):
    def __init__(self, file_watcher: FileWatcher):
        self.file_watcher = file_watcher

    def on_created(self, event):
        if not event.is_directory:
            self.file_watcher.process_creation(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self.file_watcher.process_modification(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self.file_watcher.process_deletion(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            # Treat move as deletion of old path and creation of new path
            logging.debug(f"Processing move: {event.src_path} -> {event.dest_path}")
            self.file_watcher.process_deletion(event.src_path)
            self.file_watcher.process_creation(event.dest_path)
