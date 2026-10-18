"""Label and tile matched image grids without resampling any source pixel."""
import hashlib
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HIGH_PATCH_BUDGET = 2500
HIGH_MAX_SIDE = 65535


def patches(size):
    return math.ceil(size[0] / 32) * math.ceil(size[1] / 32)


def _lines(text, font, width):
    """Wrap even an unbroken reference/mesh name; never clip its last glyph."""
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for char in paragraph:
            if line and measure.textbbox((0, 0), line + char, font=font)[2] > width:
                lines.append(line)
                line = ""
            line += char
        lines.append(line)
    return lines


def comparison(images, box, path, title, headers, *, gap=8, max_side=HIGH_MAX_SIDE, font=None):
    """Return an archival full comparison and provider-sized native comparison tiles.

    A tile contains the same half-open source box in every column. Labels live in
    padded headers, never on source pixels. Overflow is tiled, never resized.
    """
    if not images or len(images) != len(headers) or len({im.size for im in images}) != 1:
        raise ValueError("comparison sources must share the same camera pixel grid")
    if any(im.mode != "RGB" for im in images):
        raise ValueError("native comparisons require RGB sources")
    path = Path(path)
    font = font or ImageFont.load_default(size=14)
    count = len(images)
    max_side = min(max_side, HIGH_MAX_SIDE)
    padding = 8
    line_height = font.getbbox("Ag")[3] + 5
    min_column = min(160, (max_side - gap * (count - 1)) // count)
    if min_column < padding * 2 + 16:
        raise ValueError("comparison limit must allow readable labels")

    def metrics(width, height):
        column = max(width, min_column)
        total_width = count * column + gap * (count - 1)
        title_lines = _lines(title, font, max(1, total_width - padding * 2)) if title else []
        header_lines = [_lines(label, font, column - padding * 2) for label in headers]
        header = 2 * padding + line_height * (len(title_lines) + max(map(len, header_lines)))
        return (total_width, height + header), column, header, title_lines, header_lines

    def fits(size):
        return max(size) <= max_side and patches(size) <= HIGH_PATCH_BUDGET

    def write(crop, destination, column_width=None):
        x0, y0, x1, y1 = crop
        size, column, header, title_lines, header_lines = metrics(column_width or x1 - x0, y1 - y0)
        canvas = Image.new("RGB", size, "#ededed")
        draw = ImageDraw.Draw(canvas)
        labels = []
        for index, line in enumerate(title_lines):
            at = (padding, padding + index * line_height)
            draw.text(at, line, font=font, fill="black")
            labels.append(list(draw.textbbox(at, line, font=font)))
        content = []
        for index, (source, lines) in enumerate(zip(images, header_lines)):
            left = index * (column + gap)
            for row, line in enumerate(lines):
                at = (left + padding, padding + (len(title_lines) + row) * line_height)
                draw.text(at, line, font=font, fill="black")
                labels.append(list(draw.textbbox(at, line, font=font)))
            left += (column - (x1 - x0)) // 2
            canvas.paste(source.crop(crop), (left, header))
            content.append([left, header, left + x1 - x0, header + y1 - y0])
        canvas.save(destination)
        return {"path": str(destination), "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                "size_px": list(size), "patches_32px": patches(size), "crop_box": list(crop),
                "native_pixels": True, "headers": list(headers), "label_boxes": labels,
                "source_panel_size_px": [x1 - x0, y1 - y0], "panel_width_px": column,
                "panel_content_origin_px": content[0][:2], "panel_boxes": content}

    width, height = box[2] - box[0], box[3] - box[1]
    if width <= 0 or height <= 0:
        raise ValueError("comparison pixel box is empty")
    full = write(box, path)
    if fits(full["size_px"]):
        return full, [full]
    # Search native tile widths for the fewest images, then the largest useful
    # tile area. Header wrapping and rounded patch dimensions count in full.
    max_width = min(width, (max_side - gap * (count - 1)) // count)
    choices = []
    for w in sorted({min_column, max_width, *range(32, max_width + 1, 32)}):
        if not 0 < w <= max_width:
            continue
        size, _, header, _, _ = metrics(w, 0)
        h = min(height, max_side - header,
                (HIGH_PATCH_BUDGET // math.ceil(size[0] / 32)) * 32 - header)
        if h > 0 and fits((size[0], h + header)):
            choices.append((math.ceil(width / w) * math.ceil(height / h), -w * h, w, h))
    if not choices:
        raise ValueError("comparison labels exceed the native image budget")
    _, _, tile_width, tile_height = min(choices)
    tiles = []
    for y in range(box[1], box[3], tile_height):
        for x in range(box[0], box[2], tile_width):
            crop = [x, y, min(x + tile_width, box[2]), min(y + tile_height, box[3])]
            destination = path.with_name(f"{path.stem}-tile-{len(tiles):03d}{path.suffix}")
            tile = write(crop, destination, tile_width)
            tile.update(tile=len(tiles) + 1, full_comparison_path=str(path))
            tiles.append(tile)
    return full, tiles
