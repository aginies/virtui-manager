"""
Module for caching VM metadata to reduce libvirt calls.
"""

import threading
from typing import Any, Dict

from .config import load_config

_cache: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()
config = load_config()


def invalidate_cache(uuid: str):
    """
    Invalidates the cache for a specific VM.
    """
    with _lock:
        if uuid in _cache:
            del _cache[uuid]
