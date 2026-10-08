"""Strip-space donors for block quilting (WP5 correction round, 2026-10-06, task item 3: streaks along the -x temple).

**Diagnosis (FT1119 bake of 10bf932, the -x temple at arview yaw +45).** The dark lines along the arm sat in the
FILLED texels of the temple's outer face, chiefly the see-through text hole (the inner lettering cast through the
acetate, a band about 2 mm tall in the middle of the outer face, 18,674 texels): measured on print.png in strip space
(the strip chart's x = s along the temple, y = t around the section; 0.25-1 mm band-pass of luminance), the ratio of
gradient energy across the strip (d/dt) to along it (d/ds) was 11.5 on that text fill against 6.4 on the photo
texels of the same face, with 35 % less contrast (band std 4.2 against 6.5), and 13.1 against 7.8 on the quilted fill
of the -x temple's top. Real temple print is itself elongated along the arm (6-9 on every photo-painted temple face),
so the target is the photo's own ratio, not 1. Cause: quilting matched each block's context against donor windows
from ANYWHERE on the part's face, and the windows that best continue a strip's context are the edge features that run
along it: the glint along the top edge and the dark line under it (studio shading on the edge's bevel). Copied block by
block into the middle of the face, they became dashes about one block (1 mm) long.

**Fix (``donor_bins``):** on a strip chart, a block's donor windows are taken from the SAME position across the strip
(their centres' t within +-``T_TOL_BLOCKS`` blocks of the block's own t), all along the arm: a hole in the middle of the
outer face is filled from the middle of the outer face, an edge hole from the edge. Candidates are drawn per t-bin
(seeded, ``K_PER_BIN`` each, only the bins the hole's blocks need); a bin with fewer than ``MIN_BIN`` full windows
falls back to the whole donor pool, as do blocks on cap charts (no t). Measured in the bake (bake.json ``streaks``) by
the instrument's ``print_metrics.directional_energy``.
"""
from __future__ import annotations

import numpy as np

K_PER_BIN = 500
MIN_BIN = 30
T_TOL_BLOCKS = 1                  # candidate window centres within +-1 block (about 1 mm) of the block's t


def strip_t(canvas, charts_kind: list[str]) -> np.ndarray:
    """(H, W) float: t (texels from the strip chart's top) on a part canvas's strip charts, NaN elsewhere."""
    t = np.full((canvas.H, canvas.W), np.nan)
    for k, c in enumerate(canvas.charts):
        if charts_kind[k] != "strip":
            continue
        ys, xs = canvas.map_ii[k], canvas.map_jj[k]
        oy = int(canvas.off[k][1])
        # Canvas includes a one-texel crop margin (clipped at the atlas edge).
        # Undo that crop as well as packing: donors use distance from the actual
        # chart top, identical to atlas_t and independent of packing location.
        source_y = int(canvas.source_origins[k][1])
        t[ys, xs] = ys - oy + source_y - int(c["y0"])
    return t


def donor_bins(tband: np.ndarray, cy: np.ndarray, cx: np.ndarray, block_t: np.ndarray, S: int, rng,
               k_global: np.ndarray) -> tuple[np.ndarray, dict]:
    """Candidate window indices (into cy / cx) and, per t-bin a block may ask for, the boolean selector over the
    returned candidates: (cand_idx, {bin: (mask over cand_idx) or None for the global pool}). ``block_t`` = the t of
    every block centre (NaN off the strip); ``k_global`` = the global pool's indices (always included)."""
    tc = tband[cy, cx]
    bins_needed = np.unique(np.floor(block_t[np.isfinite(block_t)] / S).astype(np.int64))
    pick = [np.asarray(k_global, np.int64)]
    per_bin_idx = {}
    for b in bins_needed:
        lo, hi = (b - T_TOL_BLOCKS) * S, (b + 1 + T_TOL_BLOCKS) * S
        inside = np.nonzero(np.isfinite(tc) & (tc >= lo) & (tc < hi))[0]
        if len(inside) < MIN_BIN:
            per_bin_idx[int(b)] = None
            continue
        if len(inside) > K_PER_BIN:
            inside = np.sort(rng.choice(inside, K_PER_BIN, replace=False))
        per_bin_idx[int(b)] = inside
        pick.append(inside)
    cand = np.unique(np.concatenate(pick)) if pick else np.zeros(0, np.int64)
    pos = {int(i): j for j, i in enumerate(cand)}
    sel = {}
    for b, idx in per_bin_idx.items():
        if idx is None:
            sel[b] = None
            continue
        m = np.zeros(len(cand), bool)
        m[[pos[int(i)] for i in idx]] = True
        sel[b] = m
    gm = np.zeros(len(cand), bool)
    gm[[pos[int(i)] for i in np.asarray(k_global, np.int64)]] = True
    sel["global"] = gm
    return cand, sel


def atlas_t(layout, tex) -> np.ndarray:
    """(H, W) float: t (texels from the chart's top) on the atlas's strip charts, NaN elsewhere."""
    t = np.full(tex.shape, np.nan)
    for c in layout.charts:
        if c["kind"] != "strip":
            continue
        m = tex.chart == c["chart"]
        t[tex.ii[m], tex.jj[m]] = tex.ii[m] - int(c["y0"])
    return t
