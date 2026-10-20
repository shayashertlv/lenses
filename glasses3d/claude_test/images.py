"""Native-pixel image preparation for the isolated Claude experiment.

``Tile.png`` is the historical interface name: an untouched image can also hold
JPEG, WEBP or GIF bytes. Only crops and unsupported input formats become PNG.
Coordinates refer to the decoded source raster, with exclusive right/bottom
edges; this module never resizes or applies EXIF orientation.
"""

from __future__ import annotations

import hashlib
from contextlib import nullcontext
import io
import json
import math
import os
from pathlib import Path
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterator

from PIL import Image


# PROVISIONAL: frozen M13 C1 interface; the coordinator reconciles FACTS.md.
LIMITS = {
    "max_side_many": 2000,
    "max_long_edge": 2576,
    "max_visual_tokens": 4784,
    "max_images": 600,
    "max_request_bytes": 32_000_000,
    "max_image_bytes": 10_000_000,
}
_OVERLAP = 32
_EXTENSIONS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "GIF": "gif"}
_CACHE_LOCKS: dict[Path, threading.RLock] = {}
_CACHE_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class Tile:
    png: bytes
    w: int
    h: int
    x0: int
    y0: int
    label: str
    sha256: str


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def visual_tokens(w: int, h: int) -> int:
    """Return the exact 28-pixel patch count for positive integer dimensions."""
    if not _positive_int(w) or not _positive_int(h):
        raise ValueError("image dimensions must be positive integers")
    return ((w + 27) // 28) * ((h + 27) // 28)


def fits(w: int, h: int, *, many: bool = True) -> bool:
    """Whether native dimensions satisfy all request-dependent size limits."""
    if not _positive_int(w) or not _positive_int(h):
        return False
    edge = LIMITS["max_long_edge"]
    if many:
        edge = min(edge, LIMITS["max_side_many"])
    return max(w, h) <= edge and visual_tokens(w, h) <= LIMITS["max_visual_tokens"]


def _axis_count(length: int, limit: int) -> int:
    if length <= limit:
        return 1
    # n tiles of size <= limit cover at most n*limit - (n-1)*overlap.
    return (length - _OVERLAP + limit - _OVERLAP - 1) // (limit - _OVERLAP)


def _axis_max(length: int, count: int) -> int:
    return (length + _OVERLAP * (count - 1) + count - 1) // count


def _grid(w: int, h: int) -> tuple[int, int]:
    """Minimize tile count, then favor cuts along the source's longer axis."""
    edge = min(LIMITS["max_side_many"], LIMITS["max_long_edge"])
    square = min(edge, 28 * math.isqrt(LIMITS["max_visual_tokens"]))
    rows, cols = _axis_count(h, square), _axis_count(w, square)

    def rank(r: int, c: int) -> tuple[int, int, int]:
        return (r * c, r if w >= h else c, c if w >= h else r)

    best = rank(rows, cols)
    min_cols = _axis_count(w, edge)
    # Every better or tied grid lies within this finite bound. For each row
    # count, the token budget gives the largest allowed width directly.
    for r in range(_axis_count(h, edge), best[0] // min_cols + 1):
        max_h = _axis_max(h, r)
        width_limit = min(edge, 28 * (LIMITS["max_visual_tokens"] // ((max_h + 27) // 28)))
        c = _axis_count(w, width_limit)
        candidate = rank(r, c)
        if candidate < best:
            rows, cols, best = r, c, candidate
    return rows, cols


def _axis_bounds(length: int, count: int) -> list[tuple[int, int]]:
    width, extra = divmod(length + _OVERLAP * (count - 1), count)
    start = 0
    result = []
    for i in range(count):
        end = start + width + (i < extra)
        result.append((start, end))
        start = end - _OVERLAP
    return result


def _png(image: Image.Image) -> bytes:
    # PNG cannot preserve e.g. CMYK or floating-point channels. Refuse rather
    # than silently changing them. Pillow's generic I writer clips to 16 bits.
    if image.mode not in {"1", "L", "LA", "P", "RGB", "RGBA", "I;16", "I;16B"}:
        raise ValueError(f"cannot encode mode {image.mode!r} as PNG without changing pixel values")
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=6)
    return output.getvalue()


def prepare(png_bytes: bytes, label: str) -> list[Tile]:
    """Preserve supported fitting bytes, otherwise encode native PNG crops.

    Preparation always uses the stricter >20-image dimensions, so its results
    remain usable as the conversation grows. Oversized animations are refused:
    silently dropping their later frames would not preserve the source.
    """
    with Image.open(io.BytesIO(png_bytes)) as source:
        w, h = source.size
        whole = fits(w, h)
        if whole and source.format in _EXTENSIONS:
            source.load()
            return [Tile(png_bytes, w, h, 0, 0, label, hashlib.sha256(png_bytes).hexdigest())]
        if getattr(source, "n_frames", 1) != 1:
            raise ValueError("cannot convert or tile a multi-frame image without losing frames")
        source.load()
        if whole:
            data = _png(source)
            return [Tile(data, w, h, 0, 0, label, hashlib.sha256(data).hexdigest())]
        rows, cols = _grid(w, h)
        tiles = []
        for r, (y0, y1) in enumerate(_axis_bounds(h, rows), 1):
            for c, (x0, x1) in enumerate(_axis_bounds(w, cols), 1):
                with source.crop((x0, y0, x1, y1)) as crop:
                    data = _png(crop)
                tile_label = (
                    f"{label} - tile {r}/{c} of {rows}x{cols} - "
                    f"{x0},{y0}-{x1},{y1} px of {w}x{h}"
                )
                tiles.append(Tile(data, x1 - x0, y1 - y0, x0, y0, tile_label,
                                  hashlib.sha256(data).hexdigest()))
        return tiles


def tile(png_or_webp_bytes: bytes, label: str) -> list[Tile]:
    """Split only when necessary; see :func:`prepare` for byte preservation."""
    return prepare(png_or_webp_bytes, label)


class FileCache:
    """Persistent content-addressed uploads, with one writer per path/process.

    All instances re-read the cache under the same thread lock, so a specialist
    and parent in one runner share uploads even if both instances predate them.
    Use a separate cache per run; this is not a cross-process upload lock.
    """

    def __init__(self, path: str | Path, uploader: Callable[[bytes, str], str | dict], *,
                 run_id: str | None = None):
        self.path = Path(path).resolve()
        self.uploader = uploader
        self.run_id = run_id
        self._ledger = None
        self._pending_records = {}
        with _CACHE_LOCKS_GUARD:
            self._lock = _CACHE_LOCKS.setdefault(self.path, threading.RLock())

    def bind_run(self, run_id: str, *, ledger=None):
        """Bind this cache instance before uploads; never adopt another run's records."""
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("image cache requires a run_id")
        with self._lock:
            if self.run_id is not None and self.run_id != run_id:
                raise ValueError("image cache belongs to another run")
            self.run_id = run_id
            if ledger is not None:
                if ledger.run_id != run_id:
                    raise ValueError("image cache ledger belongs to another run")
                self._ledger = ledger

    def _owned(self, record: dict) -> bool:
        return (record.get("run_id") == self.run_id
                and record.get("cache_path") == str(self.path))

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        records = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(records, dict):
            raise ValueError("image cache must be a sha256-keyed JSON object")
        for digest, record in records.items():
            if (
                len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
                or not isinstance(record, dict)
                or not {"file_id", "w", "h", "bytes"} <= record.keys()
                or not isinstance(record["file_id"], str) or not record["file_id"].strip()
                or not all(_positive_int(record[key]) for key in ("w", "h", "bytes"))
            ):
                raise ValueError("invalid image cache record")
        return records

    def uploaded_records(self) -> dict:
        """Return only records uploaded under this run and this cache path."""
        with self._lock:
            records = {**self._read(), **self._pending_records}
            return {digest: dict(record) for digest, record in records.items()
                    if self._owned(record)}

    def owns_file(self, file_id: str) -> bool:
        return any(record["file_id"] == file_id for record in self.uploaded_records().values())

    def record_deletion(self, file_id: str, *, deleted: bool, error: str | None = None):
        """Persist one cleanup result; a failed delete leaves the expiry backup visible."""
        if type(deleted) is not bool or (not deleted and not isinstance(error, str)):
            raise ValueError("a failed deletion requires its error")
        with self._lock:
            records = {**self._read(), **self._pending_records}
            selected = [row for row in records.values()
                        if self._owned(row) and row["file_id"] == file_id]
            if not selected:
                raise ValueError("cannot mark a file that this run did not upload")
            stamp = datetime.now(timezone.utc).isoformat()
            for record in selected:
                record["deletion_attempted_at"] = stamp
                if deleted:
                    record["deleted"] = True
                    record["deleted_at"] = stamp
                    record.pop("deletion_error", None)
                elif not record.get("deleted"):
                    record["deleted"] = False
                    record["deletion_error"] = error
            self._write(records)
            self._pending_records.clear()

    def _write(self, records: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as output:
                temporary = Path(output.name)
                json.dump(records, output, sort_keys=True, separators=(",", ":"))
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def file_id_for(self, tile: Tile) -> str:
        digest = hashlib.sha256(tile.png).hexdigest()
        if tile.sha256 != digest:
            raise ValueError("tile sha256 does not match its bytes")
        if len(tile.png) > LIMITS["max_image_bytes"]:
            raise ValueError(f"image exceeds {LIMITS['max_image_bytes']} bytes")
        with Image.open(io.BytesIO(tile.png)) as image:
            if image.size != (tile.w, tile.h) or not fits(tile.w, tile.h):
                raise ValueError("tile dimensions are inconsistent or exceed native limits")
            if image.format not in _EXTENSIONS:
                raise ValueError("upload requires PNG, JPEG, WEBP or GIF bytes")
            extension = _EXTENSIONS[image.format]
        admission = self._ledger.upload_admission() if self._ledger is not None else nullcontext()
        with admission, self._lock:
            records = {**self._read(), **self._pending_records}
            if digest in records and self._owned(records[digest]) and not records[digest].get("deleted"):
                record = records[digest]
                if (record["w"], record["h"], record["bytes"]) != (tile.w, tile.h, len(tile.png)):
                    raise ValueError("cached dimensions or byte size do not match the tile")
                return record["file_id"]
            uploaded_at = datetime.now(timezone.utc)
            upload = self.uploader(tile.png, f"{digest}.{extension}")
            metadata = upload if isinstance(upload, dict) else {}
            file_id = metadata.get("file_id", metadata.get("id")) if metadata else upload
            if not isinstance(file_id, str) or not file_id.strip():
                raise ValueError("uploader did not return a file_id")
            created = metadata.get("uploaded_at", metadata.get("created_at", uploaded_at.isoformat()))
            if isinstance(created, datetime):
                created = created.isoformat()
            expires = metadata.get("expires_at") or (uploaded_at + timedelta(days=7)).isoformat()
            if isinstance(expires, datetime):
                expires = expires.isoformat()
            records[digest] = {
                "file_id": file_id, "w": tile.w, "h": tile.h, "bytes": len(tile.png),
                "run_id": self.run_id, "cache_path": str(self.path), "uploaded_at": created,
                "expires_in_seconds": 604800, "expires_at": expires, "deleted": False,
            }
            # Preserve the returned ID for process-alive cleanup even if the
            # atomic cache write fails immediately after a successful upload.
            self._pending_records[digest] = records[digest]
            self._write(records)
            self._pending_records.clear()
            return file_id


def image_block(file_id: str) -> dict:
    return {
        "type": "image",
        "source": {"type": "file", "file_id": file_id},
        "transformations": {"oversized_image": "error"},
    }


def label_block(text: str) -> dict:
    return {"type": "text", "text": text}


def _image_blocks(content: object) -> Iterator[dict]:
    if isinstance(content, list):
        for block in content:
            yield from _image_blocks(block)
    elif isinstance(content, dict):
        if content.get("type") == "image":
            yield content
        elif content.get("type") == "tool_result":
            yield from _image_blocks(content.get("content"))


def check_request(body: dict, dims: dict[str, tuple[int, int]]) -> list[str]:
    """Check compact UTF-8 JSON bytes and all message/tool-result images.

    Require the server's exact reject-instead-of-resize transformation. Missing
    dimensions and non-file image sources also refuse: their native size cannot
    be established. The uploader checks the separate file-byte cap. Call this on
    the final body immediately before sending it.
    """
    problems = []
    try:
        size = len(json.dumps(body, ensure_ascii=False, separators=(",", ":"),
                              allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        return [f"request is not serializable JSON: {exc}"]
    if size >= LIMITS["max_request_bytes"]:
        problems.append(f"request is {size} bytes; must be below {LIMITS['max_request_bytes']}")
    if not isinstance(body, dict) or not isinstance(body.get("messages", []), list):
        return problems + ["request messages must be a list"]
    blocks = []
    for message in body.get("messages", []):
        if isinstance(message, dict):
            blocks.extend(_image_blocks(message.get("content")))
    if len(blocks) > LIMITS["max_images"]:
        problems.append(f"request has {len(blocks)} images; maximum is {LIMITS['max_images']}")
    many = len(blocks) > 20
    for i, block in enumerate(blocks, 1):
        if block.get("transformations") != {"oversized_image": "error"}:
            problems.append(
                f'image {i} transformations must be exactly {{"oversized_image": "error"}}'
            )
        source = block.get("source")
        if not isinstance(source, dict):
            problems.append(f"image {i} has no valid source")
            continue
        if source.get("type") == "base64":
            problems.append(f"image {i} uses inline base64; upload it through the Files API")
            continue
        file_id = source.get("file_id")
        if source.get("type") != "file" or not isinstance(file_id, str) or not file_id:
            problems.append(f"image {i} must use a file_id source")
            continue
        dimensions = dims.get(file_id)
        if not isinstance(dimensions, (tuple, list)) or len(dimensions) != 2:
            problems.append(f"image {i} ({file_id}) has no known dimensions")
        elif not fits(*dimensions, many=many):
            problems.append(f"image {i} ({file_id}) dimensions {dimensions} exceed limits "
                            f"or are invalid (many={many})")
    return problems
