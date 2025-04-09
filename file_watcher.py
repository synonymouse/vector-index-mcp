import os
import time
import hashlib
import logging
import json
from pathlib import Path
from typing import List, Set, Dict, Any
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from indexer import Indexer
from models import IndexedDocument


class FileWatcher:
    def __init__(self, project_path: str, indexer: Indexer, ignore_patterns: List[str] = None):
        self.project_path = project_path
        self.indexer = indexer
        self.ignore_patterns = ignore_patterns or ['.git', '__pycache__', '*.pyc']
        self.known_files: Dict[str, Dict[str, Any]] = {}  # {file_path: {'hash': str, 'last_modified': float}}
        
        self.observer = Observer()
        self.event_handler = ProjectEventHandler(self)
    
    def _calculate_hash(self, file_path: str) -> str:
        try:
            with open(file_path, 'rb') as f:
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
        path = Path(file_path)
        if path.is_dir():
            return True
        
        for pattern in self.ignore_patterns:
            if path.match(pattern):
                return True
        return False
    
    def initial_scan(self):
        for root, _, files in os.walk(self.project_path):
            for file in files:
                file_path = os.path.join(root, file)
                if self._should_ignore(file_path):
                    continue
                
                try:
                    file_hash = self._calculate_hash(file_path)
                    last_modified = self._get_last_modified(file_path)
                    self.known_files[file_path] = {
                        'hash': file_hash,
                        'last_modified': last_modified
                    }
                    
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f: # Added errors='ignore'
                        content = f.read()

                    # Prepare metadata and serialize
                    metadata_dict = {'original_path': file_path}
                    metadata_json_str = json.dumps(metadata_dict)

                    document = IndexedDocument(
                        document_id=file_path, # Use file_path as unique ID
                        file_path=file_path,
                        content_hash=file_hash,
                        last_modified_timestamp=last_modified,
                        extracted_text_chunk=content, # Use correct field name
                        metadata_json=metadata_json_str # Use serialized metadata
                        # Vector will be added by indexer if needed
                    )
                    self.indexer.add_or_update_document(document)
                    logging.info(f"Indexed file: {file_path}")
                except Exception as e:
                    logging.error(f"Error processing file {file_path} during initial scan: {e}")
    
    def process_creation(self, file_path: str):
        if self._should_ignore(file_path):
            return
        
        try:
            file_hash = self._calculate_hash(file_path)
            last_modified = self._get_last_modified(file_path)
            self.known_files[file_path] = {
                'hash': file_hash,
                'last_modified': last_modified
            }
            
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f: # Added errors='ignore'
                content = f.read()

            # Prepare metadata and serialize
            metadata_dict = {'original_path': file_path}
            metadata_json_str = json.dumps(metadata_dict)

            document = IndexedDocument(
                document_id=file_path,
                file_path=file_path,
                content_hash=file_hash,
                last_modified_timestamp=last_modified,
                extracted_text_chunk=content, # Use correct field name
                metadata_json=metadata_json_str # Use serialized metadata
            )
            self.indexer.add_or_update_document(document)
            logging.info(f"Indexed new file: {file_path}")
        except Exception as e:
            logging.error(f"Error processing created file {file_path}: {e}")
    
    def process_modification(self, file_path: str):
        if self._should_ignore(file_path):
            return
        
        current_hash = self._calculate_hash(file_path)
        current_modified = self._get_last_modified(file_path)
        
        if file_path not in self.known_files or \
           current_hash != self.known_files[file_path]['hash'] or \
           current_modified != self.known_files[file_path]['last_modified']:
            
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f: # Added errors='ignore'
                    content = f.read()

                # Prepare metadata and serialize
                metadata_dict = {'original_path': file_path}
                metadata_json_str = json.dumps(metadata_dict)

                document = IndexedDocument(
                    document_id=file_path,
                    file_path=file_path,
                    content_hash=current_hash, # Use current_hash
                    last_modified_timestamp=current_modified, # Use current_modified
                    extracted_text_chunk=content, # Use correct field name
                    metadata_json=metadata_json_str # Use serialized metadata
                )
                self.indexer.add_or_update_document(document)
                self.known_files[file_path] = {
                    'hash': current_hash,
                    'last_modified': current_modified
                }
                logging.info(f"Updated indexed file: {file_path}")
            except Exception as e:
                logging.error(f"Error processing modified file {file_path}: {e}")
    
    def process_deletion(self, file_path: str):
        if file_path in self.known_files:
            try:
                self.indexer.remove_document(file_path)
                del self.known_files[file_path]
                logging.info(f"Removed indexed file: {file_path}")
            except Exception as e:
                logging.error(f"Error processing deleted file {file_path}: {e}")
    
    def start(self):
        self.observer.schedule(
            self.event_handler,
            self.project_path,
            recursive=True
        )
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
            self.file_watcher.process_deletion(event.src_path)
            self.file_watcher.process_creation(event.dest_path)