"""Immutable photo intake snapshots, not color/camera/geometry calibration.

All requests, images and transformations are validated in memory before output
creation. Originals are copied exactly; normalized PNGs apply EXIF orientation,
preserve alpha, and record any color conversion. No resizing, white compositing,
view inference, provider calls or initializer-policy validation occurs here.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import math
from numbers import Real
from pathlib import Path
import re

from PIL import Image, ImageCms, ImageOps, PngImagePlugin, UnidentifiedImageError


NORMALIZATION_VERSION = "product_photo_intake_v1"
_VIEWS = {"front", "back", "left", "right", "angled", "unknown"}
_DIMENSIONS = {"frame_width", "lens_width", "lens_height", "bridge_width", "temple_length"}
# Declared product facts about the lens, caller-supplied like the dimensions and never
# inferred from pixels. A photograph cannot tell a mirror coating from a bright studio
# reflected by a plain lens; the catalogue can.
_LENS_FACTS = {"mirror_coating": bool}
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])\Z", re.IGNORECASE)
_OPERATIONS = {1: "identity", 2: "flip_horizontal", 3: "rotate_180", 4: "flip_vertical",
               5: "transpose", 6: "rotate_90_clockwise", 7: "transverse", 8: "rotate_90_counterclockwise"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Request values must be finite JSON-serializable data") from error


def _orientation_matrix(orientation: int, width: int, height: int) -> list[list[int]]:
    return {
        1: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        2: [[-1, 0, width - 1], [0, 1, 0], [0, 0, 1]],
        3: [[-1, 0, width - 1], [0, -1, height - 1], [0, 0, 1]],
        4: [[1, 0, 0], [0, -1, height - 1], [0, 0, 1]],
        5: [[0, 1, 0], [1, 0, 0], [0, 0, 1]],
        6: [[0, -1, height - 1], [1, 0, 0], [0, 0, 1]],
        7: [[0, -1, height - 1], [-1, 0, width - 1], [0, 0, 1]],
        8: [[0, 1, 0], [-1, 0, width - 1], [0, 0, 1]],
    }[orientation]


def _normalize(raw: bytes, photo_id: str, view: str, source_path: Path) -> tuple[dict, bytes]:
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if getattr(source, "n_frames", 1) != 1:
                raise ValueError("Multi-frame images are unsupported; provide distinct still photographs")
            source.load()
            width, height = source.size
            if min(width, height) <= 0:
                raise ValueError("Photographs require nonempty pixel dimensions")
            if source.mode not in ("1", "L", "LA", "P", "PA", "RGB", "RGBA", "CMYK", "LAB"):
                raise ValueError(f"Unsupported photo mode {source.mode}; no implicit HDR/high-depth clipping is performed")
            orientation = source.getexif().get(274, 1)
            if type(orientation) is not int or orientation not in _OPERATIONS:
                raise ValueError("Invalid EXIF orientation")
            icc = source.info.get("icc_profile")
            if icc is not None and (not isinstance(icc, bytes) or not icc):
                raise ValueError("Embedded ICC metadata is not a nonempty binary profile")
            original = {"path": f"originals/{photo_id}.source", "sha256": _sha(raw), "bytes": len(raw),
                        "format": source.format, "width": width, "height": height, "mode": source.mode,
                        "exif_orientation": orientation, "icc_sha256": _sha(icc) if icc is not None else None}
            upright = ImageOps.exif_transpose(source)
            has_alpha = "A" in upright.getbands() or "transparency" in upright.info
            alpha = upright.convert("RGBA").getchannel("A") if has_alpha else None
            rgb_source = upright.convert("RGBA") if upright.mode in ("P", "PA") and has_alpha else upright
            assumptions = ["EXIF orientation was applied without resizing; originals retain their complete metadata.",
                           "No camera, exposure, illumination or physical color calibration is established."]
            if icc is not None:
                try:
                    profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                    space = profile.profile.xcolor_space.strip().upper()
                    if space == "RGB" and upright.mode in ("1", "L", "LA", "P", "PA", "RGB", "RGBA"):
                        color = rgb_source.convert("RGB")
                    elif space == "GRAY" and upright.mode in ("1", "L", "LA"):
                        color = upright.convert("L")
                    elif space == "CMYK" and upright.mode == "CMYK":
                        color = upright
                    elif space == "LAB" and upright.mode == "LAB":
                        color = upright
                    else:
                        raise ValueError(f"ICC color space {space!r} is incompatible with photo mode {upright.mode}")
                    color = ImageCms.profileToProfile(color, profile, ImageCms.createProfile("sRGB"),
                                                     renderingIntent=0, outputMode="RGB")
                except (OSError, TypeError, ImageCms.PyCMSError) as error:
                    raise ValueError("Embedded ICC profile cannot be validated and converted to sRGB") from error
                color_space = "srgb_from_embedded_icc_uncalibrated"
                assumptions.append("Embedded ICC converted by Pillow LittleCMS to sRGB with perceptual rendering intent; profile correctness is not independently verified.")
            else:
                if upright.mode == "LAB":
                    raise ValueError("An unprofiled LAB image cannot be converted without an undefined color-space assumption")
                color = rgb_source.convert("RGB")
                color_space = "unprofiled_cmyk_rgb_conversion_uncalibrated" if upright.mode == "CMYK" else "assumed_srgb_uncalibrated"
                assumptions.append("No embedded ICC profile; " + ("Pillow's generic CMYK-to-RGB conversion is an unverified color approximation."
                                    if upright.mode == "CMYK" else "RGB values are treated as assumed sRGB, not calibrated scene colors."))
            normalized = color.copy()
            if alpha is not None:
                normalized.putalpha(alpha)
            normalized.info.clear()
            transparent = alpha is not None and alpha.getextrema()[0] < 255
            assumptions.append("Alpha is preserved without compositing; hidden RGB is not foreground evidence." if has_alpha
                               else "No alpha channel is present; no transparency was inferred from image color.")
            # A deterministic sRGB PNG chunk avoids creation-time bytes in a newly
            # generated destination ICC profile while explicitly declaring conversion.
            png_metadata = PngImagePlugin.PngInfo()
            if icc is not None:
                png_metadata.add(b"sRGB", b"\x00")
            encoded = io.BytesIO()
            normalized.save(encoded, format="PNG", pnginfo=png_metadata, compress_level=6)
            png = encoded.getvalue()
            pixel_header = _canonical({"width": normalized.width, "height": normalized.height, "encoding": "RGBA8"})
            pixel_hash = _sha(pixel_header + b"\n" + normalized.convert("RGBA").tobytes())
            low_resolution = min(normalized.size) < 128 or max(normalized.size) < 512
            limitations = ["View labels are caller-supplied hypotheses, not verified camera poses.",
                           "Object dimensions and material properties cannot be established by image normalization."]
            if low_resolution:
                limitations.append(f"Low-resolution evidence ({normalized.width}x{normalized.height}); subpixel sampling cannot restore missing boundary or material detail.")
            if transparent:
                limitations.append("Transparent pixels require an alpha-aware observation stage; discarding alpha can create false contours.")
            return {"id": photo_id, "view": view, "source_path": str(source_path), "original": original,
                    "normalized": {"path": f"normalized/{photo_id}.png", "sha256": _sha(png), "pixel_sha256": pixel_hash,
                                   "pixel_hash_encoding": "canonical width/height/RGBA8 JSON, LF, decoded RGBA8 bytes",
                                   "width": normalized.width, "height": normalized.height, "mode": normalized.mode,
                                   "has_transparency": bool(transparent), "color_space": color_space, "icc_sha256": None,
                                   "srgb_chunk": icc is not None, "low_resolution": low_resolution},
                    "transform": {"exif_orientation": orientation, "operation": _OPERATIONS[orientation],
                                  "coordinate_system": "integer pixel centers; x right, y down",
                                  "original_to_normalized_xy": _orientation_matrix(orientation, width, height), "resized": False},
                    "assumptions": assumptions, "limitations": limitations}, png
    except (OSError, UnidentifiedImageError, SyntaxError) as error:
        raise ValueError(f"Photo {photo_id!r} is not a readable intact still image") from error


def _validate_output(output: Path) -> None:
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Output must be a new or empty directory")
    parent = output.parent
    while not parent.exists():
        parent = parent.parent
    if not parent.is_dir():
        raise ValueError("Output has a non-directory ancestor")


def prepare_input_bundle(request: dict, base_dir: Path, output: Path) -> dict:
    """Validate and snapshot >=2 distinct photographs; return/write manifest.json.

    Relative photo paths resolve against base_dir. Output follows ordinary Path
    semantics (relative to the caller's working directory). Bundle artifact paths
    are relative to output. ``initializer`` is opaque finite JSON, passed through
    without interpreting its keys or policy. The input digest excludes source and
    output locations but binds order, IDs, view hypotheses, all image content,
    dimensions, normalization metadata and the opaque initializer.
    """
    if not isinstance(request, dict) or not {"schema_version", "photos"} <= set(request) <= {
        "schema_version", "photos", "dimensions_mm", "initializer", "lens_facts"
    }:
        raise ValueError("Request requires schema_version/photos and only declared optional fields")
    if type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise ValueError("Unsupported input schema_version")
    photos = request["photos"]
    if not isinstance(photos, list) or len(photos) < 2:
        raise ValueError("At least two distinct photo records are required")
    base_dir, output = Path(base_dir).resolve(strict=True), Path(output).resolve()
    if not base_dir.is_dir():
        raise ValueError("base_dir must be an existing directory")
    _validate_output(output)
    dimensions = request.get("dimensions_mm", {})
    if not isinstance(dimensions, dict) or not set(dimensions) <= _DIMENSIONS:
        raise ValueError("dimensions_mm contains unsupported dimension names")
    clean_dimensions = {}
    for name, value in dimensions.items():
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"Dimension {name} must be a finite positive number in millimeters")
        try:
            number = float(value)
        except (ValueError, OverflowError) as error:
            raise ValueError(f"Dimension {name} must be finite") from error
        if not math.isfinite(number) or number <= 0:
            raise ValueError(f"Dimension {name} must be a finite positive number in millimeters")
        clean_dimensions[name] = number
    facts = request.get("lens_facts", {})
    if not isinstance(facts, dict) or not set(facts) <= set(_LENS_FACTS):
        raise ValueError("lens_facts contains unsupported fact names")
    clean_facts = {}
    for name, value in facts.items():
        if type(value) is not _LENS_FACTS[name]:
            raise ValueError(f"Lens fact {name} must be a {_LENS_FACTS[name].__name__}")
        clean_facts[name] = value
    initializer = request.get("initializer", {})
    if not isinstance(initializer, dict):
        raise ValueError("initializer must be an opaque dictionary")
    _canonical(initializer)  # Serialization only: no initializer settings are interpreted.
    initializer = deepcopy(initializer)
    records, artifacts, identifiers, original_hashes, pixel_hashes = [], [], set(), set(), set()
    for index, photo in enumerate(photos):
        if not isinstance(photo, dict) or not {"path"} <= set(photo) <= {"path", "id", "view"}:
            raise ValueError("Each photo requires path and only optional id/view fields")
        photo_id, view = photo.get("id", f"photo-{index + 1:03d}"), photo.get("view", "unknown")
        if not isinstance(photo_id, str) or not _SLUG.fullmatch(photo_id) or _DEVICE.fullmatch(photo_id):
            raise ValueError("Photo IDs must be safe 1-64 character slugs, not reserved device names")
        if photo_id.casefold() in identifiers:
            raise ValueError("Photo IDs must be unique, including case-insensitive filesystems")
        identifiers.add(photo_id.casefold())
        if not isinstance(view, str) or view not in _VIEWS:
            raise ValueError("Unsupported photo view label")
        name = photo["path"]
        if not isinstance(name, str) or not name.strip() or "\0" in name:
            raise ValueError("Photo paths must be nonempty file path strings")
        try:
            path = Path(name)
            path = (path if path.is_absolute() else base_dir / path).resolve(strict=True)
            if not path.is_file():
                raise ValueError("Photo paths must refer to files")
            raw = path.read_bytes()
        except (OSError, RuntimeError) as error:
            raise ValueError(f"Photo {photo_id!r} cannot be read from its supplied path") from error
        record, png = _normalize(raw, photo_id, view, path)
        if record["original"]["sha256"] in original_hashes or record["normalized"]["pixel_sha256"] in pixel_hashes:
            raise ValueError("Duplicate encoded photos or normalized pixels cannot supply distinct evidence")
        original_hashes.add(record["original"]["sha256"])
        pixel_hashes.add(record["normalized"]["pixel_sha256"])
        records.append(record)
        artifacts.extend(((record["original"]["path"], raw), (record["normalized"]["path"], png)))
    manifest = {"schema_version": 1, "normalization_version": NORMALIZATION_VERSION, "photos": records,
                "dimensions_mm": clean_dimensions, "lens_facts": clean_facts, "initializer": initializer,
                "calibration": {"status": "unmeasured", "camera": "unmeasured", "illumination": "unmeasured",
                                "color": "unmeasured", "physical_scale": "stated_dimensions_only" if clean_dimensions else "unmeasured"}}
    key_payload = deepcopy(manifest)
    for photo in key_payload["photos"]:
        del photo["source_path"]
    manifest["input_sha256"] = _sha(_canonical(key_payload))
    encoded_manifest = json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
    # No directories/files are created until every image and JSON value passes.
    _validate_output(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "originals").mkdir()
    (output / "normalized").mkdir()
    for relative, data in artifacts:
        with (output / relative).open("xb") as handle:
            handle.write(data)
    with (output / "manifest.json").open("xb") as handle:
        handle.write(encoded_manifest)
    return manifest
