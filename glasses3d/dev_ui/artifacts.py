"""Explicitly registered, content-addressed model and image bytes.

Browser input is a SHA-256, never a path. Verified bytes are cached against the
registered path's identity and stat, so a changed file cannot serve under an old
hash. Registration and the first read after restart verify the actual bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import threading

from .registry import Registry


_SHA_RE = re.compile(r"[a-f0-9]{64}\Z")


class ArtifactError(ValueError):
    """Unregistered, unsafe, unsupported, or modified artifact."""


def validate_sha(sha256: str) -> str:
    if not isinstance(sha256, str) or _SHA_RE.fullmatch(sha256) is None:
        raise ArtifactError("Invalid artifact SHA-256")
    return sha256


def _mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"glTF"):
        return "model/gltf-binary"
    raise ArtifactError("Artifact must be a PNG, JPEG, WebP, or GLB file")


def _signature(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _path(value: str | Path) -> Path:
    path = Path(value).absolute()
    # Reject links in every component, including Windows directory junctions.
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ArtifactError("Artifact paths cannot contain links")
    return path.resolve(strict=True)


@dataclass(frozen=True)
class _Cached:
    path: str
    stat: tuple[int, ...]
    data: bytes
    mime: str


class Artifacts:
    def __init__(self, registry: Registry):
        self.registry = registry
        self._cache: dict[str, _Cached] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _load(path: Path, expected_sha: str | None = None) -> tuple[str, _Cached]:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ArtifactError("Artifact is not a regular file")
            data = stream.read()
            after = os.fstat(stream.fileno())
        published = path.stat()
        # On Windows Python's fstat ctime is the change time while Path.stat
        # may expose the creation time. Compare common identity/size/mtime
        # between APIs; retain each API's ctime for comparisons with itself.
        if (_signature(before) != _signature(after)
                or _signature(after)[:4] != _signature(published)[:4]):
            raise ArtifactError("Artifact changed while it was being verified")
        sha = hashlib.sha256(data).hexdigest()
        if expected_sha is not None and sha != expected_sha:
            raise ArtifactError("Artifact no longer matches its recorded SHA-256")
        return sha, _Cached(str(path), _signature(published), data, _mime(data))

    def register(self, path: Path | str, expected_sha: str | None = None) -> str:
        if expected_sha is not None:
            validate_sha(expected_sha)
        with self.registry.lock, self._lock:
            resolved = _path(path)
            signature = _signature(resolved.stat())
            cached = self._cache.get(expected_sha) if expected_sha is not None else None
            if cached is not None and cached.path == str(resolved) and cached.stat == signature:
                sha = expected_sha
            else:
                sha, cached = self._load(resolved, expected_sha)
            with self.registry.lock:
                try:
                    record = self.registry.get("artifacts", sha)
                except KeyError:
                    record = None
                # Identical bytes share one URL, even if generated at two paths.
                # Keep the first surviving registration for stable restart reads.
                if record is None or not Path(record["path"]).is_file():
                    self.registry.put("artifacts", {
                        "path": str(resolved), "sha256": sha,
                        "mime": cached.mime, "size_bytes": len(cached.data),
                    }, key=sha)
                    self._cache[sha] = cached
                elif record["path"] == str(resolved):
                    self._cache[sha] = cached
            return f"/api/artifacts/{sha}"

    def read(self, sha256: str) -> tuple[bytes, str]:
        validate_sha(sha256)
        with self.registry.lock, self._lock:
            record = self.registry.get("artifacts", sha256)
            if record.get("sha256") != sha256:
                raise ArtifactError("Invalid artifact registration")
            try:
                path = _path(record["path"])
                signature = _signature(path.stat())
                cached = self._cache.get(sha256)
                if cached is None or cached.path != str(path) or cached.stat != signature:
                    _, cached = self._load(path, sha256)
                    self._cache[sha256] = cached
                if cached.mime != record.get("mime") or len(cached.data) != record.get("size_bytes"):
                    raise ArtifactError("Invalid artifact registration")
            except (OSError, KeyError, TypeError) as exc:
                raise ArtifactError("Registered artifact is unavailable") from exc
            return cached.data, cached.mime
