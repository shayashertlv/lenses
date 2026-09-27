"""M0 lens-outline ground truth: loading, masks, edge-distance metrics and overlays.

The outlines in ``data/bsa/ground_truth/<product>.json`` were traced BY EYE on the native-resolution
photos (method ``ai_visual_trace``), independently of the lens detector and of every pipeline
output: coarse points on 2x grid crops, then 2-3 refinement passes on straightened 10-14x
"normal-band" views and 4-10x pixel crops, reading the edge offset per vertex by eye. Nothing was
snapped to an image gradient.

Conventions
- Pixel frame: native resolution of the named photo, (u right, v down); integer (u, v) is the
  CENTRE of pixel column u / row v (same as ``reconstruction.camera.render_mask``).
- ``points_px``: closed polygon, no repeated end point, clockwise on screen.
- ``segment_types[i]`` labels vertex i and the half-edges on either side of it:
  'frame'   lens meets a rim / brow / endpiece / nose piece / mount,
  'free'    lens edge seen against the backdrop,
  'rimless' drilled clear lens edge (bevel band) against the backdrop.
- ``occluded[i]`` True where the lens edge is hidden behind a frame part and the vertex was
  interpolated (Miu mounts and pads, INVU back centre bar). Excluded from edge metrics by default.
- Edge definition (frame-bounded): the boundary of lens-coloured pixels. A neutral dark groove /
  inner-wall line between rim and lens (VB, Ray-Ban) counts as FRAME; lens-tinted shadow bands
  next to a frame part (Oakley brow and endpieces) count as LENS. Free/rimless: the outer
  silhouette of the lens including its edge bevel line.

Side: 'R' = viewer's right in the front photo (+X in the model frame), 'L' = viewer's left,
'C' = single shield. Entries from the back photo keep BACK-photo pixels; use ``mirror_lens`` to
express them in the mirrored back photo (the S2 ``back_mirrored`` outline source).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import numpy as np

from bsa.core import GROUND_TRUTH, PRODUCTS

SEGMENT_TYPES = ("frame", "free", "rimless")
TYPE_COLOURS = {"frame": (40, 220, 40), "free": (0, 170, 255), "rimless": (255, 0, 200)}
OCCLUDED_COLOUR = (255, 200, 0)


# ------------------------------------------------------------------------------------------ loading
def json_path(product: str) -> Path:
    return GROUND_TRUTH / f"{product}.json"


def overlay_path(product: str) -> Path:
    return GROUND_TRUTH / f"{product}_overlay.png"


def load(product: str) -> dict:
    """The ground-truth record of one product (see module docstring for the schema)."""
    if product not in PRODUCTS:
        raise KeyError(f"unknown product {product!r}")
    d = json.loads(json_path(product).read_text())
    for lens in d["lenses"]:
        lens.setdefault("photo", d["photo"])
        lens.setdefault("occluded", [False] * len(lens["points_px"]))
    return d


def lenses(product: str, photo: str = "front") -> list[dict]:
    """Lens entries traced on one photo ('front' or 'back')."""
    return [lens for lens in load(product)["lenses"] if lens["photo"] == photo]


def points(lens: dict) -> np.ndarray:
    return np.asarray(lens["points_px"], float)


def mirror_lens(lens: dict) -> dict:
    """Express a lens entry in the horizontally mirrored photo: u' = W - 1 - u. The point order is
    reversed so the polygon stays clockwise; L and R swap, C stays."""
    W = int(lens["image_size"][0])
    P = points(lens)
    P = np.column_stack([W - 1 - P[:, 0], P[:, 1]])[::-1]
    out = dict(lens)
    out["points_px"] = P.tolist()
    out["segment_types"] = list(lens["segment_types"])[::-1]
    out["occluded"] = list(lens.get("occluded", [False] * len(P)))[::-1]
    out["side"] = {"L": "R", "R": "L"}.get(lens["side"], lens["side"])
    out["photo"] = f"{lens['photo']}_mirrored"
    return out


# ------------------------------------------------------------------------------------------ geometry
def polygon_mask(points_px, shape: tuple[int, int]) -> np.ndarray:
    """Boolean HxW mask of the pixels whose CENTRE lies inside the polygon."""
    from matplotlib.path import Path as MplPath

    P = np.asarray(points_px, float)
    H, W = int(shape[0]), int(shape[1])
    u0, v0 = np.floor(P.min(0)).astype(int)
    u1, v1 = np.ceil(P.max(0)).astype(int)
    u0, v0 = max(u0, 0), max(v0, 0)
    u1, v1 = min(u1, W - 1), min(v1, H - 1)
    mask = np.zeros((H, W), bool)
    if u1 < u0 or v1 < v0:
        return mask
    uu, vv = np.meshgrid(np.arange(u0, u1 + 1), np.arange(v0, v1 + 1))
    inside = MplPath(P).contains_points(np.column_stack([uu.ravel(), vv.ravel()]), radius=0.0)
    mask[v0:v1 + 1, u0:u1 + 1] = inside.reshape(uu.shape)
    return mask


def polygon_area(points_px) -> float:
    P = np.asarray(points_px, float)
    x, y = P[:, 0], P[:, 1]
    return float(0.5 * abs(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)))


def boundary_samples(points_px, segment_types=None, occluded=None, step: float = 0.5):
    """Dense samples along the closed boundary.

    Returns (S (N,2), types (N,) str, occluded (N,) bool). Each edge i -> i+1 is split at its
    midpoint: the first half carries vertex i's label, the second half vertex i+1's label."""
    P = np.asarray(points_px, float)
    K = len(P)
    T = np.asarray(segment_types if segment_types is not None else ["frame"] * K)
    O = np.asarray(occluded if occluded is not None else [False] * K, bool)
    S, ST, SO = [], [], []
    for i in range(K):
        j = (i + 1) % K
        L = float(np.linalg.norm(P[j] - P[i]))
        n = max(int(np.ceil(L / step)), 1)
        t = np.arange(n) / n
        S.append(P[i] + t[:, None] * (P[j] - P[i]))
        first = t < 0.5
        ST.append(np.where(first, T[i], T[j]))
        SO.append(np.where(first, O[i], O[j]))
    return np.vstack(S), np.concatenate(ST), np.concatenate(SO)


def _distance_to_ring(samples: np.ndarray, poly: np.ndarray) -> np.ndarray:
    import shapely

    ring = shapely.LinearRing(poly)
    return shapely.distance(shapely.points(samples), ring)


def _stats(d: np.ndarray, signed: np.ndarray | None = None) -> dict:
    if d.size == 0:
        return {"n": 0, "mean": None, "median": None, "p95": None, "max": None, "signed_mean": None}
    out = {"n": int(d.size), "mean": float(d.mean()), "median": float(np.median(d)),
           "p95": float(np.percentile(d, 95)), "max": float(d.max())}
    out["signed_mean"] = float(signed.mean()) if signed is not None else None
    return out


def edge_distance(candidate_px, gt_lens: dict, segment_type: str | Iterable[str] | None = "frame",
                  include_occluded: bool = False, step: float = 0.5) -> dict:
    """Distance (px) from the ground-truth boundary, restricted to ``segment_type``, to the
    candidate polygon's boundary: every GT sample (every ``step`` px of GT arc length) is scored
    by its distance to the nearest point of the candidate outline.

    ``signed_mean`` > 0 means the candidate lies OUTSIDE the ground truth on average (it
    over-includes the lens); < 0 means it lies inside. ``segment_type=None`` uses every type.
    Both polygons must be in the same pixel frame (see ``mirror_lens`` for back photos)."""
    import shapely

    C = np.asarray(candidate_px, float)
    S, ST, SO = boundary_samples(gt_lens["points_px"], gt_lens["segment_types"],
                                 gt_lens.get("occluded"), step)
    keep = np.ones(len(S), bool)
    if segment_type is not None:
        wanted = {segment_type} if isinstance(segment_type, str) else set(segment_type)
        keep &= np.isin(ST, list(wanted))
    if not include_occluded:
        keep &= ~SO
    S = S[keep]
    if len(S) == 0:
        return _stats(np.zeros(0))
    d = _distance_to_ring(S, C)
    inside = shapely.contains_xy(shapely.Polygon(C), S[:, 0], S[:, 1])
    return _stats(d, np.where(inside, d, -d))


def candidate_edge_distance(candidate_px, gt_lens: dict, segment_type: str | Iterable[str] | None = "frame",
                            include_occluded: bool = False, step: float = 0.5) -> dict:
    """The other direction: every candidate boundary sample whose NEAREST ground-truth boundary
    point has the requested type (and is not occluded) is scored by its distance to the GT outline.
    ``signed_mean`` > 0 when the candidate lies outside the GT polygon."""
    import shapely
    from scipy.spatial import cKDTree

    G, GT_T, GT_O = boundary_samples(gt_lens["points_px"], gt_lens["segment_types"],
                                     gt_lens.get("occluded"), step=min(step, 0.25))
    Cs, _, _ = boundary_samples(candidate_px, None, None, step)
    _, idx = cKDTree(G).query(Cs)
    keep = np.ones(len(Cs), bool)
    if segment_type is not None:
        wanted = {segment_type} if isinstance(segment_type, str) else set(segment_type)
        keep &= np.isin(GT_T[idx], list(wanted))
    if not include_occluded:
        keep &= ~GT_O[idx]
    Cs = Cs[keep]
    if len(Cs) == 0:
        return _stats(np.zeros(0))
    gt_poly = np.asarray(gt_lens["points_px"], float)
    d = _distance_to_ring(Cs, gt_poly)
    inside_gt = shapely.contains_xy(shapely.Polygon(gt_poly), Cs[:, 0], Cs[:, 1])
    return _stats(d, np.where(inside_gt, -d, d))


def mask_iou(candidate_px, gt_lens: dict) -> float:
    """IoU of the candidate polygon and the ground-truth polygon (pixel-centre masks)."""
    W, H = gt_lens["image_size"]
    a = polygon_mask(candidate_px, (H, W))
    b = polygon_mask(gt_lens["points_px"], (H, W))
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 0.0


def match_lenses(gt_lenses: list[dict], candidates: list) -> list[tuple[int, int]]:
    """Greedy one-to-one assignment (gt index, candidate index) by nearest polygon centroid."""
    def centroid(p):
        P = np.asarray(p, float)
        return P.mean(0)
    pairs = []
    D = np.array([[np.linalg.norm(centroid(g["points_px"]) - centroid(c)) for c in candidates]
                  for g in gt_lenses]) if gt_lenses and candidates else np.zeros((0, 0))
    used_g, used_c = set(), set()
    for flat in np.argsort(D, axis=None):
        gi, ci = np.unravel_index(flat, D.shape)
        if gi in used_g or ci in used_c:
            continue
        pairs.append((int(gi), int(ci)))
        used_g.add(gi); used_c.add(ci)
    return sorted(pairs)


def summary(lens: dict) -> dict:
    """Area, area centroid and type fractions (by boundary length) of one entry."""
    import shapely

    S, ST, SO = boundary_samples(lens["points_px"], lens["segment_types"], lens.get("occluded"), 0.5)
    frac = {t: float(np.mean(ST == t)) for t in SEGMENT_TYPES}
    P = points(lens)
    c = shapely.Polygon(P).centroid
    return {"side": lens["side"], "photo": lens["photo"], "n_points": int(len(P)),
            "area_px": polygon_area(P), "centroid_px": [round(c.x, 2), round(c.y, 2)],
            "perimeter_px": float(len(S) * 0.5), "type_fractions": frac,
            "occluded_fraction": float(np.mean(SO))}


# ------------------------------------------------------------------------------------------ overlay
def _draw_poly(draw, P, types, occ, xform, width=1):
    K = len(P)
    for i in range(K):
        j = (i + 1) % K
        mid = (P[i] + P[j]) / 2
        for a, b, k in ((P[i], mid, i), (mid, P[j], j)):
            col = OCCLUDED_COLOUR if occ[k] else TYPE_COLOURS[types[k]]
            draw.line([xform(a), xform(b)], fill=col, width=width)


def render_overlay(product: str, out: Path | None = None, inset_zoom: int = 4, inset_px: int = 90,
                   insets_per_lens: int = 6) -> Path:
    """Sheet per product: each traced photo in full with the outlines colour-coded by segment type
    (frame green, free blue, rimless magenta, occluded/interpolated yellow) and numbered boxes, then
    zoomed insets (nearest-neighbour ``inset_zoom``x) at evenly spaced boundary positions."""
    from PIL import Image, ImageDraw, ImageFont

    from bsa.core import load_photo

    d = load(product)
    try:
        font = ImageFont.truetype("consola.ttf", 16)
    except OSError:
        font = ImageFont.load_default()
    panels = []
    for photo in sorted({lens["photo"] for lens in d["lenses"]}, key=lambda p: p != "front"):
        img = load_photo(PRODUCTS[product], photo)
        H, W = img.shape[:2]
        entries = [lens for lens in d["lenses"] if lens["photo"] == photo]
        allP = np.vstack([points(lens) for lens in entries])
        m = 0.08 * (allP[:, 0].max() - allP[:, 0].min())
        cx0 = int(max(allP[:, 0].min() - m, 0)); cx1 = int(min(allP[:, 0].max() + m, W))
        cy0 = int(max(allP[:, 1].min() - m, 0)); cy1 = int(min(allP[:, 1].max() + m, H))
        scale = min(1400 / (cx1 - cx0), 1.0) if W > 400 else 700 / (cx1 - cx0)
        full = Image.fromarray(img[cy0:cy1, cx0:cx1]).resize(
            (int((cx1 - cx0) * scale), int((cy1 - cy0) * scale)), Image.LANCZOS if scale < 1 else Image.NEAREST)
        dr = ImageDraw.Draw(full)
        insets = []
        for lens in entries:
            P = points(lens)
            _draw_poly(dr, P, lens["segment_types"], lens["occluded"],
                       lambda p: ((p[0] - cx0 + 0.5) * scale - 0.5, (p[1] - cy0 + 0.5) * scale - 0.5), width=2)
            S, _, _ = boundary_samples(P, None, None, 1.0)
            n_ins = insets_per_lens if len(entries) > 1 else insets_per_lens + 2
            for k in range(n_ins):
                c = S[int(len(S) * (k + 0.5) / n_ins) % len(S)]
                half = inset_px // 2 if W > 400 else max(inset_px // 6, 12)
                x0 = int(np.clip(round(c[0]) - half, 0, max(W - 2 * half, 0)))
                y0 = int(np.clip(round(c[1]) - half, 0, max(H - 2 * half, 0)))
                insets.append((lens, x0, y0, 2 * half))
        z_full = []
        for n, (lens, x0, y0, size) in enumerate(insets, 1):
            bx, by = (x0 - cx0) * scale, (y0 - cy0) * scale
            dr.rectangle([bx, by, bx + size * scale, by + size * scale], outline=(255, 60, 60), width=1)
            dr.text((bx + 2, by + 1), str(n), fill=(255, 60, 60), font=font)
            zoom = inset_zoom if W > 400 else max(inset_zoom, 360 // size)
            crop = Image.fromarray(img[y0:y0 + size, x0:x0 + size]).resize((size * zoom, size * zoom),
                                                                          Image.NEAREST)
            dc = ImageDraw.Draw(crop)
            _draw_poly(dc, points(lens), lens["segment_types"], lens["occluded"],
                       lambda p, x0=x0, y0=y0, zoom=zoom: ((p[0] - x0 + 0.5) * zoom, (p[1] - y0 + 0.5) * zoom))
            dc.rectangle([0, 0, 110, 20], fill=(255, 255, 255))
            dc.text((3, 2), f"{n} {lens['side']} ({x0},{y0})", fill=(0, 0, 0), font=font)
            z_full.append(crop)
        cols = max(1, max(full.width, 1400) // (z_full[0].width + 6)) if z_full else 1
        rows = (len(z_full) + cols - 1) // cols
        cw = z_full[0].width + 6 if z_full else 0
        ch = z_full[0].height + 6 if z_full else 0
        head = 26
        panel = Image.new("RGB", (max(full.width, cols * cw), head + full.height + rows * ch), (255, 255, 255))
        ImageDraw.Draw(panel).text(
            (4, 4), f"{product} / {photo}  {W}x{H}  green=frame blue=free magenta=rimless yellow=occluded",
            fill=(0, 0, 0), font=font)
        panel.paste(full, (0, head))
        for n, crop in enumerate(z_full):
            panel.paste(crop, ((n % cols) * cw, head + full.height + (n // cols) * ch))
        panels.append(panel)
    Wt = max(p.width for p in panels)
    sheet = Image.new("RGB", (Wt, sum(p.height for p in panels) + 10 * (len(panels) - 1)), (255, 255, 255))
    y = 0
    for p in panels:
        sheet.paste(p, (0, y)); y += p.height + 10
    out = out or overlay_path(product)
    sheet.save(out)
    return out


if __name__ == "__main__":
    for pid in PRODUCTS:
        if json_path(pid).exists():
            print(render_overlay(pid))
            for lens in load(pid)["lenses"]:
                print("  ", summary(lens))
