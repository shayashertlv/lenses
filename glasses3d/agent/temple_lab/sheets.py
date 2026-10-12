"""Host images of the facet lab (PIL): the facet_variants pages and the photo_image view (WP6_V3 §6).

    variant_scale(w, h, models) -> k
        the scale of a variant panel for a native box w x h on pages of ``models`` model pairs (the head and every
        arm drawn): at most k_cell = min(VARIANT_K_MAX 2.0, CELL_W / w, CELL_H_MAX / h) (zoom's rule,
        compare.zoom_scale: a page always holds the photo pair and one model pair), at least min(VARIANT_K_FLOOR 1.0,
        k_cell) (native when a cell allows, WP6_V3 §6); within that, the largest k (on a 1 % grid, and k_cell itself)
        that needs the fewest pages. Renders are made at this scale (never upscaled).
    variant_page_count(w, h, models) -> int
        the pages one photo of a facet_variants call returns for that box and model count (at variant_scale): the
        cost estimate's number (cli). At native scale a box of about CELL_W x CELL_H_MAX holds one model pair a page in
        either orientation, so the worst case is ``models`` pages a photo (1 + the arms drawn; review2-lab-e2-1 P2-3:
        B1's 429 x 276 box gives 7 at 6 arms); stacking a pair (clean above lined) halves it for boxes like 02's 348 x
        307 (7 -> 4).
    variant_pages(photo_pair, pairs, title, notes="") -> [Image]
        photo_pair = (label, the photo panel, label, the photo with the head's edges); pairs = [(label, clean, lined,
        note)] per model DRAWN (the head first, then each arm rendered; a refused arm or one equal to the head or to an
        earlier arm has no panels: its facts are in the tool's text and ``notes`` names it on every page, review2-lab-
        e2-1 P2-3: a refused arm's grey panel took a whole page at B1). Each model's clean render beside (or above)
        the same render with the photo's outline and that model's edges (scorecard #29: run 6's fade was chosen on a
        sheet at 3.46 px/mm where the lines covered 83-96 % of the shading differences); a pair stacks clean above lined
        when that holds more pairs a page (_vertical). One label per pair across its width, on two lines when the pair
        is narrower than LABEL_ONE_LINE_PX (a tall box's 70 px cells cut labels to 8 characters, review2-lab-e2-2 P3-6).
        Pages of at most SHEET_W x SHEET_H; each starts with its header (the title with "page i/n: " first when there
        are several, wrapped onto TITLE_LINES lines; the legend on its own line; ``notes`` on the next) and the photo
        pair, so every page compares against the photo.
    header_lines(dr, title, notes, page, pages, width, vertical) -> [str]
        a page's HEADER_LINES header lines (variant_pages draws them; the tests read them).
    edge_legend(vertical) -> str
        the legend line: the model edges (compare.draw_edges' colours and labels), the photo outline (green: magenta is
        the MODEL's outline in every compare, review2-lab-e2-2 P3-7) and how a pair reads.
    with_outline(img, mask, colour) -> Image
        the mask's boundary drawn over an image (the photo's silhouette on a render panel).
    photo_view(rgb, crop, enhance=False, scale=1.0) -> (Image, transform)
        a photo or a native-pixel crop of it at scale k = min(scale, IMAGE_MAX / its long side) (scale up to the lab's
        state.SCALE_MAX: a small crop shown larger, bilinear on the exact pixel grid, so u = x0 + (u_img + 0.5) / k -
        0.5 holds), with ``enhance`` a contrast stretch (the crop's p1-p99 luma to 0-255, compare.apply_stretch: hue
        kept), and the transform back to native pixels.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from glasses3d.g3d_host import compare as C

SHEET_W, SHEET_H, PANEL_W = 1280, 900, 640
TITLE_PX, LABEL_PX, GAP_PX = 28, 20, 4           # LABEL_PX: one label line; TITLE_PX: the v2 sheet's (T5 rehearsal)
TITLE_LINES = 2                                  # a variant page's title lines; then the legend and the notes line
HEADER_LINES = TITLE_LINES + 2
LABEL_FONT_PX, TITLE_FONT_PX = 14, 15
LABEL_PX2 = LABEL_PX + LABEL_FONT_PX + 3         # a two-line pair label
LABEL_ONE_LINE_PX = 340                          # a pair label this wide takes one line (~50 characters), else two
IMAGE_MAX = 1024
ENHANCE_PCT = (1.0, 99.0)        # photo_image enhance: the crop's luma percentiles stretched to 0-255
OUTLINE = (0, 200, 60)           # the photo's outline on a variant render panel (green: compare's magenta = the model)
TITLE_MAX = 300                  # characters; the page's width then wraps or cuts it
VARIANT_K_MAX = 2.0              # = compare.ZOOM_MAX_K: a variant panel is never more than 2 x native
VARIANT_K_FLOOR = 1.0            # variant_scale's smallest k where a cell allows it (WP6_V3 §6: native)
PAGE_MIN_W = 900                 # a variant page is at least this wide (its legend reads on one line)
CELL_W = (SHEET_W - 3 * GAP_PX) // 2                                                 # 634: one pair fills a row
PAGE_TITLE_PX = HEADER_LINES * (TITLE_FONT_PX + 4) + 2 * GAP_PX                      # 84
CELL_H_MAX = (SHEET_H - PAGE_TITLE_PX - GAP_PX) // 2 - LABEL_PX - GAP_PX             # 382: two rows a page at least
SCALE_STEP = 0.01                # variant_scale searches k on this grid
VERTICAL_UPTO = 3                # pairs stack only when side by side a page holds this many pairs or fewer


def _font(px: int):
    return ImageFont.load_default(size=px)


def edge_legend(vertical: bool = False) -> str:
    """The legend line (module doc): <= 873 px at TITLE_FONT_PX, one line on a PAGE_MIN_W page."""
    pair = "each model: clean above, edges below" if vertical else "each model: clean | with edges"
    return ("edges: ridge orange, valley blue, flat grey, soft dashed; labels deg (- valley); photo outline green; "
            + pair)


def with_outline(img: Image.Image, mask: np.ndarray, colour=OUTLINE) -> Image.Image:
    m = np.asarray(mask, bool)
    edge = m & ~ndimage.binary_erosion(m, iterations=1, border_value=0)
    out = np.asarray(img.convert("RGB")).copy()
    out[edge] = colour
    return Image.fromarray(out)


def _fit(dr: ImageDraw.ImageDraw, text: str, font, width: float) -> str:
    """text cut (with "...") to fit width px in font."""
    if dr.textlength(text, font=font) <= width:
        return text
    while text and dr.textlength(text + "...", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "..."


def _wrap(dr: ImageDraw.ImageDraw, text: str, font, width: float, lines: int) -> list[str]:
    """text in at most ``lines`` lines of width px (broken at spaces; the last one cut with "...")."""
    out, rest = [], text.split()
    while rest and len(out) < lines:
        line = rest.pop(0)
        while rest and dr.textlength(line + " " + rest[0], font=font) <= width:
            line += " " + rest.pop(0)
        out.append(line)
    if rest:
        out[-1] = _fit(dr, out[-1] + " " + " ".join(rest), font, width)
    return [_fit(dr, ln, font, width) for ln in out]


def _label_px(width: int) -> int:
    """The label height of a pair width px wide: one line from LABEL_ONE_LINE_PX, else two."""
    return LABEL_PX if width >= LABEL_ONE_LINE_PX else LABEL_PX2


def _shape(cw: int, ch: int, vertical: bool) -> tuple[int, int, int]:
    """(width, height, label height) of one pair of cw x ch panels: side by side, or stacked (vertical)."""
    if vertical:
        lh = _label_px(cw)
        return cw, lh + 2 * ch + GAP_PX, lh
    lh = _label_px(2 * cw + GAP_PX)
    return 2 * cw + GAP_PX, lh + ch, lh


def _grid(cw: int, ch: int, vertical: bool = False) -> tuple[int, int]:
    """(pairs a row, rows a page) for panels cw x ch, pairs side by side or stacked."""
    pw, ph, _lh = _shape(cw, ch, vertical)
    per_row = max(1, (SHEET_W - GAP_PX) // (pw + GAP_PX))
    rows = max(1, (SHEET_H - PAGE_TITLE_PX - GAP_PX) // (ph + GAP_PX))
    return per_row, rows


def _vertical(cw: int, ch: int) -> bool:
    """True when side-by-side pairs hold at most VERTICAL_UPTO pairs a page and stacked pairs (clean above lined) hold
    more and fit the page (a tie keeps them side by side). Only a large panel stacks: a narrow one (a tall box) keeps
    its pair's label two panels wide."""
    pw, ph, _lh = _shape(cw, ch, True)
    if pw + 2 * GAP_PX > SHEET_W or ph + GAP_PX > SHEET_H - PAGE_TITLE_PX - GAP_PX:
        return False
    h, v = _grid(cw, ch, False), _grid(cw, ch, True)
    return h[0] * h[1] <= VERTICAL_UPTO and v[0] * v[1] > h[0] * h[1]


def _per_page(cw: int, ch: int) -> int:
    """Model pairs on one page of panels cw x ch (the photo pair comes first on every page)."""
    per_row, rows = _grid(cw, ch, _vertical(cw, ch))
    return max(1, per_row * rows - 1)


def variant_scale(w: int, h: int, models: int = 1) -> float:
    w, h, models = max(1, int(w)), max(1, int(h)), max(1, int(models))
    k_cell = float(min(VARIANT_K_MAX, CELL_W / w, CELL_H_MAX / h))
    k_min = min(VARIANT_K_FLOOR, k_cell)
    cands = [k_cell] + [float(k) for k in np.arange(k_min, k_cell, SCALE_STEP)]

    def pages(k):
        return math.ceil(models / _per_page(max(1, round(w * k)), max(1, round(h * k))))
    best = min(pages(k) for k in cands)
    return max(k for k in cands if pages(k) == best)


def variant_page_count(w: int, h: int, models: int) -> int:
    k = variant_scale(w, h, models)
    return math.ceil(max(1, int(models)) / _per_page(max(1, round(max(1, int(w)) * k)),
                                                     max(1, round(max(1, int(h)) * k))))


def _pair(label: str, a: Image.Image | None, b: Image.Image | None, cw: int, ch: int, vertical: bool,
          font) -> Image.Image:
    """One pair (module doc): its label across its width (one or two lines), then a and b side by side or stacked;
    an image never larger than its cell (a safety: the lab renders at the cell's scale)."""
    pw, ph, lh = _shape(cw, ch, vertical)
    im = Image.new("RGB", (pw, ph), (255, 255, 255))
    for k, img in enumerate((a, b)):
        if img is None:
            continue
        img = img.convert("RGB")
        if img.width > cw or img.height > ch:
            s = min(cw / img.width, ch / img.height)
            img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.BILINEAR)
        im.paste(img, (0, lh + k * (ch + GAP_PX)) if vertical else (k * (cw + GAP_PX), lh))
    dr = ImageDraw.Draw(im)
    lines = 1 if lh == LABEL_PX else 2
    for i, ln in enumerate(_wrap(dr, label[:160], font, pw - 6, lines)):
        dr.text((3, 2 + i * (LABEL_FONT_PX + 3)), ln, fill=(15, 15, 15), font=font)
    return im


def header_lines(dr: ImageDraw.ImageDraw, title: str, notes: str, page: int, pages: int, width: int,
                 vertical: bool) -> list[str]:
    """A variant page's header, HEADER_LINES lines (the last "" without notes): the title with "page i/n: " first
    when there are several pages, wrapped onto TITLE_LINES lines; the legend (edge_legend); the notes, each cut to
    the page's width."""
    font = _font(TITLE_FONT_PX)
    room = width - 2 * GAP_PX - 4
    lines = _wrap(dr, (f"page {page}/{pages}: " if pages > 1 else "") + title[:TITLE_MAX], font, room, TITLE_LINES)
    lines += [""] * (TITLE_LINES - len(lines))
    return lines + [_fit(dr, edge_legend(vertical), font, room), _fit(dr, notes, font, room) if notes else ""]


def variant_pages(photo_pair: tuple, pairs: list, title: str, notes: str = "") -> list[Image.Image]:
    imgs = [photo_pair[1], photo_pair[3]] + [im for r in pairs for im in (r[1], r[2]) if im is not None]
    cw = min(CELL_W, max(im.width for im in imgs))
    ch = min(CELL_H_MAX, max(im.height for im in imgs))
    vertical = _vertical(cw, ch)
    per_row, rows = _grid(cw, ch, vertical)
    pw, ph, _lh = _shape(cw, ch, vertical)
    per = max(1, per_row * rows - 1)
    n = max(1, math.ceil(len(pairs) / per))
    font, tfont = _font(LABEL_FONT_PX), _font(TITLE_FONT_PX)
    head = (f"{photo_pair[0]} | {photo_pair[2]}", photo_pair[1], photo_pair[3])
    models = [(r[0] + (f" ({r[3]})" if len(r) > 3 and r[3] else ""), r[1], r[2]) for r in pairs]
    pages = []
    for p in range(n):
        chunk = [head] + models[p * per:(p + 1) * per]
        cols = min(per_row, len(chunk))
        W = max(PAGE_MIN_W, GAP_PX + cols * (pw + GAP_PX))
        H = PAGE_TITLE_PX + GAP_PX + math.ceil(len(chunk) / per_row) * (ph + GAP_PX)
        page = Image.new("RGB", (W, H), (255, 255, 255))
        dr = ImageDraw.Draw(page)
        for i, ln in enumerate(header_lines(dr, title, notes, p + 1, n, W, vertical)):
            dr.text((GAP_PX + 2, GAP_PX + i * (TITLE_FONT_PX + 4)), ln, fill=(10, 10, 10), font=tfont)
        for k, (label, a, b) in enumerate(chunk):
            x = GAP_PX + (k % per_row) * (pw + GAP_PX)
            y = PAGE_TITLE_PX + GAP_PX + (k // per_row) * (ph + GAP_PX)
            page.paste(_pair(label, a, b, cw, ch, vertical, font), (x, y))
        assert page.width <= SHEET_W and page.height <= SHEET_H, page.size
        pages.append(page)
    return pages


def photo_view(rgb: np.ndarray, crop=None, *, enhance: bool = False, scale: float = 1.0) -> tuple[Image.Image, dict]:
    """(image, transform): the photo or its crop [x0, y0, x1, y1] (native pixels) at k = min(scale, IMAGE_MAX / long
    side), resampled on the exact pixel grid (bilinear: never overshooting) when k != 1, optionally contrast-stretched
    (module doc)."""
    H, W = rgb.shape[:2]
    x0, y0, x1, y1 = (0, 0, W, H) if crop is None else (int(v) for v in crop)
    a = np.ascontiguousarray(np.asarray(rgb)[y0:y1, x0:x1, :3]).astype(np.uint8)
    tr: dict = {}
    if enhance:
        lo, hi = C.stretch_range(a, np.ones(a.shape[:2], bool), ENHANCE_PCT) if a.size >= 30 else (0.0, 255.0)
        a = C.apply_stretch(a, lo, hi)
        tr["stretch"] = [round(lo, 1), round(hi, 1)]
    k = min(float(scale), IMAGE_MAX / max(a.shape[1], a.shape[0]))
    if abs(k - 1.0) > 1e-9:
        shape = (max(1, round(a.shape[0] * k)), max(1, round(a.shape[1] * k)))
        a = C.resample_box(a, (0, 0, a.shape[1], a.shape[0]), k, shape)
    im = Image.fromarray(np.asarray(a, np.uint8))
    return im, {"box": [x0, y0, x1, y1], "k": round(k, 4), "native_size": [W, H], **tr,
                "to_native": f"u = {x0} + (u_img + 0.5) / {k:.4f} - 0.5, v = {y0} + (v_img + 0.5) / {k:.4f} - 0.5"}
