"""Compact new image outputs; previously delivered conversation items are immutable."""
import math

from PIL import Image, ImageDraw, ImageOps

MAX_IMAGE_SIDE = 1024
# Local delivery bound, not a claim about the provider's maximum image dimensions.
LOOK_IMAGE_SIDE = 65535
IMAGE_POLICY = "g3d.append_only_panels/2"


def compact_sheet(images):
    """Return one RGB preview for (PIL image, label) pairs, without changing source files.

    A single image retains its pixel frame unless it needs scaling. Multiple source panels are
    numbered and labelled; source paths and native sizes remain in the accompanying JSON.
    """
    if not images:
        raise ValueError("a contact sheet needs at least one image")
    if len(images) == 1:
        result = ImageOps.exif_transpose(images[0][0]).convert("RGB")
        result.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
        return result
    cols = math.ceil(math.sqrt(len(images)))
    rows = math.ceil(len(images) / cols)
    width, height = MAX_IMAGE_SIDE // cols, MAX_IMAGE_SIDE // rows
    result = Image.new("RGB", (cols * width, rows * height), "#eeeeee")
    draw = ImageDraw.Draw(result)
    for index, (source, label) in enumerate(images):
        tile = ImageOps.exif_transpose(source).convert("RGB")
        tile.thumbnail((width - 8, height - 28), Image.Resampling.LANCZOS)
        x, y = (index % cols) * width, (index // cols) * height
        result.paste(tile, (x + (width - tile.width) // 2, y + 24 + (height - 28 - tile.height) // 2))
        draw.text((x + 4, y + 5), f"{index + 1}: {label}"[:max(8, width // 7)], fill="black")
    return result
