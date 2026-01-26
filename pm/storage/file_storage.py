"""
File storage layer - direct markdown file operations (source of truth).
"""
import os
import logging
from django.conf import settings
from pm import utils

logger = logging.getLogger('pm')


class FileStorage:
    """Handles direct markdown file operations."""
    
    @staticmethod
    def load_entity(file_path, default_title, default_status, metadata_only=False):
        """Load entity from markdown file."""
        return utils.load_entity(file_path, default_title, default_status, metadata_only)
    
    @staticmethod
    def save_entity(file_path, metadata, content):
        """Save entity to markdown file."""
        utils.save_entity(file_path, metadata, content)
        return os.path.getmtime(file_path)
    
    @staticmethod
    def get_file_mtime(file_path):
        """Get file modification time."""
        if os.path.exists(file_path):
            return os.path.getmtime(file_path)
        return None
    
    @staticmethod
    def file_exists(file_path):
        """Check if file exists."""
        return os.path.exists(file_path)
