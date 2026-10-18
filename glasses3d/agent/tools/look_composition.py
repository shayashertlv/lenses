"""Lossless per-photo packing of already labelled full pairs and automatic details.

OpenAI's gpt-6-astra high detail leaves images unchanged at <=2,500 32px
patches and <=65,535 pixels per edge (including all labels and empty space):
https://developers.openai.com/api/docs/guides/images-vision (2026-10-17).
Packing never resizes, rotates or crops a source panel. Overflow gets another PNG.
"""
import hashlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .look_panels import HIGH_MAX_SIDE, HIGH_PATCH_BUDGET


def patches(size):
    return ((size[0] + 31) // 32) * ((size[1] + 31) // 32)


def _pack(sizes, width, height):
    pages = []
    # Keep the full pair first; place larger details before filling their gaps.
    order = sorted(range(len(sizes)), key=lambda i: (i != 0, -sizes[i][0] * sizes[i][1], i))
    for index in order:
        w, h = sizes[index]
        for page in pages:
            choices = [r for r in page["free"] if r[2] - r[0] >= w and r[3] - r[1] >= h]
            if choices:
                break
        else:
            page = {"free": [(0, 0, width, height)], "parts": [], "size_px": [0, 0]}
            pages.append(page)
            choices = page["free"]
        x, y, _, _ = min(choices, key=lambda r: (
            min(r[2] - r[0] - w, r[3] - r[1] - h),
            max(r[2] - r[0] - w, r[3] - r[1] - h), r[1], r[0]))
        box = [x, y, x + w, y + h]
        page["parts"].append({"index": index, "box": box})
        page["size_px"] = [max(page["size_px"][0], x + w), max(page["size_px"][1], y + h)]
        free = []
        for left, top, right, bottom in page["free"]:
            if x >= right or x + w <= left or y >= bottom or y + h <= top:
                free.append((left, top, right, bottom))
                continue
            if left < x:
                free.append((left, top, x, bottom))
            if x + w < right:
                free.append((x + w, top, right, bottom))
            if top < y:
                free.append((left, top, right, y))
            if y + h < bottom:
                free.append((left, y + h, right, bottom))
        # Maximal empty rectangles may overlap each other, but never a placed panel.
        free = sorted(set(free))
        page["free"] = [r for r in free if not any(
            q != r and q[0] <= r[0] and q[1] <= r[1] and q[2] >= r[2] and q[3] >= r[3]
            for q in free)]
    return [{"size_px": p["size_px"], "parts": p["parts"]} for p in pages]


def layout(sizes):
    """Plan packing from dimensions alone, also usable for offline history counts.

    Try every feasible patch-aligned canvas width; prefer fewer pages, then fewer
    patches and shorter edges. Each panel already fits high detail on its own.
    """
    if not sizes:
        return []
    for size in sizes:
        if (len(size) != 2 or any(type(v) is not int or v <= 0 for v in size)
                or max(size) > HIGH_MAX_SIDE or patches(size) > HIGH_PATCH_BUDGET):
            raise ValueError("a source panel exceeds high detail; cannot shrink it to compose")
    min_columns = (max(w for w, h in sizes) + 31) // 32
    min_rows = (max(h for w, h in sizes) + 31) // 32
    max_columns = min((HIGH_MAX_SIDE + 31) // 32, HIGH_PATCH_BUDGET // min_rows)
    best, best_score = None, None
    for columns in range(min_columns, max_columns + 1):
        width = min(HIGH_MAX_SIDE, columns * 32)
        height = min(HIGH_MAX_SIDE, (HIGH_PATCH_BUDGET // columns) * 32)
        pages = _pack(sizes, width, height)
        score = (len(pages), sum(patches(p["size_px"]) for p in pages),
                 max(max(p["size_px"]) for p in pages))
        if best_score is None or score < best_score:
            best, best_score = pages, score
    # Wide and tall panels can each fit alone without sharing a feasible canvas.
    if best is None:
        return [{"size_px": list(size), "parts": [{"index": i, "box": [0, 0, *size]}]}
                for i, size in enumerate(sizes)]
    return best


def compose(images, output_dir, ref):
    """Keep complete source PNGs and their metadata; return only the packed images."""
    sizes = []
    source_sizes = []
    for record in images:
        with Image.open(record["path"]) as source:
            if source.mode != "RGB":
                raise ValueError("look composition requires RGB panels to preserve every channel")
            source_sizes.append(source.size)
            if record.get("comparison_phase") in ("Before", "After"):
                sizes.append((max(64, source.width), source.height + 24))
            else:
                sizes.append(source.size)
    pages = layout(sizes)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    result = []
    for number, page in enumerate(pages, 1):
        canvas = Image.new("RGB", page["size_px"], "#ededed")
        parts = []
        for placement in page["parts"]:
            record = images[placement["index"]]
            left, top = placement["box"][:2]
            if record.get("comparison_phase") in ("Before", "After"):
                ImageDraw.Draw(canvas).text((left + 4, top + 2), record["comparison_phase"],
                                           font=ImageFont.load_default(size=14), fill="black")
                top += 24
            width, height = source_sizes[placement["index"]]
            box = [left, top, left + width, top + height]
            with Image.open(record["path"]) as source:
                canvas.paste(source, box[:2])
            parts.append({**record, "box": box})
        path = directory / f"combined-{number:02d}.png"
        canvas.save(path)
        result.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "size_px": list(canvas.size), "patches_32px": patches(canvas.size),
                       "kind": "look_composed", "ref": ref,
                       "view": f"{ref}: native comparisons / close-ups / labels, page {number}/{len(pages)}; unchanged pixels",
                       "parts": parts})
    return result
