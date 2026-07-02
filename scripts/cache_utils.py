#!/usr/bin/env python3
"""Cache utilities for review action."""

import hashlib
import json
import os
import pickle
import tempfile


class FileCache:
    """Simple file-based cache."""

    def __init__(self, cache_dir: str = None):
        self.cache_dir = cache_dir or os.path.join(tempfile.gettempdir(), "review_cache")
        os.makedirs(self.cache_dir, exist_ok=True)

    def _key_path(self, key: str) -> str:
        safe_key = hashlib.md5(key.encode()).hexdigest()
        return os.path.join(self.cache_dir, safe_key)

    def get(self, key: str):
        path = self._key_path(key)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return pickle.load(f)

    def set(self, key: str, value, ttl: int = 3600):
        path = self._key_path(key)
        with open(path, "wb") as f:
            pickle.dump(value, f)

    def delete(self, key: str):
        path = self._key_path(key)
        if os.path.exists(path):
            os.remove(path)

    def clear(self):
        for f in os.listdir(self.cache_dir):
            os.remove(os.path.join(self.cache_dir, f))


def hash_diff(diff_text: str) -> str:
    """Generate hash for diff content."""
    return hashlib.md5(diff_text.encode()).hexdigest()


def load_json_file(path: str) -> dict:
    """Load and parse JSON file."""
    with open(path) as f:
        return json.load(f)


def save_json_file(path: str, data: dict):
    """Save data to JSON file."""
    with open(path, "w") as f:
        json.dump(data, f)


def format_size(size_bytes: int) -> str:
    """Format bytes to human readable string."""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
