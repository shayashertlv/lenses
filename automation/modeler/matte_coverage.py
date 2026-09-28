"""Matte coverage: the share of a photo's glasses silhouette that its contrast matte (``fg_<view>``) holds.

Every camera fit, contour and IoU of the observation is measured against the intake's contrast matte. On a clear
crystal frame that matte holds the lenses, the metal and scattered rim fragments only (test-pilot-002, Tom Ford crystal
panto: the front matte's rim is broken, the side matte is the gold core wire), so those numbers measure matte holes, and
a symmetric product reads ``mirror_iou_low`` (front matte 0.844 against 0.94). This module rebuilds a COMPLETE
silhouette from the photo with the intake's own backdrop model at a lower contrast (``bsa.intake``: the quadratic Lab
backdrop, the shadow-weighted contrast score, the floor-reflection cut) and reports how much of it the matte covers.

Complete silhouette: contrast score > ``COMPLETE_SCORE`` (the intake's foreground needs 11-24; achromatic darkening
counts at 60 %, so a soft floor shadow stays out), opened 1 px, closed by ``CLOSE_FRACTION`` of the matte width, kept
within ``BOX_MARGIN`` of the matte's bbox, floor reflections cut, small components dropped, united with the matte, and
holes filled only when smaller than ``SMALL_HOLE_FRACTION`` of the bbox (the space a side view shows between the two
temples is backdrop). It under-fills clear crystal (a colourless interior on a white backdrop scores below 5), so the
coverage it reports is an UPPER bound for a crystal frame.

Measured 2026-09-28 on every job's photos (score 5, close 0.4 %; matte px / complete px):
  crystal (test-pilot-002 / tomford-astra1): front 0.794 / 0.799, back 0.792, left 0.415 / 0.419, rear_angled 0.445,
      held-out angled 0.745 / 0.757;
  opaque or tinted, owner-accepted (invu, miu, oakley, rayban, vb): front >= 0.975, back >= 0.837 (oakley: a floor
      shadow under the lenses), angled >= 0.981, left/right >= 0.883 except miu 0.794 / 0.801 (its clear lenses seen
      edge-on are absent from the side matte: a matte hole too).
``COVERAGE_MIN`` 0.85 separates the crystal views from every opaque one except the oakley back (0.837, a shadow) and the
miu sides (a real hole): a view under it carries ``reliable: false``, its contour/IoU are reported, never gated.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

COVERAGE_MIN = 0.85
COMPLETE_SCORE = 5.0
CLOSE_FRACTION = 0.004
BOX_MARGIN = 0.25
SMALL_HOLE_FRACTION = 0.005
MIRROR_IOU_LOW = 0.94          # bsa.intake.MIRROR_IOU_LOW: the input flag's own threshold
MIRROR_GAIN_MIN = 0.05         # mirror IoU gained by filling an unreliable matte's holes that makes mirror_iou_low an artefact
                               # (test-pilot-002 front 0.844 -> 0.941, tomford-astra1 0.862 -> 0.939; the opaque calibration fronts and backs gain <= 0.005)

_CACHE: dict[tuple, dict] = {}


def complete_silhouette(rgb: np.ndarray, fg: np.ndarray) -> np.ndarray:
    """The glasses' whole silhouette in one photo (bool HxW), the matte ``fg`` included."""
    from bsa import intake as bi
    fg = np.asarray(fg, bool)
    ys, xs = np.nonzero(fg)
    if not len(xs):
        return fg.copy()
    lab = bi.lab_image(np.ascontiguousarray(rgb[..., :3], np.uint8))
    bg, _ = bi.backdrop_model(lab)
    score, _ = bi.contrast_score(lab, bg)
    w = int(xs.max() - xs.min() + 1)
    h = int(ys.max() - ys.min() + 1)
    m = ndimage.binary_opening(score > COMPLETE_SCORE, iterations=1)
    m = ndimage.binary_closing(m, iterations=max(2, int(round(CLOSE_FRACTION * w))))
    mg = int(BOX_MARGIN * w)
    box = np.zeros_like(m)
    box[max(0, ys.min() - mg):ys.max() + mg + 1, max(0, xs.min() - mg):xs.max() + mg + 1] = True
    m &= box
    m, _ = bi.floor_reflection_cut(m | fg, score, ndimage.binary_dilation(fg, iterations=3))
    u = bi.keep_components(m) | fg
    holes = ndimage.binary_fill_holes(u) & ~u
    lab_h, n = ndimage.label(holes)
    if n:
        sizes = ndimage.sum_labels(holes, lab_h, np.arange(1, n + 1))
        u |= np.isin(lab_h, 1 + np.nonzero(sizes < SMALL_HOLE_FRACTION * h * w)[0])
    return u


def coverage_of(rgb: np.ndarray, fg: np.ndarray, *, mirror: bool = False) -> dict:
    """``{coverage, reliable, matte_px, complete_px, threshold}`` (+ the two mirror IoUs about each mask's own best axis
    with ``mirror``: the intake's ``symmetry_axis``)."""
    fg = np.asarray(fg, bool)
    full = complete_silhouette(rgb, fg)
    cov = float((fg & full).sum() / max(int(full.sum()), 1))
    out = {"coverage": round(cov, 3), "reliable": bool(cov >= COVERAGE_MIN), "matte_px": int(fg.sum()), "complete_px": int(full.sum()),
           "threshold": COVERAGE_MIN}
    if mirror:
        from bsa.intake import symmetry_axis
        out["mirror_iou_matte"] = round(float(symmetry_axis(fg)[1]), 4)
        out["mirror_iou_complete"] = round(float(symmetry_axis(full)[1]), 4)
    return out


def view_coverage(photo_path: str | Path, fg: np.ndarray, *, mirror: bool = False) -> dict:
    """``coverage_of`` for a photo on disk, cached per process by the photo's bytes and the matte (the observation of
    every revision asks again for the same photos)."""
    raw = Path(photo_path).read_bytes()
    fg = np.asarray(fg, bool)
    key = (hashlib.sha256(raw).hexdigest(), hashlib.sha256(np.packbits(fg).tobytes() + str(fg.shape).encode()).hexdigest(), bool(mirror))
    if key not in _CACHE:
        rgb = np.asarray(Image.open(photo_path).convert("RGB"))
        if rgb.shape[:2] != fg.shape:
            return {"status": "shape_mismatch", "photo_shape": list(rgb.shape[:2]), "matte_shape": list(fg.shape)}
        _CACHE[key] = coverage_of(rgb, fg, mirror=mirror)
    return dict(_CACHE[key])


def unreliable_reason(view: str, cov: dict) -> str:
    return (f"the {view} photo's matte covers {100 * cov['coverage']:.0f}% of the glasses silhouette (below {100 * COVERAGE_MIN:.0f}%: "
            "clear or crystal material the contrast matte cannot see), so this contour/IoU measures matte holes")
