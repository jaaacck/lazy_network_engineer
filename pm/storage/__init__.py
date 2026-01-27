"""
Storage layer for SQLite-based architecture.

This module provides:
- index_storage: SQLite operations (primary storage and search index)
"""

from .index_storage import IndexStorage

__all__ = ['IndexStorage']
