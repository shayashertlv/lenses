"""In-memory LRU cache for face analysis results.

Keyed by SHA-256 hash of the raw uploaded image bytes.
Guarantees identical smart-fit results for the same portrait photo.
"""

import hashlib
import threading
from collections import OrderedDict

MAX_CACHE_SIZE = 50

_cache: OrderedDict[str, dict] = OrderedDict()
_lock = threading.Lock()


def compute_image_hash(image_bytes: bytes) -> str:
    """Return the hex SHA-256 digest of raw image bytes."""
    return hashlib.sha256(image_bytes).hexdigest()


def get_cached_analysis(image_hash: str) -> dict | None:
    """Return cached analysis dict or None. Bumps entry to MRU on hit."""
    with _lock:
        if image_hash in _cache:
            _cache.move_to_end(image_hash)
            return _cache[image_hash]
    return None


def put_analysis(image_hash: str, analysis: dict) -> None:
    """Store analysis dict, evicting the oldest entry if over capacity."""
    with _lock:
        if image_hash in _cache:
            _cache.move_to_end(image_hash)
        _cache[image_hash] = analysis
        while len(_cache) > MAX_CACHE_SIZE:
            _cache.popitem(last=False)
