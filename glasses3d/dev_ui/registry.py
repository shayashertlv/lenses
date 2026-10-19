"""Small durable handle registry shared by the UI and detached task workers.

Only server-issued keys address records; neither a key nor a collection name is
a filesystem path. Callers can hold ``registry.lock`` around read/modify/write
sequences, including idempotent lookup-and-create operations.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any

from glasses3d.agent.procs import write_json_atomic


KEY_RE = re.compile(r"[A-Za-z0-9_-]{6,64}\Z")
_KIND_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_LOCKS: dict[str, "_RegistryLock"] = {}
_LOCKS_GUARD = threading.Lock()


class RegistryError(ValueError):
    """Invalid handle, record, or persisted registry data."""


def validate_key(key: str) -> str:
    if not isinstance(key, str) or KEY_RE.fullmatch(key) is None:
        raise RegistryError("Invalid server key")
    return key


class _RegistryLock:
    """Reentrant within one thread; exclusive across threads and processes."""

    def __init__(self, path: Path):
        self.path = path
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._stream = None

    def __enter__(self):
        self._thread_lock.acquire()
        try:
            if self._depth == 0:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.is_symlink():
                    raise RegistryError("Registry lock cannot be a link")
                stream = self.path.open("a+b")
                try:
                    if stream.seek(0, os.SEEK_END) == 0:
                        stream.write(b"\0")
                        stream.flush()
                    while True:
                        try:
                            stream.seek(0)
                            if os.name == "nt":
                                import msvcrt
                                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                            else:  # pragma: no cover - Windows deployment
                                import fcntl
                                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except OSError as exc:
                            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                                raise
                            time.sleep(0.01)
                except BaseException:
                    stream.close()
                    raise
                self._stream = stream
            self._depth += 1
            return self
        except BaseException:
            self._thread_lock.release()
            raise

    def __exit__(self, *exc):
        try:
            self._depth -= 1
            if self._depth == 0:
                stream, self._stream = self._stream, None
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:  # pragma: no cover
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                finally:
                    stream.close()
        finally:
            self._thread_lock.release()


class Registry:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.records_root = self.root / "registry"
        self.records_root.mkdir(exist_ok=True)
        self._confined(self.records_root)
        with _LOCKS_GUARD:
            self.lock = _LOCKS.setdefault(
                os.path.normcase(str(self.root)), _RegistryLock(self.root / ".registry.lock")
            )

    def _confined(self, path: Path) -> Path:
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise RegistryError("Registry path is outside its storage directory")
        return path

    def _directory(self, kind: str) -> Path:
        if not isinstance(kind, str) or _KIND_RE.fullmatch(kind) is None:
            raise RegistryError("Invalid registry collection")
        return self._confined(self.records_root / kind)

    def _path(self, kind: str, key: str) -> Path:
        return self._confined(self._directory(kind) / f"{validate_key(key)}.json")

    def get(self, kind: str, key: str) -> dict[str, Any]:
        with self.lock:
            path = self._path(kind, key)
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise KeyError(key) from None
            except (OSError, ValueError) as exc:
                raise RegistryError("Cannot read a stored registry record") from exc
            if not isinstance(value, dict) or value.get("key") != key:
                raise RegistryError("Invalid stored registry record")
            return value

    def put(self, kind: str, record: dict[str, Any], key: str | None = None) -> str:
        if not isinstance(record, dict):
            raise RegistryError("Registry records must be JSON objects")
        # Validate before touching the filesystem; forbid silent string coercion
        # and non-JSON NaN/Infinity accepted by json.dumps's default policy.
        try:
            value = json.loads(json.dumps(record, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise RegistryError("Registry records must contain valid JSON values") from exc
        with self.lock:
            directory = self._directory(kind)
            directory.mkdir(exist_ok=True)
            if key is None:
                key = secrets.token_urlsafe(18)
                while self._path(kind, key).exists():
                    key = secrets.token_urlsafe(18)
            path = self._path(kind, key)
            if "key" in value and value["key"] != key:
                raise RegistryError("A record cannot change its server key")
            value["key"] = key
            write_json_atomic(path, value)
            return key

    def list(self, kind: str) -> list[dict[str, Any]]:
        with self.lock:
            directory = self._directory(kind)
            if not directory.exists():
                return []
            return [self.get(kind, path.stem) for path in sorted(directory.glob("*.json"))]

    def find(self, kind: str, **fields: Any) -> dict[str, Any] | None:
        with self.lock:
            return next((row for row in self.list(kind)
                         if all(row.get(name) == value for name, value in fields.items())), None)

    def delete(self, kind: str, key: str) -> None:
        with self.lock:
            path = self._path(kind, key)
            try:
                path.unlink()
            except FileNotFoundError:
                raise KeyError(key) from None
