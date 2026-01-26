"""
Module for caching VM metadata to reduce libvirt calls.
"""
import time
import threading
from typing import Any, Dict, Optional
from .config import load_config

_cache: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()
config = load_config()
from .constants import AppCacheTimeout

def get_from_cache(uuid: str) -> Optional[Dict[str, Any]]:
    """
    Retrieves VM info from cache if available and not expired.
    Removes expired entries found during lookup.
    """
    with _lock:
        if uuid in _cache:
            entry = _cache[uuid]
            if time.time() - entry['timestamp'] < AppCacheTimeout.INFO_CACHE_TTL:
                return entry['data']
            else:
                # Clean up expired entry
                del _cache[uuid]
    return None

def set_in_cache(uuid: str, data: Dict[str, Any]):
    """
    Stores VM info in the cache with a timestamp.
    """
    with _lock:
        _cache[uuid] = {
            'data': data,
            'timestamp': time.time()
        }

def invalidate_cache(uuid: str):
    """
    Invalidates the cache for a specific VM.
    """
    with _lock:
        if uuid in _cache:
            del _cache[uuid]
