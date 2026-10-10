"""Chart and section images of a g3d.temple/2 build (plan/WP6_FACETS.md §3.4). Host only (PIL), never imported inside
Blender.

    chart_image(spec, build=None, *, footprints=None, d_range=None, width=1200) -> PIL.Image
        the (d, y) chart: cells filled (facets shaded by their lean against the local base normal, faded facets blended
        toward the base, border-band ramps, chamfer and roll bands), thickness contours every CONTOUR_MM, crease edges
        coloured per edge (ridge orange, valley blue, flat grey dashed), nodes labelled "name t=" (t = thickness from
        the inner face; "base" and solved nodes say so), decal and hinge footprints when given:
        footprints = {"decals": [{"name", "points": [[d, y], ...]}], "hinge": [...]} (drawn as dashed hulls).
    section_strip(builds, stations=(5, 15, 25, 35, 50, 65, 80), *, panel_w=170, panel_h=560) -> PIL.Image
        X-vs-y sections at the stations, one panel each, one isotropic scale for all panels (outward = right, up = up).
        builds = [(label, source)] or {label: source}, drawn by position: [0] the current revision (black), [1] the
        original (grey, filled), [2] the previous revision (teal, dashed), more in further colours. A source is a
        TempleBuild (its crease edges crossing the station are marked ridge orange / valley blue) or a chart mesh
        (V, F) / {"V", "F"} in local (d, y, X).

Per-edge crease shape is the core's rule (build._tables): the edge between two outer polygons of different regions is a
ridge when the second polygon's centroid lies below the first one's plane, "flat" below FLAT_CREASE_DEG.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import measure as MS
from .build import OUTER_KINDS, build_temple, mesh_topology
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
LEAN_LO = np.array([226.0, 236.0, 240.0])    # lean 0 deg
LEAN_HI = np.array([70.0, 104.0, 118.0])     # lean >= LEAN_MAX_DEG
LEAN_MAX_DEG = 30.0
CONTOUR_MM = 0.5
CONTOUR = (150, 128, 170)
DECAL = (142, 36, 170)
HINGE = (46, 125, 50)
NODE = (200, 30, 30)
CONTOUR_TEXT = (120, 96, 140)
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
    """[(a, b, shape, dihedral deg)] of every edge between outer / roll polygons of different regions (labels before
    '/'), a < b vertex ids of tb.V; shape 'ridge' | 'valley' | 'flat' per edge (module docstring)."""
    topo = mesh_topology(tb.polys)
    out = []
    for (a, b), p1 in topo["directed"].items():
        if a > b:
            continue
        p2 = topo["directed"].get((b, a))
        if p2 is None or tb.poly_kind[p1] not in OUTER_KINDS or tb.poly_kind[p2] not in OUTER_KINDS:
            continue
        l1, l2 = tb.poly_region[p1].split("/")[0], tb.poly_region[p2].split("/")[0]
        if l1 == l2:
            continue
        n1, n2 = tb.normals[p1], tb.normals[p2]
        dih = math.degrees(math.acos(max(-1.0, min(1.0, float(n1 @ n2)))))
        cen2 = tb.V[tb.polys[p2]].mean(axis=0)
        s = float(n1 @ (cen2 - 0.5 * (tb.V[a] + tb.V[b])))
        shape = "flat" if dih < FLAT_CREASE_DEG else ("ridge" if s < 0 else "valley")
        out.append((a, b, shape, dih))
    return out


def _shape_colour(shape: str):
    return {"ridge": RIDGE, "valley": VALLEY}.get(shape, FLAT)


def _blend(c0, c1, s: float):
    return tuple(int(round(a + (b - a) * s)) for a, b in zip(c0, c1))


def _lean_colour(lean: float):
    s = min(max(lean / LEAN_MAX_DEG, 0.0), 1.0)
    return tuple(int(round(v)) for v in LEAN_LO + (LEAN_HI - LEAN_LO) * s)


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
    arrangement's ear clipping; thickness is linear on each triangle)."""
    V2, th = tb.arr.V, tb.thickness
    out = {lv: [] for lv in levels}
    for f, tris in zip(tb.arr.faces, tb.arr.face_tris):
        for tri in tris:
            t = th[list(tri)]
            P = V2[list(tri)]
            for lv in levels:
                if t.min() > lv or t.max() < lv or t.min() == t.max():
                    continue
                pts = []
                for i, j in ((0, 1), (1, 2), (2, 0)):
                    if (t[i] - lv) * (t[j] - lv) < 0:
                        s = (lv - t[i]) / (t[j] - t[i])
                        pts.append(P[i] + s * (P[j] - P[i]))
                if len(pts) == 2:
                    out[lv].append((pts[0], pts[1]))
    return out


# --------------------------------------------------------------------------------------------- chart
def chart_image(spec, build=None, *, footprints: dict | None = None, d_range=None, width: int = 1200,
                title: str | None = None) -> Image.Image:
    """The (d, y) chart of a build (module docstring). `spec` is the spec dict (or ParsedSpec) the build came from;
    `build` defaults to build_temple(spec)."""
    tb = build_temple(spec) if build is None else build
    ps: ParsedSpec = tb.spec
    lo_d, hi_d = (ps.d0, ps.d1) if d_range is None else (float(d_range[0]), float(d_range[1]))
    ds = np.linspace(lo_d, hi_d, 200)
    y_lo, y_hi = float(np.min(ps.y_bot(ds))) - 2.0, float(np.max(ps.y_top(ds))) + 2.0
    ml, mr, mt, mb = 56, 24, 44, 96
    sc = (width - ml - mr) / (hi_d - lo_d)
    height = int(round(mt + mb + sc * (y_hi - y_lo)))
    W, H = width * SS, height * SS
    img = Image.new("RGB", (W, H), (255, 255, 255))
    dr = ImageDraw.Draw(img)
    f_small, f_mid = _font(10 * SS), _font(12 * SS)

    def px(d, y):
        return ((ml + (d - lo_d) * sc) * SS, (mt + (y_hi - y) * sc) * SS)

    for d in np.arange(math.ceil(lo_d / 10) * 10, hi_d + 1e-9, 10.0):
        dr.line([px(d, y_lo), px(d, y_hi)], fill=GRID, width=SS)
        x, y = px(d, y_lo)
        dr.text((x, y + 3 * SS), f"{d:g}", fill=GRID_TEXT, font=f_small, anchor="mt")
    for yv in np.arange(math.ceil(y_lo / 5) * 5, y_hi + 1e-9, 5.0):
        dr.line([px(lo_d, yv), px(hi_d, yv)], fill=GRID, width=SS)
        x, y = px(lo_d, yv)
        dr.text((x - 4 * SS, y), f"{yv:g}", fill=GRID_TEXT, font=f_small, anchor="rm")
    lean = {f["label"]: f["lean_deg"] for f in tb.tables["facets"]}
    V2 = tb.arr.V
    for fi, face in enumerate(tb.arr.faces):
        lab, cell = tb.face_label[fi], tb.face_cell[fi]
        if cell == "facet":
            col = _lean_colour(lean[lab])
        elif cell == "faded":
            fade = ps.facets[lab]["fade"]
            c = V2[face].mean(axis=0)
            s = float(smoothstep((c[0] - fade["d0"]) / (fade["d1"] - fade["d0"])))
            col = _blend(_lean_colour(lean[lab]), BASE, s)
        elif cell == "ramp":
            col = RAMP
        else:
            col = BASE
        if tb.face_chamfer[fi] is not None:
            col = _blend(col, CHAMFER, 0.6)
        elif tb.face_roll[fi]:
            col = _blend(col, ROLL, 0.5)
        dr.polygon([px(*V2[v]) for v in face], fill=col)
    th = tb.thickness
    levels = [round(v, 6) for v in np.arange(math.ceil(th.min() / CONTOUR_MM) * CONTOUR_MM, th.max(), CONTOUR_MM)]
    for lv, segs in _contours(tb, levels).items():
        for p, q in segs:
            dr.line([px(*p), px(*q)], fill=CONTOUR, width=SS)
        if segs:
            mids = np.array([0.5 * (p + q) for p, q in segs])
            k = int(np.argmin(np.hypot(*(mids - mids.mean(axis=0)).T)))
            x, y = px(*mids[k])
            dr.text((x, y), f"{lv:g}", fill=CONTOUR_TEXT, font=f_small, anchor="mm", stroke_width=SS,
                    stroke_fill=(255, 255, 255))
    for a, b, shape, _ in crease_edges(tb):
        pa, pb = px(*tb.V[a, :2]), px(*tb.V[b, :2])
        if shape == "flat":
            _dash(dr, [pa, pb], FLAT, 2 * SS, (5 * SS, 4 * SS))
        else:
            dr.line([pa, pb], fill=_shape_colour(shape), width=3 * SS)
    loop = tb.arr.boundary_loop
    dr.line([px(*V2[v]) for v in loop + loop[:1]], fill=OUTLINE, width=2 * SS)
    for kind, col in (("decals", DECAL), ("hinge", HINGE)):
        fps = (footprints or {}).get(kind, [])
        for fp in fps:
            Hh = _hull(fp["points"])
            if len(Hh) < 2:
                continue
            pts = [px(*p) for p in Hh] + [px(*Hh[0])]
            _dash(dr, pts, col, 2 * SS, (6 * SS, 4 * SS))
            if kind == "decals":
                x, y = px(*Hh.mean(axis=0))
                dr.text((x, y), fp.get("name", kind)[:28], fill=col, font=f_small, anchor="mm", stroke_width=SS,
                        stroke_fill=(255, 255, 255))
        if kind == "hinge" and fps:                      # small parts: one label beside them all
            P = np.vstack([np.asarray(fp["points"], np.float64) for fp in fps])
            x, y = px(float(P[:, 0].max()), float(P[:, 1].max()))
            dr.text((x + 4 * SS, y), "hinge", fill=col, font=f_small, anchor="lb", stroke_width=SS,
                    stroke_fill=(255, 255, 255))
    nodes = tb.tables["nodes"]
    placed: list = []
    order = sorted(nodes, key=lambda nm: (not (lo_d - 1 <= nodes[nm]["d"] <= hi_d + 1
                                               and y_lo <= nodes[nm]["y"] <= y_hi), nm))
    for name in order:                               # nodes on the chart first: their labels get the free spots
        n = nodes[name]
        d, y = n["d"], n["y"]
        inside = lo_d - 1 <= d <= hi_d + 1 and y_lo <= y <= y_hi
        dc, yc = min(max(d, lo_d), hi_d), min(max(y, y_lo), y_hi)
        x, yy = px(dc, yc)
        r = 4 * SS
        if inside:
            dr.ellipse([x - r, yy - r, x + r, yy + r], fill=NODE)
        else:
            dr.ellipse([x - r, yy - r, x + r, yy + r], outline=NODE, width=SS)
        g = n["given"]
        t = "?" if n["t"] is None else f"{n['t']:.2f}"
        tag = " base" if g == "base" else (" solved" if g is None else "")
        text = f"{name} t={t}{tag}{'' if inside else ' (off chart)'}"
        font = f_mid if inside else f_small
        _label(dr, (x + 6 * SS, yy - 2 * SS), text, font, (30, 30, 30) if inside else GRID_TEXT, placed, (W, H))
    head = title or (f"chart  d mm back from the hinge (right), y mm up  |  spec {ps.sha256[:8]}  |  thickness "
                     f"{th.min():.2f}-{th.max():.2f} mm (contours every {CONTOUR_MM:g} mm)")
    dr.text((ml * SS, 14 * SS), head, fill=(20, 20, 20), font=_font(13 * SS), anchor="lt")
    _chart_legend(dr, ml * SS, (height - mb + 30) * SS, f_mid)
    return img.resize((width, height), Image.LANCZOS)


def _label(dr, xy, text: str, font, fill, placed: list, size) -> None:
    """Text anchored left-bottom at xy, moved down / up by a line until it overlaps no label placed before."""
    x, y = xy
    box = dr.textbbox((x, y), text, font=font, anchor="lb")
    h = box[3] - box[1] + 2 * SS
    for k in (0, 1, -1, 2, -2, 3, -3):
        b = (box[0], box[1] + k * h, box[2], box[3] + k * h)
        if b[1] < 0 or b[3] > size[1]:
            continue
        if not any(b[0] < q[2] and q[0] < b[2] and b[1] < q[3] and q[1] < b[3] for q in placed):
            break
    else:
        b, k = box, 0
    placed.append(b)
    dr.text((x, y + k * h), text, fill=fill, font=font, anchor="lb", stroke_width=SS, stroke_fill=(255, 255, 255))


def _chart_legend(dr, x0: float, y0: float, font) -> None:
    x = x0
    items = [("ridge", RIDGE, "line"), ("valley", VALLEY, "line"), ("flat < 2°", FLAT, "dash"),
             ("base", BASE, "box"), ("border ramp", RAMP, "box"), ("thickness contour", CONTOUR, "line"),
             ("decal", DECAL, "dash"), ("hinge part", HINGE, "dash"), ("node", NODE, "dot")]
    for label, col, kind in items:
        if kind == "line":
            dr.line([(x, y0), (x + 28 * SS, y0)], fill=col, width=3 * SS)
        elif kind == "dash":
            _dash(dr, [(x, y0), (x + 28 * SS, y0)], col, 2 * SS, (5 * SS, 4 * SS))
        elif kind == "box":
            dr.rectangle([x, y0 - 7 * SS, x + 28 * SS, y0 + 7 * SS], fill=col, outline=(160, 160, 160))
        else:
            dr.ellipse([x + 10 * SS, y0 - 4 * SS, x + 18 * SS, y0 + 4 * SS], fill=col)
        dr.text((x + 34 * SS, y0), label, fill=(40, 40, 40), font=font, anchor="lm")
        x += (34 + 8 * len(label) + 22) * SS
    y1 = y0 + 26 * SS
    x = x0
    dr.text((x, y1), "facet lean vs base:", fill=(40, 40, 40), font=font, anchor="lm")
    x += 140 * SS
    for lv in (0, 10, 20, 30):
        dr.rectangle([x, y1 - 7 * SS, x + 28 * SS, y1 + 7 * SS], fill=_lean_colour(lv), outline=(160, 160, 160))
        dr.text((x + 34 * SS, y1), f"{lv}°{'+' if lv == 30 else ''}", fill=(40, 40, 40), font=font, anchor="lm")
        x += 80 * SS


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
