"""Host images of the facet lab (PIL): the facet_variants sheet and the photo_image view.

    variant_layout(n_panels) -> (columns, panel_w, panel_h)
        the per-panel box of a sheet of n panels (photo, head, variants): 2 columns, <= SHEET_W x SHEET_H overall,
        panels <= PANEL_W wide (WP6_FACETS §4.1); renders are made at a scale that fits it, never upscaled.
    variant_sheet(panels, title) -> Image
        panels = [(label, Image | None, note)]: an image panel, or a refused variant drawn as a labelled grey panel.
    with_outline(img, mask, colour) -> Image
        the mask's boundary drawn over an image (the photo's silhouette on a render panel).
    photo_view(rgb, crop) -> (Image, transform)
        a photo or a native-pixel crop of it, downscaled to a long side <= IMAGE_MAX (never upscaled), and the
        transform back to native pixels.
"""
from __future__ import annotations

import math
import textwrap

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

SHEET_W, SHEET_H, PANEL_W = 1280, 900, 640
TITLE_PX, LABEL_PX, GAP_PX = 28, 20, 4
IMAGE_MAX = 1024
OUTLINE = (230, 0, 160)
REFUSED_BG = (214, 214, 214)


def _font(px: int):
    return ImageFont.load_default(size=px)


def variant_layout(n_panels: int) -> tuple[int, int, int]:
    cols = 2
    rows = max(1, math.ceil(n_panels / cols))
    panel_h = (SHEET_H - TITLE_PX - rows * (LABEL_PX + GAP_PX) - GAP_PX) // rows
    panel_w = (SHEET_W - (cols + 1) * GAP_PX) // cols
    return cols, min(PANEL_W, panel_w), int(panel_h)


def with_outline(img: Image.Image, mask: np.ndarray, colour=OUTLINE) -> Image.Image:
    m = np.asarray(mask, bool)
    edge = m & ~ndimage.binary_erosion(m, iterations=1, border_value=0)
    out = np.asarray(img.convert("RGB")).copy()
    out[edge] = colour
    return Image.fromarray(out)


def _refused_panel(size: tuple[int, int], note: str) -> Image.Image:
    im = Image.new("RGB", size, REFUSED_BG)
    dr = ImageDraw.Draw(im)
    lines = textwrap.wrap(note, width=max(20, size[0] // 8))[: max(1, size[1] // 16)]
    dr.multiline_text((8, 8), "\n".join(lines), fill=(40, 40, 40), font=_font(13), spacing=3)
    return im


def variant_sheet(panels: list, title: str) -> Image.Image:
    """Panels in 2 columns under a title; each panel labelled above it; the sheet <= SHEET_W x SHEET_H."""
    cols, pw, ph = variant_layout(len(panels))
    cells = []
    for label, img, note in panels:
        if img is None:
            img = _refused_panel((pw, max(40, min(ph, 160))), note or "refused")
        img = img.convert("RGB")
        if img.width > pw or img.height > ph:                     # safety: never larger than the cell
            k = min(pw / img.width, ph / img.height)
            img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)
        cell = Image.new("RGB", (pw, img.height + LABEL_PX), (255, 255, 255))
        cell.paste(img, (0, LABEL_PX))
        ImageDraw.Draw(cell).text((3, 2), label[:90], fill=(15, 15, 15), font=_font(14))
        cells.append(cell)
    rows = max(1, math.ceil(len(cells) / cols))
    row_h = [max(c.height for c in cells[r * cols:(r + 1) * cols]) for r in range(rows)]
    W = cols * pw + (cols + 1) * GAP_PX
    H = TITLE_PX + sum(row_h) + (rows + 1) * GAP_PX
    sheet = Image.new("RGB", (W, H), (255, 255, 255))
    ImageDraw.Draw(sheet).text((GAP_PX + 2, 6), title[:150], fill=(10, 10, 10), font=_font(15))
    y = TITLE_PX + GAP_PX
    for r in range(rows):
        for c in range(cols):
            n = r * cols + c
            if n < len(cells):
                sheet.paste(cells[n], (GAP_PX + c * (pw + GAP_PX), y))
        y += row_h[r] + GAP_PX
    assert sheet.width <= SHEET_W and sheet.height <= SHEET_H, sheet.size
    return sheet


def photo_view(rgb: np.ndarray, crop=None) -> tuple[Image.Image, dict]:
    """(image, transform): the photo or its crop [x0, y0, x1, y1] (native pixels), downscaled by k <= 1 to a long
    side <= IMAGE_MAX."""
    H, W = rgb.shape[:2]
    x0, y0, x1, y1 = (0, 0, W, H) if crop is None else (int(v) for v in crop)
    im = Image.fromarray(np.asarray(rgb)[y0:y1, x0:x1, :3])
    k = min(1.0, IMAGE_MAX / max(im.width, im.height))
    if k < 1.0:
        im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
    return im, {"box": [x0, y0, x1, y1], "k": round(k, 4), "native_size": [W, H],
                "to_native": f"u = {x0} + (u_img + 0.5) / {k:.4f} - 0.5, v = {y0} + (v_img + 0.5) / {k:.4f} - 0.5"}
