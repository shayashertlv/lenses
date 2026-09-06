"""File-backed analysis cache for deterministic Smart Fit results.

Gemini does not guarantee deterministic output even with temperature=0 and seed.
This cache ensures the same portrait always returns the same analysis by storing
results keyed by SHA-256 hash of the raw image bytes.
"""

import hashlib
import json
import os
import threading
from collections import OrderedDict
from pathlib import Path

CACHE_DIR = Path(__file__).parent / ".analysis_cache"
_MAX_MEMORY = 50
_MAX_DISK = 200

_memory_cache: OrderedDict[str, dict] = OrderedDict()
_lock = threading.Lock()


def compute_image_hash(image_bytes: bytes) -> str:
    """Return SHA-256 hex digest of raw image bytes."""
    return hashlib.sha256(image_bytes).hexdigest()


def derive_seed(image_hash: str) -> int:
    """Derive a Gemini-safe seed (0 to 2^31-1) from the image hash."""
    return int(image_hash[:8], 16) & 0x7FFFFFFF


def get_cached_analysis(image_hash: str) -> dict | None:
    """Look up a cached analysis by image hash. Returns None on miss."""
    with _lock:
        # Fast path: in-memory
        if image_hash in _memory_cache:
            _memory_cache.move_to_end(image_hash)
            return _memory_cache[image_hash]

    # Slow path: disk
    path = CACHE_DIR / f"{image_hash}.json"
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Corrupt file — remove and treat as miss
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None

    # Promote to memory
    with _lock:
        _memory_cache[image_hash] = data
        _memory_cache.move_to_end(image_hash)
        _evict_memory()

    return data


def put_analysis(image_hash: str, analysis: dict) -> None:
    """Store analysis to both memory and disk."""
    # Memory
    with _lock:
        _memory_cache[image_hash] = analysis
        _memory_cache.move_to_end(image_hash)
        _evict_memory()

    # Disk — atomic write
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / f"{image_hash}.json"
    tmp = target.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(target))
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return

    _evict_disk()


def clear_cache() -> None:
    """Remove all cached entries (memory + disk)."""
    with _lock:
        _memory_cache.clear()

    if CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.json"):
            try:
                f.unlink()
            except OSError:
                pass


def _evict_memory():
    """Evict oldest memory entries beyond _MAX_MEMORY. Caller holds _lock."""
    while len(_memory_cache) > _MAX_MEMORY:
        _memory_cache.popitem(last=False)


def _evict_disk():
    """Evict oldest disk entries beyond _MAX_DISK (by mtime)."""
    try:
        files = sorted(CACHE_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
    except OSError:
        return
    while len(files) > _MAX_DISK:
        oldest = files.pop(0)
        try:
            oldest.unlink()
        except OSError:
            pass
