"""Offline, explicit photo intake for g3d.job/1. Originals are never modified."""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import uuid

from PIL import Image, ImageOps

from .budget import create_json_exclusive, now_iso
from .session_inputs import ReferenceInput
from ..g3d_host.fit_views import view_candidates

SCHEMA = "g3d.job/1"
MAX_PIXELS = 40_000_000
MAX_REFERENCE_BYTES = 20 * 1024 * 1024
DEFAULT_JOBS_ROOT = Path(__file__).resolve().parents[1] / "data" / "jobs"
_FORMATS = {".png": ("PNG", "image/png"), ".jpg": ("JPEG", "image/jpeg"),
            ".jpeg": ("JPEG", "image/jpeg"), ".webp": ("WEBP", "image/webp"),
            ".gif": ("GIF", "image/gif")}
_SIDES = (None, "left", "right", "both")


class JobError(ValueError):
    """Invalid or changed job inputs; no inference or network action is allowed."""


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json(text: str, label: str):
    def pairs(rows):
        out = {}
        for key, value in rows:
            if key in out:
                raise JobError(f"{label}: duplicate JSON key {key!r}")
            out[key] = value
        return out

    def constant(value):
        raise JobError(f"{label}: non-finite JSON number {value}")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise JobError(f"{label}: invalid JSON: {exc}") from exc


def _view(value, name: str) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("kind"), str) or not value["kind"].strip():
        raise JobError(f"{name}: an explicit view kind is required")
    if "side" not in value or value["side"] not in _SIDES:
        raise JobError(f"{name}: side must be explicitly null, left, right, or both")
    kind, side = value["kind"], value["side"]
    try:
        candidates = list(view_candidates(kind))
    except ValueError as exc:
        raise JobError(f"{name}: {exc}") from exc
    if len(candidates) == 1:
        only = candidates[0]
        exact_side = "left" if only == "left" or only.endswith("_left") else (
            "right" if only == "right" or only.endswith("_right") else None)
        if exact_side and side not in (None, exact_side):
            raise JobError(f"{name}: side {side!r} contradicts view kind {kind!r}")
    if side in ("left", "right"):
        matching = [candidate for candidate in candidates if candidate == side or candidate.endswith(f"_{side}")]
        if matching:
            candidates = matching
    return {"view_kind": kind, "side": side, "fit_kinds": candidates}


def _image(payload: bytes, name: str) -> tuple[dict, bytes | None]:
    expected, mime = _FORMATS[Path(name).suffix.lower()]
    try:
        with Image.open(io.BytesIO(payload)) as original:
            if original.format != expected:
                raise JobError(f"{name}: filename MIME {mime} disagrees with decoded {original.format}")
            w, h = original.size
            if w < 1 or h < 1 or w * h > MAX_PIXELS:
                raise JobError(f"{name}: image must contain at most {MAX_PIXELS:,} pixels")
            if getattr(original, "n_frames", 1) != 1:
                raise JobError(f"{name}: reference photos must be single-frame images")
            orientation = original.getexif().get(274, 1)
            if orientation not in range(1, 9):
                raise JobError(f"{name}: invalid EXIF orientation {orientation!r}")
            original.load()
            oriented = ImageOps.exif_transpose(original).convert("RGBA")
            decoded = _sha(f"RGBA:{oriented.width}x{oriented.height}:".encode() + oriented.tobytes())
            row = {"sha256": _sha(payload), "bytes": len(payload), "mime": mime,
                   "decoded_sha256": decoded, "size_px": list(oriented.size), "w": w, "h": h,
                   "exif_orientation": orientation, "sent_sha256": _sha(payload), "sent_mime": mime}
            if len(payload) <= MAX_REFERENCE_BYTES:
                return row, None
            # Same-size transport copies only; the immutable original stays available for inspection.
            output = io.BytesIO()
            oriented.save(output, format="PNG")
            sent, sent_mime, suffix = output.getvalue(), "image/png", ".png"
            if len(sent) > MAX_REFERENCE_BYTES:
                output = io.BytesIO()
                rgb = Image.new("RGB", oriented.size, "white")
                rgb.paste(oriented, mask=oriented.getchannel("A"))
                rgb.save(output, format="JPEG", quality=95)
                sent, sent_mime, suffix = output.getvalue(), "image/jpeg", ".jpg"
            if len(sent) > MAX_REFERENCE_BYTES:
                raise JobError(f"{name}: same-size PNG and JPEG q95 exceed the 20 MiB reference limit")
            row.update(sent_sha256=_sha(sent), sent_mime=sent_mime, transport_suffix=suffix)
            return row, sent
    except JobError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise JobError(f"{name}: image could not be decoded: {exc}") from exc


def create_job(*, images: Path, views: Path, name: str, description: str,
               specs: Path | None = None, jobs_root: Path | None = None) -> Path:
    """Validate every photo/view before creating a job; no budget or approval is created."""
    if not isinstance(name, str) or not name.strip():
        raise JobError("job name must be nonempty text")
    if not isinstance(description, str) or not description.strip():
        raise JobError("description must be nonempty text")
    source = Path(images).resolve(strict=True)
    if not source.is_dir():
        raise JobError("images must name a directory")
    view_map = _json(Path(views).read_bytes().decode("utf-8"), "views")
    if not isinstance(view_map, dict):
        raise JobError("views must be a JSON object mapping filenames to kind and side")
    entries = list(source.iterdir())
    image_extensions = set(Image.registered_extensions()) | {".heic", ".heif", ".avif"}
    unsupported = [p.name for p in entries if p.suffix.lower() in image_extensions
                   and p.suffix.lower() not in _FORMATS]
    if unsupported:
        raise JobError(f"unsupported photo format: {sorted(unsupported)}; use PNG, JPEG, WebP, or GIF")
    photos = sorted((p for p in entries if p.suffix.lower() in _FORMATS), key=lambda p: p.name)
    if not photos:
        raise JobError("images directory contains no supported photos")
    names = [p.name for p in photos]
    if len(set(n.casefold() for n in names)) != len(names):
        raise JobError("photo filenames must be unique ignoring case")
    if len({Path(n).stem.casefold() for n in names}) != len(names):
        raise JobError("photo stems must be unique ignoring case for camera and overlay identities")
    if set(view_map) != set(names):
        raise JobError(f"image/view bijection required; missing views: {sorted(set(names) - set(view_map))}; "
                       f"unknown images: {sorted(set(view_map) - set(names))}")
    specs_text = Path(specs).read_bytes().decode("utf-8") if specs is not None else "{}"
    specs_data = _json(specs_text, "specs")
    if not isinstance(specs_data, dict):
        raise JobError("specs must be a JSON object")
    prepared, seen = [], {}
    for index, photo in enumerate(photos):
        if photo.is_symlink() or photo.resolve() != photo.absolute() or not photo.is_file():
            raise JobError(f"{photo.name}: photo must be a regular file within the images directory")
        view = _view(view_map[photo.name], photo.name)
        payload = photo.read_bytes()
        details, sent = _image(payload, photo.name)
        if details["decoded_sha256"] in seen:
            raise JobError(f"duplicate decoded photos: {seen[details['decoded_sha256']]} and {photo.name}")
        seen[details["decoded_sha256"]] = photo.name
        file = f"images/{photo.name}"
        sent_file = (f"reference_images/reference-{index + 1:03d}{details.pop('transport_suffix')}"
                     if sent is not None else file)
        row = {"index": index, "file": file, "original_name": photo.name, "sent_file": sent_file,
               **view, **details}
        prepared.append((row, payload, sent))
    base = Path(jobs_root or DEFAULT_JOBS_ROOT).resolve()
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "job"
    job_id = f"{slug}-{uuid.uuid4().hex[:8]}"
    root = base / job_id
    root.mkdir(parents=True, exist_ok=False)
    (root / "images").mkdir()
    for row, payload, sent in prepared:
        (root / row["file"]).write_bytes(payload)
        if sent is not None:
            (root / "reference_images").mkdir(exist_ok=True)
            (root / row["sent_file"]).write_bytes(sent)
    job = {"schema": SCHEMA, "input_layout": "job-images/1", "id": job_id, "name": name, "created": now_iso(),
           "description": description, "description_sha256": _sha(description.encode("utf-8")),
           "specs": specs_data, "specs_json": specs_text, "specs_sha256": _sha(specs_text.encode("utf-8")),
           "images": [row for row, _, _ in prepared]}
    create_json_exclusive(root / "job.json", job)
    return root


def _asset(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative or len(path.parts) != 2:
        raise JobError("job image path must stay within its photo directory")
    if path.parts[0] not in ("images", "reference_images"):
        raise JobError("job image path must name an original or transport photo")
    candidate = root / relative
    if candidate.is_symlink() or candidate.resolve() != candidate.absolute() or not candidate.is_file():
        raise JobError(f"missing or linked job photo: {relative}")
    return candidate


def load_job(root) -> dict:
    """Read a job and verify copied photo bytes before any consumer uses them."""
    root = Path(root).resolve()
    job = _json((root / "job.json").read_bytes().decode("utf-8"), "job")
    if not isinstance(job, dict) or job.get("schema") != SCHEMA or job.get("input_layout") != "job-images/1":
        raise JobError(f"expected {SCHEMA}")
    if not isinstance(job.get("description"), str) or _sha(job["description"].encode()) != job.get("description_sha256"):
        raise JobError("job description digest differs")
    text = job.get("specs_json")
    if not isinstance(text, str) or _sha(text.encode()) != job.get("specs_sha256") or _json(text, "specs") != job.get("specs"):
        raise JobError("job specs digest differs")
    rows = job.get("images")
    if not isinstance(rows, list) or not rows:
        raise JobError("job must contain photos")
    names, files = set(), set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("original_name"), str)
                or not isinstance(row.get("file"), str) or "side" not in row):
            raise JobError("invalid job photo record")
        name = row["original_name"]
        if name in names or row.get("file") in files:
            raise JobError("duplicate job photo record")
        names.add(name)
        files.add(row.get("file"))
        checked = _view({"kind": row.get("view_kind"), "side": row.get("side")}, name)
        if checked["fit_kinds"] != row.get("fit_kinds"):
            raise JobError(f"{name}: view candidates differ from the declared kind")
        for file_key, sha_key in (("file", "sha256"), ("sent_file", "sent_sha256")):
            relative = row.get(file_key)
            if not isinstance(relative, str) or _sha(_asset(root, relative).read_bytes()) != row.get(sha_key):
                raise JobError(f"{name}: {file_key} photo digest differs")
    return job


def image_paths(job_root) -> dict[str, Path]:
    root = Path(job_root).resolve()
    return {row["original_name"]: root / row["file"] for row in load_job(root)["images"]}


def view_specs(job_root) -> dict[str, dict]:
    return {row["original_name"]: {"kind": row["view_kind"], "side": row["side"]}
            for row in load_job(job_root)["images"]}


def references(job_root) -> list[ReferenceInput]:
    root = Path(job_root).resolve()
    rows = load_job(root)["images"]
    out = []
    for index, row in enumerate(rows):
        payload = (root / row["sent_file"]).read_bytes()
        image_url = f"data:{row['sent_mime']};base64,{base64.b64encode(payload).decode('ascii')}"
        side = f", side {row['side']}" if row["side"] is not None else ""
        out.append(ReferenceInput(label=f"Reference {index + 1} of {len(rows)}", path=row["sent_file"],
                                  image_url=image_url, detail="high", view=f"{row['view_kind']}{side}",
                                  size_px=tuple(row["size_px"])))
    return out
