import os
import hashlib
import logging
import asyncio  # Added for asyncio.run_coroutine_threadsafe
from pathlib import Path
import pathspec
from typing import List, Dict, TypedDict, Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from .indexer import Indexer  # Indexer methods are now async
from .models import IndexedDocument, FileMetadata
from .content_extractor import chunk_content


class KnownFileInfo(TypedDict):
    """
    Structure for storing information about files that the watcher
    has already processed or is aware of. Used to detect changes.
    """

    hash: str
    last_modified: float


class FileWatcher:
    """
    Monitors a project directory for file changes (creations, modifications, deletions)
    and triggers re-indexing actions accordingly. It uses a .gitignore-style mechanism
    to exclude specified files and directories.
    """

    def __init__(
        self,
        project_path: str,
        indexer: Optional[Indexer],  # Indexer can be None initially
        event_loop: Optional[asyncio.AbstractEventLoop],  # Added event_loop
        ignore_patterns: List[str] = None,
        abs_lancedb_path_to_ignore: Optional[str] = None,
    ):
        """
        Initializes the FileWatcher.

        Args:
            project_path: The root path of the project to monitor.
            indexer: An instance of the Indexer to use for adding/removing documents.
            ignore_patterns: A list of .gitignore-style patterns to ignore.
                             If None, only .gitignore from project_path is used.
            abs_lancedb_path_to_ignore: The absolute canonical path to the LanceDB
                                        directory, which should always be ignored.
        """
        self.project_path = project_path
        self.project_root = Path(project_path).resolve()
        self.indexer: Optional[Indexer] = indexer
        self.event_loop: Optional[asyncio.AbstractEventLoop] = event_loop
        self.abs_lancedb_path_to_ignore = abs_lancedb_path_to_ignore
        self.known_files: Dict[str, KnownFileInfo] = {}

        patterns = ignore_patterns or []
        gitignore_path = self.project_root / ".gitignore"
        if gitignore_path.is_file():
            try:
                with open(gitignore_path, "r", encoding="utf-8") as f:
                    patterns.extend(f.read().splitlines())
                logging.debug(
                    f"Loaded {len(patterns)} patterns from .gitignore at {gitignore_path}"
                )
            except Exception as e:
                logging.error(f"Error reading .gitignore file at {gitignore_path}: {e}")
        # PathSpec is used to efficiently match paths against .gitignore style patterns
        self.path_spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
        logging.info(
            f"FileWatcher initialized for project: {self.project_path}. Ignoring {len(patterns)} patterns."
        )

        self.observer = Observer()
        self.event_handler = ProjectEventHandler(self)

    def _calculate_hash(self, file_path: str) -> str:
        """Calculates the SHA256 hash of a file's content."""
        try:
            with open(file_path, "rb") as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
                return file_hash
        except FileNotFoundError:
            logging.warning(f"File not found when calculating hash: {file_path}")
            return ""
        except Exception as e:
            logging.error(f"Error calculating hash for {file_path}: {e}", exc_info=True)
            return ""

    def _get_last_modified(self, file_path: str) -> float:
        """Gets the last modified timestamp of a file."""
        try:
            return os.path.getmtime(file_path)
        except FileNotFoundError:
            logging.warning(
                f"File not found when getting last modified time: {file_path}"
            )
            return 0
        except Exception as e:
            logging.error(
                f"Error getting last modified time for {file_path}: {e}", exc_info=True
            )
            return 0

    def _should_ignore(self, path: str) -> bool:
        """
        Determines if a given path should be ignored based on .gitignore rules,
        being outside the project root, or being the LanceDB directory itself.
        """
        real_event_path = os.path.realpath(path)

        # Explicitly ignore the LanceDB directory to prevent self-indexing or loops
        if self.abs_lancedb_path_to_ignore and real_event_path.startswith(
            self.abs_lancedb_path_to_ignore
        ):
            logging.debug(
                f"Ignoring path '{real_event_path}' as it is within the LanceDB directory '{self.abs_lancedb_path_to_ignore}'."
            )
            return True

        absolute_path = Path(path).resolve()
        if (
            absolute_path.is_dir()
        ):  # Always ignore directories themselves, only process files
            logging.debug(f"Ignoring directory path: {absolute_path}")
            return True

        try:
            # PathSpec works with paths relative to the directory where .gitignore (or patterns) are defined.
            relative_path = absolute_path.relative_to(self.project_root)
            is_ignored = self.path_spec.match_file(str(relative_path))
            if is_ignored:
                logging.debug(
                    f"Ignoring path '{path}' due to match in ignore patterns (relative: '{relative_path}')."
                )
            return is_ignored
        except ValueError:
            # This occurs if absolute_path is not inside self.project_root
            logging.debug(
                f"Ignoring path '{path}' as it is outside the project root '{self.project_root}'."
            )
            return True

    def _process_and_index_file(self, file_path: str) -> bool:
        """
        Reads the content of a file, splits it into chunks, generates embeddings,
        and adds/updates these chunks in the index. Updates `known_files` state.

        Returns:
            True if processing was successful (or file was skipped appropriately), False on error.
        """
        if not self.indexer or not self.event_loop:
            logging.warning(
                f"Indexer ({'present' if self.indexer else 'MISSING'}) or event_loop ({'present' if self.event_loop else 'MISSING'}) "
                f"not available in FileWatcher. Skipping processing for {file_path}"
            )
            return False
        try:
            file_hash = self._calculate_hash(file_path)
            last_modified = self._get_last_modified(file_path)

            if not file_hash:  # Hash calculation failed (e.g. file disappeared)
                logging.warning(
                    f"Could not calculate hash for {file_path}, skipping processing."
                )
                return False  # Cannot proceed without a hash

            # Read file content
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            chunks = chunk_content(
                content
            )  # Assumes chunk_content handles empty content gracefully
            total_chunks = len(chunks)

            if total_chunks == 0:
                logging.info(
                    f"File '{file_path}' is empty or resulted in no processable chunks. Removing from index if present."
                )
                # Record its hash/mtime to avoid reprocessing if unchanged but empty
                self.known_files[file_path] = {
                    "hash": file_hash,
                    "last_modified": last_modified,
                }
                # Ensure any previous index entries for this file are removed if it became empty
                if self.indexer and self.event_loop:  # Check event_loop too
                    future = asyncio.run_coroutine_threadsafe(
                        self.indexer.remove_document(file_path), self.event_loop
                    )
                    # future.result(timeout=5) # Optional: wait for completion, but can block watcher
                    logging.debug(
                        f"Scheduled remove_document for empty file {file_path}. Future: {future}"
                    )
                return True  # Processed (by acknowledging it's empty)

            # Process and index each chunk
            for i, chunk_text in enumerate(chunks):
                chunk_doc_id = f"{file_path}::{i}"
                document = IndexedDocument(
                    document_id=chunk_doc_id,
                    file_path=file_path,  # Store relative or absolute path consistently
                    content_hash=file_hash,
                    last_modified_timestamp=last_modified,
                    chunk_index=i,
                    total_chunks=total_chunks,
                    extracted_text_chunk=chunk_text,
                    metadata=FileMetadata(original_path=file_path),
                    # The 'vector' field is populated by the indexer's add_or_update_document method
                )
                if self.indexer and self.event_loop:  # Check event_loop too
                    future = asyncio.run_coroutine_threadsafe(
                        self.indexer.add_or_update_document(document), self.event_loop
                    )
                    # future.result(timeout=5) # Optional: wait for completion
                    logging.debug(
                        f"Scheduled add_or_update_document for chunk {document.document_id}. Future: {future}"
                    )

            # Update known_files state only after successful processing of all chunks
            self.known_files[file_path] = {
                "hash": file_hash,
                "last_modified": last_modified,
            }
            logging.info(
                f"Successfully indexed {total_chunks} chunks for file: {file_path}"
            )
            return True

        except FileNotFoundError:
            logging.warning(
                f"File not found during processing (it may have been deleted rapidly): {file_path}"
            )
            # If file is gone, ensure it's removed from known_files and index
            if file_path in self.known_files:
                del self.known_files[file_path]
            if self.indexer and self.event_loop:  # Check event_loop too
                future = asyncio.run_coroutine_threadsafe(
                    self.indexer.remove_document(file_path), self.event_loop
                )
                # future.result(timeout=5)
                logging.debug(
                    f"Scheduled remove_document for file not found during processing {file_path}. Future: {future}"
                )
            return False  # Indicate processing did not complete for this file
        except Exception as e:
            logging.error(f"Error processing file {file_path}: {e}", exc_info=True)
            return False

    def initial_scan(self):
        """
        Performs an initial scan of the project directory, processing and indexing
        all relevant files that are not ignored.
        """
        logging.info(f"Starting initial project scan for: {self.project_path}...")
        processed_files_count = 0
        for root, _, files in os.walk(self.project_path, topdown=True):
            # Filter out ignored directories from os.walk itself if possible,
            # though _should_ignore will also catch files within them.
            # For now, _should_ignore handles individual files.
            for file_name in files:
                file_path = os.path.join(root, file_name)
                if self._should_ignore(file_path):
                    continue

                # Check if file is already known and unchanged to avoid redundant processing
                known_info = self.known_files.get(file_path)
                if known_info:
                    current_hash = self._calculate_hash(file_path)
                    current_modified = self._get_last_modified(file_path)
                    if (
                        current_hash == known_info["hash"]
                        and current_modified == known_info["last_modified"]
                    ):
                        logging.debug(
                            f"Skipping unchanged known file during initial scan: {file_path}"
                        )
                        processed_files_count += (
                            1  # Count as "processed" in the sense of "checked"
                        )
                        continue

                logging.debug(f"Initial scan: Processing file {file_path}")
                if self._process_and_index_file(file_path):
                    processed_files_count += 1
        logging.info(
            f"Initial scan complete. Processed (checked or indexed) {processed_files_count} files."
        )

    def process_creation(self, file_path: str):
        """Handles file creation events."""
        if self._should_ignore(file_path):
            return
        logging.info(f"File created: {file_path}. Processing for indexing.")
        self._process_and_index_file(file_path)

    def process_modification(self, file_path: str):
        """Handles file modification events."""
        if self._should_ignore(file_path):
            return

        logging.debug(f"File modified event for: {file_path}")
        current_hash = self._calculate_hash(file_path)
        current_modified = self._get_last_modified(file_path)

        if (
            not current_hash
        ):  # Hash calculation failed (e.g., file deleted quickly after modify event)
            logging.warning(
                f"Hash calculation failed for modified file {file_path}. It might have been deleted. Removing if known."
            )
            if file_path in self.known_files:
                self.process_deletion(file_path)  # Treat as deletion
            return

        known_info = self.known_files.get(file_path)
        needs_reindex = False
        if not known_info:
            logging.warning(
                f"Modified event for a file not previously known: {file_path}. Processing as new creation."
            )
            needs_reindex = True
        elif (
            current_hash != known_info["hash"]
            or current_modified != known_info["last_modified"]
        ):
            logging.info(
                f"Change detected in {file_path} (Hash or MTime mismatch). Re-indexing..."
            )
            needs_reindex = True
        else:
            logging.debug(
                f"No significant change (hash and mtime match) detected for {file_path}. Skipping re-index."
            )

        if needs_reindex:
            try:
                # Remove old version from index before adding new one
                if self.indexer and self.event_loop:  # Check event_loop too
                    # This ensures that if the number of chunks changes, old ones are gone.
                    future = asyncio.run_coroutine_threadsafe(
                        self.indexer.remove_document(file_path), self.event_loop
                    )
                    # future.result(timeout=5) # Wait for removal before re-adding
                    logging.debug(
                        f"Scheduled removal of old document chunks for {file_path} before re-indexing. Future: {future}"
                    )
                else:
                    logging.warning(
                        f"Indexer not available. Cannot remove old chunks for modified file {file_path}."
                    )
                self._process_and_index_file(file_path)  # This will update known_files
            except Exception as e:
                logging.error(
                    f"Error during re-indexing of modified file {file_path}: {e}",
                    exc_info=True,
                )

    def process_deletion(self, file_path: str):
        """Handles file deletion events."""
        # No need to check _should_ignore here. If we knew about it, we should remove it from index.
        if file_path in self.known_files:
            logging.info(
                f"File deleted: {file_path}. Removing from index and known files."
            )
            try:
                if self.indexer and self.event_loop:  # Check event_loop too
                    future = asyncio.run_coroutine_threadsafe(
                        self.indexer.remove_document(file_path), self.event_loop
                    )
                    # future.result(timeout=5)
                    logging.debug(
                        f"Scheduled remove_document for deleted file {file_path}. Future: {future}"
                    )
                else:
                    logging.warning(
                        f"Indexer not available. Cannot remove index entries for deleted file {file_path}."
                    )
                del self.known_files[file_path]
            except Exception as e:
                logging.error(
                    f"Error removing index entries or from known_files for deleted file {file_path}: {e}",
                    exc_info=True,
                )
        else:
            # This can happen if a file is created and deleted quickly before watcher processes creation,
            # or if an ignored file is deleted.
            logging.debug(
                f"Deletion event for an untracked or already removed file: {file_path}"
            )

    def start(self):
        """Starts the file system observer."""
        if not self.observer.is_alive():
            self.observer.schedule(
                self.event_handler, self.project_path, recursive=True
            )
            self.observer.start()
            logging.info(f"File watcher started for directory: {self.project_path}")
        else:
            logging.warning("File watcher start requested, but it is already running.")

    def stop(self):
        """Stops the file system observer."""
        if self.observer.is_alive():
            self.observer.stop()
            self.observer.join(timeout=5)
            if self.observer.is_alive():
                logging.warning(
                    "File watcher observer thread did not stop cleanly after timeout."
                )
            else:
                logging.info("File watcher stopped successfully.")
        else:
            logging.info("File watcher stop requested, but it was not running.")


class ProjectEventHandler(FileSystemEventHandler):
    """
    Handles file system events (created, modified, deleted, moved) from the
    watchdog observer and delegates processing to the FileWatcher instance.
    """

    def __init__(self, file_watcher: FileWatcher):
        """
        Initializes the event handler.

        Args:
            file_watcher: The FileWatcher instance that will process the events.
        """
        super().__init__()
        self.file_watcher = file_watcher
        logging.debug("ProjectEventHandler initialized.")

    def on_created(self, event):
        """Called when a file or directory is created."""
        super().on_created(event)
        if not event.is_directory:
            logging.debug(f"Event: created file {event.src_path}")
            self.file_watcher.process_creation(event.src_path)

    def on_modified(self, event):
        """Called when a file or directory is modified."""
        super().on_modified(event)
        if not event.is_directory:
            logging.debug(f"Event: modified file {event.src_path}")
            self.file_watcher.process_modification(event.src_path)

    def on_deleted(self, event):
        """Called when a file or directory is deleted."""
        super().on_deleted(event)
        if not event.is_directory:
            logging.debug(f"Event: deleted file {event.src_path}")
            self.file_watcher.process_deletion(event.src_path)

    def on_moved(self, event):
        """Called when a file or directory is moved or renamed."""
        super().on_moved(event)
        # A move is treated as a deletion of the source and a creation of the destination.
        logging.debug(f"Event: moved {event.src_path} -> {event.dest_path}")
        if not event.is_directory:
            self.file_watcher.process_deletion(event.src_path)
            self.file_watcher.process_creation(event.dest_path)
        else:
            # Handling directory moves can be complex. A simple approach is to
            # trigger a re-scan or more granularly process files within.
            # For now, log and rely on individual file events if they are generated,
            # or initial_scan if needed for full sync after massive moves.
            logging.info(
                f"Directory moved: {event.src_path} -> {event.dest_path}. Individual file events will be processed if generated by OS."
            )
            # Potentially, one could iterate through dest_path and process creations,
            # and assume files in src_path are implicitly deleted from index if not re-created.
            # This depends on how watchdog reports moves of directories and their contents.
