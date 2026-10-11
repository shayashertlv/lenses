"""Host images of the facet lab (PIL): the facet_variants sheet and the photo_image view.

    variant_layout(n_panels, aspect=None) -> (columns, panel_w, panel_h)
        the per-panel box of a sheet of n panels (photo, head, variants), <= SHEET_W x SHEET_H overall, panels
        <= PANEL_W wide (WP6_FACETS §4.1). Without ``aspect`` 2 columns; with the crop's aspect (width / height) the
        2 or 3 columns that show such a crop largest (a wide side-view crop keeps 2 columns; Prada's top-photo crop,
        aspect 1.43, takes 3: 11 % larger at 5 panels, 37 % at 8); renders are made at a scale that fits the box,
        never upscaled.
    variant_sheet(panels, title, layout=None) -> Image
        panels = [(label, Image | None, note)]: an image panel, or a refused variant drawn as a labelled grey panel;
        ``layout`` = variant_layout's triple the panels were rendered for (default variant_layout(len(panels))).
        Labels and the title are cut to the sheet's pixels (the title at most TITLE_MAX characters first).
    crease_legend(min_deg) -> str
        the title's legend: the creases drawn (from min_deg, compare.CREASE_MIN_DEG: a crease under it shows on no
        panel) and the line colours.
    with_outline(img, mask, colour) -> Image
        the mask's boundary drawn over an image (the photo's silhouette on a render panel).
    with_creases(img, polylines, width) -> Image
        crease polylines [(points (K, 2) px, shape)] drawn over an image in photo_compare's overlay colours (ridge
        orange, valley blue: compare.RIDGE_RGB / VALLEY_RGB).
    crease_width(px_per_mm) -> int
        the line width for a panel shown at px_per_mm: 2 px as in the compare overlay, 1 px below
        CREASE_THIN_PX_PER_MM, where 2 px lines would merge.
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

from glasses3d.g3d_host.compare import RIDGE_RGB, VALLEY_RGB

SHEET_W, SHEET_H, PANEL_W = 1280, 900, 640
TITLE_PX, LABEL_PX, GAP_PX = 28, 20, 4
LABEL_FONT_PX, TITLE_FONT_PX = 14, 15
LAYOUT_COLUMNS = (2, 3)
IMAGE_MAX = 1024
OUTLINE = (230, 0, 160)
REFUSED_BG = (214, 214, 214)
CREASE_THIN_PX_PER_MM = 3.0
TITLE_MAX = 220                  # characters; the sheet's width (about 190 characters at 15 px) then cuts it


def crease_legend(min_deg: float) -> str:
    return f"creases from {min_deg:g}° (ridge orange, valley blue), photo outline magenta"


def _font(px: int):
    return ImageFont.load_default(size=px)


def _box(n_panels: int, cols: int) -> tuple[int, int, int]:
    rows = max(1, math.ceil(n_panels / cols))
    panel_h = (SHEET_H - TITLE_PX - rows * (LABEL_PX + GAP_PX) - GAP_PX) // rows
    panel_w = (SHEET_W - (cols + 1) * GAP_PX) // cols
    return cols, min(PANEL_W, panel_w), int(panel_h)


def variant_layout(n_panels: int, aspect: float | None = None) -> tuple[int, int, int]:
    if aspect is None or not aspect > 0:
        return _box(n_panels, 2)

    def scale(box):                                   # the size a crop of this aspect is shown at (never upscaled)
        _c, pw, ph = box
        return min(pw / aspect, ph)
    return max((_box(n_panels, c) for c in LAYOUT_COLUMNS), key=lambda b: (scale(b), -b[0]))


def with_outline(img: Image.Image, mask: np.ndarray, colour=OUTLINE) -> Image.Image:
    m = np.asarray(mask, bool)
    edge = m & ~ndimage.binary_erosion(m, iterations=1, border_value=0)
    out = np.asarray(img.convert("RGB")).copy()
    out[edge] = colour
    return Image.fromarray(out)


def crease_width(px_per_mm: float | None) -> int:
    return 1 if px_per_mm is not None and px_per_mm < CREASE_THIN_PX_PER_MM else 2


def with_creases(img: Image.Image, polylines: list, width: int = 2) -> Image.Image:
    im = img.convert("RGB").copy()
    dr = ImageDraw.Draw(im)
    for pts, shape in polylines:
        dr.line([tuple(map(float, p)) for p in pts], fill=RIDGE_RGB if shape > 0 else VALLEY_RGB, width=width)
    return im


def _refused_panel(size: tuple[int, int], note: str) -> Image.Image:
    im = Image.new("RGB", size, REFUSED_BG)
    dr = ImageDraw.Draw(im)
    lines = textwrap.wrap(note, width=max(20, size[0] // 8))[: max(1, size[1] // 16)]
    dr.multiline_text((8, 8), "\n".join(lines), fill=(40, 40, 40), font=_font(13), spacing=3)
    return im


def _fit(dr: ImageDraw.ImageDraw, text: str, font, width: float) -> str:
    """text cut (with "...") to fit width px in font."""
    if dr.textlength(text, font=font) <= width:
        return text
    while text and dr.textlength(text + "...", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "..."


def variant_sheet(panels: list, title: str, layout: tuple | None = None) -> Image.Image:
    """Panels in the layout's columns under a title; each panel labelled above it; the sheet <= SHEET_W x SHEET_H."""
    cols, pw, ph = layout or variant_layout(len(panels))
    cells = []
    font = _font(LABEL_FONT_PX)
    for label, img, note in panels:
        if img is None:
            img = _refused_panel((pw, max(40, min(ph, 160))), note or "refused")
        img = img.convert("RGB")
        if img.width > pw or img.height > ph:                     # safety: never larger than the cell
            k = min(pw / img.width, ph / img.height)
            img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)
        cell = Image.new("RGB", (pw, img.height + LABEL_PX), (255, 255, 255))
        cell.paste(img, (0, LABEL_PX))
        dr = ImageDraw.Draw(cell)
        dr.text((3, 2), _fit(dr, label[:90], font, pw - 6), fill=(15, 15, 15), font=font)
        cells.append(cell)
    rows = max(1, math.ceil(len(cells) / cols))
    row_h = [max(c.height for c in cells[r * cols:(r + 1) * cols]) for r in range(rows)]
    W = cols * pw + (cols + 1) * GAP_PX
    H = TITLE_PX + sum(row_h) + (rows + 1) * GAP_PX
    sheet = Image.new("RGB", (W, H), (255, 255, 255))
    dr = ImageDraw.Draw(sheet)
    tfont = _font(TITLE_FONT_PX)
    dr.text((GAP_PX + 2, 6), _fit(dr, title[:TITLE_MAX], tfont, W - 2 * GAP_PX - 4), fill=(10, 10, 10), font=tfont)
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
