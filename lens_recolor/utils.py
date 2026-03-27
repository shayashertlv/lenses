"""Image loading, saving, validation, and resize helpers."""

import os
import time
from pathlib import Path

from PIL import Image

from config import MAX_IMAGE_DIMENSION


def validate_input_image(path: str) -> dict:
    """
    Validate an input image and return info about it.

    Returns dict with:
    - is_valid: bool
    - width: int
    - height: int
    - format: str (JPEG, PNG, etc.)
    - file_size_mb: float
    - needs_resize: bool (if > 4096px on any side)
    - error: str or None
    """
    result = {
        "is_valid": False,
        "width": 0,
        "height": 0,
        "format": "",
        "file_size_mb": 0.0,
        "needs_resize": False,
        "error": None,
    }

    if not os.path.exists(path):
        result["error"] = f"File not found: {path}"
        return result

    file_size = os.path.getsize(path)
    result["file_size_mb"] = round(file_size / (1024 * 1024), 2)

    try:
        with Image.open(path) as img:
            img.verify()
    except Exception as e:
        result["error"] = f"Not a valid image file: {e}"
        return result

    # Re-open after verify (verify can leave file in bad state)
    with Image.open(path) as img:
        result["width"] = img.width
        result["height"] = img.height
        result["format"] = img.format or "UNKNOWN"

    max_side = max(result["width"], result["height"])
    result["needs_resize"] = max_side > MAX_IMAGE_DIMENSION
    result["is_valid"] = True

    return result


def load_image_bytes(path: str) -> tuple[bytes, str]:
    """
    Load image from path, resizing if necessary.
    Returns (image_bytes, mime_type).
    """
    info = validate_input_image(path)
    if not info["is_valid"]:
        raise ValueError(info["error"])

    img = Image.open(path)

    if info["needs_resize"]:
        print(f"  Image is {info['width']}x{info['height']} — resizing to fit {MAX_IMAGE_DIMENSION}px max side.")
        img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.LANCZOS)

    # Determine format and mime type
    fmt = img.format
    if fmt is None or info["needs_resize"]:
        # After resize, format is lost; determine from extension or default to PNG
        ext = Path(path).suffix.lower()
        if ext in (".jpg", ".jpeg"):
            fmt = "JPEG"
        elif ext == ".webp":
            fmt = "WEBP"
        else:
            fmt = "PNG"

    mime_map = {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
        "GIF": "image/gif",
    }
    mime_type = mime_map.get(fmt, "image/png")

    # Convert to bytes
    import io
    buf = io.BytesIO()
    # Ensure RGB for JPEG
    if fmt == "JPEG" and img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    img.save(buf, format=fmt)
    image_bytes = buf.getvalue()

    return image_bytes, mime_type


def save_image(data: bytes, output_path: str) -> str:
    """Save raw image bytes to file. Returns the path."""
    # Ensure output directory exists
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(output_path, "wb") as f:
        f.write(data)

    return output_path


def generate_output_path(input_path: str, suffix: str = "") -> str:
    """Generate a default output path based on input path and timestamp."""
    stem = Path(input_path).stem
    timestamp = int(time.time())
    ext = ".png"
    if suffix:
        return f"output_{stem}_{suffix}_{timestamp}{ext}"
    return f"output_{stem}_{timestamp}{ext}"
