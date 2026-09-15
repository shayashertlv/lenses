"""Serve the published AR try-on (the `ar/` pipeline's built site); never expose a development tree."""

import collections
import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path


_PREFIX = "/ar"
_ENTRY = "index.html"
# Startup diagnostics: the page posts its step log (names, timings, error text, device strings; never an image) while
# a session starts; the last reports are readable as JSON so a stall on a device can be read without the device.
_DIAGNOSTIC_PATH = _PREFIX + "/diagnostic"
_DIAGNOSTICS_PATH = _PREFIX + "/diagnostics.json"
_MAX_DIAGNOSTIC_BYTES = 16 * 1024
_DIAGNOSTIC_KEEP = 40
_CHUNK_BYTES = 128 * 1024
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
# Enforce on worker responses as well as the document: module workers have their
# own policy. Blob connections load embedded GLB textures locally; no external
# host or reporting endpoint is allowed. WASM execution does not enable JS eval.
_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; "
    "worker-src 'self' blob:; connect-src 'self' blob:; "
    "img-src 'self' blob: data:; media-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; font-src 'self'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
_PATH = re.compile(r"[A-Za-z0-9_@./-]+\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_HASHED_ASSET = re.compile(r"assets/(?:[^/]+/)*[^/]+-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]+\Z")
_PRIVATE_PARTS = {
    "private", "recording", "recordings", "recovery", "archive", "archives",
    "qa", "logs", "test-results", "tests", "node_modules", "src",
}
_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".wasm": "application/wasm",
    ".task": "application/octet-stream",
    ".tflite": "application/octet-stream",
    ".bin": "application/octet-stream",
    ".data": "application/octet-stream",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}


def _public_path(path):
    """Accept only canonical, explicitly public build paths, without decoding."""
    if not isinstance(path, str) or not _PATH.fullmatch(path):
        return False
    parts = path.split("/")
    return (
        all(part and not part.startswith(".") and part.lower() not in _PRIVATE_PARTS
            for part in parts)
        and path != "public-manifest.json"
        and Path(path).suffix.lower() in _MIME
    )


def _stamp(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


@dataclass(frozen=True)
class _File:
    size: int
    sha256: str


class PublicARSite:
    """Manifest allowlist and integrity checks scoped to one built directory.

    `npm run publish` in `ar/` writes the site and a manifest with schemaVersion 1
    and files [{path, size, sha256}]. Hashes are verified using bounded reads on
    first access, then cached until either the manifest or the file changes.
    Models are streamed without loading them into a response-sized buffer. The
    manifest itself is not a public resource. `/ar/` serves the entry document;
    `/ar` redirects to it.
    """

    def __init__(self, directory):
        self.directory = Path(directory)
        self._lock = threading.RLock()
        self._manifest_stamp = None
        self._files = {}
        self._verified = {}
        self._diagnostics = collections.deque(maxlen=_DIAGNOSTIC_KEEP)

    def serve_post(self, handler, request_target):
        """Accept one startup diagnostic report; return False for every other POST."""
        try:
            path = urllib.parse.urlsplit(request_target).path
        except ValueError:
            return False
        if path != _DIAGNOSTIC_PATH:
            return False
        try:
            length = int(handler.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = -1
        if length <= 0 or length > _MAX_DIAGNOSTIC_BYTES:
            self._message(handler, 413, "Diagnostic report too large.\n", False)
            return True
        try:
            report = json.loads(handler.rfile.read(length))
            if not isinstance(report, dict):
                raise ValueError("not an object")
        except (ValueError, UnicodeDecodeError):
            self._message(handler, 400, "Invalid diagnostic report.\n", False)
            return True
        with self._lock:
            self._diagnostics.append({"receivedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "report": report})
        handler.send_response(204)
        self._headers(handler)
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return True

    def _serve_diagnostics(self, handler, head):
        with self._lock:
            data = json.dumps({"reports": list(self._diagnostics)}, indent=2).encode("utf-8")
        handler.send_response(200)
        self._headers(handler)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        if not head:
            handler.wfile.write(data)

    def _manifest(self):
        manifest_path = self.directory / "public-manifest.json"
        with self._lock:
            stamp = _stamp(manifest_path.stat())
            if stamp == self._manifest_stamp:
                return self._files
            with manifest_path.open("rb") as source:
                raw = source.read(_MAX_MANIFEST_BYTES + 1)
            if len(raw) > _MAX_MANIFEST_BYTES:
                raise ValueError("Manifest exceeds the public build limit")
            manifest = json.loads(raw)
            if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
                raise ValueError("Unsupported public manifest")
            entries = manifest.get("files")
            if not isinstance(entries, list) or not entries:
                raise ValueError("Empty public manifest")
            files = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("Invalid manifest entry")
                path, size, digest = entry.get("path"), entry.get("size"), entry.get("sha256")
                if (not _public_path(path) or path in files
                        or type(size) is not int or size < 0
                        or not isinstance(digest, str) or not _HASH.fullmatch(digest)):
                    raise ValueError("Invalid public file")
                files[path] = _File(size, digest)
            if _ENTRY not in files:
                raise ValueError("The public entry is missing")
            self._files = files
            self._manifest_stamp = stamp
            self._verified = {}
            return files

    @staticmethod
    def _headers(handler):
        handler.send_header("Content-Security-Policy", _CONTENT_SECURITY_POLICY)
        handler.send_header("Cross-Origin-Opener-Policy", "same-origin")
        handler.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        handler.send_header("Cross-Origin-Resource-Policy", "same-origin")
        handler.send_header("X-Content-Type-Options", "nosniff")

    def _message(self, handler, status, message, head):
        data = message.encode("utf-8")
        handler.send_response(status)
        self._headers(handler)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        if not head:
            handler.wfile.write(data)

    def _verify(self, source, path, entry):
        stamp = _stamp(os.fstat(source.fileno()))
        if stamp[2] != entry.size:
            raise ValueError("Public file size differs from the build")
        with self._lock:
            if self._verified.get(path) == (stamp, entry.sha256):
                return
            digest = hashlib.sha256()
            while block := source.read(_CHUNK_BYTES):
                digest.update(block)
            if digest.hexdigest() != entry.sha256 or _stamp(os.fstat(source.fileno())) != stamp:
                raise ValueError("Public file differs from the build")
            self._verified[path] = (stamp, entry.sha256)
            source.seek(0)

    def serve(self, handler, request_target, *, head=False):
        """Return False for routes outside AR, leaving the parent app untouched."""
        try:
            parts = urllib.parse.urlsplit(request_target)
        except ValueError:
            return False
        path = parts.path
        if path != _PREFIX and not path.startswith(_PREFIX + "/"):
            return False
        if path == _PREFIX:
            # The page addresses its assets with the /ar/ base; keep the document at /ar/.
            handler.send_response(302)
            self._headers(handler)
            handler.send_header("Location", _PREFIX + "/" + ("?" + parts.query if parts.query else ""))
            handler.send_header("Cache-Control", "no-cache")
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return True
        if path == _DIAGNOSTICS_PATH:
            self._serve_diagnostics(handler, head)
            return True
        if path == _PREFIX + "/":
            relative = _ENTRY
        else:
            # Decode once; residual escapes, backslashes, dot segments and empty
            # components are rejected rather than normalized into another file.
            try:
                relative = urllib.parse.unquote(path[len(_PREFIX) + 1:], errors="strict")
            except UnicodeError:
                relative = ""
        if not _public_path(relative):
            self._message(handler, 404, "Not found.\n", head)
            return True
        try:
            entry = self._manifest().get(relative)
        except (OSError, ValueError, TypeError):
            self._message(handler, 503, "The AR try-on is not available in this build.\n", head)
            return True
        if entry is None:
            self._message(handler, 404, "Not found.\n", head)
            return True
        try:
            root = self.directory.resolve(strict=True)
            target = (root / relative).resolve(strict=True)
            if (not target.is_relative_to(root) or target != root / relative
                    or not target.is_file()):
                raise FileNotFoundError()
            source = target.open("rb")
        except OSError:
            self._message(handler, 404, "Not found.\n", head)
            return True
        with source:
            try:
                self._verify(source, relative, entry)
            except (OSError, ValueError):
                self._message(handler, 503, "AR file verification failed.\n", head)
                return True
            etag = '"sha256-' + entry.sha256 + '"'
            if_none_match = handler.headers.get("If-None-Match", "")
            unchanged = any(value.strip().removeprefix("W/") in (etag, "*")
                            for value in if_none_match.split(","))
            handler.send_response(304 if unchanged else 200)
            self._headers(handler)
            handler.send_header("Content-Type", _MIME[target.suffix.lower()])
            handler.send_header("ETag", etag)
            # Hashed assets are immutable; models revalidate; the document itself is never served from a cache, so a
            # phone that reopens the page always runs the deployed build.
            handler.send_header("Cache-Control", "public, max-age=31536000, immutable" if _HASHED_ASSET.fullmatch(relative)
                                else "no-store" if target.suffix.lower() == ".html" else "no-cache")
            if not unchanged:
                handler.send_header("Content-Length", str(entry.size))
            handler.end_headers()
            if not head and not unchanged:
                remaining = entry.size
                while remaining:
                    block = source.read(min(_CHUNK_BYTES, remaining))
                    if not block:
                        break
                    handler.wfile.write(block)
                    remaining -= len(block)
        return True


AR_SITE = PublicARSite(Path(__file__).resolve().parent.parent / "ar" / "site")
