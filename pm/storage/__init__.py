"""
Storage abstraction layer for hybrid markdown + SQLite architecture.

This module provides:
- file_storage: Direct markdown file operations (source of truth)
- index_storage: SQLite index operations (performance layer)
- sync: Synchronization between files and index
"""

from .file_storage import FileStorage
from .index_storage import IndexStorage
from .sync import SyncManager

__all__ = ['FileStorage', 'IndexStorage', 'SyncManager']
