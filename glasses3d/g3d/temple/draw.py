"""Chart and section images of a g3d.temple/2 build (plan/WP6_FACETS.md §3.4). Host only (PIL), never imported inside
Blender.

    chart_image(spec, build=None, *, footprints=None, d_range=None, width=1200) -> PIL.Image
        the (d, y) chart: cells filled (every facet one FACET tint, a warp facet's under a diagonal WARP_HATCH, faded
        facets blended toward the base, border-band ramps, chamfer and roll bands), every facet labelled with its
        name inside it, thickness contours every CONTOUR_MM, crease edges coloured per edge (ridge orange, valley
        blue, flat grey dashed, a soft border short-dashed), nodes labelled "name t=" (t = thickness from the inner
        face; "base" and solved nodes say so), decal and hinge footprints when given:
        footprints = {"decals": [{"name", "points": [[d, y], ...]}], "hinge": [...]} (drawn as dashed hulls), and a
        legend of what this chart draws, wrapped to the image width (v2's one row, spaced at 8 px a character, ran
        past the right edge at the lab's 1024 px), under a title that shrinks or wraps to fit. No lean shading: the
        facets' lean against the base has no visual meaning for Astra and left its text (WP6_V3 §6; review
        2026-10-12), and v2's shading drew a facet of lean 0 in nearly the base's colour.
        img.info["g3d_chart"] = {"frame": {lo_d, y_hi, ml, mt, sc}, "facets" / "nodes" / "dots": {name: box},
        "legend": [[label, box]]}: final-image pixel boxes [x0, y0, x1, y1] (px of (d, y) = ml + (d - lo_d) sc,
        mt + (y_hi - y) sc), for tests and tools; never written into a saved PNG.
    section_strip(builds, stations=(5, 15, 25, 35, 50, 65, 80), *, panel_w=170, panel_h=560) -> PIL.Image
        X-vs-y sections at the stations, one panel each, one isotropic scale for all panels (outward = right, up = up).
        builds = [(label, source)] or {label: source}, drawn by position: [0] the current revision (black), [1] the
        original (grey, filled), [2] the previous revision (teal, dashed), more in further colours. A source is a
        TempleBuild (its crease edges crossing the station are marked ridge orange / valley blue) or a chart mesh
        (V, F) / {"V", "F"} in local (d, y, X).

Per-edge crease shape is the core's rule, the one the crease tables aggregate (build.crease_edges / build.edge_folds):
a facet|base edge by its fold across the border against the BASE surface (the receipt's facet|base angle; never the
border band's ramp), any other edge between two outer polygons of different regions a ridge when the second polygon's
centroid lies below the first one's plane; "flat" below FLAT_CREASE_DEG or without a fold. A soft facet's border with
the base is drawn dashed in its shape's colour (legend "soft border" only on charts that have one).
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

from . import build as BD
from . import measure as MS
from .build import build_temple
from .spec import FLAT_CREASE_DEG, ParsedSpec, smoothstep

SS = 2                                  # supersampling factor (drawn at SS x, downsampled)
RIDGE = (232, 113, 10)
VALLEY = (26, 115, 232)
FLAT = (140, 140, 140)
OUTLINE = (20, 20, 20)
GRID = (222, 222, 222)
GRID_TEXT = (110, 110, 110)
BASE = (246, 245, 241)
RAMP = (239, 232, 210)
CHAMFER = (226, 222, 213)
ROLL = (232, 228, 220)
FACET = (200, 216, 228)                 # every facet cell, one tint (v2 shaded by lean: 0 deg was near the base's
#                                         colour, so a flat facet vanished; facets are told apart by creases and names)
FACET_TEXT = (14, 44, 70)               # facet names: dark slate, 14 px, on a white halo (legible over the hatch)
FACET_FONT_PX = 14
FACET_LABEL_MARGINS_PX = (6, 3, 1)      # a facet's name keeps this clear of its outline and of placed labels / dots,
#                                         the largest that fits (6 px: 0.9 mm on the lab's 1024 px chart of 136 mm)
NODE_LABEL_SPOTS = ((1, 0), (1, 1), (1, -1), (-1, 0), (-1, 1), (-1, -1), (1, 2), (1, -2), (-1, 2), (-1, -2),
                    (1, 3), (1, -3), (-1, 3), (-1, -3))   # (side: 1 right / -1 left of the dot, lines down)
CONTOUR_LABEL_TRIES = 200               # contour label spots tried per level, most central first
TITLE_LINE_PX = 16                      # a wrapped title's line step (11 px text)
LEGEND_TOP_PX = 30                      # below the plot: the x tick labels, then legend rows LEGEND_ROW_PX apart
LEGEND_ROW_PX = 24
LEGEND_BOTTOM_PX = 18
LEGEND_SWATCH_PX = 28                   # swatch, gap to its text, gap to the next item
LEGEND_TEXT_GAP_PX = 6
LEGEND_ITEM_GAP_PX = 22
CONTOUR_MM = 0.5
CONTOUR_EPS_MM = 1e-9                   # a thickness this close to a contour level is ON it (float noise)
CONTOUR = (150, 128, 170)
DECAL = (142, 36, 170)
HINGE = (46, 125, 50)
NODE = (200, 30, 30)
CONTOUR_TEXT = (120, 96, 140)
WARP_HATCH = (96, 64, 160)              # the diagonal hatch over a warp facet's cells (one smooth face, no plane)
WARP_HATCH_PX = 9                       # hatch spacing (px)
SOFT_DASH = (2 * SS, 3 * SS)            # a soft border's facet|base edges: short dashes (smooth-shaded)
STYLES = [{"line": (25, 25, 25), "width": 2.0, "fill": None, "dash": None},
          {"line": (120, 120, 120), "width": 1.5, "fill": (214, 214, 214), "dash": None},
          {"line": (0, 137, 123), "width": 2.0, "fill": None, "dash": (7, 5)},
          {"line": (173, 20, 87), "width": 2.0, "fill": None, "dash": (3, 4)},
          {"line": (121, 85, 72), "width": 2.0, "fill": None, "dash": (10, 4)}]


def _font(px: float):
    return ImageFont.load_default(size=max(8, int(round(px))))


def _dash(dr: ImageDraw.ImageDraw, pts, fill, width: int, dash=(8, 6)) -> None:
    on, off = dash
    for a, b in zip(pts[:-1], pts[1:]):
        a, b = np.asarray(a, float), np.asarray(b, float)
        L = float(np.hypot(*(b - a)))
        if L <= 0:
            continue
        u = (b - a) / L
        t = 0.0
        while t < L:
            e = min(t + on, L)
            dr.line([tuple(a + u * t), tuple(a + u * e)], fill=fill, width=width)
            t = e + off


def crease_edges(tb) -> list:
    """[(a, b, shape, deg)] of every edge between outer / roll polygons of different regions (labels before '/'),
    a < b vertex ids of tb.V; shape 'ridge' | 'valley' | 'flat' per edge by the core's rule (module docstring)."""
    return [(a, b, _edge_shape(deg, ridge), deg) for a, b, _pair, deg, ridge, _p1, _p2 in BD.crease_edges(tb)]


def _edge_shape(deg: float, ridge) -> str:
    return "flat" if ridge is None or deg < FLAT_CREASE_DEG else ("ridge" if ridge else "valley")


def _shape_colour(shape: str):
    return {"ridge": RIDGE, "valley": VALLEY}.get(shape, FLAT)


def _blend(c0, c1, s: float):
    return tuple(int(round(a + (b - a) * s)) for a, b in zip(c0, c1))


def _hull(P: np.ndarray) -> np.ndarray:
    P = np.unique(np.asarray(P, np.float64), axis=0)
    if len(P) < 3:
        return P

    def half(pts):
        h = []
        for p in pts:
            while len(h) >= 2 and ((h[-1][0] - h[-2][0]) * (p[1] - h[-2][1])
                                   - (h[-1][1] - h[-2][1]) * (p[0] - h[-2][0])) <= 0:
                h.pop()
            h.append(p)
        return h
    lo = half(P)
    hi = half(P[::-1])
    return np.asarray(lo[:-1] + hi[:-1])


def _contours(tb, levels) -> dict:
    """{level: [(p, q)]} iso-thickness segments in (d, y) over the outer cells (marching triangles on the
    arrangement's ear clipping; thickness is linear on each triangle). A corner within CONTOUR_EPS_MM of the level
    counts as on it and on the >= side, so a region lying at the level draws only its border with lower thickness,
    never the float noise inside it."""
    V2, th = tb.arr.V, tb.thickness
    out = {lv: [] for lv in levels}
    for f, tris in zip(tb.arr.faces, tb.arr.face_tris):
        for tri in tris:
            P = V2[list(tri)]
            for lv in levels:
                t = th[list(tri)]
                if t.min() > lv + CONTOUR_EPS_MM or t.max() < lv - CONTOUR_EPS_MM:
                    continue
                t = np.where(np.abs(t - lv) <= CONTOUR_EPS_MM, lv, t)
                up = t >= lv
                pts = []
                for i, j in ((0, 1), (1, 2), (2, 0)):
                    if up[i] != up[j]:
                        s = (lv - t[i]) / (t[j] - t[i])
                        pts.append(P[i] + s * (P[j] - P[i]))
                if len(pts) == 2 and np.hypot(*(pts[1] - pts[0])) > CONTOUR_EPS_MM:
                    out[lv].append((pts[0], pts[1]))
    return out


# --------------------------------------------------------------------------------------------- chart
def chart_image(spec, build=None, *, footprints: dict | None = None, d_range=None, width: int = 1200,
                title: str | None = None) -> Image.Image:
    """The (d, y) chart of a build (module docstring). `spec` is the spec dict (or ParsedSpec) the build came from;
    `build` defaults to build_temple(spec). Drawn in this order: grid, cells, warp hatch, contours, creases, outline,
    footprints, node dots, node labels, facet names, contour labels, title, legend; each label is placed clear of
    every label and dot placed before it where a spot is free (node labels: _label; facet names: _facet_labels;
    contour labels: the most central free spot of their level)."""
    tb = build_temple(spec) if build is None else build
    ps: ParsedSpec = tb.spec
    lo_d, hi_d = (ps.d0, ps.d1) if d_range is None else (float(d_range[0]), float(d_range[1]))
    ds = np.linspace(lo_d, hi_d, 200)
    y_lo, y_hi = float(np.min(ps.y_bot(ds))) - 2.0, float(np.max(ps.y_top(ds))) + 2.0
    edges = [(a, b, pair, _edge_shape(deg, ridge)) for a, b, pair, deg, ridge, _p1, _p2 in BD.crease_edges(tb)]
    soft_edge = [shape != "flat" and pair[1] == "base" and bool(ps.facets[pair[0]].get("soft"))
                 for _a, _b, pair, shape in edges]
    fps = {k: [(fp, _hull(fp["points"])) for fp in (footprints or {}).get(k, [])] for k in ("decals", "hinge")}
    fps = {k: [(fp, Hh) for fp, Hh in v if len(Hh) >= 2] for k, v in fps.items()}
    f_small, f_mid, f_facet = _font(10 * SS), _font(12 * SS), _font(FACET_FONT_PX * SS)
    th = tb.thickness
    head = title or (f"chart  d mm back from the hinge (right), y mm up  |  spec {ps.sha256[:8]}  |  thickness "
                     f"{th.min():.2f}-{th.max():.2f} mm (contours every {CONTOUR_MM:g} mm)")
    ml, mr = 56, 24
    f_head, heads = _title_lines(head, (width - ml - 12) * SS)
    mt = 44 + TITLE_LINE_PX * (len(heads) - 1)
    legend = _legend_layout(_legend_items(warp="warp" in tb.face_cell, soft=any(soft_edge),
                                          decals=bool(fps["decals"]), hinge=bool(fps["hinge"])),
                            f_mid, ml * SS, (width - 12) * SS)
    rows = len({it[4] for it in legend})
    mb = LEGEND_TOP_PX + LEGEND_ROW_PX * (rows - 1) + LEGEND_BOTTOM_PX
    sc = (width - ml - mr) / (hi_d - lo_d)
    height = int(round(mt + mb + sc * (y_hi - y_lo)))
    W, H = width * SS, height * SS
    img = Image.new("RGB", (W, H), (255, 255, 255))
    dr = ImageDraw.Draw(img)
    placed: list = []                                # label and dot boxes (supersampled px), in placing order
    for i, line in enumerate(heads):                 # the title (drawn last) is no free spot for a label
        placed.append(_box(dr, (ml * SS, (14 + TITLE_LINE_PX * i) * SS), line, f_head, "lt", 0))

    def px(d, y):
        return ((ml + (d - lo_d) * sc) * SS, (mt + (y_hi - y) * sc) * SS)

    for d in np.arange(math.ceil(lo_d / 10) * 10, hi_d + 1e-9, 10.0):
        dr.line([px(d, y_lo), px(d, y_hi)], fill=GRID, width=SS)
        x, y = px(d, y_lo)
        _text(dr, (x, y + 3 * SS), f"{d:g}", f_small, GRID_TEXT, "mt", placed, halo=False)
    for yv in np.arange(math.ceil(y_lo / 5) * 5, y_hi + 1e-9, 5.0):
        dr.line([px(lo_d, yv), px(hi_d, yv)], fill=GRID, width=SS)
        x, y = px(lo_d, yv)
        _text(dr, (x - 4 * SS, y), f"{yv:g}", f_small, GRID_TEXT, "rm", placed, halo=False)
    V2 = tb.arr.V
    warp_mask = None
    for fi, face in enumerate(tb.arr.faces):
        lab, cell = tb.face_label[fi], tb.face_cell[fi]
        if cell == "warp":
            col = FACET
            if warp_mask is None:
                warp_mask = Image.new("L", (W, H), 0)
            ImageDraw.Draw(warp_mask).polygon([px(*V2[v]) for v in face], fill=255)
        elif cell == "facet":
            col = FACET
        elif cell == "faded":
            fade = ps.facets[lab]["fade"]
            c = V2[face].mean(axis=0)
            s = float(smoothstep((c[0] - fade["d0"]) / (fade["d1"] - fade["d0"])))
            col = _blend(FACET, BASE, s)
        elif cell == "ramp":
            col = RAMP
        else:
            col = BASE
        if tb.face_chamfer[fi] is not None:
            col = _blend(col, CHAMFER, 0.6)
        elif tb.face_roll[fi]:
            col = _blend(col, ROLL, 0.5)
        dr.polygon([px(*V2[v]) for v in face], fill=col)
    if warp_mask is not None:                        # the warp hatch: diagonal lines, only over warp cells
        lines = Image.new("L", (W, H), 0)
        ld = ImageDraw.Draw(lines)
        step = WARP_HATCH_PX * SS
        for x0 in range(-H, W, step):
            ld.line([(x0, H), (x0 + H, 0)], fill=255, width=2 * SS)
        img.paste(WARP_HATCH, (0, 0, W, H), ImageChops.multiply(lines, warp_mask))
    levels = [round(v, 6) for v in np.arange(math.ceil(th.min() / CONTOUR_MM) * CONTOUR_MM, th.max(), CONTOUR_MM)]
    contours = _contours(tb, levels)
    for segs in contours.values():
        for p, q in segs:
            dr.line([px(*p), px(*q)], fill=CONTOUR, width=SS)
    for (a, b, pair, shape), soft in zip(edges, soft_edge):
        pa, pb = px(*tb.V[a, :2]), px(*tb.V[b, :2])
        if shape == "flat":
            _dash(dr, [pa, pb], FLAT, 2 * SS, (5 * SS, 4 * SS))
        elif soft:                                   # smooth-shaded: a dashed line of its shape
            _dash(dr, [pa, pb], _shape_colour(shape), 3 * SS, SOFT_DASH)
        else:
            dr.line([pa, pb], fill=_shape_colour(shape), width=3 * SS)
    loop = tb.arr.boundary_loop
    dr.line([px(*V2[v]) for v in loop + loop[:1]], fill=OUTLINE, width=2 * SS)
    for kind, col in (("decals", DECAL), ("hinge", HINGE)):
        for fp, Hh in fps[kind]:
            pts = [px(*p) for p in Hh] + [px(*Hh[0])]
            _dash(dr, pts, col, 2 * SS, (6 * SS, 4 * SS))
            if kind == "decals":
                _text(dr, px(*Hh.mean(axis=0)), fp.get("name", kind)[:28], f_small, col, "mm", placed)
        if kind == "hinge" and fps[kind]:            # small parts: one label beside them all
            P = np.vstack([np.asarray(fp["points"], np.float64) for fp, _ in fps[kind]])
            x, y = px(float(P[:, 0].max()), float(P[:, 1].max()))
            _text(dr, (x + 4 * SS, y), "hinge", f_small, col, "lb", placed)
    nodes = tb.tables["nodes"]
    order = sorted(nodes, key=lambda nm: (not (lo_d - 1 <= nodes[nm]["d"] <= hi_d + 1
                                               and y_lo <= nodes[nm]["y"] <= y_hi), nm))
    dots, node_boxes = {}, {}
    r = 4 * SS
    for name in order:                               # every dot first: no label may cover another node's dot
        n = nodes[name]
        inside = lo_d - 1 <= n["d"] <= hi_d + 1 and y_lo <= n["y"] <= y_hi
        x, yy = px(min(max(n["d"], lo_d), hi_d), min(max(n["y"], y_lo), y_hi))
        if inside:
            dr.ellipse([x - r, yy - r, x + r, yy + r], fill=NODE)
        else:
            dr.ellipse([x - r, yy - r, x + r, yy + r], outline=NODE, width=SS)
        dots[name] = (x, yy, inside)
        placed.append((x - r - SS, yy - r - SS, x + r + SS, yy + r + SS))
    for name in order:                               # nodes on the chart first: their labels get the free spots
        n = nodes[name]
        x, yy, inside = dots[name]
        g = n["given"]
        t = "?" if n["t"] is None else f"{n['t']:.2f}"
        tag = " base" if g == "base" else (" solved" if g is None else "")
        text = f"{name} t={t}{tag}{'' if inside else ' (off chart)'}"
        node_boxes[name] = _label(dr, (x, yy), text, f_mid if inside else f_small,
                                  (30, 30, 30) if inside else GRID_TEXT, placed, (W, H))
    plot = (ml * SS, mt * SS, (width - mr) * SS, (height - mb) * SS)
    facet_boxes = _facet_labels(dr, tb, px, placed, plot, f_facet, (W, H))
    for lv, segs in contours.items():                # contour labels last: the most central spot left free; none free
        if not segs:                                 # (a short loop in a crowd of nodes): the line stands unlabelled
            continue                                 # (its neighbours' labels and the title's step give its level)
        mids = np.array([0.5 * (p + q) for p, q in segs])
        tries = np.argsort(np.hypot(*(mids - mids.mean(axis=0)).T), kind="stable")[:CONTOUR_LABEL_TRIES]
        text = f"{lv:g}"
        spot = next((k for k in tries if _free(_box(dr, px(*mids[k]), text, f_small, "mm"), placed, plot)), None)
        if spot is not None:
            _text(dr, px(*mids[spot]), text, f_small, CONTOUR_TEXT, "mm", placed)
    for i, line in enumerate(heads):
        dr.text((ml * SS, (14 + TITLE_LINE_PX * i) * SS), line, fill=(20, 20, 20), font=f_head, anchor="lt")
    y0 = (height - mb + LEGEND_TOP_PX) * SS
    _chart_legend(dr, [(lab, col, kind, x, y + y0, x1) for lab, col, kind, x, y, x1 in legend], f_mid)
    out = img.resize((width, height), Image.LANCZOS)
    fin = lambda b: [round(float(v) / SS, 2) for v in b]       # noqa: E731
    out.info["g3d_chart"] = {"frame": {"lo_d": lo_d, "y_hi": y_hi, "ml": ml, "mt": mt, "sc": sc},
                             "facets": {k: fin(b) for k, b in facet_boxes.items()},
                             "nodes": {k: fin(b) for k, b in node_boxes.items()},
                             "dots": {k: fin((x - r, yy - r, x + r, yy + r)) for k, (x, yy, _) in dots.items()},
                             "legend": [[lab, fin((x, y + y0 - 7 * SS, x1, y + y0 + 7 * SS))]
                                        for lab, _c, _k, x, y, x1 in legend]}
    return out


def _title_lines(text: str, max_w: float):
    """(font, lines) of a chart title: one line at 13 px, else at 12 or 11 px when that fits max_w (supersampled px),
    else wrapped at spaces into lines of 11 px (TITLE_LINE_PX apart; the plot moves down by the extra lines). v2 drew
    one 13 px line at any width: its default title ran past the right edge of a 700 px chart."""
    meas = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for p in (13, 12, 11):
        font = _font(p * SS)
        if meas.textlength(text, font=font) <= max_w:
            return font, [text]
    lines: list = []
    for word in text.split():
        if lines and meas.textlength(lines[-1] + " " + word, font=font) <= max_w:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return font, lines


def _box(dr, xy, text: str, font, anchor: str, halo: int = SS):
    return dr.textbbox(xy, text, font=font, anchor=anchor, stroke_width=halo)


def _overlaps(b, q) -> bool:
    return b[0] < q[2] and q[0] < b[2] and b[1] < q[3] and q[1] < b[3]


def _free(b, placed: list, area) -> bool:
    """Box b lies inside `area` (x0, y0, x1, y1) and overlaps no placed box."""
    return (area[0] <= b[0] and area[1] <= b[1] and b[2] <= area[2] and b[3] <= area[3]
            and not any(_overlaps(b, q) for q in placed))


def _text(dr, xy, text: str, font, fill, anchor: str, placed: list, halo: bool = True):
    """Text at xy (on a white halo unless halo=False), its box added to placed."""
    w = SS if halo else 0
    placed.append(_box(dr, xy, text, font, anchor, w))
    dr.text(xy, text, fill=fill, font=font, anchor=anchor, stroke_width=w, stroke_fill=(255, 255, 255))


def _label(dr, dot, text: str, font, fill, placed: list, size):
    """A node's label beside its dot at `dot`: anchored left-bottom 6 px right of it (side 1) or right-bottom 6 px
    left of it (side -1), moved down / up by whole lines: the first NODE_LABEL_SPOTS spot inside the image that
    overlaps no box placed before (labels, and every node's dot: v3 session2's L0 label ran over L's dot 1.4 mm
    away); none free: right of the dot on its line. The box is added to placed and returned."""
    x, y = dot
    spots = []
    for side, k in NODE_LABEL_SPOTS:
        xy = (x + 6 * SS, y - 2 * SS) if side > 0 else (x - 6 * SS, y - 2 * SS)
        anchor = "lb" if side > 0 else "rb"
        b = _box(dr, xy, text, font, anchor)
        h = b[3] - b[1] + 2 * SS
        spots.append(((xy[0], xy[1] + k * h), anchor, (b[0], b[1] + k * h, b[2], b[3] + k * h)))
    xy, anchor, b = next((s for s in spots if _free(s[2], placed, (0, 0, size[0], size[1]))), spots[0])
    placed.append(b)
    dr.text(xy, text, fill=fill, font=font, anchor=anchor, stroke_width=SS, stroke_fill=(255, 255, 255))
    return b


def _sat(bad: np.ndarray) -> np.ndarray:
    """The summed-area table of a boolean pixel mask (one row and column of zeros in front)."""
    S = np.zeros((bad.shape[0] + 1, bad.shape[1] + 1), np.int64)
    S[1:, 1:] = bad.astype(np.int64).cumsum(0).cumsum(1)
    return S


def _fit_box(S: np.ndarray, w: float, h: float, target, centre_in: np.ndarray | None = None):
    """The centre (x, y) of the w x h pixel box covering no bad pixel of the summed-area table S (_sat; and, with
    centre_in, whose centre pixel is in centre_in) nearest `target`; None when none fits. Every position at once."""
    H, W = S.shape[0] - 1, S.shape[1] - 1
    w, h = int(math.ceil(w)), int(math.ceil(h))
    if w > W or h > H or w < 1 or h < 1:
        return None
    tot = S[h:, w:] - S[:H + 1 - h, w:] - S[h:, :W + 1 - w] + S[:H + 1 - h, :W + 1 - w]
    ok = tot == 0
    if centre_in is not None:
        ok &= centre_in[h // 2:H - h + 1 + h // 2, w // 2:W - w + 1 + w // 2]
    yy, xx = np.nonzero(ok)
    if not len(yy):
        return None
    cx, cy = xx + w / 2.0, yy + h / 2.0
    k = int(np.argmin((cx - target[0]) ** 2 + (cy - target[1]) ** 2))
    return float(cx[k]), float(cy[k])


def _facet_labels(dr, tb, px, placed: list, plot, font, size) -> dict:
    """Every facet with a cell on the chart gets its name once, inside its cells (WP6_V3 §6 "facets labelled"; v3
    session2: the chart named no facet, so Astra had to match the receipt's names to shapes by the nodes). Worked at
    final-image pixels (supersampled / SS): a facet's cells rasterised and clipped to the plot; the name's box is
    placed nearest the cells' centroid, with FACET_LABEL_MARGINS_PX (largest first) clear of the cells' outline
    (creases) and of every placed label and dot; a facet too small for that: the box's centre on a cell pixel and
    clear of placed boxes (the name runs over the outline); else at the cell pixel nearest the centroid. Smallest
    facets first, so the scarce spots go to them. {facet: box} (supersampled px), each box added to placed."""
    W1, H1 = size[0] // SS, size[1] // SS
    x0, y0, x1, y1 = (int(round(v / SS)) for v in plot)
    by_label: dict = {}
    for fi, lab in enumerate(tb.face_label):
        if lab != "base":
            by_label.setdefault(lab, []).append(fi)
    masks = {}
    for lab, fis in by_label.items():
        m = Image.new("L", (W1, H1), 0)
        md = ImageDraw.Draw(m)
        for fi in fis:
            md.polygon([tuple(c / SS for c in px(*tb.arr.V[v])) for v in tb.arr.faces[fi]], fill=255)
        a = np.zeros((H1, W1), bool)
        a[y0:y1, x0:x1] = np.asarray(m)[y0:y1, x0:x1] > 0
        if a.any():
            masks[lab] = a
    out = {}
    for lab in sorted(masks, key=lambda k: (int(masks[k].sum()), k)):
        mask = masks[lab]
        b = _box(dr, (0, 0), lab, font, "mm", 2 * SS)
        tw, th = (b[2] - b[0]) / SS, (b[3] - b[1]) / SS
        occ = np.zeros((H1, W1), bool)
        for q in placed:
            occ[max(0, int(q[1] // SS)):max(0, int(math.ceil(q[3] / SS))),
                max(0, int(q[0] // SS)):max(0, int(math.ceil(q[2] / SS)))] = True
        ys, xs = np.nonzero(mask)
        target = (float(xs.mean()) + 0.5, float(ys.mean()) + 0.5)
        spot, S = None, _sat(~mask | occ)
        for mg in FACET_LABEL_MARGINS_PX:
            spot = _fit_box(S, tw + 2 * mg, th + 2 * mg, target)
            if spot is not None:
                break
        if spot is None:
            spot = _fit_box(_sat(occ), tw, th, target, centre_in=mask)
        if spot is None:
            k = int(np.argmin((xs + 0.5 - target[0]) ** 2 + (ys + 0.5 - target[1]) ** 2))
            spot = (float(xs[k]) + 0.5, float(ys[k]) + 0.5)
        xy = (spot[0] * SS - 0.5 * (b[0] + b[2]), spot[1] * SS - 0.5 * (b[1] + b[3]))   # the box centred on spot
        box = _box(dr, xy, lab, font, "mm", 2 * SS)
        placed.append(box)
        dr.text(xy, lab, fill=FACET_TEXT, font=font, anchor="mm", stroke_width=2 * SS, stroke_fill=(255, 255, 255))
        out[lab] = box
    return out


def _legend_items(*, warp: bool, soft: bool, decals: bool, hinge: bool) -> list:
    """[(label, colour, swatch kind)] of what the chart draws: warp, soft border, decal and hinge only on a chart
    that has them (a legend line for an absent mark is a fact about nothing)."""
    items = [("ridge", RIDGE, "line"), ("valley", VALLEY, "line"), ("flat < 2°", FLAT, "dash")]
    if soft:
        items.append(("soft border", RIDGE, "soft"))
    items.append(("facet", FACET, "box"))
    if warp:
        items.append(("warp", WARP_HATCH, "hatch"))
    items += [("base", BASE, "box"), ("border ramp", RAMP, "box"), ("thickness contour", CONTOUR, "line")]
    if decals:
        items.append(("decal", DECAL, "dash"))
    if hinge:
        items.append(("hinge part", HINGE, "dash"))
    return items + [("node", NODE, "dot")]


def _legend_layout(items: list, font, x0: float, x1: float) -> list:
    """[(label, colour, kind, x, y, x_end)]: items left to right from x0, by their measured text width, wrapped to a
    new row LEGEND_ROW_PX lower before an item would pass x1 (v2 spaced items at 8 px a character, one row, and lost
    the last items past the right edge at the lab's 1024 px: v3 session2's "decal" and "hinge part"); y from 0."""
    meas = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    out, x, y = [], x0, 0.0
    for label, col, kind in items:
        w = (LEGEND_SWATCH_PX + LEGEND_TEXT_GAP_PX) * SS + meas.textlength(label, font=font)
        if x > x0 and x + w > x1:
            x, y = x0, y + LEGEND_ROW_PX * SS
        out.append((label, col, kind, x, y, x + w))
        x += w + LEGEND_ITEM_GAP_PX * SS
    return out


def _chart_legend(dr, layout: list, font) -> None:
    for label, col, kind, x, y0, _x1 in layout:
        sw = LEGEND_SWATCH_PX * SS
        if kind == "line":
            dr.line([(x, y0), (x + sw, y0)], fill=col, width=3 * SS)
        elif kind == "dash":
            _dash(dr, [(x, y0), (x + sw, y0)], col, 2 * SS, (5 * SS, 4 * SS))
        elif kind == "soft":                         # dashed in its shape's colour: half ridge, half valley
            _dash(dr, [(x, y0), (x + sw / 2, y0)], RIDGE, 3 * SS, SOFT_DASH)
            _dash(dr, [(x + sw / 2, y0), (x + sw, y0)], VALLEY, 3 * SS, SOFT_DASH)
        elif kind == "hatch":
            dr.rectangle([x, y0 - 7 * SS, x + sw, y0 + 7 * SS], fill=FACET, outline=(160, 160, 160))
            for k in (7, 14, 21):
                dr.line([(x + (k - 7) * SS, y0 + 7 * SS), (x + (k + 7) * SS, y0 - 7 * SS)], fill=col, width=SS)
        elif kind == "box":
            dr.rectangle([x, y0 - 7 * SS, x + sw, y0 + 7 * SS], fill=col, outline=(160, 160, 160))
        else:
            dr.ellipse([x + 10 * SS, y0 - 4 * SS, x + 18 * SS, y0 + 4 * SS], fill=col)
        dr.text((x + sw + LEGEND_TEXT_GAP_PX * SS, y0), label, fill=(40, 40, 40), font=font, anchor="lm")


# --------------------------------------------------------------------------------------------- sections
def _mesh_of(src):
    if hasattr(src, "V") and hasattr(src, "tris"):
        return np.asarray(src.V, np.float64), np.asarray(src.tris, np.int64)
    if isinstance(src, dict):
        return np.asarray(src["V"], np.float64), np.asarray(src["F"], np.int64)
    V, F = src
    return np.asarray(V, np.float64), np.asarray(F, np.int64)


def chain_segments(seg: np.ndarray, tol: float = 1e-6) -> list[np.ndarray]:
    """Section segments (k, 2, 2) joined into polylines by shared endpoints (closed loops repeat their first point).
    Zero-length and repeated segments (a plane through mesh vertices) are dropped first."""
    key = lambda p: (round(float(p[0]) / tol), round(float(p[1]) / tol))   # noqa: E731
    seen, keep = set(), []
    for p, q in np.asarray(seg, np.float64).reshape(-1, 2, 2):
        kp, kq = key(p), key(q)
        if kp == kq or (kp, kq) in seen or (kq, kp) in seen:
            continue
        seen.add((kp, kq))
        keep.append((p, q))
    seg = np.asarray(keep).reshape(-1, 2, 2)
    if len(seg) == 0:
        return []
    ends: dict = {}
    for i, (p, q) in enumerate(seg):
        ends.setdefault(key(p), []).append((i, 0))
        ends.setdefault(key(q), []).append((i, 1))
    used = np.zeros(len(seg), bool)
    out = []
    for s0 in range(len(seg)):
        if used[s0]:
            continue
        used[s0] = True
        line = [seg[s0, 0], seg[s0, 1]]
        for direction in (1, 0):
            while True:
                tip = line[-1] if direction else line[0]
                nxt = None
                for j, e in ends.get(key(tip), []):
                    if not used[j]:
                        nxt = (j, e)
                        break
                if nxt is None:
                    break
                j, e = nxt
                used[j] = True
                other = seg[j, 1 - e]
                if direction:
                    line.append(other)
                else:
                    line.insert(0, other)
        out.append(np.asarray(line))
    return out


def section_crease_points(tb, d: float) -> list:
    """[(y, X, shape)] where the build's crease edges cross the plane d."""
    out = []
    for a, b, shape, _ in crease_edges(tb):
        A, B = tb.V[a], tb.V[b]
        if (A[0] - d) * (B[0] - d) > 0 or A[0] == B[0]:
            continue
        t = (d - A[0]) / (B[0] - A[0])
        P = A + t * (B - A)
        out.append((float(P[1]), float(P[2]), shape))
    return out


def section_strip(builds, stations=(5, 15, 25, 35, 50, 65, 80), *, panel_w: int = 170, panel_h: int = 560,
                  title: str | None = None) -> Image.Image:
    """Section panels of one or more builds / meshes overlaid (module docstring)."""
    items = list(builds.items()) if isinstance(builds, dict) else [tuple(b) for b in builds]
    meshes = [_mesh_of(src) for _, src in items]
    secs = [[chain_segments(MS.slice_d(V, F, float(d))) for d in stations] for V, F in meshes]
    ymin = np.full(len(stations), np.inf)
    ymax = np.full(len(stations), -np.inf)
    xmin = np.full(len(stations), np.inf)
    xmax = np.full(len(stations), -np.inf)
    for per in secs:
        for k, lines in enumerate(per):
            for P in lines:
                ymin[k], ymax[k] = min(ymin[k], P[:, 0].min()), max(ymax[k], P[:, 0].max())
                xmin[k], xmax[k] = min(xmin[k], P[:, 1].min()), max(xmax[k], P[:, 1].max())
    ok = np.isfinite(ymin)
    mt, mb = 64, 92
    hmax = float(np.max((ymax - ymin)[ok])) if ok.any() else 1.0
    wmax = float(np.max((xmax - xmin)[ok])) if ok.any() else 1.0
    sc = min((panel_h - 30) / hmax, (panel_w - 40) / wmax)
    ylo = float(np.min(ymin[ok])) if ok.any() else 0.0
    yhi = float(np.max(ymax[ok])) if ok.any() else 1.0
    body = int(math.ceil(sc * (yhi - ylo))) + 30
    width = panel_w * len(stations)
    height = mt + body + mb
    W, H = width * SS, height * SS
    img = Image.new("RGB", (W, H), (255, 255, 255))
    dr = ImageDraw.Draw(img)
    f_small, f_mid = _font(10 * SS), _font(12 * SS)
    for k, d in enumerate(stations):
        x0 = k * panel_w
        cx = 0.5 * (xmin[k] + xmax[k]) if ok[k] else 0.0

        def px(y, X, x0=x0, cx=cx):
            return ((x0 + panel_w / 2 + (X - cx) * sc) * SS, (mt + 15 + (yhi - y) * sc) * SS)
        if k:
            dr.line([(x0 * SS, mt * SS), (x0 * SS, (height - mb) * SS)], fill=GRID, width=SS)
        for yv in np.arange(math.ceil(ylo / 5) * 5, yhi + 1e-9, 5.0):
            a = px(yv, cx)
            dr.line([(x0 * SS + 2 * SS, a[1]), ((x0 + 10) * SS, a[1])], fill=GRID_TEXT, width=SS)
        dr.text(((x0 + panel_w / 2) * SS, 40 * SS), f"d {d:g}", fill=(20, 20, 20), font=f_mid, anchor="mm")
        order = sorted(range(len(items)), key=lambda i: (STYLES[min(i, len(STYLES) - 1)]["fill"] is None, -i))
        for i in order:
            st = STYLES[min(i, len(STYLES) - 1)]
            for P in secs[i][k]:
                pts = [px(y, X) for y, X in P]
                if len(pts) < 2:
                    continue
                if st["fill"] is not None and len(pts) > 2:
                    dr.polygon(pts, fill=st["fill"])
                if st["dash"]:
                    _dash(dr, pts, st["line"], int(st["width"] * SS), tuple(v * SS for v in st["dash"]))
                else:
                    dr.line(pts, fill=st["line"], width=int(st["width"] * SS), joint="curve")
        for i, (_, src) in enumerate(items):
            if not hasattr(src, "polys"):
                continue
            r = (4 if i == 0 else 3) * SS
            for y, X, shape in section_crease_points(src, float(d)):
                x, yy = px(y, X)
                col = _shape_colour(shape)
                if i == 0:
                    dr.ellipse([x - r, yy - r, x + r, yy + r], fill=col, outline=(255, 255, 255))
                else:
                    dr.ellipse([x - r, yy - r, x + r, yy + r], outline=col, width=SS)
        if k == 0:                                   # y labels over the sections, readable on a fill
            for yv in np.arange(math.ceil(ylo / 5) * 5, yhi + 1e-9, 5.0):
                dr.text(((x0 + 12) * SS, px(yv, cx)[1]), f"y {yv:g}", fill=GRID_TEXT, font=f_small, anchor="lm",
                        stroke_width=SS, stroke_fill=(255, 255, 255))
        if secs[0][k]:
            P = np.vstack(secs[0][k])
            dr.text(((x0 + panel_w / 2) * SS, 54 * SS), f"X {P[:, 1].min():.2f}-{P[:, 1].max():.2f}",
                    fill=GRID_TEXT, font=f_small, anchor="mm")
    yb = (height - mb + 22) * SS
    x = 12 * SS
    for i, (label, _) in enumerate(items):
        st = STYLES[min(i, len(STYLES) - 1)]
        if st["fill"] is not None:
            dr.rectangle([x, yb - 7 * SS, x + 28 * SS, yb + 7 * SS], fill=st["fill"], outline=st["line"])
        elif st["dash"]:
            _dash(dr, [(x, yb), (x + 28 * SS, yb)], st["line"], int(st["width"] * SS),
                  tuple(v * SS for v in st["dash"]))
        else:
            dr.line([(x, yb), (x + 28 * SS, yb)], fill=st["line"], width=int(st["width"] * SS))
        dr.text((x + 34 * SS, yb), str(label), fill=(40, 40, 40), font=f_mid, anchor="lm")
        x += (34 + 8 * len(str(label)) + 26) * SS
    for label, col in (("ridge", RIDGE), ("valley", VALLEY)):
        dr.ellipse([x, yb - 4 * SS, x + 8 * SS, yb + 4 * SS], fill=col)
        dr.text((x + 14 * SS, yb), label, fill=(40, 40, 40), font=f_mid, anchor="lm")
        x += 70 * SS
    bar = sc * 1.0
    y2 = yb + 26 * SS
    dr.line([(12 * SS, y2), (12 * SS + bar * SS, y2)], fill=(20, 20, 20), width=2 * SS)
    dr.text((12 * SS + bar * SS + 8 * SS, y2), f"1 mm ({sc:.1f} px/mm, same scale in every panel; outward = right)",
            fill=(40, 40, 40), font=f_small, anchor="lm")
    head = title or "sections X (outward, right) vs y (up) at d mm back from the hinge"
    dr.text((12 * SS, 14 * SS), head, fill=(20, 20, 20), font=_font(13 * SS), anchor="lt")
    return img.resize((width, height), Image.LANCZOS)
