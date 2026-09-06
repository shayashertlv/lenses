"""Image helpers and shared utilities."""

import io
import os
from pathlib import Path

from PIL import Image, ImageOps

from config import MAX_IMAGE_DIMENSION


def validate_image(path: str) -> dict:
    """
    Validate an image file and return info about it.

    Returns dict with:
    - is_valid, width, height, format, file_size_mb, needs_resize, error
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

    result["file_size_mb"] = round(os.path.getsize(path) / (1024 * 1024), 2)

    try:
        with Image.open(path) as img:
            img.verify()
    except Exception as e:
        result["error"] = f"Not a valid image file: {e}"
        return result

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
    info = validate_image(path)
    if not info["is_valid"]:
        raise ValueError(info["error"])

    img = Image.open(path)
    img = ImageOps.exif_transpose(img)

    if info["needs_resize"]:
        print(f"  Resizing {info['width']}x{info['height']} to fit {MAX_IMAGE_DIMENSION}px max side.")
        img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.LANCZOS)

    # Determine format
    fmt = img.format
    if fmt is None or info["needs_resize"]:
        ext = Path(path).suffix.lower()
        fmt_map = {".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP", ".png": "PNG"}
        fmt = fmt_map.get(ext, "PNG")

    mime_map = {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
        "GIF": "image/gif",
    }
    mime_type = mime_map.get(fmt, "image/png")

    buf = io.BytesIO()
    if fmt == "JPEG" and img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    img.save(buf, format=fmt)
    return buf.getvalue(), mime_type


def load_image_as_part(path: str):
    """Load image and return a google.genai types.Part."""
    from google.genai import types
    image_bytes, mime_type = load_image_bytes(path)
    return types.Part.from_bytes(data=image_bytes, mime_type=mime_type)


def normalize_tryon_aspect(tryon_bytes: bytes, portrait_path: str) -> bytes:
    """Resize a try-on image to match the portrait's aspect ratio.

    Nano Banana Pro often returns images at its native resolution (e.g.
    1024×1024) regardless of the input portrait dimensions.  If the aspect
    ratios differ significantly the result looks stretched / squished.

    Centre-crops the try-on output to the portrait's aspect ratio, then
    scales it to the portrait's pixel size.
    """
    try:
        portrait = ImageOps.exif_transpose(Image.open(portrait_path))
        tryon = Image.open(io.BytesIO(tryon_bytes))

        pw, ph = portrait.size
        tw, th = tryon.size

        target_ratio = pw / ph
        tryon_ratio = tw / th

        if abs(target_ratio - tryon_ratio) / max(target_ratio, tryon_ratio) < 0.05:
            return tryon_bytes

        if tryon_ratio > target_ratio:
            new_w = int(th * target_ratio)
            left = (tw - new_w) // 2
            tryon = tryon.crop((left, 0, left + new_w, th))
        else:
            new_h = int(tw / target_ratio)
            top = (th - new_h) // 2
            tryon = tryon.crop((0, top, tw, top + new_h))

        tryon = tryon.resize((pw, ph), Image.LANCZOS)

        buf = io.BytesIO()
        tryon.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return tryon_bytes


def save_image(data: bytes, output_path: str) -> str:
    """Save raw image bytes to file."""
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(data)
    return output_path
